"""Componente 5 della Proposta P1: punteggio continuo di sonnolenza 0-1 con doppia soglia (registro D-27).

Schema della Proposta:  Score finale = K * Score_AI + alfa * Inerzia_capo + beta * Parametri_veicolo
- Score_AI: quanto a lungo gli occhi sono rimasti chiusi di recente. Il riconoscimento "occhio chiuso" è il
  modello per soggetto addestrato in calibrazione (soglie P80 su palpebra chiusa e scarto palpebra - occhi
  stretti, D-24; EAR come ripiego). Ogni fotogramma chiuso spinge il punteggio verso 1, ogni fotogramma aperto
  verso 0 (media mobile esponenziale): con gli occhi chiusi di continuo per `eye_seconds` il punteggio arriva
  a 0,75 (soglia rossa), un battito di ciglia resta sotto 0,25.
- Inerzia_capo: lo stesso per il capo chinato oltre `pitch_nod_threshold` per `head_seconds`.
- Parametri_veicolo: predisposto (beta = 0): oggi la Smartbox non riceve dati dal veicolo.
- Sbadiglio: con la bocca che si apre, chiusura degli occhi e inclinazione del capo sono attribuite allo
  sbadiglio (come in logic.py, D-11); nelle prove la testa si inclina fino a 22 gradi sbadigliando.
Soglie: gialla 0,60 (attenzione), rossa 0,75 (pericolo), come nella Proposta.
"""
import math

class DrowsinessScore:
    def __init__(self, cfg):
        sc = cfg["score"]          # tutti i valori in config.yaml, sezione score (D-48)
        ind, al = cfg["indicators"], cfg["alarm"]
        self.cfg = sc
        self.k, self.alpha, self.beta = sc["K"], sc["alpha"], sc["beta"]
        self.yellow, self.red = sc["yellow"], sc["red"]
        # costante di tempo tale che dopo T secondi continui il punteggio valga 0,75: 1 - exp(-T/tau) = 0,75
        self.tau_eye = sc["eye_seconds"] / math.log(4)
        self.tau_head = sc["head_seconds"] / math.log(4)
        self.src = ind["eye_closure_source"]
        self.blink_thr = ind["blink_closed_threshold"]
        self.blink_margin = ind["blink_squint_margin"]
        self.ear_thr = ind["ear_closed_threshold"]
        self.mar_thr = ind["mar_yawn_threshold"]
        self.pitch_thr = ind["pitch_nod_threshold"]
        self.yawn_min = al["yawn_min_seconds"]
        self.opening_frac = al["yawn_opening_fraction"]
        self.head_partial = sc["head_partial_weight"]
        self.nod_lost_fraction = al["nod_lost_fraction"]
        self._last_pitch = None
        self.grace_s = al["yawn_eye_grace_seconds"]
        self.ai = self.head = 0.0
        self.vehicle = 0.0          # predisposto: 0-1 dai dati del veicolo (sterzo, corsia), oggi assente
        self.score = 0.0
        self._prev_t = None
        self._wide_since = None
        self._grace_until = -1e9

    def _closed(self, ind):
        b, s = getattr(ind, "blink", None), getattr(ind, "squint", None)
        if self.src == "blendshape" and b is not None and s is not None:
            return b >= self.blink_thr and b - s >= self.blink_margin
        return ind.ear < self.ear_thr

    def update(self, ind, now):
        """Aggiorna con un fotogramma; restituisce il punteggio finale 0-1."""
        dt = 0.0 if self._prev_t is None else max(0.0, now - self._prev_t)
        self._prev_t = now
        if not ind.face_found:          # senza volto il punteggio resta fermo (come il livello in logic.py) ...
            f = self.nod_lost_fraction
            if self._last_pitch is not None and f > 0 and self._last_pitch >= self.pitch_thr * f:
                # ... tranne quando il volto si perde con il capo già chinato: il capo conta come chinato (D-42)
                self.head += (1 - math.exp(-dt / self.tau_head)) * (1.0 - self.head)
                self.score = min(1.0, self.k * self.ai + self.alpha * self.head + self.beta * self.vehicle)
            return self.score
        self._last_pitch = ind.pitch
        opening = ind.mar > self.mar_thr * self.opening_frac
        if opening:
            self._wide_since = self._wide_since if self._wide_since is not None else now
        else:
            if self._wide_since is not None and now - self._wide_since >= self.yawn_min:
                self._grace_until = now + self.grace_s
            self._wide_since = None
        yawning = opening or now <= self._grace_until
        eye = 1.0 if (self._closed(ind) and not yawning) else 0.0
        if opening:
            head = 0.0
        elif ind.pitch >= self.pitch_thr:
            head = 1.0
        else:
            head = max(0.0, ind.pitch / self.pitch_thr) * self.head_partial   # capo un po' chino: contributo parziale
        self.ai += (1 - math.exp(-dt / self.tau_eye)) * (eye - self.ai)
        self.head += (1 - math.exp(-dt / self.tau_head)) * (head - self.head)
        self.score = min(1.0, self.k * self.ai + self.alpha * self.head + self.beta * self.vehicle)
        return self.score

    def level(self):
        """0 sotto la soglia gialla, 1 tra gialla e rossa, 2 oltre la rossa."""
        return 2 if self.score >= self.red else (1 if self.score >= self.yellow else 0)
