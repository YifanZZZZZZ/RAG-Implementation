# Pittsburgh & CMU RAG Question Answering

## Introduction

This project implements an end-to-end retrieval-augmented generation
(RAG) system for factual questions about Pittsburgh and Carnegie Mellon
University, including history, culture, events, food, and sports.

The pipeline covers document collection, cleaning, chunking, retrieval,
and answer generation. It supports BM25 sparse retrieval, dense retrieval,
and Reciprocal Rank Fusion (RRF). Retrieved passages are provided to a
language model to generate concise answers.

## Generate Answers

The example below uses fixed-length chunks, MiniLM embeddings, RRF
retrieval, and a local Qwen3-8B MLX reader.

### 1. Environment Setup

```python3.11 -m venv .venv-retrieval
.venv-retrieval/bin/python -m pip install \
  -r configs/requirements-retrieval.lock.txt

python3.11 -m venv .venv-reader
.venv-reader/bin/python -m pip install \
  -r configs/requirements-reader.lock.txt
```

### 2. Prepare the dataset

Save questions as a JSON array with unique IDs:

```json
[
  {
    "id": "1",
    "question": "Who is Pittsburgh named after?"
  },
  {
    "id": "2",
    "question": "Where is Carnegie Mellon University located?"
  }
]
```

Reference answers are not required for inference.

### 2. Run inference

```bash
.venv-retrieval/bin/python -m rag.reader run \
  --questions data/evaluation/questions.json \
  --mode rag \
  --index artifacts/index_optimal_fixed_minilm_v2 \
  --embedding minilm \
  --method rrf \
  --top-k 15 \
  --candidates 50 \
  --device mps \
  --model-root models \
  --reader-model models/Qwen3-8B-MLX-4bit \
  --reader-python .venv-reader/bin/python \
  --context-window 8192 \
  --output results/my_rag_run
```

Replace `--questions` to use another dataset. Use a new output directory
for each run.

## Retrieval Options

- `--method bm25`: sparse retrieval.
- `--method dense`: dense retrieval.
- `--method rrf`: hybrid retrieval using RRF.
- `--method weighted`: hybrid retrieval using normalized score fusion.

To use another chunking configuration, select its corresponding index.
To use Qwen embeddings, select a compatible Qwen index and set
`--embedding qwen`.

## Acknowledgments

This project was developed for
[CMU Advanced NLP Assignment 2](https://github.com/anlp-fall2026/hw2-public).
It uses Hugging Face Transformers, FAISS, rank-bm25, and MLX-LM,
with MiniLM and Qwen models.
