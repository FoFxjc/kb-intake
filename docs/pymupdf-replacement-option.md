# PyMuPDF 替换方案（备选，暂不实施）

状态：备选方案（Peter，2026-10-22）。kb-intake 目前只在公司内部使用，先在公司环境里测试并验证流程；需要时再按本文替换。

## 背景

PyMuPDF 是 AGPL-3.0（或 Artifex 商业许可）。它只是 kb-intake 自己直接引入的依赖；Docling 不依赖它（Docling 用 docling-parse 和 pypdfium2，二者已在环境中）。替换掉 kb-intake 自己的用法，整个安装里就不再有 AGPL 组件。

## 用途与替代

| 用途 | 推荐替代 | 许可证 |
|---|---|---|
| 取词与坐标、按框取文字、字体名 / 字号 / 加粗 | pdfplumber（pdfminer.six） | MIT |
| find_tables（疑似漏表、review 补抽） | pdfplumber `find_tables`（PyMuPDF 的实现移植自它） | MIT |
| 页面渲染（页面图片、大模型截图） | pypdfium2（已安装） | Apache-2.0 / BSD-3 |
| 印刷页码（PDF 页标签） | pypdfium2 底层接口或 pypdf | Apache-2.0 / BSD-3 |
| 页面图片位置（页分类） | pypdfium2 页面对象 | Apache-2.0 / BSD-3 |
| doctor 冒烟测试用的小 PDF | 仓库内放一份预生成的 PDF | — |

备选：取词用 docling-parse（C++、快、与 Docling 看到的文字层一致），但它没有表格检测，pdfplumber 仍需要。

## 风险

- pdfminer 是纯 Python，比 MuPDF 慢；276 页估计多 1–2 分钟（待实测）
- 分词、连字、空白、控制字符的处理不同，交付包的文字和坐标会变，确定性基线要重建
- 约十几个模块要改，1–2 天；包内容变化，应发布 v0.4.0

## 实施步骤（需要时）

1. 小规模验证（约半天）：只把取词和 find_tables 换成 pdfplumber，在 OCBC 上跑 24 张基准表（1,906 个数字必须逐格一致）、p106 疑似漏表、耗时
2. 通过后全面替换，回归 v2 §7 与 UI 设计 §6，发布 v0.4.0
