# Retrieval v1

直接使用 Transformers/PyTorch、numpy、FAISS、rank-bm25 实现，没有使用高层 RAG 框架，不进行训练。

## 已实现

- MiniLM：attention-mask mean pooling，384 维，float32。
- Qwen3-Embedding-4B：left padding + last-token pooling，2560 维；Apple MPS 使用 float16，CPU 使用 float32。只对 query 添加官方检索 instruction，不对 documents 添加。
- 两者均 L2 normalize，然后使用 FAISS IndexFlatIP；这是精确 cosine 检索。文档和查询均拒绝超限，不静默截断。
- BM25Okapi：rank-bm25，k1=1.5、b=0.75、epsilon=0.25。英文小写词项和数值分词，不去停用词或做词干化。仅 BM25 分词忽略 URL 地址，原 chunks 不变。
- RRF：各取前 50，1-based rank，score=sum(1/(60+rank))；无词项匹配的零分结果不进入 sparse 排名。默认返回 5。
- weighted：当前语料规模较小，取得全库 dense 和 BM25 分数，各自 min-max 到 [0,1]，score=alpha*dense+(1-alpha)*sparse，默认 alpha=0.5。常数分数归零。这里是全库归一化，不是仅对两个 top-50 的并集归一化；--candidates 仅影响 RRF。
- 并列按原 chunk 行号排序。alpha、候选数、RRF 常数均未调优。

## 运行

在项目根目录：

```bash
conda activate anlp-rag
python -m rag.retrieval build \
  --chunks artifacts/fixed_256_32_v2/chunks.jsonl \
  --output artifacts/index_fixed_v1 --models minilm qwen

python -m rag.retrieval build \
  --chunks artifacts/structure_256_v2/chunks.jsonl \
  --output artifacts/index_structure_v1 --models minilm qwen
```

只建立 BM25：传 `--models`，后面不加模型。指定一个模型：`--models minilm` 或 `--models qwen`。默认 MiniLM batch=32，Qwen batch=2；可通过 --batch-size 修改。--device auto 优先 MPS，再 CUDA，再 CPU。

重复 build 会跳过已完成且哈希一致的索引，未完成的任务从 batch_*.npy 继续。不要同时对同一个输出目录执行 build。变更源 chunks 或模型配置需使用新目录。

```bash
python -m rag.retrieval search \
  --index artifacts/index_structure_v1 \
  --model minilm --method rrf \
  --query "When was Carnegie Mellon University founded?" \
  --top-k 5 --output artifacts/example_retrieval.json
```

`--method` 可以为 bm25 / dense / rrf / weighted。BM25 不加载 embedding 模型；切换 dense 模型用 --model qwen。调加权融合用 --alpha（dense 权重）；RRF 用 --candidates 和 --rrf-k。

索引目录保存 chunks 原样副本、BM25 分词语料和参数，以及每个模型的 embeddings.npy、index.faiss、manifest.json、批次缓存。FAISS 行号严格对应 chunks 行号。BM25 启动时由保存的分词语料重建内存统计，避免 pickle。结果包含最终分数、原始 BM25/dense 分数、chunk 文本及所有来源，可直接传给下一步 reader。

Mac 上 PyTorch 与 FAISS wheel 各自携带 libomp，实测同进程查询会崩溃。因此 FAISS 建库/搜索放在轻量子进程执行，仍直接调用 IndexFlatIP，不设置 KMP_DUPLICATE_LIB_OK。该实现每次 query 会重新载入小型 FAISS 索引；大规模服务可改为持久 worker。PyTorch CPU 线程数固定为 1，MPS 推理保持启用。

完成的结果只是 retrieval，尚不生成答案；功能 smoke test 不等同于公开题上的准确率评测。下一阶段固定 reader 后，再比较两种 chunking、两种 embedding 和各检索方法。

## 验证与引用

运行 `python -m unittest discover -s tests`。模型实际加载、FAISS 建库与检索 smoke test 另行执行。

- https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2 （mean pooling、长度限制）
- https://huggingface.co/Qwen/Qwen3-Embedding-4B （last-token pooling、query instruction）
- https://github.com/facebookresearch/faiss/wiki/MetricType-and-distances （归一化内积与 cosine）
- https://github.com/dorianbrown/rank_bm25 （BM25Okapi）
- Cormack, Clarke, Buettcher (2009), Reciprocal Rank Fusion outperforms Condorcet and individual Rank Learning Methods, SIGIR, DOI:10.1145/1571941.1572114.

环境的确切依赖版本记录在 configs/requirements-retrieval.lock.txt。

## 初步检索观察

MiniLM 的三个手工 smoke queries 只检查流程，不用于报告准确率：

- “Which sport do the Pittsburgh Penguins play?”：两种分块的 RRF 首位都返回包含 hockey 的 Visit Pittsburgh 内容。
- “When was Carnegie Mellon University founded?”：结构分块首位是只有标题的 CMU chunk，不能回答年份；固定窗口首位也不直接给出年份。这是后续错误分析案例，不应据此判断 reader 能正确作答。
- “What is Picklesburgh?”：检索到活动相关新闻/简介片段，仍需检查是否包含完整定义。

保留现有 documents 和 chunks，因此标题碎片、URL 噪声、网页覆盖不足仍可能影响排序。不要把 smoke test 通过解释为 retrieval quality 已达标。

Qwen 固定窗口的同组 smoke queries 也已通过功能检查：Picklesburgh 的 RRF 首位更接近活动定义，但 Penguins 的首位包含导航链接，CMU 成立年份依然缺少直接证据。这里只记录案例，不凭三个问题宣称某个模型更优。

索引 manifest 的 elapsed_seconds 仅记录当前执行中的编码/缓存读取循环，不包含模型加载；断点续跑时也不包含此前运行耗时。因此不要直接将它当作完整端到端性能基准。融合分数也不是答案置信度，不能跨查询或跨检索方法直接比较。
