# Preparazione dell'ambiente Smartbox v4.1 su Windows (una volta sola).
# Uso, dalla cartella principale del repository:
#   powershell -ExecutionPolicy Bypass -File .\setup.ps1
# Fa: trova Python 3.12, crea .venv, installa le librerie, scarica il modello MediaPipe,
# prepara la cartella data\ ed esegue i test.

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$env:PYTHONUTF8 = "1"

function Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }

# 1. Python 3.12 (mediapipe 0.10.14 non ha pacchetti per Python 3.13 o successivi)
Step "Cerco Python 3.12"
$py = $null
if (Get-Command py -ErrorAction SilentlyContinue) {
    try {
        $v = & cmd /c "py -3.12 --version 2>nul"
        if ($LASTEXITCODE -eq 0 -and "$v" -match "3\.12") { $py = @("py", "-3.12") }
    } catch { }
}
if (-not $py) {
    $cand = Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"
    if (Test-Path $cand) { $py = @($cand) }
}
if (-not $py) {
    Write-Host "Python 3.12 non trovato. Installalo con:" -ForegroundColor Yellow
    Write-Host "   winget install -e --id Python.Python.3.12" -ForegroundColor Yellow
    Write-Host "poi chiudi e riapri il terminale e rilancia questo script." -ForegroundColor Yellow
    exit 1
}
$pyExe = $py[0]; $pyArgs = @($py | Select-Object -Skip 1)
& $pyExe @pyArgs --version

# 2. Ambiente virtuale
Step "Ambiente virtuale .venv"
if (-not (Test-Path ".venv\Scripts\python.exe")) {
    & $pyExe @pyArgs -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw "creazione di .venv non riuscita" }
}
$venvPy = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
& $venvPy --version

# 3. Librerie
Step "Installo le librerie del dispositivo, qualche minuto la prima volta"
& $venvPy -m pip install --upgrade pip
& $venvPy -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw "installazione delle librerie non riuscita" }

# 4. Modello MediaPipe con le blendshape (circa 3,7 MB)
Step "Modello face_landmarker.task"
$model = "smartbox\face_landmarker.task"
if (-not (Test-Path $model)) {
    $url = "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task"
    Invoke-WebRequest -Uri $url -OutFile $model -UseBasicParsing
}
"{0}: {1:N0} byte" -f $model, (Get-Item $model).Length

# 5. Cartella dei dati locali (registro eventi, coda, prove; esclusa da git)
Step "Cartella data"
New-Item -ItemType Directory -Force data | Out-Null

# 6. Test della logica
Step "Test della logica (pytest)"
& $venvPy -m pytest -q tests
if ($LASTEXITCODE -ne 0) { throw "alcuni test non sono passati" }

Write-Host "`nAmbiente pronto. Prossimo passo:  powershell -ExecutionPolicy Bypass -File .\verifica.ps1" -ForegroundColor Green
