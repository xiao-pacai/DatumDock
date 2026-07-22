# Windows 构建与安装验证

DatumDock 首发仅支持 Windows 10/11 x64。发布包不进行代码签名，因此首次运行可能会显示 Windows SmartScreen 提示；请只从项目的正式 GitHub Release 下载。

> 当前状态：源码运行与开发态 DD 图标已经验证；可发布 PyInstaller/Inno Setup 安装包、无 Python 环境启动、开始菜单/卸载程序图标和隔离机器业务验收均未完成。本文件描述后续发布流程，不能作为已有安装包证据。

## 计划前置条件

- Python 3.11 x64，并在独立虚拟环境中安装 `.[dev,inference]`；
- Inno Setup 6（生成安装包时需要）；
- 后续模型阶段才需要的可选 NVIDIA GPU 与匹配的 PyTorch CUDA 构建；当前正式应用尚未接入模型推理。

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
.\scripts\build.ps1 -PythonPath .\.venv\Scripts\python.exe
```

上述命令是计划中的构建入口；只有在脚本、PyInstaller 和 Inno Setup 均取得实际验证后，才允许声明生成以下交付物：

- `dist\DatumDock\DatumDock.exe`：可携带的 Windows 分发目录；
- `dist-installer\DatumDock-Setup-0.1.0-x64.exe`：Inno Setup 安装包。

未来若只验证 PyInstaller，可保留 `-SkipInstaller`。安装包不得在构建或运行时联网下载模型。

## 隔离环境验收

建议在 Windows Sandbox 或无 Python 的干净虚拟机执行以下检查：

1. 安装并卸载安装包，确认开始菜单项与可选桌面图标正常。
2. 在无 Python 环境启动 DatumDock，从存档式主页创建并打开受管数据集；不得重新出现工作区或项目目录。
3. 导入至少一张 JPG 或 PNG，确认其被复制并转为受管 PNG。
4. 新建标签、绘制矩形、重启应用后确认标注仍存在。
5. 在 YOLO 导出真正接入后，再检查 `images/`、`labels/` 与 `data.yaml`；当前不得执行或勾选。
6. 在数据集备份真正接入后，再验证备份不包含 `.pt`/`.onnx` 二进制；当前不得执行或勾选。

当前仓库的 Python 3.11 依赖安装若被网络或证书阻塞，请先解决该环境问题，再把隔离环境验收标记为通过；不能由开发机的全局 Python 替代。

## English Summary

DatumDock targets Windows 10/11 x64, but the distributable installer is not yet verified. Source-mode DD icons and deterministic YOLO Detection export are complete; PyInstaller/Inno Setup output, clean-machine startup, Start-menu/uninstaller icons, backup, and model inference remain future gates. This document describes the planned validation sequence and must not be cited as evidence of an existing installer.
