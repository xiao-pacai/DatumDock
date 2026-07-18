# Windows 构建与安装验证

DatumDock 首发仅支持 Windows 10/11 x64。发布包不进行代码签名，因此首次运行可能会显示 Windows SmartScreen 提示；请只从项目的正式 GitHub Release 下载。

## 前置条件

- Python 3.11 x64，并在独立虚拟环境中安装 `.[dev,inference]`；
- Inno Setup 6（生成安装包时需要）；
- 可选：NVIDIA GPU 与相匹配的 PyTorch CUDA 构建。缺少 GPU 时应用仍可使用 CPU 推理。

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev,inference]"
.\scripts\build.ps1 -PythonPath .\.venv\Scripts\python.exe
```

该命令会先运行 Ruff、格式检查和 pytest，再生成：

- `dist\DatumDock\DatumDock.exe`：可携带的 Windows 分发目录；
- `dist-installer\DatumDock-Setup-0.1.0-x64.exe`：Inno Setup 安装包。

若只验证 PyInstaller，可使用 `-SkipInstaller`。构建脚本不会联网下载模型；模型由用户在应用内单独导入。

## 隔离环境验收

建议在 Windows Sandbox 或无 Python 的干净虚拟机执行以下检查：

1. 安装并卸载安装包，确认开始菜单项与可选桌面图标正常。
2. 在无 Python 环境启动 DatumDock，创建工作区、项目和数据集。
3. 导入至少一张 JPG 或 PNG，确认其被复制并转为受管 PNG。
4. 新建标签、绘制矩形、重启应用后确认标注仍存在。
5. 导出 YOLO Detection 数据集，检查 `images/`、`labels/` 与 `data.yaml`。
6. 验证项目备份不包含 `.pt`/`.onnx` 二进制，恢复后模型显示需要重新导入。

当前仓库的 Python 3.11 依赖安装若被网络或证书阻塞，请先解决该环境问题，再把隔离环境验收标记为通过；不能由开发机的全局 Python 替代。

## English Summary

DatumDock targets Windows 10/11 x64. Build it in an isolated Python 3.11 environment with `.[dev,inference]`, then run `scripts\build.ps1`. The script validates Ruff, formatting and pytest before producing a PyInstaller folder and, when Inno Setup 6 is available, a Windows installer. Validate installation in a clean environment without Python: launch, import, annotate, export YOLO, and verify model binaries stay out of project backups. The package is unsigned, so SmartScreen notices are expected for early releases.
