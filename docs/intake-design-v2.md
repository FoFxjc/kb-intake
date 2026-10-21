# kb-intake 设计 v2：扫描层演示（端到端）

状态：已确认（Peter，2026-10-21）
取代：`intake-design-v1.md` 的 §1 定位、§3 组件形态、§4 契约、§6 计划、§9.2 流程细节
仍然有效：v1 §9.3 稳定性约束（版本锁定、模型离线、缓存、子进程硬超时、失败兜底）、v1 §2 边界的判断标准
相关：`layout-design-v1.md` §2 区块类型表

## 1. 定位

kb-intake 是**扫描层**：扫描原文件，用 Docling 判断每个区块的数据类型，统计各类型的数量，按用户批准的 workflow 逐个区块抓取，经过人工 review 和确认后，把“原文件 + jsonl”写进交付文件夹，供下一步使用。

- **只描述，不解释**：intake 回答“页面上有什么、在哪里、原文是什么”。数字解析、单位、续表合并、标题层级、表格的业务类型，都是使用方的事
- **文字来自文字层**：Docling 只负责结构（区块类型、框、表格行列）；所有文字按框从原文件的文字层取。扫描件以后用 OCR，并标明来源
- **人工确认两次**：批准 workflow、确认结果。没有确认的包不会出现在交付文件夹
- **本机运行**：`uv tool install`，不需要 Docker、数据库服务；本地状态是一个 SQLite 文件
- **只交付文件**：intake 写交付文件夹，不调用、不通知使用方

## 2. 流程与作业状态

```
uploaded → labeled → planned → approved → extracted → in_review → confirmed → delivered
  上传      打标签     定 workflow  用户批准    抓取        review      确认        写入交付文件夹
```

| # | 步骤 | 做什么 | 产出（工作目录内） |
|---|---|---|---|
| 1 | 上传 | 收原文件，算 sha256，去重；收声明 | 作业记录 |
| 2 | 打标签 + 元数据 | 检测与页分类；文字层与词坐标；Docling **只跑版面检测 + 图形分类**（关表格结构、关 OCR）→ 区块清单；find_tables 只用来找“疑似漏掉的表” | 区块清单、页信息、元数据 |
| 3 | 统计 | 各类型区块数量、各类页数量、低置信度区块数、疑似漏表数、预计抓取耗时 | 统计 |
| 4 | 定 workflow | 按统计结果给出建议（套用默认模板 + 规则，例如有扫描页时提示“OCR 未实现，这些区块将记为未处理”）；用户可调整 | workflow 草稿 |
| 5 | 用户批准 | 记录批准人、时间、workflow 版本 | 批准的 workflow |
| 6 | 抓取 | 按 workflow 逐个区块处理（§3）；表格区块跑 TableFormer，单元格文字按框从文字层取 | 区块内容、表格、gaps |
| 7 | review | 逐页核对；可以**改判区块类型**、**排除区块**；被改的区块按 workflow 重新抓取 | 人工操作记录 |
| 8 | 确认 | 记录确认人、时间；包定稿，之后不能修改 | — |
| 9 | 交付 | 写入交付文件夹（§5） | 交付包 |

- 每一步完成后状态落盘（SQLite）；服务重启后作业从上次的状态继续
- 第 6 步与第 2 步的区块对应：第 2 步定下的 `region_id` 是最终的编号。第 6 步 Docling 带表格结构重跑时，按框匹配回第 2 步的表格区块（IoU ≥ 0.5）；没匹配上的表格区块记 gap，不新增区块
- 用户在第 7 步把一个区块改判成表格，而 Docling 当初没把它当成表格时：对这个框用 find_tables 抽行列结构，`extract_method = find_tables`

## 3. workflow

一份 YAML，写明每种区块类型怎么处理。模板随安装包提供（目前只有 `default`），用户可以复制后修改。

```yaml
name: default
version: 1
handlers:                 # 区块类型（layout-design §2）→ 处理方式
  title: text
  heading: text
  text: text
  list: text
  caption: text
  footnote: text
  formula: text
  toc: text
  table: table_structure
  chart: text             # 取图中文字，不解读数值
  diagram: text
  picture: discard
  logo: discard
  icon: discard
  signature: record       # 只记位置，不取内容
  stamp: record
  page_header: discard
  page_footer: discard
  unknown: unhandled
exclude_pages: []         # 例如 [1, 2]
```

| 处理方式 | 做什么 |
|---|---|
| `text` | 按框从文字层取文字 |
| `table_structure` | TableFormer 出行列结构，单元格文字按框从文字层取 |
| `record` | 只记录类型和位置 |
| `discard` | 不取内容，区块状态记为丢弃 |
| `unhandled` | 不处理，记 gap |
| `ocr` | 预留，未实现：区块记为未处理并记 gap |

## 4. review 能做的操作

| 操作 | 效果 |
|---|---|
| 改判类型 | 区块的类型改为用户指定的类型，`label_source = user`，按新类型重新抓取 |
| 排除区块 | 区块状态改为 `excluded`，不交付内容 |
| 确认 | 包定稿 |

不支持在 UI 里修改抓到的文字或数字：内容来自原文件，人工改内容等于造数据。内容有问题时记 gap，交给使用方。

## 5. 交付文件（契约 v2）

```
delivery/
  {doc_id}@{sha256前12位}/
    source.pdf
    manifest.json
    pages.jsonl
    regions.jsonl
    tables.jsonl
    layout.jsonl
    gaps.jsonl
    workflow.json
    review.jsonl
    raw/docling.json
```

- 确认之前所有东西都在工作目录里。确认时先写到交付文件夹里的临时目录（`.tmp-…`），写完后整体改名为正式目录名，所以交付文件夹里出现的包一定是完整的
- 坐标一律以左上角为原点，单位为 PDF 点，保留全精度
- 同一份原文件、同一份 workflow、同样的人工操作，生成的包除 `manifest.run` 外逐字节相同
- JSON Schema 在 `contract/v2/`；`kb-intake check <包>` 校验。契约以后只增不改

### 5.1 `source.pdf`
原文件，逐字节不变。

### 5.2 `manifest.json`

| 字段 | 内容 |
|---|---|
| `contract_version` | `"2"` |
| `package`、`doc_id` | 包名、文档 id |
| `source` | 原文件名、sha256、字节数、媒体类型 |
| `metadata` | 声明的字段 + 检测结果（页数、语言、各类页的数量、是否有文字层） |
| `stats` | 各类型区块的数量（打标签后）、各状态区块的数量（抓取后）、表格数、gaps 按代码的数量 |
| `engines` | Docling 及配套包的版本、模型 repo 与 revision、PyMuPDF 版本、推理设备 |
| `workflow` | 模板名、版本、批准人、批准时间（全文在 `workflow.json`） |
| `status` | `confirmed`；确认人、确认时间 |
| `files` | 每个文件的 sha256、行数 |
| `run` | 各步耗时、缓存命中、主机信息（不参与逐字节比较） |

### 5.3 `pages.jsonl`（一页一行）
`page_no`、`page_label`（印刷页码）、`width`、`height`、`kind`（`native` / `scanned` / `mixed` / `blank`）、`text`（整页文字，取自文字层）、`text_source`（`native`）、`excluded`（是否被 workflow 排除）

### 5.4 `regions.jsonl`（一个区块一行，核心文件）

| 字段 | 内容 |
|---|---|
| `region_id` | `{doc_id}:p{页号四位}:r{序号三位}`，按阅读顺序 |
| `page_no`、`bbox` | 位置 |
| `type`、`subtype` | layout-design §2 的类型；子类型来自图形分类器（如 `bar`、`line`、`pie`），没有为 null |
| `score` | 类型置信度 |
| `label_source` | `model` / `user` |
| `layer` | `body` / `furniture`（页眉页脚等） |
| `reading_order` | 全文档内的顺序 |
| `text` | 按框从文字层取的文字；`discard` / `record` / `excluded` 时为 null |
| `text_source` | `native`（以后有 `ocr`） |
| `font` | 区块内主要字体：`name`、`size`、`bold`（给使用方推断标题层级用） |
| `status` | `extracted` / `discarded` / `recorded` / `excluded` / `unhandled` |
| `handler` | 按 workflow 的哪种处理方式 |
| `table_id` | 表格区块对应的表，其他为 null |

### 5.5 `tables.jsonl`（一张表一行）

| 字段 | 内容 |
|---|---|
| `table_id` | `{doc_id}:t{页号四位}_{序号两位}` |
| `region_id`、`page_no`、`bbox` | 对应的区块与位置 |
| `n_rows`、`n_cols` | 行数、列数 |
| `cells` | `r`、`c`、`rs`、`cs`、`text`（按框从文字层取）、`bbox`（单元格内文字的外接框）、`bbox_source`（`text` / `words` / `cell`）、`is_header`；空单元格的 `text`、`bbox` 为 null |
| `markdown` | 方便阅读的表格文本 |
| `extract_method` | `docling`（TableFormer）/ `find_tables`（用户改判后补抽） |
| `continued_hint` | 可能是哪张表的续表（`table_id`），只做提示，不合并 |

### 5.6 `layout.jsonl`（一页一行）
`page_no`、`words`：`[x0, y0, x1, y1, text]`，取自文字层

### 5.7 `gaps.jsonl`（一个问题一行）
`code`、`page_no`、`region_id`（可选）、`table_id`（可选）、`detail`

| `code` | 含义 |
|---|---|
| `table_suspected` | find_tables 在一个非表格区块里找到了表（p106 那种），附 find_tables 的框 |
| `low_confidence` | 区块类型置信度低于阈值 |
| `table_words_unassigned` | 表格框里有词没有归到任何单元格 |
| `table_unmatched` | 打标签时是表格的区块，抓取时没匹配到 TableFormer 的结果 |
| `unhandled` | workflow 规定不处理的区块（含 `ocr` 未实现、`unknown`） |
| `no_text_layer` | 页或区块没有文字层 |
| `lang_mismatch` | 声明的语言与检测不符 |
| `extractor_degraded` | Docling 失败、超时，或某一步出错后降级 |

### 5.8 `workflow.json`
批准时的 workflow 全文 + 批准人、批准时间。

### 5.9 `review.jsonl`（一条人工操作一行）
`action`（`reclassify` / `exclude` / `confirm`）、`region_id`、`from`、`to`、`by`、`at`、`note`

### 5.10 `raw/docling.json`
Docling 的原始输出（打标签阶段与抓取阶段各一份时都保留），只用于审计和复现，使用方不需要读。

## 6. 界面与接口

**UI（静态 HTML，不需要构建）**
1. 上传：选文件、填声明
2. 标签与统计：各类型数量、各类页数量、疑似漏表、低置信度区块；点开可看页面上的区块框
3. workflow：显示建议的 workflow，可以调整每种类型的处理方式和排除页；批准按钮
4. 抓取进度
5. review：逐页看页面图、区块框（按类型着色）、表格单元格框、该页的 gaps；改判类型、排除区块；确认按钮
6. 交付：交付文件夹里的包列表、契约校验结果

**HTTP API**：作业的创建、查询状态、取统计、取 / 改 workflow、批准、取单页核对数据、提交 review 操作、确认；包列表与校验；引擎状态。`serve` 默认只监听 127.0.0.1，目录扫描受 `scan_roots` 限制（v1 中已实现的安全默认保持不变）。

**命令行（与 UI 等价，方便脚本化）**
```
kb-intake label   <file> --decl decl.yaml            # 1–3：打标签、统计，输出作业 id 和建议的 workflow
kb-intake approve <job> --workflow wf.yaml --by NAME # 4–5
kb-intake extract <job>                              # 6
kb-intake review  <job> --apply ops.yaml --by NAME   # 7：批量提交改判 / 排除
kb-intake confirm <job> --by NAME                    # 8–9：写入交付文件夹
kb-intake check   <包>
```

## 7. 演示验收

| 项 | 标准 |
|---|---|
| 端到端 | OCBC 2025 英文版、UOB 2025 各走一遍完整流程（UI 一遍、命令行一遍），交付包通过 `kb-intake check` |
| 标签与统计 | 统计数字与 `regions.jsonl` 一致；图表有子类型 |
| review | 在 OCBC p106 上把疑似漏表的区块改判为表格，确认后交付包里有这张表（`extract_method = find_tables`），`review.jsonl` 有记录 |
| 文字来源 | 抽查：区块和单元格的文字与文字层一致（不再出现破折号被改写、表头文字错乱） |
| 交付的原子性 | 确认过程中交付文件夹里不出现不完整的包 |
| 确定性 | 同一原文件、同一 workflow、同样的 review 操作，两次交付的包除 `manifest.run` 外逐字节相同 |
| 回归 | 24 张基准表：结构与数字仍然正确（文字改为取自文字层后重新核对一次） |
| 本机运行 | 照 README 用 `uv tool install` 安装、下载模型、断网完成一次端到端 |

## 8. 设计但不实现

- **批量**：一批文件共用一次 workflow 批准；统计汇总到批次；review 只看有 gap 或低置信度区块的文件，其余直接确认或抽查。命令行 `label` / `confirm` 以后接受目录
- **OCR**（扫描件、扫描区块）：`ocr` 处理方式与 `OcrEngine` 接口已预留
- **Docker**
- **kb 适配**：kb 读取契约 v2 的交付包，自己负责数字解析、单位、续表合并、标题层级

## 9. 现有代码怎么处理

| 现有 | 处理 |
|---|---|
| 检测、页分类、文字层、词坐标、印刷页码 | 保留 |
| Docling 引擎、子进程超时、缓存、模型锁定与校验、依赖校验 | 保留；打标签阶段加图形分类器（模型锁进 `models.lock.json`），关表格结构 |
| find_tables | 只用于 `table_suspected` 提示和用户改判后的补抽 |
| 区块类型映射、阅读顺序、页眉页脚分层 | 保留 |
| 单元格文字取 Docling 的识别结果、表头重新取字、带符号的文字守恒、主副数字比对 | 删除：文字一律取自文字层，守恒改为 `table_words_unassigned` |
| 续表合并、`context_above`、`value` / `nil`、标题层级与 profile、`headings.jsonl`、`tables_secondary.jsonl`、`working.pdf` | 删除（续表只保留提示） |
| `scan` 一次跑完的流程 | 拆成 §2 的分步作业 |
| UI 与 API | 按 §6 重排 |
