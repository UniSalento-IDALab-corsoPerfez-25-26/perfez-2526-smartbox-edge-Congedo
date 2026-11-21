"""Genera config.js del repository dashboard da config.yaml: la demo nel browser usa gli stessi parametri
del dispositivo (D-48).

Uso (dalla cartella principale del repository):  python tools/genera_config_js.py
Il repository dashboard va clonato accanto a questo (stessa cartella superiore); in alternativa si indica
la sua cartella con la variabile d'ambiente SMARTBOX_DASHBOARD_DIR.
Va rilanciato dopo ogni modifica di config.yaml; il test test_config_js_allineato lo controlla quando
il repository dashboard è presente.
Si copiano solo le sezioni che la demo usa; nessun segreto né impostazione del dispositivo.
"""
import json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
sys.path.insert(0, PROJECT)

import yaml
from smartbox.config import check

DASHBOARD_DIR = os.environ.get("SMARTBOX_DASHBOARD_DIR",
                               os.path.join(os.path.dirname(PROJECT), "perfez-2526-smartbox-dashboard-Congedo"))
OUT = os.path.join(DASHBOARD_DIR, "config.js")
SECTIONS = ("detector", "indicators", "alarm", "calibration", "evaluation", "demo")


def render(cfg):
    data = {"video": {"face_lost_seconds": cfg["video"]["face_lost_seconds"]}}
    data.update({s: cfg[s] for s in SECTIONS})
    return ("// GENERATO da tools/genera_config_js.py a partire da config.yaml: non modificare a mano (D-48).\n"
            "export const DEFAULT_CONFIG = " + json.dumps(data, ensure_ascii=False, indent=2) + ";\n")


def main():
    path = os.path.join(PROJECT, "config.yaml")
    cfg = check(yaml.safe_load(open(path, encoding="utf-8")), path)
    if not os.path.isdir(DASHBOARD_DIR):
        sys.exit(f"repository dashboard non trovato in {DASHBOARD_DIR}: clonarlo accanto a questo "
                 "o impostare SMARTBOX_DASHBOARD_DIR")
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(render(cfg))
    print(f"scritto {OUT}")


if __name__ == "__main__":
    main()
