# DatumDock 架构说明

## 1. 原则

- UI、领域模型、文件读写和应用状态分层，避免将业务规则写死在控件回调中。
- 标注数据模型优先独立于 PySide6，保证其可被测试和未来的命令行转换工具复用。
- 文件读取失败或 JSON 格式异常不应导致应用崩溃。

## 2. 建议代码结构

```text
src/datumdock/
├─ __main__.py              # 应用启动入口
├─ app.py                   # QApplication 初始化与依赖组装
├─ domain/                  # Workspace、Project、LabelSet、ModelEntry、Dataset、NamingPolicy、DatasetSample 等纯数据模型
├─ services/                # 工作区、项目、模型导入/推理、数据集、索引、缩略图、重命名、样本删除、标注读写、划分、模型格式导出
├─ ui/                      # 主窗口、项目/数据集导航、模型管理、设置、数据集池、画布、导出向导等
├─ state/                   # 当前工作区、项目、数据集、界面语言、快捷键、筛选条件、选中标注、脏状态等
├─ i18n/                    # 简体中文、英文翻译资源和本地化辅助函数
└─ resources/               # Qt 资源、图标或主题
```

## 3. 关键对象

| 对象 | 职责 |
| --- | --- |
| `Workspace` | 本机工作区入口，维护多个项目的注册信息、最近打开记录和全局偏好。 |
| `LocaleService` | 读取和保存全局界面语言，加载 Qt 翻译资源并通知可见界面重新翻译。 |
| `ShortcutService` | 集中注册操作及默认组合，读取/验证/保存用户绑定，并将更新即时应用到 Qt Actions。 |
| `ThemeService` | 集中提供界面设计令牌、状态样式、系统缩放和减少动态效果适配；不管理项目标签颜色。 |
| `IconRegistry` | 集中登记自有 SVG/PNG 图标、语义名称、尺寸与各状态变体，为菜单、工具栏、空状态和安装包提供统一资产。 |
| `Project` | 项目元数据、项目说明、项目级标签集及其多个数据集的索引。 |
| `LabelSet` | 项目唯一的标签集合，维护标签的稳定 ID、英文训练名、中文别名、描述、同义词、颜色和 YOLO 类别 ID。 |
| `LabelColorService` | 为活动标签分配高辨识度唯一颜色，验证手动颜色选择，并提供统一的 shape 渲染颜色。 |
| `LabelMigrationService` | 预检标签修改影响范围，并将需要持久化的名称变更安全地批量同步到所有相关标注文件。 |
| `LabelSetComparisonService` | 计算标签集训练映射签名和逐字段差异，作为模板、转移、合并的安全门槛。 |
| `LabelSetMergeService` | 在无训练映射冲突时，以明确目标版本为主安全合并标签定义并记录来源。 |
| `LabelInspectionService` | 以项目标签 ID 为条件跨数据集查询样本，构建不复制文件的实时标签检查集合。 |
| `ModelEntry` | 项目模型条目的稳定 ID、显示名、受管文件路径、格式、任务类型、探测元数据、运行配置、标签映射和状态。 |
| `ModelImportService` | 复制模型到项目模型目录，识别格式、提取元数据、验证可加载性并生成可编辑配置。 |
| `AutoAnnotationService` | 使用已验证模型对样本推理，将结果作为可审阅的待确认标注交给画布。 |
| `Dataset` | 独立数据集的数据集池和本地元数据位置；引用项目级标签集。 |
| `NamingPolicy` | 数据集的图片命名模板、前缀、起始编号、补零位数和扩展名保留规则。 |
| `ImageImportProfile` | MVP 导入图片的支持格式和统一 PNG 转码规则；后续可扩展其他受管格式策略。 |
| `DatasetSample` | 样本稳定 ID、原图路径、标注路径、尺寸、导入时间、状态和可选标签。 |
| `ImageDocument` | 当前打开图片及其标注集合和脏状态。 |
| `RectangleShape` | 类别、两个图像坐标点、可选属性。 |
| `Label` | 单个项目标签的稳定 ID、英文训练名、中文别名、描述、同义词、颜色和状态。 |
| `LabelMeRepository` | LabelMe JSON 的读取、校验与写入。 |
| `XAnyLabelingInteropService` | 递归导入 X-AnyLabeling 图片与同名 LabelMe JSON，并生成可被 X-AnyLabeling 重新打开的交换目录。 |
| `CompatibilityPayloadRepository` | 保存并回写当前不可编辑的 X-AnyLabeling shape 与扩展字段，保证保存、迁移、重命名和导出不会静默丢失它们。 |
| `WorkspaceService` | 创建、打开、登记、切换和恢复多个项目/数据集上下文。 |
| `DatasetPoolService` | 将图片复制到目标数据集的受管池，建立样本索引、去重、搜索和筛选。 |
| `DatasetTemplateService` | 从源数据集复制可复用配置并创建空目标数据集，不复制样本和标注。 |
| `ImageImportService` | 在后台复制或转码常见静态图片，生成导入报告并原子写入受管池与项目索引。 |
| `DuplicateDetectionService` | 基于最终像素内容哈希检测完全相同图片，并在导入前提供已有样本的对比与确认信息。 |
| `SimilarityGroupService` | 在后台以感知哈希生成近似图片候选组，维护用户确认的相似组并为分组划分提供约束。 |
| `ProjectIndexRepository` | 使用 SQLite 维护项目级样本、标签关联、状态、路径和统计索引，为万级样本筛选与标签检查提供查询。 |
| `ThumbnailService` | 在后台按需生成和缓存缩略图，以稳定样本 ID 为键，并提供可取消的优先级队列。 |
| `SampleRenameService` | 预览并安全执行池内样本批量重命名，同步 LabelMe、索引、缓存和路径引用。 |
| `SampleDeletionService` | 预检并以受管事务删除样本的图片、标注、索引与派生信息。 |
| `TrashService` | 将少量删除样本包移入项目回收站，支持恢复和永久清空；大批量删除可跳过回收站。 |
| `InferenceBackendSelector` | 选择可用 GPU 推理后端，无法使用时回退 CPU，并提供首次 CPU 推理的诊断与配置指引。 |
| `ProjectBackupService` | 导出项目压缩包、写入版本和完整性清单，并在导入前验证结构和文件校验。 |
| `DatasetTransferService` | 在标签集完全一致的数据集之间安全复制或移动样本包。 |
| `DatasetMergeService` | 对兼容数据集预览标签差异、样本数和重复图，再执行可恢复的复制或移动合并。 |
| `ExportRequest` | 当次导出的临时参数：目标格式、候选样本、比例、种子和输出目录；不持久化。 |
| `SplitPlanner` | 根据候选样本、比例和种子生成确定性的 train/val/test 划分。 |
| `DatasetExporter` | 模型格式导出器的统一接口，声明支持的标注类型、校验规则和输出方式。 |
| `YoloDetectionExporter` | 首个 `DatasetExporter` 实现，将图片、标签和 `data.yaml` 写入独立 YOLO 目录。 |
| `AnnotationCanvas` | 坐标变换、绘制和鼠标交互；不负责 JSON 持久化。 |
| `MainWindow` | 组合 UI，转发用户意图到状态和服务层。 |

## 4. 坐标约定

- 数据模型始终保存原始图片像素坐标。
- 画布显示坐标通过缩放与平移转换得到。
- 写入前将矩形坐标归一化为左上角与右下角，避免反向拖拽产生无效数据。

## 5. 工作区、数据集池与导出边界

推荐受管存储布局如下；实现可调整文件名，但隔离关系必须保持：

```text
workspace/
├─ workspace.json
└─ projects/
   └─ {project-id}/
      ├─ project.json
      ├─ label-set.json
      ├─ project-index.sqlite
      ├─ trash/
      │  └─ {trash-item-id}/
      ├─ models/
      │  └─ {model-id}/
      │     ├─ model.onnx 或 model.pt
      │     └─ model.json
      └─ datasets/
         └─ {dataset-id}/
            ├─ dataset.json
            ├─ pool/
            │  ├─ images/
            │  └─ annotations/
            └─ cache/
               └─ thumbnails/
```

- `label-set.json` 是项目级事实来源。标签记录建议为 `{id, class_id, name, alias, description, synonyms, color, status}`；`id`、`class_id` 与 `name` 要受到变更保护。
- 标签集同时生成两类签名：`training_signature` 覆盖稳定标签 ID、类别 ID、英文训练名与状态，用于判断能否安全合并数据；`display_signature` 覆盖别名、描述、同义词和颜色，用于展示差异。签名与版本记录在项目元数据中。
- `color` 是项目级标签定义的一部分。`LabelColorService` 使用预定义的可访问调色板及色差校验生成候选色；活动标签颜色不得重复，归档标签颜色可复用。
- 全局应用设置保存 `ui_locale`（初始值 `zh_CN`）、`shortcut_overrides`、默认数据划分比例和回收站少量样本阈值，不保存在项目或数据集元数据中；翻译资源随应用安装包提供，例如 `i18n/datumdock_zh_CN.qm` 与 `i18n/datumdock_en_US.qm`。
- 每个模型在项目 `models/{model-id}/` 中受管存放，`model.json` 记录 `{id, display_name, format, task_type, source_filename, runtime_config, model_classes, label_mapping, status}`。模型二进制和配置只属于其所在项目；项目备份只保留模型配置并在导入后标记二进制待重新导入。
- `project-index.sqlite` 是万级样本的查询事实来源，至少包含 `datasets`、`samples`、`sample_labels`、`sample_review`、`similarity_groups`、`similarity_members`、`import_jobs` 和回收站记录。对 `dataset_id`、`filename`、`review_status`、`label_id`、相似组 ID、更新时间建立索引。
- `sample_review` 是互斥的图片级状态，至少区分 `unreviewed`、`auto_pending_review`、`reviewed` 与 `issue`；从 `issue` 完成复核时必须原子切换为 `reviewed`。自动标注 shape 保存 `model_id`、推理时间和置信度，人工标注不被模型任务覆盖。
- `trash/` 只保存被选择“移入回收站”的完整样本包及恢复元数据；永久删除和大批量删除不进入该目录。
- 图片及其 LabelMe JSON 在导入时复制到目标数据集池；池内可使用样本 ID 命名以避免同名文件冲突，并在元数据中保留原始来源路径。
- 外部 X-AnyLabeling/LabelMe 文件的兼容载荷与 DatumDock 的可编辑矩形框分层存储：矩形框解析为内部 `label_id` 与像素坐标；其他 shape 及 `flags`、`attributes`、`description`、`difficult`、`score` 等未知或未支持字段保留为只读兼容载荷。`LabelMeRepository` 在写回或导出时按原顺序合并该载荷，不将私有稳定 ID、复核状态或模型元数据泄漏到交换 JSON。
- `XAnyLabelingInteropService` 的导入流程为“扫描配对 → 校验 JSON → 复制并转 PNG → 解析/保留 shape → 原子写入索引”；导出流程为“生成临时目录 → 写 PNG、同名 JSON 与 `labels.txt` → 校验配对和 `imagePath` → 原子替换目标目录”。具体兼容契约见 `docs/X_ANYLABELING_INTEROP.md`。
- `dataset.json` 包含可选 `naming_policy`。它只定义受管池中图片的整理名称，绝不回写外部来源文件；`DatasetSample.id` 是不随文件名变化的内部身份。
- 样本 ID 必须独立于文件排序；建议由导入时生成 UUID，并记录原始规范化路径、最终 PNG 内容哈希和文件指纹用于完全重复检测。
- 标注数据优先存储稳定标签 ID，并在读写 LabelMe/YOLO 时解析到英文训练名或类别 ID；这能避免中文别名或描述修改影响既有标注。
- 标签管理页面显示的 `usage_count` 由项目索引或按需扫描计算，不作为标签定义的事实来源；它用于修改前影响预览和用户理解。
- 项目样本索引需维护 `label_id → sample_id` 的可查询关联及每样本的标签框计数，以支持标签检查集合而无需每次扫描全部 JSON 文件。
- 切换工作区、项目或数据集是状态边界：当前文档的未保存改动必须先处理，随后刷新目标项目标签集、目标数据集样本索引和筛选条件，并清空尚未执行的临时导出请求。
- 导出属于一次不可修改原池的临时操作。`ExportRequest` 只存在于当前导出流程，完成或取消后不写入项目元数据；默认复制文件，硬链接或符号链接可作为后续可选优化。
- 划分器在固定种子下先稳定排序样本 ID，再伪随机打乱，以保证结果可复现。
- MVP 的确定性划分器以完全重复图片和已确认近似图片组为不可拆分单元，在保持组完整的前提下尽量接近用户比例；更精细的类别分层优化属于后续增强，但不得以拆分关联图片为代价。

## 6. 万级样本性能与后台任务

- 样本表、标签检查集合和搜索结果必须通过 SQLite 分页查询；UI 列表/网格采用虚拟化模型，仅创建可视区域及小范围预取的控件。
- 图片解码、缩略图生成、文件复制/转码、完整性扫描、批量重命名、批量删除、模型推理和导出在后台任务执行；主线程只接收进度、结果和错误事件。
- `ThumbnailService` 以 `sample_id + 文件版本` 为缓存键，按当前可见项目、滚动方向和标签检查集合优先级生成；离开视图时可取消低优先级任务。
- 项目打开时先载入元数据、统计和首屏样本；不得同步扫描一万张图片或生成全部缩略图。全量扫描作为可显示进度和可取消的后台维护任务。
- 所有批量任务必须支持进度、取消和可恢复状态；取消后已完成项保持一致，未完成项不应产生半写入文件或索引。

## 7. 样本重命名与删除

- `SampleRenameService` 先根据 `NamingPolicy` 和候选样本生成完整预览，再校验文件名、扩展名、目标冲突和可写性。重命名通过临时名称避免交换或序号覆盖冲突。
- 每次样本重命名须同步图片文件、配套 LabelMe JSON 文件名、LabelMe `imagePath`、样本索引、缩略图缓存和其他记录的路径；样本稳定 ID 与标注内容保持不变。
- `SampleDeletionService` 对单个样本收集受管关联项：图片、LabelMe JSON、索引记录、缩略图、缓存及自动标注信息。确认后委托 `TrashService` 移入回收站或直接永久删除，并在完成时重新计算标签 `usage_count`。
- 删除服务的范围严格限制在目标数据集池。它不得沿原始来源路径向外删除文件，也不得删除项目级标签、模型或其他样本。
- 这两类多文件操作须保存最小恢复信息并按步骤校验；若任一步骤失败，要回滚已经改变的内容或留下可恢复状态，禁止默默产生孤立文件或索引。

### 图片导入与转码

- `ImageImportService` 接受 JPG/JPEG、PNG、BMP、WebP、TIFF 等静态图片，并将外部源文件复制后统一转码为池内 PNG；源文件路径仅记录用于可追溯，不参与后续写入。
- 转码过程在后台进行，先写入临时 PNG 并验证可读性、尺寸和像素内容哈希后再原子放入池内和索引；透明通道原样保留。
- `DuplicateDetectionService` 在最终 PNG 哈希与已有样本相同时，向 UI 提供两图预览、来源路径和已存在样本信息；用户明确继续后仍创建两个稳定样本 ID。
- 失败、重复、取消和成功结果写入导入报告，已完成项保持一致，未完成项不创建索引记录。

### 近似图片与分组划分

- `SimilarityGroupService` 在后台为受管 PNG 生成感知哈希，先以保守阈值产出候选对/候选组；不阻塞导入、浏览或标注。
- 高置信度候选可自动建立相似组，边界候选进入人工检查队列。用户可确认组、把误报图片移出组或忽略候选；不会自动删除或合并任何样本。
- `SplitPlanner` 使用已确认相似组作为不可拆分单元：组内样本必须分配到同一 train/val/test 集合。它以组为最小粒度优化比例，结果允许因组大小而与目标比例存在已解释的少量偏差。

## 8. 国际化

- 使用 PySide6/Qt 的 `QTranslator` 或等价资源机制管理系统文案；所有可见系统字符串从集中翻译键加载。
- `LocaleService` 更换翻译器后触发窗口、菜单、工具栏、模型/标签管理页和活动对话框的 `retranslateUi`，以实现即时切换。
- 开发阶段以中文源文案和英文翻译资源为基准；缺失翻译必须在质量检查中被发现，不能静默显示错误语言或翻译键。
- 领域数据与 UI 资源严格分离。`Label.alias`、`Label.description` 等用户录入内容按原样显示，不通过翻译系统处理。

## 9. 快捷键管理

- 所有可配置用户操作注册为稳定的 `action_id`，例如 `file.save`、`canvas.delete_shape`、`dataset.next_sample`；UI 文案和默认快捷键与 `action_id` 分离。
- `ShortcutService` 维护默认绑定、用户覆盖绑定和不可配置保留组合。应用启动时将有效绑定分配给对应 `QAction`/快捷键对象。
- 保存新绑定前，服务检查空值、语法、同一上下文冲突、跨上下文歧义和系统保留组合；允许用户明确替换冲突绑定，但不允许绕过保留组合。
- 更新绑定时撤销旧 `QAction` 快捷键并立即应用新绑定；恢复默认时删除对应 `shortcut_overrides` 项。
- 快捷键配置应有单元测试，覆盖默认值、冲突检测、替换、恢复默认和设置持久化。

## 10. 标签检查与批量迁移

- `LabelInspectionService` 接收项目 ID、目标 `label_id` 和可选数据集/样本筛选条件，返回按稳定样本 ID 去重的实时结果集及目标标签框数量。
- 结果集只保存查询条件和排序/分页状态，不复制图片、创建额外数据集或改变文件系统；样本打开操作通过原始 `dataset_id` 和 `sample_id` 回到对应画布。
- 当标签迁移、样本保存、重命名或删除完成时，相关索引应在同一受管操作中更新，确保标签检查集合不会展示已失效路径。

- `LabelMigrationService` 对英文训练名等影响持久化标注的改动执行“预检 → 用户确认 → 写入 → 校验 → 完成”流程。
- 预检产出受影响的数据集、样本文件和 shape 数量；无写权限、损坏 JSON 或未知标签引用必须在确认前提示。
- 迁移写入应使用同目录临时文件和原子替换；在开始写入前保留最小恢复信息，以便失败时回滚已完成文件或下次启动继续恢复。
- 仅改变中文别名、描述、同义词、颜色等显示字段时，不改写每张标注 JSON；显示层从项目标签集实时解析即可。
- 内部 `label_id` 永远不变；LabelMe 的 `shape.label` 与项目英文训练名保持同步，YOLO 导出则始终从当前标签集读取 `class_id`。

## 11. 自动标注模型导入与运行

- `ModelImportService` 根据扩展名和文件签名路由到格式适配器，首选 `OnnxModelInspector`；PT 首个适配器处理可验证的 Ultralytics YOLO 检测 checkpoint 和兼容自训练 YOLO 模型，其他变体必须明确提示不支持。
- ONNX 探测读取图结构、输入输出张量名、元素类型、形状和 metadata properties。若包含类别、模型任务或预处理信息，应纳入建议配置但不盲目信任。
- 模型适配器输出统一的 `ModelInspectionResult`：格式、任务候选、输入规格、输出规格、类别表、建议预处理、解码器候选、警告和不可确定字段。
- 导入先复制到临时位置并探测、验证；仅验证成功后才原子移动到项目 `models/` 目录并创建 `ModelEntry`。更新模型也遵循同一流程，旧模型在新模型成功前保持可用。
- `InferenceBackendSelector` 启动时探测 ONNX Runtime 可用执行提供程序；有可验证 GPU 提供程序时优先 GPU，否则使用 CPU。首次 CPU 推理显示诊断结果、继续按钮和打开本地 GPU 配置指引的入口。
- 推理运行使用本地后端；PT 后端只在受支持格式和依赖可用时启用。模型加载、推理和自动标注均不上传本地数据。
- 类别映射是 `ModelEntry` 的配置：模型类别可映射到项目稳定 `label_id`，未映射类别默认不生成标注。自动标注结果立刻写入标注文件并带 `model_id`、置信度与图片级待复核状态；模型任务不改写既有人工标注。

## 12. 项目备份、导入与数据集间转移

- `ProjectBackupService` 生成带格式版本、`manifest.json`、文件清单和校验和的压缩项目包；项目元数据、标签集、SQLite 索引、受管数据集池和标注是必选内容，模型二进制始终排除，模型配置保留为待重新导入状态。
- 导入备份时先解压到临时目录，验证格式版本、路径安全性、清单和校验和；全部通过后才原子移动为新的受管项目，失败时不在工作区留下半导入目录。
- `DatasetTransferService` 比较源与目标的 `label_set_id` 和标签签名（稳定 ID、类别 ID、英文训练名）。完全一致才允许复制或移动；复制新建目标样本记录，移动在目标写入验证成功后移除源样本。

### 数据集模板与合并

- `DatasetTemplateService` 创建空数据集时复制源数据集的命名规则、导入规范和视图偏好，并引用同项目标签集；跨项目模板先通过 `LabelSetComparisonService` 比较，不能自动把不兼容标签带入目标项目。
- `LabelSetComparisonService` 输出按标签字段分类的差异报告；训练映射冲突为阻断级，显示字段差异为需确认级。
- `LabelSetMergeService` 只在无阻断冲突时执行。目标标签集是唯一事实来源，源中缺失于目标且训练映射兼容的标签可加入；不会自动重写已有标注。
- `DatasetMergeService` 在训练签名一致时预览样本数量和完全重复图，按用户选择复制或移动样本包。目标样本写入和索引校验完成前不得删除源样本。

## 13. 模型格式导出器

- `DatasetExporter` 是可注册接口；导出向导读取已注册导出器并让用户选择模型类型/格式。
- 每个导出器独立负责：支持的标注类型检查、必要转换、目录结构、配置文件和格式校验。
- `YoloDetectionExporter` 支持矩形框；未来的分割导出器不得伪造或猜测不存在的分割标注，应明确要求对应标注类型。
- 导出器输出只写入用户当次选择的目标目录，不能在工作区或数据集目录内保存方案、日志或历史记录。
- `SplitPlanner` 读取全局默认比例但接受当次导出覆盖；完全重复图片和已确认近似组均作为不可拆分单元，并在组约束导致比例偏差时说明原因。

## 14. 视觉系统

- 主题以明亮、小清新的低饱和莫兰迪色为基础：暖灰背景、雾绿灰面板、灰豆绿主强调和雾玫瑰次点缀。设计令牌集中定义，例如 `appBackground`、`surface`、`surfaceSubtle`、`panel`、`accent`、`accentSoft`、`danger`、`textPrimary` 和 `textSecondary`；业务控件中不得散落硬编码色值。
- `ThemeService` 将 token 映射为 Qt 调色板、QSS 和图标状态；组件只消费语义 token，不能自行猜测颜色。项目标签的唯一颜色仍由 `LabelColorService` 提供，绝不能混入应用强调色和错误色。
- 版式 token 包含 8px 间距基准、文字层级、圆角、分隔线、轻阴影和 120–180ms 的可选状态动画；它们应随系统 DPI 缩放并尊重“减少动态效果”偏好。
- 状态色要同时使用图标、边框或文字，不得只依赖颜色区分；键盘焦点环不可被主题样式覆盖。
- 自有图标资源存放于 `assets/icons/`，由 `IconRegistry` 以语义名称（如 `import`、`export`、`auto_annotate`、`delete`）提供给 UI；SVG 为优先源格式，按需生成 PNG/ICO 等发布尺寸。任何图标替换只修改受管源资产及其派生物，不影响业务代码中的语义名称。

## 15. 错误处理

- 图片无法加载：在文件列表标记错误，并允许继续浏览其他图片。
- JSON 解析失败：提示文件名与原因，不自动覆盖原文件。
- 保存失败：保留内存修改和脏状态，并提供重试入口。
- 导出前校验失败：列出未标注、损坏或不支持的样本，并明确让用户选择跳过或取消；绝不静默遗漏。

## English Summary

The architecture separates domain models, services, UI, and application state. `ProjectIndexRepository` uses SQLite, while virtualized views, lazy thumbnails, and background jobs support at least 10,000 common images per project. `ImageImportService` copies and normalizes supported images to managed PNG, while `XAnyLabelingInteropService` performs atomic X-AnyLabeling import/export and `CompatibilityPayloadRepository` preserves unsupported shapes. `TrashService` restores small deleted sample bundles or bypasses the trash for permanent bulk deletion. Auto-annotations are persisted with review status, source model, and confidence. `LocaleService`, `ShortcutService`, and `LabelColorService` manage global UI settings and consistent label presentation.
