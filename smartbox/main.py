"""Smartbox v4 – rilevazione sonnolenza del guidatore (ciclo principale, unico per PC e Raspberry, D-38).

Uso:
  python -m smartbox.main                     # sorgente da config.yaml, dati in data/
  python -m smartbox.main --source video.mp4  # file video (test riproducibili)
  python -m smartbox.main --source csi        # Pi Camera Module 3
  python -m smartbox.main --show              # finestra con indicatori a video (solo sviluppo)
  python -m smartbox.main --screen --preview 8090 --data-dir ~/smartbox-dati
                                              # Raspberry con monitor HDMI (lo usa deploy/raspberry/schermo.py)

Modello a thread (tesi §3.12.4):
  acquisizione  -> coda di 1 fotogramma (con la camera si elabora sempre l'ultimo, la latenza non cresce)
  elaborazione  -> questo thread: percezione + logica + score + registro
  attuazione    -> uscite a innesto: buzzer/LED (alerts), telefono (phone), monitor (display), anteprima (preview)
  comunicazione -> EventPublisher (coda SQLite, MQTT/HTTP); posizione dal telefono (gps)
Con un file video l'acquisizione resta sequenziale: nessun fotogramma scartato, risultati riproducibili.

Ogni parte sta in un modulo indipendente; qui si collegano soltanto. Configurazione: smartbox/config.py.
"""
import argparse, csv, json, os, queue, statistics, subprocess, sys, threading, time
from .config import load_config, paths, save_calibration, save_head_calibration
from .sources import open_source, rotate
from .detector import FaceIndicatorExtractor
from .logic import DrowsinessMonitor, Event
from .score import DrowsinessScore
from .calibration import GuidedCalibration, HeadCalibration
from .protocol import ProtocolSession
from . import settings as settings_mod
from .alerts import LocalAlerter
from .phone import PhoneNotifier
from .publisher import EventPublisher, now_iso
from .sysinfo import cpu_temperature
from . import gps as gps_mod
from .access import Access


class CaptureThread(threading.Thread):
    """Legge continuamente dalla camera e tiene solo l'ultimo fotogramma."""

    def __init__(self, source):
        super().__init__(daemon=True, name="acquisizione")
        self.source, self.q = source, queue.Queue(maxsize=1)
        self.failures, self._stop_ev = 0, threading.Event()

    def run(self):
        while not self._stop_ev.is_set():
            ok, frame, t = self.source.read()
            if not ok:
                self.failures += 1; time.sleep(0.05); continue
            self.failures = 0
            try:
                self.q.get_nowait()             # scarta il vecchio
            except queue.Empty:
                pass
            self.q.put((frame, t))

    def stop(self):
        self._stop_ev.set()


class AccMonitor:
    """Segnale ACC del veicolo: quando cade per shutdown_delay_seconds, chiede l'arresto."""

    def __init__(self, pcfg):
        self.pin, self.delay = pcfg["acc_pin"], pcfg["shutdown_delay_seconds"]
        self._dev, self._low_since = None, None
        if self.pin is not None:
            try:
                from gpiozero import DigitalInputDevice
                self._dev = DigitalInputDevice(self.pin)
            except Exception as e:
                print(f"[acc] pin ACC non disponibile ({e})", file=sys.stderr)

    def should_stop(self):
        if not self._dev:
            return False
        if self._dev.value:
            self._low_since = None; return False
        self._low_since = self._low_since or time.monotonic()
        return time.monotonic() - self._low_since >= self.delay


class Calibrator:
    """Taratura guidata durante l'esecuzione: si avvia con il file <dati>/calibra o dal pulsante dell'anteprima."""

    def __init__(self, data_dir):
        self.data_dir, self.trigger = data_dir, paths(data_dir)["calibra"]
        self.request = threading.Event()
        self.head_request = threading.Event()            # «Calibra capo»: solo posizione e chinamento (D-45)
        self.session, self.msg, self.msg_until = None, "", -1.0
        self.kind = "occhi"                              # "occhi" (taratura completa) o "capo"

    def maybe_start(self, t, cal):
        """cal: sezione calibration della configurazione in uso (criteri e durate delle fasi, D-48)."""
        if self.session is None and self.head_request.is_set():
            self.head_request.clear()
            self.session, self.kind = HeadCalibration(t, cal), "capo"
            print("[taratura capo] avviata", flush=True)
            return
        if self.session is None and (self.request.is_set() or os.path.exists(self.trigger)):
            self.request.clear()
            try:
                os.remove(self.trigger)
            except FileNotFoundError:
                pass
            self.session, self.kind = GuidedCalibration(t, cal), "occhi"
            print("[taratura] avviata", flush=True)

    def update(self, ind, t):
        """Restituisce (fase, secondi_rimasti, nuove_soglie): fase None fuori dalla taratura, "esito" per 5 s dopo."""
        if self.session is None:
            return ("esito", 0.0, None) if t < self.msg_until else (None, 0.0, None)
        phase, left = self.session.update(ind, t)
        if phase != "fatto":
            return phase, left, None
        res, err = self.session.result(), self.session.error
        if self.kind == "capo":
            print(f"[taratura capo] {self.session.summary()}", flush=True)
        self.session, self.msg_until = None, t + 5
        if not res:
            self.msg = "non riuscita: " + err
            print(f"[taratura] {self.msg}", flush=True)
            return "esito", 0.0, None
        thr, misure = res
        if self.kind == "capo":
            save_head_calibration(self.data_dir, thr, misure)
            self.msg = (f"capo fatta: dritto {misure['capo_dritto']:+.0f}°, chino {misure['capo_chino']:+.0f}°, "
                        f"soglia chinamento {thr['pitch_nod_threshold']}°")
            print(f"[taratura capo] {self.msg}; misure {misure}", flush=True)
            return "esito", 0.0, thr
        save_calibration(self.data_dir, thr, misure)
        self.msg = f"fatta: soglia {thr['blink_closed_threshold']}, margine {thr['blink_squint_margin']}"
        print(f"[taratura] {self.msg}; misure {misure}", flush=True)
        return "esito", 0.0, thr


def parse_args(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--secrets", default="secrets.yaml")
    ap.add_argument("--data-dir", default="data", help="registro, coda, taratura, impostazioni locali e segreti")
    ap.add_argument("--source", help="indice camera, 'csi' o percorso video (override config)")
    ap.add_argument("--show", action="store_true", help="mostra finestra di debug")
    ap.add_argument("--screen", action="store_true", help="allarme sul monitor del Raspberry (framebuffer)")
    ap.add_argument("--preview", type=int, default=0, metavar="PORTA", help="anteprima su 127.0.0.1:PORTA")
    return ap.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    data_dir = os.path.expanduser(args.data_dir)
    os.makedirs(data_dir, exist_ok=True)
    cfg, calib_date = load_config(args.config, args.secrets, data_dir)
    vcfg = cfg["video"]
    every = max(1, vcfg["process_every_n_frames"])

    extractor = FaceIndicatorExtractor(cfg["detector"])
    monitor = DrowsinessMonitor(cfg)
    score = DrowsinessScore(cfg)             # punteggio 0-1 della Proposta P1 (D-27)
    score_on = score.cfg.get("enabled", True)
    score_decides = score_on and score.cfg.get("decide", False)

    # --- uscite a innesto: ognuna si accende dalla riga di comando o dalla configurazione
    alerter = LocalAlerter(cfg)                                   # buzzer/LED (o console in sviluppo)
    phone = PhoneNotifier(cfg)                                    # notifiche sul telefono via ntfy (D-39)
    publisher = EventPublisher(cfg)                               # coda verso il server
    gps = gps_mod.from_config(cfg)                                # posizione dal telefono (D-37)
    calibrator = Calibrator(data_dir)
    commands = queue.Queue()                                      # comandi dalla pagina del dispositivo (D-41)
    prove_dir = os.path.join(data_dir, "prove")
    protocol, last_result = None, None

    rotation = [cfg["video"]["rotation"] or 0]          # gradi, cambiabili dal vivo dalla pagina (D-44)

    def on_settings(azione, valori):
        if azione == "rotazione":                                 # si applica subito, senza ricaricare la logica
            okk, txt, deg = settings_mod.save_rotation(data_dir, valori.get("gradi"))
            if okk:
                rotation[0] = deg
            return okk, txt
        fn = {"salva": lambda: settings_mod.save(data_dir, valori), "reset": lambda: settings_mod.reset(data_dir),
              "rimuovi_taratura": lambda: settings_mod.remove_calibration(data_dir)}.get(azione)
        if not fn:
            return False, "azione sconosciuta"
        okk, txt = fn()
        if okk:
            commands.put("ricarica")
        return okk, txt

    screen = preview = None
    if args.screen:
        from .display import Screen
        screen = Screen()
    if args.preview:
        from .preview import PreviewServer
        pg = cfg.get("pagina", {})                                # accesso dal telefono attraverso l'hotspot (D-43)
        access = Access(pg.get("password_hash"), pg.get("interface", "wlan0"), pg.get("telefono"))
        preview = PreviewServer(args.preview, on_command=commands.put, on_settings=on_settings, prove_dir=prove_dir,
                                access=access).start()
        if access.enabled:
            print("[pagina] accesso dal telefono " + ("attivo (rete " + access.iface + ", con password)" if access.hash
                                                      else "richiesto ma senza password: resta solo 127.0.0.1"), flush=True)
    drawing = screen is not None or preview is not None
    if drawing:
        from .display import render, message_frame
    acc = AccMonitor(cfg["power"])
    print(f"[avvio] avviso locale: {alerter.health}; soglie "
          f"{'tarate il ' + calib_date if calib_date else 'standard'}; telefono: {phone.health}; dati in {data_dir}", flush=True)

    log_path = cfg["outputs"]["log_csv"]
    new = not os.path.exists(log_path)
    log = open(log_path, "a", newline="", encoding="utf-8")
    wr = csv.writer(log)
    if new:
        wr.writerow(["timestamp", "t_video_s", "level", "prev_level", "reason", "perclos", "ear"])

    def emit(ev, is_file):
        ts = now_iso()
        wr.writerow([ts, round(ev.t, 2) if is_file else "", ev.level, ev.prev_level, ev.reason,
                     round(ev.perclos, 3), round(ev.ear, 3)])
        log.flush()
        publisher.publish(ev, ts)
        when = f"{ev.t:8.1f}s" if is_file else time.strftime("%H:%M:%S")   # file: tempo del video; camera: ora
        print(f"[{when}] livello {ev.prev_level}->{ev.level} – {ev.reason} (PERCLOS {ev.perclos:.2f})", flush=True)

    def show_message(text, sub):
        if drawing:
            fr = message_frame(text, sub)
            if screen: screen.show(fr)
            if preview: preview.publish(fr)

    source, capture = None, None
    n = processed = faces = 0
    proc_ms, t0, last_status, fps_meas = [], time.time(), 0.0, 0.0
    fps_n, fps_t = 0, time.monotonic()      # immagini elaborate contate in ogni secondo
    reason, shutdown = "", False
    stato_path, last_stato = os.path.join(data_dir, "stato.json"), 0.0
    try:
        while True:
            # --- apertura (o riapertura) della sorgente: modalità "senza visione" finché fallisce ---
            if source is None:
                try:
                    source = open_source(vcfg, args.source)
                    if not source.is_file:
                        capture = CaptureThread(source); capture.start()
                    print("[avvio] telecamera aperta", flush=True)
                except Exception as e:
                    src = str(args.source if args.source is not None else vcfg["source"])
                    if not src.isdigit() and src.lower() != "csi":     # un file mancante non si "riconnette"
                        sys.exit(f"Sorgente video non apribile: {e}")
                    if monitor.level < 1:
                        emit(Event(time.monotonic(), 1, "camera non disponibile", 0.0, 0.0, monitor.level), False)
                        monitor.level = 1
                    alerter.set_level(monitor.level)
                    show_message("TELECAMERA NON TROVATA", "collegare la webcam: nuovo tentativo tra 5 s")
                    time.sleep(5); continue

            # --- acquisizione ---
            if source.is_file:
                ok, frame, t = source.read()
                if not ok:
                    break
            else:
                try:
                    frame, t = capture.q.get(timeout=1.0)
                except queue.Empty:
                    if capture.failures > 50:          # camera scollegata: si riprova ad aprirla
                        print("[avvio] telecamera persa, la riapro", flush=True)
                        capture.stop(); source.close(); source = capture = None
                    continue
            n += 1
            if n % every:
                continue

            # --- elaborazione ---
            t_start = time.perf_counter()
            frame = rotate(frame, rotation[0])      # camera montata storta: tutto lavora sull'immagine dritta (D-44)
            ind = extractor(frame, t)
            while True:                             # comandi dalla pagina del dispositivo (D-41)
                try:
                    cmd = commands.get_nowait()
                except queue.Empty:
                    break
                if cmd == "calibra" and protocol is None:
                    calibrator.request.set()
                elif cmd == "calibra_capo" and protocol is None:
                    calibrator.head_request.set()
                elif cmd in ("protocollo", "sbadigli") and protocol is None and calibrator.session is None:
                    monitor, score = DrowsinessMonitor(cfg), DrowsinessScore(cfg)   # la prova parte da zero, come nella demo
                    protocol = ProtocolSession(cmd, t, os.path.join(prove_dir, time.strftime("%Y%m%d-%H%M%S-") + cmd), cfg)
                    last_result = None
                    print(f"[prova] {cmd} avviata", flush=True)
                elif cmd == "interrompi":
                    if protocol is not None:
                        print("[prova] interrotta: niente salvato", flush=True)
                    protocol, calibrator.session = None, None
                elif cmd == "ricarica":
                    cfg, calib_date = load_config(args.config, args.secrets, data_dir)
                    monitor, score = DrowsinessMonitor(cfg), DrowsinessScore(cfg)
                    rotation[0] = cfg["video"]["rotation"] or 0
                    print("[impostazioni] ricaricate dalla pagina", flush=True)
            if protocol is None:
                calibrator.maybe_start(t, cfg["calibration"])
            phase, left, new_thr = calibrator.update(ind, t)      # la taratura vede l'inclinazione grezza del capo
            # inclinazione del capo rispetto alla posizione normale del guidatore misurata in taratura (D-41)
            ind.pitch = ind.pitch - cfg["indicators"]["pitch_baseline"]
            if new_thr is not None:                 # taratura riuscita: si riparte con le soglie del soggetto
                cfg, calib_date = load_config(args.config, args.secrets, data_dir)
                monitor, score = DrowsinessMonitor(cfg), DrowsinessScore(cfg)
            ev = None
            if phase in (None, "esito"):            # durante la taratura gli occhi chiusi sono voluti: logica ferma
                ev = monitor.update(ind, t)
                if score_on:
                    score.update(ind, t)
                if ev:
                    reason = ev.reason
                    if protocol is None:
                        emit(ev, source.is_file)
                    else:                           # prova: niente server né telefono, non è un evento vero
                        print(f"[prova] livello {ev.prev_level}->{ev.level} – {ev.reason}", flush=True)
            if protocol is not None and not protocol.update(ind, t, ev, monitor):
                last_result = protocol.save()
                last_result["cartella"] = os.path.basename(protocol.out_dir)
                print(f"[prova] finita e salvata in {protocol.out_dir}", flush=True)
                protocol = None
            proc_ms.append((time.perf_counter() - t_start) * 1000)
            if len(proc_ms) > 5000:
                proc_ms = proc_ms[-5000:]
            processed += 1; faces += ind.face_found
            # con score.decide il livello dell'allarme locale è il più alto tra regole e punteggio
            level_now = max(monitor.level, score.level()) if score_decides else monitor.level
            alerter.set_level(level_now)
            phone.set_level(level_now if protocol is None else 0, reason)

            now_wall = time.time()
            fps_n += 1
            if time.monotonic() - fps_t >= 1.0:
                fps_meas, fps_n, fps_t = fps_n / (time.monotonic() - fps_t), 0, time.monotonic()
            fix = gps.latest() if gps else None

            # --- heartbeat di stato ---
            if now_wall - last_status >= cfg["forward"]["status_every_seconds"]:
                last_status = now_wall
                mode = "senza visione" if monitor.face_lost else (
                    "completa" if publisher.pending() == 0 else "senza comunicazione")
                publisher.send_status({"level": monitor.level, "mode": mode, "fps": round(fps_meas, 1),
                                       "cpu_temp": cpu_temperature(),
                                       "alert": alerter.health + (f", telefono {phone.health}" if phone.enabled else ""),
                                       "face_ratio": round(faces / max(1, processed), 2),
                                       "perclos": round(monitor.perclos, 3),
                                       "score": round(score.score, 3) if score_on else None,
                                       "calibrated": calib_date,
                                       "speed_kmh": fix.get("speed_kmh") if fix else None})

            # --- stato in tempo reale per la procedura guidata (tools/configura.py, D-40): nessuna immagine ---
            if now_wall - last_stato >= 2:
                last_stato = now_wall
                try:
                    tmp = stato_path + ".tmp"
                    with open(tmp, "w", encoding="utf-8") as f:
                        json.dump({"ts": now_wall, "fps": round(fps_meas, 1), "level": monitor.level, "reason": reason,
                                   "face": bool(ind.face_found), "score": round(score.score, 3),
                                   "calibrated": calib_date, "pending": publisher.pending(),
                                   "alert": alerter.health, "phone": phone.health if phone.enabled else None,
                                   "gps": None if not gps else ({"speed_kmh": fix.get("speed_kmh"), "age_s": fix.get("age_s")}
                                                                if fix else "nessuna posizione recente")}, f)
                    os.replace(tmp, stato_path)
                except OSError:
                    pass

            if acc.should_stop():
                print("[acc] accensione spenta: arresto controllato")
                shutdown = True
                break

            # --- monitor e anteprima ---
            if drawing:
                img = render(frame, {"level": monitor.level, "reason": reason, "score": score.score,
                                     "perclos": monitor.perclos, "fps": fps_meas, "ind": ind,
                                     "calib_date": calib_date, "pending": publisher.pending(),
                                     "gps": fix if gps else "off", "calib_phase": phase,
                                     "calib_left": left, "calib_msg": calibrator.msg,
                                     "prompt": protocol.prompt if protocol else None})
                if screen: screen.show(img)
                if preview:
                    preview.publish(img)
                    g = lambda v, n=3: None if v is None else round(float(v), n)
                    preview.state = {
                        "ts": now_wall, "fps": round(fps_meas, 1), "level": monitor.level, "reason": reason,
                        "perclos": round(monitor.perclos, 3), "score": round(score.score, 3), "face": bool(ind.face_found),
                        "ear": g(ind.ear), "mar": g(ind.mar), "pitch": g(ind.pitch, 1),
                        "blink": g(getattr(ind, "blink", None)), "squint": g(getattr(ind, "squint", None)),
                        "calib_date": calib_date, "calib_phase": phase, "calib_left": round(left, 1), "calib_msg": calibrator.msg,
                        "calib_kind": calibrator.kind,
                        "prova": ({"tipo": protocol.kind, "prompt": protocol.prompt, "say": protocol.say,
                                   "fine": protocol.end} if protocol else None),
                        "ultimo_risultato": last_result, "soglie": settings_mod.current(cfg),
                        "rotazione": rotation[0],
                        "telefono": phone.health if phone.enabled else None, "coda": publisher.pending(),
                        "gps": (fix.get("speed_kmh") if fix else "assente") if gps else None}

            if args.show:
                import cv2
                col = [(0, 180, 0), (0, 200, 255), (0, 0, 255)][monitor.level]
                txt = (f"EAR {ind.ear:.2f} MAR {ind.mar:.2f} pitch {ind.pitch:+.0f} "
                       f"PERCLOS {monitor.perclos:.2f} score {score.score:.2f} L{monitor.level} {fps_meas:.0f}fps") if ind.face_found else "volto non trovato"
                cv2.putText(frame, txt, (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.55, col, 2)
                cv2.imshow("smartbox", frame)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break
    except KeyboardInterrupt:
        pass
    finally:
        if capture: capture.stop()
        if source: source.close()
        extractor.close(); alerter.close(); phone.close(); publisher.close(); log.close()
        if args.show:
            import cv2; cv2.destroyAllWindows()
        el = max(1e-6, time.time() - t0)
        print(f"frame elaborati: {processed}, fps medio: {processed / el:.1f}, "
              f"volto trovato: {100 * faces / max(1, processed):.0f}%")
        if len(proc_ms) >= 20:   # misure richieste dalla tesi §3.15.4
            q = statistics.quantiles(proc_ms, n=20)
            print(f"latenza percezione+logica: media {statistics.mean(proc_ms):.1f} ms, "
                  f"dev.std {statistics.pstdev(proc_ms):.1f} ms, p95 {q[18]:.1f} ms")
        if shutdown and cfg.get("power", {}).get("shutdown_on_acc_off"):
            subprocess.run(["sudo", "shutdown", "-h", "now"])


if __name__ == "__main__":
    main()
