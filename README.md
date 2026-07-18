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

> 🚧 **预发布开发中**：`v0.1.0` 已包含可运行的桌面开发版本与核心服务回归，但尚未完成 Python 3.11 依赖、真实模型、X-AnyLabeling 和安装包的隔离环境验收。请不要将它用于唯一的数据副本。

DatumDock 用于把分散在本地文件夹中的视觉数据，集中到安全、可追踪的数据集池中进行管理、标注、复核与导出。它的重点不只是“画框”，而是让多项目、多数据集、标签体系、模型和训练导出在一个清晰的桌面工作流内协作。

## 当前状态

项目已进入可运行的 Windows GUI 实现阶段：可创建工作区/项目/数据集、导入受管 PNG、管理标签、绘制矩形并自动保存、导出 YOLO、进行 X-AnyLabeling 目录互操作和项目备份。发布前仍需完成 Python 3.11、真实模型和隔离安装验证。

当前已验证的开发机检查为 Ruff、格式检查、服务层 pytest 与 Qt 离屏 GUI 冒烟。由于当前环境访问 PyPI 出现 TLS 错误，Python 3.11 的完整依赖与 `pytest-qt` 尚未安装；详情见 [路线图的外部阻塞记录](docs/ROADMAP.md#当前外部阻塞记录)。

## 本地运行

首选 Python 3.11 独立虚拟环境。安装依赖后，使用下列命令启动：

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev,inference]"
python -m datumdock
```

构建 Windows 分发目录和 Inno Setup 安装包的具体步骤见 [Windows 构建说明](docs/BUILD_WINDOWS.md)。

当前优先级、完成规则和验收边界分别记录在 [路线图](docs/ROADMAP.md)、[验收标准](docs/ACCEPTANCE.md) 与 [X-AnyLabeling 对标基线](docs/X_ANYLABELING_BASELINE.md)。

## 核心能力

| 领域 | 规划能力 |
| --- | --- |
| 多项目管理 | 工作区 → 项目 → 数据集 → 受管数据集池；可快速切换多个项目与数据集。 |
| 受管数据集池 | 导入后复制到软件内部统一管理；常见静态图片统一转换为 PNG，外部源文件不被改写。 |
| 数据质量 | 导入时检查完全相同图片；近似图片以相似组管理，避免训练、验证和测试集之间的数据泄露。 |
| 标注与复核 | MVP 优先矩形框；图片级未复核、自动标注待复核、已复核和有问题状态；高效交互参考 X-AnyLabeling。 |
| 标签体系 | 项目级标签集，包含英文训练名、中文别名、描述、同义词、稳定类别 ID 与独立颜色；可跨数据集检查标签使用情况。 |
| 自动标注 | 每个项目可管理本地 ONNX 与受支持的 Ultralytics YOLO `.pt` 模型；优先 GPU、无 GPU 时明确回退 CPU。 |
| 模型训练导出 | 导出时自由选择比例、随机种子和目标格式；MVP 首先提供可直接训练的 YOLO Detection 目录与 `data.yaml`。 |
| 格式互操作 | 可导入 X-AnyLabeling/LabelMe 图片与同名 JSON；可导出让 X-AnyLabeling 直接打开的目录。 |
| 安全与可移植 | 项目备份支持校验后导入；模型二进制不随备份包分发，避免无意携带大文件或执行风险。 |

## 设计理念

- **本地优先**：图片、标注和模型默认只在本机处理，不自动上传。
- **数据集先于标注**：从导入、重命名、筛选、重复图、复核、标签到训练导出形成完整闭环。
- **标签对人友好、对训练稳定**：中文别名与描述帮助快速识别，英文训练名和类别 ID 保持稳定。
- **长期使用舒适**：明亮小清新的莫兰迪 UI、充足留白、统一图标与直接的画布反馈。
- **可审阅的 AI**：自动标注是待人工确认的建议，不会静默覆盖人工标注。

## X-AnyLabeling 互操作

DatumDock 将与 X-AnyLabeling 共用 LabelMe JSON 工作流：

- 导入含图片与同名 JSON 的目录后，矩形框可继续编辑；
- 当前不支持的多边形、旋转框、圆、线、点和扩展字段会被保留，而不是静默删除；
- 导出后生成 PNG、同名 LabelMe JSON 与 `labels.txt`，可由 X-AnyLabeling 直接打开；
- DatumDock 的项目管理、复核状态、模型来源等私有信息不会写进交换 JSON。

详见 [X-AnyLabeling 互操作规范](docs/X_ANYLABELING_INTEROP.md)。

## 项目文档

| 文档 | 内容 |
| --- | --- |
| [文档导航](docs/README.md) | 推荐阅读顺序、每份文档的职责与开发前检查。 |
| [产品需求文档](docs/PRD.md) | MVP 范围、数据池、标签、模型、导出与性能要求。 |
| [架构说明](docs/ARCHITECTURE.md) | 分层、核心对象、受管存储、任务与视觉系统。 |
| [交互与界面规范](docs/UX.md) | 三栏工作流、莫兰迪设计令牌、画布与页面交互。 |
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
- 不提交真实数据集、项目工作区、导出训练集、模型权重、密钥或缓存；
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

DatumDock is a local-first desktop application for managing and annotating computer-vision datasets. It is designed around a workspace → project → dataset → managed pool hierarchy, so images, annotations, labels, models, review states, and exports remain organized rather than scattered across folders.

The repository is in an executable pre-release implementation stage. Its Windows-first PySide6 GUI already covers the managed-pool, rectangle-annotation, YOLO-export, LabelMe/X-AnyLabeling exchange, backup, and bilingual-shell slices. It is not a release-ready installer yet: Python 3.11 dependencies, real-model workflows, X-AnyLabeling interoperability, and clean-machine installer validation still need to pass.

Planned MVP capabilities include managed PNG ingestion; duplicate and similarity-group handling; rectangle annotation; image-level review states; project-level bilingual label management; local ONNX and supported Ultralytics YOLO model assistance; deterministic YOLO Detection export; validated project backups; and configurable shortcuts. DatumDock also imports X-AnyLabeling/LabelMe directories and exports directly reopenable directories while preserving unsupported shapes as compatibility payloads.

The repository uses the MIT license. Before public release, complete the checks recorded in [docs/ROADMAP.md](docs/ROADMAP.md), especially the Python 3.11 and isolated installer verification. See [CONTRIBUTING.md](CONTRIBUTING.md) for development rules and [SECURITY.md](SECURITY.md) for responsible vulnerability reporting.
