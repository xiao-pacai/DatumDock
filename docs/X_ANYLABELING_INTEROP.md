# X-AnyLabeling 互操作规范

> 实现状态（2026-07-20）：正式受管目录预检/导入、显式标签解析、恢复型提交、独立目录导出、`labels.txt`、私有字段清理和自动化双向保真已经实现。X-AnyLabeling v3.3.10 实际打开、编辑、保存和回导因可信依赖安装 TLS 中断尚未完成，因此步骤五仍未通过最终硬闸门。

## 1. 目标

用户可以将已有的 X-AnyLabeling 图片标注目录导入 DatumDock，继续进行数据集管理和矩形框标注；也可以从 DatumDock 导出一个目录，让 X-AnyLabeling 直接打开图片并加载对应标注。

互操作以 X-AnyLabeling 使用的 LabelMe JSON sidecar 工作流为边界：图片文件与同名 `.json` 标注文件位于目录中，JSON 的 `imagePath`、图片尺寸和 `shapes` 记录必须匹配。

本规范参考 X-AnyLabeling 官方 [用户指南](https://github.com/CVHub520/X-AnyLabeling/blob/main/docs/en/user_guide.md) 中的图片目录、删除标注文件、shape 字段和 LabelMe 工作流说明，以及 [项目 README](https://github.com/CVHub520/X-AnyLabeling/blob/main/README.md)。

## 2. 从 X-AnyLabeling 导入

### 输入范围

- 用户选择包含图片与同名 LabelMe JSON 的 X-AnyLabeling 标注目录；允许嵌套目录扫描。
- 支持 X-AnyLabeling 常见静态图片格式：JPG/JPEG、PNG、BMP、WebP、TIFF。
- 图片复制到 DatumDock 受管数据集池，并在 MVP 中统一转为 PNG；外部 X-AnyLabeling 原目录不被修改。
- 导入器读取 JSON 的 `version`、`flags`、`shapes`、`imagePath`、`imageData`、`imageHeight` 和 `imageWidth`，并校验图片尺寸与路径。

### shape 与字段处理

| X-AnyLabeling / LabelMe 内容 | DatumDock 当前行为 |
| --- | --- |
| `rectangle` | 导入为可编辑矩形框，并映射到项目标签。 |
| `label` | 映射到英文训练名；可在导入向导中建立中文别名、描述和颜色。 |
| `score` | 作为兼容字段保留；不影响人工矩形编辑。 |
| `group_id`、`description`、`difficult`、`flags`、`attributes` | 保留为 shape 或图片的兼容负载，不在 MVP 主界面编辑。 |
| polygon、rotation、circle、line、point 等其他 shape | 原样保留为不支持 shape；在 DatumDock 中显示提示但不提供编辑，X-AnyLabeling 导出时必须还原。 |

- 任何 JSON 损坏、图片缺失、尺寸不一致、未知标签或不支持 shape 都必须在导入报告中逐项呈现。
- 不支持 shape 绝不因保存、重命名、标签迁移或导出被静默删除。若用户执行会影响其语义的操作，应用必须先提示兼容风险。

### 标签集导入整改（已完成）

- [x] 同时从所有 shape 的 `label` 与交换目录根部的 UTF-8/UTF-8 BOM `labels.txt` 收集标签；`labels.txt` 中引用数为零的预定义标签也进入映射页。
- [x] 活动标签的英文训练名大小写不敏感完全一致时自动映射到现有标签；当前数据集中不存在的标签默认选择“新建数据集标签”，用户仍可改为已有标签或只读保留。
- [x] 默认新建保留外部原名作为别名。原名符合训练名规则时直接用作英文训练名；中文、空格或其他不安全字符生成稳定、唯一的安全训练名。
- [x] 映射表使用足够行高、可伸缩下拉框和清晰的“别名 · 英文训练名”展示，当前选择不再裁切或重叠。
- [x] 确认导入后，新标签由 `LabelSetService` 一次修订原子写入；映射 rectangle 使用稳定标签 ID，重启后仍可编辑。
- [x] 导入报告区分新建、映射和只读保留；若 `labels.txt`、JSON 或图片在预检后变化，会在创建标签前阻止提交并要求重新预检。
- 标签名称冲突按 DatumDock 标签集比较规则处理，不自动猜测两个不同名称是否同义。

## 3. 导出为 X-AnyLabeling 可打开目录

### 导出结构

```text
xanylabeling-export/
├─ image_000001.png
├─ image_000001.json
├─ image_000002.png
└─ labels.txt
```

- 每张图片都导出为受管 PNG 的副本。
- 只有图片至少包含一个 DatumDock 可编辑矩形或导入时保留的兼容 shape，才生成同名 `.json` 文件；完全没有 shape 的图片不生成空 JSON。
- 只含 polygon、rotation、circle、line、point 或未知标签 shape 等兼容内容的图片仍生成 JSON，确保外部标注不会因 DatumDock 当前不可编辑而丢失。
- JSON 使用标准 LabelMe 字段：`version`、`flags`、`shapes`、`imagePath`、`imageData`（默认 `null`）、`imageHeight`、`imageWidth`。
- DatumDock 矩形框写入 `shape_type: "rectangle"`、英文训练名 `label` 和两个对角点 `points`。
- 导入时保留的不支持 shape 和兼容字段与 DatumDock 矩形框合并写回；相对图片路径和尺寸按导出目录重新生成。
- `labels.txt` 每行一个英文训练名，供用户在 X-AnyLabeling 中按需载入预定义标签；项目标签颜色、中文别名和描述不强行写入 X-AnyLabeling 全局配置。

### 导出限制

- DatumDock 的图片级复核状态、相似组、项目标签中文信息、模型来源、回收站和数据集管理元数据不属于 LabelMe/X-AnyLabeling 标注格式，因此不写入导出 JSON。
- 当前仅能编辑矩形框。导出的其他 shape 来自导入时保留的兼容负载；新建或编辑它们需等待 DatumDock 对应 shape 支持。
- 互操作导出是独立副本，不影响受管数据集池，也不等同于 YOLO 训练数据集导出。

## 4. 验证与回归测试

- 使用一组 X-AnyLabeling 创建的矩形框、含 score/flags/attributes 和混合 shape 样例进行导入测试。
- 验证矩形框可编辑、保存、重开；不支持 shape 和附加字段经“导入 → DatumDock 修改矩形 → X-AnyLabeling 导出”后仍存在。
- 使用 X-AnyLabeling 实际打开导出目录，确认图片加载、同名 JSON 自动加载、矩形框显示正确，且保留 shape 可见。
- 任何兼容字段丢失、尺寸错误、`imagePath` 错误或无法被 X-AnyLabeling 打开均为阻断发布缺陷。

## 5. 当前实现证据与未完成项

- 正式 `ManagedDatasetGateway` 使用数据集级互操作服务，不调用旧 `Workspace/Project` 路径模型；预览模式仍为纯内存且零写盘。
- 预检递归配对同相对目录、同 stem 的图片与 JSON，拒绝符号链接、绝对/UNC/盘符/`..` 路径、尺寸冲突、无效 `imageData` 和提交前来源变化。
- 导入复用 PNG 规范化、重复判断和缩略图，并以单样本恢复日志、文件发布和 SQLite 事务作为提交边界；故障注入证明不会留下半登记样本。
- 导出在目标父目录同卷暂存，回读全部图片和实际生成的 JSON、核对 shape/尺寸/标签并递归剔除 `datumdock_*` 后才原子发布；失败时最终目录不存在。
- 自动化当前已覆盖混合 shape 顺序、未知矩形只读保留、扩展字段、无标注图片不生成 JSON、100 图导入/重开/导出、双数据集任务隔离和来源树不变；`labels.txt`、未知标签默认新建、映射 UI 和新标签可编辑矩形闭环仍待按本节补充。
- 尚未完成：在独立环境中用固定 X-AnyLabeling v3.3.10 实际打开导出目录、编辑矩形、保存并回导。官方源码已取得，但可信 PyPI 安装第三方 GUI 依赖时发生 TLS EOF；官方 Windows CPU 资产下载也因网络过慢超时，未通过完整摘要校验且未执行。未关闭 TLS 校验。

## English Summary

The next interoperability correction is documented but not yet implemented: import must read JSON shape labels and root `labels.txt`, default unknown labels to dataset-label creation, repair the mapping UI, preserve unsafe names as aliases, and reopen mapped rectangles as editable annotations. Existing recoverable import/export and compatibility-payload tests remain in place, while the external X-AnyLabeling GUI hard gate is still pending.
