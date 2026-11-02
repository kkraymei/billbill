$ErrorActionPreference = 'Stop'
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path

# 依赖 openpyxl / pdfplumber，优先找装了这些包的解释器。
$Candidates = @(
    "$env:USERPROFILE\.conda\envs\anaconda-finance\python.exe",
    "$env:USERPROFILE\.conda\envs\notebook-env\python.exe",
    "$env:USERPROFILE\anaconda3\python.exe",
    'python',
    'py'
)

$PythonExe = $null
foreach ($Candidate in $Candidates) {
    if ($Candidate -like '*\*') {
        if (Test-Path -LiteralPath $Candidate) { $PythonExe = $Candidate; break }
    } else {
        try { $PythonExe = (Get-Command $Candidate -ErrorAction Stop).Source; break } catch {}
    }
}
if (-not $PythonExe) {
    throw '没有找到 Python 3。请安装 Python 3.10+ 后重试，或直接修改 start.ps1 里的路径。'
}

# 缺依赖就地补齐，省得用户自己折腾。
$Probe = & $PythonExe -c "import openpyxl, pdfplumber, xlrd, olefile, PIL.Image, pypdfium2, PyQt6.QtWidgets" 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host '正在安装依赖…'
    & $PythonExe -m pip install --quiet -r (Join-Path $ProjectDir 'requirements.txt')
    if ($LASTEXITCODE -ne 0) { throw '依赖安装失败，请检查上方错误。' }
}

Set-Location -LiteralPath $ProjectDir
Write-Host "使用 Python: $PythonExe"
& $PythonExe (Join-Path $ProjectDir 'app.py')
