# kb-intake 设计 v1：把入库前的部分拆成独立库

状态：草案；决定编号拟为拆分 D49、Docling 抽取层 D50（已核实未被占用）；第 9 节"抽取层：Docling 主 + find_tables 副，稳定性优先"已确认（2026-10-20）
日期：2026-10-20（review 修订 17:30；对齐 kb #29 / #30 修订 17:45）；2026-10-21 定位纠偏：以可插拔、可独立部署的组件为目标，见第 11 节
相关：`layout-design-v1.md`（区域与类型）、`ocr-design-v1.md`（OCR）、`docling-eval-v1.md`（实测依据）

## 1. 定位

kb-intake 是一个**可插拔、可独立部署**的组件，负责把“任意文件”变成“知识库可以读的标准入件包”。它只关心文件格式，不关心知识。

- **可插拔**：每个处理阶段（检测、取文字、OCR、版面、表格、标题、检查）都是接口后面的插件，按配置选用和组合；装不装 Docling、用哪个 OCR，只改配置不改代码（§3.1）
- **可独立部署**：一个容器、一条命令起服务，带 Web UI 和 HTTP API；不依赖 kb 仓库、kb 的数据库或任何 kb 代码；运行时可离线（§3.2、§7）
- **kb 只是使用方之一**：kb 通过契约 v1 读包。任何能读契约的系统都可以接；OCBC 的 6 份年报只是测试样本，用来验证功能和 UI，不是组件的设计目标

- 输入：一个文件 + 一份声明（声明里显式写 `doc_id`；intake 只校验格式并用它给包命名，kb 导入时按自己的类型配置复算，对不上就拒收）
- 输出：一个入件包（目录或 zip），带版本号的契约
- 自带：Web UI、命令行、大模型 API 配置、本地状态
- 不做：独立 worker 进程、任务队列、调用 kb、写 Doris、写 kb 的 Postgres

kb 只读入件包，不再碰原件，也不再 import 任何格式解析代码。

## 2. 边界

| 归 kb-intake | 归 kb |
|---|---|
| 接收上传、校验声明、sha256 去重 | 建树（类型配置、PageIndex Flash） |
| 扫描分类：魔数、逐页原生/扫描/混合/空白（kb #29 的页分类）、语言 | 章节分类、切块 |
| 选择流程 A–F | 概念打标（领域包） |
| 取文字：文字层、OCR 主副双引擎、词坐标、置信度 | 表格的业务解读：`structure`（kb #30，用类型配置的实体词）、表头还原后的指标/分解/变动分类、观测抽取 |
| 版面区域：Docling 版面模型 × 原生证据融合 → 区域与类型（layout-design §2–§4） | 观测、事实合并 |
| 表格的几何部分：找表、单元格及其 bbox（Docling 主 + find_tables 副；扫描页表格识别） | 分词、嵌入、载入 Doris |
| 标题候选：字体 + 编号规则（只依赖文件本身） | 大模型顾问（节点摘要等，依赖树） |
| 印刷页 ↔ 物理页 | 检索层、agent 接口 |
| 生成带隐形文字层的工作 PDF、页面图片 | |
| 大模型作证人：复读 OCR 分歧、抽样页判类型 | |

判断标准：只依赖文件本身就能做的归 intake；需要类型配置或领域包的归 kb。

- 标题候选属于 intake，用候选建树归 kb。
- 表格按同一标准切开：`tables_of()` 里只依赖 PDF 的部分（找表、单元格、单元格框、词坐标）搬到 intake；用到 profile 实体词的部分（`structure` 计算）留在 kb，改为读入件包的 `tables.jsonl` + `layout.jsonl`。

## 3. 组件形态

一个 Python 包，一个进程。`serve` 模式下作业在同一进程的后台线程里逐个执行（一次一份，不排队、不并发），这样 UI 能看到逐步进度。

### 3.1 插件接口

每个阶段一个接口（Python Protocol），插件通过 entry point 组 `kb_intake.plugins` 注册，配置按名字选用：

| 阶段 | 接口 | 输入 → 输出 | 内置插件 |
|---|---|---|---|
| 格式检测 | `Detector` | 文件 → 格式、逐页类别、语言 | `pdf`（魔数 + 页分类） |
| 取文字 | `TextSource` | 页 → 文字、词坐标 | `pymupdf` |
| OCR | `OcrEngine` | 页图或区域 → 词、坐标、置信度 | `rapidocr`、`tesseract`（流程 B 时实现） |
| 版面 | `LayoutEngine` | 页 → 区域（layout-design §2 类型） | `docling` |
| 表格 | `TableExtractor` | 页 / 区域 → 表格（单元格 + 框） | `docling`、`find_tables` |
| 标题 | `HeadingDetector` | 文档 → 标题候选 | `font_numbering`（字体 + 编号，按 profile 或自动推断）、`docling` |
| 检查 | `Check` | 包内产物 → checks / gaps | `text_conservation`、`table_cross`、`table_missed` |

约定：
- 每个插件声明 `name`、`version`、支持的格式、是否需要模型、所需 extra（如 `kb-intake[docling]`）；manifest 记录本次实际用了哪些插件及版本
- 基础安装只依赖 PyMuPDF，不装 Docling 也能出合法包；可选插件没装或出错时降级并记 gap，不让整份失败
- 流程（A–F）是配置里的插件组合，由检测结果选择；主副关系（如表格 Docling 主、find_tables 副）也写在配置里
- 文档系列相关的规则（如 OCBC 的字体→层级映射）只能放在 profile 配置里，代码里不出现任何样本专属逻辑
- 新格式（docx、pptx、xlsx、html……）只需新增 `Detector` + `TextSource`（或复用 Docling 后端）；目前只留接口，不实现

### 3.2 HTTP API

| 方法 | 路径 | 作用 |
|---|---|---|
| GET | `/v1/health` | 存活与版本 |
| GET | `/v1/plugins` | 已安装插件、版本、是否可用（模型是否就位） |
| GET / PUT | `/v1/config` | 读取 / 修改当前配置（密钥只显示环境变量名） |
| POST | `/v1/jobs` | 上传文件 + 声明，返回 job_id |
| GET | `/v1/jobs/{id}` | 逐步状态、耗时、交叉验证结论、gaps 摘要 |
| GET | `/v1/jobs/{id}/pages/{n}` | 单页核对数据：页图、区域框、表格单元格框、词框、该页 gaps |
| GET | `/v1/packages`、`/v1/packages/{name}` | 包列表；下载 zip |
| POST | `/v1/packages/check` | 上传一个包，按契约校验 |

UI 只调这套 API；其他系统也可以直接调它来入件。和使用方之间仍然只交接文件（包），不调用使用方。

```
kb-intake run  <file> --decl decl.yaml --out ./packages     # 命令行：跑完退出
kb-intake serve --data ./intake-data --port 8700           # 启动 UI（同一进程内同步执行，一次一份）
kb-intake check <package>                                  # 按契约校验一个入件包
```

- Web 框架：FastAPI + 静态 HTML（无前端构建步骤）
- 本地状态：SQLite 文件 `intake-data/intake.db`（登记、运行记录、证人缓存），不依赖外部数据库
- 中间产物缓存：`intake-data/cache/`，键为“原件 sha256 + 工具版本 + 模型 revision + 参数”（第 9.3 节）
- 运行记录：每一步一行（步骤、工具与版本、耗时、结论、交叉验证结果、是否命中缓存）

### UI（4 个视图）

1. 上传：拖入文件，填声明 7 个字段，显示扫描分类结果和选中的流程
2. 运行：逐步状态、各步工具和版本、交叉验证结论、缺口
3. 逐页核对：左边页面图（叠加区域框，按类型着色），右边识别文字；按置信度着色；主副引擎分歧（OCR 和表格）、证人判定逐条可看
4. 入件包：清单、契约校验结果、下载 zip

### 配置 `intake.yaml`

```yaml
llm:
  enabled: false                 # 默认关闭，关了照样能出包
  provider: openai_compatible    # MiniMax / Claude / 本地模型都走兼容接口
  base_url: https://...
  model: ...
  api_key_env: INTAKE_LLM_API_KEY  # 只写环境变量名，密钥不进文件
  roles:
    witness_ocr: true            # 主副 OCR 分歧或低置信时复读
    witness_classify: true       # 抽样页判文档类型、语言
    describe_images: false       # 图片描述，作为低权重元数据放进包里
  triggers: {min_confidence: 0.85, sample_rate: 0.02}
  cache: by_hash                 # 模型 + 版本 + 提示词 + 输入哈希
ocr:
  primary: rapidocr
  secondary: tesseract
  dpi: 300
tables:
  primary: docling               # 见第 9 节；第 2 步时为 find_tables
  secondary: find_tables
docling:
  version: "2.140.0"             # 和 uv.lock 一致；升级走回归
  table_mode: accurate
  device: cpu                    # 正式出包用 cpu；本机调试可设 mps，manifest 记录
  num_threads: 4
  artifacts_path: /opt/docling-models   # 按 revision 预下载，运行时只读本地、不联网
  document_timeout: 1800
headings:
  font_profiles: profiles/fonts/  # 按文档系列的字体→层级映射；没有命中时自动统计字体分布 + 编号推断
```

UI 里有同样的配置页，可以测试连通性。

## 4. 输出契约：入件包 v1

```
{doc_id}@{sha256[:12]}/
  manifest.json    契约版本、声明、检测结果、选定流程、各步工具与版本（含 Docling 模型 revision、推理设备）、
                   缓存命中情况、交叉验证摘要、是否降级、大模型是否参与
  source.<ext>     原件
  working.pdf      带文字层的工作 PDF（原生 PDF 时就是原件）
  pages.jsonl      page_no, page_label, kind(native|scanned|mixed|blank), text, text_source(native|ocr|mixed),
                   ocr_confidence, width, height, docling_confidence
  layout.jsonl     词级坐标：page_no, words[x0,y0,x1,y1,text,source,conf]
  regions.jsonl    区域（layout-design §4）：region_id, page_no, bbox, type, subtype, score,
                   type_source(model|native|fused), agree, text_source, reading_order, status, ref,
                   layout_model, layout_version
  tables.jsonl     表格：table_id, region_ids[], pages[], bboxes[], cells[{r,c,rs,cs,text,value,nil,bbox,is_header}],
                   header_rows, markdown, extract_method(docling|find_tables|merged), continued_from, checks
  headings.jsonl   标题候选：region_id, page_no, bbox, text, level, numbering, sources[font_rule|numbering|docling], furniture
  docling.json     无损 DoclingDocument（流程 A 时有；固定版本）
  gaps.jsonl       ocr_text, image_region_unread, lang_mismatch, decl_mismatch,
                   table_missed, table_header_suspect, table_cross_mismatch, table_cells_dropped,
                   table_text_unaccounted, extractor_degraded ...
  llm.jsonl        可选：证人判定、图片描述（标明模型与版本）
  checks.json      契约自检结果
```

- JSON Schema 放在 intake 仓库的 `contract/v1/`，kb 引用同一份 schema 做校验
- 同一原件、同一配置重跑，包的内容逐字节一致（时间戳只放 manifest 的 run 字段）；靠中间产物缓存保证，见 9.3
- 坐标统一为 TOPLEFT 原点、PDF 点单位。**精度不取整**：`layout.jsonl` 的词坐标、`cells[].bbox` 保持全精度（与 kb #30 的 `structure` 计算所需一致，也保证第 2 步与 kb 现有产物逐字节对得上）；只有给 UI 用的派生字段可以取整
- 每个单元格都有 `bbox`（第 2 步用 find_tables 的 cell 框，3b 用 Docling 的单元格框）
- `cells[].value`：数字解析结果（去千分位；括号为负；单元格只有 `-`/`–` 时 `value=null`、`nil=true`）；`text` 保留原文
- 契约只增字段不改字段；改字段时升 v2

## 5. kb 侧改动

- 新增 `pipeline/import_package.py`：校验契约 → 复算 `doc_id`（`profiles.abbr(doc_type)` 等类型配置规则，含保留缩写校验），与声明不符则拒收 → 把入件包放到 `data/packages/` → 登记 document
- `document.path` 改为指向 `data/packages/{doc_id}@{sha12}/working.pdf`（kb 实际读取的文件）；原生 PDF 时 working.pdf 与原件逐字节相同
- `build.py` 的页文字、词坐标、表格改读 `pages.jsonl`、`layout.jsonl`、`tables.jsonl`
- 表格：kb 保留 `structure` 计算（#30 的逻辑，用 profile 实体词），输入从“自己跑 find_tables + 词坐标”改为读 `tables.jsonl` 的单元格及框 + `layout.jsonl` 的全精度词坐标；字段映射写在一个适配层里。3b 之后同一个适配层接 Docling 产出的单元格
- 建树读 `headings.jsonl` 作为骨架候选；建树和 `service/read.py` 的页面渲染改读 `working.pdf`
- 文档页的区域叠加层读 `regions.jsonl`（layout-design §5）
- 删除：`pipeline/registry.py` 的上传和去重部分、`ingest.detect()`（含 #29 的页分类，搬到 intake）、`tables_of()` 中只依赖 PDF 的部分、`build.py` 的 layout 写出、`tree.page_labels()`
- Postgres：`source` 由入件包 manifest 写入；`ingest_run` 只保留 kb 自己的阶段（tree、build、load），intake 的运行记录随包附带
- 管理接口的“触发入库”改为“导入入件包”

## 6. 实施计划

### 6.1 已完成（2026-10-20）

| 步 | 内容 | 状态 |
|---|---|---|
| 0 | kb 基线 tag `pre-intake-baseline` | 完成 |
| 1–3 | 拆仓库、契约 v1、流程 A 出包；kb 改读包（kb#32） | PR 已开：kb-intake#1、#2，kb#32 |
| 3b | Docling 表格主抽取、后处理、三重检查、标题候选（intake 侧） | 主体完成，未开 PR |

第 0–3 步证明了“搬出来不改变结果”。这部分到此为止。

### 6.2 接下来：做成组件（按顺序）

| 步 | 内容 | 验收 |
|---|---|---|
| C | 插件化（§3.1）：把现有 detect / native / 3b 代码重构到接口后面；注册表 + 配置选用；Docling 改为可选 extra；样本专属规则移进 profile | 三种配置都能出合法包且通过 `check`：①基础安装（无 Docling）②Docling 主 + find_tables 副 ③关闭表格抽取；切换只改配置。`examples/` 下一个最小第三方插件，安装后出现在 `/v1/plugins` 并能被选用。24 张基准表与确定性测试仍然通过 |
| D | 服务与 UI：§3.2 的 API + 4 个视图（上传、运行进度、逐页核对、入件包）+ 配置页 | 浏览器里上传任意 PDF → 看到逐步进度 → 逐页核对看到区域框、表格单元格、gaps → 下载的包通过契约校验。用 OCBC 年报和至少一份非 OCBC 的 PDF 各走一遍 |
| E | 独立部署：Dockerfile（基础镜像 + 带 Docling 权重的镜像两种）、`uv tool install` | 在一台没有 kb 仓库的机器上，一条 `docker run` 起服务；断网可用；数据目录挂载后重启不丢 |
| F | 流程 B（扫描 PDF）：OCR 插件 | 合成扫描集出包；数字串一致率达标；分歧都有证人记录或缺口 |

### 6.3 不在组件范围内（使用方各自的事）

- kb 用 3b 包重建、31 题评测、观测与事实的逐条差异：等 kb 决定接入 3b 包时，作为 kb 的任务单独做
- 某个文档系列的规则调优（如中文版年报的子附注识别）：写进对应 profile，按需做
## 7. 部署

- 单容器：`docker run -p 8700:8700 -v ./intake-data:/data -e INTAKE_LLM_API_KEY=... kb-intake serve`
- 两种镜像：`kb-intake:base`（只有 PyMuPDF）、`kb-intake:docling`（带按 revision 固定的模型权重）
- 配置：挂载 `intake.yaml`，或用 `INTAKE_*` 环境变量覆盖；不读任何 `KB_*` 变量
- 或本机：`uv tool install git+https://github.com/FoFxjc/kb-intake` 后 `kb-intake serve`
- Docling 模型权重在构建镜像时用 `docling-tools models download` 按 Hugging Face revision 预下载，放在 `artifacts_path`；企业内网环境无需连 Hugging Face
- kb 与 intake 之间只有文件交接：共享目录、手动上传 zip，或 kb 管理页“导入入件包”

## 8. 不做

独立 worker 进程、消息队列、intake 调 kb 的接口、intake 写 kb 的数据库、多租户、权限系统。

## 9. 抽取层：Docling 主 + find_tables 副，稳定性优先（已确认）

依据见 `docling-eval-v1.md`（OCBC AR 2025 实测：重点页 24 张表数字错位 0；1 张漏识别；3 张表头文字小错；全文 1.03 s/页）。

### 9.1 为什么是主副

出发点是稳定性。Docling 是视觉模型，出错时不会报错（漏表那一页置信度照样是 EXCELLENT）；find_tables 是基于坐标和线条的规则方法，出错方式和 Docling 不同。两边对不上就是一个可以定位的信号，进入 gaps，不会悄悄进库。这和 OCR 主副双引擎是同一个原则。

### 9.2 流程（原生页）

1. 文字层 → `pages.jsonl`、`layout.jsonl`（不依赖 Docling）
2. Docling 标准管线（关闭 OCR，TableFormer ACCURATE）→ `docling.json`（进缓存）
3. find_tables 跑同一批页 → 副候选
4. 区域：Docling 版面结果映射到 layout-design §2 的类型表，按 layout-design §3.2 与原生证据融合 → `regions.jsonl`
5. 后处理（全部确定性，不调用大模型）：
   - 清控制字符 `\x07`、`\x03`（PDF 文字层本来就有，和抽取器无关）
   - 单元格只有 `-` 或 `–` 时：`value = null`、`nil = true`，不当负号；负数只认括号
   - 表头单元格用 bbox 回文字层重新取字，与 Docling 文字不一致时以文字层为准，记 `table_header_suspect`
   - 跨页续表：相邻页列名相同，且后表位于页首或标题带 "(continued)" → 合并为一张表，`extract_method=merged`，保留各段页码和 bbox
   - 坐标统一为 TOPLEFT（Docling 元素是 BOTTOMLEFT，单元格是 TOPLEFT）
6. 三重检查（从强到弱）：
   1. **文字守恒**（主检查，最便宜、最确定）：表格 bbox 内文字层的每个数字词，必须在 Docling 的单元格里恰好出现一次；单元格里的每个数字也必须能在文字层找到。缺的记 `table_text_unaccounted`（带页码、词坐标），多的记 `table_cells_dropped` 或 `table_cross_mismatch`。Docling 日志里“丢弃单元格”的警告靠这一步定位。
   2. **主副比对**：按 bbox 重叠匹配两边的表（IoU ≥ 0.5，或一边被另一边包含），比较数字多重集合；不一致记 `table_cross_mismatch`，两边结果都留在包里。
   3. **漏表兜底**：find_tables 有、Docling 没有时，看 Docling 把这块判成了什么：
      - 判成 `chart` / `diagram` / `picture` → 按模型，find_tables 结果记为误识别（图表上的坐标轴数字常被 find_tables 误当成表）
      - 判成 `text`，或不在任何区域里，且 find_tables 的表通过文字守恒检查（如 p106）→ 用 find_tables 结果入表，`extract_method=find_tables`，记 `table_missed`
      - 两边都没有、但区域内多行数字按列对齐 → 只记 `table_missed`，不入表
7. 标题候选：字体 + 编号规则为主（OCBC 上：Geomanist 20pt = 章节，Roboto-Bold 13pt = 附注 `21.`，Roboto-Bold 10pt = 子附注 `21.1`），Docling 的标题作为佐证；每页重复的标题标 `furniture=true`。字体映射按文档系列放配置；没有配置时按字体 × 字号分布 + 编号自动推断，并在 manifest 标明来源

### 9.3 稳定性约束

1. **依赖全部锁死**：docling、docling-core、docling-parse、docling-ibm-models 通过 uv.lock 锁定版本，记入 manifest。Docling 几乎每天发一个版本，任何一个没锁住，输出都可能悄悄变化。
2. **模型权重固定并离线**：按 Hugging Face revision 下载后放进镜像，运行时通过 `artifacts_path` 只读本地文件，禁止运行中联网下载。
3. **确定性靠缓存，不靠引擎**（与 ocr-design §1、layout-design §3.5 同一做法）：`docling.json` 等模型产物按“原件 sha256 + 工具版本 + 模型 revision + 参数”缓存；重跑命中缓存直接复用，保证同一原件的包逐字节一致。正式出包用 CPU、固定线程数；用 MPS/GPU 时 manifest 记录设备。
4. **升级必须过回归**：以 docling-eval 核对过的 24 张表和标题树作为基准快照，再加上 6 份报告的包差异审查；有差异就不升级。升级会让缓存键失效，必然重新推理，所以回归是升级的唯一入口。
5. **超时与失败显式化**：设置 `document_timeout`；转换 status 不是 SUCCESS，或有页面失败时，失败页退回 find_tables，记 `extractor_degraded`，manifest 标明降级。不静默出包，也不让整份失败。
6. **失败隔离**：Docling 出错不影响文字层产物（`pages.jsonl`、`layout.jsonl`）。

### 9.4 3b 验收

| 项 | 标准 |
|---|---|
| 基准快照 | docling-eval 的 24 张表：数字与快照一致；p106 以 `table_missed` + find_tables 结果出现；p216→217 合并为一张表 |
| 文字守恒 | 6 份报告所有表：未解释的数字词全部进入 gaps；gaps 条数写进报告 |
| 标题候选 | OCBC 2025 英文版：章节 / 附注 / 子附注三层与编号一致；页眉标题全部 `furniture=true` |
| 确定性 | 同机连续出包两次逐字节一致（第二次命中缓存）；清缓存后 CPU 重跑，`docling.json` 哈希一致 |
| 下游 | kb 用新包重建后（`structure` 走同一适配层），31 题评测不低于基线；观测与事实条数变化逐条列出原因 |
| 耗时 | 记录每份报告的耗时（CPU 与 MPS 各一次） |

## 10. 与 layout-design、ocr-design 的关系

- layout-design 的区域阶段（L-2）在 intake 里实现，产物是 `regions.jsonl`；kb 只读。
- layout-design L-1 的选型（Docling vs PP-StructureV3）：demo 先采用 Docling 作为版面引擎（MIT、实测表格最强、已有完整评估），引擎接在区域接口后面；PP-StructureV3 对比推迟，待 Peter 确认。
- layout-design §3.2 的表格融合规则以本文 9.2 为准：行列结构用 Docling（TableFormer），find_tables 做比对和兜底。
- ocr-design 的页分类（kb #29）在第 1 步随 `ingest.detect()` 搬到 intake；OCR 阶段在 intake 里实现（流程 B，第 6 步），不在 3b 范围内。

## 11. 修订记录

- 2026-10-20 17:30 review：
  - 契约补 `regions.jsonl`，与 layout-design 的区域模型对齐；`headings.jsonl` 和 `tables.jsonl` 通过 `region_id` 关联到区域
  - 9.2 增加“文字守恒”检查作为主检查，用来定位 Docling 丢弃的单元格；主副比对的匹配规则写明
  - 9.2 漏表兜底按模型判定的类型分流：判成图表的按模型，判成正文且通过文字守恒的用 find_tables（修正了 layout-design §3.2 原规则会丢掉 p106 这类真表的问题）
  - 9.3 的确定性改为“靠缓存”，与 ocr-design、layout-design 一致；不再要求不同设备逐字节一致
  - 新增 9.4 验收、第 10 节与相关设计的关系
- 2026-10-20 17:45 对齐 kb 现状（coding session 提出）：
  - 决定编号不再写死 D46（D46–D48 已被 #26、#29、#30 占用）
  - 新增第 0 步：先合并 #26、#29、#30，以合并后的 main 打基线 tag，“逐字节不变”都对它
  - 边界：`tables_of()` 按“是否用到类型配置”切开；#30 的 `structure` 用 profile 实体词，留在 kb，改读包
  - 契约：坐标不再取 2 位小数，词坐标和单元格框保持全精度；每个单元格必须有 bbox
  - #29 的页分类随 `ingest.detect()` 搬到 intake
- 2026-10-20 17:50 第 0–3 步开工前确认：
  - `doc_id` 写在声明里，kb 导入时复算校验（`profiles.abbr` 属于类型配置，intake 不能 import）
  - `document.path` 指向 working.pdf；第 3 步逐字节验收豁免这一个字段
  - 编号：拆分 D49，Docling 抽取层 D50
- 2026-10-21 定位纠偏（Peter）：
  - 目标是可插拔、可独立部署的组件；现有文件只用来测试功能和 UI
  - 新增 §3.1 插件接口、§3.2 HTTP API；第 6 节改为实施计划：C 插件化 → D 服务与 UI → E 独立部署 → F 扫描件
  - kb 侧的 3b 重建与评测移出组件范围（§6.3）
