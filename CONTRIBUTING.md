# 参与贡献 DatumDock

感谢你关注 DatumDock。项目目前处于需求与工程骨架阶段；提交代码前，请先阅读产品和工程约束，避免实现与数据安全边界相冲突。

## 开始前

1. 阅读 `AGENTS.md`、`docs/PRD.md`、`docs/ARCHITECTURE.md`、`docs/UX.md`、`docs/VISUAL_DESIGN.md` 与 `docs/ACCEPTANCE.md`。
2. 从 `docs/ROADMAP.md` 中选择未完成且边界明确的任务；一个提交尽量只解决一个主题。
3. 遇到会改变文件格式、样本删除范围、标签映射或导出语义的设计问题，先提出讨论，不要自行假定。

## 提交要求

- Python 代码注释、docstring、TODO/FIXME 和开发日志使用中文；用户可见的系统文案必须经中英文翻译资源管理。
- 必须遵循 `docs/CODE_STYLE.md` 与 `pyproject.toml` 中的 Ruff 规则；提交前运行 Ruff 检查、Ruff 格式检查和相关 pytest。
- 新增或修改 Markdown 时，中文为主并附 `## English Summary`。
- 不提交真实数据集、项目工作区、导出训练集、模型权重、个人路径、密钥或大体积缓存。
- 受管数据、标签迁移、删除、导入、备份和导出必须有明确错误处理，不能静默丢数据。
- 涉及标注、LabelMe/X-AnyLabeling 互操作、数据划分或 YOLO 导出的变更必须增加或更新自动化测试。
- 可复现实机缺陷先添加能在旧实现失败的回归；鼠标双击、释放、快捷键焦点等时序必须使用真实 Qt 事件序列覆盖。
- 完整回归不得带有 DatumDock 自身弃用警告。提交前生成分支覆盖率；核心文件事务、标注和互操作模块目标 90%，其他当前生产模块目标 85%。可信依赖阻塞必须写入路线图，禁止关闭 TLS 校验规避。
- UI 必须复用现代视觉 v2 的设计令牌、组件层和自有图标语义名；主页可借鉴 Scratch 的友好引导，标注页可借鉴 X-AnyLabeling 的高效工作流，但不得复制第三方品牌、图标、代码或像素布局。

## 拉取请求

- 说明问题、方案、影响范围和验证方式。
- 视觉改动附截图或短录屏；图标改动指出替换的源资产和已验证尺寸。
- 不要在同一个 PR 混入无关格式化、重命名或生成文件。

## English Summary

Thanks for contributing to DatumDock. Keep commits focused, preserve managed-data safety, and turn reproduced field defects into real regression tests before fixing them. Full verification includes warning-free project code, branch coverage, and real Qt input-order tests where applicable. Never bypass trusted TLS or fabricate unavailable quality evidence.
