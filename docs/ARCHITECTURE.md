# DatumDock 架构说明

## 0. 目标架构覆盖说明（步骤四已接入标签与标注闭环）

2026-07-19 已将正式入口从用户可见的 `Workspace -> Project -> Dataset` 改为 [内部数据集主页与存档式管理方案](DATASET_LIBRARY.md) 定义的 `AppLibrary -> ManagedDataset`。步骤二已实现资料库初始化、UUID 目录、事务创建、启动对账恢复、打开/切换、重命名、归档/恢复、损坏项隔离和模板配置复制。

本文件中仍出现的 `Workspace`、`Project`、`WorkspaceService` 和“项目级”描述只用于标记旧实现来源；正式入口不再调用它们，新增代码不得继续扩大该层级。步骤三已将图片池、样本索引、缩略图、重命名与回收站迁入 `ManagedDataset`；步骤四已迁入标签、矩形标注、LabelMe、自动保存与图片级复核。模型和导出尚未迁移。

当前模块边界为：`services.sample_repository` 是 SQLite v3 样本、标签反向索引和双状态复核查询事实来源；`services.image_pool` 负责两阶段转码、哈希、缩略图与启动对账；`services.managed_labels` 负责标签和训练名迁移；`services.annotations` 与 `services.labelme` 负责有序 JSON、可恢复提交和自动保存；`services.shortcuts` 负责全量动作注册和原子快捷键偏好；`services.sample_governance` 负责重命名和删除事务；`ui.managed_gateway` 只向页面暴露数据对象或图片字节，不暴露受管路径。详见 [受管图片池](IMAGE_POOL.md) 与 [标注工作流](ANNOTATION_WORKFLOW.md)。

## 1. 原则

- UI、领域模型、文件读写和应用状态分层，避免将业务规则写死在控件回调中。
- 标注数据模型优先独立于 PySide6，保证其可被测试和未来的命令行转换工具复用。
- 文件读取失败或 JSON 格式异常不应导致应用崩溃。

## 2. 建议代码结构

```text
src/datumdock/
├─ __main__.py              # 应用启动入口
├─ app.py                   # QApplication 初始化与依赖组装
├─ domain/                  # AppLibrary、ManagedDataset、LabelSet、ModelEntry、NamingPolicy、DatasetSample 等纯数据模型
├─ services/                # 内部资料库、模型导入/推理、数据集、索引、缩略图、重命名、样本删除、标注读写、划分、模型格式导出
├─ ui/                      # 数据集主页、四区标注工作台、模型管理、设置、虚拟图片列表、画布、导出向导等
├─ state/                   # 当前数据集、主页状态、界面语言、快捷键、筛选条件、选中标注、待处理状态等
├─ i18n/                    # 简体中文、英文翻译资源和本地化辅助函数
└─ resources/               # Qt 资源、图标、主题，以及版本化中英文离线教程内容
```

## 3. 关键对象

| 对象 | 职责 |
| --- | --- |
| `AppLibrary` | 软件内部资料库，登记所有受管数据集的稳定 ID、目录、摘要和资料库版本；不作为用户可见的工作区。 |
| `ManagedDataset` | 用户可见的唯一顶层数据对象，独立拥有元数据、标签集、图片池、标注、索引、模型配置、回收站和缓存。 |
| `DatasetLibraryService` | 初始化内部资料库，以事务创建、登记、打开和恢复受管数据集；启动时对账索引与 UUID 目录，并向主页提供安全摘要。 |
| `LibraryRecoveryReport` | 只读报告已恢复、损坏、已刷新和被忽略的目录；不得借报告自动删除、移动或跟随未知入口。 |
| `Workspace` | 旧实现对象，仅用于迁移读取；目标产品不再创建或打开用户工作区。 |
| `LocaleService` | 读取和保存全局界面语言，加载 Qt 翻译资源并通知可见界面重新翻译。 |
| `HelpContentService` | 按应用版本与界面语言加载安装包内可信教程目录、正文和插图，解析页面跳转；不依赖远程内容完成核心阅读。 |
| `TutorialProgressRepository` | 在全局设置中保存快速开始折叠状态、已完成步骤、教程阅读位置和内容版本；不写入数据集。 |
| `ShortcutService` | 集中注册操作及默认组合，读取/验证/保存用户绑定，并将更新即时应用到 Qt Actions。 |
| `ThemeService` | 集中提供界面设计令牌、状态样式、系统缩放和减少动态效果适配；不管理数据集标签颜色。 |
| `IconRegistry` | 集中登记自有 SVG/PNG 图标、语义名称、尺寸与各状态变体，为菜单、工具栏、空状态和安装包提供统一资产。 |
| `Project` | 旧实现对象，仅用于把所属标签和模型配置迁移到独立受管数据集；目标产品不再展示项目层级。 |
| `LabelSet` | 单个数据集的标签集合，维护标签的稳定 ID、英文训练名、中文别名、描述、同义词、颜色和 YOLO 类别 ID。 |
| `LabelColorService` | 为活动标签分配高辨识度唯一颜色，验证手动颜色选择，并提供统一的 shape 渲染颜色。 |
| `ManagedLabelMigrationService` | 预检标签修改影响范围，以恢复备份将英文训练名安全同步到相关标注文件；类别 ID 变化不改写 JSON。 |
| `LabelSetComparisonService` | 计算标签集训练映射签名和逐字段差异，作为模板、转移、合并的安全门槛。 |
| `LabelSetMergeService` | 在无训练映射冲突时，以明确目标版本为主安全合并标签定义并记录来源。 |
| `LabelInspectionService` | 以当前数据集标签 ID 为条件查询样本，构建不复制文件的实时标签检查集合。 |
| `ModelEntry` | 数据集模型条目的稳定 ID、显示名、受管文件路径、格式、任务类型、探测元数据、运行配置、标签映射和状态。 |
| `ModelImportService` | 复制模型到当前数据集模型目录，识别格式、提取元数据、验证可加载性并生成可编辑配置。 |
| `AutoAnnotationService` | 使用已验证模型对样本推理，将结果作为可审阅的待确认标注交给画布。 |
| `Dataset` | 现有代码名称；目标重构后由独立拥有标签、模型、索引和受管池的 `ManagedDataset` 取代。 |
| `NamingPolicy` | 数据集的图片命名模板、前缀、起始编号、补零位数和扩展名保留规则。 |
| `ImageImportProfile` | MVP 导入图片的支持格式和统一 PNG 转码规则；后续可扩展其他受管格式策略。 |
| `DatasetSample` | 样本稳定 UUID、受管相对路径、尺寸/哈希、图片健康、复核状态、标注摘要/版本/框数及回收站状态。 |
| `AnnotationDocument` | 当前样本、图片名/尺寸、有序矩形和兼容 shape、LabelMe 根字段、文档版本与图片级复核状态。 |
| `RectangleShape` | 稳定 shape UUID、稳定 `label_id`、原图像素坐标、兼容字段、可选模型来源与置信度。 |
| `Label` | 单个数据集标签的稳定 UUID、类别 ID、英文训练名、中文别名、描述、同义词、唯一颜色、状态和时间。 |
| `LabelMeRepository` | 有序读取、验证和原子写入 LabelMe JSON；外部负载剔除 `datumdock_` 私有字段。 |
| `AnnotationService` | 校验并协调 JSON、SQLite v2 和有限恢复标记，不同步扫描全部标注。 |
| `AnnotationAutosaveService` | 按数据集串行保存不可变快照，并按样本 UUID 隔离最后版本和磁盘摘要。 |
| `XAnyLabelingInteropService` | 递归导入 X-AnyLabeling 图片与同名 LabelMe JSON，并生成可被 X-AnyLabeling 重新打开的交换目录。 |
| `CompatibilityPayloadRepository` | 保存并回写当前不可编辑的 X-AnyLabeling shape 与扩展字段，保证保存、迁移、重命名和导出不会静默丢失它们。 |
| `WorkspaceService` | 旧实现服务，迁移完成后由 `DatasetLibraryService` 取代。 |
| `DatasetPoolService` | 将图片复制到目标数据集的受管池，建立样本索引、去重、搜索和筛选。 |
| `DatasetTemplateService` | 从源数据集复制可复用配置并创建空目标数据集，不复制样本和标注。 |
| `ImageImportService` | 在后台复制或转码常见静态图片，生成导入报告并原子写入受管池与数据集索引。 |
| `DuplicateDetectionService` | 基于最终像素内容哈希检测完全相同图片，并在导入前提供已有样本的对比与确认信息。 |
| `SimilarityGroupService` | 在后台以感知哈希生成近似图片候选组，维护用户确认的相似组并为分组划分提供约束。 |
| `DatasetSampleRepository` | 步骤四正式 SQLite v2 边界，维护样本、标注摘要、`sample_labels`、复核状态、感知哈希、相似组、回收站、操作日志和分页定位；旧 `ProjectIndexRepository` 不在正式入口使用。 |
| `ThumbnailService` | 在后台按需生成和缓存缩略图，以稳定样本 ID 为键，并提供可取消的优先级队列。 |
| `SampleRenameService` | 预览并安全执行池内样本批量重命名，同步 LabelMe、索引、缓存和路径引用。 |
| `SampleDeletionService` | 预检并以受管事务删除样本的图片、标注、索引与派生信息。 |
| `TrashService` | 将少量删除样本包移入当前数据集回收站，支持恢复和永久清空；大批量删除可跳过回收站。 |
| `InferenceBackendSelector` | 选择可用 GPU 推理后端，无法使用时回退 CPU，并提供首次 CPU 推理的诊断与配置指引。 |
| `DatasetBackupService` | 导出数据集压缩包、写入版本和完整性清单，并在导入前验证结构和文件校验。 |
| `DatasetTransferService` | 在标签集完全一致的数据集之间安全复制或移动样本包。 |
| `DatasetMergeService` | 对兼容数据集预览标签差异、样本数和重复图，再执行可恢复的复制或移动合并。 |
| `ExportRequest` | 当次导出的临时参数：目标格式、候选样本、比例、种子和输出目录；不持久化。 |
| `SplitPlanner` | 根据候选样本、比例和种子生成确定性的 train/val/test 划分。 |
| `DatasetExporter` | 模型格式导出器的统一接口，声明支持的标注类型、校验规则和输出方式。 |
| `YoloDetectionExporter` | 首个 `DatasetExporter` 实现，将图片、标签和 `data.yaml` 写入独立 YOLO 目录。 |
| `AnnotationCanvas` | 坐标变换、绘制和鼠标交互；不负责 JSON 持久化。 |
| `AnnotationWorkspace` | 组合顶部主操作栏、左侧标注工具、中央 `AnnotationCanvas`、右侧当前标注与虚拟图片列表；只转发意图，不直接执行耗时 I/O。 |
| `TutorialCenter` | 展示首页学习卡片和应用内教程阅读器，通过稳定 `action_id` 跳转到功能，不直接执行有副作用的业务操作。 |
| `MainWindow` | 在数据集主页和 `AnnotationWorkspace` 间切换，并组装全局操作、语言、主题和错误边界。 |

## 4. 坐标约定

- 数据模型始终保存原始图片像素坐标。
- 画布显示坐标通过缩放与平移转换得到。
- 写入前将矩形坐标归一化为左上角与右下角，避免反向拖拽产生无效数据。
- A0.7 已在 `AnnotationCanvas` 输入层实现纯函数式 `CanvasProjection`：矩形工具的中央底板坐标逐轴投影到 `[0, image_width] × [0, image_height]`，动态预览和提交共享同一结果；图片内点保持不变。
- `AnnotationService`、领域模型和 `LabelMeRepository` 不信任 UI 钳制，继续拒绝非有限、零面积或真正越界的矩形。选择、平移、侧栏点击和不可编辑文档不会调用该钳制创建标注。
- A0.8 已将辅助线状态统一为“最新指针画布坐标 + 当前图片可见矩形 + 加载健康状态”推导，不读取当前工具、当前选中 shape 或矩形草稿作为显示前置条件；普通模式和预览模式调用同一共享画布实现。
- A0.8 的滚轮分派集中在共享画布：`Ctrl` 缩放优先于 `Alt` 横向滚动，`Alt` 优先于普通纵向滚动。缩放命令接收画布锚点并通过统一双精度视图变换保持锚点下原图像素稳定，不改变领域文档、历史或复核状态。

## 5. 内部资料库、数据集池与导出边界

Windows 默认受管存储位于 `%LOCALAPPDATA%\DatumDock`，而不是安装目录或用户选择的项目目录。实现可调整内部文件名，但每个数据集的隔离关系必须保持：

```text
%LOCALAPPDATA%\DatumDock\
├─ settings.json
├─ library.json
└─ datasets\
   └─ {dataset-uuid}\
      ├─ dataset.json
      ├─ label-set.json
      ├─ index.sqlite
      ├─ trash\
      │  └─ {trash-item-id}\
      ├─ models\
      │  └─ {model-id}\
      │     ├─ model.onnx 或 model.pt
      │     └─ model.json
      ├─ pool\
      │  ├─ images\
      │  └─ annotations\
      └─ cache\
         └─ thumbnails\
```

- `library.json` 只登记受管数据集和主页摘要；它不复制每个数据集的标签或标注事实。索引缺失或目录未登记时，启动对账逐个扫描规范 UUID 目录，以有效 `dataset.json` 原子恢复摘要；现有索引自身损坏时不得自动覆盖。
- `dataset.json` 是恢复摘要的事实来源。标签文件、SQLite 或固定目录损坏时仍可使用有效元数据展示真实诊断卡片；元数据也损坏时使用 UUID 派生占位名称。
- 扫描只查看 `datasets/` 的直接子项。非 UUID 名称、普通文件和符号链接进入 `LibraryRecoveryReport`，不得跟随、删除或移动。
- `label-set.json` 是单个受管数据集的标签事实来源。标签记录建议为 `{id, class_id, name, alias, description, synonyms, color, status}`；`id`、`class_id` 与 `name` 要受到变更保护。
- 标签集同时生成两类签名：`training_signature` 覆盖稳定标签 ID、类别 ID、英文训练名与状态，用于判断能否安全复制、移动或合并数据；`display_signature` 覆盖别名、描述、同义词和颜色，用于展示差异。签名与版本记录在数据集元数据中。
- `color` 是数据集标签定义的一部分。`LabelColorService` 使用预定义的可访问调色板及色差校验生成候选色；活动标签颜色不得重复，归档标签颜色可复用。
- 全局应用设置保存 `ui_locale`（初始值 `zh_CN`）、`shortcut_overrides`、默认数据划分比例和回收站少量样本阈值，不保存在数据集元数据中；翻译资源随应用安装包提供，例如 `i18n/datumdock_zh_CN.qm` 与 `i18n/datumdock_en_US.qm`。
- 每个模型在数据集 `models/{model-id}/` 中受管存放，`model.json` 记录 `{id, display_name, format, task_type, source_filename, runtime_config, model_classes, label_mapping, status}`。模型二进制和配置只属于其所在数据集；数据集备份只保留模型配置并在导入后标记二进制待重新导入。
- `index.sqlite` 是单个受管数据集内万级样本的查询事实来源。schema v3 在 `samples` 维护标注摘要、版本、框数、更新时间和可空双状态复核值，并以 `sample_labels(sample_id, label_id, shape_id)` 支持标签使用量、筛选和跨页定位；升级不全量解析 JSON。
- 图片级复核只允许空值、`pending_review` 与 `completed`。空值不显示第三种徽标；加载失败另由图片健康或 `annotation_state` 表示。正式保存边界已经支持人工/模型来源，模型推理本身仍未接入。
- 零 shape 样本可由人工明确确认为 `completed`，并以框数为零表达负样本；不再保留 `completed_negative` 用户状态。

### 7.1 已实施的双状态复核模型

> 五状态只作为 schema v2 历史迁移输入保留。当前运行版本和新建数据集均使用本节 schema v3 模型。

- schema v3 的 `review_status` 只允许空值、`pending_review` 和 `completed`。空值表示尚未产生复核状态，不作为第三种用户可见徽标。
- 纯人工创建首个矩形时写入 `completed`；模型新增或改变任何预测框时写入 `pending_review`。待复核图片的第一次有效人工编辑在保存请求中同时携带目标状态 `completed`。
- 有效人工编辑来源必须由命令边界明确标记，至少包含创建、移动、缩放、删除、换标签和实际改变文档的撤销/重做；选择、视图变换、搜索、筛选和取消对话框不得伪造编辑来源。
- `AnnotationAutosaveService` 在同一次 JSON/SQLite 协调保存中提交人工编辑和 `completed`，不能先改状态后写框。任一阶段失败时内存待处理状态与持久化状态仍为 `pending_review`，重试使用最新完整快照。
- `review.mark_completed` 用于检查后无需编辑的图片，通过 Gateway/Service 在一个事务中更新状态和摘要；按钮与可配置快捷键共用该 `action_id`。写盘失败不得让 UI 先显示完成。
- 零框且明确确认的图片也使用 `completed`，通过框数为零识别负样本，不保留 `completed_negative` 枚举。
- v2→v3 迁移规则：`unreviewed` 转为空值，`pending_review` 保持，`completed` 与 `completed_negative` 合并为 `completed`，`issue` 为避免误判完成而转为 `pending_review` 并记录迁移诊断。
- 图片缺失、JSON 损坏、未知标签和恢复失败继续由 `health_status` / `annotation_state` 表达，可以与复核状态并存，不得偷偷增加为第三种复核状态。
- SQLite 迁移必须在事务中完成并保留回滚；主页摘要、筛选、标签检查和导出候选只从新枚举与独立健康字段读取。
- `trash/` 只保存被选择“移入回收站”的完整样本包及恢复元数据；永久删除和大批量删除不进入该目录。
- 图片导入时只复制/转码图片，不创建空 LabelMe JSON；首次画框或确认负样本时才创建。索引只保留原始文件名，不持久化外部绝对来源路径。
- 外部 X-AnyLabeling/LabelMe 文件的兼容载荷与 DatumDock 的可编辑矩形框分层存储：矩形框解析为内部 `label_id` 与像素坐标；其他 shape 及 `flags`、`attributes`、`description`、`difficult`、`score` 等未知或未支持字段保留为只读兼容载荷。`LabelMeRepository` 在写回或导出时按原顺序合并该载荷，不将私有稳定 ID、复核状态或模型元数据泄漏到交换 JSON。交换导出始终复制范围内图片，但仅在文档至少包含一个可编辑或兼容 shape 时写出同名 JSON。
- `XAnyLabelingInteropService` 的目录导入导出仍是后续边界。步骤四只实现受管池内 LabelMe 的有序兼容读写和私有字段剔除；完整流程仍应遵循“扫描配对 → 校验 → 临时目录 → 交换验证 → 原子发布”。
- `dataset.json` 包含可选 `naming_policy`。它只定义受管池中图片的整理名称，绝不回写外部来源文件；`DatasetSample.id` 是不随文件名变化的内部身份。
- 样本 ID 必须独立于文件排序；建议由导入时生成 UUID，并记录原始规范化路径、最终 PNG 内容哈希和文件指纹用于完全重复检测。
- 标注数据优先存储稳定标签 ID，并在读写 LabelMe/YOLO 时解析到英文训练名或类别 ID；这能避免中文别名或描述修改影响既有标注。
- 标签管理页面显示的 `usage_count` 由当前数据集索引或按需扫描计算，不作为标签定义的事实来源；它用于修改前影响预览和用户理解。
- 数据集样本索引需维护 `label_id → sample_id` 的可查询关联及每样本的标签框计数，以支持标签检查集合而无需每次扫描全部 JSON 文件。
- 从工作台返回主页或切换数据集是状态边界：立即自动保存任务必须先完成；写入失败时保留待处理状态并阻止静默切换。随后刷新目标数据集标签集、样本索引和筛选条件，并清空尚未执行的临时导出请求。
- 导出属于一次不可修改原池的临时操作。`ExportRequest` 只存在于当前导出流程，完成或取消后不写入数据集元数据；默认复制文件，硬链接或符号链接可作为后续可选优化。
- 划分器在固定种子下先稳定排序样本 ID，再伪随机打乱，以保证结果可复现。
- MVP 的确定性划分器以完全重复图片和已确认近似图片组为不可拆分单元，在保持组完整的前提下尽量接近用户比例；更精细的类别分层优化属于后续增强，但不得以拆分关联图片为代价。

## 6. 万级样本性能与后台任务

- 样本表、标签检查集合和搜索结果必须通过 SQLite 分页查询；UI 列表/网格采用虚拟化模型，仅创建可视区域及小范围预取的控件。
- 图片解码、缩略图生成、文件复制/转码、完整性扫描、批量重命名、批量删除、模型推理和导出在后台任务执行；主线程只接收进度、结果和错误事件。
- `ThumbnailService` 以 `sample_id + 文件版本` 为缓存键，按当前可见数据集、滚动方向和标签检查集合优先级生成；离开视图时可取消低优先级任务。
- 数据集打开时先载入元数据、统计和首屏样本；不得同步扫描一万张图片或生成全部缩略图。全量扫描作为可显示进度和可取消的后台维护任务。
- 所有批量任务必须支持进度、取消和可恢复状态；取消后已完成项保持一致，未完成项不应产生半写入文件或索引。

## 7. 样本重命名与删除

- `SampleRenameService` 先根据 `NamingPolicy` 和候选样本生成完整预览，再校验文件名、扩展名、目标冲突和可写性。重命名通过临时名称避免交换或序号覆盖冲突。
- 每次样本重命名须同步图片文件、配套 LabelMe JSON 文件名、LabelMe `imagePath`、样本索引、缩略图缓存和其他记录的路径；样本稳定 ID 与标注内容保持不变。
- `SampleDeletionService` 对单个样本收集受管关联项：图片、LabelMe JSON、索引记录、缩略图、缓存及自动标注信息。确认后委托 `TrashService` 移入回收站或直接永久删除，并在完成时重新计算标签 `usage_count`。
- 删除服务的范围严格限制在目标数据集池。它不得沿原始来源路径向外删除文件，也不得删除数据集标签、模型或其他样本。
- 这两类多文件操作须保存最小恢复信息并按步骤校验；若任一步骤失败，要回滚已经改变的内容或留下可恢复状态，禁止默默产生孤立文件或索引。

### 图片导入与转码

- `ImageImportService` 接受 JPG/JPEG、PNG、BMP、WebP、TIFF 等静态图片，并将外部源文件复制后统一转码为池内 PNG；外部绝对路径只存在当次预检会话，索引只保留原始文件名。
- 转码过程在后台进行，先写入临时 PNG 并验证可读性、尺寸和像素内容哈希后再原子放入池内和索引；透明通道原样保留。
- `DuplicateDetectionService` 在最终 PNG 哈希与已有样本相同时，向 UI 提供两图预览、来源路径和已存在样本信息；用户明确继续后仍创建两个稳定样本 ID。
- 失败、重复、取消和成功结果写入导入报告，已完成项保持一致，未完成项不创建索引记录。

### 近似图片与分组划分

- `ImageImportService` 为受管 PNG 生成版本化 dHash 和平均 RGB，先按感知哈希分桶产生候选，再执行 Hamming/颜色距离复核。
- 所有候选组都以 `pending` 登记，只能由用户确认或忽略；不会自动删除、合并、确认或拆分任何样本。
- `SplitPlanner` 使用已确认相似组作为不可拆分单元：组内样本必须分配到同一 train/val/test 集合。它以组为最小粒度优化比例，结果允许因组大小而与目标比例存在已解释的少量偏差。

## 8. 国际化

- 使用 PySide6/Qt 的 `QTranslator` 或等价资源机制管理系统文案；所有可见系统字符串从集中翻译键加载。
- `LocaleService` 更换翻译器后触发窗口、菜单、工具栏、模型/标签管理页和活动对话框的 `retranslateUi`，以实现即时切换。
- 开发阶段以中文源文案和英文翻译资源为基准；缺失翻译必须在质量检查中被发现，不能静默显示错误语言或翻译键。
- 领域数据与 UI 资源严格分离。`Label.alias`、`Label.description` 等用户录入内容按原样显示，不通过翻译系统处理。

## 9. 快捷键管理

- 所有 DatumDock 定义的键盘操作都注册为稳定的 `action_id`，例如 `file.save`、`canvas.delete_shape`、`dataset.next_sample`；UI 文案、作用域和默认快捷键与 `action_id` 分离。页面不得直接创建不在注册表中的硬编码 `QShortcut`。
- `ActionRegistry` 是全部应用操作、默认绑定、作用域和可见入口的事实来源。设置页直接枚举注册表，因此后续新增带键盘入口的操作会自动出现，不依赖手工维护第二份列表。
- `ShortcutService` 维护出厂默认绑定、用户覆盖绑定、显式未绑定状态和平台保留组合。应用启动时将有效绑定分配给对应 `QAction`/快捷键对象。
- 保存新绑定前，服务区分“显式未绑定”与无效空输入，并检查语法、同一上下文冲突、跨上下文歧义和系统保留组合；允许用户明确替换冲突绑定，但不允许绕过保留组合。
- 更新绑定时撤销旧 `QAction` 快捷键并立即应用新绑定；单项/分组恢复删除对应覆盖。“恢复全部默认按键”在一个原子设置事务中清空全部快捷键覆盖，验证默认注册表后再统一应用到所有活动窗口。
- 如果恢复全部的持久化、验证或应用阶段失败，服务回滚到恢复前的完整配置；快捷键事务不得写入数据集，也不得重置其他全局设置字段。
- 平台保留组合由运行平台策略提供；文本控件的普通编辑行为和中键/滚轮等纯鼠标手势不登记成键盘 `action_id`。
- 快捷键配置应有单元测试，覆盖注册表完整性、默认值、未绑定、作用域冲突、替换、单项/分组/全部恢复、失败回滚、即时应用和重启持久化。

## 10. 标签检查与批量迁移

- `LabelInspectionService` 接收数据集 ID、目标 `label_id` 和可选样本筛选条件，返回按稳定样本 ID 去重的实时结果集及目标标签框数量。
- 结果集只保存查询条件和排序/分页状态，不复制图片、创建额外数据集或改变文件系统；样本打开操作通过 `dataset_id` 和 `sample_id` 回到对应画布。
- 当标签迁移、样本保存、重命名或删除完成时，相关索引应在同一受管操作中更新，确保标签检查集合不会展示已失效路径。

- `LabelMigrationService` 对英文训练名等影响持久化标注的改动执行“预检 → 用户确认 → 写入 → 校验 → 完成”流程。
- 预检产出当前数据集内受影响的样本文件和 shape 数量；无写权限、损坏 JSON 或未知标签引用必须在确认前提示。
- 迁移写入应使用同目录临时文件和原子替换；在开始写入前保留最小恢复信息，以便失败时回滚已完成文件或下次启动继续恢复。
- 仅改变中文别名、描述、同义词、颜色等显示字段时，不改写每张标注 JSON；显示层从当前数据集标签集实时解析即可。
- 内部 `label_id` 永远不变；LabelMe 的 `shape.label` 与数据集英文训练名保持同步，YOLO 导出则始终从当前标签集读取 `class_id`。

## 11. 自动标注模型导入与运行

- `ModelImportService` 根据扩展名和文件签名路由到格式适配器，首选 `OnnxModelInspector`；PT 首个适配器处理可验证的 Ultralytics YOLO 检测 checkpoint 和兼容自训练 YOLO 模型，其他变体必须明确提示不支持。
- ONNX 探测读取图结构、输入输出张量名、元素类型、形状和 metadata properties。若包含类别、模型任务或预处理信息，应纳入建议配置但不盲目信任。
- 模型适配器输出统一的 `ModelInspectionResult`：格式、任务候选、输入规格、输出规格、类别表、建议预处理、解码器候选、警告和不可确定字段。
- 导入先复制到临时位置并探测、验证；仅验证成功后才原子移动到当前数据集 `models/` 目录并创建 `ModelEntry`。更新模型也遵循同一流程，旧模型在新模型成功前保持可用。
- `InferenceBackendSelector` 启动时探测 ONNX Runtime 可用执行提供程序；有可验证 GPU 提供程序时优先 GPU，否则使用 CPU。首次 CPU 推理显示诊断结果、继续按钮和打开本地 GPU 配置指引的入口。
- 推理运行使用本地后端；PT 后端只在受支持格式和依赖可用时启用。模型加载、推理和自动标注均不上传本地数据。
- 类别映射是 `ModelEntry` 的配置：模型类别可映射到当前数据集稳定 `label_id`，未映射类别默认不生成标注。自动标注结果立刻写入标注文件并带 `model_id`、置信度与图片级待复核状态；模型任务不改写既有人工标注。

## 12. 数据集备份、导入与数据集间转移

- `DatasetBackupService` 生成带格式版本、`manifest.json`、文件清单和校验和的数据集压缩包；数据集元数据、标签集、SQLite 索引、受管图片池和标注是必选内容，模型二进制始终排除，模型配置保留为待重新导入状态。
- 导入备份时先解压到临时目录，验证格式版本、路径安全性、清单和校验和；全部通过后才原子移动为新的受管数据集并登记到 `library.json`，失败时不在内部资料库留下半导入目录。
- `DatasetTransferService` 比较源与目标的 `label_set_id` 和标签签名（稳定 ID、类别 ID、英文训练名）。完全一致才允许复制或移动；复制新建目标样本记录，移动在目标写入验证成功后移除源样本。

### 数据集模板与合并

- `DatasetTemplateService` 创建空数据集时复制源数据集的独立标签定义、命名规则、导入规范和视图偏好，但不复制图片、标注、模型二进制或回收站内容。复制前通过 `LabelSetComparisonService` 校验标签定义，不能把损坏或冲突映射写入新数据集。
- `LabelSetComparisonService` 输出按标签字段分类的差异报告；训练映射冲突为阻断级，显示字段差异为需确认级。
- `LabelSetMergeService` 只在无阻断冲突时执行。目标标签集是唯一事实来源，源中缺失于目标且训练映射兼容的标签可加入；不会自动重写已有标注。
- `DatasetMergeService` 在训练签名一致时预览样本数量和完全重复图，按用户选择复制或移动样本包。目标样本写入和索引校验完成前不得删除源样本。

## 13. 模型格式导出器

- `DatasetExporter` 是可注册接口；导出向导读取已注册导出器并让用户选择模型类型/格式。
- 每个导出器独立负责：支持的标注类型检查、必要转换、目录结构、配置文件和格式校验。
- `YoloDetectionExporter` 支持矩形框；未来的分割导出器不得伪造或猜测不存在的分割标注，应明确要求对应标注类型。
- 导出器输出只写入用户当次选择的目标目录，不能在内部资料库或数据集目录内保存方案、日志或历史记录。
- `SplitPlanner` 读取全局默认比例但接受当次导出覆盖；完全重复图片和已确认近似组均作为不可拆分单元，并在组约束导致比例偏差时说明原因。

## 14. 视觉系统

- 视觉事实来源为 `docs/VISUAL_DESIGN.md`。主题改用冷白/浅蓝灰背景、白色表面、清晰品牌蓝、Logo 浅橙/浅蓝、浅色画布底板和现代圆角组件；旧暖灰/灰绿莫兰迪主视觉、默认 Qt 灰色及步骤四早期大面积深色画布不得继续扩展。
- 工作台品牌区使用能按可见内容边界稳定缩放的独立组件；不得依赖带透明留白的 `QIcon` 默认布局推断 Logo 的实际视觉大小。常规宽度显示完整字标，响应式收纳达到阈值后再切换为 `DD` 标记。
- `ThemeService` 将 `appBackground`、`surface`、`surfaceSubtle`、`surfaceHover`、`brandPrimary`、`brandSoft`、`canvasBackplate`、`canvasImageBoundary`、`focusRing`、`danger`、`textPrimary`、`textSecondary` 等语义 token 映射为 Qt 调色板、QSS 和图标状态；业务控件不得散落颜色、圆角、字号和行高常量。
- 组件层集中提供主/次/幽灵/危险按钮、输入框、筛选 chip、菜单、表格、虚拟列表、数据集卡片、状态徽标、工具按钮、对话框和空状态；页面只组合组件，不复制成段 QSS。
- 版式 token 使用 4px 基础网格和 8/12/16/24/32px 常用间距，分别支持宽松主页与紧凑标注工作台；包含字体层级、圆角、轻阴影和 120–180ms 可选状态动画，并尊重 DPI 与“减少动态效果”。
- 状态色要同时使用图标、边框或文字，不得只依赖颜色区分；键盘焦点环不可被主题样式覆盖。
- 自有图标资源存放于 `assets/icons/`，由 `IconRegistry` 以语义名称（如 `import`、`export`、`auto_annotate`、`delete`）提供给 UI；SVG 为优先源格式，按需生成 PNG/ICO 等发布尺寸。任何图标替换只修改受管源资产及其派生物，不影响业务代码中的语义名称。
- 保留 Windows 原生窗口边框和系统按钮，品牌顶栏位于应用内容区。任何无边框窗口方案必须先验证拖动、缩放、阴影、DPI、键盘和辅助功能，不能只为外观牺牲稳定性。
- 应用入口负责在主窗口首次显示前请求 Windows/Qt 最大化状态；页面和业务 Gateway 不管理顶层窗口几何。最大化仅覆盖当前显示器可用区域，不把子对话框强制最大化，也不引入独占全屏或跨屏窗口逻辑。
- 固定截图回归覆盖主页与标注工作台的中英文、空/有数据、hover/selected/disabled、100%/150% DPI；视觉禁止项出现即视为回归失败。

## 15. 内置教程内容与进度

- 教程资源按 `content_version`、`app_version_range` 和语言组织，至少包含教程 ID、标题、摘要、预计时间、章节、插图引用、相关 `action_id`、外部链接及其目标说明。
- 简体中文和英文使用相同稳定教程 ID 与章节 ID。`LocaleService` 切换语言时，`TutorialCenter` 用相同 ID 重载正文并恢复章节、滚动位置和完成状态。
- 核心教程作为只读可信资源随应用打包，首页首次绘制后再延迟加载卡片摘要和插图，不能因解析全部教程拖慢应用启动。
- `TutorialProgressRepository` 只保存 `{tutorial_id, section_id, completed, last_position, content_version}` 等全局进度；数据集备份、X-AnyLabeling 交换和 YOLO 导出均不得包含这些记录。
- 教程跳转只分发已登记的无副作用导航 `action_id`。导入、删除、模型推理和导出等操作仍必须经过原有页面及确认流程，教程不能绕过安全检查。
- 核心内容不得从网络动态覆盖。外部官方文档通过系统浏览器打开，并在 UI 显示外链标记、站点说明和失败提示；离线失败不影响本地教程。
- 第三方命令、参数和截图记录适用版本。应用升级发现内容版本变化时迁移旧进度并把新增章节标记为未读，不能清空全部阅读记录。

## 16. 错误处理

- 图片无法加载：在文件列表标记错误，并允许继续浏览其他图片。
- JSON 解析失败：提示文件名与原因，不自动覆盖原文件。
- 保存失败：保留内存修改和脏状态，并提供重试入口。
- 资料库元数据写入失败：Repository 转换底层 I/O 异常，Service 保证公开变更只抛业务异常，Gateway 最终保证返回 `UiCommandResult`；回滚也失败时同时报告原始错误与回滚错误。
- 导出前校验失败：列出未标注、损坏或不支持的样本，并明确让用户选择跳过或取消；绝不静默遗漏。
- 教程正文或插图缺失：保留主页和数据集入口，显示可恢复的内容错误，不得因帮助资源损坏阻止应用启动。

## 17. X-AnyLabeling / LabelMe 互操作边界

- 正式流程由数据集级 `XAnyLabelingInteropService` 编排；页面和 Qt 对话框只通过 `ManagedDatasetGateway` 发出预检、提交、导出和取消请求，不直接访问外部目录或 SQLite。
- 导入预检是只读阶段，保存根目录与每个源文件的相对路径、大小、修改时间和 SHA-256。提交前必须重核指纹；来源发生变化时拒绝继续，且外部目录从不被移动、改名或补写。
- 标签解析只允许活动英文训练名的大小写不敏感精确匹配、用户显式映射、原子批量新建或只读保留；不使用模糊猜测。未知 shape 和未知标签 shape 作为兼容负载保留。
- 每个导入样本以 `managed_operations` 的 `xany_import` 恢复清单、受管 PNG/缩略图/JSON 发布和单个 SQLite 事务为边界。启动对账可完成已提交样本或撤回未登记文件，无法证明一致时保留现场诊断。
- 导出只接收当次范围快照和尚不存在的目标目录。在目标父目录同卷暂存，逐对回读图片与 JSON、核对尺寸/shape/标签并清除所有 `datumdock_*` 私有字段后，才原子发布最终目录。
- 当前 schema 保持 v3；兼容负载仍以 LabelMe JSON 为事实来源，不为步骤五新增第二份字段数据库。

## 18. 整数据集永久删除事务

- 新增正式边界 `DatasetDeletionService`、`DatasetDeletionPreflight`、`DatasetDeletionRequest`、`DatasetDeletionReport` 与 `DatasetDeletionRecoveryManifest`。UI 只能通过 `ManagedDatasetGateway` 请求预检和提交，不直接拼接目录或递归删除文件。
- 预检以数据集稳定 UUID 解析唯一受管目录，拒绝数据集名称路径、绝对/转义路径、非 UUID 目录和符号链接；统计图片、标注、标签、模型、索引、缩略图、缓存、样本回收站、内部恢复项、文件总数与字节数。
- 提交请求携带数据集 UUID、预检摘要、资料库修订号、用户输入的完整名称及第二次确认令牌。预检后名称、目录摘要、运行任务或资料库修订发生变化时，原确认失效并要求重新预检。
- 删除前由保存离开保护确认该数据集不存在失败的内存标注；后台任务服务确认没有仍在写入的导入、重命名、标注迁移、自动标注、样本删除或恢复任务。只读查询任务可取消并等待退出。
- 在资料库根目录的 `recovery/dataset-deletions/<operation_uuid>/` 写入原子恢复清单，然后将 `datasets/<dataset_uuid>` 在同卷原子移动到该操作的暂存目录。移动范围只允许这一条已解析 UUID 目录，不跟随其中未知符号链接。
- 暂存成功后原子更新 `library.json`，移除对应登记并刷新主页摘要；最后清理暂存树和恢复清单。最终清理结束前不得返回完整成功。
- 启动恢复按清单阶段处理：资料库仍登记时把完整暂存目录移回原 UUID 位置；资料库已移除时继续清理暂存内容。无法证明目录完整性或阶段一致时保留现场并显示诊断，不猜测覆盖、重新登记或删除其他目录。
- 递归清理只能删除恢复清单逐项登记且仍位于暂存目录内的普通文件/目录；遇到重解析点、路径逃逸、权限变化或摘要不符立即停止并报告。外部来源、已导出目录和备份包永远不进入清单。
- 首版不建立整数据集回收站。归档只修改元数据且可恢复；永久删除与样本级回收站是不同操作，不能复用“少量样本阈值”推断用户意图。

## English Summary

The architecture now specifies safeguarded whole-dataset permanent deletion through a gateway-only service, impact preflight, revision-bound confirmation, same-volume staging, an atomic library update, and deterministic startup recovery. Archive remains the reversible option; external sources, exports, and backups are never deletion targets. This deletion flow is specified but not yet implemented.
