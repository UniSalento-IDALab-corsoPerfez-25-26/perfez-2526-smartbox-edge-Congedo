"""Inoltro della sintesi dell'evento verso gateway/backend, con store-and-forward.

v4 rispetto al prototipo v3 (tesi §3.13, §4.4):
- invio in un thread dedicato: un endpoint lento non rallenta più il ciclo di visione;
- coda locale SQLite (outbox): un evento resta in coda finché MQTT (ack QoS 1) o HTTP (2xx) non lo
  conferma, e viene ritentato con backoff; il campo event_id permette la deduplicazione lato backend;
- ts = istante della rilevazione, ISO 8601 con fuso orario (schema smartbox-evento/2);
- heartbeat di stato (schema smartbox-stato/1) per la dashboard.
Ordine: MQTT -> HTTP -> resta in coda. Nessuna immagine esce mai dal dispositivo.
"""
import json, os, sqlite3, sys, threading, time, uuid
from datetime import datetime, timedelta, timezone
from . import __version__

EVENT_SCHEMA = "smartbox-evento/2"
STATUS_SCHEMA = "smartbox-stato/1"


def now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")


def event_payload(vehicle_id, ev, ts_iso):
    return {
        "schema": EVENT_SCHEMA,
        "event_id": str(uuid.uuid4()),
        "vehicle": vehicle_id,
        "ts": ts_iso,
        "level": ev.level,
        "prev_level": ev.prev_level,
        "reason": ev.reason,
        "indicators": {"perclos": round(ev.perclos, 3), "ear": round(ev.ear, 3)},
        "sw": __version__,
    }


class EventPublisher:
    def __init__(self, cfg):
        f = cfg["forward"]
        self.vehicle = f["vehicle_id"]
        self.m, self.h = f.get("mqtt", {}), f.get("http", {})
        self._prefix = f"{self.m.get('topic_prefix', 'smartbox')}/{self.vehicle}"
        self._lock = threading.Lock()
        db_path = cfg["outputs"].get("outbox_db", "data/outbox.sqlite")
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self._db = sqlite3.connect(db_path, check_same_thread=False)
        self._db.execute("""CREATE TABLE IF NOT EXISTS outbox(
            id INTEGER PRIMARY KEY, payload TEXT NOT NULL, created TEXT NOT NULL,
            sent_at TEXT, via TEXT, attempts INTEGER DEFAULT 0)""")
        self._db.commit()
        self._retention = cfg["outputs"]["retention_days"]
        self._purge()
        self._c = self._connect_mqtt() if self.m.get("enabled") else None
        self._wake = threading.Event()
        self._stop = threading.Event()
        self.last_via = "locale"
        self._thr = threading.Thread(target=self._worker, daemon=True, name="comunicazione")
        self._thr.start()

    # ---------- MQTT ----------
    def _connect_mqtt(self):
        try:
            import paho.mqtt.client as mqtt
            try:
                c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"smartbox-{self.vehicle}")
            except AttributeError:          # paho-mqtt 1.x
                c = mqtt.Client(client_id=f"smartbox-{self.vehicle}")
            if self.m.get("username"):
                c.username_pw_set(self.m["username"], self.m.get("password", ""))
            if self.m.get("tls"):
                c.tls_set(ca_certs=self.m.get("ca_cert"))
            # last will: se la smartbox sparisce dalla rete il gateway lo sa
            c.will_set(f"{self._prefix}/stato", json.dumps(
                {"schema": STATUS_SCHEMA, "vehicle": self.vehicle, "online": False}), qos=1, retain=True)
            c.reconnect_delay_set(1, 30)
            c.connect_async(self.m["host"], self.m["port"], keepalive=30)
            c.loop_start()                  # riconnessione automatica nel thread di rete di paho
            return c
        except Exception as e:
            print(f"[mqtt] configurazione fallita ({e}); uso HTTP se abilitato", file=sys.stderr)
            return None

    def _send_mqtt(self, topic, body, retain=False):
        if not (self._c and self._c.is_connected()):
            return False
        try:
            info = self._c.publish(topic, body, qos=1, retain=retain)
            info.wait_for_publish(timeout=3)
            return info.is_published()      # PUBACK ricevuto dal broker
        except Exception as e:
            print(f"[mqtt] invio fallito ({e})", file=sys.stderr)
            return False

    # ---------- HTTP ----------
    def _send_http(self, path, payload):
        if not self.h.get("enabled"):
            return False
        try:
            import requests
            hdr = {"Authorization": f"Bearer {self.h['token']}"} if self.h.get("token") else {}
            r = requests.post(self.h["url"].rstrip("/") + path, json=payload, headers=hdr,
                              timeout=self.h["timeout_seconds"])
            return 200 <= r.status_code < 300
        except Exception as e:
            print(f"[http] invio fallito ({e})", file=sys.stderr)
            return False

    # ---------- API pubblica (chiamata dal ciclo principale, non blocca) ----------
    def publish(self, ev, ts_iso=None):
        payload = event_payload(self.vehicle, ev, ts_iso or now_iso())
        with self._lock:
            self._db.execute("INSERT INTO outbox(payload, created) VALUES(?, ?)",
                             (json.dumps(payload), payload["ts"]))
            self._db.commit()
        self._wake.set()
        return payload

    def send_status(self, status: dict):
        """Heartbeat best-effort: non va in coda, conta solo l'ultimo."""
        body = {"schema": STATUS_SCHEMA, "vehicle": self.vehicle, "ts": now_iso(),
                "online": True, "sw": __version__, "outbox_pending": self.pending(), **status}
        threading.Thread(target=self._send_status, args=(body,), daemon=True).start()

    def _send_status(self, body):
        if not self._send_mqtt(f"{self._prefix}/stato", json.dumps(body), retain=True):
            self._send_http("/status", body)

    def pending(self):
        with self._lock:
            return self._db.execute("SELECT COUNT(*) FROM outbox WHERE sent_at IS NULL").fetchone()[0]

    # ---------- thread di comunicazione ----------
    def _worker(self):
        backoff = 1
        while not self._stop.is_set():
            with self._lock:
                rows = self._db.execute(
                    "SELECT id, payload FROM outbox WHERE sent_at IS NULL ORDER BY id LIMIT 20").fetchall()
            if not rows:
                self._wake.wait(5); self._wake.clear(); continue
            for rid, body in rows:
                via = None
                if self._send_mqtt(f"{self._prefix}/eventi", body):
                    via = "mqtt"
                elif self._send_http("/events", json.loads(body)):
                    via = "http"
                with self._lock:
                    if via:
                        self._db.execute("UPDATE outbox SET sent_at=?, via=?, attempts=attempts+1 WHERE id=?",
                                         (datetime.now(timezone.utc).isoformat(), via, rid))
                    else:
                        self._db.execute("UPDATE outbox SET attempts=attempts+1 WHERE id=?", (rid,))
                    self._db.commit()
                if not via:
                    break
                self.last_via = via
            if via:
                backoff = 1
            else:
                self.last_via = "locale"
                self._stop.wait(backoff); backoff = min(backoff * 2, 30)

    def _purge(self):
        limit = (datetime.now(timezone.utc) - timedelta(days=self._retention)).isoformat()
        with self._lock:
            self._db.execute("DELETE FROM outbox WHERE sent_at IS NOT NULL AND sent_at < ?", (limit,))
            self._db.commit()

    def close(self, flush_seconds=3.0):
        # prova a svuotare la coda prima di chiudere (spegnimento ritardato su ACC)
        end = time.monotonic() + flush_seconds
        while self.pending() and time.monotonic() < end:
            self._wake.set(); time.sleep(0.2)
        self._stop.set(); self._wake.set()
        if self._c:
            self._c.loop_stop(); self._c.disconnect()
        self._db.close()
