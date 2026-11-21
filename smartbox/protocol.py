"""Protocolli di prova eseguiti sul dispositivo, con la sua camera (registro D-41).

Stessi protocolli della demo nel browser (smartbox-dashboard/demo.js, D-08, D-19):
  - "protocollo": 60 s con occhi socchiusi (controllo), occhi chiusi, sbadiglio (controllo), capo chinato;
  - "sbadigli":   60 s con tre sbadigli (con yawns_per_window_warning = 3 l'allarme arriva al terzo).
Durante la prova si registrano, fotogramma per fotogramma, le misure del volto (nessuna immagine), gli eventi della
logica e gli sbadigli contati; alla fine si salvano nella cartella della prova gli stessi file della demo
(indicatori.csv, annotazioni.csv, eventi.csv, config_tarata.yaml) più risultato.json, e si valuta il risultato con la
stessa regola di tools/evaluate.py e di logic.js: un allarme conta se cade tra l'inizio dell'episodio e la fine
più evaluation.tolerance_seconds di config.yaml.
Le istruzioni sono testi brevi che la pagina pronuncia con la sintesi vocale del computer o del telefono.
"""
import csv, json, os
import yaml

PROTOCOLS = {
    "protocollo": [
        (0, 10, "Comportamento normale: guarda avanti", None), (10, 15, "SOCCHIUDI GLI OCCHI (sole)", "socchiusi"),
        (15, 22, "Normale", None), (22, 26, "CHIUDI GLI OCCHI", "occhi_chiusi"), (26, 36, "Normale", None),
        (36, 43, "SBADIGLIA", "sbadiglio"), (43, 50, "Normale", None),
        (50, 54, "CHINA LA TESTA IN AVANTI", "chinamento"), (54, 60, "Normale", None)],
    "sbadigli": [
        (0, 8, "Comportamento normale: guarda avanti", None), (8, 15, "SBADIGLIA", "sbadiglio"),
        (15, 25, "Normale", None), (25, 32, "SBADIGLIA", "sbadiglio"), (32, 42, "Normale", None),
        (42, 49, "SBADIGLIA", "sbadiglio"), (49, 60, "Normale", None)],
}
CONTROL = {"socchiusi", "sbadiglio"}       # nel protocollo: episodi in cui l'allarme NON è atteso
SPOKEN = {"occhi_chiusi": "chiudi gli occhi", "sbadiglio": "sbadiglia", "chinamento": "china la testa in avanti",
          "socchiusi": "socchiudi gli occhi, come per il sole"}
INDICATOR_COLS = ["t", "face", "ear", "mar", "pitch", "blink", "squint", "jaw"]


def evaluate(alarms, ann, tol):
    """alarms: [(t, motivo)], ann: [(inizio, fine, tipo)]. Stessa regola di tools/evaluate.py e logic.js."""
    used, detail = set(), []
    for a, b, tipo in ann:
        k = next((i for i, (t, _) in enumerate(alarms) if a <= t <= b + tol and i not in used), None)
        if k is not None:
            used.add(k)
        detail.append({"tipo": tipo, "inizio": a, "fine": b,
                       "allarme_t": round(alarms[k][0], 2) if k is not None else None,
                       "motivo": alarms[k][1] if k is not None else None,
                       "latenza": round(alarms[k][0] - a, 2) if k is not None else None})
    vp = len(used)
    fp, fn = len(alarms) - vp, len(ann) - vp
    lat = [d["latenza"] for d in detail if d["latenza"] is not None]
    return {"vp": vp, "fp": fp, "fn": fn, "precisione": round(vp / max(1, vp + fp), 2),
            "richiamo": round(vp / max(1, vp + fn), 2), "latenza_media": round(sum(lat) / len(lat), 2) if lat else None,
            "dettaglio": detail,
            "falsi_positivi": [{"t": round(t, 2), "motivo": m} for i, (t, m) in enumerate(alarms) if i not in used]}


class ProtocolSession:
    def __init__(self, kind, t0, out_dir, cfg):
        if kind not in PROTOCOLS:
            raise ValueError(f"protocollo sconosciuto: {kind}")
        self.kind, self.t0, self.out_dir, self.cfg = kind, t0, out_dir, cfg
        self.steps = PROTOCOLS[kind]
        self.end = self.steps[-1][1]
        self.frames, self.events, self.yawns = [], [], []
        self._last_yawn = None
        self.prompt, self.say = "", ""

    def update(self, ind, t, ev=None, monitor=None):
        """Registra un fotogramma; restituisce False quando la prova è finita."""
        dt = t - self.t0
        step = next((s for s in self.steps if s[0] <= dt < s[1]), None)
        if step is None:
            self.prompt, self.say = "Prova finita", "Prova finita."
            return False
        nxt = next((s for s in self.steps if s[3] and dt < s[0] <= dt + 3), None)
        self.prompt = f"{'Preparati: ' + SPOKEN[nxt[3]] if nxt else step[2]}  ·  {dt:.0f} / {self.end} s"
        # la frase cambia a ogni passo (il numero alla fine la distingue: la pagina la pronuncia una volta sola)
        if step[3]:
            self.say = f"Ora: {SPOKEN[step[3]]}. ({step[0]})"
        elif nxt:
            self.say = f"Preparati: {SPOKEN[nxt[3]]}. ({nxt[0]})"
        else:
            self.say = "Guarda avanti, comportamento normale." if step[0] == 0 else f"Basta. Guarda avanti. ({step[0]})"
        f = lambda v, n: "" if v is None else round(float(v), n)
        self.frames.append({"t": round(dt, 3), "face": 1 if ind.face_found else 0, "ear": f(ind.ear, 4),
                            "mar": f(ind.mar, 4), "pitch": f(ind.pitch, 2), "blink": f(getattr(ind, "blink", None), 3),
                            "squint": f(getattr(ind, "squint", None), 3), "jaw": f(getattr(ind, "jaw", None), 3)})
        if ev is not None:
            self.events.append((dt, ev))
        yw = getattr(monitor, "_yawns", None)
        if yw:
            last = yw[-1]
            if last != self._last_yawn:
                self._last_yawn = last
                self.yawns.append(round(dt, 2))
        return True

    def annotations(self):
        ctl = CONTROL if self.kind == "protocollo" else set()
        return [(a, b, tipo) for a, b, _, tipo in self.steps if tipo and tipo not in ctl]

    def result(self):
        alarms = [(t, e.reason) for t, e in self.events if e.level > 0 and e.level > (e.prev_level or 0)]
        ann = self.annotations()
        res = {"tipo": self.kind, "durata_s": self.end, "fotogrammi": len(self.frames),
               "volto_trovato": round(sum(f["face"] for f in self.frames) / max(1, len(self.frames)), 2)}
        if self.kind == "sbadigli":
            used, rows = set(), []
            for a, b, _ in ann:
                k = next((j for j, t in enumerate(self.yawns) if j not in used and a <= t <= b + 3), None)
                if k is not None:
                    used.add(k)
                rows.append({"inizio": a, "fine": b, "contato_t": self.yawns[k] if k is not None else None})
            res.update({"sbadigli": rows, "sbadigli_fuori_tempo": [t for j, t in enumerate(self.yawns) if j not in used],
                        "allarmi": [{"t": round(t, 2), "motivo": m} for t, m in alarms],
                        "richiesti_per_allarme": self.cfg["alarm"].get("yawns_per_window_warning")})
        else:
            res.update(evaluate(alarms, ann, self.cfg["evaluation"]["tolerance_seconds"]))
            res["controlli"] = []
            for a, b, _, tipo in self.steps:
                if tipo in CONTROL:
                    fp = [x for x in res["falsi_positivi"] if a <= x["t"] <= b + 3]
                    res["controlli"].append({"tipo": tipo, "inizio": a, "fine": b, "allarme": fp[0] if fp else None})
        return res

    def save(self):
        """Scrive i file della prova (stessi nomi della demo) e restituisce il risultato."""
        os.makedirs(self.out_dir, exist_ok=True)
        w = lambda name: open(os.path.join(self.out_dir, name), "w", newline="", encoding="utf-8")
        with w("indicatori.csv") as f:
            wr = csv.DictWriter(f, fieldnames=INDICATOR_COLS)
            wr.writeheader(); wr.writerows(self.frames)
        with w("annotazioni.csv") as f:
            wr = csv.writer(f); wr.writerow(["inizio_s", "fine_s", "tipo"]); wr.writerows(self.annotations())
        with w("eventi.csv") as f:
            wr = csv.writer(f); wr.writerow(["timestamp", "t_video_s", "level", "prev_level", "reason", "perclos", "ear"])
            for t, e in self.events:
                wr.writerow(["", round(t, 2), e.level, e.prev_level, e.reason, round(e.perclos, 3), round(e.ear, 3)])
        with w("config_tarata.yaml") as f:
            f.write("# configurazione usata durante la prova sul dispositivo (D-41)\n")
            yaml.safe_dump({"indicators": self.cfg["indicators"], "alarm": self.cfg["alarm"]}, f, sort_keys=False)
        res = self.result()
        with w("risultato.json") as f:
            json.dump(res, f, ensure_ascii=False, indent=1)
        return res
