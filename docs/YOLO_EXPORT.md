# YOLO Detection 确定性导出

本文记录 DatumDock 步骤六已经实现并验证的 YOLO Detection 导出边界。该功能只生成训练数据集，不在应用内训练模型，也不保存导出方案或导出历史。

## 1. 用户流程

在标注工作台打开“分类导出 → YOLO Detection”后，用户可以：

1. 选择全部候选、当前筛选结果或显式选中的图片；
2. 决定是否加入已完成零框负样本、未复核零框图片或待复核图片；
3. 设置当次 `train / val / test` 比例和固定随机种子；
4. 选择一个尚不存在的目标目录；
5. 查看样本、框、类别、重复组、相似组、比例偏差和风险；
6. 确认后在后台导出，或在安全边界取消；
7. 查看结构化报告并打开输出目录。

设置页保存全局默认比例。向导中的临时修改只影响本次导出，不会回写设置或数据集。

## 2. 候选规则

默认候选必须属于当前数据集、不在回收站、图片和标注健康、复核状态为“已完成”，并且至少有一个有效可编辑矩形。

- 已完成且零框的图片可作为明确负样本加入，并生成同名零字节 `.txt`。
- 复核状态为空的零框图片需要启用高级选项并再次确认。
- 待复核图片必须由用户显式加入，并显示未完成人工确认的警告。
- 回收站、损坏图片、损坏或摘要不一致的 JSON、未知标签、非法矩形、未完成恢复以及只读兼容 shape 不会被静默修复或导出。

预检保存样本 UUID、图片和标注摘要、标注版本、复核状态、标签集修订及相似关系指纹。提交前会重新核对；发生变化后旧预检立即失效。

## 3. 类别映射

YOLO 类别 ID 直接使用数据集标签的稳定类别 ID，不按本次出现的标签重新编号。完整标签表必须连续覆盖 `0..N-1`，训练名必须大小写不敏感唯一。历史矩形引用归档标签时允许导出并给出警告；未知标签和类别空洞会阻断预检。

`data.yaml` 使用 UTF-8 和相对路径。比例为零的 `val` 或 `test` 仍保留空目录，对应 YAML 值为 `null`；`train` 和 `val` 键始终存在。

## 4. 防止数据泄露的划分器

`DeterministicSplitPlanner` 当前算法版本为 `group-stratified-v1`。

1. 每个候选样本先作为独立节点；
2. 规范化像素内容哈希相同的样本合并；
3. 每个已确认相似组的成员合并；
4. 重叠关系通过并查集继续传递，形成不可拆分连通分量；
5. 待确认相似关系只产生风险，已忽略关系不参与约束；
6. 划分以连通分量为最小单位，绝不会为了比例拆组。

规划器使用稳定 UUID 排序，以及 `SHA-256(seed + component_signature + algorithm_version)` 生成稳定次序。目标数量使用最大余数法，并在组完整的前提下综合样本数、每类图片数、每类框数、负样本数和稀有标签覆盖。相同快照、比例、种子和算法版本会得到相同划分指纹。

若一个超大组使目标比例无法精确满足，DatumDock 保留组完整并在预检中说明实际偏差。任何比例大于零的集合都必须至少取得一个连通分量，否则预检阻断。

## 5. 输出与原子发布

```text
<目标目录>/
├─ data.yaml
├─ images/
│  ├─ train/
│  ├─ val/
│  └─ test/
└─ labels/
   ├─ train/
   ├─ val/
   └─ test/
```

目标目录必须完全不存在。导出器在目标父目录同卷创建 `.datumdock-yolo-<uuid>.tmp`，复制 PNG、生成 TXT 和 YAML，刷新文件后再完整回读。验证覆盖图片尺寸、图片/标签 stem、类别、坐标、目录、划分、组约束和划分指纹。全部通过后才原子改名为最终目录。

失败或取消时最终目录不会出现。临时目录清理失败会报告准确位置，而不会伪报成功。导出只读访问受管数据集，不修改图片池、LabelMe JSON、SQLite、复核状态或标签集。

## 6. YOLO 坐标

矩形坐标按原图尺寸转换为：

```text
class_id x_center y_center width height
```

四个几何值位于 `0..1`，宽高必须大于零。DatumDock 使用确定性小数序列化；越界、零面积和非有限框在预检时阻断，不通过钳制掩盖数据错误。

## 7. 验证证据

步骤六的自动化测试覆盖比例、固定种子、查询顺序、重复/相似连通分量、稀有标签、类别空洞、负样本、非法框、目标冲突、快照失效、取消、复制失败、100 图导出、10,000 条规划和真实 Qt 向导。

独立 Python 3.11.15 环境使用 Ultralytics 8.4.104、PyTorch 2.13.0 CPU 和 torchvision 0.28.0 回读 100 张图片的导出结果。Ultralytics 实际加载得到 train 80、val 10、test 10，三个集合均无损坏图片；零比例与显式空标签负样本也通过独立加载。Ultralytics 不属于 DatumDock 运行依赖，不下载模型，也不执行训练。

## 8. 未包含能力

步骤六不包含模型推理、自动标注、Segmentation、COCO/VOC、备份、跨数据集移动/合并、训练或 Windows 安装包。相关入口必须继续明确显示未接入。

## English Summary

DatumDock Step 6 implements validated YOLO Detection export with per-run ratios, a fixed seed, atomic publication, explicit negative samples, and deterministic group-aware stratification. Exact duplicates and confirmed near-duplicate connected components are never split across train, validation, and test sets. A separate Python 3.11.15 environment with Ultralytics 8.4.104 and PyTorch 2.13.0 CPU successfully loaded a 100-image 80/10/10 export. Export does not mutate the managed dataset or persist export plans or history. Model inference, backups, cross-dataset governance, and packaging remain future work.
