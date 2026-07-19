# DatumDock 受管图片池

> 状态：2026-07-19 步骤三已实现。本文记录普通模式中已经过测试的图片导入、索引、浏览、重命名和删除边界。矩形标注持久化、X-AnyLabeling、模型和导出不属于本步骤。

## 1. 用户数据边界

- 用户选择的文件或目录只是导入来源。DatumDock 不修改、重命名、移动或删除来源文件。
- 成功图片发布到 `%LOCALAPPDATA%\DatumDock\datasets\<dataset_uuid>\pool\images`；用户显示名不参与路径拼接。
- 外部绝对来源路径只存在当次导入会话，持久化数据只保留原始文件名。
- 目录默认递归扫描，不跟随符号链接。损坏图片、多帧 WebP/TIFF 和非支持文件进入失败报告，不静默截取首帧。

## 2. 导入和 PNG 规范化

已支持 JPG/JPEG、PNG、BMP、WebP 和 TIFF 的静态单帧图片。所有格式，包括来源 PNG，都经过同一流程：

1. 枚举来源并检查文件类型、符号链接、帧数和可解码性。
2. 使用 EXIF 方向校正像素；灰度、灰度透明、RGB 和 RGBA 分别保留，调色板和 CMYK 转为安全的 RGB/RGBA。
3. 写入数据集内部导入暂存 PNG，刷新、`fsync` 并重新打开校验。
4. 计算规范化像素 SHA-256、受管 PNG 文件 SHA-256、版本化 64 位 dHash 与平均 RGB。
5. 生成最长边 320 px 的缩略图，缓存键使用稳定样本 UUID 和内容哈希版本。
6. 用户完成重复决策后，逐张登记可恢复操作，发布图片/缩略图，再提交 SQLite 样本行。

取消只在单样本事务边界生效：已提交样本继续有效，未提交项不会留下半文件或空索引。

## 3. 完全重复与近似图

- 完全重复以规范化像素 SHA-256 为准，因此文件编码或 EXIF 不同但最终像素相同的图片仍可被识别。
- 已有资料库匹配与同批次匹配都在提交前停下；每个重复项必须选择“跳过”或“仍然保留”，没有静默默认项。
- 近似图先以 8 个感知哈希分桶缩小候选，再校验 Hamming 距离与颜色距离。候选组初始为 `pending`，只有用户才能改为 `confirmed` 或 `ignored`。
- 本步骤不自动删除、合并或拆分近似图。已确认组已提供稳定查询接口，后续 `SplitPlanner` 必须将组内样本分配到同一个子集。

## 4. SQLite v1 与分页

`index.sqlite` 的 `user_version=1`。`DatasetSampleRepository` 是样本查询事实来源，包含：

- `samples`、`sample_labels`；
- `perceptual_hash_bands`；
- `similarity_groups`、`similarity_members`；
- `trash_items`、`managed_operations`；
- `naming_counters`、`migration_issues` 与所需查询索引。

v0→v1 迁移位于 SQLite 事务内，只转换确认在当前受管数据集内的绝对路径。无法安全转换的记录进入诊断，不删除文件。

正式右侧图片列表使用 `QListView + QAbstractListModel + QStyledItemDelegate`，固定每页 200 条。列表与网格共用当前页领域对象，缩略图按需读取，原图只加载当前样本。搜索、状态和排序均回到 SQLite 查询，不在 UI 中全量扫描。

## 5. 重命名和删除

- 重命名先生成旧名/新名/冲突预览，然后使用两段暂存名解决名称交换。样本 UUID、内容哈希和缩略图键不变。
- 若将来已经存在同名 LabelMe JSON，重命名会同步文件名和 `imagePath`；损坏 JSON 会中止操作，不覆盖原件。
- 删除数量不超过全局阈值时默认进入当前数据集回收站。恢复名称冲突时按当前命名规则分配新名，但样本 UUID 不变。
- 大批量或用户显式永久删除使用内部 `.deleting` 区与双重确认。只有索引删除提交后才清理文件，外部来源永远不进入删除目标。

## 6. 操作日志与启动对账

导入、重命名、移入回收站、恢复与永久删除均使用 `managed_operations`。启动时以“SQLite 索引状态 + 受管文件”共同判断：

- 索引未提交时恢复旧文件；
- 索引已提交时完成收尾；
- 两份目录同时存在、主图片缺失或日志负载损坏时，保留所有现存文件和日志供诊断，不猜测覆盖。

对账还会标记索引指向的缺失/损坏 PNG，报告未登记 PNG、迁移问题和残留临时文件。它不会自动删除未知文件。

## 7. 已验证与后续边界

步骤三自动回归覆盖：六种格式、EXIF、灰度/透明、多帧拒绝、来源树哈希不变、完全重复保留/跳过、近似组确认/忽略、v0→v1 迁移、缩略图重建、两数据集隔离、重命名交换、回收站冲突恢复、永久删除、崩溃窗口恢复、Gateway 异常边界与 10,000 条分页索引。

本步骤不创建空 LabelMe JSON，画布在普通模式为图片浏览模式。矩形框编辑、图片级复核、X-AnyLabeling、模型、YOLO 导出和备份将在后续步骤接入。

## English Summary

DatumDock step three implements a real managed image pool. It imports six common static formats, normalizes EXIF orientation and color mode into verified PNG files, requires explicit exact-duplicate decisions, records reviewable near-image candidates, and serves a 200-item paged Qt model backed by SQLite v1. Batch rename, per-dataset trash, conflict-safe restore, and permanent deletion use operation journals and restart reconciliation. External source files are never modified. Persistent annotations, X-AnyLabeling interchange, models, exports, and backups remain later-stage work.
