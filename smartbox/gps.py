"""Posizione e velocità dallo smartphone del guidatore (registro D-37).

Lo smartphone fa da hotspot e il Raspberry vi si collega; sul telefono un'app GPS open source invia
la posizione ogni pochi secondi a questo ricevitore, anche a schermo spento:
- Traccar Client (protocollo OsmAnd): richiesta HTTP con parametri id, lat, lon, speed (nodi), ...
  oppure, nelle versioni recenti, corpo JSON {"device_id": ..., "location": {"coords": {...}}} (velocità in m/s);
- GPSLogger (Android): "Log to custom URL" con gli stessi parametri del protocollo OsmAnd.

Sicurezza:
- l'identificativo del dispositivo impostato nell'app funziona da token (secrets.yaml, gps.token);
- si accettano solo richieste dalla rete dell'hotspot (stessa sottorete dell'interfaccia Wi-Fi) e da 127.0.0.1;
- la posizione resta sul Raspberry (posizioni.csv); al server va al massimo la velocità.
Oggi posizione e velocità sono solo registrate e mostrate: non cambiano l'allarme (γ = 0, D-27, D-35).
"""
import csv, ipaddress, json, os, subprocess, threading, time, http.server, socketserver
from datetime import datetime, timezone
from urllib.parse import urlsplit, parse_qs

KNOT_KMH = 1.852


def _f(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if x == x else None          # scarta NaN


def parse_fix(query, body=b"", content_type=""):
    """Restituisce (id_dispositivo, fix) da una richiesta OsmAnd (parametri) o JSON; fix è None se manca la posizione.
    fix = {"lat", "lon", "speed_kmh" (None se ignota), "accuracy_m", "bearing", "ts" (epoch s, del telefono)}"""
    q = {k: v[-1] for k, v in parse_qs(query).items()}
    if body and not q.get("lat"):
        if "json" in content_type or body.lstrip()[:1] == b"{":
            try:
                d = json.loads(body)
            except ValueError:
                return None, None
            loc = d.get("location", d)
            if isinstance(loc, list):
                loc = loc[-1] if loc else {}
            c = loc.get("coords", loc)
            sp = _f(c.get("speed"))
            fix = {"lat": _f(c.get("latitude", c.get("lat"))), "lon": _f(c.get("longitude", c.get("lon"))),
                   "speed_kmh": round(sp * 3.6, 1) if sp is not None and sp >= 0 else None,
                   "accuracy_m": _f(c.get("accuracy")), "bearing": _f(c.get("heading", c.get("bearing"))),
                   "ts": time.time()}
            dev = d.get("device_id") or d.get("id") or q.get("id") or q.get("deviceid")
            return dev, (fix if fix["lat"] is not None and fix["lon"] is not None else None)
        q.update({k: v[-1] for k, v in parse_qs(body.decode("utf-8", "replace")).items()})   # form POST
    lat, lon = _f(q.get("lat")), _f(q.get("lon"))
    sp = _f(q.get("speed"))
    ts = _f(q.get("timestamp"))
    if ts and ts > 1e12:                  # millisecondi
        ts /= 1000
    fix = {"lat": lat, "lon": lon,
           "speed_kmh": round(sp * KNOT_KMH, 1) if sp is not None and sp >= 0 else None,
           "accuracy_m": _f(q.get("accuracy")), "bearing": _f(q.get("bearing")), "ts": ts or time.time()}
    dev = q.get("id") or q.get("deviceid")
    return dev, (fix if lat is not None and lon is not None else None)


def local_networks(iface="wlan0"):
    """Sottoreti IPv4 dell'interfaccia Wi-Fi (la rete dell'hotspot del telefono)."""
    try:
        out = subprocess.run(["ip", "-j", "-4", "addr", "show", "dev", iface], capture_output=True,
                             text=True, timeout=2).stdout
        nets = []
        for d in json.loads(out or "[]"):
            for a in d.get("addr_info", []):
                nets.append(ipaddress.ip_network(f"{a['local']}/{a['prefixlen']}", strict=False))
        return nets
    except Exception:
        return []


class GpsReceiver:
    """Ricevitore HTTP della posizione; latest() restituisce l'ultima posizione se più recente di max_age secondi."""

    def __init__(self, token, port=5055, iface="wlan0", max_age=15.0, log_csv=None):
        if not token:
            raise ValueError("serve gps.token in secrets.yaml (lo stesso identificativo impostato nell'app)")
        self.token, self.port, self.iface, self.max_age, self.log_csv = str(token), port, iface, max_age, log_csv
        self._fix, self._t, self._lock = None, 0.0, threading.Lock()
        self._nets, self._nets_t = [], 0.0
        self.received = self.rejected = 0

    def allowed(self, ip):
        a = ipaddress.ip_address(ip)
        if a.is_loopback:
            return True
        if time.monotonic() - self._nets_t > 30:
            self._nets, self._nets_t = local_networks(self.iface), time.monotonic()
        return any(a in n for n in self._nets)

    def handle(self, client_ip, path, body=b"", content_type=""):
        """Elabora una richiesta; restituisce il codice HTTP."""
        if not self.allowed(client_ip):
            self.rejected += 1
            return 403
        dev, fix = parse_fix(urlsplit(path).query, body, content_type)
        if dev != self.token:
            self.rejected += 1
            return 403
        if fix is None:
            return 400
        with self._lock:
            self._fix, self._t = fix, time.monotonic()
        self.received += 1
        if self.log_csv:
            self._log(fix)
        return 200

    def _log(self, fix):
        """Una riga per posizione in posizioni.csv (resta sul dispositivo)."""
        new = not os.path.exists(self.log_csv)
        with open(self.log_csv, "a", newline="", encoding="utf-8") as f:
            wr = csv.writer(f)
            if new:
                wr.writerow(["timestamp", "lat", "lon", "speed_kmh", "accuracy_m", "bearing"])
            wr.writerow([datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
                         fix["lat"], fix["lon"], fix["speed_kmh"], fix["accuracy_m"], fix["bearing"]])

    def latest(self):
        with self._lock:
            if self._fix and time.monotonic() - self._t <= self.max_age:
                return dict(self._fix, age_s=round(time.monotonic() - self._t, 1))
        return None

    def start(self):
        rx = self

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _do(self):
                n = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(min(n, 65536)) if n else b""
                code = rx.handle(self.client_address[0], self.path, body, self.headers.get("Content-Type", ""))
                self.send_response(code)
                self.send_header("Content-Length", "0")
                self.end_headers()

            do_GET = do_POST = _do

        class S(socketserver.ThreadingMixIn, http.server.HTTPServer):
            daemon_threads = True
            allow_reuse_address = True

        srv = S(("0.0.0.0", self.port), H)
        threading.Thread(target=srv.serve_forever, daemon=True, name="gps").start()
        return srv


def from_config(cfg):
    """Avvia il ricevitore se gps.enabled è vero; restituisce None se spento o non avviabile."""
    g = cfg.get("gps", {})
    if not g.get("enabled"):
        return None
    try:
        rx = GpsReceiver(g.get("token"), port=g["port"], iface=g["interface"],
                         max_age=g["max_age_seconds"], log_csv=g.get("log_csv"))
        rx.start()
        print(f"[gps] in ascolto sulla porta {rx.port} (solo rete {rx.iface} e 127.0.0.1)", flush=True)
        return rx
    except Exception as e:
        print(f"[gps] non avviato: {e}", flush=True)
        return None
