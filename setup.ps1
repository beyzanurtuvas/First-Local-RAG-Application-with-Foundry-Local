[CmdletBinding()]
param(
    [string]$Python = $env:LLC_PYTHON,
    [switch]$SkipIndex
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $ProjectRoot

function Resolve-Python {
    param([string]$Explicit)
    if ($Explicit) {
        if (-not (Test-Path -LiteralPath $Explicit -PathType Leaf)) {
            throw "LLC_PYTHON ile verilen Python bulunamadı: $Explicit"
        }
        return (Resolve-Path -LiteralPath $Explicit).Path
    }
    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if ($pythonCommand) { return $pythonCommand.Source }
    $pyCommand = Get-Command py -ErrorAction SilentlyContinue
    if ($pyCommand) {
        & $pyCommand.Source -3 -c "import sys; print(sys.executable)" 2>$null
        if ($LASTEXITCODE -eq 0) {
            return (& $pyCommand.Source -3 -c "import sys; print(sys.executable)").Trim()
        }
    }
    throw "Python 3.11+ bulunamadı. https://www.python.org/downloads/windows/ adresinden kurun veya LLC_PYTHON ortam değişkenini ayarlayın."
}

$ResolvedPython = Resolve-Python -Explicit $Python
$VersionText = & $ResolvedPython -c "import sys; print('.'.join(map(str, sys.version_info[:3]))); raise SystemExit(0 if sys.version_info >= (3,11) else 3)"
if ($LASTEXITCODE -ne 0) { throw "Python 3.11 veya üstü gerekli. Bulunan: $VersionText" }
Write-Host "Python: $VersionText ($ResolvedPython)"

$VenvPython = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $VenvPython)) {
    & $ResolvedPython -m venv (Join-Path $ProjectRoot '.venv')
}
& $VenvPython -m pip install --upgrade pip
& $VenvPython -m pip install -r (Join-Path $ProjectRoot 'requirements.txt')

$SourceDir = if ($env:LLC_SOURCE_DIR) { $env:LLC_SOURCE_DIR } else { 'C:\Users\MSI-CYBORG\Desktop\Building Your First Local RAG Application with Foundry Local' }
$RequiredDocs = @(
    'One-Month Machine Learning Plan.docx',
    'Python Pandas Titanic Project Plan.docx',
    'Quantum Programming Month Plan.docx'
)
foreach ($Name in $RequiredDocs) {
    $DocumentPath = Join-Path $SourceDir $Name
    if (-not (Test-Path -LiteralPath $DocumentPath -PathType Leaf)) {
        throw "Kaynak belge bulunamadı: $DocumentPath"
    }
    Write-Host "Kaynak doğrulandı: $Name"
}

$SetupArgs = @('cli.py', 'setup')
if ($SkipIndex) { $SetupArgs += '--no-index' }
& $VenvPython @SetupArgs
if ($LASTEXITCODE -ne 0) { throw 'Local Learning Coach kurulumu tamamlanamadı.' }

$Foundry = Get-Command foundry -ErrorAction SilentlyContinue
if ($Foundry) {
    & $Foundry.Source --version
    & $Foundry.Source model list --cached
} else {
    Write-Warning 'Foundry Local bulunamadı. Resmi Windows kurulumu: winget install Microsoft.FoundryLocal'
}

Write-Host ''
Write-Host 'Kurulum tamamlandı. Sonraki komutlar:'
Write-Host '  .\.venv\Scripts\python cli.py profile create'
Write-Host '  .\.venv\Scripts\python cli.py plan generate'
Write-Host '  .\.venv\Scripts\streamlit run app.py'
Write-Host '  .\.venv\Scripts\python -m pytest'
