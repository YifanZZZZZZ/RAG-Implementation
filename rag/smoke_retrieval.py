"""Small functional checks, not a scored public-evaluation benchmark."""

import argparse, json, gc, time
from pathlib import Path
import numpy as np
from .retrieval import Retriever, write_json, faiss_search

QUERIES = [
    "When was Carnegie Mellon University founded?",
    "Which sport do the Pittsburgh Penguins play?",
    "What is Picklesburgh?",
]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--indexes", nargs="+", type=Path, required=True)
    p.add_argument("--models", nargs="+", default=["minilm", "qwen"])
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    root = Path(__file__).resolve().parents[1]
    records = []
    for folder in a.indexes:
        for model in a.models:
            r = Retriever(folder, model, root / "models")
            matrix = np.load(folder / model / "embeddings.npy", allow_pickle=False)
            assert len(matrix) == len(r.chunks) and np.isfinite(matrix).all()
            assert np.allclose(np.linalg.norm(matrix, axis=1), 1, atol=1e-4)
            # FAISS score and row alignment independently checked against numpy.
            q = r.embedder.encode([QUERIES[1]], query=True)
            values, ids = faiss_search(r.index, q, len(matrix), len(matrix))
            assert np.allclose(values[0], (matrix @ q[0])[ids[0]], atol=2e-5)
            for query in QUERIES:
                for method in ["bm25", "dense", "rrf", "weighted"]:
                    begin = time.monotonic()
                    results = r.search(query, method, top_k=3)
                    assert (
                        len(results) == 3 and len({x["chunk_id"] for x in results}) == 3
                    )
                    assert all(np.isfinite(x["score"]) for x in results)
                    records.append(
                        {
                            "index": str(folder),
                            "model": model,
                            "method": method,
                            "query": query,
                            "seconds": time.monotonic() - begin,
                            "results": results,
                        }
                    )
            print(
                f"{folder.name}/{model}: norms, FAISS/numpy alignment, 12 searches PASS",
                flush=True,
            )
            del r, matrix
            gc.collect()
            import torch

            if torch.backends.mps.is_available():
                torch.mps.empty_cache()
    write_json(
        a.output,
        {
            "purpose": "Functional smoke test, no annotated ground truth and no accuracy estimate",
            "records": records,
        },
    )


if __name__ == "__main__":
    main()
