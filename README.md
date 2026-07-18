<p align="center">
  <img src="assets/brand/datumdock-wordmark-v3.png" width="440" alt="DatumDock Logo">
</p>

<h1 align="center">DatumDock</h1>

<p align="center">本地优先的视觉数据集管理与标注桌面应用</p>

<p align="center">
  <a href="#当前状态">当前状态</a> ·
  <a href="#核心能力">核心能力</a> ·
  <a href="#项目文档">项目文档</a> ·
  <a href="#english-summary">English</a>
</p>

> 🎨 **步骤一已完成**：DatumDock 全量 UI 原型已准备好供视觉审核。普通模式是无副作用空主页，`--ui-preview` 使用关闭即丢弃的内存演示数据。真实数据集、模型、导入、导出和删除逻辑尚未接入，请不要把原型当作数据管理成品。

DatumDock 用于把分散在本地文件夹中的视觉数据，集中到安全、可追踪的数据集池中进行管理、标注、复核与导出。它的重点不只是“画框”，而是让多个独立数据集、标签体系、模型和训练导出在一个清晰的桌面工作流内协作。

> **入口已完成视觉重构（2026-07-19）**：新 GUI 不再显示工作区、项目树或打开目录。首页以类似游戏存档的方式展示受管数据集，点击卡片直接进入标注工作台。当前仅完成界面和内存交互，软件内部持久化将在后续阶段接入。完整边界见 [内部数据集主页与存档式管理方案](docs/DATASET_LIBRARY.md)。

## 当前状态

正式启动入口已切换到现代 PySide6 应用外壳。界面包含数据集主页、学习中心、四区标注工作台、标签/模型/相似图/回收站/统计管理页、设置页、16 个稳定路由和 28 个集中注册弹窗。工作台支持纯内存矩形选择、创建、移动、八点缩放、缩放/适配、撤销/重做和列表同步；快捷键录入、冲突检查与恢复默认同样只作用于当前预览会话。

`python -m datumdock` 显示安全空主页，未接入操作只提示“功能待接入”；`python -m datumdock --ui-preview` 显示完整演示状态，并持续标记“界面预览”。新外壳不会调用旧工作区、文件、SQLite、模型或导出服务。仓库中的旧服务和旧界面代码仍保留，供后续业务迁移与既有回归使用。

当前已验证 Ruff、格式检查、31 项 pytest、普通/预览 CLI 启动、16 个路由、28 个弹窗、中英文切换，以及 100%/125%/150% DPI 核心截图。由于当前环境访问 PyPI 出现 TLS 错误，新的 Python 3.11 虚拟环境尚未完成依赖安装；本轮使用已具备 PySide6 的开发环境验证，详情见 [路线图的外部阻塞记录](docs/ROADMAP.md#当前外部阻塞记录)。

## 本地运行

首选 Python 3.11 独立虚拟环境。安装 UI 与开发依赖后，使用下列命令启动：

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
python -m datumdock
```

打开完整 UI 演示：

```powershell
python -m datumdock --ui-preview
```

如果当前环境已经安装依赖但尚未执行可编辑安装，可在仓库根目录临时运行：

```powershell
$env:PYTHONPATH = "src"
python -m datumdock --ui-preview
```

推理依赖不属于本轮 UI 原型的启动前提；后续接入 ONNX/PT 时再执行 `python -m pip install -e ".[dev,inference]"`。

如果只需要一次性安装完整开发、推理和构建依赖，也可使用：

```powershell
python -m pip install -r requirements.txt
```

构建 Windows 分发目录和 Inno Setup 安装包的具体步骤见 [Windows 构建说明](docs/BUILD_WINDOWS.md)。

当前优先级、完成规则和验收边界分别记录在 [路线图](docs/ROADMAP.md)、[验收标准](docs/ACCEPTANCE.md) 与 [X-AnyLabeling 对标基线](docs/X_ANYLABELING_BASELINE.md)。

## 核心能力

| 领域 | 规划能力 |
| --- | --- |
| 数据集主页 | 像游戏存档一样创建、查看、搜索和打开多个受管数据集；不要求选择工作区或项目目录。 |
| 内置教程 | 首页提供可折叠快速开始和离线中英文学习中心，覆盖 DatumDock 全流程、YOLO Detection、数据划分/泄露与导出训练准备。 |
| 受管数据集池 | 导入后复制到软件内部统一管理；常见静态图片统一转换为 PNG，外部源文件不被改写。 |
| 数据质量 | 导入时检查完全相同图片；近似图片以相似组管理，避免训练、验证和测试集之间的数据泄露。 |
| 标注与复核 | 参考 X-AnyLabeling 的高效布局：顶部切换/导入/导出、左侧矩形框与 AI 工具、中央画布、右侧标注和带状态图片列表；图片级区分未标注、待审核、已完成、有问题和异常。 |
| 标签体系 | 每个数据集独立管理标签集，包含英文训练名、中文别名、描述、同义词、稳定类别 ID 与独立颜色；可按标签检查图片。 |
| 自动标注 | 每个数据集可管理本地 ONNX 与受支持的 Ultralytics YOLO `.pt` 模型；优先 GPU、无 GPU 时明确回退 CPU。 |
| 模型训练导出 | 导出时自由选择比例、随机种子和目标格式；MVP 首先提供可直接训练的 YOLO Detection 目录与 `data.yaml`。 |
| 格式互操作 | 可导入 X-AnyLabeling/LabelMe 图片与同名 JSON；可导出让 X-AnyLabeling 直接打开的目录。 |
| 安全与可移植 | 数据集备份支持校验后导入；模型二进制不随备份包分发，避免无意携带大文件或执行风险。 |

## 设计理念

- **本地优先**：图片、标注和模型默认只在本机处理，不自动上传。
- **数据集先于标注**：从导入、重命名、筛选、重复图、复核、标签到训练导出形成完整闭环。
- **标签对人友好、对训练稳定**：中文别名与描述帮助快速识别，英文训练名和类别 ID 保持稳定。
- **长期使用舒适**：主页参考 Scratch 的现代友好感，标注工作台参考 X-AnyLabeling 的紧凑效率，统一使用 DatumDock 自有冷白/浅蓝灰表面、品牌蓝、圆角图标和直接反馈。
- **可审阅的 AI**：自动标注是待人工确认的建议，不会静默覆盖人工标注。

## X-AnyLabeling 互操作

DatumDock 将与 X-AnyLabeling 共用 LabelMe JSON 工作流：

- 导入含图片与同名 JSON 的目录后，矩形框可继续编辑；
- 当前不支持的多边形、旋转框、圆、线、点和扩展字段会被保留，而不是静默删除；
- 导出后生成 PNG、同名 LabelMe JSON 与 `labels.txt`，可由 X-AnyLabeling 直接打开；
- DatumDock 的内部管理信息、复核状态、模型来源等私有信息不会写进交换 JSON。

详见 [X-AnyLabeling 互操作规范](docs/X_ANYLABELING_INTEROP.md)。

## 项目文档

| 文档 | 内容 |
| --- | --- |
| [文档导航](docs/README.md) | 推荐阅读顺序、每份文档的职责与开发前检查。 |
| [内部数据集主页与存档式管理方案](docs/DATASET_LIBRARY.md) | 最新入口、软件内部存储、数据集边界、旧结构迁移与验收清单。 |
| [产品需求文档](docs/PRD.md) | MVP 范围、数据池、标签、模型、导出与性能要求。 |
| [架构说明](docs/ARCHITECTURE.md) | 分层、核心对象、受管存储、任务与视觉系统。 |
| [交互与界面规范](docs/UX.md) | 标注工作台四区布局、画布与页面交互。 |
| [现代视觉设计规范 v2](docs/VISUAL_DESIGN.md) | Scratch/X-AnyLabeling 参考边界、现代主题、组件、图标、动效与截图验收。 |
| [UI 原型页面清单](docs/UI_INVENTORY.md) | 16 个路由、28 个弹窗、组件、运行边界与本轮验证结果。 |
| [UI 交付与自检报告](docs/UI_REVIEW.md) | 截图矩阵、验证命令、已知边界与 94 分自评。 |
| [路线图](docs/ROADMAP.md) | 按阶段拆分的开发任务与优先级。 |
| [验收标准](docs/ACCEPTANCE.md) | 每项功能可操作或可自动验证的完成条件。 |
| [对标基线](docs/X_ANYLABELING_BASELINE.md) | 与 X-AnyLabeling 核心工作流的分级质量目标。 |
| [互操作规范](docs/X_ANYLABELING_INTEROP.md) | X-AnyLabeling/LabelMe 导入、导出与兼容字段保留规则。 |

## 仓库结构

```text
DatumDock/
├─ .github/                 # Issue 与 Pull Request 模板
├─ assets/
│  ├─ brand/                # Logo 等品牌资产
│  └─ icons/                # 自有 UI 图标资产
├─ docs/                    # 产品、架构、交互、路线与验收文档
├─ src/                     # 应用源代码（将使用 Python/PySide6）
├─ tests/                   # 自动化测试
├─ AGENTS.md                # 面向 Codex/协作开发的项目约束
├─ CONTRIBUTING.md          # 贡献说明
└─ SECURITY.md              # 安全报告说明
```

## 参与开发

在代码初始化前，请先阅读 [贡献指南](CONTRIBUTING.md)。主要约定如下：

- 代码注释使用中文；Markdown 中文为主，同时提供英文摘要；
- 使用 Ruff 统一格式化、静态检查与 import 排序；详细规则见 [代码规范](docs/CODE_STYLE.md)；
- 不提交真实数据集、内部资料库、旧工作区、导出训练集、模型权重、密钥或缓存；
- 涉及受管数据、格式互操作、划分或 YOLO 导出的改动必须有相应测试；
- UI 复用统一设计令牌与自有图标资产，不复制第三方产品图形；
- 每完成一项功能，更新路线图并按验收标准验证。

## GitHub 发布前检查

仓库已经包含 `.gitignore`、`.gitattributes`、贡献指南、安全策略、中文/英文 Issue 模板和 PR 模板。上传到 GitHub 前，请完成以下项目：

1. 确认 [MIT 许可证](LICENSE) 符合发布意图。
2. 在 GitHub 设置仓库简介、主题标签、可见性与安全联系渠道。
3. 完成 Python 3.11、X-AnyLabeling、真实模型和隔离安装包验收后再创建 Release。
4. 首次发布后确认 README Logo、Issue 模板和默认 `main` 分支显示正常。

当你提供 GitHub 仓库链接后，我可以继续完成远程地址绑定、首次提交、推送及仓库页面配置。

## 品牌资产

![DatumDock Logo](assets/brand/datumdock-wordmark-v3.png)

当前 Logo 由项目名称直接构成：浅橙色与浅蓝色交叠的 `DD` 单字母标记，搭配深炭灰 `DatumDock` 字标。它适用于 GitHub、关于页和文档；后续 Windows 应用图标将从 `DD` 标记另行导出，避免在小尺寸强行使用完整字标。资产说明见 [assets/brand/README.md](assets/brand/README.md)。

## English Summary

DatumDock is a local-first desktop application for managing and annotating computer-vision datasets. Its confirmed target experience uses a game-save-like home page backed by an app-managed dataset library: users create or open a dataset directly, without selecting a workspace or project directory, and each dataset independently owns its images, annotations, labels, models, review states, and indexes.

The step-one UI prototype is ready for visual review. The official entry point now uses a modern PySide6 shell with a save-game-like dataset home, a compact four-zone annotation workspace, management pages, settings, 16 registered routes, and 28 registered dialogs. `python -m datumdock` opens a safe empty home; `python -m datumdock --ui-preview` uses disposable in-memory demo data. No real dataset, model, import, export, or deletion operation is connected to the new shell yet.

Planned MVP capabilities include an offline bilingual quick-start and tutorial center for DatumDock and YOLO; managed PNG ingestion; duplicate and similarity-group handling; rectangle annotation; image-level review states; dataset-level bilingual label management; local ONNX and supported Ultralytics YOLO model assistance; deterministic YOLO Detection export; validated dataset backups; and configurable shortcuts. DatumDock also imports X-AnyLabeling/LabelMe directories and exports directly reopenable directories while preserving unsupported shapes as compatibility payloads.

The repository uses the MIT license. Before public release, complete the checks recorded in [docs/ROADMAP.md](docs/ROADMAP.md), especially the Python 3.11 and isolated installer verification. See [CONTRIBUTING.md](CONTRIBUTING.md) for development rules and [SECURITY.md](SECURITY.md) for responsible vulnerability reporting.

The UI prototype has passed Ruff, format checks, 31 pytest cases, CLI launch smoke tests, bilingual checks, and 100%/125%/150% DPI screenshot review. Python 3.11 dependency installation remains externally blocked by a PyPI TLS error and is recorded in the roadmap. For the complete development, inference, and build dependency set, run `python -m pip install -r requirements.txt` after package access is restored.
