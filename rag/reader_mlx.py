"""MLX-only generation worker; embedding process has already exited."""

import hashlib, importlib.metadata, json, sys, time
from pathlib import Path
import mlx.core as mx
from mlx_lm import load, stream_generate
from mlx_lm.sample_utils import make_sampler
from .reader_prompts import pack, parse_answer, VERSION


def main():
    folder = Path(sys.argv[1])
    job = json.loads((folder / "run.json").read_text())
    cfg = job["config"]
    rows = json.loads((folder / "retrieval.json").read_text())
    print("Loading Qwen3 reader (MLX 4-bit, non-thinking)...", flush=True)
    model, tokenizer = load(
        cfg["reader_model"], tokenizer_config={"trust_remote_code": False}
    )
    model_config = json.loads((Path(cfg["reader_model"]) / "config.json").read_text())
    if cfg["context_window"] > model_config["max_position_embeddings"]:
        raise ValueError("Context exceeds model limit")
    sampler = make_sampler(
        temp=cfg["temperature"], top_p=cfg["top_p"], top_k=cfg["sampling_top_k"]
    )
    runtime = {
        "backend": "mlx-lm",
        "prompt_version": VERSION,
        "thinking": False,
        "libraries": {
            p: importlib.metadata.version(p) for p in ["mlx", "mlx-lm", "transformers"]
        },
    }
    (folder / "reader_runtime.json").write_text(json.dumps(runtime, indent=2))
    with (folder / "answers.jsonl").open("w") as out:
        for i, row in enumerate(rows, 1):
            # Stable per-question seed independent of batch order and retrieval variant.
            seed = (
                cfg["seed"]
                + int(hashlib.sha256(str(row["id"]).encode()).hexdigest()[:8], 16)
            ) % (2**32)
            mx.random.seed(seed)
            packed = pack(
                row["question"],
                row["results"],
                tokenizer,
                cfg["mode"],
                cfg["context_window"],
                cfg["max_new_tokens"],
            )
            print(
                f'Reading {i}/{len(rows)} | input={packed["input_tokens"]} tokens | evidence={len(packed["context"])}',
                flush=True,
            )
            started = time.monotonic()
            segments = []
            last = None

            def progress(done, total):
                print(f"  prefill {done}/{total}", flush=True)

            for event in stream_generate(
                model,
                tokenizer,
                packed["prompt_tokens"],
                max_tokens=cfg["max_new_tokens"],
                sampler=sampler,
                prefill_step_size=512,
                prompt_progress_callback=progress,
            ):
                segments.append(event.text)
                last = event
                if event.generation_tokens % 32 == 0:
                    print(f"  generated {event.generation_tokens} tokens", flush=True)
            if last is None:
                raise RuntimeError("Reader produced no generation response")
            raw = "".join(segments)
            parsed = parse_answer(raw, packed["context"], cfg["mode"])
            if last.finish_reason == "length":
                parsed["warnings"].append("generation_hit_token_limit")
            result = {
                "id": row["id"],
                "question": row["question"],
                **parsed,
                "context": packed["context"],
                "excluded_chunk_ids": packed["excluded_chunk_ids"],
                "messages": packed["messages"],
                "input_tokens": packed["input_tokens"],
                "generation_tokens": last.generation_tokens,
                "finish_reason": last.finish_reason,
                "seed": seed,
                "reader_seconds": time.monotonic() - started,
                "retrieval_seconds": row.get("retrieval_seconds", 0),
                "peak_memory_gb": last.peak_memory,
            }
            out.write(json.dumps(result, ensure_ascii=False) + "\n")
            out.flush()
            print("Answer: " + parsed["raw_answer"], flush=True)
            mx.clear_cache()


if __name__ == "__main__":
    main()
