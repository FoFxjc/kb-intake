# kb-intake 设计：服务冻结、搜索与聚类、大模型接入、界面（v1）

状态：已确认（Peter，2026-10-21）；S / L / U 完成并合并到 main，已发布 v0.3.1（2026-10-22，安装见 docs/DEPLOYMENT.md）
前提：`intake-design-v2.md`（扫描层演示，已通过 §7 验收，PR #5）。本文在它之上增加功能，不改变 v2 的流程、状态机和交付契约的已有字段
UI 原型：claude.ai artifact「kb-intake UI prototype」（四个页面：Labels & inventory、Workflow & approval、Review inbox、Confirm & deliver），已由 Peter 确认

## 1. 要做什么、按什么顺序

| 阶段 | 内容 | 结束标志 |
|---|---|---|
| S | 服务：设计完整 API（含本文新增的接口）→ 实现 → 冻结 | OpenAPI 快照进仓库；接口形状变化会让测试失败 |
| L | 大模型后端：MiniMax 接入、文档摘要、图表与图片描述、图表转表格（实验） | 有密钥时 OCBC 跑通；无密钥或关闭时流程照常 |
| U | 界面：React + Vite + PDF.js，按原型实现 | Playwright 端到端走通；Peter 真人走查 |

先 S 再 L 再 U。U 只调用冻结后的 API。

## 2. 原则（继承 v2，不变）

- 只描述，不解释；文字取自文字层；两道人工确认；本机运行；只交付文件；稳定性优先
- 大模型：**可选**、**默认关闭**、**低权重**。结果单独存放，不改动 `regions.jsonl` / `tables.jsonl` 等核心文件；失败不阻塞流程，只记 gap
- 远程模型会把区块截图与文字发给外部服务：必须由用户在配置里显式打开，UI 上明确提示，manifest 记录

## 3. 服务（阶段 S）

### 3.1 新增接口

| 方法 | 路径 | 作用 |
|---|---|---|
| GET | `/v1/jobs/{id}/source.pdf` | 原文件，支持 HTTP Range（给 PDF.js 按需加载） |
| GET | `/v1/jobs/{id}/regions` | 区块列表：`q`（全文）、`type`、`status`、`page`、`gap`、`min_score`、`max_score`、`cursor`、`limit`；按阅读顺序分页 |
| GET | `/v1/jobs/{id}/facets` | 当前过滤条件下各类型、各状态、各 gap 代码的数量（左栏计数） |
| GET | `/v1/jobs/{id}/clusters` | 相似分组（§3.3），每组带成员 `region_id` 和分组依据 |
| GET | `/v1/jobs/{id}/queue` | review 收件箱：按问题分组的待处理项，每项带状态（open / seen / resolved） |
| GET | `/v1/jobs/{id}/summary` | 文档摘要（大模型，可能为空） |
| GET | `/v1/jobs/{id}/llm` | 大模型输出列表与进度 |

现有接口保持不变；`POST /v1/jobs/{id}/review` 本来就接受一组操作，批量处理直接复用。

### 3.2 搜索

- SQLite FTS5，建在本地状态库里（不需要另装服务）
- 索引内容：区块文字、表格单元格文字、大模型描述（单独字段，命中时标明来源）
- 打标签后建索引；抓取、review、大模型完成后增量更新
- 搜索结果带页码和框，UI 在页面和缩略图上高亮

### 3.3 聚类（确定性分组，不用向量模型）

| 分组 | 依据 |
|---|---|
| 重复的页眉、页脚、页码 | 类型相同 + 文字归一化后相同 + 页面上的纵向位置带相同 + 字体相同 |
| 外形相同的图标、标志 | 分类器类别相同 + 尺寸档位相同 |
| 表头相同的表 | 首行文字相同 + 列数相同 |
| 同一问题 | gap 代码相同（如全部低置信度） |

只有成员 ≥ 2 的组才返回。组本身不改变任何数据；用户对一组执行的操作，按成员逐条写进 `review.jsonl`。

### 3.4 review 新增操作

`ack`（标记已看，不改内容）：用于收件箱里“确认没问题”的项，写进 `review.jsonl`，对应 gap 标 `resolved`（`action: ack`）。`review.jsonl` 的 `action` 枚举增加 `ack`，契约只增不改。

### 3.5 冻结

- OpenAPI 文档导出到 `contract/api/openapi-v1.json`，提交进仓库
- 测试：运行时生成的 OpenAPI 与快照不一致即失败；要改接口，必须同时更新快照并在 PR 里说明
- 冻结后接口只增不改

## 4. 大模型（阶段 L）

### 4.1 接入

```yaml
llm:
  enabled: false
  provider: openai_compatible
  base_url: https://api.minimax.io/v1
  model: MiniMax-M3            # 需支持图片输入；以 MiniMax 官方文档为准，开工前核实
  api_key_env: INTAKE_LLM_API_KEY
  remote: true                 # UI 与 manifest 据此提示
  features:
    summary: true              # 文档摘要
    describe: true             # 图表与图片描述（只作用于 workflow 设为 describe 的区块）
    chart_table: false         # 图表转表格，实验
```

### 4.2 三种用途

| 用途 | 何时运行 | 输入 | 输出 |
|---|---|---|---|
| 文档摘要 | 打标签之后（异步，不阻塞进入 workflow） | 标题与各页文字的摘录（有长度上限） | 2–4 句说明文档结构与内容 |
| 图表 / 图片描述 | 抓取阶段，workflow 处理方式为 `describe` 的区块 | 区块截图 + 区块内文字 | 类型、标题、坐标轴与单位、主要内容 |
| 图表转表格（实验） | 抓取阶段，仅图表区块，开关打开时 | 区块截图 | 估计的数据表 + 置信说明，标低权重 |

workflow 新增处理方式 `describe`（契约 v2 的 handler 枚举增加 `describe`）。

### 4.3 Docling 的接口

- 图片描述：优先用 Docling 的 `PictureDescriptionApiOptions`（OpenAI 兼容接口，需 `enable_remote_services=True`）
- 需要先核实：Docling 的图片描述能否只作用于指定的区块（按分类或按区块），还是对所有图片运行。若不能过滤，改为直接调用其描述模型类处理单个区块，或在 intake 内按同样的接口格式自己调用；在报告里说明选了哪种
- 图表转表格：Docling 首页列出了 chart understanding（柱状、饼图、折线 → 表格）。需要先核实它的入口、所用模型、能否接远程接口；不可行时用 MiniMax 按固定提示词输出表格 JSON

### 4.4 交付与复现

- 新增 `llm.jsonl`（一条输出一行）：`kind`（`summary` / `describe` / `chart_table`）、`region_id`（摘要为 null）、`provider`、`model`、`prompt_version`、`input_sha256`、`output`（`text` 或 `table`）、`weight: "low"`
- 文件里不放时间；耗时、token 用量放 `manifest.run`
- 缓存键：provider + model + prompt_version + input_sha256；重跑命中缓存，包可复现
- `manifest.llm`：`used`、`provider`、`model`、`remote`、各用途的数量
- 失败（超时、配额、拒绝）：记 gap `llm_failed`，流程继续

## 5. 界面（阶段 U）

### 5.1 技术

- React 18 + Vite + TypeScript；PDF 渲染用 PDF.js（`pdfjs-dist`，Apache-2.0，可配 `react-pdf`）；长列表用虚拟滚动
- **构建产物提交进仓库**（`src/kb_intake/service/static/`），随 Python 包发布：`uv tool install` 的用户不需要 Node。CI 检查提交的构建产物与源码一致
- 界面全部英文；断网可用（字体、库全部打包，不访问 CDN）

### 5.2 页面（按原型）

| 页面 | 要点 |
|---|---|
| 作业列表 / 上传 | 新建作业、已有作业的状态；重复上传时提示 existing job |
| Labels & inventory | 步骤条；元数据与文档摘要（标低权重、远程）；左栏区块类型与“需要留意”的计数（来自 facets）；中间区块列表（搜索、过滤、虚拟滚动）；右侧 PDF.js 页面 + 区块框叠加 |
| Workflow & approval | 每种类型的处理方式（含 describe）；三个大模型开关与远程提示；“本次将处理”实时汇总；批准 |
| 抓取进度 | 各步进度、Docling 与大模型的进度 |
| Review inbox | 按问题分组的队列与完成进度；相似分组的批量操作；上一个 / 下一个；页面叠加（区块框、单元格框、find_tables 框）；问题说明、大模型描述（紫色标签、低权重）；操作（改判、排除、ack） |
| Confirm & deliver | 汇总、遗留问题（open / resolved）、人工操作记录、交付包文件清单、确认 |

## 6. 验收

| 项 | 标准 |
|---|---|
| 冻结 | OpenAPI 快照测试存在且生效（故意改一个字段，测试失败） |
| 搜索 | OCBC：搜 “overdue” 命中 p106；搜索与过滤组合正确；响应 < 200 ms |
| 聚类 | OCBC：重复页眉、相同图标、表头相同的表各成组；批量排除写出逐条 review 记录 |
| 大模型（有密钥） | OCBC：摘要 1 条、图表描述 = workflow 选中的区块数；`llm.jsonl` 通过契约校验；第二次运行全部命中缓存，包逐字节一致（除豁免字段） |
| 大模型（无密钥或关闭） | 流程照常，UI 隐藏相关内容，无报错 |
| 界面 | Playwright 走完 OCBC：搜索、按类型过滤、批准（含 describe）、review 收件箱（p106 改判、批量排除图标、ack 一项）、确认交付；每个页面截图；控制台 0 错误 |
| 安装 | `uv tool install` 从 git 安装，无 Node，`serve` 打开即是新界面 |
| 回归 | v2 §7 的八项仍然通过 |

## 7. 不做

批量作业、OCR、Docker、kb 适配、向量语义聚类、多用户与权限。
