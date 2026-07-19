# DatumDock 标签与矩形标注工作流

> 状态：2026-07-19 步骤四已实现。本文记录普通模式中已经过测试的数据集级标签、矩形框、LabelMe JSON、立即自动保存、图片级复核和恢复边界。模型推理、YOLO 导出、完整 X-AnyLabeling 目录导入导出与备份不属于本步骤。

## 1. 用户工作流

1. 从主页打开一个受管数据集，在“标签管理”中预先建立标签。
2. 每个标签包含稳定 UUID、类别 ID、英文训练名、中文别名、描述、同义词、唯一颜色和活动/归档状态。
3. 回到标注工作台，从右侧图片列表选择图片，在左侧选择矩形工具后画框。
4. 画布和右侧标注列表双向选择；矩形可移动、使用八个控制柄缩放、删除或改派标签。
5. 新建、移动、缩放、删除、换标签、撤销、重做和复核状态变化都会立即进入串行自动保存队列。
6. 用户以整张图片为单位确认“已完成”“已完成（无目标）”或“有问题”。
7. 在标签图片检查页选择一个标签，可分页查看所有包含该标签的图片，并双击跳回对应图片和首个目标框。

没有活动标签时，矩形工具保持禁用并提示先管理标签。归档标签不能用于新框，但历史框仍可显示、移动、删除或改派。

## 2. 标签规则

- 标签和标签集使用规范 UUID；类别 ID 在包括归档标签在内的整个标签集中唯一且不复用。
- 活动英文训练名按大小写不敏感规则唯一；活动颜色精确唯一。
- 中文别名必填；描述和同义词可选。标签搜索覆盖训练名、别名、描述和同义词。
- 修改别名、描述、同义词或颜色只写 `label-set.json`，不会触碰任何标注 JSON。
- 修改类别 ID 需要影响预览和修订号确认，但不会改写 LabelMe JSON。
- 修改英文训练名会备份并原子迁移受影响 JSON 的标准 `shape.label`，同时保留稳定 `datumdock_label_id`。
- 训练名迁移中断时，`recovery/label-migrations/` 中的有限操作记录用于启动恢复；恢复不会扫描一万份无关 JSON。

## 3. LabelMe 存储事实边界

标注文件固定存放在：

```text
datasets/<dataset_uuid>/pool/annotations/<image_stem>.json
```

JSON 是标注交换事实来源，SQLite v2 是分页查询与状态摘要事实来源。DatumDock 的受管 JSON：

- 使用标准 `shape_type: "rectangle"`、英文训练名和两个原图像素对角点；
- 保存私有稳定 `datumdock_shape_id` 与 `datumdock_label_id`，避免显示字段变化破坏内部引用；
- 新文件使用 `imageData: null`；已有合法 `imageData`、根字段和未知字段保持不变；
- 按原顺序保存矩形与未支持 shape，不会把兼容 shape 统一移动到文件前后；
- 保留 `flags`、`attributes`、`score`、`group_id`、`description`、`difficult` 等兼容负载；
- 未来执行外部目录导出时会递归剔除全部 `datumdock_` 私有字段。

步骤四只保证受管池内部 JSON 的读取、编辑与保真回写。完整 X-AnyLabeling 目录配对、`labels.txt`、独立导出目录和第三方应用实际打开验收仍属于后续步骤。

## 4. SQLite v2

`DatasetSampleRepository` 在事务中把步骤三 v1 升级为 v2，新增并维护：

- `annotation_count`、`annotation_state`、`annotation_version`、`annotation_sha256` 和更新时间；
- `sample_labels(sample_id, label_id, shape_id)` 反向索引；
- 标签使用量、标签筛选、复核状态筛选和跨页样本定位查询；
- v1 `auto_pending_review` 到 `pending_review` 的兼容迁移。

升级只迁移结构和已有索引行，不同步解析全部 JSON。标签检查、图片列表和万级定位均查询 SQLite，每页最多 200 条，不为一万张图片创建一万个控件。

## 5. 原子保存与恢复

一次真实保存依次完成：

1. 验证数据集、样本、标签、图片尺寸、矩形坐标、磁盘摘要和受管路径。
2. 在 `recovery/annotation-operations/<dataset_uuid>/` 写入操作标记。
3. 写入并验证临时 JSON，刷新后原子替换正式文件。
4. 重读正式 JSON 并计算 SHA-256。
5. 在一个 SQLite 事务内提交摘要、版本、框数、反向标签索引和复核状态。
6. 删除恢复标记并报告该文档版本已保存。

若 JSON 已提交但 SQLite 失败，恢复标记和原 JSON都会保留，样本进入 `recovery_required`。下次按需加载时只回放有限恢复标记。损坏 JSON 保持原字节并以只读异常显示，绝不为了继续编辑而覆盖。

每个自动保存请求持有不可变文档快照；同一数据集队列按 sample UUID 分别维护最后版本和摘要，不能把一张图的并发前提用于另一张图。切图、切数据集、返回主页或关闭窗口时，正在保存会先等待；失败时必须选择“重试保存 / 放弃内存修改 / 取消”。

## 6. 复核状态

| 状态 | 规则 |
| --- | --- |
| `unreviewed` | 新导入图片的默认状态。 |
| `pending_review` | 第一次人工画框后进入；已完成图片发生任何框编辑后也回到此状态。 |
| `completed` | 用户明确确认，且至少有一个有效矩形。 |
| `completed_negative` | 用户明确确认无目标，且矩形数量必须为零。 |
| `issue` | 用户明确标记有问题；编辑后仍保持，直到重新确认完成。 |

缺图、损坏 JSON、未知标签或待恢复操作属于派生异常，不允许用户手工设置。删除最后一个框后进入待审核，不会自动变成负样本。仅存在矩形也不会自动变成已完成。

矩形必须使用有限数值、面积大于零并完全位于图片边界内。面积大于零的极小框允许保存但产生质量警告；零面积、非有限或越界框会阻止保存。

## 7. 与图片治理协同

- 批量重命名同步修改标注文件名、`imagePath`、SQLite 路径、摘要和更新时间；提交失败恢复原文件字节及原摘要。
- 移入回收站时保留样本 UUID、标注文件、缩略图和反向索引；恢复名称冲突时按当前命名规则安全改名并重新计算 JSON 摘要。
- 永久删除同时删除受管图片、标注、缩略图、样本行和标签反向索引；外部来源文件永不属于删除目标。
- 缩略图“标注预览”开启时，仅对视图请求的当前页可见项按需加载矩形，关闭后清除叠加缓存，不全量扫描数据集。

## 8. 已验证证据

- Python 3.11：Ruff、格式、`compileall` 和完整 pytest 通过；结果为 153 passed、1 skipped、14 warnings。
- 唯一跳过是当前 Windows 账户缺少创建符号链接权限；14 条警告来自正式入口未调用的旧图片算法。
- 自动回归覆盖 100 张图片连续保存/重开、10,000 条标签和状态分页定位、双数据集快速保存隔离、JSON/SQLite 故障注入、训练名迁移与恢复。
- pytest-qt 覆盖真实鼠标画框、八点缩放、换标签、删除、撤销/重做和复核状态。
- 22 张原生截图覆盖中文/英文、1366×768、1440×900、1920×1080 的工作台、标签管理、标签检查、迁移编辑和保存失败保护。

## 9. 尚未完成

- ONNX/PT 模型导入、GPU/CPU 推理和自动标注；
- X-AnyLabeling 完整目录导入导出和独立应用打开验证；
- YOLO Detection 划分与导出；
- 项目备份、跨数据集复制/移动/合并；
- Windows 安装包和无 Python 环境验证。

## English Summary

Step four implements dataset-level labels, editable rectangles, ordered LabelMe persistence, immediate serialized autosave, image-level review states, label inspection, SQLite v2 indexing, and bounded recovery. Managed JSON preserves unsupported shapes and unknown fields in order, while internal stable IDs are stripped from future external export payloads. Rename, trash, restore, and permanent deletion now keep annotation files and SQLite summaries consistent. Model inference, complete X-AnyLabeling directory exchange, YOLO export, backup, and installer delivery remain future work.
