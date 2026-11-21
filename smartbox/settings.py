"""Soglie modificabili dalla pagina del dispositivo (registro D-41): gli stessi campi della demo nel browser.

Dove si salvano (nella cartella dati, fuori dal repository, con l'ordine di smartbox/config.py):
  - soglie "occhio chiuso" (palpebra, margine, EAR) in taratura.yaml, perché sono quelle che la taratura misura;
    un valore scritto a mano resta finché non si rifà la taratura;
  - tutte le altre in locale.yaml.
"Valori di partenza" toglie le modifiche fatte a mano da locale.yaml; "Rimuovi taratura" cancella taratura.yaml.
"""
import os
from datetime import datetime
import yaml
from .config import paths, read_yaml

FIELDS = [   # (sezione, chiave, etichetta, passo, minimo, massimo)
    ("indicators", "blink_closed_threshold", "Palpebra chiusa (blendshape)", 0.01, 0.30, 0.95),
    ("indicators", "blink_squint_margin", "Margine palpebra − occhi stretti", 0.01, -0.30, 0.60),
    ("indicators", "ear_closed_threshold", "Soglia EAR occhio chiuso", 0.01, 0.05, 0.35),
    ("indicators", "mar_yawn_threshold", "Soglia MAR sbadiglio", 0.05, 0.20, 1.20),
    ("indicators", "pitch_nod_threshold", "Soglia chinamento (gradi)", 1, 5, 45),
    ("alarm", "perclos_warning", "PERCLOS attenzione", 0.01, 0.05, 0.60),
    ("alarm", "perclos_danger", "PERCLOS pericolo", 0.01, 0.10, 0.80),
    ("alarm", "microsleep_seconds", "Microsonno (s)", 0.1, 0.5, 5.0),
    ("alarm", "yawns_per_window_warning", "Sbadigli per attenzione", 1, 1, 10),
    ("alarm", "hysteresis_seconds", "Isteresi (s)", 1, 0, 30),
]
CALIB_KEYS = {"blink_closed_threshold", "blink_squint_margin", "ear_closed_threshold"}


def current(cfg):
    return [{"sezione": s, "chiave": k, "etichetta": l, "passo": st, "min": lo, "max": hi, "valore": cfg[s].get(k)}
            for s, k, l, st, lo, hi in FIELDS] + [
        {"sezione": "alarm", "chiave": "gradual_descent", "etichetta": "Discesa graduale 2→1→0", "tipo": "bool",
         "valore": bool(cfg["alarm"].get("gradual_descent"))}]


def _write(path, data, header):
    with open(path, "w", encoding="utf-8") as f:
        f.write(header)
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)


def save(data_dir, values):
    """values: {"sezione.chiave": valore}. Restituisce (ok, messaggio). Valori fuori intervallo: rifiutati."""
    p = paths(data_dir)
    limits = {f"{s}.{k}": (lo, hi, st) for s, k, _, st, lo, hi in FIELDS}
    loc, cal = read_yaml(p["locale"]), read_yaml(p["taratura"])
    changed = []
    for key, v in values.items():
        if key == "alarm.gradual_descent":
            loc.setdefault("alarm", {})["gradual_descent"] = bool(v)
            changed.append(key); continue
        if key not in limits:
            return False, f"campo sconosciuto: {key}"
        lo, hi, st = limits[key]
        try:
            v = float(v)
        except (TypeError, ValueError):
            return False, f"valore non numerico per {key}"
        if not lo <= v <= hi:
            return False, f"{key}: {v} fuori dall'intervallo {lo} – {hi}"
        v = int(v) if float(st).is_integer() else round(v, 3)
        s, k = key.split(".")
        if k == "pitch_nod_threshold" and k in (cal.get("indicators") or {}):
            cal["indicators"][k] = v          # misurata con «Calibra capo» (D-45): la modifica a mano va lì, o non conterebbe
        elif k in CALIB_KEYS:
            cal.setdefault("indicators", {})[k] = v
            if k != "ear_closed_threshold":
                cal["indicators"]["eye_closure_source"] = "blendshape"
            cal["data"] = cal.get("data") or datetime.now().astimezone().isoformat(timespec="seconds")
            cal["modificata_a_mano"] = datetime.now().astimezone().isoformat(timespec="seconds")
        else:
            loc.setdefault(s, {})[k] = v
        changed.append(key)
    os.makedirs(data_dir, exist_ok=True)
    _write(p["locale"], loc, "# Impostazioni di questo dispositivo (D-36 … D-41). Non sono nel repository.\n")
    if cal:
        _write(p["taratura"], cal, "# Taratura per soggetto (D-24, D-36); può contenere modifiche a mano dalla pagina (D-41).\n")
    return True, f"Salvate {len(changed)} impostazioni."


def save_rotation(data_dir, degrees):
    """Rotazione dell'immagine (D-44): intero tra −180 e 180, in locale.yaml. Non tocca soglie né taratura."""
    try:
        deg = int(round(float(degrees)))
    except (TypeError, ValueError):
        return False, "rotazione non numerica", None
    deg = (deg + 179) % 360 - 179                 # sempre tra −179 e 180
    p = paths(data_dir)
    loc = read_yaml(p["locale"])
    loc.setdefault("video", {})["rotation"] = deg
    os.makedirs(data_dir, exist_ok=True)
    _write(p["locale"], loc, "# Impostazioni di questo dispositivo (D-36 … D-44). Non sono nel repository.\n")
    return True, f"Rotazione {deg:+d}°: rifai la taratura quando l'immagine è dritta.", deg


def reset(data_dir):
    p = paths(data_dir)
    loc = read_yaml(p["locale"])
    for s in ("indicators", "alarm"):
        loc.pop(s, None)
    _write(p["locale"], loc, "# Impostazioni di questo dispositivo (D-36 … D-41). Non sono nel repository.\n")
    return True, "Tornati i valori di partenza (la taratura del guidatore resta)."


def remove_calibration(data_dir):
    try:
        os.remove(paths(data_dir)["taratura"])
        return True, "Taratura rimossa: si usano le soglie standard."
    except FileNotFoundError:
        return True, "Non c'era nessuna taratura."
