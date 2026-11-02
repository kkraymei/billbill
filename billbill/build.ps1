# 打包成单文件 exe：产出 dist\billbill.exe
#
#   .\build.ps1              正常构建
#   .\build.ps1 -Clean       清掉缓存重新构建（改了依赖后用）

param([switch]$Clean)

$ErrorActionPreference = 'Stop'
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path

# 必须用装了 openpyxl / pdfplumber 的那个解释器，否则打出来的 exe 缺依赖。
$Candidates = @(
    "$env:USERPROFILE\.conda\envs\anaconda-finance\python.exe",
    "$env:USERPROFILE\.conda\envs\notebook-env\python.exe",
    'python'
)
$PythonExe = $null
foreach ($Candidate in $Candidates) {
    if ($Candidate -like '*\*') {
        if (Test-Path -LiteralPath $Candidate) { $PythonExe = $Candidate; break }
    } else {
        try { $PythonExe = (Get-Command $Candidate -ErrorAction Stop).Source; break } catch {}
    }
}
if (-not $PythonExe) { throw '找不到 Python 3。' }

Write-Host "使用 Python: $PythonExe"

& $PythonExe -c "import openpyxl, pdfplumber, xlrd, olefile, PIL.Image, pypdfium2, PyQt6.QtWidgets" 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host '正在补齐运行依赖…'
    & $PythonExe -m pip install --quiet -r (Join-Path $ProjectDir 'requirements.txt')
    if ($LASTEXITCODE -ne 0) { throw '依赖安装失败。' }
}

& $PythonExe -m PyInstaller --version 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host '正在安装 PyInstaller…'
    & $PythonExe -m pip install --quiet pyinstaller
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller 安装失败。' }
}

# conda 环境的 ffi 等 DLL 在 Library\bin 下，不加进 PATH 会打包失败。
$EnvRoot = Split-Path -Parent $PythonExe
$env:PATH = "$EnvRoot\Library\bin;$env:PATH"

Set-Location -LiteralPath $ProjectDir
$Args = @('-m', 'PyInstaller', 'billbill.spec', '--noconfirm')
if ($Clean) { $Args += '--clean' }
& $PythonExe @Args
if ($LASTEXITCODE -ne 0) { throw '打包失败，见上面的输出。' }

$Exe = Join-Path $ProjectDir 'dist\billbill.exe'
$SizeMB = [math]::Round((Get-Item $Exe).Length / 1MB, 1)
Write-Host ""
Write-Host "构建完成：$Exe（$SizeMB MB）" -ForegroundColor Green
