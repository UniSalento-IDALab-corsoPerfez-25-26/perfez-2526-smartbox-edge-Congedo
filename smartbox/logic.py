"""Logica di allarme: finestre temporali, PERCLOS, microsonno, sbadigli, chinamenti, isteresi.

Livelli: 0 = normale, 1 = attenzione, 2 = pericolo.
Restituisce eventi (transizioni di livello) da loggare e notificare.

v4 rispetto al prototipo v3 (tesi §4.9):
- il "volto assente" è gestito qui e non in main.py: l'assenza di dati porta almeno al livello 1
  e blocca la discesa (un sistema che tace in caso di guasto è pericoloso, §3.14.1);
- discesa graduale opzionale 2 -> 1 -> 0 (alarm.gradual_descent);
- durata minima dello sbadiglio configurabile; ogni evento riporta il livello precedente.
"""
from collections import deque
from dataclasses import dataclass
import time


@dataclass
class Event:
    t: float
    level: int
    reason: str
    perclos: float
    ear: float
    prev_level: int = 0


class DrowsinessMonitor:
    def __init__(self, cfg):
        ind, al = cfg["indicators"], cfg["alarm"]
        self.ear_thr = ind["ear_closed_threshold"]
        # Criterio "occhio chiuso": blendshape MediaPipe (palpebra chiusa vs occhi stretti) se disponibili,
        # altrimenti EAR. Con l'EAR occhi socchiusi (sole) e chiusi risultano uguali (prova del 10/11/2026:
        # EAR 0,09 contro 0,10); con le blendshape no (palpebra 0,54 / stretti 0,58 contro 0,70 / 0,21).
        self.closure_src = ind["eye_closure_source"]
        self.blink_thr = ind["blink_closed_threshold"]
        self.blink_margin = ind["blink_squint_margin"]
        self.mar_thr = ind["mar_yawn_threshold"]
        self.pitch_thr = ind["pitch_nod_threshold"]
        self.win = ind["window_seconds"]
        self.al = al
        self.face_lost_s = cfg["video"]["face_lost_seconds"]
        self.gradual = al["gradual_descent"]
        self.opening_frac = al["yawn_opening_fraction"]
        self._closed = deque()      # (t, 1/0) per PERCLOS
        self._closed_sum = 0
        self._yawns = deque()       # istanti di sbadiglio
        self._eye_closed_since = None
        self._mouth_open_since = None
        self._wide_since = None         # bocca in apertura (MAR > yawn_opening_fraction × soglia), per la tolleranza sugli occhi
        self._yawn_grace_until = -1e9
        self._nod_since = None
        self._last_pitch = None         # ultima inclinazione con il volto visibile (D-42)
        self._lost_since = None
        self.face_lost = False
        self.level = 0
        self._below_since = None
        self._last_alert_t = -1e9
        self.perclos = 0.0

    def _prune(self, now):
        while self._closed and now - self._closed[0][0] > self.win:
            self._closed_sum -= self._closed.popleft()[1]
        while self._yawns and now - self._yawns[0] > self.win:
            self._yawns.popleft()

    def _eyes_closed(self, ind):
        blink, squint = getattr(ind, "blink", None), getattr(ind, "squint", None)
        if self.closure_src == "blendshape" and blink is not None and squint is not None:
            # chiuso = palpebra chiusa E non solo occhi stretti
            return blink >= self.blink_thr and blink - squint >= self.blink_margin
        return ind.ear < self.ear_thr

    def _event(self, now, level, reason, ear):
        ev = Event(now, level, reason, self.perclos, ear, self.level)
        self.level = level
        return ev

    def update(self, ind, now=None):
        now = time.time() if now is None else now
        if not ind.face_found:
            return self._update_no_face(now)
        self._lost_since, self.face_lost = None, False
        self._last_pitch = ind.pitch

        # Sbadigliando gli occhi si stringono: con la bocca che si apre (MAR oltre yawn_opening_fraction della
        # soglia sbadiglio) la chiusura è attribuita allo sbadiglio e non conta né per PERCLOS né per il microsonno.
        # Alla fine di uno sbadiglio vero (bocca aperta almeno yawn_min_seconds) gli occhi restano spesso
        # chiusi ancora 1-2 s: la tolleranza continua per yawn_eye_grace_seconds dopo la chiusura della bocca.
        mouth_opening = ind.mar > self.mar_thr * self.opening_frac
        if mouth_opening:
            if self._wide_since is None:
                self._wide_since = now
        else:
            if self._wide_since is not None and now - self._wide_since >= self.al["yawn_min_seconds"]:
                self._yawn_grace_until = now + self.al["yawn_eye_grace_seconds"]
            self._wide_since = None
        yawning = self.al["yawn_masks_eye_closure"] and (mouth_opening or now <= self._yawn_grace_until)
        closed = self._eyes_closed(ind) and not yawning
        self._closed.append((now, 1 if closed else 0))
        self._closed_sum += 1 if closed else 0
        self._prune(now)
        # denominatore = fotogrammi effettivamente analizzati nella finestra (tesi §4.2)
        self.perclos = self._closed_sum / max(1, len(self._closed))
        # all'avvio la finestra è quasi vuota: un solo battito darebbe PERCLOS 50-100%.
        # Il PERCLOS può dare allarme solo quando copre almeno perclos_min_seconds di dati.
        perclos_ok = now - self._closed[0][0] >= self.al["perclos_min_seconds"]
        perclos = self.perclos if perclos_ok else 0.0

        # microsonno: occhi chiusi continuativamente
        if closed:
            self._eye_closed_since = self._eye_closed_since or now
        else:
            self._eye_closed_since = None
        microsleep = (self._eye_closed_since is not None and
                      now - self._eye_closed_since >= self.al["microsleep_seconds"])

        # sbadiglio: bocca aperta oltre soglia per > yawn_min_seconds, contato una volta per episodio
        if ind.mar > self.mar_thr:
            self._mouth_open_since = self._mouth_open_since or now
            if (now - self._mouth_open_since > self.al["yawn_min_seconds"]
                    and (not self._yawns or self._yawns[-1] < self._mouth_open_since)):
                self._yawns.append(now)
        else:
            self._mouth_open_since = None

        # chinamento del capo
        if ind.pitch > self.pitch_thr:
            self._nod_since = self._nod_since or now
        else:
            self._nod_since = None
        nod = self._nod_since is not None and now - self._nod_since >= self.al["nod_seconds"]

        target, reason = 0, ""
        if microsleep or perclos >= self.al["perclos_danger"] or nod:
            target, reason = 2, "microsonno" if microsleep else ("chinamento" if nod else "PERCLOS alto")
        elif perclos >= self.al["perclos_warning"] or len(self._yawns) >= self.al["yawns_per_window_warning"]:
            target, reason = 1, "PERCLOS" if perclos >= self.al["perclos_warning"] else "sbadigli ripetuti"

        # isteresi: si sale subito, si scende solo dopo hysteresis_seconds sotto soglia
        if target > self.level:
            if target == 2 or now - self._last_alert_t >= self.al["min_seconds_between_alerts"]:
                self._last_alert_t = now
                self._below_since = None
                return self._event(now, target, reason, ind.ear)
        elif target < self.level:
            self._below_since = self._below_since or now
            if now - self._below_since >= self.al["hysteresis_seconds"]:
                new = max(target, self.level - 1) if self.gradual else target
                # se resta un gradino da scendere, il conto dell'isteresi riparte da adesso
                self._below_since = now if new > target else None
                return self._event(now, new, "rientro", ind.ear)
        else:
            self._below_since = None
        return None

    def head_going_down(self):
        """Il volto si è perso mentre il capo era già chinato (almeno nod_lost_fraction della soglia):
        con la camera davanti, chinandosi molto si vedono solo fronte e capelli (prova del 16/11/2026, D-42)."""
        f = self.al["nod_lost_fraction"]
        return self._last_pitch is not None and f > 0 and self._last_pitch >= self.pitch_thr * f

    def _update_no_face(self, now):
        # senza volto non si aggiornano gli indicatori e il livello non scende
        self._eye_closed_since = self._mouth_open_since = None
        self._below_since = None
        self._lost_since = self._lost_since or now
        if self.head_going_down():
            # il capo stava scendendo quando il volto è sparito: il chinamento continua (D-42)
            self._nod_since = self._nod_since or self._lost_since
            if now - self._nod_since >= self.al["nod_seconds"] and self.level < 2:
                self._last_alert_t = now
                return self._event(now, 2, "chinamento", 0.0)
        else:
            self._nod_since = None
        if not self.face_lost and now - self._lost_since >= self.face_lost_s:
            self.face_lost = True
            if self.level < 1:
                return self._event(now, 1, "volto assente", 0.0)
        return None
