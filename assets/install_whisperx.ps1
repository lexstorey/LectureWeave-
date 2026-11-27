$ErrorActionPreference = "Stop"
$envRoot = Join-Path $env:LOCALAPPDATA "LectureWeavePlus\whisperx-env"
$envPython = Join-Path $envRoot "Scripts\python.exe"

Write-Host "LectureWeave+ WhisperX setup" -ForegroundColor Magenta
Write-Host "This downloads Python packages from PyPI and PyTorch's official package server."

if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
    Write-Host "Installing Python 3.12 with Windows Package Manager..." -ForegroundColor Cyan
    winget install --exact --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements
}

Write-Host "Ensuring Python 3.12 is installed..." -ForegroundColor Cyan
& py install 3.12
if ($LASTEXITCODE -ne 0) {
    & py -3.12 -c "import sys; print(sys.executable)"
    if ($LASTEXITCODE -ne 0) { throw "Python 3.12 could not be installed or located." }
}

if (-not (Test-Path -LiteralPath $envPython)) {
    Write-Host "Creating LectureWeave+'s private Python environment..." -ForegroundColor Cyan
    & py -3.12 -m venv $envRoot
    if ($LASTEXITCODE -ne 0) { throw "Could not create the WhisperX environment." }
}

Write-Host "Installing or repairing WhisperX..." -ForegroundColor Cyan
& $envPython -m pip install --upgrade pip
& $envPython -m pip install --upgrade whisperx

Write-Host "Installing the CUDA 12.8 PyTorch build..." -ForegroundColor Cyan
& $envPython -m pip install --upgrade --force-reinstall torch==2.8.0 torchvision==0.23.0 torchaudio==2.8.0 --index-url https://download.pytorch.org/whl/cu128

Write-Host "Verifying WhisperX and GPU access..." -ForegroundColor Cyan
& $envPython -c "import torch, whisperx; print('WhisperX ready'); print('CUDA:', torch.cuda.is_available()); print('GPU:', torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'not available')"
if ($LASTEXITCODE -ne 0) { throw "WhisperX verification failed." }

Write-Host "Setup complete. Return to LectureWeave+ and click Check setup." -ForegroundColor Green
Read-Host "Press Enter to close"
