# Verifica completa Smartbox v4.1 sui dati delle prove del 10/11/2026 (dopo setup.ps1).
# Uso, dalla cartella principale del repository:
#   powershell -ExecutionPolicy Bypass -File .\verifica.ps1
# Esegue: test della logica, replay di tutte le prove, confronto blendshape/EAR sulla prova 4,
# valutazione degli eventi registrati dal vivo e tabella di sensibilita della soglia EAR.

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$env:PYTHONUTF8 = "1"
$py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { Write-Host "Manca .venv: esegui prima setup.ps1" -ForegroundColor Yellow; exit 1 }

function Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }

Step "1. Test della logica"
& $py -m pytest -q tests

# I dati delle prove non sono nel repository: vanno messi in data\prova1 ... data\provaN (vedi README).
if (-not (Test-Path "data\prova4\indicatori.csv")) {
    Write-Host "`nNessun dato delle prove in data\: eseguiti solo i test della logica." -ForegroundColor Yellow
    exit 0
}

Step "2. Replay di tutte le prove con la logica attuale"
& $py tools\replay.py --tutte

Step "3. Prova 4 con il criterio EAR (atteso: falso microsonno nei socchiusi, circa 13 s)"
& $py tools\replay.py data\prova4 --criterio ear

Step "3b. Taratura per soggetto delle soglie blendshape (D-24) e replay con le soglie tarate"
& $py tools\calibra.py --tutte --reali

Step "3c. Modello C5: score 0-1 con soglie 0,60 / 0,75 (D-27)"
& $py tools\modello.py --tutte

Step "4. Eventi registrati dal vivo nella prova 4 (logica del browser di quel momento)"
& $py tools\evaluate.py data\prova4\eventi.csv data\prova4\annotazioni_3b.csv --tolleranza 3

Step "5. Sensibilita alla soglia EAR, prova 4"
& $py tools\evaluate.py data\prova4\eventi.csv data\prova4\annotazioni_3b.csv --sweep data\prova4\indicatori.csv

Write-Host "`nVerifica terminata." -ForegroundColor Green
