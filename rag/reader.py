"""Local Qwen3 RAG / closed-book runner with process-separated retrieval and reading."""

import argparse, hashlib, json, os, subprocess, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for part in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(part)
    return h.hexdigest()


def save(path, obj):
    Path(path).write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n")


def questions_from(path=None, query=None):
    if query is not None:
        rows = [{"id": "1", "question": query}]
    else:
        raw = Path(path).read_text()
        try:
            rows = json.loads(raw)
        except json.JSONDecodeError:
            rows = [json.loads(l) for l in raw.splitlines() if l.strip()]
        if not isinstance(rows, list):
            raise ValueError("Questions must be a JSON list or JSONL")
    clean = []
    seen = set()
    for i, row in enumerate(rows, 1):
        if (
            not isinstance(row, dict)
            or not isinstance(row.get("question"), str)
            or not row["question"].strip()
        ):
            raise ValueError("Each item requires a nonempty question")
        id = str(row.get("id", i))
        if id in seen:
            raise ValueError("Duplicate question ID")
        seen.add(id)
        clean.append({"id": id, "question": row["question"]})
    if not clean:
        raise ValueError("No questions")
    return (
        clean  # Reference answers are deliberately not passed into retrieval or reader.
    )


def environment():
    env = dict(os.environ)
    env["HF_HUB_OFFLINE"] = "1"
    env["TRANSFORMERS_OFFLINE"] = "1"
    return env


def prepare(source, target, reader_python):
    source = source.resolve()
    target = target.resolve()
    if not target.exists():
        subprocess.run(
            [
                str(reader_python),
                "-m",
                "mlx_lm",
                "convert",
                "--hf-path",
                str(source),
                "--mlx-path",
                str(target),
                "-q",
                "--q-bits",
                "4",
                "--q-group-size",
                "64",
            ],
            cwd=ROOT,
            env=environment(),
            check=True,
        )
    cfg = json.loads((target / "config.json").read_text())
    if cfg.get("model_type") != "qwen3" or cfg.get("quantization", {}).get("bits") != 4:
        raise ValueError("Expected quantized Qwen3 model")
    original = json.loads((source / "config.json").read_text())
    for key in [
        "hidden_size",
        "num_hidden_layers",
        "num_attention_heads",
        "vocab_size",
    ]:
        if cfg[key] != original[key]:
            raise ValueError("Converted reader architecture differs from source")
    marker = target / "reader_manifest.json"
    if marker.exists():
        old = json.loads(marker.read_text())
        if old["source_config_sha256"] != sha(source / "config.json"):
            raise ValueError("Source model differs")
        for name, digest in old["files"].items():
            if sha(target / name) != digest:
                raise ValueError("Reader model file changed")
        print("Reader already prepared and verified.", flush=True)
        return
    files = [
        target / "config.json",
        target / "tokenizer_config.json",
        target / "tokenizer.json",
        *sorted(target.glob("*.safetensors")),
    ]
    manifest = {
        "base_model": "Qwen/Qwen3-8B",
        "source_path": str(source),
        "source_config_sha256": sha(source / "config.json"),
        "source_weight_index_sha256": sha(source / "model.safetensors.index.json"),
        "conversion": "mlx_lm.convert -q --q-bits 4 --q-group-size 64",
        "quantization": cfg["quantization"],
        "files": {p.name: sha(p) for p in files},
        "training_or_finetuning": False,
    }
    save(marker, manifest)
    print("Reader prepared: " + str(target), flush=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    prep = sub.add_parser("prepare")
    prep.add_argument("--source", type=Path, default=ROOT / "models/Qwen3-8B")
    prep.add_argument(
        "--reader-model", type=Path, default=ROOT / "models/Qwen3-8B-MLX-4bit"
    )
    prep.add_argument(
        "--reader-python", type=Path, default=ROOT / ".venv-reader/bin/python"
    )
    run = sub.add_parser("run")
    group = run.add_mutually_exclusive_group(required=True)
    group.add_argument("--query")
    group.add_argument("--questions", type=Path)
    run.add_argument("--mode", choices=["rag", "closed-book"], default="rag")
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--andrewid", help="Export submission.json with this Andrew ID after generation")
    run.add_argument(
        "--index", type=Path, default=ROOT / "artifacts/index_structure_v1"
    )
    run.add_argument("--embedding", choices=["minilm", "qwen"], default="minilm")
    run.add_argument(
        "--method", choices=["bm25", "dense", "rrf", "weighted"], default="rrf"
    )
    run.add_argument("--top-k", type=int, default=15)
    run.add_argument("--candidates", type=int, default=50)
    run.add_argument("--alpha", type=float, default=0.5)
    run.add_argument("--rrf-k", type=int, default=60)
    run.add_argument("--device", choices=["auto", "mps", "cpu", "cuda"], default="auto")
    run.add_argument("--model-root", type=Path, default=ROOT / "models")
    run.add_argument(
        "--reader-model", type=Path, default=ROOT / "models/Qwen3-8B-MLX-4bit"
    )
    run.add_argument(
        "--reader-python", type=Path, default=ROOT / ".venv-reader/bin/python"
    )
    run.add_argument("--context-window", type=int, default=4096)
    run.add_argument("--max-new-tokens", type=int, default=256)
    run.add_argument("--temperature", type=float, default=0.7)
    run.add_argument("--top-p", type=float, default=0.8)
    run.add_argument("--sampling-top-k", type=int, default=20)
    run.add_argument("--seed", type=int, default=42)
    a = p.parse_args()
    if a.command == "prepare":
        prepare(a.source, a.reader_model, a.reader_python)
        return
    if (
        a.max_new_tokens <= 0
        or a.context_window <= a.max_new_tokens
        or a.temperature < 0
        or not 0 < a.top_p <= 1
        or a.sampling_top_k < 0
    ):
        raise ValueError("Invalid generation settings")
    if (
        a.top_k <= 0
        or (a.method == "rrf" and a.candidates < a.top_k)
        or not 0 <= a.alpha <= 1
        or a.rrf_k <= 0
    ):
        raise ValueError("Invalid retrieval settings")
    rows = questions_from(a.questions, a.query)
    if a.andrewid is not None:
        a.andrewid = a.andrewid.strip()
        if not a.andrewid or a.andrewid == "YOUR_ANDREWID" or any(c.isspace() for c in a.andrewid):
            raise ValueError("--andrewid requires your actual Andrew ID, without whitespace")
        if any(row["id"] == "andrewid" for row in rows):
            raise ValueError("Question ID conflicts with submission metadata: andrewid")
    if a.output.exists():
        raise FileExistsError("Use a new output directory: " + str(a.output))
    if not a.reader_python.is_file():
        raise FileNotFoundError("Set up .venv-reader first; see rag/READER.md")
    manifest = a.reader_model / "reader_manifest.json"
    model_meta = json.loads(manifest.read_text())
    for name, digest in model_meta["files"].items():
        if sha(a.reader_model / name) != digest:
            raise ValueError("Reader checksum mismatch: " + name)
    cfg = {
        k: str(v.resolve()) if isinstance(v, Path) else v
        for k, v in vars(a).items()
        if k not in {"command", "query", "questions", "output"}
    }
    output = a.output.resolve()
    output.mkdir(parents=True)
    job = {
        "config": cfg,
        "questions": rows,
        "status": "running",
        "reader_manifest_sha256": sha(manifest),
        "source_code_sha256": {
            x.name: sha(x) for x in (ROOT / "rag").glob("reader*.py")
        },
    }
    if a.mode == "rag":
        job["index_manifest_sha256"] = sha(a.index / "manifest.json")
        job["dense_manifest_sha256"] = (
            sha(a.index / a.embedding / "manifest.json") if a.method != "bm25" else None
        )
    save(output / "run.json", job)
    begin = time.monotonic()
    try:
        if a.mode == "rag":
            print("Stage 1/2: retrieval (reader is not loaded)", flush=True)
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "rag.reader_retrieve",
                    str(output / "run.json"),
                    str(output / "retrieval.json"),
                ],
                cwd=ROOT,
                env=environment(),
                check=True,
            )
        else:
            save(output / "retrieval.json", [{**q, "results": []} for q in rows])
        print("Stage 2/2: reading (embedding process has exited)", flush=True)
        subprocess.run(
            [str(a.reader_python), "-m", "rag.reader_mlx", str(output)],
            cwd=ROOT,
            env=environment(),
            check=True,
        )
        answers = [
            json.loads(l) for l in (output / "answers.jsonl").read_text().splitlines()
        ]
        if len(answers) != len(rows):
            raise RuntimeError("Incomplete answers")
        save(
            output / "predictions.json",
            [
                {"id": r["id"], "question": r["question"], "answer": r["answer"]}
                for r in answers
            ],
        )
        if a.andrewid is not None:
            by_id = {str(r["id"]): r["answer"] for r in answers}
            if len(by_id) != len(answers) or set(by_id) != {r["id"] for r in rows}:
                raise ValueError("Submission answer IDs do not match question IDs")
            if any(not isinstance(answer, str) or not answer.strip() for answer in by_id.values()):
                raise ValueError("Submission requires a nonempty string answer for every question")
            save(output / "submission.json", {
                "andrewid": a.andrewid,
                **{row["id"]: by_id[row["id"]] for row in rows},
            })
        job.update(status="complete", elapsed_seconds=time.monotonic() - begin)
        save(output / "run.json", job)
        print("Saved: " + str(output), flush=True)
    except Exception as e:
        job.update(status="failed", error=str(e))
        save(output / "run.json", job)
        raise


if __name__ == "__main__":
    main()
