# RAG chunking v2

本阶段仅改变 chunking，不修改 documents、重新抓取、清理噪声或补做 OCR。
旧 fixed_320_48_v1 已由以下两种策略替代。

## 模型限制与实验控制

- all-MiniLM-L6-v2 的 `sentence_bert_config.json` 限制为 256 tokens；不能用 tokenizer 的 512 上限代替它。
- Qwen3-Embedding-4B 使用 32,768 tokens 上限；每个最终 chunk 同时用其 tokenizer 独立核对。
- 两种 embedding 模型使用同一套 chunks，方便控制变量。这里不利用 Qwen 的长上下文能力；它应作为后续独立实验。
- 256 是最终输入的最大长度，包含 Title、结构策略的 Context 和 special tokens；正文通常短于 256。计数时禁用 truncation。
- 标题最多 32 个 MiniLM tokens，结构上下文最多 48 个。上下文过长只截短重复的前缀，原始正文不删除。
- 只需要本地 tokenizers，不加载 embedding 权重。后续 embed `text` 原样输入，不再额外添加 document prompt；query instruction 单独处理。

## 执行

在项目根目录、已安装 transformers 的 anlp-rag 环境执行：

```bash
python -m rag.chunking \
  --input data/snapshots/20261027T195426Z/documents.jsonl \
  --strategy fixed \
  --chunk-size 256 --overlap 32 \
  --output artifacts/fixed_256_32_v2

python -m rag.chunking \
  --input data/snapshots/20261027T195426Z/documents.jsonl \
  --strategy structure \
  --chunk-size 256 \
  --output artifacts/structure_256_v2
```

输出目录已存在时拒绝覆盖；重新实验请使用新目录。默认 tokenizer 来自项目 models/all-MiniLM-L6-v2 和 models/Qwen3-Embedding-4B；也可通过 --minilm-tokenizer / --qwen-tokenizer 指定。

## 两种策略

**固定窗口**：基于 MiniLM token offsets 划分原文，动态扣除标题与 special tokens，正文相邻窗口重叠 32 tokens。窗口独立复核两个 tokenizer 的限制。保留精确原文字符区间，不通过 tokenizer decode 重建正文。

**内容类型结构分块 + 句子级兜底**：先将已有 blocks 映射回原 Markdown 的精确位置，再按类型处理。

- 普通文章：按 heading 层级划分章节，同章节内尽量合并相邻段落；长段落按段落或句子边界拆分。
- 活动：显式 group 卡片独立处理；部分扁平日历根据已有事件链接、日期与标题识别卡片，并记录 heuristic 警告。长卡片重复原文中提取的短标题/字段作为上下文，不推测日期。
- 数值卡片：连续的数值标题和说明组成一个单元。
- 列表：优先按列表项切分。
- HTML 表格：优先按行拆分并重复受长度约束的表头上下文；合并单元格保留原表示，不自动重建关系。
- PDF：页标题与页正文作为同一单元；布局型表格优先按行，其他文字按句子/段落。已有布局文本不等同于可靠的语义表格，不能修复 OCR 遗漏。
- 代码：优先按行切分。
- 单句、单行或单个列表项仍超长时，最终按 token 上限拆分。结构策略没有固定 token overlap，依靠重复上下文维持定位信息。

这是结构与句子规则分块，不是基于句子 embedding 相似度的 semantic change-point 算法。任何无法精确映射的 blocks 都回退至原 Markdown 并记录警告；不丢弃原文。

## 产物和检查

- chunks.jsonl：检索/embedding 使用 `text`；`body_text` 是原文切片，`prefix` 是上下文。
- occurrences：所有来源、文档 ID、PDF 页码、字符位置、block 索引、分块方式和警告。
- manifest.json：输入和输出哈希、模型下载记录、tokenizer 指纹、代码哈希、库版本和配置。
- stats.json / document_stats.json：总体与逐文档统计。
- preview.md：HTML 和部分 PDF 样本，便于阅读。

按最终 `text` 的 SHA-256 做 exact chunk deduplication；重复 chunk 的所有 occurrences 保留，不做近似去重。检查每个文档原文的全部字符被覆盖（fixed 允许重叠），以及每个最终输入满足两个模型的长度限制。

```bash
python -m unittest discover -s tests -p test_chunking.py
```

现有噪声、旧活动、页码空壳和缺失图中文字仍保留。评测时请区分 corpus 缺失、chunk 边界、retrieval 和 reader 错误。


## 下一阶段

Embedding、BM25、FAISS 和混合检索的实现与命令见 [RETRIEVAL.md](RETRIEVAL.md)。

Qwen3-8B reader 与 closed-book 基线见 [READER.md](READER.md)。
