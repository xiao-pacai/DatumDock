<#!
.SYNOPSIS
使用 Python 3.11 构建 DatumDock 的 Windows 分发目录和 Inno Setup 安装包。

.DESCRIPTION
构建前执行格式检查和测试。首次使用须在 Python 3.11 虚拟环境安装 .[dev,inference]。
#>

[CmdletBinding()]
param(
    [string]$PythonPath = "python",
    [switch]$SkipTests,
    [switch]$SkipInstaller
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

& $PythonPath --version
if (-not $SkipTests) {
    & $PythonPath -m ruff check src tests
    & $PythonPath -m ruff format --check src tests
    & $PythonPath -m pytest
}

& $PythonPath -m PyInstaller --noconfirm --clean datumdock.spec
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller 构建失败。"
}

if (-not $SkipInstaller) {
    $InnoCompiler = Get-Command iscc -ErrorAction SilentlyContinue
    if ($null -eq $InnoCompiler) {
        throw "未找到 Inno Setup iscc。请安装 Inno Setup 6 后重新运行，或使用 -SkipInstaller 仅构建分发目录。"
    }
    & $InnoCompiler.Source (Join-Path $ProjectRoot "installer\DatumDock.iss")
    if ($LASTEXITCODE -ne 0) {
        throw "Inno Setup 安装包构建失败。"
    }
}
