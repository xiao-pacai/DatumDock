# X-AnyLabeling 对标完成基线

## 1. 目的

DatumDock 以 X-AnyLabeling 的成熟标注体验、格式兼容性和本地 AI 辅助工作流作为质量参照，但不以“复制所有上游功能”为模糊完成定义。

DatumDock 的差异化重点是多项目/多数据集池、项目级标签知识管理、样本治理和面向模型的导出。对标的目标是：这些能力不能以牺牲标注可靠性、可编辑性、快捷操作、格式正确性或自动标注可审查性为代价。

本基线基于 X-AnyLabeling 的官方 [用户指南](https://github.com/CVHub520/X-AnyLabeling/blob/main/docs/en/user_guide.md)、[自定义模型指南](https://github.com/CVHub520/X-AnyLabeling/blob/main/docs/en/custom_model.md) 和 [项目 README](https://github.com/CVHub520/X-AnyLabeling/blob/main/README.md) 中公开描述的能力制定。上游能力变化时，先更新本文件，再调整路线图和验收项。

## 2. 对标原则

- 对标“可完成的真实工作流”，而不是仅对标界面截图或菜单数量。
- 每项功能必须有明确用户路径、错误处理和可验证结果。
- 所有标注编辑必须可保存、重开、导出和再次编辑，不能因模型、重命名、切换项目或语言切换损坏数据。
- AI 自动标注必须可见、可审查、可修改、不会静默覆盖人工标注。
- X-AnyLabeling 支持但 DatumDock 尚未进入当前等级的能力，必须在产品中明确标注为未支持，不能制造“看似支持”的入口。

## 3. 完成等级

| 等级 | 名称 | 含义 | 对外表述 |
| --- | --- | --- | --- |
| L0 | 工程骨架 | 文档、数据模型、测试与 GUI 骨架存在。 | 不可作为可用产品发布。 |
| L1 | 数据集管理可用 | 多项目/数据集池、标签集、样本治理和 YOLO Detection 导出完整可用。 | 可用于受限的目标检测数据集管理。 |
| L2 | 核心标注工作流对标 | 达到日常图片标注所需的可靠编辑、快捷操作、检查和数据格式闭环。 | 可替代 X-AnyLabeling 的基础图片检测标注工作流。 |
| L3 | AI 与格式对标 | 自动标注模型、常用形状和主要格式工作流达到可审查、可导入/导出的质量。 | 可用于常规 AI 辅助标注与多格式数据交换。 |
| L4 | 扩展任务对标 | 分割、关键点、旋转框、视频/跟踪、OCR 等按需要逐项完成。 | 仅能声明已完成的具体任务，不泛称全功能对等。 |

“项目完成”至少指达到 L2；若用户选择将自动标注和多格式交换作为首发必需能力，则首发完成门槛为 L3。L4 为按业务需求逐项交付，不设无边界的“一次性全部完成”。

## 4. L1：数据集管理可用

以下全部必需：

- 存档式主页可创建、搜索和快速打开多个受管数据集，数据池、标签集、模型库严格隔离；新建数据集可复用兼容配置，标签集/数据集合并先经训练映射比较与差异确认。
- 受管数据集池导入、搜索、筛选、图片级复核状态和稳定样本 ID；至少一万张常见图片通过索引、虚拟列表、延迟缩略图和后台任务保持可用。
- 常见静态图片导入统一转换为 PNG、完全重复图片对比确认、近似图片相似组与不可拆分导出、少量样本回收站/大批量永久删除、数据集备份校验和标签集一致的数据集转移。
- 命名规则预览与安全批量重命名；删除样本时清理受管关联信息且不删除外部源文件。
- 数据集级标签管理、中文别名/描述/同义词、当前数据集标签检查集合和历史标签迁移。
- YOLO Detection 训练目录、类别 ID、归一化坐标、数据划分和 `data.yaml` 通过自动化验证。
- 简体中文/英文界面切换、莫兰迪主题、未保存保护和可恢复错误提示。

## 5. L2：核心标注工作流对标

以下全部必需：

- 高性能图片浏览、缩放、平移、适配窗口、文件导航和明确的保存状态。
- 标注工作台提供顶部数据集/导入/导出操作、左侧标注与 AI 工具、中央画布，以及右侧同步标注列表和带图片级状态的虚拟图片列表。
- 矩形框创建、选择、移动、八点缩放、删除、取消、复制/粘贴（如适用）及撤销/重做。
- 可搜索标签选择器、最近标签、常用标签、可自定义快捷键、每标签一致且可辨识的颜色和目标标签高亮；标签信息在中英文 UI 下均清晰可读。
- LabelMe JSON 与 X-AnyLabeling 目录的健壮双向互操作：可导入图片与同名 JSON、矩形框可编辑、损坏文件不崩溃；不支持 shape 及其扩展字段不静默丢失，重开和从 DatumDock 导出后可由 X-AnyLabeling 再次打开。
- 标注质量检查：空标签、越界框、无效面积、缺失图片/标注与未保存状态均有可操作提示。
- 100 张及以上普通图片连续浏览、编辑、保存、重开与导出的回归验证无崩溃、无数据错配。

以下为 L2 强烈建议、若缺失需在发布说明中披露：多边形、点、线、圆、旋转框；形状属性、描述、困难样本标记和分组 ID。

## 6. L3：AI 与格式对标

以下全部必需：

- 项目级模型管理：本地导入、检查、验证、更新、删除和模型类别到项目标签的映射。
- ONNX 模型的可靠本地运行与检测矩形框预标注；优先使用可用 GPU、首次 CPU 回退有可操作指引。结果直接进入当前数据集并带模型来源、置信度和图片级待审核状态，可逐图审查、修改或删除。
- 对受支持 PT 变体的明确适配；不支持时给出原因，不猜测模型结构。
- 导出器框架和至少 YOLO Detection；新增格式必须有格式校验、最小示例和回归测试。
- LabelMe、YOLO Detection、COCO Detection、Pascal VOC Detection 的导入/导出能力，或在当前发布范围中明确标记并将缺口保留在路线图。

当对应标注形状已经支持时，应继续补齐：YOLO Segmentation/OBB/Pose、COCO Segmentation/Keypoints 等格式。格式支持必须与实际 shape 支持绑定，禁止仅生成表面上合法但语义错误的文件。

## 7. L4：扩展任务对标菜单

按业务优先级逐项实现并独立验收：

- 多边形、实例分割、关键点、旋转框、圆、线和点标注。
- 文本/OCR、分类、关系属性、形状描述、困难样本、分组 ID。
- 视频帧、目标跟踪、MOT/MOTS。
- SAM 类视觉提示分割、文本提示检测/分割、批量自动标注、GPU/TensorRT 等推理后端。
- COCO/VOC/YOLO 之外的格式转换和可视化导出。

这些能力参考 X-AnyLabeling 的扩展方向，但只有用户明确需要时才进入 DatumDock 的实现范围。

## 8. 质量闸门与追踪方式

- 每个 L1–L4 条目在 `docs/ROADMAP.md` 中必须有对应任务，在 `docs/ACCEPTANCE.md` 中必须有可验证验收项。
- 每个已声明支持的格式、形状和模型适配器都要有正常样例、边界样例和错误样例测试。
- 每次发布前进行手工冒烟测试：创建/打开数据集、导入、标注、标签检查、重命名、删除、模型预标注、导出和语言切换。
- 发现数据损坏、导出错误、静默丢失标注或 AI 覆盖人工标注，视为阻断发布的 P0 缺陷。
- 发布说明必须注明当前完成等级、已支持的任务/格式/模型以及已知限制。

## English Summary

DatumDock uses X-AnyLabeling as a workflow and quality benchmark, not as a promise to clone every upstream feature. L1 requires the managed dataset home experience and YOLO Detection. L2 requires a reliable annotation workspace with top dataset/import/export actions, left annotation and AI tools, a central canvas, a synchronized right annotation/image panel, and reopenable X-AnyLabeling/LabelMe exchange without silent payload loss. L3 covers reviewed AI assistance and common interchange formats, while L4 covers separately accepted advanced tasks such as segmentation, pose, OBB, video, tracking, and OCR.
