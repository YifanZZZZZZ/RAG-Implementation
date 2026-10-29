"""Direct HF inference, pooling follows each model's official model card."""

from pathlib import Path
import numpy as np
import torch
from transformers import AutoModel, AutoTokenizer

MODELS = {"minilm": "all-MiniLM-L6-v2", "qwen": "Qwen3-Embedding-4B"}
TASK = "Given a web search query, retrieve relevant passages that answer the query"


class Embedder:
    def __init__(self, name, root, device="auto"):
        if name not in MODELS:
            raise ValueError(name)
        self.name = name
        # Avoid threaded CPU shard conversion crashes observed on this Mac.
        torch.set_num_threads(1)
        self.device = (
            (
                "mps"
                if torch.backends.mps.is_available()
                else "cuda" if torch.cuda.is_available() else "cpu"
            )
            if device == "auto"
            else device
        )
        self.dtype = (
            torch.float32 if name == "minilm" or self.device == "cpu" else torch.float16
        )
        self.limit = 256 if name == "minilm" else 32768
        path = Path(root) / MODELS[name]
        self.tokenizer = AutoTokenizer.from_pretrained(
            path,
            local_files_only=True,
            padding_side="left" if name == "qwen" else "right",
        )
        self.model = (
            AutoModel.from_pretrained(
                path,
                local_files_only=True,
                dtype=self.dtype,
                attn_implementation="sdpa",
            )
            .to(self.device)
            .eval()
        )
        self.dimension = self.model.config.hidden_size
        print(
            f"Loaded {name}: {self.device}, {self.dtype}, dimension={self.dimension}",
            flush=True,
        )

    def encode(self, texts, query=False):
        if query and self.name == "qwen":
            texts = [f"Instruct: {TASK}\nQuery:{x}" for x in texts]
        batch = self.tokenizer(
            texts, padding=True, truncation=False, return_tensors="pt"
        )
        if batch["input_ids"].shape[1] > self.limit:
            raise ValueError(
                "Embedding input exceeds model limit; refusing silent truncation"
            )
        batch = {k: v.to(self.device) for k, v in batch.items()}
        with torch.inference_mode():
            extra = {"use_cache": False} if self.name == "qwen" else {}
            hidden = self.model(**batch, **extra).last_hidden_state
            if self.name == "qwen":
                vectors = hidden[:, -1].float()  # left padding => final token is real
            else:
                mask = batch["attention_mask"].unsqueeze(-1)
                vectors = (hidden.float() * mask).sum(1) / mask.sum(1).clamp(min=1)
            vectors = torch.nn.functional.normalize(vectors, p=2, dim=1)
        result = np.ascontiguousarray(vectors.cpu().numpy(), dtype=np.float32)
        if not np.isfinite(result).all() or not np.allclose(
            np.linalg.norm(result, axis=1), 1, atol=1e-4
        ):
            raise ValueError("Invalid embeddings")
        return result
