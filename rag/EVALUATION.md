# Local evaluation

## Current default: standard (no LLM judge)

Use `--metric-set standard` for Recall, F1, ROUGE-1/2/L and their average.
`total = (answer_recall + f1 + rouge_avg) / 3`. All scores are macro-averaged
across questions and range from 0 to 1. This mode does not import the judge,
load any language model, or output judge fields. It rejects --judge.
Protocol: qa-evaluation-v3-standard-no-judge. This local total is not the
leaderboard's four-component total. Existing predictions can be rescored
without generating them again. Use a new output directory.

The sections below document historical modes; em-f1-pr is no longer the default.

## Optional historical mode: Exact Match, F1, Precision, Recall

Select `--metric-set em-f1-pr` (protocol
`qa-evaluation-v2-em-f1-pr`). It needs no additional metric package or model download.
Exact Match uses literal string equality, equivalent to Hugging Face evaluate's
exact_match default options: case, punctuation and whitespace are significant.
No citation stripping or whitespace cleanup is applied by this evaluator.

Token F1, precision and recall lowercase text, delete ASCII string.punctuation,
remove English articles a/an/the, and split on whitespace. Counter intersection
counts repeated tokens. Zero overlap returns zero for all three metrics, including
when both normalized strings are empty.

For multiple reference answers, EM is one if any reference matches exactly.
Choose the reference with highest token F1 and take precision and recall from that
same reference (first reference wins ties). Dataset scores are macro averages over
questions; all four metrics range from zero to one, higher is better. There is no
combined total, ROUGE or judge score in this mode. Old predictions can be rescored
without regenerating them; old protocol totals are not directly comparable.

From the project root, generate answers using the completed fixed MiniLM index:

```bash
source .venv/bin/activate
python -m rag.reader run \
  --questions data/evaluation/public_evaluation_data.json \
  --mode rag \
  --index artifacts/index_cleaned_fixed_v1 \
  --embedding minilm --method rrf \
  --top-k 5 --candidates 50 --rrf-k 60 \
  --output results/cleaned_fixed_minilm_rrf_public_v1

python -m rag.evaluate \
  --references data/evaluation/public_evaluation_data.json \
  --predictions results/cleaned_fixed_minilm_rrf_public_v1/predictions.json \
  --metric-set em-f1-pr \
  --system cleaned-fixed-minilm-rrf-qwen3-8b \
  --output results/cleaned_fixed_minilm_rrf_public_v1/evaluation_em_f1_pr
```

Outputs: summary.json (overall means), per_question.jsonl and per_question.csv
(question-level scores and selected reference), and by_category.json (category
means; optional --labels supplies categories). Use a new evaluation output folder.
The evaluator validates prediction/reference IDs before scoring.

## Legacy protocol and optional judge

The following examples explicitly select `--metric-set legacy` to retain the
previous Recall/F1/ROUGE/judge workflow. They are not required for the current run.

This is an implementation guide, not report prose. No full evaluation has been run.
Generation and scoring are separate. Gold answers are read only by the evaluator/judge,
not supplied to the QA reader. Freeze predictions before comparing systems.

## Environment

From the project root:

```bash
.venv-reader/bin/python -m pip install -r configs/requirements-evaluation.txt
```

Standard scoring uses no model and works in any Python environment with rouge-score.
Local judge inference uses the existing MLX reader environment. It requires an explicit
local model path; it does not download models. Choose an HF-accessible model released
before 2026-01-01 with at most 32B total parameters. Prefer a different model from the
reader; self-judging Qwen must be disclosed. The code cannot certify model eligibility.

## Score existing predictions (no generation)

Replace RUN with an actual run containing predictions for all reference IDs:

```bash
.venv-reader/bin/python -m rag.evaluate \
  --metric-set legacy \
  --references data/evaluation/public_evaluation_data.json \
  --predictions results/RUN/answers.jsonl \
  --system fixed-minilm-rrf \
  --output results/evaluation/RUN_standard
```

Accepts reader answers.jsonl, predictions.json record lists, or an ID-to-answer JSON
object. IDs must match exactly (duplicates/missing/extra IDs fail). Explicit empty
answers score zero on standard metrics. A subset test requires a corresponding
reference subset; it is never silently selected from available predictions.
Optional --labels file.json takes a list of {"id":"1","category":"entity"}
covering exactly the same IDs. Categories must be fixed before inspecting outcomes.

Outputs: summary.json, per_question.jsonl, per_question.csv, by_category.json.
All metrics are 0-1 except judge_raw (1-5). Multiply by 100 for display, not in stored data.
The summary records input and rubric fingerprints and rouge-score version.
Keep source reader run.json, model/index metadata and evidence alongside these outputs.
No predictions are generated or overwritten. Evaluation output directories must be new.

## Judge existing predictions, with resume

```bash
.venv-reader/bin/python -m rag.judge \
  --references data/evaluation/public_evaluation_data.json \
  --predictions results/RUN/answers.jsonl \
  --model /absolute/path/to/local-mlx-judge-model \
  --output results/evaluation/RUN_judge
```

Uses the rubric in rag/judge.py, greedy decoding (temperature 0), seed 42,
non-thinking chat template, 8192 total context tokens and at most 512 generated tokens.
No reference/question/answer truncation is permitted. Prompt overflow fails that item.
The judge receives only question, alternative references and cleaned answer, not system
identity, retrieval evidence or lexical metric scores. The rubric explicitly treats
these fields as untrusted data. This reduces, but cannot eliminate, judge prompt injection.
Validate an initial sample manually before freezing the judge protocol for all systems.

Successful judgments are appended per question. Invalid/truncated JSON is recorded in
errors.jsonl and retried with a schema reminder, up to --attempts (default 2).
Unsuccessful items never get a fabricated score. Rerun the same command to retry missing
IDs; existing successes are reused only when input, model hashes, libraries, code,
rubric and generation configuration match. If interrupted during a JSONL write, inspect
and remove only the incomplete final line before resuming; malformed caches fail closed.
Do not run concurrent writers against the same judge directory.

Score all four components:

```bash
.venv-reader/bin/python -m rag.evaluate \
  --metric-set legacy \
  --predictions results/RUN/answers.jsonl \
  --judge results/evaluation/RUN_judge/judgments.jsonl \
  --system fixed-minilm-rrf \
  --output results/evaluation/RUN_full
```

## Metric contract (qa-evaluation-v1)

- Strip only reader [D1] citation markers; preserve original prediction files.
- Recall/F1: lowercase, remove ASCII punctuation, remove articles a/an/the,
  whitespace tokenization, Counter overlap (not set overlap). Zero overlap returns zero.
  This is a SQuAD-style English normalizer, not numeric or semantic equivalence.
- For each question, independently maximize each standard metric over references.
- ROUGE: rouge-score 0.1.2, rouge1/rouge2/rougeL fmeasure, no stemming;
  use the library's own tokenizer (different from SQuAD normalization).
- rouge_avg = (rouge1 + rouge2 + rougeL)/3. A one-word exact answer has rouge2=0;
  do not dynamically drop that component.
- standard_only = (answer_recall + f1 + rouge_avg)/3.
- judge_normalized = (judge_raw - 1)/4.
- total = (answer_recall + f1 + rouge_avg + judge_normalized)/4.
- Dataset metrics are unweighted means of per-question scores, not pooled token counts.
- Missing any judge judgments makes aggregate judge and total null, even if some rows
  have valid judgments. Standard-only remains available. Never rank standard-only and
  four-component totals together. Category totals require complete judging within category.
- needs_review judgments retain their scores and are counted/flagged for inspection,
  never silently dropped. Local judge scores are proxies, not certified official scores.

Rubric: 5 correct/complete, 4 minor omission without missing a key requested fact,
3 substantive partial correctness, 2 mostly wrong with limited correctness,
1 wrong/non-answer/refusal. Exact facts (dates/numbers/entities) and contradictions matter;
semantic equivalents are accepted. Detailed rules and strict JSON schema live in judge.py.

## Tests

```bash
.venv-reader/bin/python -m unittest discover -s tests -p test_evaluation.py
```

Tests use synthetic references/predictions/judge records only, no model inference.
References: https://aclanthology.org/D16-1264/ (SQuAD §6.1);
https://github.com/google-research/google-research/tree/master/rouge (Apache-2.0).
Model selection, paired statistical comparisons and a multi-system runner are separate
from this per-system evaluation CLI. Use identical reference sets and judge configurations
for system comparisons; label public evaluation results as development results.
