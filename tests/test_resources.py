"""品牌资源路径的开发环境回归测试。"""

from datumdock.resources import resource_root


def test_resource_root_contains_brand_assets_in_source_checkout() -> None:
    """源码运行时必须能读取 GUI、安装包共用的 Logo 与应用图标。"""

    root = resource_root()

    assert (root / "assets" / "brand" / "datumdock-wordmark-v3.png").is_file()
    assert (root / "assets" / "brand" / "datumdock-app-icon.ico").is_file()
    assert (root / "installer" / "version_info.txt").is_file()
