"""Taratura per soggetto delle soglie "occhio chiuso" da una prova registrata, poi replay con quelle soglie (D-24).

Uso (dalla cartella principale del repository):
  python tools/calibra.py data/prova5                         # aperti 2-6 s, chiusi 23-24,5 s (protocollo 3b)
  python tools/calibra.py data/prova5 --aperti 2-6 --chiusi 23-24.5
  python tools/calibra.py --tutte                             # tutte le prove con le blendshape

Nella demo la taratura si fa con "Calibra" (5 s occhi aperti, 3 s occhi chiusi) PRIMA della prova.
Qui, per le prove già registrate, gli occhi chiusi si prendono dal primo secondo e mezzo dell'episodio
"occhi chiusi" del protocollo: la verifica è quindi ottimistica (taratura e prova sullo stesso episodio)
e serve a confrontare soglie fisse e tarate, non a misurare le prestazioni.
"""
import argparse, csv, glob, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
sys.path.insert(0, PROJECT)
sys.path.insert(0, HERE)

import yaml
from smartbox.calibration import blendshape_thresholds, ear_threshold
from smartbox.config import check
from replay import run_folder


def interval(s):
    a, b = s.split("-")
    return float(a), float(b)


def samples(folder, win):
    ear, bs = [], []
    for r in csv.DictReader(open(os.path.join(folder, "indicatori.csv"), encoding="utf-8")):
        if str(r["face"]).lower() not in ("1", "true"):
            continue
        t = float(r["t"])
        if win[0] <= t <= win[1]:
            ear.append(float(r["ear"]))
            if r.get("blink"):
                bs.append((float(r["blink"]), float(r["squint"])))
    return ear, bs


def calibrate(folder, args, base):
    ear_o, bs_o = samples(folder, args.aperti)
    ear_c, bs_c = samples(folder, args.chiusi)
    print(f"\n##### {folder}: taratura da aperti {args.aperti[0]:g}-{args.aperti[1]:g} s, "
          f"chiusi {args.chiusi[0]:g}-{args.chiusi[1]:g} s")
    if not bs_o:
        print("  nessuna blendshape nel file: taratura solo EAR non necessaria (c'è già config_tarata.yaml)")
        return
    bs = blendshape_thresholds(bs_o, bs_c, base["calibration"])
    ear = ear_threshold(ear_o, ear_c, base["calibration"])
    if not bs:
        print("  misura non credibile (aperti e chiusi non distinguibili o pochi campioni): soglie non tarate")
        return
    m = bs["misure"]
    print(f"  palpebra aperti {m['palpebra_aperti']:.2f}, chiusi {m['palpebra_chiusi']:.2f} -> soglia {bs['blink_closed_threshold']}")
    print(f"  scarto palpebra-stretti aperti {m['scarto_aperti']:+.2f}, chiusi {m['scarto_chiusi']:+.2f} -> margine {bs['blink_squint_margin']}")
    print(f"  soglia EAR P80 dagli stessi intervalli: {ear}")

    print("\n  --- con le soglie fisse di config.yaml / config_tarata.yaml")
    run_folder(folder, args, base)
    print("\n  --- con le soglie tarate sul soggetto")
    extra = {"indicators": {"eye_closure_source": "blendshape",
                            "blink_closed_threshold": bs["blink_closed_threshold"],
                            "blink_squint_margin": bs["blink_squint_margin"]}}
    run_folder(folder, args, base, extra)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cartella", nargs="?")
    ap.add_argument("--tutte", action="store_true", help="tutte le cartelle data/prova* con le blendshape")
    ap.add_argument("--aperti", type=interval, default=(2.0, 6.0), help="intervallo occhi aperti, es. 2-6")
    ap.add_argument("--chiusi", type=interval, default=(23.0, 24.5), help="intervallo occhi chiusi, es. 23-24.5")
    ap.add_argument("--config", default=os.path.join(PROJECT, "config.yaml"))
    ap.add_argument("--tolleranza", type=float, help="secondi dopo la fine dell'episodio entro cui l'allarme conta (default: evaluation.tolerance_seconds di config.yaml)")
    ap.add_argument("--reali", action="store_true",
                    help="valuta con annotazioni_reali.csv (inizio e fine degli episodi ricavati dai dati), se presente")
    args = ap.parse_args()
    # opzioni richieste da replay.run_folder
    args.senza_tarata, args.criterio, args.eventi, args.annotazioni = False, None, None, None
    reali = args.reali

    base = check(yaml.safe_load(open(args.config, encoding="utf-8")), args.config)
    if args.tolleranza is None:
        args.tolleranza = base["evaluation"]["tolerance_seconds"]
    if args.tutte:
        folders = [f for f in sorted(glob.glob(os.path.join(PROJECT, "data", "prova*")))
                   if "blink" in open(os.path.join(f, "indicatori.csv"), encoding="utf-8").readline()]
    elif args.cartella:
        folders = [args.cartella]
    else:
        ap.error("indicare una cartella di prova oppure --tutte")
    for f in folders:
        real = os.path.join(f, "annotazioni_reali.csv")
        args.annotazioni = real if reali and os.path.exists(real) else None
        calibrate(f, args, base)


if __name__ == "__main__":
    main()
