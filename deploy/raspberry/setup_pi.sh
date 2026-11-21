#!/usr/bin/env bash
# Prepara l'ambiente Python di Smartbox sul Raspberry Pi 5 (registro D-29). L'equivalente di setup.ps1.
# Uso, dalla cartella principale del repository:   bash deploy/raspberry/setup_pi.sh
#
# Raspberry Pi OS su Debian 13 ha Python 3.13, ma mediapipe 0.10.14 per ARM esiste solo fino alla 3.12:
# si usa un Python 3.12 separato installato con uv, senza toccare quello di sistema.
set -euo pipefail
cd "$(dirname "$0")/../.."

missing=0
command -v git >/dev/null || { echo "Manca git"; missing=1; }
# si controllano le librerie, non i nomi dei pacchetti (su Debian 13 libglib2.0-0 si chiama libglib2.0-0t64)
for lib in libGL.so.1 libglib-2.0.so.0; do
  /sbin/ldconfig -p | grep -q "$lib" || { echo "Manca la libreria $lib"; missing=1; }
done
if [ "$missing" = 1 ]; then
  echo "Installalo con:  sudo apt update && sudo apt install -y git libgl1 libglib2.0-0t64"
  exit 1
fi

UV="$(command -v uv || echo "$HOME/.local/bin/uv")"
if [ ! -x "$UV" ]; then
  echo "Manca uv. Installalo con:  curl -LsSf https://astral.sh/uv/install.sh | sh"
  exit 1
fi

echo "==> Python 3.12"
"$UV" python install 3.12

echo "==> Ambiente .venv"
[ -x .venv/bin/python ] || "$UV" venv --python 3.12 .venv
# Su Raspberry: opencv-python-headless al posto di opencv-python (nessuna finestra grafica)
"$UV" pip install --python .venv/bin/python "mediapipe==0.10.14" opencv-python-headless numpy pyyaml \
  "paho-mqtt>=2.0" requests pytest

echo "==> Test della logica"
.venv/bin/python -m pytest -q tests

echo "==> Telecamera"
ls /dev/video0 >/dev/null 2>&1 && echo "webcam USB su /dev/video0" || echo "ATTENZIONE: nessuna webcam su /dev/video0"

echo
echo "Fatto. Per l'avvio automatico con allarme a schermo vedi deploy/raspberry/LEGGIMI.md"
