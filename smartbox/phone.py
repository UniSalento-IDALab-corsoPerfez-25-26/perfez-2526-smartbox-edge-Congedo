"""Allarme sullo smartphone del guidatore (Proposta P1, C4 allerta multimodale; registro D-39).

Uscita a innesto come buzzer e monitor: riceve il livello a ogni fotogramma e invia una notifica solo
quando il livello sale (ATTENZIONE, PERICOLO), con promemoria finché resta PERICOLO e, facoltativo, un
avviso discreto al rientro. Le notifiche passano dal server ntfy installato SUL RASPBERRY: il telefono
(hotspot) le riceve in locale con l'app ntfy, senza internet e senza servizi esterni.

- ATTENZIONE: priorità 4 (alta: suono e vibrazione);
- PERICOLO:   priorità 5 (urgente: suono e vibrazione lunghi, può superare il "non disturbare");
- rientro:    priorità 2 (bassa, senza suono), solo se notify_recovery è vero.
Il messaggio contiene solo livello, motivo e ora: niente immagini, posizione o misure del volto.
L'invio avviene in un thread: se il telefono o il server non rispondono, l'allarme in cabina non cambia.

Configurazione (config.yaml / locale.yaml, sezione outputs.phone) e segreti (secrets.yaml, phone.token).
"""
import json, queue, sys, threading, time, urllib.request

LEVELS = {1: ("ATTENZIONE", 4, "warning"), 2: ("PERICOLO", 5, "rotating_light")}


def http_send(url, token, payload, timeout=3.0):
    """Pubblica su ntfy in formato JSON (POST alla radice del server, topic nel corpo)."""
    req = urllib.request.Request(url.rstrip("/") + "/", data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json",
                                          **({"Authorization": f"Bearer {token}"} if token else {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return 200 <= r.status < 300


class PhoneNotifier:
    def __init__(self, cfg, send=None, clock=time.monotonic):
        p = (cfg.get("outputs", {}) or {}).get("phone") or {}
        self.enabled = bool(p.get("enabled"))
        self.token = p.get("token")
        # con le notifiche accese i parametri vengono da config.yaml (outputs.phone, D-48), senza valori di riserva
        self.url = p["url"] if self.enabled else None
        self.topic = p["topic"] if self.enabled else None
        self.vehicle = cfg["forward"]["vehicle_id"] if self.enabled else None
        self.repeat = float(p["repeat_seconds"]) if self.enabled else 0.0   # promemoria finché resta PERICOLO (0 = mai)
        self.recovery = bool(p["notify_recovery"]) if self.enabled else False
        self._send = send or (lambda payload: http_send(self.url, self.token, payload))
        self._clock = clock
        self._level, self._last_sent = 0, -1e9
        self.health = "spento" if not self.enabled else "in attesa"
        self.sent = self.failed = 0
        self._q = queue.Queue(maxsize=20)
        if self.enabled:
            threading.Thread(target=self._worker, daemon=True, name="telefono").start()

    def set_level(self, level, reason=""):
        if not self.enabled:
            return
        now = self._clock()
        prev, self._level = self._level, level
        if level > prev or (level == 2 and self.repeat > 0 and now - self._last_sent >= self.repeat):
            self._queue(self._payload(level, reason, repeat=level == prev))
            self._last_sent = now
        elif level == 0 and prev > 0 and self.recovery:
            self._queue({"topic": self.topic, "title": f"Smartbox {self.vehicle}: rientrato",
                         "message": f"Livello normale alle {time.strftime('%H:%M:%S')}",
                         "priority": 2, "tags": ["white_check_mark"]})

    def _payload(self, level, reason, repeat=False):
        name, prio, tag = LEVELS[level]
        return {"topic": self.topic, "title": f"{name}{' (ancora)' if repeat else ''} – Smartbox {self.vehicle}",
                "message": f"{reason or name} alle {time.strftime('%H:%M:%S')}",
                "priority": prio, "tags": [tag]}

    def _queue(self, payload):
        try:
            self._q.put_nowait(payload)
        except queue.Full:                      # telefono irraggiungibile da tempo: si scartano i più vecchi
            try:
                self._q.get_nowait()
            except queue.Empty:
                pass
            self._q.put_nowait(payload)

    def _worker(self):
        while True:
            payload = self._q.get()
            if payload is None:
                return
            try:
                ok = self._send(payload)
            except Exception as e:
                ok = False
                print(f"[telefono] invio non riuscito ({e.__class__.__name__}: {e})", file=sys.stderr, flush=True)
            if ok:
                self.sent += 1; self.health = "ok"
            else:
                self.failed += 1; self.health = "non raggiungibile"

    def flush(self, timeout=3.0):
        """Attende che la coda si svuoti (usato nei test e alla chiusura)."""
        end = time.monotonic() + timeout
        while not self._q.empty() and time.monotonic() < end:
            time.sleep(0.02)
        time.sleep(0.05)

    def close(self):
        if self.enabled:
            self.flush(2.0)
            self._q.put(None)
