"""Avviso locale: buzzer e LED via GPIO sul Raspberry, con fallback su console (solo sviluppo).

v4: self-test all'avvio (tesi §3.8.2, §3.14). Con buzzer_sense_pin collegato a un circuito di
retroazione il test verifica davvero il buzzer; senza, il risultato è "non verificabile".
"""
import sys, threading, time


class WearableNotifier:
    """Componente 4 della Proposta P1, vibrazione al polso: PREDISPOSTA come optional, non realizzata (D-26).

    Riceve solo i cambi di livello. Per collegare uno smartwatch o una smartband basta implementare
    `_send(level)`: per esempio una notifica BLE diretta dalla Smartbox, oppure un messaggio allo smartphone
    del guidatore (gateway C2), che la inoltra all'orologio. Vedi documentazione/Relazione_confronto_P1_v2.
    Con outputs.wearable.enabled: false (default) non fa nulla; con true, finché `_send` non è implementata,
    scrive soltanto nel registro di esecuzione che cosa avrebbe inviato.
    """
    PATTERNS = {0: None, 1: "vibrazione breve", 2: "vibrazione lunga ripetuta"}

    def __init__(self, cfg):
        w = cfg.get("outputs", {}).get("wearable") or {}
        self.enabled = bool(w.get("enabled", False))
        self.device = w.get("device")
        self._last = 0

    def notify(self, level: int):
        if not self.enabled or level == self._last:
            return
        self._last = level
        if self.PATTERNS.get(level):
            self._send(level)

    def _send(self, level: int):
        print(f"[polso] {self.PATTERNS[level]} (livello {level}) -> {self.device or 'nessun dispositivo collegato'}",
              file=sys.stderr)


class LocalAlerter:
    def __init__(self, cfg):
        out = cfg["outputs"]
        self.beep = out.get("console_beep", True) and sys.stdout.isatty()   # sotto systemd non serve
        self._gpio = None
        self._sense = None
        if out.get("gpio_enabled"):
            try:
                from gpiozero import Buzzer, LED, DigitalInputDevice
                self._gpio = (Buzzer(out["buzzer_pin"]), LED(out["led_pin"]))
                if out.get("buzzer_sense_pin") is not None:
                    self._sense = DigitalInputDevice(out["buzzer_sense_pin"])
            except Exception as e:
                print(f"[alerts] GPIO non disponibile ({e})", file=sys.stderr)
        # "ok" | "non verificabile" | "guasto" | "assente"
        self.health = "non verificabile" if self._gpio else ("solo console" if self.beep else "assente")
        if self._gpio and out.get("self_test_on_start", True):
            self.health = self.self_test()
        self.wearable = WearableNotifier(cfg)    # optional, spento per default (C4, D-26)
        self._level = 0
        self._stop = threading.Event()
        self._thr = threading.Thread(target=self._loop, daemon=True, name="attuazione")
        self._thr.start()

    def self_test(self) -> str:
        """Impulso di prova; con il pin di retroazione controlla che il buzzer assorba corrente."""
        self._set(True); time.sleep(0.15)
        sensed = self._sense.value if self._sense else None
        self._set(False)
        if sensed is None:
            return "non verificabile"
        return "ok" if sensed else "guasto"

    def set_level(self, level: int):
        self._level = level
        self.wearable.notify(level)

    def _loop(self):
        # livello 1: impulso ogni 2 s; livello 2: impulsi rapidi continui
        while not self._stop.is_set():
            lv = self._level
            if lv == 0:
                self._set(False); time.sleep(0.05); continue   # 50 ms: latenza di attivazione bassa
            on = 0.15 if lv == 2 else 0.1
            off = 0.15 if lv == 2 else 1.9
            self._set(True); time.sleep(on); self._set(False)
            self._stop.wait(off) if lv == 2 else self._wait_level_change(lv, off)

    def _wait_level_change(self, lv, seconds):
        # durante la pausa lunga del livello 1, un passaggio a livello 2 interrompe subito l'attesa
        end = time.monotonic() + seconds
        while time.monotonic() < end and self._level == lv and not self._stop.is_set():
            time.sleep(0.05)

    def _set(self, on):
        if self._gpio:
            b, l = self._gpio
            (b.on() if on else b.off()); (l.on() if on else l.off())
        elif on and self.beep:
            print("\a", end="", flush=True)

    def close(self):
        self._stop.set(); self._set(False)
