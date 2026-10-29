# Qwen3 reader

本阶段接入本地 Qwen/Qwen3-8B reader。检索逻辑保持不变；不训练、不微调、不修改 documents/chunks/indexes。

## 模型与配置

- Reader：已有 Qwen3-8B 原始权重，使用 MLX-LM 在本地做 4-bit affine quantization，group_size=64。转换后约 4.3 GiB；计入 scale/bias 等元数据后平均约 4.5 bits/weight，并非每个参数/张量都是纯 4 bit。原始模型仍保留。
- 推理后端：Apple Silicon MLX；它只负责 reader 推理，不实现或替代检索。
- `enable_thinking=False`；默认 temperature=0.7、top_p=0.8、top_k=20，沿用官方 non-thinking 建议。
- 默认总上下文预算 4096 tokens，预留 256 输出 tokens；使用 Qwen chat template 对完整 prompt 计数。按检索顺序装入完整 chunks，超预算的 chunk 跳过并记录，不静默截断问题或正文。
- 每题独立对话、独立 KV cache。seed=42 加上题目 ID 的稳定 hash；同一道题在不同实验中使用相同 seed。随机种子不保证跨硬件/库版本逐 bit 一致。
- Prompt 要求短语或一句话、最多 30 词；RAG 只使用给定证据、引用 [D1] 等标签，不足时返回固定拒答。词数限制和事实约束是 prompt 指令，不能保证模型始终遵守。
- 检索完整批次后进程退出，再启动 reader，避免 Qwen embedding 和 reader 同时驻留 GPU。closed-book 完全跳过检索。

## 已安装环境

- 检索/调度：`conda activate anlp-rag`
- Reader 专用：项目 `.venv-reader`，由主程序自动调用，无需切换环境。
- Reader 依赖记录在 `configs/requirements-reader.lock.txt`，不改动检索环境依赖。

重新准备新机器时：

```bash
python -m venv .venv-reader
.venv-reader/bin/python -m pip install -r configs/requirements-reader.lock.txt
python -m rag.reader prepare
```

`prepare` 从 models/Qwen3-8B 转换到 models/Qwen3-8B-MLX-4bit。已准备好的模型只校验哈希，不重复转换。首次需要原始权重在本地；子进程禁用 Hugging Face 网络请求。

## 单题 RAG

在项目根目录执行：

```bash
conda activate anlp-rag
python -m rag.reader run \
  --index artifacts/index_structure_v1 \
  --embedding minilm --method rrf \
  --query "Which sport do the Pittsburgh Penguins play?" \
  --output results/qwen3_example
```

每次使用新的 output 目录，避免覆盖实验。默认配置即上述 structure + MiniLM + RRF。可选：

- `--embedding qwen`：使用 Qwen3-Embedding-4B 检索，reader 仍为 Qwen3-8B。
- `--method bm25|dense|rrf|weighted`：选择检索方式，`--alpha` 为 weighted 的 dense 权重。
- `--index artifacts/index_fixed_v1`：切换固定窗口。
- `--top-k 5`、`--candidates 50`、`--rrf-k 60`：检索参数。
- `--context-window 4096`、`--max-new-tokens 256`：reader 输入/输出预算。
- `--temperature`、`--top-p`、`--sampling-top-k`、`--seed`：生成参数；比较检索时保持一致。

## Closed-book 与批量问题

```bash
python -m rag.reader run \
  --mode closed-book \
  --query "Which sport do the Pittsburgh Penguins play?" \
  --output results/qwen3_closed_book_example

python -m rag.reader run \
  --questions configs/reader_smoke_questions.json \
  --output results/qwen3_batch_example
```

`--questions` 接受 JSON list 或 JSONL，每项包含 id、question；其他字段（包括参考答案）不传给检索或 reader。问题 ID 不能重复。可直接以相同输入加 `--mode closed-book` 比较。

## 输出

- `run.json`：配置、模型/索引 manifest 哈希、代码哈希、问题、运行状态。
- `retrieval.json`：每题实际检索结果及耗时；closed-book 的 results 为空。
- `reader_runtime.json`：MLX、MLX-LM、Transformers 版本与 prompt 版本。
- `answers.jsonl`：answer、raw_answer、引用对应的 chunk/URL、实际送入上下文的完整内容、被预算排除的 chunk、完整消息、token 数、结束原因、耗时和 MLX 峰值内存。
- `predictions.json`：id/question/answer 的简化列表，可供后续评测读取；尚未声称符合 leaderboard 的最终提交 schema。

`raw_answer` 保留模型原始输出；`answer` 只去除明确的 [D#] 引用标记，方便做答案匹配。引用检查仅验证标签是否存在，不能验证引文是否真正支持所有事实。缺失引用、未知引用、带额外文字的拒答、超过建议词数或达到生成长度上限会记录 warnings。失败运行保留已写出的 answers.jsonl 和 failed 状态；目前不自动恢复部分 reader 任务，请使用新目录重跑。

## 检查与限制

运行 `python -m unittest discover -s tests`。

小样本问题是手写功能检查，不是公开评测准确率。Picklesburgh 回答中的活动细节出现在 D5，但模型只引用了 D2、D4，存在引用不完整；closed-book 则出现把节日说成 Texas 小镇的幻觉。保留 v1 输出；v2 收紧了回答长度和证据要求。CMU 年份问题中当前 top-5 缺少直接证据时，RAG 应拒答，不能将 closed-book 的回答自动视为正确标签。

后续应在固定公开数据上正式比较 closed-book、BM25、dense、RRF 和 weighted；同时审查回答正确性与证据支持，不能仅凭引用存在或程序运行成功判断模型表现。

## 报告引用

- [Qwen3-8B model card](https://huggingface.co/Qwen/Qwen3-8B)：模型、chat template、non-thinking 与采样建议。
- [MLX-LM](https://github.com/ml-explore/mlx-lm)：Apple Silicon 推理和本地权重量化。

报告应明确 reader 使用 4-bit 量化，而非原始 BF16 推理；量化不是训练数据标注或 fine-tuning。

最终样本与人工检查见 `results/reader_smoke_summary.md`；最终 RAG 输出为 `results/reader_qwen3_rag_final_smoke`，closed-book 为 `results/reader_qwen3_closed_book_smoke_v2`。
