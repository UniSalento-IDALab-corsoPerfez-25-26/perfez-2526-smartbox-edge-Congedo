"""Pagina del dispositivo: video, dati in diretta, taratura, protocolli di prova e soglie (registro D-30, D-36, D-41).

È l'equivalente della demo nel browser, ma con la camera del dispositivo: la pagina (smartbox/web/pagina.html) mostra
l'immagine elaborata (MJPEG), i dati in diretta, i pulsanti Calibra / Protocollo 60 s / Prova sbadigli, le soglie
modificabili e le prove salvate; le istruzioni della prova le pronuncia il browser che guarda la pagina.
Dal PC si raggiunge con un tunnel SSH (ssh -L 8090:127.0.0.1:8090 ...), senza password. Se è attivo l'accesso dal
telefono (D-43, smartbox/access.py) ascolta anche sulla rete dell'hotspot, ma lì chiede la password; ogni altra rete
è rifiutata. Le richieste che cambiano qualcosa vogliono anche l'intestazione X-Smartbox (nessun altro sito può inviarle).
Nessuna immagine viene salvata; le prove contengono solo misure del volto.
"""
import json, os, re, threading, time, http.server, socketserver
from urllib.parse import parse_qs
import cv2
from .access import host_ok, LOGIN_PAGE

WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
PROVA_FILES = ("indicatori.csv", "annotazioni.csv", "eventi.csv", "config_tarata.yaml", "risultato.json")


class PreviewServer:
    def __init__(self, port=8090, on_command=None, on_settings=None, prove_dir=None, access=None):
        self.port, self.on_command, self.on_settings, self.prove_dir = port, on_command, on_settings, prove_dir
        self.access = access                 # smartbox.access.Access: accesso dal telefono (D-43), None = solo 127.0.0.1
        self._jpg, self._lock = None, threading.Lock()
        self.state = {}                      # aggiornato dal ciclo principale: dati in diretta per la pagina

    def publish(self, frame):
        ok, jpg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
        if ok:
            with self._lock:
                self._jpg = jpg.tobytes()

    def _latest(self):
        with self._lock:
            return self._jpg

    def list_prove(self):
        out = []
        d = self.prove_dir
        if not d or not os.path.isdir(d):
            return out
        for name in sorted(os.listdir(d), reverse=True)[:30]:
            p = os.path.join(d, name)
            if not os.path.isdir(p):
                continue
            res = {}
            try:
                res = json.load(open(os.path.join(p, "risultato.json"), encoding="utf-8"))
            except (OSError, ValueError):
                pass
            out.append({"nome": name, "file": [f for f in PROVA_FILES if os.path.exists(os.path.join(p, f))],
                        "tipo": res.get("tipo"), "precisione": res.get("precisione"), "richiamo": res.get("richiamo"),
                        "sbadigli": res.get("sbadigli")})
        return out

    def start(self):
        pv = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, fmt, *a):
                # dal telefono si annotano accessi, pagina principale ed errori (non i dati in diretta, troppo frequenti)
                msg = (fmt % a)[:120]
                if not self.client_address[0].startswith("127.") and (
                        "/api/stato" not in msg and "/stream" not in msg or not re.search(r'" (200|206) ', msg)):
                    print(f"[pagina] {self.client_address[0]} {msg}", flush=True)

            def _gate(self, api=False):
                """True se la richiesta può proseguire; altrimenti risponde lei (rifiuto o pagina di accesso)."""
                ip = self.client_address[0]
                kind = pv.access.kind(ip) if pv.access else ("locale" if ip.startswith("127.") or ip == "::1" else "rifiutato")
                if kind == "rifiutato" or not host_ok(self.headers.get("Host")):
                    self._send(403, {"ok": False, "testo": "accesso non ammesso da questa rete"}); return False
                if kind == "remoto" and not pv.access.session_ok(self.headers.get("Cookie")):
                    if api:
                        self._send(401, {"ok": False, "testo": "serve l'accesso con password"})
                    else:
                        self.send_response(303); self.send_header("Location", "/login"); self.end_headers()
                    return False
                return True

            def _login_page(self, err=""):
                return self._send(200, LOGIN_PAGE.replace("{errore}", f'<div class="e">{err}</div>' if err else "").encode(),
                                  "text/html; charset=utf-8")

            def _send(self, code, body, ctype="application/json; charset=utf-8", extra=None):
                b = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(b)))
                self.send_header("Cache-Control", "no-store")
                for k, v in (extra or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(b)

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                if self.path == "/login" and pv.access:
                    if pv.access.kind(self.client_address[0]) != "remoto" or not host_ok(self.headers.get("Host")):
                        return self._send(403, {"ok": False})
                    pw = parse_qs(self.rfile.read(min(n, 4096)).decode("utf-8", "replace")).get("password", [""])[0]
                    token, txt = pv.access.login(self.client_address[0], pw)
                    if not token:
                        return self._login_page(txt)
                    self.send_response(303)
                    self.send_header("Set-Cookie", f"sbx={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age={12 * 3600}")
                    self.send_header("Location", "/"); self.end_headers(); return
                if not self._gate(api=True):
                    return
                if self.headers.get("X-Smartbox") != "1":
                    return self._send(403, {"ok": False, "testo": "richiesta non ammessa"})
                try:
                    body = json.loads(self.rfile.read(min(n, 65536)) or b"{}")
                except ValueError:
                    return self._send(400, {"ok": False, "testo": "dati non validi"})
                if self.path == "/api/comando":
                    cmd = body.get("comando")
                    if cmd not in ("calibra", "calibra_capo", "protocollo", "sbadigli", "interrompi"):
                        return self._send(400, {"ok": False, "testo": "comando sconosciuto"})
                    pv.on_command(cmd)
                    return self._send(200, {"ok": True})
                if self.path == "/api/impostazioni":
                    ok, txt = pv.on_settings(body.get("azione", "salva"), body.get("valori") or {})
                    return self._send(200 if ok else 400, {"ok": ok, "testo": txt})
                return self._send(404, {"ok": False})

            def do_GET(self):
                if self.path == "/login" and pv.access and pv.access.kind(self.client_address[0]) == "remoto" \
                        and host_ok(self.headers.get("Host")):
                    return self._login_page()
                if self.path == "/logout" and pv.access:
                    pv.access.logout(self.headers.get("Cookie"))
                    self.send_response(303); self.send_header("Set-Cookie", "sbx=; Max-Age=0; Path=/")
                    self.send_header("Location", "/login"); self.end_headers(); return
                if not self._gate(api=self.path.startswith("/api/")):
                    return
                if self.path == "/stream":
                    self.send_response(200)
                    self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=f")
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    try:
                        while True:
                            j = pv._latest()
                            if j:
                                self.wfile.write(b"--f\r\nContent-Type: image/jpeg\r\n\r\n" + j + b"\r\n")
                            time.sleep(0.05)
                    except (BrokenPipeError, ConnectionResetError):
                        return
                if self.path == "/api/stato":
                    return self._send(200, pv.state)
                if self.path == "/api/prove":
                    return self._send(200, pv.list_prove())
                m = re.fullmatch(r"/prove/([0-9A-Za-z_-]+)/([a-z_]+\.(?:csv|yaml|json))", self.path)
                if m and pv.prove_dir and m.group(2) in PROVA_FILES:
                    f = os.path.join(pv.prove_dir, m.group(1), m.group(2))
                    if os.path.isfile(f):
                        return self._send(200, open(f, "rb").read(), "text/plain; charset=utf-8",
                                          {"Content-Disposition": f'attachment; filename="{m.group(1)}_{m.group(2)}"'})
                    return self._send(404, {"ok": False})
                if self.path in ("/", "/index.html"):
                    return self._send(200, open(os.path.join(WEB, "pagina.html"), "rb").read(), "text/html; charset=utf-8")
                return self._send(404, {"ok": False})

        class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
            daemon_threads = True
            allow_reuse_address = True

        def serve():
            try:
                bind = "0.0.0.0" if (self.access and self.access.enabled and self.access.hash) else "127.0.0.1"
                Server((bind, self.port), Handler).serve_forever()
            except OSError as e:
                print(f"[anteprima] non disponibile: {e}", flush=True)

        threading.Thread(target=serve, daemon=True, name="anteprima").start()
        return self
