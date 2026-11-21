"""Configurazione della Smartbox: un'unica lettura per tutti i programmi (registro D-38).

Ordine di lettura (ogni livello sovrascrive il precedente):
  1. config.yaml del repository: valori di progetto;
  2. <cartella dati>/locale.yaml: impostazioni di quel dispositivo (es. invio al server, GPS);
  3. <cartella dati>/taratura.yaml: soglie "occhio chiuso" del soggetto tarato (D-24, D-36);
  4. segreti (token, password): <cartella dati>/secrets.yaml se esiste, altrimenti il file indicato.
La cartella dati (sul Raspberry ~/smartbox-dati, sul PC data/) contiene anche registro eventi, coda e posizioni:
nulla di tutto questo entra nel repository.

config.yaml è l'unica fonte dei parametri della logica, dello score, della taratura e del rilevatore (D-48): il codice
non ha valori di riserva. Se manca un parametro, check() lo dice subito invece di usare un numero nascosto.
"""
import os
from datetime import datetime
import yaml

SECRET_SECTIONS = {"mqtt": ("forward", "mqtt"), "http": ("forward", "http"), "gps": ("gps",),
                   "phone": ("outputs", "phone"), "pagina": ("pagina",)}


REQUIRED = {   # parametri letti dai moduli della logica: devono esserci tutti dopo l'unione dei file
    "video": ["face_lost_seconds", "rotation"],
    "detector": ["min_detection_confidence", "min_tracking_confidence"],
    "indicators": ["ear_closed_threshold", "mar_yawn_threshold", "eye_closure_source", "blink_closed_threshold",
                   "blink_squint_margin", "pitch_nod_threshold", "pitch_baseline", "window_seconds"],
    "alarm": ["perclos_warning", "perclos_danger", "perclos_min_seconds", "microsleep_seconds",
              "yawns_per_window_warning", "yawn_min_seconds", "yawn_opening_fraction", "yawn_masks_eye_closure",
              "yawn_eye_grace_seconds", "nod_seconds", "nod_lost_fraction", "hysteresis_seconds",
              "min_seconds_between_alerts", "gradual_descent"],
    "score": ["enabled", "decide", "eye_seconds", "head_seconds", "K", "alpha", "beta", "yellow", "red",
              "head_partial_weight"],
    "calibration": ["fraction", "min_gap", "ear_distinct_ratio", "min_open_samples", "min_closed_samples",
                    "ear_limits", "blink_limits", "margin_limits", "phases", "head"],
    "calibration.head": ["fraction", "min_drop_degrees", "threshold_limits", "bowed_quantile",
                         "min_straight_samples", "min_bowed_samples", "phases"],
    "evaluation": ["tolerance_seconds"],
    "outputs": ["retention_days"],
    "outputs.phone": ["enabled", "url", "topic", "repeat_seconds", "notify_recovery"],
    "forward": ["vehicle_id", "status_every_seconds"],
    "forward.http": ["timeout_seconds"],
    "gps": ["enabled", "port", "interface", "max_age_seconds"],
    "power": ["acc_pin", "shutdown_delay_seconds"],
}


def check(cfg, where="config.yaml"):
    """Solleva ValueError con l'elenco dei parametri mancanti; restituisce cfg se è completa."""
    missing = []
    for section, keys in REQUIRED.items():
        node = cfg
        for part in section.split("."):
            node = node.get(part) if isinstance(node, dict) else None
        missing += [f"{section}.{k}" for k in keys if not isinstance(node, dict) or k not in node]
    if missing:
        raise ValueError(f"parametri mancanti in {where}: " + ", ".join(missing))
    return cfg


def merge(dst, src):
    """Unione ricorsiva dei dizionari: i valori di src prevalgono."""
    for k, v in (src or {}).items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            merge(dst[k], v)
        else:
            dst[k] = v
    return dst


def read_yaml(path):
    try:
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        return {}


def paths(data_dir):
    j = lambda name: os.path.join(data_dir, name)
    return {"locale": j("locale.yaml"), "taratura": j("taratura.yaml"), "secrets": j("secrets.yaml"),
            "eventi": j("eventi.csv"), "outbox": j("outbox.sqlite"), "posizioni": j("posizioni.csv"),
            "calibra": j("calibra")}


def load_config(config_path="config.yaml", secrets_path="secrets.yaml", data_dir="data"):
    """Restituisce (cfg, data_taratura): data_taratura è None se si usano le soglie standard."""
    p = paths(data_dir)
    cfg = merge(read_yaml(config_path), read_yaml(p["locale"]))
    calib = read_yaml(p["taratura"])
    merge(cfg.setdefault("indicators", {}), calib.get("indicators", {}))
    sec = read_yaml(p["secrets"] if os.path.exists(p["secrets"]) else secrets_path)
    for key, where in SECRET_SECTIONS.items():
        node = cfg
        for w in where:
            node = node.setdefault(w, {})
        node.update(sec.get(key) or {})
    out = cfg.setdefault("outputs", {})
    out["log_csv"], out["outbox_db"] = p["eventi"], p["outbox"]
    cfg["gps"].setdefault("log_csv", p["posizioni"])
    check(cfg, config_path)
    return cfg, calib.get("data")


def save_calibration(data_dir, thresholds, misure):
    """Salva le soglie del soggetto; restituisce data e ora della taratura."""
    os.makedirs(data_dir, exist_ok=True)
    data = datetime.now().astimezone().isoformat(timespec="seconds")
    old = read_yaml(paths(data_dir)["taratura"])
    doc = {"data": data, "indicators": dict(thresholds), "misure": misure}
    if "data_capo" in old:            # la soglia del chinamento misurata con «Calibra capo» resta (D-45)
        thr = (old.get("indicators") or {}).get("pitch_nod_threshold")
        if thr is not None:
            doc["indicators"]["pitch_nod_threshold"] = thr
        doc["data_capo"], doc["misure_capo"] = old["data_capo"], old.get("misure_capo")
    _write_calibration(data_dir, doc)
    return data


def save_head_calibration(data_dir, values, misure):
    """Taratura del solo capo (D-45): aggiorna posizione dritta e soglia del chinamento, il resto resta."""
    os.makedirs(data_dir, exist_ok=True)
    doc = read_yaml(paths(data_dir)["taratura"])
    doc.setdefault("indicators", {}).update(values)
    doc["data_capo"] = datetime.now().astimezone().isoformat(timespec="seconds")
    doc["misure_capo"] = misure
    _write_calibration(data_dir, doc)
    return doc["data_capo"]


def _write_calibration(data_dir, doc):
    with open(paths(data_dir)["taratura"], "w", encoding="utf-8") as f:
        f.write("# Taratura per soggetto (registro D-24, D-36, D-45). Rifarla cambiando guidatore.\n")
        yaml.safe_dump(doc, f, allow_unicode=True, sort_keys=False)
