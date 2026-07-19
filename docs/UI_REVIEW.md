# DatumDock UI 与步骤三交付复验报告

> 最终结论（2026-07-19）：DatumDock 步骤三受管图片池已完成，可以在普通模式导入、转换、浏览、筛选、重命名和安全删除图片；真实标注持久化、模型和导出逻辑将在后续步骤接入。

## 1. 复验背景

步骤二首次交付后的独立审计把状态暂时降为“部分完成”，评分 78 / 100，并确认两个 P1：底层写盘异常可能越过 Service/Gateway；`library.json` 缺失或进程在“发布目录—登记索引”窗口中中断后，有效数据集目录可能从主页隐藏。普通损坏诊断还会混入步骤一演示统计，稳定标签映射与统计关系验证不足，Python 3.11 和 pytest-qt 也尚未实际运行。

本轮先把这些复现固化为失败回归，再修改实现。旧实现上新增选择性测试出现 17 项失败；修复后相同测试与扩展用例全部通过。本报告只记录证据已经覆盖的事实，不把图片、标注、模型、导出、备份或安装包描述为完成。

## 2. 已完成修复

### 2.1 启动对账与孤儿恢复

- `dataset.json` 是主页摘要恢复的事实来源，`library.json` 是登记和排序索引。
- 索引缺失、有效 UUID 目录未登记或摘要过期时，`DatasetLibraryService` 启动后自动验证并原子恢复登记。
- 标签文件、SQLite 或固定目录损坏但元数据有效时，诊断卡片保留真实名称与描述；元数据也损坏时使用 UUID 派生的中立占位名称。
- 非 UUID 目录、普通文件与符号链接只进入 `LibraryRecoveryReport`，不跟随、不删除、不移动。
- 已存在但损坏的 `library.json` 保持原始字节并触发安全降级，不自动重建覆盖。

### 2.2 Repository、Service 与 Gateway 错误边界

- Repository 在保存资料库、数据集元数据和标签集前重新执行完整模型验证，并把 I/O/验证错误转换为仓库异常。
- Service 公开变更方法只向上抛业务异常；写盘、资料库登记、回滚和恢复区转移同时失败时，错误消息保留原始原因和恢复失败原因。
- `ManagedDatasetGateway.dispatch()` 对业务异常和未预期异常都有最终安全边界，始终返回 `UiCommandResult`；`ApplicationShell` 另有防御性边界。
- Toast 不再承诺无法证明的“未留下半成品”，只说明操作失败并要求查看诊断后重试。

### 2.3 领域验证与真实诊断

- `Label.id` 与 `LabelSet.id` 必须是规范 UUID。
- 标签 ID、类别 ID 全局唯一；活动训练名按大小写不敏感规则唯一；活动颜色按大小写不敏感规则唯一。
- `reviewed_count` 不得大于 `image_count`，主页不会再产生超过 100% 的损坏复核比例。
- 损坏数据集诊断改为单页只读对话框，仅显示真实名称、UUID、原因与原文件未覆盖说明；关闭不会修改资料库。

## 3. 普通模式与预览模式边界

- `python -m datumdock` 使用 `ManagedDatasetGateway` 与 `%LOCALAPPDATA%\DatumDock` 真实资料库；只有初始化无法安全完成时才降级为 `UnavailableGateway`。
- `python -m datumdock --ui-preview` 始终使用独立 `PreviewGateway`，创建、改名、切换和关闭都不读取或修改真实资料库。
- 普通模式不使用演示图片、标签、模型或统计。步骤三已将图片导入、画布浏览、重命名、回收站和永久删除接为真实功能；标注持久化、AI、模型、YOLO/X-AnyLabeling 与备份仍明确提示后续接入。
- 新建和已有空数据集进入同一个真实工作台；顶部切换会重建当前数据集上下文，不会串入另一个数据集的数据。

## 4. Python 3.11 与自动化证据

Python 3.11 自带旧 pip 在构建隔离子进程中曾出现 PyPI TLS `SSLEOFError`。本轮没有关闭证书校验，而是使用已正常联网的新版 pip 从官方 PyPI 下载 CPython 3.11 / Windows x64 wheels，再由仓库 `.venv` 使用 `--no-index` 离线安装步骤二和开发依赖。

最终命令：

```powershell
.\.venv\Scripts\python.exe -m ruff check src tests
.\.venv\Scripts\python.exe -m ruff format --check src tests
.\.venv\Scripts\python.exe -m compileall -q src
$env:QT_QPA_PLATFORM = "offscreen"
.\.venv\Scripts\python.exe -m pytest -q
```

结果：

- Python 3.11.0、PySide6 6.11.1、pytest-qt 4.5.0；
- Ruff 与格式检查通过，53 个 Python 文件格式一致；
- `compileall` 通过；
- **88 passed、1 skipped、14 warnings**；
- 3 项 pytest-qt 真实控件回归覆盖创建并切换、写盘错误 Toast、语言切换与内容隔离；
- 唯一跳过项是当前 Windows 账户缺少创建符号链接所需权限；未知目录保留用例通过，符号链接代码分支明确拒绝跟随；
- 14 条警告均来自 Pillow 对旧 `getdata()` API 的未来弃用提示，不影响步骤二资料库结果，已留待图片处理阶段升级。

## 5. GUI、截图与真实资料库隔离

- 普通模式和 `--ui-preview` 均在 Python 3.11 下保持 Qt 事件循环存活；普通临时根只初始化一个 `library.json`，预览根产生 0 个资料库文件。
- `scripts/capture_step2_review.py` 在临时资料库创建两个数据集、重新构造 Service 后再截图；每张截图前断言当前路由，并等待启动页定时导航完成。
- `build/ui-review/step2-revalidation/` 包含 12 张原生 Windows 截图：简体中文和英文分别覆盖 1366×768、1440×900、1920×1080 的主页与空工作台。
- 每组主页/工作台 SHA-256 均不同，修复了旧证据中英文 1440×900 工作台误抓主页的问题；抽查未发现关键操作裁切。
- 完整测试前后真实 `%LOCALAPPDATA%\DatumDock` 文件树哈希均为 `2643171C90EAC3176D4E05C8A8FE0DC32BC597E88E69DE90304E9A8A16BEC6DF`。

## 6. 重新评分

| 领域 | 得分 | 说明 |
| --- | ---: | --- |
| 需求覆盖 | 30 / 30 | 步骤二真实资料库、主页、空工作台、切换和元数据操作完整；未越界宣称后续功能。 |
| 数据正确性与安全 | 29 / 30 | 原子写入、启动对账、孤儿诊断、模型复验、回滚信息和资料库哈希证据通过；符号链接自动用例受当前账户权限限制。 |
| GUI 接入与体验 | 15 / 15 | 真实创建直达空工作台，卡片、筛选、只读诊断、顶部切换和双语页面可用。 |
| 测试与稳定性 | 14 / 15 | Python 3.11 完整矩阵、pytest-qt、事件循环和 12 张路由截图通过；保留 1 项权限相关跳过。 |
| 文档与工程质量 | 9 / 10 | 架构、资料库、验收、路线图、视觉状态、启动说明和复验脚本同步；安装包隔离验证不属于步骤二。 |
| **总分** | **97 / 100** | 高于 90 分门槛，无 P0/P1；未用评分抵消任何硬性失败。 |

## 7. 步骤三图片池实施证据

- 图片经两阶段导入：后台预检生成验证过的临时 PNG/缩略图，用户完成完全重复决策后再逐张原子提交。
- SQLite v1 保存样本、哈希分桶、相似组、回收站和操作日志；v0 迁移失败回滚，不安全路径只诊断。
- 普通工作台使用每页 200 条的 Qt 虚拟模型、真实缩略图与受管 PNG 画布；预览模式保留内存矩形演示。
- 重命名、移入回收站、恢复和永久删除均有可恢复日志；索引与文件状态不能共同证明时保留现场，不猜测覆盖。
- 双数据集导入/切换/缓存隔离、Gateway 异常边界、来源树哈希不变与 10,000 条索引压力已转为正式回归。

## 8. 步骤三 Python 3.11 结果

最终命令：

```powershell
.\.venv\Scripts\python.exe -m ruff check src tests scripts
.\.venv\Scripts\python.exe -m ruff format --check src tests scripts
.\.venv\Scripts\python.exe -m compileall -q src
$env:QT_QPA_PLATFORM = "offscreen"
.\.venv\Scripts\python.exe -m pytest -q
```

- Ruff、格式检查和 `compileall` 通过。
- **127 passed、1 skipped、14 warnings**；跳过项仅因当前 Windows 账户没有创建测试符号链接的权限。
- 14 条警告仍来自步骤三正式入口未调用的旧 `services.dataset` 代码中 Pillow `getdata()` 未来弃用提示，不影响当前图片池结果。

## 9. 步骤三原生截图

`scripts/capture_step3_review.py` 使用临时资料库创建两个数据集，导入三张图，将一张移入回收站并重启 Service 后截图。

- `build/ui-review/step3-image-pool/` 共 20 张原生 Windows 截图。
- 中英文均覆盖 1366×768、1440×900、1920×1080 的主页和真实图片工作台。
- 1440×900 额外覆盖真实导入对话框、相似图检查、回收站和设置页。
- 截图使用 Windows 原生 Qt 平台；`offscreen` 只用于自动测试，因其在当前机器上不能正确渲染系统字体。

## 10. 步骤三评分

| 领域 | 得分 | 说明 |
| --- | ---: | --- |
| 需求覆盖 | 29 / 30 | 图片池主线完整；近似组内拆分留待后续产品交互。 |
| 数据正确性与安全 | 30 / 30 | 外部来源保护、路径边界、事务、操作日志和故障恢复回归通过。 |
| GUI 接入与体验 | 14 / 15 | 真实分页/画布/治理页已接入；标注数与图片状态编辑属于步骤四。 |
| 测试与稳定性 | 14 / 15 | 127 项通过，10,000 条压力与 20 张截图通过；1 项符号链接用例受权限跳过。 |
| 文档与工程质量 | 10 / 10 | 边界、启动、资料库、验收、路线图和图片池文档同步。 |
| **总分** | **97 / 100** | 高于 90 分门槛，无已知 P0/P1，不以评分抵消未完成边界。 |

## 11. 尚未完成的产品能力

- 矩形标注持久化、自动保存和图片级复核；
- 标签管理真实写入与 LabelMe/X-AnyLabeling 交换；
- ONNX/PT 模型导入、CPU/GPU 推理和自动标注；
- YOLO Detection 导出、备份和跨数据集转移；
- PyInstaller/Inno Setup 安装、卸载和无 Python 环境验证。

## English Summary

DatumDock step three completes the real managed image-pool slice and scores 97/100 with no known P0/P1 issues. Six-format EXIF-aware normalized PNG ingestion, exact-duplicate decisions, reviewable near-image groups, SQLite v1 paging, real thumbnails/canvas, batch rename, trash/restore, permanent deletion, task cancellation, and restart reconciliation are implemented behind the gateway. Python 3.11 passes 127 tests; one symlink test is skipped for Windows account permissions. Twenty native bilingual screenshots cover the three target resolutions and step-three governance pages. Persistent annotations, X-AnyLabeling, models, exports, backups, and installer delivery remain future work.
