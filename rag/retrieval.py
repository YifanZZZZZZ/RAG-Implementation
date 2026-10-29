"""BM25 + direct FAISS retrieval and explicit RRF / min-max weighted fusion."""

import os

os.environ.setdefault("OMP_NUM_THREADS", "1")
import argparse
from collections import Counter
import hashlib
import importlib.metadata
import json
from pathlib import Path
import re
import time
import numpy as np
import subprocess
import sys
from rank_bm25 import BM25Okapi

VERSION = "retrieval-v1"


def faiss_build(matrix_path, index_path):
    subprocess.run(
        [
            sys.executable,
            "-m",
            "rag.faiss_worker",
            "build",
            str(index_path),
            str(matrix_path),
        ],
        check=True,
    )


def faiss_search(index_path, vectors, count, k):
    result = subprocess.run(
        [sys.executable, "-m", "rag.faiss_worker", "search", str(index_path)],
        input=json.dumps(
            {
                "vectors": vectors.tolist(),
                "dimension": vectors.shape[1],
                "count": count,
                "k": k,
            }
        ),
        text=True,
        capture_output=True,
        check=True,
    )
    value = json.loads(result.stdout)
    return np.asarray(value["scores"]), np.asarray(value["ids"], dtype=int)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, value):
    path = Path(path)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    temp.replace(path)


def tokenize(text):
    # Same lexical tokenizer for documents and queries; preserve numbers and names.
    text = re.sub(r"https?://[^\s<>\)]+", " ", text)
    return re.findall(r"[a-z]+(?:'[a-z]+)?|\d+(?:[.,]\d+)*", text.casefold())


def order(scores, k):
    return np.argsort(-np.asarray(scores), kind="stable")[:k]


def normalize(scores):
    scores = np.asarray(scores, dtype=np.float64)
    spread = np.ptp(scores)
    return (
        np.zeros_like(scores) if spread <= 1e-12 else (scores - scores.min()) / spread
    )


def combine(sparse, dense, method, top_k=15, candidates=50, alpha=0.5, rrf_k=60):
    if (
        not 0 <= alpha <= 1
        or rrf_k <= 0
        or (method == "rrf" and candidates < top_k)
        or top_k <= 0
    ):
        raise ValueError("Invalid fusion settings")
    sparse = np.asarray(sparse)
    dense = np.asarray(dense)
    if (
        sparse.shape != dense.shape
        or not np.isfinite(sparse).all()
        or not np.isfinite(dense).all()
    ):
        raise ValueError("Invalid scores")
    if method == "bm25":
        return order(sparse, top_k), sparse
    if method == "dense":
        return order(dense, top_k), dense
    if method == "weighted":
        scores = alpha * normalize(dense) + (1 - alpha) * normalize(sparse)
        return order(scores, top_k), scores
    if method != "rrf":
        raise ValueError(method)
    scores = np.zeros(len(sparse), dtype=np.float64)
    # No lexical evidence => no arbitrary zero-score BM25 ranks in RRF.
    sparse_ids = [i for i in order(sparse, candidates) if sparse[i] > 0]
    for ids in [sparse_ids, order(dense, candidates)]:
        for rank, i in enumerate(ids, 1):
            scores[i] += 1 / (rrf_k + rank)
    return order(scores, top_k), scores


def prepare(chunks_path, out):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    source = Path(chunks_path)
    digest = sha(source)
    if (out / "manifest.json").exists():
        meta = json.loads((out / "manifest.json").read_text())
        if meta["chunks_sha256"] != digest or meta["implementation"] != VERSION:
            raise ValueError("Index input/version mismatch; use a new directory")
        if sha(out / "chunks.jsonl") != digest:
            raise ValueError("Stored chunks changed")
        return list(map(json.loads, (out / "chunks.jsonl").read_text().splitlines()))
    chunks = list(map(json.loads, source.read_text().splitlines()))
    if not chunks or len({c["chunk_id"] for c in chunks}) != len(chunks):
        raise ValueError("Empty or duplicate chunks")
    corpus = [tokenize(c["text"]) for c in chunks]
    if not any(corpus):
        raise ValueError("Empty lexical corpus")
    (out / "chunks.jsonl").write_bytes(source.read_bytes())
    write_json(
        out / "bm25.json",
        {
            "tokens": corpus,
            "k1": 1.5,
            "b": 0.75,
            "epsilon": 0.25,
            "tokenizer_version": 1,
        },
    )
    write_json(
        out / "manifest.json",
        {
            "implementation": VERSION,
            "chunks_sha256": digest,
            "source": str(source.resolve()),
            "count": len(chunks),
            "bm25_sha256": sha(out / "bm25.json"),
            "libraries": {
                p: importlib.metadata.version(p)
                for p in ["torch", "transformers", "faiss-cpu", "rank-bm25", "numpy"]
            },
        },
    )
    return chunks


def build(chunks_path, out, models, model_root, device="auto", batch_size=None):
    from .embedding import Embedder, MODELS, TASK

    out = Path(out)
    chunks = prepare(chunks_path, out)
    for name in models:
        folder = out / name
        folder.mkdir(exist_ok=True)
        model_path = Path(model_root) / MODELS[name]
        record = model_path.with_name(model_path.name + ".download.json")
        identity = {
            "model": name,
            "chunks_sha256": sha(out / "chunks.jsonl"),
            "config_sha256": sha(model_path / "config.json"),
            "tokenizer_sha256": sha(model_path / "tokenizer.json"),
            "download_record": (
                json.loads(record.read_text()) if record.exists() else None
            ),
            "query_instruction": TASK if name == "qwen" else None,
            "implementation": VERSION,
        }
        if (folder / "manifest.json").exists():
            saved = json.loads((folder / "manifest.json").read_text())
            if (
                saved["identity"] != identity
                or sha(folder / "index.faiss") != saved["index_sha256"]
            ):
                raise ValueError("Existing dense index mismatch")
            print(f"{name}: completed index already exists; skip", flush=True)
            continue
        # Progress journal validates resume configuration; batches survive interrupted runs.
        if (folder / "progress.json").exists():
            if json.loads((folder / "progress.json").read_text()) != identity:
                raise ValueError("Resume identity mismatch")
        else:
            write_json(folder / "progress.json", identity)
        embedder = Embedder(name, model_root, device)
        size = batch_size or (32 if name == "minilm" else 2)
        if size <= 0:
            raise ValueError("batch-size must be positive")
        vectors = []
        start = 0
        begin = time.monotonic()
        # Each checkpoint filename specifies its first row; independent of later batch size.
        while start < len(chunks):
            checkpoint = folder / f"batch_{start:06d}.npy"
            if checkpoint.exists():
                v = np.load(checkpoint, allow_pickle=False)
            else:
                v = embedder.encode([c["text"] for c in chunks[start : start + size]])
                temp = checkpoint.with_suffix(".tmp")
                with temp.open("wb") as f:
                    np.save(f, v, allow_pickle=False)
                temp.replace(checkpoint)
            if (
                v.ndim != 2
                or v.shape[1] != embedder.dimension
                or not 0 < len(v) <= len(chunks) - start
                or not np.isfinite(v).all()
                or not np.allclose(np.linalg.norm(v, axis=1), 1, atol=1e-4)
            ):
                raise ValueError("Invalid checkpoint")
            vectors.append(v)
            start += len(v)
            print(
                f"{name}: {start}/{len(chunks)} chunks | {time.monotonic()-begin:.1f}s",
                flush=True,
            )
        matrix = np.ascontiguousarray(np.concatenate(vectors), dtype=np.float32)
        np.save(folder / "embeddings.npy", matrix, allow_pickle=False)
        faiss_build(folder / "embeddings.npy", folder / "index.faiss")
        write_json(
            folder / "manifest.json",
            {
                "identity": identity,
                "dimension": embedder.dimension,
                "count": len(chunks),
                "device": embedder.device,
                "dtype": str(embedder.dtype),
                "metric": "L2-normalized inner product = cosine",
                "index_sha256": sha(folder / "index.faiss"),
                "embeddings_sha256": sha(folder / "embeddings.npy"),
                "elapsed_seconds": time.monotonic() - begin,
            },
        )
        del embedder
        import gc, torch

        gc.collect()
        if torch.backends.mps.is_available():
            torch.mps.empty_cache()


class Retriever:
    def __init__(self, path, model=None, model_root=None, device="auto"):
        model_root = model_root or Path(__file__).resolve().parents[1] / "models"
        self.path = Path(path)
        meta = json.loads((self.path / "manifest.json").read_text())
        if (
            sha(self.path / "chunks.jsonl") != meta["chunks_sha256"]
            or sha(self.path / "bm25.json") != meta["bm25_sha256"]
        ):
            raise ValueError("Corrupt index inputs")
        self.chunks = list(
            map(json.loads, (self.path / "chunks.jsonl").read_text().splitlines())
        )
        bm = json.loads((self.path / "bm25.json").read_text())
        self.bm25 = BM25Okapi(
            bm["tokens"], k1=bm["k1"], b=bm["b"], epsilon=bm["epsilon"]
        )
        self.embedder = None
        self.index = None
        if model:
            from .embedding import Embedder, MODELS, TASK

            folder = self.path / model
            m = json.loads((folder / "manifest.json").read_text())
            if (
                m["identity"]["chunks_sha256"] != meta["chunks_sha256"]
                or sha(folder / "index.faiss") != m["index_sha256"]
            ):
                raise ValueError("Dense index mismatch")
            model_path = Path(model_root) / MODELS[model]
            identity = m["identity"]
            if (
                sha(model_path / "config.json") != identity["config_sha256"]
                or sha(model_path / "tokenizer.json") != identity["tokenizer_sha256"]
                or identity["query_instruction"] != (TASK if model == "qwen" else None)
            ):
                raise ValueError("Query model/config differs from indexed model")
            self.index = folder / "index.faiss"
            self.embedder = Embedder(model, model_root, device)
            if (
                m["count"] != len(self.chunks)
                or m["dimension"] != self.embedder.dimension
            ):
                raise ValueError("Dense index dimensions mismatch")

    def search(self, query, method="rrf", top_k=15, candidates=50, alpha=0.5, rrf_k=60):
        if not query.strip():
            raise ValueError("Empty query")
        sparse = self.bm25.get_scores(tokenize(query))
        dense = np.zeros(len(self.chunks))
        if method != "bm25":
            if self.embedder is None:
                raise ValueError("Dense retrieval requires a model")
            q = self.embedder.encode([query], query=True)
            # Small corpus: exact scores for all rows enable well-defined global min-max fusion.
            values, ids = faiss_search(
                self.index, q, len(self.chunks), len(self.chunks)
            )
            dense[ids[0]] = values[0]
        selected, scores = combine(
            sparse, dense, method, top_k, candidates, alpha, rrf_k
        )
        return [
            {
                "rank": r,
                "score": float(scores[i]),
                "bm25_score": float(sparse[i]),
                "dense_score": float(dense[i]) if method != "bm25" else None,
                **self.chunks[i],
            }
            for r, i in enumerate(selected, 1)
        ]


def main():
    root = Path(__file__).resolve().parents[1]
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    b = sub.add_parser("build")
    b.add_argument("--chunks", type=Path, required=True)
    b.add_argument("--output", type=Path, required=True)
    b.add_argument(
        "--models", nargs="*", choices=["minilm", "qwen"], default=["minilm", "qwen"]
    )
    b.add_argument("--batch-size", type=int)
    s = sub.add_parser("search")
    s.add_argument("--index", type=Path, required=True)
    s.add_argument("--query", required=True)
    s.add_argument("--model", choices=["minilm", "qwen"], default="minilm")
    s.add_argument(
        "--method", choices=["bm25", "dense", "rrf", "weighted"], default="rrf"
    )
    s.add_argument("--top-k", type=int, default=15)
    s.add_argument("--candidates", type=int, default=50)
    s.add_argument("--alpha", type=float, default=0.5)
    s.add_argument("--rrf-k", type=int, default=60)
    s.add_argument("--output", type=Path)
    for x in [b, s]:
        x.add_argument("--model-root", type=Path, default=root / "models")
        x.add_argument(
            "--device", choices=["auto", "cpu", "mps", "cuda"], default="auto"
        )
    a = p.parse_args()
    if a.command == "build":
        build(a.chunks, a.output, a.models, a.model_root, a.device, a.batch_size)
    else:
        r = Retriever(
            a.index, a.model if a.method != "bm25" else None, a.model_root, a.device
        )
        results = r.search(a.query, a.method, a.top_k, a.candidates, a.alpha, a.rrf_k)
        payload = {
            "index_path": str(a.index.resolve()),
            "index_manifest_sha256": sha(a.index / "manifest.json"),
            "top_k": a.top_k,
            "query": a.query,
            "method": a.method,
            "model": a.model if a.method != "bm25" else None,
            "alpha": a.alpha,
            "rrf_k": a.rrf_k,
            "candidates": a.candidates,
            "results": results,
        }
        if a.output:
            write_json(a.output, payload)
        for row in results:
            print(
                f"\n#{row['rank']} score={row['score']:.6f} {row['chunk_id']}\n{row['text'][:900]}\nSources: {row['source_urls']}"
            )


if __name__ == "__main__":
    main()
