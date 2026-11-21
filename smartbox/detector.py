"""Estrazione degli indicatori dal volto: EAR, MAR, pitch della testa.

Usa MediaPipe Face Mesh (468 landmark). Restituisce solo numeri, mai immagini:
le immagini non vengono salvate (vincolo privacy della tesi).

v4: il timestamp dell'API Tasks è il tempo reale del fotogramma (tesi §4.1), non +33 ms fissi.
"""
from dataclasses import dataclass
from typing import Optional
import math
import os, urllib.request
import numpy as np
import cv2
import mediapipe as mp

# MediaPipe ha due API: quella "legacy" (mp.solutions) e quella "Tasks" (FaceLandmarker).
# v4.1: si preferisce Tasks perché fornisce anche le blendshape (palpebra chiusa, occhi stretti),
# usate per distinguere gli occhi chiusi da quelli socchiusi per il sole. Il modello
# face_landmarker.task si scarica una volta sola. La legacy resta come ripiego (solo EAR).
try:
    from mediapipe.tasks import python as _mp_python
    from mediapipe.tasks.python import vision as _mp_vision
    _LEGACY = False
except ImportError:
    _LEGACY = hasattr(mp, "solutions")
_TASK_MODEL_URL = ("https://storage.googleapis.com/mediapipe-models/face_landmarker/"
                   "face_landmarker/float16/1/face_landmarker.task")
_TASK_MODEL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "face_landmarker.task")

# Indici dei landmark MediaPipe Face Mesh
LEFT_EYE = [362, 385, 387, 263, 373, 380]   # p1..p6 nell'ordine della formula EAR
RIGHT_EYE = [33, 160, 158, 133, 153, 144]
MOUTH_TOP, MOUTH_BOTTOM, MOUTH_LEFT, MOUTH_RIGHT = 13, 14, 78, 308
# Punti per la stima della posa (solvePnP): naso, mento, occhio dx/sx esterno, bocca dx/sx
POSE_IDX = [1, 152, 33, 263, 61, 291]
POSE_MODEL = np.array([
    [0.0, 0.0, 0.0],        # punta del naso
    [0.0, -63.6, -12.5],    # mento
    [-43.3, 32.7, -26.0],   # occhio destro (esterno)
    [43.3, 32.7, -26.0],    # occhio sinistro (esterno)
    [-28.9, -28.9, -24.1],  # angolo bocca destro
    [28.9, -28.9, -24.1],   # angolo bocca sinistro
], dtype=np.float64)


@dataclass
class Indicators:
    face_found: bool
    ear: float = 0.0      # Eye Aspect Ratio medio dei due occhi
    mar: float = 0.0      # Mouth Aspect Ratio
    pitch: float = 0.0    # gradi; positivo = capo chinato in avanti
    blink: Optional[float] = None    # blendshape eyeBlink (0-1), media dei due occhi; None con l'API legacy
    squint: Optional[float] = None   # blendshape eyeSquint (0-1)
    jaw: Optional[float] = None      # blendshape jawOpen (0-1), solo registrata


def _dist(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def eye_aspect_ratio(pts):
    """EAR = (|p2-p6| + |p3-p5|) / (2 |p1-p4|)  (Soukupová & Čech, 2016)."""
    p1, p2, p3, p4, p5, p6 = pts
    horiz = _dist(p1, p4)
    if horiz == 0:
        return 0.0
    return (_dist(p2, p6) + _dist(p3, p5)) / (2.0 * horiz)


class FaceIndicatorExtractor:
    def __init__(self, det):
        """det: sezione detector di config.yaml (confidenze minime di MediaPipe, D-48)."""
        conf_det, conf_track = det["min_detection_confidence"], det["min_tracking_confidence"]
        if _LEGACY:
            self._mesh = mp.solutions.face_mesh.FaceMesh(
                max_num_faces=1, refine_landmarks=False,
                min_detection_confidence=conf_det, min_tracking_confidence=conf_track)
        else:
            mp_python, vision = _mp_python, _mp_vision
            if not os.path.exists(_TASK_MODEL_PATH):
                print("[detector] scarico il modello face_landmarker.task (una volta sola)...")
                urllib.request.urlretrieve(_TASK_MODEL_URL, _TASK_MODEL_PATH)
            # il modello si passa come buffer: su Windows model_asset_path non accetta percorsi
            # con caratteri non ASCII (es. cartelle utente con accenti)
            with open(_TASK_MODEL_PATH, "rb") as f:
                model = f.read()
            opts = vision.FaceLandmarkerOptions(
                base_options=mp_python.BaseOptions(model_asset_buffer=model),
                running_mode=vision.RunningMode.VIDEO, num_faces=1,
                min_face_detection_confidence=conf_det, min_tracking_confidence=conf_track,
                output_face_blendshapes=True)
            self._mesh = vision.FaceLandmarker.create_from_options(opts)
        self._blend = {}
        self._last_ms = -1

    def close(self):
        self._mesh.close()

    def _landmarks(self, rgb, t):
        """Restituisce la lista dei landmark (oggetti con .x .y normalizzati) o None.
        Con l'API Tasks salva anche le blendshape del volto in self._blend."""
        self._blend = {}
        if _LEGACY:
            res = self._mesh.process(rgb)
            return res.multi_face_landmarks[0].landmark if res.multi_face_landmarks else None
        # il Tasks API in modalità VIDEO vuole timestamp in ms strettamente crescenti
        ms = max(int(round(t * 1000)), self._last_ms + 1)
        self._last_ms = ms
        res = self._mesh.detect_for_video(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb), ms)
        if res.face_blendshapes:
            self._blend = {c.category_name: c.score for c in res.face_blendshapes[0]}
        return res.face_landmarks[0] if res.face_landmarks else None

    def __call__(self, frame_bgr, t: float) -> Indicators:
        """t = istante del fotogramma in secondi (dalla FrameSource)."""
        h, w = frame_bgr.shape[:2]
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        lm = self._landmarks(rgb, t)
        if lm is None:
            return Indicators(face_found=False)
        pt = lambda i: (lm[i].x * w, lm[i].y * h)

        ear = (eye_aspect_ratio([pt(i) for i in LEFT_EYE]) +
               eye_aspect_ratio([pt(i) for i in RIGHT_EYE])) / 2.0

        mouth_w = _dist(pt(MOUTH_LEFT), pt(MOUTH_RIGHT))
        mar = _dist(pt(MOUTH_TOP), pt(MOUTH_BOTTOM)) / mouth_w if mouth_w else 0.0

        pitch = self._pitch([pt(i) for i in POSE_IDX], w, h)
        b = self._blend
        if "eyeBlinkLeft" in b:
            return Indicators(True, ear, mar, pitch,
                              blink=(b["eyeBlinkLeft"] + b["eyeBlinkRight"]) / 2,
                              squint=(b["eyeSquintLeft"] + b["eyeSquintRight"]) / 2,
                              jaw=b.get("jawOpen"))
        return Indicators(True, ear, mar, pitch)

    @staticmethod
    def _pitch(image_pts, w, h) -> float:
        focal = w
        cam = np.array([[focal, 0, w / 2], [0, focal, h / 2], [0, 0, 1]], dtype=np.float64)
        ok, rvec, _ = cv2.solvePnP(POSE_MODEL, np.array(image_pts, dtype=np.float64),
                                   cam, np.zeros((4, 1)), flags=cv2.SOLVEPNP_ITERATIVE)
        if not ok:
            return 0.0
        rot, _ = cv2.Rodrigues(rvec)
        pitch = math.degrees(math.atan2(-rot[2, 1], rot[2, 2]))
        if pitch > 90: pitch -= 180
        if pitch < -90: pitch += 180
        return -pitch   # chinare il capo in avanti -> valore positivo
