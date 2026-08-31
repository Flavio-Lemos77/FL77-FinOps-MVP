# Controle Financeiro Pessoal — inicialização nativa no Windows
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$api = Join-Path $root "api"
$venv = Join-Path $root ".venv"

 $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
if (-not $pythonCommand) {
    throw "Python 3 não foi encontrado. Instale em https://www.python.org/downloads/windows/ e execute este arquivo novamente."
}
$python = $pythonCommand.Source

& $python --version
if ($LASTEXITCODE -ne 0) {
    throw "Python 3 não foi encontrado. Instale em https://www.python.org/downloads/windows/ e execute este arquivo novamente."
}

if (-not (Test-Path $venv)) {
    & $python -m venv $venv
}

& "$venv\Scripts\python.exe" -m pip install --upgrade pip
& "$venv\Scripts\python.exe" -m pip install -r "$api\requirements-windows.txt"

Set-Location $api
$env:DATABASE_URL = "sqlite:///./financeiro.db"
Write-Host "Painel: http://localhost:8000" -ForegroundColor Green
Write-Host "API:    http://localhost:8000/docs" -ForegroundColor Green
& "$venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8000
