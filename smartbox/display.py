"""Uscita su monitor: framebuffer del Raspberry e disegno dello stato sull'immagine (registro D-30, D-38).

- Screen: scrive l'immagine direttamente nel framebuffer /dev/fb0 (monitor HDMI senza desktop).
  Se il monitor non c'è il programma prosegue e l'immagine resta disponibile per l'anteprima.
- render(): disegna su un fotogramma 640×480 le misure (in alto), lo stato (in basso: NORMALE verde,
  ATTENZIONE giallo, PERICOLO rosso lampeggiante) e, durante la taratura, le istruzioni.
"""
import time
import cv2
import numpy as np

LEVELS = ("NORMALE", "ATTENZIONE", "PERICOLO")
COLORS = ((0, 160, 0), (0, 200, 255), (0, 0, 255))


class Screen:
    def __init__(self, fb="/dev/fb0"):
        try:
            name = fb.rsplit("/", 1)[-1]
            sysfs = f"/sys/class/graphics/{name}"
            self.w, self.h = map(int, open(f"{sysfs}/virtual_size").read().split(","))
            self.bpp = int(open(f"{sysfs}/bits_per_pixel").read())
            stride = int(open(f"{sysfs}/stride").read())
            self.fb = open(fb, "r+b", buffering=0)
            shape = (self.h, stride // (self.bpp // 8)) + ((4,) if self.bpp == 32 else ())
            self.canvas = np.zeros(shape, dtype=np.uint16 if self.bpp == 16 else np.uint8)
        except Exception as e:
            print(f"[schermo] monitor non disponibile: {e}", flush=True)
            self.fb = None

    def show(self, img):
        if self.fb is None:
            return
        h = self.h
        w = int(img.shape[1] * h / img.shape[0])
        if w > self.w:
            w = self.w
            h = int(img.shape[0] * w / img.shape[1])
        img = cv2.resize(img, (w, h))
        x0, y0 = (self.w - w) // 2, (self.h - h) // 2
        if self.bpp == 16:
            self.canvas[y0:y0 + h, x0:x0 + w] = cv2.cvtColor(img, cv2.COLOR_BGR2BGR565).view(np.uint16).reshape(h, w)
        else:
            self.canvas[y0:y0 + h, x0:x0 + w] = cv2.cvtColor(img, cv2.COLOR_BGR2BGRA)
        try:
            self.fb.seek(0)
            self.fb.write(self.canvas.tobytes())
        except OSError as e:
            print(f"[schermo] scrittura sul monitor non riuscita: {e}", flush=True)


def message_frame(text, sub=""):
    fr = np.zeros((480, 640, 3), np.uint8)
    cv2.rectangle(fr, (0, 0), (639, 479), (0, 200, 255), 14)
    cv2.putText(fr, text, (30, 230), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 200, 255), 2)
    cv2.putText(fr, sub, (30, 270), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)
    return fr


CALIB_TEXT = {
    "prepara": ("TARATURA", "guarda la camera, occhi aperti, posa di guida"),
    "aperti": ("OCCHI APERTI  {left:.0f}", "guarda la camera, non muoverti"),
    "chiudi": ("CHIUDI GLI OCCHI", "tienili chiusi e conta lentamente fino a 5"),
    "chiusi": ("CHIUDI GLI OCCHI", "tienili chiusi e conta lentamente fino a 5"),
    "esito": ("TARATURA", "{msg}"),
    "capo_prepara": ("TARATURA CAPO", "capo dritto, guarda davanti come alla guida"),
    "capo_dritto": ("CAPO DRITTO  {left:.0f}", "resta fermo e guarda davanti"),
    "capo_china": ("CHINA IL CAPO", "in avanti, come addormentandoti, e resta giu"),
    "capo_chino": ("CAPO CHINO  {left:.0f}", "resta giu finche non senti rialza"),
}


def render(frame, st):
    """Disegna lo stato su una copia 640×480 del fotogramma e la restituisce.
    st: level, reason, score, perclos, fps, ind, calib_date, pending (None = invio spento),
        gps ("off" | None | fix), calib_phase, calib_left, calib_msg."""
    h, w = frame.shape[:2]
    if abs(w * 3 - h * 4) > 4:              # immagine verticale (camera ruotata di 90°, D-44): bande nere, niente stiramento
        s = min(640 / w, 480 / h)
        nw, nh = int(w * s), int(h * s)
        fr = np.zeros((480, 640, 3), np.uint8)
        fr[(480 - nh) // 2:(480 - nh) // 2 + nh, (640 - nw) // 2:(640 - nw) // 2 + nw] = cv2.resize(frame, (nw, nh))
    else:
        fr = cv2.resize(frame, (640, 480))
    lv, ind = st["level"], st["ind"]
    col = COLORS[lv]
    blink_on = lv < 2 or int(time.time() * 3) % 2 == 0          # PERICOLO: lampeggia
    if blink_on:
        cv2.rectangle(fr, (0, 0), (639, 479), col, 14 if lv else 4)
    cv2.rectangle(fr, (0, 400), (640, 480), col if blink_on else (0, 0, 0), -1)
    txt = (0, 0, 0) if lv == 1 else (255, 255, 255)
    cv2.putText(fr, f"{LEVELS[lv]}  {st.get('reason', '') if lv else ''}"[:38], (12, 448),
                cv2.FONT_HERSHEY_SIMPLEX, 1.0, txt, 3)
    cal = f"tarato {st['calib_date'][:10]}" if st.get("calib_date") else "soglie standard"
    coda = f"  coda {st['pending']}" if st.get("pending") is not None else ""
    cv2.putText(fr, f"score {st['score']:.2f}  PERCLOS {st['perclos']:.2f}  {cal}{coda}", (12, 472),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, txt, 1)

    cv2.rectangle(fr, (0, 0), (640, 70), (0, 0, 0), -1)
    g = st.get("gps", "off")
    gps_txt = "" if g == "off" else ("   GPS assente" if g is None else
                                     f"   {g['speed_kmh']:.0f} km/h" if g.get("speed_kmh") is not None else "   GPS ok")
    cv2.putText(fr, ("VOLTO TROVATO" if ind.face_found else "VOLTO NON TROVATO") + f"   {st['fps']:.1f} fps{gps_txt}",
                (10, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 200, 0) if ind.face_found else (0, 0, 255), 2)
    if ind.face_found:
        b = ind.blink if ind.blink is not None else 0.0
        s = ind.squint if ind.squint is not None else 0.0
        cv2.putText(fr, f"palpebra {b:.2f}  stretti {s:.2f}  EAR {ind.ear:.2f}  capo {ind.pitch:+.0f} gradi",
                    (10, 58), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)

    if st.get("prompt"):                    # istruzione della prova in corso (D-41)
        cv2.rectangle(fr, (0, 74), (640, 118), (90, 40, 0), -1)
        size = 0.75                         # rimpicciolisce la scritta finché entra nei 640 pixel
        while size > 0.4 and cv2.getTextSize(st["prompt"], cv2.FONT_HERSHEY_SIMPLEX, size, 2)[0][0] > 620:
            size -= 0.05
        cv2.putText(fr, st["prompt"], (12, 104), cv2.FONT_HERSHEY_SIMPLEX, size, (255, 255, 255), 2)
    phase = st.get("calib_phase")
    if phase:
        big, small = CALIB_TEXT[phase]
        big = big.format(left=st.get("calib_left", 0))
        small = small.format(msg=st.get("calib_msg", "")).replace("°", "")   # il carattere ° non c'è nel font
        cv2.rectangle(fr, (0, 150), (640, 330), (60, 30, 0), -1)
        cv2.putText(fr, big, (30, 230), cv2.FONT_HERSHEY_SIMPLEX, 1.6, (255, 255, 255), 4)
        cv2.putText(fr, small[:52], (30, 290), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
    return fr
