# DatumDock UI 原型页面清单

> 状态：步骤一已实现并通过视觉原型验收。所有条目只代表界面与内存演示交互，不代表真实数据集、模型或导出逻辑已经接入。

## 0. 本轮实现记录

- 16 / 16 个 `RouteId` 已注册并完成遍历测试；预览专用组件页只在 `--ui-preview` 出现。
- 28 / 28 个 `DialogId` 已集中注册，可打开、切换语言、校验、取消和关闭。
- 普通模式使用 `UnavailableGateway`，预览模式使用 `PreviewGateway`；两者均不调用真实文件、SQLite、模型或导出服务。
- 已生成 15 张核心审核截图，覆盖安全空主页、中英文、1366×768 / 1440×900 / 1920×1080 和 100% / 125% / 150% DPI。
- Ruff、格式检查、Python 3.11 `compileall`、31 项 pytest 和普通/预览 CLI 启动冒烟均通过。

## 1. 运行边界

- 普通启动显示安全空主页，任何数据操作只发出“功能待接入”提示。
- `--ui-preview` 使用独立内存演示数据，关闭程序后全部丢弃。
- 新界面不调用旧 `WorkspaceService`、文件系统、SQLite、模型推理或导出服务。
- 所有页面通过稳定路由访问，所有弹窗通过统一注册表创建。

## 2. 页面路由

| 路由 | 页面 | 主要入口 | 必须展示的状态 |
| --- | --- | --- | --- |
| `startup` | 启动加载 | 应用启动 | Logo、加载提示 |
| `home` | 数据集主页 | 默认首页、返回主页 | 正常、空、加载、错误、损坏卡片 |
| `learning_center` | 学习中心 | 首页教程区 | 分类、搜索、阅读进度 |
| `tutorial_reader` | 教程阅读器 | 教程卡片、帮助入口 | 目录、章节、上一步/下一步、完成状态 |
| `release_notes` | 新功能与版本说明 | 首页和关于入口 | 当前版本、本地内容说明 |
| `about` | 关于 DatumDock | 首页和设置 | 完整 Logo、版本、许可证 |
| `annotation_workspace` | 标注工作台 | 数据集卡片、新建完成 | 顶部、左侧、画布、右侧和状态栏四区布局 |
| `label_manager` | 标签管理 | 工作台顶部 | 表格、筛选、详情、颜色和使用量 |
| `label_inspection` | 标签图片检查 | 标签行“查看图片” | 网格/列表、状态、框数、高亮跳转 |
| `label_comparison` | 标签集比较与合并 | 标签管理、数据集操作 | 一致、显示差异、缺失、阻断冲突 |
| `model_manager` | 模型管理 | 工作台顶部 | 模型表、验证状态、标签映射 |
| `similarity_review` | 相似图片检查 | 更多操作、数据治理 | 候选组、相似度、确认/忽略 |
| `trash` | 回收站 | 更多操作、设置 | 可恢复项、空状态、永久清空 |
| `dataset_overview` | 数据集详情与统计 | 数据集卡片、更多操作 | 图片、标签、状态、质量和存储摘要 |
| `settings` | 设置 | 首页、工作台顶部 | 常规、语言、快捷键、数据、显示、教程、关于 |
| `component_gallery` | 组件与状态样例 | 仅界面预览模式 | normal、hover、selected、disabled、loading、empty、error |

## 3. 弹窗与向导

| 标识 | 界面 | 预览交互 |
| --- | --- | --- |
| `create_dataset` | 新建空数据集 | 名称校验、描述、创建摘要 |
| `create_from_template` | 从数据集复制配置 | 源选择、复制范围、不会复制样本提示 |
| `dataset_diagnostics` | 损坏数据集诊断 | 问题摘要与后续恢复入口 |
| `rename_dataset` | 重命名数据集 | 名称校验和预览 |
| `archive_dataset` | 归档数据集 | 影响说明与确认 |
| `label_editor` | 标签创建/编辑 | 英文名、别名、描述、同义词、类别 ID |
| `label_color` | 标签颜色选择 | 调色板、占用颜色和冲突提示 |
| `model_import` | 模型导入 | 文件外观、解析阶段与格式提示 |
| `model_inspection` | 参数探测结果 | 输入输出、尺寸、类别和警告 |
| `model_mapping` | 模型类别映射 | 逐类别映射和未映射警告 |
| `auto_annotation` | 自动标注配置 | 模型、范围、阈值和后端 |
| `cpu_fallback` | CPU 回退说明 | 继续和打开 GPU 指引 |
| `gpu_guide` | GPU 配置说明 | 环境检查步骤和故障排查 |
| `image_import` | 图片导入 | 来源外观、支持格式、统一 PNG 说明 |
| `duplicate_compare` | 完全重复图片比较 | 并排预览、跳过或保留 |
| `import_report` | 导入进度与报告 | 阶段进度、取消、成功/重复/失败统计 |
| `rename_samples` | 批量重命名 | 规则、旧名/新名和冲突预览 |
| `delete_current` | 删除当前图片 | 关联项、回收站和永久删除说明 |
| `delete_batch` | 批量删除 | 数量、阈值、恢复性和影响范围 |
| `yolo_export` | YOLO Detection 导出 | 范围、比例、种子、统计和目录结构预览 |
| `xany_exchange` | X-AnyLabeling 导入/导出 | 方向选择、兼容 shape 和目录说明 |
| `backup_export` | 数据集备份导出 | 包含项、模型二进制排除说明 |
| `backup_import` | 备份导入与完整性检查 | 版本、清单、校验和迁入名称 |
| `dataset_transfer` | 数据集复制/移动/合并 | 标签签名、重复图和执行方式 |
| `task_center` | 后台任务 | 进度、取消和错误详情 |
| `save_error` | 保存失败 | 重试、放弃内存修改、取消 |
| `json_error` | 损坏 JSON | 原文件保护和诊断详情 |
| `unsupported_model` | 不支持模型 | 原因、支持范围和下一步 |

## 4. 可复用组件

- 品牌区、页面标题、面包屑、预览横幅、响应式操作栏和 Toast。
- 主按钮、次按钮、幽灵按钮、危险按钮、图标工具按钮和分段按钮。
- 输入框、搜索框、下拉框、筛选 chip、开关、步进输入和帮助问号。
- 数据集卡片、教程卡片、统计卡片、快速开始步骤和状态徽标。
- 表格、分页/虚拟列表、缩略图项、标注项和数据差异行。
- 空状态、加载骨架、错误状态、内联验证、进度条和错误详情。
- 标注画布、矩形框、标签浮层、置信度、八点控制柄和选中同步。

## 5. 验收矩阵

- 路由注册测试遍历全部页面并验证可创建、进入和返回。
- 对话框注册测试遍历全部弹窗并验证可打开和关闭。
- 中英文键集合一致，切换语言不改写演示数据内容。
- 普通模式与预览模式均不创建数据集、SQLite、图片、模型或导出文件。
- 1366×768、1440×900、1920×1080 与 100%、125%、150% DPI 完成布局和截图检查。
- 核心截图包含主页、工作台、标签、模型、设置、YOLO 向导和危险确认。

## English Summary

This inventory defines every page, dialog, state, and reusable component in DatumDock's completed UI-only prototype phase. All 16 routes and 28 dialogs are registered and tested. Normal mode remains side-effect free, while `--ui-preview` uses disposable in-memory demo data. This does not mean that real dataset, model, or export services are connected.
