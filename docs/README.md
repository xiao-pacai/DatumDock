# DatumDock 文档导航

本目录是 DatumDock 的产品与工程事实来源。开始任何功能前，先按以下顺序阅读；当文档之间出现冲突时，以更具体的约束为准，并在实现前更新冲突处，而不是自行猜测。

## 推荐阅读顺序

1. [根目录 README](../README.md)：项目定位、当前阶段、仓库入口和 GitHub 说明。
2. [内部数据集主页与存档式管理方案](DATASET_LIBRARY.md)：最新且优先的入口、术语、内部存储和旧结构迁移决定。
3. [产品需求文档](PRD.md)：用户需求、MVP 范围、数据格式、非功能性要求与待确认决策。
4. [架构说明](ARCHITECTURE.md)：领域对象、服务边界、受管存储、并发任务和数据安全规则。
5. [交互与界面规范](UX.md)：页面结构、标注操作、莫兰迪视觉系统、图标和交互反馈。
6. [代码规范](CODE_STYLE.md)：Ruff、测试、中文注释和 Python 编码约定。
7. [路线图](ROADMAP.md)：当前应执行的最小可验证任务。
8. [验收标准](ACCEPTANCE.md)：功能完成前必须满足的可操作或可自动验证条件。
9. [X-AnyLabeling 对标基线](X_ANYLABELING_BASELINE.md) 与 [互操作规范](X_ANYLABELING_INTEROP.md)：对外宣称兼容/对标前必须满足的质量与格式边界。

## 文档职责

| 文档 | 唯一职责 | 不应替代 |
| --- | --- | --- |
| `DATASET_LIBRARY.md` | 定义存档式主页、用户可见层级、软件内部资料库与旧结构迁移方向。 | 已实现状态与底层模块细节。 |
| `PRD.md` | 定义用户真正需要什么、MVP 边界和待确认产品选择。 | 架构实现细节与任务状态。 |
| `ARCHITECTURE.md` | 定义数据模型、存储、服务边界和一致性规则。 | 页面视觉和逐项验收。 |
| `UX.md` | 定义用户操作、布局、视觉令牌与反馈。 | 数据格式和持久化细节。 |
| `CODE_STYLE.md` | 定义代码格式、中文注释和测试执行方式。 | 业务需求和产品优先级。 |
| `ROADMAP.md` | 定义开发顺序与进行状态。 | 完成质量的唯一证明。 |
| `ACCEPTANCE.md` | 定义完成的可验证条件。 | 功能设计或实现计划。 |
| `X_ANYLABELING_*.md` | 定义对标等级和外部格式互操作边界。 | 通用项目需求。 |

## 开发前检查

- [ ] 已确认当前任务位于 `ROADMAP.md` 的最高优先级未完成项，且其边界清晰。
- [ ] 已阅读关联的 PRD、架构、UX、代码规范和验收条目。
- [ ] 未改变受管数据、标签映射、导出格式或删除范围的既定边界；如必须改变，先更新文档并请求产品确认。
- [ ] 已确认新代码能遵循 Ruff、中文注释和测试要求。
- [ ] 已明确将运行的验证命令，以及成功/失败时应保留或回滚的数据。

## 维护规则

- 功能需求改变时先更新 PRD；实现边界改变时同步更新架构；交互改变时同步更新 UX；完成状态只在验收满足后更新路线图。
- 不把“计划中”写成“已实现”，不把未验证的兼容性写成“支持”。
- 所有 Markdown 中文为主，文件结尾保留英文摘要；链接优先使用仓库相对路径，方便 GitHub 和本地同时打开。

## English Summary

This directory is DatumDock's product and engineering source of truth. Read the root README, managed dataset library plan, PRD, architecture, UX, code style, roadmap, acceptance criteria, and X-AnyLabeling documents in the listed order before starting a feature. `DATASET_LIBRARY.md` is the latest authority for replacing the visible workspace/project hierarchy with a game-save-like dataset home page and app-managed storage. Each document has a distinct responsibility: requirements define scope, architecture defines safe implementation boundaries, UX defines interaction and visual rules, code style defines formatting and Chinese comments, the roadmap defines order, and acceptance proves completion. Update the relevant document before changing a boundary, and never describe planned or unverified behavior as implemented or supported.
