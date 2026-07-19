# DatumDock UI 与步骤二交付自检报告

> 当前结论：DatumDock 步骤二内部数据集资料库已完成，可以在普通模式创建、保存、打开和切换数据集；图片导入、真实标注持久化、模型和导出逻辑将在后续步骤接入。

## 1. 交付范围

- 正式入口已切换到 `ApplicationShell`；普通模式提供无副作用空主页，预览模式使用一次性内存数据。
- 已实现 16 个稳定路由、28 个集中注册弹窗、现代组件库、自有 SVG 图标和中英文资源。
- 标注工作台提供纯内存矩形创建、选择、移动、八点缩放、标签同步、缩放、适配、撤销和重做。
- 设置页提供语言即时切换、快捷键录入/冲突检查/恢复默认、回收站阈值问号帮助和其他配置外观。
- `UiGateway` 隔离新界面与旧业务服务；本轮不会创建数据集、图片、SQLite、模型、导出目录或偏好文件。

## 2. 自动验证

```powershell
python -m ruff check src tests
python -m ruff format --check src tests
py -3.11 -m compileall -q src
$env:QT_QPA_PLATFORM = "offscreen"
python -m pytest -q
```

本轮结果：Ruff 通过、格式检查通过、Python 3.11 编译检查通过、31 项 pytest 通过。普通模式与 `--ui-preview` 均完成进程级启动冒烟；16 个路由和 28 个弹窗完成遍历。

## 3. 视觉验证

本地截图位于忽略提交的 `build/ui-review/`，共 15 张，覆盖：

- 中文 1440×900：安全空主页、预览主页、标注工作台、标签管理、模型管理、设置、组件样例、YOLO 导出、危险删除；
- 英文 1366×768：主页、设置；
- 中文 125% / 1440×900：主页、标注工作台；
- 中文 150% / 1920×1080：主页、标注工作台。

检查并修正了动态卡片残留遮挡、工作台品牌区裁切、矩形控制柄重复位移、中文状态中的英文单位和危险删除范围未翻译等问题。

## 4. 自检评分

| 领域 | 得分 | 说明 |
| --- | ---: | --- |
| UI 页面与流程覆盖 | 25 / 25 | 路由、管理页、状态页和 28 个弹窗均可发现与创建。 |
| 视觉质量与一致性 | 27 / 30 | 现代冷白/浅蓝灰、品牌色、深色画布和自有图标统一；教程正文仍为演示内容。 |
| 导航与演示交互 | 19 / 20 | 页面跳转、对话框、画布、筛选、语言和快捷键均可交互；业务结果仅为内存演示。 |
| 响应式、DPI 与中英文 | 13 / 15 | 三种分辨率与三档 DPI 已检查；更极端窗口尺寸不属于本轮基线。 |
| 测试与工程质量 | 10 / 10 | 静态检查、格式、31 项回归、CLI 启动和安全边界均通过。 |
| **总分** | **94 / 100** | 达到不低于 90 分的步骤一交付要求。 |

硬性安全验收通过：普通模式和预览模式都未调用真实数据集服务，未把任何未接入业务操作伪装为成功。

## 5. 已知边界

- Python 3.11 新虚拟环境因 PyPI TLS `SSLEOFError` 无法安装依赖；已完成 3.11 `compileall`，但依赖与发布环境验证仍需网络恢复。
- 教程阅读器使用内置演示正文，不代表最终教程已经校订。
- 旧业务服务与旧界面代码仍保留，但正式入口不再使用；后续应通过 `UiGateway` 逐项迁移，不应让页面直接访问文件或 SQLite。
- 本轮不是安装包、真实导入、真实标注持久化、模型推理或 YOLO/X-AnyLabeling 导出交付。

## 6. 步骤二真实资料库交付

- 普通模式改用 `ManagedDatasetGateway`，首次启动自动建立 `%LOCALAPPDATA%\DatumDock`；资料库初始化失败时才降级到安全的 `UnavailableGateway`。
- 数据集以 UUID 作为目录名；名称只保存在元数据中。创建按“预检 → 临时目录 → 元数据/标签/索引 → 结构验证 → 原子发布 → 原子登记”执行。
- 主页已真实接入创建、搜索、排序、卡片打开、重命名、归档、恢复、损坏诊断和从其他数据集复制配置。
- 新建和已有空数据集均进入同一工作台，显示当前名称、0 张图片、空画布、空图片池与清晰导入入口。
- 顶部数据集下拉使用真实资料库快照，切换时重新加载标签、图片、模型和画布上下文；当前步骤的空数据集不会混入演示内容。
- `--ui-preview` 仍是独立内存模式，即使真实 `library.json` 损坏也不会读取或改写它。

步骤二新增或扩展的自动验证覆盖：首次初始化、重启恢复、两个数据集隔离、名称判重、Windows 非法名称和路径逃逸、UUID 目录稳定、重命名、归档/恢复、模板深复制、复制排除项、创建登记失败恢复、损坏索引保护、单项损坏隔离、真实 GUI 向导、卡片打开、顶部切换、普通模式未接入副作用、预览隔离、双语内容保护和三种窗口尺寸。

## 7. 步骤二视觉与运行验证

原生 Windows 截图位于忽略提交的 `build/ui-review/step2/`，共 12 张：简体中文和英文分别覆盖 1366×768、1440×900、1920×1080 的真实主页与空工作台。截图使用临时资料库创建两个数据集，重新构造 Service 后再渲染，因此同时验证重启恢复；临时资料库在流程结束后删除。

最终质量命令：

```powershell
python -m ruff check src tests
python -m ruff format --check src tests
py -3.11 -m compileall -q src
$env:QT_QPA_PLATFORM = "offscreen"
python -m pytest -q
```

本轮完整结果：61 项 pytest 通过；Ruff、格式检查和 Python 3.11 编译检查通过；普通模式与 `--ui-preview` 均完成真实 Qt 事件循环启动/关闭冒烟。Python 3.11 独立环境依赖安装仍受 PyPI TLS 阻塞，因此 GUI 运行使用当前已安装 PySide6 的开发解释器，未把该项描述为 Python 3.11 发布环境验证。

## 8. 步骤二自检评分

| 领域 | 得分 | 说明 |
| --- | ---: | --- |
| 需求覆盖 | 30 / 30 | 完成步骤二要求的真实资料库、主页、空工作台、切换和元数据操作；未越界宣称后续功能。 |
| 数据正确性与安全 | 29 / 30 | 原子 JSON、固定 UUID 路径、事务创建、回滚/恢复、损坏隔离和副作用测试通过；显式资料库重建工具仍属后续维护功能。 |
| GUI 接入与体验 | 15 / 15 | 保留步骤一视觉，真实创建后直达空工作台，卡片、筛选、诊断和顶部切换可用。 |
| 测试与稳定性 | 15 / 15 | 61 项完整回归、真实 GUI 路径、多分辨率、中英文及重启场景通过。 |
| 文档与工程质量 | 9 / 10 | 代码边界、中文注释、文档和启动说明已同步；Python 3.11 独立依赖环境仍受外部 TLS 阻塞。 |
| **总分** | **98 / 100** | 高于步骤二 90 分交付门槛，无安全或页面覆盖硬性失败。 |

## English Summary

DatumDock step two completes the persistent internal dataset library and scores 98/100. Normal mode now creates, saves, reopens, switches, renames, archives, restores, diagnoses, and independently clones configuration for UUID-backed datasets. New and existing empty datasets open in the real annotation workspace without demo content. Preview mode remains isolated. Ruff, formatting, Python 3.11 compilation, 61 tests, GUI smoke checks, bilingual content protection, and 12 native multi-resolution screenshots pass. Image import, annotation persistence, models, exports, backups, and installer delivery remain future work.
