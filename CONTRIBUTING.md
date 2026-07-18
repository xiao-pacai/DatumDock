# 参与贡献 DatumDock

感谢你关注 DatumDock。项目目前处于需求与工程骨架阶段；提交代码前，请先阅读产品和工程约束，避免实现与数据安全边界相冲突。

## 开始前

1. 阅读 `AGENTS.md`、`docs/PRD.md`、`docs/ARCHITECTURE.md`、`docs/UX.md` 与 `docs/ACCEPTANCE.md`。
2. 从 `docs/ROADMAP.md` 中选择未完成且边界明确的任务；一个提交尽量只解决一个主题。
3. 遇到会改变文件格式、样本删除范围、标签映射或导出语义的设计问题，先提出讨论，不要自行假定。

## 提交要求

- Python 代码注释、docstring、TODO/FIXME 和开发日志使用中文；用户可见的系统文案必须经中英文翻译资源管理。
- 必须遵循 `docs/CODE_STYLE.md` 与 `pyproject.toml` 中的 Ruff 规则；提交前运行 Ruff 检查、Ruff 格式检查和相关 pytest。
- 新增或修改 Markdown 时，中文为主并附 `## English Summary`。
- 不提交真实数据集、项目工作区、导出训练集、模型权重、个人路径、密钥或大体积缓存。
- 受管数据、标签迁移、删除、导入、备份和导出必须有明确错误处理，不能静默丢数据。
- 涉及标注、LabelMe/X-AnyLabeling 互操作、数据划分或 YOLO 导出的变更必须增加或更新自动化测试。
- UI 必须复用设计令牌和自有图标语义名，遵循莫兰迪小清新视觉系统；不得复制第三方品牌资产。

## 拉取请求

- 说明问题、方案、影响范围和验证方式。
- 视觉改动附截图或短录屏；图标改动指出替换的源资产和已验证尺寸。
- 不要在同一个 PR 混入无关格式化、重命名或生成文件。

## English Summary

Thanks for contributing to DatumDock. Read the product, architecture, UX, and acceptance documents before coding. Keep commits focused; use Chinese code comments and bilingual Markdown summaries; never commit datasets, workspaces, model weights, secrets, or caches. Changes affecting managed data, annotation interchange, splits, or YOLO export require clear failure handling and relevant tests. UI work must use the shared design tokens and DatumDock-owned icon assets.
