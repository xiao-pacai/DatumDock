"""使用 Win32 屏幕捕获和真实系统光标句柄生成 A0.6 视觉证据。"""

from __future__ import annotations

import ctypes
import hashlib
import json
import sys
from ctypes import wintypes
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from datumdock.ui.preview_canvas import CanvasTool, PreviewAnnotationCanvas
from datumdock.ui.prototype_models import (
    AnnotationItemViewData,
    ImageItemViewData,
    ImageStatus,
    LabelViewData,
)

SRCCOPY = 0x00CC0020
CAPTUREBLT = 0x40000000
DI_NORMAL = 0x0003
DIB_RGB_COLORS = 0
CURSOR_SHOWING = 0x00000001


class POINT(ctypes.Structure):
    """Win32 屏幕坐标。"""

    _fields_ = (("x", wintypes.LONG), ("y", wintypes.LONG))


class RECT(ctypes.Structure):
    """Win32 原生窗口矩形。"""

    _fields_ = (
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG),
    )


class CURSORINFO(ctypes.Structure):
    """Win32 当前系统光标信息。"""

    _fields_ = (
        ("cbSize", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("hCursor", wintypes.HANDLE),
        ("ptScreenPos", POINT),
    )


class ICONINFO(ctypes.Structure):
    """Win32 光标热点及位图句柄。"""

    _fields_ = (
        ("fIcon", wintypes.BOOL),
        ("xHotspot", wintypes.DWORD),
        ("yHotspot", wintypes.DWORD),
        ("hbmMask", wintypes.HBITMAP),
        ("hbmColor", wintypes.HBITMAP),
    )


class BITMAPINFOHEADER(ctypes.Structure):
    """顶向下 32 位屏幕位图描述。"""

    _fields_ = (
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    )


class BITMAPINFO(ctypes.Structure):
    """Win32 位图读取结构。"""

    _fields_ = (("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3))


def _configure_win32() -> tuple[ctypes.WinDLL, ctypes.WinDLL]:
    """声明本脚本使用的 Win32 函数签名，避免 64 位句柄被截断。"""

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    user32.GetDC.restype = wintypes.HDC
    user32.ReleaseDC.argtypes = (wintypes.HWND, wintypes.HDC)
    user32.GetWindowRect.argtypes = (wintypes.HWND, ctypes.POINTER(RECT))
    user32.GetWindowRect.restype = wintypes.BOOL
    user32.GetCursorInfo.argtypes = (ctypes.POINTER(CURSORINFO),)
    user32.GetCursorInfo.restype = wintypes.BOOL
    user32.GetIconInfo.argtypes = (wintypes.HANDLE, ctypes.POINTER(ICONINFO))
    user32.GetIconInfo.restype = wintypes.BOOL
    user32.DrawIconEx.argtypes = (
        wintypes.HDC,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.UINT,
        wintypes.HBRUSH,
        wintypes.UINT,
    )
    user32.DrawIconEx.restype = wintypes.BOOL
    gdi32.CreateCompatibleDC.argtypes = (wintypes.HDC,)
    gdi32.CreateCompatibleDC.restype = wintypes.HDC
    gdi32.CreateCompatibleBitmap.argtypes = (wintypes.HDC, ctypes.c_int, ctypes.c_int)
    gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
    gdi32.SelectObject.argtypes = (wintypes.HDC, wintypes.HGDIOBJ)
    gdi32.SelectObject.restype = wintypes.HGDIOBJ
    gdi32.BitBlt.argtypes = (
        wintypes.HDC,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.HDC,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.DWORD,
    )
    gdi32.BitBlt.restype = wintypes.BOOL
    gdi32.GetDIBits.argtypes = (
        wintypes.HDC,
        wintypes.HBITMAP,
        wintypes.UINT,
        wintypes.UINT,
        wintypes.LPVOID,
        ctypes.POINTER(BITMAPINFO),
        wintypes.UINT,
    )
    gdi32.GetDIBits.restype = ctypes.c_int
    gdi32.DeleteObject.argtypes = (wintypes.HGDIOBJ,)
    gdi32.DeleteDC.argtypes = (wintypes.HDC,)
    return user32, gdi32


def _native_capture_with_cursor(canvas: PreviewAnnotationCanvas, target: Path) -> int:
    """通过 GDI 捕获画布屏幕区域，并绘入当前真实 HCURSOR。"""

    user32, gdi32 = _configure_win32()
    window_rect = RECT()
    if not user32.GetWindowRect(int(canvas.winId()), ctypes.byref(window_rect)):
        raise ctypes.WinError(ctypes.get_last_error())
    left = window_rect.left
    top = window_rect.top
    width = window_rect.right - window_rect.left
    height = window_rect.bottom - window_rect.top
    screen_dc = user32.GetDC(None)
    memory_dc = gdi32.CreateCompatibleDC(screen_dc)
    bitmap = gdi32.CreateCompatibleBitmap(screen_dc, width, height)
    previous = gdi32.SelectObject(memory_dc, bitmap)
    icon = ICONINFO()
    cursor = CURSORINFO(cbSize=ctypes.sizeof(CURSORINFO))
    try:
        if not gdi32.BitBlt(
            memory_dc,
            0,
            0,
            width,
            height,
            screen_dc,
            left,
            top,
            SRCCOPY | CAPTUREBLT,
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        if not user32.GetCursorInfo(ctypes.byref(cursor)):
            raise ctypes.WinError(ctypes.get_last_error())
        if not cursor.flags & CURSOR_SHOWING:
            raise RuntimeError("系统光标当前不可见")
        if not user32.GetIconInfo(cursor.hCursor, ctypes.byref(icon)):
            raise ctypes.WinError(ctypes.get_last_error())
        draw_x = cursor.ptScreenPos.x - left - int(icon.xHotspot)
        draw_y = cursor.ptScreenPos.y - top - int(icon.yHotspot)
        if not user32.DrawIconEx(
            memory_dc,
            draw_x,
            draw_y,
            cursor.hCursor,
            0,
            0,
            0,
            None,
            DI_NORMAL,
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        header = BITMAPINFOHEADER(
            biSize=ctypes.sizeof(BITMAPINFOHEADER),
            biWidth=width,
            biHeight=-height,
            biPlanes=1,
            biBitCount=32,
            biCompression=0,
        )
        bitmap_info = BITMAPINFO(bmiHeader=header)
        buffer = ctypes.create_string_buffer(width * height * 4)
        if not gdi32.GetDIBits(
            memory_dc,
            bitmap,
            0,
            height,
            buffer,
            ctypes.byref(bitmap_info),
            DIB_RGB_COLORS,
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        image = Image.frombuffer("RGB", (width, height), buffer, "raw", "BGRX", 0, 1)
        target.parent.mkdir(parents=True, exist_ok=True)
        image.save(target)
        return int(cursor.hCursor)
    finally:
        if icon.hbmMask:
            gdi32.DeleteObject(icon.hbmMask)
        if icon.hbmColor:
            gdi32.DeleteObject(icon.hbmColor)
        if previous:
            gdi32.SelectObject(memory_dc, previous)
        if bitmap:
            gdi32.DeleteObject(bitmap)
        if memory_dc:
            gdi32.DeleteDC(memory_dc)
        if screen_dc:
            user32.ReleaseDC(None, screen_dc)


def _canvas(locale_name: str) -> PreviewAnnotationCanvas:
    """构造包含一张演示图和一个选中矩形的共享画布。"""

    alias = "金属零件" if locale_name == "zh_CN" else "Metal part"
    description = "系统指针视觉复验" if locale_name == "zh_CN" else "System cursor review"
    label = LabelViewData(
        "label-1",
        0,
        "metal_part",
        alias,
        description,
        (),
        "#5B83E6",
        1,
    )
    annotation = AnnotationItemViewData(
        "shape-1",
        label.id,
        80.0,
        45.0,
        240.0,
        135.0,
    )
    canvas = PreviewAnnotationCanvas()
    canvas.resize(900, 620)
    canvas.load_preview(
        ImageItemViewData(
            "image-1",
            "sample.png",
            ImageStatus.COMPLETED,
            320,
            180,
            1,
            1,
        ),
        (label,),
        (annotation,),
    )
    canvas.show()
    return canvas


def _capture_locale(
    application: QApplication,
    output_root: Path,
    locale_name: str,
) -> int:
    """为一种界面语言捕获移动、缩放、画框和闭合手形指针。"""

    canvas = _canvas(locale_name)
    canvas.raise_()
    canvas.activateWindow()
    canvas.setFocus()
    QTest.qWait(300)
    annotation = canvas.annotations[0]
    rect = canvas._annotation_rect(annotation)
    positions = {
        "move": rect.center(),
        "horizontal": canvas._handle_points(rect)["right"],
        "vertical": canvas._handle_points(rect)["top"],
        "forward-diagonal": canvas._handle_points(rect)["top_left"],
        "backward-diagonal": canvas._handle_points(rect)["top_right"],
    }
    manifest: list[dict[str, object]] = []

    def save(name: str, point, expected: Qt.CursorShape) -> None:
        QTest.mouseMove(canvas, QPoint(8, 8))
        QTest.mouseMove(canvas, point.toPoint())
        canvas._refresh_cursor(point)
        QCursor.setPos(canvas.mapToGlobal(point.toPoint()))
        QTest.qWait(80)
        if canvas.cursor().shape() != expected:
            raise RuntimeError(f"系统指针类型错误: {name}")
        target = output_root / f"cursor-{name}.png"
        handle = _native_capture_with_cursor(canvas, target)
        manifest.append(
            {
                "name": name,
                "qt_cursor": expected.name,
                "hcursor": handle,
                "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
            }
        )

    save("move", positions["move"], Qt.CursorShape.SizeAllCursor)
    save("horizontal", positions["horizontal"], Qt.CursorShape.SizeHorCursor)
    save("vertical", positions["vertical"], Qt.CursorShape.SizeVerCursor)
    save("forward-diagonal", positions["forward-diagonal"], Qt.CursorShape.SizeFDiagCursor)
    save("backward-diagonal", positions["backward-diagonal"], Qt.CursorShape.SizeBDiagCursor)

    canvas.set_tool(CanvasTool.RECTANGLE)
    save("rectangle", canvas._image_rect().center(), Qt.CursorShape.CrossCursor)

    canvas.set_tool(CanvasTool.SELECT)
    center = canvas._image_rect().center().toPoint()
    QTest.mousePress(canvas, Qt.MouseButton.MiddleButton, pos=center)
    QCursor.setPos(canvas.mapToGlobal(center))
    application.processEvents()
    if canvas.cursor().shape() != Qt.CursorShape.ClosedHandCursor:
        raise RuntimeError("中键平移没有显示闭合手形指针")
    target = output_root / "cursor-closed-hand.png"
    handle = _native_capture_with_cursor(canvas, target)
    manifest.append(
        {
            "name": "closed-hand",
            "qt_cursor": Qt.CursorShape.ClosedHandCursor.name,
            "hcursor": handle,
            "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        }
    )
    QTest.mouseRelease(canvas, Qt.MouseButton.MiddleButton, pos=center)
    (output_root / "cursor-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    canvas.close()
    return len(manifest)


def capture(output_root: Path) -> int:
    """生成中英文两套真实 Win32 系统光标证据。"""

    application = QApplication.instance() or QApplication(["datumdock-pointer-review"])
    return sum(
        _capture_locale(application, output_root / locale_name, locale_name)
        for locale_name in ("zh_CN", "en_US")
    )


def main() -> int:
    """仅在 Windows 上执行原生系统光标捕获。"""

    if sys.platform != "win32":
        raise RuntimeError("系统光标视觉复验只支持 Windows")
    output_root = Path("build/ui-review/a0.5-a0.7/native-cursors").resolve()
    count = capture(output_root)
    print(f"已生成 {count} 张 Win32 系统光标截图: {output_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
