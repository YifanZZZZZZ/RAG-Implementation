"""Retrieve a question batch, then exit to release embedding model/GPU memory."""

import json, sys, time
from pathlib import Path
from .retrieval import Retriever, sha


def main():
    job = json.loads(Path(sys.argv[1]).read_text())
    cfg = job["config"]
    retriever = Retriever(
        cfg["index"],
        cfg["embedding"] if cfg["method"] != "bm25" else None,
        cfg["model_root"],
        cfg["device"],
    )
    rows = []
    for i, q in enumerate(job["questions"], 1):
        start = time.monotonic()
        hits = retriever.search(
            q["question"],
            cfg["method"],
            cfg["top_k"],
            cfg["candidates"],
            cfg["alpha"],
            cfg["rrf_k"],
        )
        rows.append(
            {**q, "results": hits, "retrieval_seconds": time.monotonic() - start}
        )
        print(f'Retrieved {i}/{len(job["questions"])}', flush=True)
    Path(sys.argv[2]).write_text(json.dumps(rows, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
