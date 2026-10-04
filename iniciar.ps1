<#
.SYNOPSIS
    Inicia o FL77 FinOps em modo DESENVOLVIMENTO local (sem Docker).
    Para produção, use: docker compose up --build -d
#>

$ErrorActionPreference = "Stop"
$Root    = $PSScriptRoot
$ApiDir  = Join-Path $Root "api"
$VenvDir = Join-Path $Root ".venv"
$Python  = $null

# ── Localizar Python 3.12+ ────────────────────────────────────────────────
foreach ($candidate in @("py", "python3", "python")) {
    try {
        $ver = & $candidate --version 2>&1
        if ($ver -match "Python 3\.(1[2-9]|[2-9]\d)") {
            $Python = $candidate
            break
        }
    } catch { }
}

if (-not $Python) {
    Write-Error "Python 3.12+ não encontrado no PATH. Instale em https://www.python.org/downloads/"
    exit 1
}
Write-Host "Usando: $Python ($(& $Python --version))" -ForegroundColor Cyan

# ── Criar/atualizar virtualenv ────────────────────────────────────────────
if (-not (Test-Path $VenvDir)) {
    Write-Host "Criando virtualenv em $VenvDir ..." -ForegroundColor Yellow
    & $Python -m venv $VenvDir
}

$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
$Req = Join-Path $ApiDir "requirements.txt"

Write-Host "Instalando/atualizando dependências..." -ForegroundColor Yellow
& $VenvPython -m pip install --quiet --upgrade pip
& $VenvPython -m pip install --quiet -r $Req

# ── Variáveis de ambiente para desenvolvimento ────────────────────────────
$env:ENV              = "development"
$env:DATABASE_URL     = "sqlite:///$ApiDir/financeiro.db"
# Em desenvolvimento a chave é gerada automaticamente a cada início —
# se quiser sessões persistentes entre reinicios, defina aqui:
# $env:APP_SESSION_SECRET = "minha-chave-de-dev"

# ── Iniciar Uvicorn ───────────────────────────────────────────────────────
Write-Host ""
Write-Host "==================================================" -ForegroundColor Green
Write-Host "  FL77 FinOps rodando em http://localhost:8000"    -ForegroundColor Green
Write-Host "  Pressione Ctrl+C para parar."                    -ForegroundColor Green
Write-Host "==================================================" -ForegroundColor Green
Write-Host ""

Push-Location $ApiDir
try {
    & $VenvPython -m uvicorn app.main:app `
        --host 127.0.0.1 `
        --port 8000 `
        --reload
} finally {
    Pop-Location
}
