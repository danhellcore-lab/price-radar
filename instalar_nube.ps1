# Deja Price Radar funcionando en la nube. Se ejecuta una sola vez.
#
# Antes de correrlo hacen falta dos cosas que solo puede hacer una persona:
#   1) haber iniciado sesion:  gh auth login
#   2) tener la cadena de conexion de Neon (https://neon.com)
#
# Uso:
#   .\instalar_nube.ps1 -NeonUrl "postgresql://..." -TelegramToken "..." -TelegramChat "..."

param(
    [string]$NeonUrl = "",
    [string]$TelegramToken = "",
    [string]$TelegramChat = "",
    [string]$Repo = "price-radar",
    [string]$Categorias = "notebook,celular,televisor,zapatillas"
)

$ErrorActionPreference = "Stop"

# La cadena de conexion lleva contraseña. Se lee de un archivo local que nunca
# se sube al repositorio y que se borra al terminar, para no tener que
# escribirla en pantalla ni dejarla en el historial de comandos.
$archivoNeon = Join-Path $PSScriptRoot "neon.txt"
if (-not $NeonUrl -and (Test-Path $archivoNeon)) {
    $NeonUrl = (Get-Content $archivoNeon -Raw).Trim()
}
if (-not $NeonUrl) {
    throw "Falta la conexion de Neon. Pegala en el archivo neon.txt dentro de esta carpeta."
}

$archivoTelegram = Join-Path $PSScriptRoot "telegram.txt"
if (-not $TelegramToken -and (Test-Path $archivoTelegram)) {
    $lineas = Get-Content $archivoTelegram | Where-Object { $_.Trim() }
    if ($lineas.Count -ge 1) { $TelegramToken = $lineas[0].Trim() }
    if ($lineas.Count -ge 2) { $TelegramChat = $lineas[1].Trim() }
}
$env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
            [System.Environment]::GetEnvironmentVariable("Path", "User")

function Paso($n, $texto) { Write-Host "`n[$n] $texto" -ForegroundColor Cyan }

Paso 1 "Comprobando la sesion de GitHub..."
gh auth status | Out-Null
if ($LASTEXITCODE -ne 0) { throw "No hay sesion de GitHub. Ejecuta primero:  gh auth login" }
$usuario = (gh api user --jq .login)
Write-Host "    Conectado como: $usuario"

Paso 2 "Creando el repositorio y subiendo el codigo..."
$existe = gh repo view "$usuario/$Repo" 2>$null
if ($LASTEXITCODE -eq 0) {
    Write-Host "    Ya existia; se reutiliza."
    git remote remove origin 2>$null | Out-Null
    git remote add origin "https://github.com/$usuario/$Repo.git"
    git push -u origin main --force
} else {
    # Publico: es lo que hace gratis a Actions y Pages.
    gh repo create $Repo --public --source=. --remote=origin --push
}

Paso 3 "Guardando los secretos (cifrados, no se ven en los registros)..."
$NeonUrl | gh secret set DATABASE_URL --repo "$usuario/$Repo"
if ($TelegramToken) { $TelegramToken | gh secret set TELEGRAM_BOT_TOKEN --repo "$usuario/$Repo" }
if ($TelegramChat) { $TelegramChat | gh secret set TELEGRAM_CHAT_ID --repo "$usuario/$Repo" }
gh variable set CATEGORIES --repo "$usuario/$Repo" --body $Categorias

Paso 4 "Activando la web publica (GitHub Pages)..."
try {
    gh api "repos/$usuario/$Repo/pages" -X POST -f "build_type=workflow" | Out-Null
    Write-Host "    Pages activado."
} catch {
    Write-Host "    Pages ya estaba activado."
}

Paso 5 "Lanzando la primera busqueda..."
gh workflow run scan.yml --repo "$usuario/$Repo"
Start-Sleep -Seconds 12
gh run list --repo "$usuario/$Repo" --limit 1

Write-Host "`n=================================================" -ForegroundColor Green
Write-Host " LISTO. A partir de ahora corre solo cada 30 min." -ForegroundColor Green
Write-Host "=================================================" -ForegroundColor Green
Remove-Item $archivoNeon, $archivoTelegram -ErrorAction SilentlyContinue
Write-Host " (Se borraron neon.txt y telegram.txt: ya estan guardados en GitHub.)"
Write-Host ""
Write-Host " Tu web:        https://$usuario.github.io/$Repo/"
Write-Host " Seguimiento:   https://github.com/$usuario/$Repo/actions"
Write-Host ""
Write-Host " La primera ejecucion tarda ~3 min (arma el indice de Ripley)."
Write-Host " Luego abre PriceRadar.exe > Ajustes y pega:"
Write-Host "   - Conexion Neon:        la misma que usaste aqui"
Write-Host "   - URL del informe web:  https://$usuario.github.io/$Repo/"
