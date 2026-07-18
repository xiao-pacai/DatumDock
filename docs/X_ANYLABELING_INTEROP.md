# X-AnyLabeling 互操作规范

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

### 标签集导入

- 默认从所有 shape 的 `label` 收集英文训练名，创建或匹配项目标签集。
- 若用户同时提供 X-AnyLabeling 的标签列表或配置文件，导入向导可读取预定义标签和颜色作为辅助信息；项目级中文别名、描述和稳定类别 ID 仍由 DatumDock 管理。
- 标签名称冲突按 DatumDock 标签集比较规则处理，不自动猜测两个不同名称是否同义。

## 3. 导出为 X-AnyLabeling 可打开目录

### 导出结构

```text
xanylabeling-export/
├─ image_000001.png
├─ image_000001.json
├─ image_000002.png
├─ image_000002.json
└─ labels.txt
```

- 每张图片导出为受管 PNG 的副本，且具有同名 `.json` 文件。
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

## English Summary

DatumDock imports X-AnyLabeling image directories with same-name LabelMe JSON sidecars and exports a directory that X-AnyLabeling can open directly. Rectangles become editable DatumDock annotations. Unsupported X-AnyLabeling shapes and fields such as score, group ID, flags, attributes, and descriptions are retained as compatibility payload and merged back during export, rather than being silently lost. The export writes PNG images, same-name LabelMe JSON files, and an optional `labels.txt`; DatumDock-only management metadata is not exported.
