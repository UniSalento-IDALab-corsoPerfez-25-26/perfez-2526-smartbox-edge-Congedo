"""Validazione: confronta gli allarmi rilevati (data/eventi.csv, colonna t_video_s) con le
annotazioni manuali di un video di test (CSV: inizio_s,fine_s,tipo) e calcola
precisione, richiamo e latenza media dell'allarme, anche per tipo di episodio.

Uso: python tools/evaluate.py data/eventi.csv data/annotazioni.csv --tolleranza 3 [--json]
     python tools/evaluate.py ... --sweep data/indicatori.csv   (tabella di sensibilità soglia EAR, §5.5)

Solo le salite di livello (level > prev_level) contano come allarmi: i "rientro" non sono allarmi.
"""
import argparse, csv, json, os


def load_detections(path):
    out = []
    for r in csv.DictReader(open(path, encoding="utf-8")):
        if not r.get("t_video_s"):
            continue
        lv, prev = int(r["level"]), int(r.get("prev_level") or 0)
        if lv > 0 and lv > prev:
            out.append((float(r["t_video_s"]), r.get("reason", "")))
    return out


def load_annotations(path):
    return [(float(r["inizio_s"]), float(r["fine_s"]), r.get("tipo", ""))
            for r in csv.DictReader(open(path, encoding="utf-8"))]


def evaluate(det, ann, tol):
    """det: [(t, motivo)], ann: [(inizio, fine, tipo)]. Associazione uno-a-uno, primo allarme utile."""
    used, matches = set(), []
    for s, e, tipo in ann:
        hit = next((i for i, (d, _) in enumerate(det) if s <= d <= e + tol and i not in used), None)
        if hit is not None:
            used.add(hit)
        matches.append({"inizio": s, "fine": e, "tipo": tipo,
                        "allarme_t": det[hit][0] if hit is not None else None,
                        "motivo": det[hit][1] if hit is not None else None,
                        "latenza": round(det[hit][0] - s, 2) if hit is not None else None})
    tp = len(used); fp = len(det) - tp; fn = len(ann) - tp
    lat = [m["latenza"] for m in matches if m["latenza"] is not None]
    fps_list = [{"t": d, "motivo": r} for i, (d, r) in enumerate(det) if i not in used]
    return {"episodi": len(ann), "allarmi": len(det), "vp": tp, "fp": fp, "fn": fn,
            "precisione": round(tp / max(1, tp + fp), 3), "richiamo": round(tp / max(1, tp + fn), 3),
            "latenza_media": round(sum(lat) / len(lat), 2) if lat else None,
            "dettaglio": matches, "falsi_positivi": fps_list}


def sweep(indicators_csv, ann, cfg_path, thresholds, tol):
    """Riapplica la logica con varie soglie EAR su indicatori già estratti (t,face,ear,mar,pitch)."""
    import yaml, sys, os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from smartbox.logic import DrowsinessMonitor
    from smartbox.detector import Indicators
    rows = list(csv.DictReader(open(indicators_csv, encoding="utf-8")))
    base = yaml.safe_load(open(cfg_path, encoding="utf-8"))
    out = []
    for thr in thresholds:
        base["indicators"]["ear_closed_threshold"] = thr
        base["indicators"]["eye_closure_source"] = "ear"   # tabella di sensibilità della soglia EAR
        mon, det = DrowsinessMonitor(base), []
        for r in rows:
            face = str(r["face"]).lower() in ("1", "true")
            ev = mon.update(Indicators(face, float(r["ear"]), float(r["mar"]), float(r["pitch"])), float(r["t"]))
            if ev and ev.level > ev.prev_level:
                det.append((ev.t, ev.reason))
        res = evaluate(det, ann, tol)
        out.append((thr, res["precisione"], res["richiamo"], res["fp"], res["fn"]))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("detections"); ap.add_argument("annotations")
    ap.add_argument("--tolleranza", type=float, help="secondi dopo la fine dell'episodio entro cui l'allarme conta (default: evaluation.tolerance_seconds di config.yaml)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--sweep", help="CSV indicatori per fotogramma (t,face,ear,mar,pitch)")
    ap.add_argument("--config", default=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.yaml"))
    a = ap.parse_args()
    if a.tolleranza is None:
        import yaml
        a.tolleranza = yaml.safe_load(open(a.config, encoding="utf-8"))["evaluation"]["tolerance_seconds"]
    ann = load_annotations(a.annotations)
    if a.sweep:
        print("soglia_EAR  precisione  richiamo  FP  FN")
        for thr, p, r, fp, fn in sweep(a.sweep, ann, a.config, [round(0.14 + 0.01 * i, 2) for i in range(13)], a.tolleranza):
            print(f"{thr:9.2f}  {p:10.2f}  {r:8.2f}  {fp:2d}  {fn:2d}")
        return
    res = evaluate(load_detections(a.detections), ann, a.tolleranza)
    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=2)); return
    print(f"episodi annotati: {res['episodi']}  allarmi: {res['allarmi']}")
    print(f"veri positivi {res['vp']}  falsi positivi {res['fp']}  mancati {res['fn']}")
    lat = f"{res['latenza_media']:.1f} s" if res["latenza_media"] is not None else "n.d."
    print(f"precisione {res['precisione']:.2f}  richiamo {res['richiamo']:.2f}  latenza media {lat}")
    for m in res["dettaglio"]:
        esito = f"allarme a {m['allarme_t']:.1f}s ({m['motivo']}), latenza {m['latenza']:.1f}s" if m["allarme_t"] is not None else "MANCATO"
        print(f"  {m['tipo']:<14} {m['inizio']:5.1f}-{m['fine']:5.1f}s : {esito}")
    for f in res["falsi_positivi"]:
        print(f"  falso positivo a {f['t']:.1f}s ({f['motivo']})")


if __name__ == "__main__":
    main()
