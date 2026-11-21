"""Avvio della Smartbox sul Raspberry Pi 5 con allarme sul monitor (registro D-30, D-36, D-37, D-38).

Dal D-38 non contiene più logica propria: avvia il ciclo principale unico (smartbox/main.py) con
  --screen              allarme sul monitor HDMI (framebuffer, senza desktop)
  --preview 8090        anteprima con pulsante "Calibra" su 127.0.0.1:8090 (dal PC con un tunnel SSH)
  --data-dir ~/smartbox-dati
                        registro eventi, coda verso il server, taratura, posizioni, impostazioni locali
                        (locale.yaml) e segreti (secrets.yaml): tutto fuori dal repository.
Buzzer/LED (outputs.gpio_enabled), invio al server, GPS dal telefono e spegnimento su ACC si accendono
dalla configurazione, come su qualsiasi altro dispositivo.

Avvio: dal servizio systemd smartbox-schermo.service (vedi LEGGIMI.md in questa cartella).
Altre opzioni di smartbox.main si possono aggiungere in coda, es. --source csi.
"""
import os, sys

# la cartella del repository è due livelli sopra questo file (deploy/raspberry/schermo.py)
PROJECT = os.environ.get("SMARTBOX_DIR",
                         os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, PROJECT)
os.chdir(PROJECT)

from smartbox.main import main

if __name__ == "__main__":
    main(["--screen", "--preview", "8090", "--data-dir", os.path.expanduser("~/smartbox-dati")] + sys.argv[1:])
