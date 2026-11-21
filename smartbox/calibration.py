"""Taratura per soggetto delle soglie "occhio chiuso" (registro decisioni D-10, D-24).

Si misurano gli stessi indicatori a occhi aperti e a occhi chiusi e la soglia si mette all'80% del
percorso dagli aperti ai chiusi (criterio P80 del PERCLOS: l'occhio è "chiuso" se chiuso oltre l'80%).
- EAR (scende chiudendo):            soglia = chiusi + 0,2 × (aperti − chiusi)
- palpebra chiusa (sale chiudendo):   soglia = aperti + 0,8 × (chiusi − aperti)
- palpebra − occhi stretti (sale):    margine = aperti + 0,8 × (chiusi − aperti)
Le soglie fisse 0,60 / 0,25 di D-12 valevano per il soggetto 1 e non per il soggetto 2 (D-23).
La stessa formula è in smartbox-dashboard/demo.js (pulsante "Calibra").
Tutti i criteri (frazione P80, campioni minimi, intervalli ammessi, durate delle fasi) stanno nella sezione
`calibration` di config.yaml e arrivano qui come dizionario `cal` (D-48): in questo file non ci sono numeri da tarare.
"""
def quantile(values, q):
    s = sorted(values)
    return s[min(len(s) - 1, int(q * len(s)))] if s else float("nan")


def median(values):
    """Mediana come in smartbox-dashboard/demo.js: con un numero pari di valori prende quello superiore."""
    s = sorted(values)
    return s[len(s) // 2] if s else float("nan")


def _clamp(x, lo_hi):
    return min(lo_hi[1], max(lo_hi[0], x))


def _enough(cal, n_open, n_closed):
    return n_open >= cal["min_open_samples"] and n_closed >= cal["min_closed_samples"]


def _phases(ends, names):
    """Fasi nell'ordine previsto, con la fine di ciascuna in secondi dall'avvio (crescente)."""
    out = tuple((n, float(ends[n])) for n in names)
    if any(b <= a for (_, a), (_, b) in zip(out, out[1:])):
        raise ValueError(f"fasi della taratura non crescenti: {dict(out)}")
    return out


def ear_threshold(open_ear, closed_ear, cal):
    """Soglia EAR P80, o None se aperti e chiusi non sono distinguibili (chiusi >= ear_distinct_ratio × aperti)."""
    if not _enough(cal, len(open_ear), len(closed_ear)):
        return None
    eo, ec = median(open_ear), median(closed_ear)
    if ec >= eo * cal["ear_distinct_ratio"]:
        return None
    return round(_clamp(ec + (1 - cal["fraction"]) * (eo - ec), cal["ear_limits"]), 3)


def blendshape_thresholds(open_bs, closed_bs, cal):
    """open_bs, closed_bs: sequenze di (blink, squint). Restituisce un dict con
    blink_closed_threshold e blink_squint_margin, più i valori misurati; None se la misura non è credibile
    (pochi campioni, oppure palpebra o scarto palpebra − occhi stretti che cambiano meno di min_gap)."""
    if not _enough(cal, len(open_bs), len(closed_bs)):
        return None
    frac, gap = cal["fraction"], cal["min_gap"]
    bo, bc = median(b for b, _ in open_bs), median(b for b, _ in closed_bs)
    do, dc = median(b - s for b, s in open_bs), median(b - s for b, s in closed_bs)
    if bc - bo < gap or dc - do < gap:
        return None
    return {
        "blink_closed_threshold": round(_clamp(bo + frac * (bc - bo), cal["blink_limits"]), 3),
        "blink_squint_margin": round(_clamp(do + frac * (dc - do), cal["margin_limits"]), 3),
        "misure": {"palpebra_aperti": round(bo, 3), "palpebra_chiusi": round(bc, 3),
                   "scarto_aperti": round(do, 3), "scarto_chiusi": round(dc, 3)},
    }


class GuidedCalibration:
    """Taratura guidata sul dispositivo, con la stessa sequenza del pulsante "Calibra" della demo (D-36).

    Si chiama update(ind, t) a ogni fotogramma; le fasi (fine di ciascuna in calibration.phases) sono:
      prepara -> aperti (si raccolgono i campioni) -> chiudi (tempo per chiudere) -> chiusi (si raccolgono) -> fatto.
    Il guidatore non vede lo schermo a occhi chiusi: l'istruzione è chiuderli e contare fino a 5.
    result() restituisce le soglie da unire a cfg["indicators"], oppure None con il motivo in self.error.
    """
    PHASE_NAMES = ("prepara", "aperti", "chiudi", "chiusi")

    def __init__(self, t0, cal):
        self.t0, self.cal = t0, cal
        self.phases = _phases(cal["phases"], self.PHASE_NAMES)
        self.open_ear, self.closed_ear, self.open_bs, self.closed_bs = [], [], [], []
        self.open_pitch = []          # posizione normale del capo (D-41): la webcam può inquadrare dal basso
        self.error = None

    def phase(self, t):
        dt = t - self.t0
        for name, end in self.phases:
            if dt < end:
                return name, end - dt
        return "fatto", 0.0

    def update(self, ind, t):
        name, left = self.phase(t)
        if ind.face_found and name in ("aperti", "chiusi"):
            ear, bs = (self.open_ear, self.open_bs) if name == "aperti" else (self.closed_ear, self.closed_bs)
            ear.append(ind.ear)
            if name == "aperti":
                self.open_pitch.append(ind.pitch)
            if ind.blink is not None and ind.squint is not None:
                bs.append((ind.blink, ind.squint))
        return name, left

    def result(self):
        if not _enough(self.cal, len(self.open_ear), len(self.closed_ear)):
            self.error = "volto rilevato troppo poco: più luce, viso verso la camera"
            return None
        bs = blendshape_thresholds(self.open_bs, self.closed_bs, self.cal)
        if not bs:
            self.error = "occhi aperti e chiusi non distinguibili: ripetere tenendo gli occhi ben chiusi"
            return None
        out = {"eye_closure_source": "blendshape",
               "blink_closed_threshold": bs["blink_closed_threshold"],
               "blink_squint_margin": bs["blink_squint_margin"]}
        ear = ear_threshold(self.open_ear, self.closed_ear, self.cal)
        if ear is not None:
            out["ear_closed_threshold"] = ear
        if len(self.open_pitch) >= self.cal["min_open_samples"]:
            out["pitch_baseline"] = round(median(self.open_pitch), 1)   # gradi: lo zero del capo per questo guidatore
        return out, bs["misure"]


class HeadCalibration:
    """Taratura del solo capo (D-45), senza la parte degli occhi: «Calibra capo» nella pagina del dispositivo.

    Fasi (fine di ciascuna in calibration.head.phases): capo_prepara -> capo_dritto (si raccoglie l'inclinazione
    normale) -> capo_china (la voce lo dice e si ha il tempo di chinarsi) -> capo_chino (si raccoglie).
    A capo chino conta un percentile alto (bowed_quantile), non la mediana: chi si china in ritardo non annulla la misura.
    Risultato: pitch_baseline (lo zero del capo, come nella taratura completa) e pitch_nod_threshold =
    fraction × (chino − dritto), arrotondata al grado. Le soglie degli occhi non si toccano.
    A capo chino la webcam può perdere il volto (D-42): servono almeno min_bowed_samples misure valide.
    """
    PHASE_NAMES = ("capo_prepara", "capo_dritto", "capo_china", "capo_chino")

    def __init__(self, t0, cal):
        self.t0, self.cal = t0, cal["head"]
        self.phases = _phases(self.cal["phases"], self.PHASE_NAMES)
        self.straight, self.bowed = [], []
        self.error = None
        self.seen = 0                 # fotogrammi visti in tutto nelle due fasi di misura (per capire un rifiuto)

    phase = GuidedCalibration.phase

    def update(self, ind, t):
        name, left = self.phase(t)
        if name in ("capo_dritto", "capo_chino"):
            self.seen += 1
        if ind.face_found and name in ("capo_dritto", "capo_chino"):
            (self.straight if name == "capo_dritto" else self.bowed).append(ind.pitch)
        return name, left

    def summary(self):
        """Riga per il registro del servizio, anche quando la misura è rifiutata."""
        f = lambda v: "—" if not v else f"{median(v):+.1f}"
        return (f"fotogrammi {self.seen}, volto a capo dritto {len(self.straight)} (mediana {f(self.straight)}), "
                f"a capo chino {len(self.bowed)} (mediana {f(self.bowed)})")

    def result(self):
        h = self.cal
        if len(self.straight) < h["min_straight_samples"]:
            self.error = "volto rilevato troppo poco a capo dritto: più luce, viso verso la camera"
            return None
        if len(self.bowed) < h["min_bowed_samples"]:
            self.error = "volto perso a capo chino: chinalo un po' meno o abbassa la camera"
            return None
        base, bowed = median(self.straight), quantile(self.bowed, h["bowed_quantile"])
        drop = bowed - base
        if drop < h["min_drop_degrees"]:
            self.error = f"capo chinato troppo poco ({drop:.0f} gradi): chinalo di più, come addormentandoti"
            return None
        thr = int(round(_clamp(h["fraction"] * drop, h["threshold_limits"])))
        out = {"pitch_baseline": round(base, 1), "pitch_nod_threshold": thr}
        return out, {"capo_dritto": round(base, 1), "capo_chino": round(bowed, 1), "chinamento": round(drop, 1),
                     "campioni_dritto": len(self.straight), "campioni_chino": len(self.bowed)}
