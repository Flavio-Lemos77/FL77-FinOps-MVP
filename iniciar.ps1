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
$env:ENV = "development"

# Banco SQLite com caminho absoluto em formato URI correto (barras normais)
$DbPath = ($ApiDir -replace '\\', '/') + "/financeiro.db"
$env:DATABASE_URL = "sqlite:///$DbPath"

# Chave de sessão persistente para desenvolvimento:
# gerada uma única vez e salva num arquivo local — assim o login não some
# a cada vez que você reinicia o servidor.
$SecretFile = Join-Path $Root ".dev_secret"
if (-not (Test-Path $SecretFile)) {
    $NewSecret = -join ((65..90) + (97..122) + (48..57) |
        Get-Random -Count 48 | ForEach-Object { [char]$_ })
    Set-Content -Path $SecretFile -Value $NewSecret -NoNewline
    Write-Host "Chave de sessão de desenvolvimento criada em .dev_secret" -ForegroundColor Yellow
}
$env:APP_SESSION_SECRET = Get-Content -Path $SecretFile -Raw

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
