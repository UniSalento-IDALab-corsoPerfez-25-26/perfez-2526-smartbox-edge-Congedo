"""Astrazione della sorgente video (tesi §3.12.3): webcam USB, file video, Pi Camera CSI.

Ogni sorgente restituisce (ok, fotogramma BGR, istante in secondi).
Per i file l'istante è n / fps (risultati riproducibili); per le camere è l'orologio monotono.
"""
import sys, time
import cv2

_ROT90 = (None, cv2.ROTATE_90_CLOCKWISE, cv2.ROTATE_180, cv2.ROTATE_90_COUNTERCLOCKWISE)
_MATRICES = {}


def rotate(frame, degrees):
    """Raddrizza l'immagine di una camera montata storta (registro D-44): gradi in senso orario, a passi di 1.

    Si scompone in quarti di giro esatti (cv2.rotate, senza perdita: a 90° e 270° l'immagine diventa verticale)
    più un resto tra −45° e +45° (warpAffine attorno al centro, stesse dimensioni, angoli neri).
    """
    deg = int(round(degrees or 0)) % 360
    if deg == 0:
        return frame
    k, r = divmod(deg + 45, 90)
    r -= 45
    if k % 4:
        frame = cv2.rotate(frame, _ROT90[k % 4])
    if r:
        h, w = frame.shape[:2]
        m = _MATRICES.get((r, w, h))
        if m is None:
            m = _MATRICES[(r, w, h)] = cv2.getRotationMatrix2D((w / 2, h / 2), -r, 1.0)
        frame = cv2.warpAffine(frame, m, (w, h))
    return frame


class FrameSource:
    is_file = False

    def read(self):
        """Restituisce (ok, fotogramma BGR o None, istante in secondi)."""
        raise NotImplementedError

    def close(self):
        pass


class WebcamSource(FrameSource):
    def __init__(self, index, width, height):
        if sys.platform.startswith("linux"):     # Raspberry: V4L2 e MJPG (la webcam USB dà solo MJPG, D-34)
            self._cap = cv2.VideoCapture(index, cv2.CAP_V4L2)
            self._cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        else:
            self._cap = cv2.VideoCapture(index)
        if not self._cap.isOpened():
            self._cap.release()
            raise IOError(f"webcam {index} non apribile")
        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        if not sys.platform.startswith("linux"):
            # niente fotogrammi vecchi accumulati nel driver. Su Linux (V4L2) un solo buffer dimezza gli fps
            # della webcam USB (da circa 9 a 4,4 sul Raspberry, D-38): lì ci pensa già CaptureThread,
            # che tiene solo l'ultimo fotogramma.
            self._cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

    def read(self):
        ok, frame = self._cap.read()
        return ok, frame, time.monotonic()

    def close(self):
        self._cap.release()


class FileSource(FrameSource):
    is_file = True

    def __init__(self, path):
        self._cap = cv2.VideoCapture(path)
        if not self._cap.isOpened():
            raise IOError(f"file video non apribile: {path}")
        fps = self._cap.get(cv2.CAP_PROP_FPS) or 30.0
        self.fps = fps if 5 <= fps <= 120 else 30.0   # i .webm del browser a volte non dichiarano l'fps
        self._n = 0

    def read(self):
        ok, frame = self._cap.read()
        if ok:
            self._n += 1
        return ok, frame, self._n / self.fps

    def close(self):
        self._cap.release()


class CsiSource(FrameSource):
    def __init__(self, width, height):
        from picamera2 import Picamera2   # disponibile solo su Raspberry Pi OS
        self._cam = Picamera2()
        self._cam.configure(self._cam.create_video_configuration(
            main={"size": (width, height), "format": "BGR888"}))
        self._cam.start()

    def read(self):
        frame = self._cam.capture_array()
        return frame is not None, frame, time.monotonic()

    def close(self):
        self._cam.stop()


def open_source(video_cfg, override=None) -> FrameSource:
    src = override if override is not None else video_cfg["source"]
    w, h = video_cfg["width"], video_cfg["height"]
    if str(src).lower() == "csi":
        return CsiSource(w, h)
    if str(src).isdigit():
        return WebcamSource(int(src), w, h)
    return FileSource(str(src))
