"""Valuta il punteggio 0-1 della Proposta P1 (smartbox/score.py, D-27) sulle prove registrate.

Uso (dalla cartella principale del repository):
  python tools/modello.py --tutte            # tutte le prove con blendshape
  python tools/modello.py data/prova6        # una prova
  python tools/modello.py data/prova6 --curva curva.csv   # salva anche il punteggio nel tempo

Per ogni prova: taratura per soggetto (come tools/calibra.py: aperti 2-6 s, chiusi 23-24,5 s), poi punteggio
fotogramma per fotogramma, allarmi gialli (>= 0,60) e rossi (>= 0,75), confronto con annotazioni_reali.csv
(o annotazioni.csv) e valore massimo nei controlli: guida normale 2-9 s, occhi socchiusi 10-15 s, sbadiglio 36-43 s.
Stesso limite di calibra.py: la taratura usa parte dell'episodio valutato, quindi il risultato è ottimistico.
"""
import argparse, copy, csv, glob, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
sys.path.insert(0, PROJECT)
sys.path.insert(0, HERE)

import yaml
from smartbox.calibration import blendshape_thresholds
from smartbox.config import check
from smartbox.score import DrowsinessScore
from replay import load_indicators, merge
from evaluate import evaluate, load_annotations

CONTROLLI = {"guida normale": (2, 9), "occhi socchiusi": (10, 15), "sbadiglio": (36, 43)}


def crossings(curve, thr):
    """Istanti in cui il punteggio supera la soglia (si riarma quando scende di 0,1 sotto)."""
    out, on = [], False
    for t, s in curve:
        if s >= thr and not on:
            out.append(t); on = True
        elif s < thr - 0.1:
            on = False
    return out


def run(folder, base, args):
    rows = load_indicators(os.path.join(folder, "indicatori.csv"))
    if not any(i.blink is not None for _, i in rows):
        print(f"\n=== {folder}: niente blendshape, salto"); return
    cfg = copy.deepcopy(base)
    tarata = os.path.join(folder, "config_tarata.yaml")
    if os.path.exists(tarata):
        merge(cfg, yaml.safe_load(open(tarata, encoding="utf-8")))
    cal = blendshape_thresholds([(i.blink, i.squint) for t, i in rows if 2 <= t <= 6 and i.face_found],
                                [(i.blink, i.squint) for t, i in rows if 23 <= t <= 24.5 and i.face_found],
                                cfg["calibration"])
    if cal:
        cfg["indicators"].update({"eye_closure_source": "blendshape",
                                  "blink_closed_threshold": cal["blink_closed_threshold"],
                                  "blink_squint_margin": cal["blink_squint_margin"]})
    sc = DrowsinessScore(cfg)
    curve = [(t, sc.update(i, t)) for t, i in rows]
    red, yellow = crossings(curve, sc.red), crossings(curve, sc.yellow)
    print(f"\n=== {folder}")
    print(f"taratura: {'palpebra >= %s, margine >= %s' % (cal['blink_closed_threshold'], cal['blink_squint_margin']) if cal else 'non credibile, soglie del file'}")
    print(f"allarmi rossi: {[round(t, 2) for t in red]}   gialli: {[round(t, 2) for t in yellow]}")
    for nome, (a, b) in CONTROLLI.items():
        print(f"  massimo nei controlli, {nome:<16} {max(s for t, s in curve if a <= t <= b):.2f}")
    ann_path = next((p for p in (os.path.join(folder, "annotazioni_reali.csv"), os.path.join(folder, "annotazioni.csv"))
                     if os.path.exists(p)), None)
    if ann_path:
        ann = load_annotations(ann_path)
        inside = lambda t: any(a <= t <= b + args.tolleranza for a, b, _ in ann)
        res = evaluate([(t, "punteggio") for t in red], ann, args.tolleranza)
        # un secondo allarme dentro lo stesso episodio (occhi riaperti e richiusi) non è un falso allarme
        fp_red = [round(t, 2) for t in red if not inside(t)]
        fp_yel = [round(t, 2) for t in yellow if not inside(t)]
        repeat = len([t for t in red if inside(t)]) - res["vp"]
        print(f"  {os.path.basename(ann_path)} -> episodi rilevati {res['vp']}/{len(ann)}  "
              f"falsi allarmi rossi {len(fp_red)} gialli {len(fp_yel)}"
              + (f"  (allarmi ripetuti nello stesso episodio: {repeat})" if repeat else ""))
        for m in res["dettaglio"]:
            esito = f"rosso dopo {m['latenza']:.2f} s" if m["allarme_t"] is not None else "MANCATO"
            print(f"    {m['tipo']:<14} {m['inizio']:5.1f}-{m['fine']:5.1f} s : {esito}")
        if fp_red or fp_yel:
            print(f"    falsi allarmi: rossi {fp_red}, gialli {fp_yel}")
    if args.curva:
        with open(args.curva, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f); w.writerow(["t", "score"])
            w.writerows((f"{t:.3f}", f"{s:.3f}") for t, s in curve)
        print(f"  curva salvata in {args.curva}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cartella", nargs="?")
    ap.add_argument("--tutte", action="store_true")
    ap.add_argument("--config", default=os.path.join(PROJECT, "config.yaml"))
    ap.add_argument("--tolleranza", type=float, help="secondi dopo la fine dell'episodio entro cui l'allarme conta (default: evaluation.tolerance_seconds di config.yaml)")
    ap.add_argument("--curva", help="CSV in cui salvare il punteggio nel tempo (solo con una cartella)")
    args = ap.parse_args()
    base = check(yaml.safe_load(open(args.config, encoding="utf-8")), args.config)
    if args.tolleranza is None:
        args.tolleranza = base["evaluation"]["tolerance_seconds"]
    if args.tutte:
        folders = sorted(glob.glob(os.path.join(PROJECT, "data", "prova*"))); args.curva = None
    elif args.cartella:
        folders = [args.cartella]
    else:
        ap.error("indicare una cartella di prova oppure --tutte")
    for f in folders:
        run(f, base, args)


if __name__ == "__main__":
    main()
