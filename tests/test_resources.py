"""品牌资源路径的开发环境回归测试。"""

from PIL import Image

from datumdock.resources import application_icon_path, resource_root


def test_resource_root_contains_brand_assets_in_source_checkout() -> None:
    """源码运行时必须能读取 GUI、安装包共用的 Logo 与应用图标。"""

    root = resource_root()

    assert (root / "assets" / "brand" / "datumdock-wordmark-v3.png").is_file()
    assert (root / "assets" / "brand" / "datumdock-app-icon.png").is_file()
    assert (root / "assets" / "brand" / "datumdock-app-icon.ico").is_file()
    assert (root / "installer" / "version_info.txt").is_file()


def test_application_icon_is_multisize_dd_asset() -> None:
    """Windows 图标必须包含常用尺寸，并保留双色 DD 的透明轮廓。"""

    path = application_icon_path()
    with Image.open(path) as icon:
        sizes = set(icon.info.get("sizes", ()))
        assert {(16, 16), (32, 32), (48, 48), (256, 256)} <= sizes
        assert icon.convert("RGBA").getbbox() is not None
