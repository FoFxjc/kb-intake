# Docling 评估 v1：能否作为 kb-intake 的抽取层

状态：评估完成；已确定 Docling 主 + find_tables 副，以稳定性为首要目标（2026-10-20 17:17 Peter，取代同日 17:07 记录的"只用 Docling"）
日期：2026-10-20
对象：Docling 2.140.0（MIT，LF AI & Data），标准 PDF 管线，关闭 OCR，TableFormer ACCURATE
样本：OCBC Annual Report 2025（276 页，InDesign 导出的原生文字 PDF，没有书签）
运行：本地 M3 Pro，请求了 MPS（日志不写实际设备）；全文 284 s，平均 1.03 s/页
设计落点：`intake-design-v1.md` 第 9 节（本文的接入方式与稳定性措施以那里为准）

## 1. 结论

Docling 可以作为 kb-intake 处理原生 PDF（流程 A）的主抽取器，DoclingDocument JSON 作为中间产物也成立。
数字对位是它最强的一项，需要后处理的都是确定性规则，不需要大模型。

| 维度 | 结果 | 处理 |
|---|---|---|
| 表格数字对位 | 重点页 24 张表，逐个数字和 pdfplumber 坐标核对，错位 0 | 直接用 |
| 表格结构 | 括号负数保留；Note 列独立；GROUP/BANK × 年份两级表头正确 | 直接用 |
| 漏识别的表 | p106 "NPAs by Period Overdue" 被拆成 34 个零散文本（全文仅此一处） | find_tables 兜底，记缺口 |
| 表头文字 | 3 张小错（p178、p176、p130），数字都对 | 对照文字层重新取字 |
| 跨页续表 | p216→217 输出两张列名相同的表，Docling 不会自己合并 | 规则拼接 |
| 标题层级 | 默认全部是 L1；开启层级恢复后附注和子附注对，章节层被放到 L6 | 用字体 + 编号重建 |
| 页眉标题 | "Notes to the Financial Statements" 作为正文标题重复出现 59 次 | 去重，归入页眉页脚 |
| bbox 溯源 | 元素和单元格的框都准确；抽查的非空单元格 100% 有框 | 统一坐标原点 |
| 阅读顺序 | p9、p84 双栏顺序正确 | 直接用 |
| 逐页置信度 | 258 页 EXCELLENT，p96（章节分隔页）FAIR | 写进 manifest |

## 2. 需要的后处理（intake 侧，全部确定性）

1. **控制字符**：清除 `\x07`（文本 327 处，单元格 65 处）和 `\x03`（3 处）。
   复核：pdftotext 也能读出 391 处 `\x07`，所以是 PDF 文字层本来就有，不是 Docling 带进来的。不管用哪个抽取器，intake 都要清。
2. **破折号**：原文有约 2000 个 en dash `–`，在财务表里表示"无/零"，Docling 统一改成了 `-`。
   规则：单元格里只有 `-` 或 `–` 时视为 nil，不能当负号；负数一律以括号形式表示。
3. **标题重建**：Geomanist 20pt 是章节，Roboto-Bold 13pt 是附注（`21.`），Roboto-Bold 10pt 是子附注（`21.1`），和编号结果完全一致。
   需要过滤 Roboto-Bold 9pt 的杂项，以及被误判为标题的 KPI 大字。这套规则只依赖文件本身，按边界划分属于 intake。产出的是"标题候选"，最终建树仍归 kb。
4. **页眉去重**：每页重复出现的标题移到页眉页脚层。
5. **跨页续表**：相邻页的表列名相同、且后一页标题带 "(continued)" 或位于页首时，拼接成一张表，并保留两段各自的页码和 bbox。
6. **坐标统一**：元素坐标原点是 BOTTOMLEFT，表格单元格是 TOPLEFT；入件包统一用 TOPLEFT（和 layout.jsonl 一致），保持全精度。
7. **表头重新取字**：用表头单元格的 bbox 去 PDF 文字层取原文，替换 Docling 识别出的表头文字。
8. **漏表兜底**：find_tables 识别出、但 Docling 没识别的表，按 Docling 对这块的判定分流：判成图表的按模型；判成正文（像 p106）且通过文字守恒检查的，用 find_tables 的结果入表，并记缺口 `table_missed`。

## 3. 接入方式：Docling 主 + find_tables 副，稳定性优先

决定：保留主副抽取。理由是稳定性：Docling 是视觉模型，出错时不会报错（p106 漏表那一页置信度照样是 EXCELLENT）；find_tables 是规则方法，出错方式和 Docling 不同。两边对不上的地方进入 gaps，不会悄悄进库。这和 OCR 主副双引擎是同一个原则。

- 入件包新增 `docling.json`（无损 DoclingDocument），manifest 里写明全部依赖的版本和模型 revision
- `tables.jsonl` 由 Docling 输出加后处理生成；`extract_method = docling | find_tables | merged`
- 三重检查：文字守恒（主）→ 与 find_tables 比对数字集合 → 漏表兜底（详见 intake-design 9.2）
- 新增 `headings.jsonl`（标题候选：文字、层级、来源 `font_rule|numbering|docling`、页码、bbox），作为 kb 建树的输入之一
- `pages.jsonl` 和 `layout.jsonl` 继续从文字层出，不依赖 Docling
- 新增缺口类型：`table_missed`、`table_header_suspect`、`table_cross_mismatch`、`table_cells_dropped`、`table_text_unaccounted`、`extractor_degraded`

### 稳定性措施

1. **依赖全部锁死**：docling、docling-core、docling-parse、docling-ibm-models 通过 uv.lock 锁定版本。Docling 几乎每天发一个版本，任何一个没锁住，输出都可能悄悄变化。
2. **模型权重固定并离线打包**：权重按 Hugging Face revision 下载后放进镜像，运行时通过 `artifacts_path` 只读本地文件，禁止运行中联网下载。
3. **确定性靠缓存**：模型产物按"原件 sha256 + 工具版本 + 模型 revision + 参数"缓存，重跑命中缓存，包逐字节一致。正式出包用 CPU、固定线程数；MPS/GPU 的浮点结果可能有细微差别，用时在 manifest 记录设备。
   待验证：CPU 清缓存重跑两遍，比较 `docling.json` 的哈希；MPS 与 CPU 的差异只记录、不作为验收。
4. **后处理全部用确定性规则**，不调用大模型。
5. **升级必须过回归**：以这次核对过的 24 张表和标题树作为基准快照，升级前全量对比，有差异就不升级。
6. **超时与失败显式化**：设置 `document_timeout`；转换 status 不是 SUCCESS，或有页面失败时，失败页退回 find_tables，写进 gaps，不静默出包。

## 4. 未覆盖 / 未核实

- 日志里有 3 次"单元格被丢弃"的警告，其中一次丢了 37 个，没能定位到具体表格；重点页没发现缺数字。接入文字守恒检查后应能定位
- CPU 清缓存重跑是否逐字节一致，还没测
- find_tables 在 OCBC 无边框财务表上的表现没有实测
- 只测了 1 份报告；另外 5 份 OCBC 报告和扫描版（流程 B）都没测
- 云端工作区连不上 Hugging Face；部署时要预先下载模型（`docling-tools models download`），并放进 Docker 镜像

## 5. 依据

本地 coding session 的 `~/docling-eval/REPORT.md` 和 `ocbc.docling.json`、`overlay_p101/125/177.png`。
本文第 2 节的 `\x07` 来源、破折号数量，以及 p106、p130、p217 的原文，已在云端用 pdftotext 复核。
