# 知识库采集 v3

本版两个入口共用解析器：`collect.py` 自动处理 CSV 中每个 URL；`process_manual.py` 导入浏览器保存的本地文件。没有递归爬虫，也不读取 public evaluation QA。

## 运行自动采集

在项目根目录运行（先激活虚拟环境）：

```bash
source .venv/bin/activate
python scripts/collect.py --sources config/source.csv
```

默认处理 CSV 全部行，接受 `source id` / `source_id`、`category`、`url` 列。每个 URL 总时限 240 秒；可用 `--url-timeout 120` 调整。每个阶段即时输出，等待期间每 5 秒输出状态；下载显示字节数，PDF 解析显示页号。任务结束打印输出目录。

先试两条可以加 `--limit 2`。`--no-browser` 关闭浏览器渲染。HTTP 请求默认延迟 2 秒，并检查 robots.txt。浏览器加载失败、403、robots 不允许、超时、空正文或无法确认日历内容的页面都会进入人工清单；不会无限重试。

自动流程：URL 规范化 → Requests 下载并保留原文件 → 判断 HTML/PDF/文本 → 提取语义结构 → 清理 → 完整性启发式检查 → 内容去重。对于疑似 JavaScript 外壳，使用 Playwright Chromium 再尝试一次。此检测不能识别所有“有正文但部分内容尚未加载”的页面，仍需抽查。

依赖在 `requirements-collect.txt`，新环境安装：

```bash
python -m pip install -r scripts/requirements-collect.txt
python -m playwright install chromium
```

## 手动补充

1. 打开本次自动输出目录中的 `manual_queue.csv`，查看 `url` 和 `reason`。
2. 在你自己的浏览器中打开页面，等正文和活动信息加载完成，再另存为 HTML（不要只保存验证页）。如果是 PDF，保存原始 PDF。
3. 把文件放在自己的固定目录，例如项目的 `data/manual/`。在对应 CSV 行填写 `local_path`，**建议绝对路径**；包含逗号的路径需遵循 CSV 引号规则。可选填写真实的 `saved_at`，例如 `2026-10-27T15:30:00-04:00`。不要把导入时间当作网页保存时间。
4. 不必删除未完成的行。执行：

```bash
python scripts/process_manual.py --manifest data/processed/<自动运行目录>/manual_queue.csv
```

这会创建新的 `_manual` 目录，将自动运行已接受的文档与手动导入结果合并，并再次精确去重。未填写路径、文件不存在、保存了验证码页面或正文不完整的行会留在新目录的 `manual_queue.csv`。后续继续填写**新目录**清单，再运行相同手动命令即可逐轮补充。

默认以清单所在目录为基础语料目录；若复制清单到其他位置，指定 `--base-run data/processed/<已有运行目录>`。相对 `local_path` 按清单目录解析。

普通“另存为 HTML”可能没有保存动态 DOM。如果导入后仍显示空日历，打开保存文件检查正文；必要时用浏览器开发者工具复制加载完成的 `<html>` 元素的 outerHTML，保存为 UTF-8 `.html`。对于无法合法访问的内容，可以保持缺失并在报告说明覆盖限制，不必强行补齐。

## 输出与后续使用

- `documents.jsonl`：通过启发式检查、去重后的文档；正文在 `content_markdown`，结构在 `blocks`，出处列表在 `sources`。后续 chunking / embedding 使用这个文件。
- `raw/`：原始响应、手动文件副本、渲染 HTML、每个任务参数/结果/错误日志。
- `parsed/`：清理前结构；`cleaned/`：清理后结构与检查标记；`markdown/`：便于人工阅读的内容。**这些目录也包含待复查文档，不能直接全量当成合格知识库。**
- `manual_queue.csv`：本轮尚未解决的 URL。
- `progress.jsonl` / `fetch_log.jsonl`：实时阶段/最终处理状态。
- `duplicates.jsonl`：去重记录；`discovered_links.jsonl`：发现的链接清单，**不会自动下载这些子页面**。
- `input_snapshot.csv` / `run_config.json` / `corpus_stats.json` / `data_creation_notes.md`：来源、依赖版本、数量和报告证据。

JSONL 每行一个 JSON 对象；中文 Unicode 保留，换行在 JSON 中显示为 `\n` 是正常序列化。需要直接阅读正文时打开 `markdown/*.md`。

这是文档准备，不是完成 RAG：之后仍需抽查、补充必要子页面、chunking、建检索索引和 evaluation。课程推荐主页只覆盖主页内容；要覆盖 history/schedule/vendors 等页面，请把目标 URL 明确加入 CSV 后重新采集。不要把“URL 全部处理完”误认为知识覆盖完整。

## 提取规则与边界

按响应类型和页面结构分派，而不根据 category 删除正文。category 仅用于来源记录。

- HTML：Beautiful Soup，优先 main/article/Wikipedia 正文容器，保留标题层级、段落、嵌套列表、代码缩进、链接、定义列表、表格和活动卡片；遍历 div/span/time，避免丢失日期、场馆等信息。
- 清理：移除脚本、导航、表单、页脚模板等；尽量单独保留页脚地址/开放时间/联系方式；清理空白、HTML 实体和重复卡片，不删除全部非 ASCII 字符。
- 简单表格转 Markdown；合并单元格以保留 rowspan/colspan 的 HTML 表格嵌入 Markdown。Markdown 阅读器的渲染效果可能不同，结构化单元格也保存在 blocks。
- PDF：pypdf 按页提取布局文本；不做 OCR，不保证多栏阅读顺序或表格单元格语义。空文本页会进入人工清单，非空页仍可能需要人工核对。
- URL 去重：规范主机名、默认端口、跟踪参数和普通 fragment，保留业务查询参数和 hash 路由；页面声明的 canonical 只记录，避免错误 canonical 合并不同活动页。
- 内容去重：对规范化的结构块计算 SHA-256。代码缩进和表格结构参与哈希；不做模糊去重。相同正文合并来源；页面标题等结构不同则可能保留两个文档。
- 自动检查不是事实核验，也不能证明正文完整。不会点击分页、日历按钮或抓取 iframe 的全部内容。JSON-LD 单独留存，不自动转成正文。

## 报告 Data creation

`data_creation_notes.md` 是实际运行证据，可结合 `corpus_stats.json` 的数量改写报告：

1. 来源由 `source.csv` 显式列出；纳入范围为这些页面、通过检查的正文和人工补充。解释哪些子页面是你主动选入，以及尚有哪些缺口。
2. 工具为 Requests / Playwright、Beautiful Soup 和 pypdf；记录自动与手动获取方式，描述结构保留、清理、URL 和内容去重。
3. 本采集流程没有创建训练标注，数量为 0。若你后来另建 QA 或微调，须另行报告实际类型和数量。
4. 本流程网页用作检索证据，不是训练集；不把公开 evaluation 答案写入知识文档。公开 evaluation 单独用于测评和消融。其他训练数据若使用须另行说明。

## 验证

```bash
python -m unittest discover -s scripts/tests -v
```

包含结构保留和本地 HTTP 端到端测试（403、超时、空页、自动去重、手动合并）。集成测试需要允许绑定 localhost 端口，不连接外部站点。
