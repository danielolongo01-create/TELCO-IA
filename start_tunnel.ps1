<#
Lance le serveur NOC et un tunnel Cloudflare, extrait automatiquement
l'URL publique generee, et affiche un QR code a scanner avec le telephone
(pas de copier-coller manuel d'URL = pas d'erreur de frappe).

Usage :  .\start_tunnel.ps1
Arret  :  fermez simplement cette fenetre (Ctrl+C), ce qui arrete aussi
          le tunnel et uvicorn.
#>

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

# --- 1. Demarre uvicorn en arriere-plan (dans son propre venv s'il existe) ---
$python = if (Test-Path ".\.venv\Scripts\python.exe") { ".\.venv\Scripts\python.exe" } else { "python" }

Write-Host "Demarrage du serveur NOC (uvicorn)..." -ForegroundColor Cyan
$uvicornProc = Start-Process -FilePath $python `
    -ArgumentList "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", "8000" `
    -PassThru -WindowStyle Minimized

Start-Sleep -Seconds 3
if ($uvicornProc.HasExited) {
    Write-Host "ERREUR : uvicorn s'est arrete immediatement. Lancez-le manuellement pour voir l'erreur :" -ForegroundColor Red
    Write-Host "  $python -m uvicorn main:app --host 127.0.0.1 --port 8000" -ForegroundColor Yellow
    exit 1
}
Write-Host "uvicorn actif (PID $($uvicornProc.Id))." -ForegroundColor Green

# --- 2. Demarre cloudflared et capture sa sortie en direct ---
Write-Host "Ouverture du tunnel Cloudflare..." -ForegroundColor Cyan

$tunnelUrl = $null
$cloudflaredArgs = @("tunnel", "--protocol", "http2", "--url", "http://localhost:8000")

$psi = New-Object System.Diagnostics.ProcessStartInfo
$psi.FileName = "cloudflared"
$psi.Arguments = $cloudflaredArgs -join " "
$psi.RedirectStandardError = $true
$psi.RedirectStandardOutput = $true
$psi.UseShellExecute = $false

$proc = New-Object System.Diagnostics.Process
$proc.StartInfo = $psi
$proc.Start() | Out-Null

# cloudflared ecrit son log sur stderr
while (-not $proc.StandardError.EndOfStream) {
    $line = $proc.StandardError.ReadLine()
    Write-Host $line

    if ($line -match "https://[a-zA-Z0-9\-]+\.trycloudflare\.com") {
        $tunnelUrl = $matches[0]
        break
    }
}

if (-not $tunnelUrl) {
    Write-Host "ERREUR : impossible de recuperer l'URL du tunnel." -ForegroundColor Red
    exit 1
}

$ingestUrl = "$tunnelUrl/api/v1/telemetry/ingest"

Write-Host ""
Write-Host "=======================================================" -ForegroundColor Green
Write-Host " DASHBOARD : $tunnelUrl" -ForegroundColor Green
Write-Host " INGESTION : $ingestUrl" -ForegroundColor Green
Write-Host "=======================================================" -ForegroundColor Green
Write-Host ""

# Garde une trace de la derniere URL (utile si le QR ne marche pas)
$ingestUrl | Out-File -Encoding utf8 ".\last_tunnel_url.txt"

# --- 3. Genere un QR code de l'URL d'ingestion, pour le telephone ---
$qrApiUrl = "https://api.qrserver.com/v1/create-qr-code/?size=400x400&data=" + [uri]::EscapeDataString($ingestUrl)
$qrPath = ".\tunnel_qrcode.png"
try {
    Invoke-WebRequest -Uri $qrApiUrl -OutFile $qrPath -TimeoutSec 15
    Write-Host "QR code genere : $qrPath" -ForegroundColor Cyan
    Write-Host "Scannez-le avec l'appareil photo du telephone, puis choisissez" -ForegroundColor Cyan
    Write-Host "'Copier' (pas 'Ouvrir') pour recuperer l'URL sans la retaper." -ForegroundColor Cyan
    Start-Process $qrPath
} catch {
    Write-Host "Impossible de generer le QR code (pas de reseau ?). Utilisez l'URL texte ci-dessus." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Sur le telephone, lancez ensuite :" -ForegroundColor Magenta
Write-Host "  python probe_termux.py `"$ingestUrl`"" -ForegroundColor White
Write-Host ""
Write-Host "Laissez cette fenetre ouverte. Ctrl+C pour tout arreter." -ForegroundColor DarkGray

# --- 4. Continue a afficher les logs du tunnel jusqu'a l'arret ---
try {
    while (-not $proc.StandardError.EndOfStream) {
        Write-Host $proc.StandardError.ReadLine()
    }
} finally {
    if (-not $proc.HasExited) { $proc.Kill() }
    if (-not $uvicornProc.HasExited) { $uvicornProc.Kill() }
}
