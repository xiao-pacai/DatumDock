"""使用隔离 Ultralytics 环境回读 DatumDock 导出的 YOLO Detection 数据集。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ultralytics.data.dataset import YOLODataset
from ultralytics.data.utils import check_det_dataset


def validate(data_yaml: Path) -> dict[str, object]:
    """加载全部非空集合并返回图片、矩形和负样本统计。"""

    data = check_det_dataset(str(data_yaml))
    split_statistics: dict[str, dict[str, int] | None] = {}
    for split_name in ("train", "val", "test"):
        image_path = data.get(split_name)
        if image_path is None:
            split_statistics[split_name] = None
            continue
        dataset = YOLODataset(
            img_path=image_path,
            data=data,
            task="detect",
            augment=False,
            cache=False,
        )
        split_statistics[split_name] = {
            "images": len(dataset),
            "boxes": sum(len(label["cls"]) for label in dataset.labels),
            "negatives": sum(1 for label in dataset.labels if len(label["cls"]) == 0),
        }
    return {
        "names": data["names"],
        "splits": split_statistics,
    }


def main() -> int:
    """解析命令行并输出机器可读的验证统计。"""

    parser = argparse.ArgumentParser()
    parser.add_argument("data_yaml", type=Path)
    args = parser.parse_args()
    if not args.data_yaml.is_absolute() or not args.data_yaml.is_file():
        parser.error("data_yaml 必须是存在的绝对文件路径")
    print(json.dumps(validate(args.data_yaml), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
