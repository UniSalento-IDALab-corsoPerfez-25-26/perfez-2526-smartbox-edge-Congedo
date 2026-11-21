"""Accesso alla pagina del dispositivo dal telefono, attraverso l'hotspot (registro D-43).

Regole:
  - da 127.0.0.1 (il PC con il tunnel SSH): accesso libero, come prima;
  - dalla rete dell'interfaccia indicata (l'hotspot del telefono, wlan0): solo dopo l'accesso con password;
  - da qualsiasi altra rete (per esempio quella di casa via cavo): rifiutato.
La password non è mai salvata in chiaro: in secrets.yaml c'è solo l'hash PBKDF2-SHA256 con sale casuale.
Dopo l'accesso il browser riceve un cookie di sessione (HttpOnly, SameSite=Strict) valido 12 ore o fino alla chiusura.
I tentativi sbagliati sono rallentati e, dopo 5 errori in 5 minuti dallo stesso indirizzo, bloccati per 5 minuti.
Il collegamento telefono–Raspberry è HTTP: resta dentro l'hotspot, protetto dalla sua password Wi-Fi (WPA2).
"""
import hashlib, hmac, ipaddress, json, re, secrets, subprocess, sys, threading, time

ITERATIONS = 200_000
SESSION_HOURS = 12
MAX_FAILS, FAIL_WINDOW, BLOCK_SECONDS = 5, 300, 300


def hash_password(password, salt=None, iterations=ITERATIONS):
    salt = salt or secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), iterations)
    return f"pbkdf2_sha256${iterations}${salt}${dk.hex()}"


def check_password(password, stored):
    try:
        algo, it, salt, digest = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        return hmac.compare_digest(hash_password(password, salt, int(it)).split("$")[3], digest)
    except (ValueError, AttributeError):
        return False


def local_networks(iface="wlan0"):
    """Sottoreti IPv4 di un'interfaccia: `ip` su Linux (Raspberry), `ifconfig` su macOS (prove sul Mac)."""
    nets = []
    try:
        out = subprocess.run(["ip", "-j", "-4", "addr", "show", "dev", iface], capture_output=True,
                             text=True, timeout=2).stdout
        for d in json.loads(out or "[]"):
            for a in d.get("addr_info", []):
                nets.append(ipaddress.ip_network(f"{a['local']}/{a['prefixlen']}", strict=False))
        return nets
    except (FileNotFoundError, ValueError, subprocess.SubprocessError):
        pass
    try:
        out = subprocess.run(["ifconfig", iface], capture_output=True, text=True, timeout=2).stdout
        for ip, mask in re.findall(r"inet (\d+\.\d+\.\d+\.\d+) netmask (0x[0-9a-f]+)", out):
            nets.append(ipaddress.ip_network(f"{ip}/{bin(int(mask, 16)).count('1')}", strict=False))
    except (FileNotFoundError, subprocess.SubprocessError):
        pass
    return nets


def host_ok(host):
    """Intestazione Host ammessa: nomi locali o un indirizzo IP (difesa dal «DNS rebinding»)."""
    h = (host or "").rsplit(":", 1)[0].strip("[]").lower()
    if h in ("localhost", "smartbox", "smartbox.local"):
        return True
    try:
        ipaddress.ip_address(h)
        return True
    except ValueError:
        return False


class Access:
    def __init__(self, password_hash=None, iface="wlan0", enabled=False, clock=time.time):
        self.hash, self.iface, self.enabled, self.clock = password_hash, iface, bool(enabled), clock
        self._sessions, self._fails, self._blocked = {}, {}, {}
        self._nets, self._nets_t, self._lock = [], -1e9, threading.Lock()

    # --- chi sta chiedendo
    def kind(self, client_ip):
        """"locale" (tunnel), "remoto" (rete dell'hotspot, serve la password) oppure "rifiutato"."""
        try:
            a = ipaddress.ip_address(client_ip)
        except ValueError:
            return "rifiutato"
        if a.is_loopback:
            return "locale"
        if not (self.enabled and self.hash):
            return "rifiutato"
        if time.monotonic() - self._nets_t > 30:
            self._nets, self._nets_t = local_networks(self.iface), time.monotonic()
        return "remoto" if any(a in n for n in self._nets) else "rifiutato"

    # --- sessioni
    def session_ok(self, cookie_header):
        m = re.search(r"(?:^|;\s*)sbx=([A-Za-z0-9_-]{20,})", cookie_header or "")
        if not m:
            return False
        with self._lock:
            exp = self._sessions.get(m.group(1))
            if exp and exp > self.clock():
                return True
            self._sessions.pop(m.group(1), None)
        return False

    def login(self, client_ip, password):
        """Restituisce (token o None, messaggio)."""
        now = self.clock()
        with self._lock:
            if self._blocked.get(client_ip, 0) > now:
                return None, "Troppi tentativi sbagliati: riprova tra qualche minuto."
        if check_password(password or "", self.hash or ""):
            token = secrets.token_urlsafe(32)
            with self._lock:
                self._sessions[token] = now + SESSION_HOURS * 3600
                self._fails.pop(client_ip, None)
            return token, "ok"
        time.sleep(1.0)                                   # rallenta i tentativi
        with self._lock:
            fails = [t for t in self._fails.get(client_ip, []) if now - t < FAIL_WINDOW] + [now]
            self._fails[client_ip] = fails
            if len(fails) >= MAX_FAILS:
                self._blocked[client_ip] = now + BLOCK_SECONDS
                print(f"[pagina] accesso bloccato per 5 minuti a {client_ip} dopo {len(fails)} tentativi",
                      file=sys.stderr, flush=True)
        return None, "Password sbagliata."

    def logout(self, cookie_header):
        m = re.search(r"(?:^|;\s*)sbx=([A-Za-z0-9_-]+)", cookie_header or "")
        if m:
            with self._lock:
                self._sessions.pop(m.group(1), None)


LOGIN_PAGE = """<!doctype html><html lang="it"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Smartbox · accesso</title>
<style>body{margin:0;font:16px -apple-system,"Segoe UI",Arial,sans-serif;background:#f6f7f9;color:#1d2430;display:flex;
min-height:100vh;align-items:center;justify-content:center}form{background:#fff;border:1px solid #d5dae3;border-radius:12px;
padding:22px;width:min(92vw,360px)}h1{font-size:20px;margin:0 0 6px;color:#13294b}p{margin:0 0 14px;color:#5a6475;font-size:14px}
input,button{font:inherit;width:100%;padding:10px;border-radius:8px;border:1px solid #d5dae3;box-sizing:border-box}
button{margin-top:10px;background:#2b5cab;border-color:#2b5cab;color:#fff}.e{color:#9b2a1f;margin-top:10px;font-size:14px}
@media (prefers-color-scheme:dark){body{background:#11151c;color:#e6e9ef}form{background:#181e27;border-color:#2c3542}
input{background:#11151c;color:#e6e9ef;border-color:#2c3542}h1{color:#9db8ea}}</style></head>
<body><form method="post" action="/login"><h1>Smartbox</h1><p>Pagina del dispositivo: inserisci la password scelta nella
procedura guidata.</p><input type="password" name="password" autocomplete="current-password" autofocus required>
<button>Entra</button>{errore}</form></body></html>"""
