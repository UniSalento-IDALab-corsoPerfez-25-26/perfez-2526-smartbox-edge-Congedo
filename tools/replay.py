"""Replay: riapplica la logica di smartbox/logic.py a un indicatori.csv registrato dalla demo
e confronta gli allarmi con le annotazioni (registro decisioni D-14). Non serve mediapipe.

Uso (dalla cartella principale del repository):
  python tools/replay.py data/prova4                      # una prova: cartella con indicatori.csv e annotazioni
  python tools/replay.py data/prova4 --criterio ear       # forza il criterio EAR (per vedere il falso microsonno)
  python tools/replay.py --tutte                          # tutte le cartelle data/prova*
  python tools/replay.py data/prova4 --eventi out.csv     # salva anche gli eventi ricalcolati

In una cartella di prova:
  indicatori.csv      t,face,ear,mar,pitch[,blink,squint,jaw]   (obbligatorio)
  annotazioni_3b.csv  oppure annotazioni.csv                    (se presenti, per precisione e richiamo)
  config_tarata.yaml  soglie tarate dalla demo, unite a config.yaml (salvo --senza-tarata)
Senza le colonne blink/squint la logica usa automaticamente l'EAR.
"""
import argparse, copy, csv, glob, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
sys.path.insert(0, PROJECT)
sys.path.insert(0, HERE)

import yaml
from smartbox.logic import DrowsinessMonitor
from smartbox.config import check
from evaluate import evaluate, load_annotations


class Ind:
    """Stessi campi di detector.Indicators, senza importare mediapipe/opencv."""
    def __init__(self, face_found, ear, mar, pitch, blink=None, squint=None):
        self.face_found, self.ear, self.mar, self.pitch = face_found, ear, mar, pitch
        self.blink, self.squint = blink, squint


def _num(v):
    return None if v is None or str(v).strip() == "" else float(v)


def load_indicators(path):
    rows = []
    for r in csv.DictReader(open(path, encoding="utf-8")):
        face = str(r["face"]).strip().lower() in ("1", "true")
        rows.append((float(r["t"]), Ind(face, float(r["ear"] or 0), float(r["mar"] or 0), float(r["pitch"] or 0),
                                        _num(r.get("blink")), _num(r.get("squint")))))
    return rows


def merge(base, extra):
    for k, v in (extra or {}).items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            merge(base[k], v)
        else:
            base[k] = v
    return base


def replay(rows, cfg):
    mon, events = DrowsinessMonitor(cfg), []
    for t, ind in rows:
        ev = mon.update(ind, t)
        if ev:
            events.append(ev)
    return events


def run_folder(folder, args, base_cfg, extra=None):
    """extra: parametri uniti per ultimi alla configurazione (es. soglie da tools/calibra.py)."""
    ind_path = os.path.join(folder, "indicatori.csv")
    if not os.path.exists(ind_path):
        print(f"[{folder}] manca indicatori.csv"); return None
    cfg = copy.deepcopy(base_cfg)
    tarata = os.path.join(folder, "config_tarata.yaml")
    if not args.senza_tarata and os.path.exists(tarata):
        merge(cfg, yaml.safe_load(open(tarata, encoding="utf-8")))
    merge(cfg, extra)
    if args.criterio:
        cfg["indicators"]["eye_closure_source"] = args.criterio
    rows = load_indicators(ind_path)
    has_blend = any(i.blink is not None for _, i in rows)
    crit = cfg["indicators"]["eye_closure_source"]
    if crit == "blendshape" and not has_blend:
        crit = "ear (blendshape assenti nel file)"

    events = replay(rows, cfg)
    print(f"\n=== {folder}")
    ic = cfg["indicators"]
    soglie = (f"palpebra >= {ic['blink_closed_threshold']}, margine >= {ic['blink_squint_margin']}"
              if crit == "blendshape" else f"EAR < {ic['ear_closed_threshold']}")
    print(f"fotogrammi {len(rows)}, durata {rows[-1][0]:.1f} s, criterio occhio chiuso: {crit} ({soglie})")
    for ev in events:
        print(f"  {ev.t:6.2f} s  livello {ev.prev_level}->{ev.level}  {ev.reason}")

    if args.eventi:
        out = args.eventi if not args.tutte else os.path.join(folder, "eventi_replay.csv")
        with open(out, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["timestamp", "t_video_s", "level", "prev_level", "reason", "perclos", "ear"])
            for ev in events:
                w.writerow(["", f"{ev.t:.2f}", ev.level, ev.prev_level, ev.reason, f"{ev.perclos:.3f}", f"{ev.ear:.3f}"])
        print(f"  eventi salvati in {out}")

    ann_path = args.annotazioni or next((p for p in (os.path.join(folder, "annotazioni_3b.csv"),
                                                     os.path.join(folder, "annotazioni.csv")) if os.path.exists(p)), None)
    if not ann_path:
        return None
    det = [(ev.t, ev.reason) for ev in events if ev.level > ev.prev_level]
    res = evaluate(det, load_annotations(ann_path), args.tolleranza)
    print(f"  annotazioni: {os.path.basename(ann_path)}  ->  VP {res['vp']}  FP {res['fp']}  mancati {res['fn']}  "
          f"precisione {res['precisione']:.2f}  richiamo {res['richiamo']:.2f}")
    for m in res["dettaglio"]:
        esito = (f"allarme a {m['allarme_t']:.2f} s ({m['motivo']})" if m["allarme_t"] is not None else "MANCATO")
        print(f"    {m['tipo']:<14} {m['inizio']:5.1f}-{m['fine']:5.1f} s : {esito}")
    for fp in res["falsi_positivi"]:
        print(f"    falso positivo a {fp['t']:.2f} s ({fp['motivo']})")
    return res


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cartella", nargs="?", help="cartella della prova (es. data/prova4)")
    ap.add_argument("--tutte", action="store_true", help="tutte le cartelle data/prova*")
    ap.add_argument("--config", default=os.path.join(PROJECT, "config.yaml"))
    ap.add_argument("--criterio", choices=["blendshape", "ear"], help="forza il criterio occhio chiuso")
    ap.add_argument("--annotazioni", help="CSV annotazioni da usare al posto di quello della cartella")
    ap.add_argument("--senza-tarata", action="store_true", help="non unire config_tarata.yaml")
    ap.add_argument("--eventi", help="salva gli eventi ricalcolati (con --tutte: eventi_replay.csv in ogni cartella)",
                    nargs="?", const="eventi_replay.csv")
    ap.add_argument("--tolleranza", type=float, help="secondi dopo la fine dell'episodio entro cui l'allarme conta (default: evaluation.tolerance_seconds di config.yaml)")
    args = ap.parse_args()

    base = check(yaml.safe_load(open(args.config, encoding="utf-8")), args.config)
    if args.tolleranza is None:
        args.tolleranza = base["evaluation"]["tolerance_seconds"]
    if args.tutte:
        folders = sorted(glob.glob(os.path.join(PROJECT, "data", "prova*")))
        if not folders:
            sys.exit("nessuna cartella data/prova* trovata")
    elif args.cartella:
        folders = [args.cartella]
    else:
        ap.error("indicare una cartella di prova oppure --tutte")
    for f in folders:
        run_folder(f, args, base)


if __name__ == "__main__":
    main()
