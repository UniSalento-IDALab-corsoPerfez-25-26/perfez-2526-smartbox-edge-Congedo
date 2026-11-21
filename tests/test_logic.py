"""Test di regressione della logica (tesi §4.6 esteso ai casi limite). Uso: python -m pytest -q"""
import copy, os, sys
import yaml
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from smartbox.logic import DrowsinessMonitor


class Ind:   # sostituto leggero di detector.Indicators: i test non richiedono mediapipe
    def __init__(self, face_found, ear=0.3, mar=0.02, pitch=0.0, blink=None, squint=None):
        self.face_found, self.ear, self.mar, self.pitch = face_found, ear, mar, pitch
        self.blink, self.squint = blink, squint   # None = API legacy senza blendshape -> criterio EAR


CFG = yaml.safe_load(open(os.path.join(os.path.dirname(__file__), "..", "config.yaml"), encoding="utf-8"))
CAL = CFG["calibration"]   # criteri della taratura da config.yaml (D-48)
FPS = 20


def make(**alarm):
    cfg = copy.deepcopy(CFG); cfg["alarm"].update(alarm)
    return DrowsinessMonitor(cfg), [0.0]


def run(m, t, seconds, face=True, ear=0.30, mar=0.02, pitch=0.0, blink=None, squint=None):   # MAR a bocca chiusa misurato: 0,00-0,07
    evs = []
    for _ in range(int(round(seconds * FPS))):
        t[0] += 1 / FPS
        e = m.update(Ind(face, ear, mar, pitch, blink, squint), t[0])
        if e:
            evs.append((round(t[0], 1), e.level, e.reason))
    return evs


def test_scenario_tesi_discesa_diretta():
    """Stessa sequenza del §4.6, comportamento del prototipo v3 (gradual_descent: false)."""
    m, t = make(gradual_descent=False)
    assert run(m, t, 10) == []
    assert run(m, t, 2, ear=0.10) == [(11.6, 2, "microsonno")]
    assert run(m, t, 8) == [(17.1, 0, "rientro")]
    assert run(m, t, 1.5, pitch=30) == [(21.1, 2, "chinamento")]


def test_discesa_graduale():
    m, t = make(gradual_descent=True)
    run(m, t, 10); run(m, t, 2, ear=0.10)
    evs = run(m, t, 12)
    assert [(lv, r) for _, lv, r in evs] == [(1, "rientro"), (0, "rientro")]
    assert abs(evs[1][0] - evs[0][0] - 5.0) < 0.11   # ogni gradino attende l'isteresi


def test_battiti_normali_nessun_allarme():
    m, t = make()
    evs = []
    for _ in range(30):                     # ~28 s: un battito di 0,1 s ogni 0,95 s
        evs += run(m, t, 0.85); evs += run(m, t, 0.10, ear=0.10)
    assert evs == []                         # ~10% di chiusura: sotto la soglia di attenzione (15%)


def test_volto_assente_porta_a_livello_1_e_non_scende():
    m, t = make()
    run(m, t, 3)
    evs = run(m, t, 6, face=False)
    assert [(lv, r) for _, lv, r in evs] == [(1, "volto assente")]
    assert 7.9 <= evs[0][0] <= 8.2               # 5 s dopo la perdita del volto (a 3 s)
    assert run(m, t, 20, face=False) == []   # senza dati il livello resta 1
    assert run(m, t, 6)[-1][1:] == (0, "rientro")


def test_sbadigli_ripetuti():
    m, t = make(yawns_per_window_warning=3)
    evs = []
    for _ in range(3):
        evs += run(m, t, 1.5, mar=0.8); evs += run(m, t, 4)
    assert (1, "sbadigli ripetuti") in [(lv, r) for _, lv, r in evs]


def test_sbadiglio_isolato_configurabile():
    m, t = make(yawns_per_window_warning=1)
    assert [(lv, r) for _, lv, r in run(m, t, 1.5, mar=0.8)] == [(1, "sbadigli ripetuti")]


def test_perdita_volto_durante_allarme_non_spegne():
    m, t = make()
    run(m, t, 2, ear=0.10)
    assert m.level == 2
    run(m, t, 10, face=False)
    assert m.level == 2


def test_battito_all_avvio_non_da_perclos():
    """Finestra quasi vuota all'avvio: un battito non deve dare PERCLOS alto (bug visto nella demo)."""
    m, t = make()
    evs = run(m, t, 0.15, ear=0.10) + run(m, t, 0.2)
    assert evs == []
    assert run(m, t, 15) == []


def test_occhi_stretti_durante_sbadiglio_non_sono_microsonno():
    """Sbadigliando gli occhi si chiudono: deve risultare uno sbadiglio, non un microsonno."""
    m, t = make(yawns_per_window_warning=1)
    run(m, t, 12)
    evs = run(m, t, 0.5, ear=0.10, mar=0.30) + run(m, t, 2.5, ear=0.08, mar=0.75) + run(m, t, 6)
    reasons = [r for _, lv, r in evs if lv > 0 and r != "rientro"]
    assert reasons == ["sbadigli ripetuti"]


def test_microsonno_a_bocca_chiusa_resta_microsonno():
    m, t = make()
    run(m, t, 12)
    assert [r for _, _, r in run(m, t, 2, ear=0.08, mar=0.02)] == ["microsonno"]


def test_occhi_chiusi_subito_dopo_lo_sbadiglio_non_sono_microsonno():
    """Dati reali: bocca chiusa a 36,3 s, occhi riaperti a 38,3 s. È la coda dello sbadiglio."""
    m, t = make()
    run(m, t, 12)
    evs = run(m, t, 3, ear=0.10, mar=0.70) + run(m, t, 2.0, ear=0.08) + run(m, t, 5)
    assert [r for _, lv, r in evs if r == "microsonno"] == []


def test_microsonno_dopo_la_tolleranza_dello_sbadiglio_resta_rilevato():
    m, t = make()
    run(m, t, 12)
    evs = run(m, t, 3, ear=0.30, mar=0.70) + run(m, t, 3.0) + run(m, t, 2.0, ear=0.08)
    assert "microsonno" in [r for _, _, r in evs]


def test_sguardo_in_basso_all_avvio_non_da_perclos():
    """Dati reali: 1,5 s di occhi 'chiusi' appena premuto il pulsante -> PERCLOS 17% a 10 s."""
    m, t = make()
    evs = run(m, t, 1.4, ear=0.15) + run(m, t, 25)
    assert evs == []


# --- criterio blendshape (valori misurati nella prova del 10/11/2026) ---
APERTI = dict(ear=0.37, blink=0.05, squint=0.17)


def test_blendshape_occhi_socchiusi_per_il_sole_nessun_allarme():
    """EAR come a occhi chiusi (0,09) ma palpebra 0,54 / occhi stretti 0,58: non è una chiusura."""
    m, t = make()
    run(m, t, 10, **APERTI)
    assert run(m, t, 5, ear=0.09, blink=0.54, squint=0.58) + run(m, t, 5, **APERTI) == []


def test_blendshape_occhi_chiusi_microsonno():
    m, t = make()
    run(m, t, 10, **APERTI)
    assert [r for _, _, r in run(m, t, 3, ear=0.10, blink=0.70, squint=0.21)] == ["microsonno"]


def test_blendshape_testa_china_e_chinamento_non_microsonno():
    m, t = make()
    run(m, t, 10, **APERTI)
    assert [r for _, _, r in run(m, t, 3, ear=0.08, blink=0.57, squint=0.59, pitch=32)] == ["chinamento"]


def test_criterio_ear_se_richiesto_in_configurazione():
    m, t = make()
    m.closure_src = "ear"
    run(m, t, 10, **APERTI)
    assert [r for _, _, r in run(m, t, 3, ear=0.09, blink=0.54, squint=0.58)] == ["microsonno"]


# --- taratura per soggetto delle soglie blendshape (D-23, D-24) ---
from smartbox.calibration import blendshape_thresholds, ear_threshold

# mediane misurate: soggetto 2 (prova 5) e soggetto 1 (prova 4)
SOG2 = dict(aperti=(0.28, 0.55), socchiusi=(0.52, 0.58), chiusi=(0.57, 0.51), chinato=(0.40, 0.56))
SOG1 = dict(aperti=(0.16, 0.19), socchiusi=(0.54, 0.55), chiusi=(0.70, 0.21), chinato=(0.59, 0.61))


def _taratura(sog):
    return blendshape_thresholds([sog["aperti"]] * 30, [sog["chiusi"]] * 15, CAL)


def test_soglie_fisse_non_valgono_per_il_soggetto_2():
    """Prova 5: con 0,60 / 0,25 gli occhi chiusi del soggetto 2 non sono riconosciuti."""
    m, t = make()
    b, s = SOG2["aperti"]; run(m, t, 10, ear=0.19, blink=b, squint=s)
    b, s = SOG2["chiusi"]
    assert run(m, t, 3, ear=0.09, blink=b, squint=s) == []


def test_taratura_riconosce_gli_occhi_chiusi_di_entrambi_i_soggetti():
    for sog in (SOG1, SOG2):
        cfg = copy.deepcopy(CFG)
        cfg["indicators"].update({k: v for k, v in _taratura(sog).items() if k != "misure"})
        m, t = DrowsinessMonitor(cfg), [0.0]
        b, s = sog["aperti"]; assert run(m, t, 10, ear=0.2, blink=b, squint=s) == []
        b, s = sog["socchiusi"]; assert run(m, t, 5, ear=0.1, blink=b, squint=s) == []
        b, s = sog["aperti"]; run(m, t, 15, ear=0.2, blink=b, squint=s)
        b, s = sog["chiusi"]
        assert [r for _, _, r in run(m, t, 3, ear=0.09, blink=b, squint=s)] == ["microsonno"]


def test_taratura_rifiutata_se_occhi_chiusi_non_distinguibili():
    assert blendshape_thresholds([(0.3, 0.5)] * 30, [(0.35, 0.5)] * 15, CAL) is None
    assert blendshape_thresholds([(0.3, 0.5)] * 5, [(0.7, 0.2)] * 15, CAL) is None   # pochi campioni


from smartbox.score import DrowsinessScore


def _score_run(sc, t, seconds, **kw):
    out = 0.0
    for _ in range(int(round(seconds * FPS))):
        t[0] += 1 / FPS
        d = dict(ear=0.3, mar=0.02, pitch=0.0, blink=None, squint=None); d.update(kw)
        out = sc.update(Ind(True, d["ear"], d["mar"], d["pitch"], d["blink"], d["squint"]), t[0])
    return out


def _cfg_sog(sog):
    cfg = copy.deepcopy(CFG)
    cfg["indicators"].update({k: v for k, v in _taratura(sog).items() if k != "misure"})
    return cfg


def test_punteggio_occhi_chiusi_supera_la_soglia_rossa():
    """Occhi chiusi di continuo per 1,5 s -> 0,75 (rosso); il punteggio sale nel tempo."""
    sc, t = DrowsinessScore(_cfg_sog(SOG2)), [0.0]
    b, s = SOG2["aperti"]; assert _score_run(sc, t, 5, blink=b, squint=s) < 0.05
    b, s = SOG2["chiusi"]
    assert 0.55 < _score_run(sc, t, 1.0, blink=b, squint=s) < 0.75   # dopo 1 s: giallo, non ancora rosso
    assert _score_run(sc, t, 0.6, blink=b, squint=s) >= 0.75 and sc.level() == 2


def test_punteggio_battiti_e_socchiusi_restano_bassi():
    sc, t = DrowsinessScore(_cfg_sog(SOG2)), [0.0]
    ba, sa = SOG2["aperti"]; bc, scl = SOG2["chiusi"]
    for _ in range(20):                      # battiti di 0,2 s ogni secondo circa
        _score_run(sc, t, 0.8, blink=ba, squint=sa); v = _score_run(sc, t, 0.2, blink=bc, squint=scl)
        assert v < 0.6
    b, s = SOG2["socchiusi"]
    assert _score_run(sc, t, 5, blink=b, squint=s) < 0.6 and sc.level() == 0


def test_punteggio_capo_chinato_rosso_ma_non_durante_lo_sbadiglio():
    sc, t = DrowsinessScore(CFG), [0.0]
    _score_run(sc, t, 3)
    assert _score_run(sc, t, 2.5, pitch=22, mar=0.6) < 0.6      # testa inclinata sbadigliando (prova 4)
    _score_run(sc, t, 5)
    assert _score_run(sc, t, 1.1, pitch=30) >= 0.75             # capo chinato a bocca chiusa


def test_vibrazione_polso_predisposta_spenta_per_default(capsys):
    from smartbox.alerts import WearableNotifier
    w = WearableNotifier(CFG); w.notify(2)
    assert capsys.readouterr().err == ""                         # spenta: nessun invio
    cfg = copy.deepcopy(CFG); cfg["outputs"]["wearable"] = {"enabled": True}
    w = WearableNotifier(cfg); w.notify(1); w.notify(1); w.notify(2); w.notify(0)
    err = capsys.readouterr().err
    assert err.count("[polso]") == 2 and "vibrazione lunga" in err   # solo i cambi di livello, nulla per lo 0


def test_taratura_ear_p80():
    assert ear_threshold([0.30] * 30, [0.10] * 15, CAL) == 0.14
    assert ear_threshold([0.20] * 30, [0.19] * 15, CAL) is None


# ---------------------------------------------------------------- taratura guidata sul dispositivo (D-36)
def _ind_bs(blink, squint, ear, face=True):
    return Ind(face, ear=ear, mar=0.1, blink=blink, squint=squint)


def test_guided_calibration_soggetto():
    from smartbox.calibration import GuidedCalibration
    g = GuidedCalibration(0.0, CAL)
    t = 0.0
    while g.phase(t)[0] != "fatto":
        name, _ = g.phase(t)
        closed = name == "chiusi"
        g.update(_ind_bs(0.75 if closed else 0.10, 0.20 if closed else 0.15, 0.10 if closed else 0.30), t)
        t += 0.1
    out, misure = g.result()
    # 0,10 + 0,8 x (0,75 - 0,10) = 0,62 ; scarto -0,05 + 0,8 x (0,55 + 0,05) = 0,43
    assert out["blink_closed_threshold"] == 0.62
    assert out["blink_squint_margin"] == 0.43
    assert out["eye_closure_source"] == "blendshape"
    assert "ear_closed_threshold" in out


def test_guided_calibration_senza_volto():
    from smartbox.calibration import GuidedCalibration
    g = GuidedCalibration(0.0, CAL)
    for i in range(120):
        g.update(_ind_bs(None, None, 0.0, face=False), i * 0.1)
    assert g.result() is None and "luce" in g.error


# ---------------------------------------------------------------- posizione dallo smartphone (D-37)
def test_gps_osmand_query():
    from smartbox.gps import parse_fix
    dev, fix = parse_fix("id=abc&lat=40.35&lon=18.17&speed=27&accuracy=5&timestamp=1794830000")
    assert dev == "abc" and fix["lat"] == 40.35 and fix["lon"] == 18.17
    assert fix["speed_kmh"] == 50.0                     # 27 nodi = 50 km/h
    assert fix["ts"] == 1794830000


def test_gps_json_e_form():
    from smartbox.gps import parse_fix
    body = b'{"device_id": "abc", "location": {"coords": {"latitude": 40.3, "longitude": 18.1, "speed": 10, "accuracy": 4}}}'
    dev, fix = parse_fix("", body, "application/json")
    assert dev == "abc" and fix["speed_kmh"] == 36.0     # 10 m/s
    dev, fix = parse_fix("", b"id=abc&lat=40.3&lon=18.1&speed=-1", "application/x-www-form-urlencoded")
    assert dev == "abc" and fix["speed_kmh"] is None      # velocità ignota


def test_gps_token_e_rete():
    from smartbox.gps import GpsReceiver
    rx = GpsReceiver("segreto", iface="interfaccia-inesistente")
    assert rx.handle("127.0.0.1", "/?id=altro&lat=1&lon=2") == 403          # token sbagliato
    assert rx.handle("203.0.113.5", "/?id=segreto&lat=1&lon=2") == 403      # fuori dalla rete dell'hotspot
    assert rx.handle("127.0.0.1", "/?id=segreto") == 400                    # senza posizione
    assert rx.latest() is None
    assert rx.handle("127.0.0.1", "/?id=segreto&lat=1&lon=2&speed=10") == 200
    assert rx.latest()["speed_kmh"] == 18.5
    rx.max_age = -1
    assert rx.latest() is None                                              # posizione troppo vecchia


# ---------------------------------------------------------------- configurazione unica (D-38)
def test_config_livelli(tmp_path):
    from smartbox.config import load_config, save_calibration
    base = os.path.join(os.path.dirname(__file__), "..", "config.yaml")
    d = str(tmp_path)
    cfg, data = load_config(base, "inesistente.yaml", d)
    assert data is None and cfg["indicators"]["blink_closed_threshold"] == 0.60     # soglie standard
    assert cfg["outputs"]["outbox_db"] == os.path.join(d, "outbox.sqlite")           # dati fuori dal repository
    (tmp_path / "locale.yaml").write_text("forward:\n  http:\n    enabled: true\ngps:\n  enabled: true\n")
    (tmp_path / "secrets.yaml").write_text("http:\n  token: t1\ngps:\n  token: g1\n")
    save_calibration(d, {"blink_closed_threshold": 0.651, "blink_squint_margin": 0.329}, {"palpebra_aperti": 0.18})
    cfg, data = load_config(base, "inesistente.yaml", d)
    assert data and cfg["indicators"]["blink_closed_threshold"] == 0.651
    assert cfg["indicators"]["mar_yawn_threshold"] == CFG["indicators"]["mar_yawn_threshold"]   # il resto resta
    assert cfg["forward"]["http"]["enabled"] and cfg["forward"]["http"]["token"] == "t1"
    assert cfg["forward"]["http"]["url"] == CFG["forward"]["http"]["url"]
    assert cfg["gps"] == dict(CFG["gps"], enabled=True, token="g1", log_csv=os.path.join(d, "posizioni.csv"))


# ---------------------------------------------------------------- notifiche sul telefono (D-39)
def _phone(**kw):
    from smartbox.phone import PhoneNotifier
    sent, now = [], [0.0]
    cfg = {"outputs": {"phone": dict(CFG["outputs"]["phone"], enabled=True, **kw)},
           "forward": {"vehicle_id": "veicolo-01"}}
    return PhoneNotifier(cfg, send=lambda p: sent.append(p) or True, clock=lambda: now[0]), sent, now


def test_telefono_solo_quando_sale():
    ph, sent, now = _phone()
    for lv in (0, 0, 1, 1, 1, 2, 2):
        now[0] += 1; ph.set_level(lv, "microsonno" if lv == 2 else "PERCLOS")
    ph.flush()
    assert [p["priority"] for p in sent] == [4, 5]            # ATTENZIONE e PERICOLO, una volta ciascuno
    assert "microsonno" in sent[1]["message"] and sent[1]["topic"] == "smartbox"
    now[0] += 31; ph.set_level(2, "microsonno"); ph.flush()
    assert len(sent) == 3 and "ancora" in sent[2]["title"]     # promemoria dopo 30 s di PERICOLO
    ph.set_level(0); ph.flush()
    assert sent[-1]["priority"] == 2                            # rientro, senza suono
    assert all("lat" not in str(p) and "ear" not in p for p in sent)


def test_telefono_spento_e_errori():
    from smartbox.phone import PhoneNotifier
    off = PhoneNotifier({"outputs": {}, "forward": {}})
    off.set_level(2, "x"); assert off.health == "spento"
    def boom(p): raise OSError("rete assente")
    ph = PhoneNotifier({"outputs": {"phone": dict(CFG["outputs"]["phone"], enabled=True)}, "forward": {"vehicle_id": "v"}}, send=boom)
    ph.set_level(2, "microsonno"); ph.flush()
    assert ph.health == "non raggiungibile" and ph.failed == 1


# ---------------------------------------------------------------- prove sul dispositivo e soglie dalla pagina (D-41)
def test_protocollo_sul_dispositivo(tmp_path):
    from smartbox.protocol import ProtocolSession
    from smartbox.logic import DrowsinessMonitor
    cfg = copy.deepcopy(CFG)
    mon = DrowsinessMonitor(cfg)
    ps = ProtocolSession("protocollo", 0.0, str(tmp_path / "prova"), cfg)
    t, says = 0.0, set()
    while True:
        t += 1 / FPS
        closed = 22.3 <= t <= 25.5                       # occhi chiusi nell'episodio previsto
        nod = 50.3 <= t <= 53.5                          # capo chinato
        ind = Ind(True, ear=0.1 if closed else 0.3, pitch=30.0 if nod else 0.0,
                  blink=0.8 if closed else 0.1, squint=0.2 if closed else 0.1)
        ev = mon.update(ind, t)
        if not ps.update(ind, t, ev, mon):
            break
        says.add(ps.say)
    res = ps.save()
    assert res["vp"] == 2 and res["fp"] == 0 and res["richiamo"] == 1.0
    assert all(c["allarme"] is None for c in res["controlli"])          # socchiusi e sbadiglio: nessun allarme
    assert any("chiudi gli occhi" in s for s in says) and any("Preparati" in s for s in says)
    files = sorted(os.listdir(tmp_path / "prova"))
    assert files == ["annotazioni.csv", "config_tarata.yaml", "eventi.csv", "indicatori.csv", "risultato.json"]
    head = open(tmp_path / "prova" / "indicatori.csv").readline().strip()
    assert head == "t,face,ear,mar,pitch,blink,squint,jaw"               # stesso formato della demo


def test_soglie_dalla_pagina(tmp_path):
    from smartbox import settings
    from smartbox.config import load_config
    base = os.path.join(os.path.dirname(__file__), "..", "config.yaml")
    d = str(tmp_path)
    ok, _ = settings.save(d, {"indicators.blink_closed_threshold": "0.7", "alarm.microsleep_seconds": 2,
                              "alarm.gradual_descent": False})
    assert ok
    cfg, data = load_config(base, "x.yaml", d)
    assert cfg["indicators"]["blink_closed_threshold"] == 0.7 and cfg["alarm"]["microsleep_seconds"] == 2.0
    assert cfg["alarm"]["gradual_descent"] is False and data            # la soglia degli occhi va nella taratura
    assert settings.save(d, {"alarm.microsleep_seconds": 99})[0] is False   # fuori intervallo
    assert settings.save(d, {"video.source": 1})[0] is False                # campo non ammesso
    settings.reset(d)
    cfg, _ = load_config(base, "x.yaml", d)
    assert cfg["alarm"]["microsleep_seconds"] == CFG["alarm"]["microsleep_seconds"]
    assert cfg["indicators"]["blink_closed_threshold"] == 0.7               # la taratura resta
    settings.remove_calibration(d)
    cfg, data = load_config(base, "x.yaml", d)
    assert data is None and cfg["indicators"]["blink_closed_threshold"] == CFG["indicators"]["blink_closed_threshold"]


def test_taratura_misura_posizione_del_capo():
    from smartbox.calibration import GuidedCalibration
    g = GuidedCalibration(0.0, CAL)
    t = 0.0
    while g.phase(t)[0] != "fatto":
        closed = g.phase(t)[0] == "chiusi"
        g.update(Ind(True, ear=0.1 if closed else 0.3, pitch=-8.0, blink=0.75 if closed else 0.1,
                     squint=0.2 if closed else 0.15), t)
        t += 0.1
    out, _ = g.result()
    assert out["pitch_baseline"] == -8.0          # webcam che inquadra dal basso: lo zero del capo è a -8 gradi


# ---------------------------------------------------------------- capo chinato fuori dall'inquadratura (D-42)
def _nod_sequence(m, t, peak, lost_s):
    """Capo dritto, poi scende fino a peak gradi in 0,8 s, poi il volto sparisce per lost_s secondi, poi ritorna dritto."""
    evs = run(m, t, 2.0, pitch=2.0)
    for k in range(int(0.8 * FPS)):
        evs += run(m, t, 1 / FPS, pitch=2.0 + (peak - 2.0) * (k + 1) / (0.8 * FPS))
    evs += run(m, t, lost_s, face=False)
    evs += run(m, t, 3.0, pitch=2.0)
    return evs


def test_chinamento_con_volto_perso():
    # prova del 16/11/2026: il capo arriva a 13 gradi (soglia 10), poi il volto sparisce per 2,8 s
    m, t = make()
    m.pitch_thr = 10.0
    evs = _nod_sequence(m, t, 13.0, 2.8)
    assert any(lv == 2 and r == "chinamento" for _, lv, r in evs), evs


def test_volto_perso_senza_chinamento():
    m, t = make()
    m.pitch_thr = 10.0
    evs = run(m, t, 2.0, pitch=2.0) + run(m, t, 2.8, face=False) + run(m, t, 3.0, pitch=2.0)
    assert not any(r == "chinamento" for _, _, r in evs)          # capo dritto: girarsi o uscire non è un chinamento
    evs = _nod_sequence(m, t, 5.0, 2.8)                            # 5 gradi: sotto il 70% della soglia
    assert not any(r == "chinamento" for _, _, r in evs)


# ---------------------------------------------------------------- pagina dal telefono con password (D-43)
def test_password_e_reti(monkeypatch):
    import ipaddress
    from smartbox import access as A
    h = A.hash_password("prova-lunga-123")
    assert h.startswith("pbkdf2_sha256$") and "prova" not in h
    assert A.check_password("prova-lunga-123", h) and not A.check_password("sbagliata", h)
    monkeypatch.setattr(A, "local_networks", lambda iface: [ipaddress.ip_network("10.52.1.0/24")])
    acc = A.Access(h, "wlan0", enabled=True)
    assert acc.kind("127.0.0.1") == "locale"
    assert acc.kind("10.52.1.246") == "remoto"            # telefono sull'hotspot: serve la password
    assert acc.kind("192.168.1.20") == "rifiutato"        # rete di casa
    assert A.Access(None, "wlan0", enabled=True).kind("10.52.1.246") == "rifiutato"   # senza password: niente accesso
    assert A.host_ok("10.52.1.76:8090") and A.host_ok("localhost:8090") and not A.host_ok("sito-esterno.com")


def test_accesso_e_blocco(monkeypatch):
    from smartbox import access as A
    monkeypatch.setattr(A.time, "sleep", lambda s: None)
    acc = A.Access(A.hash_password("giusta-123"), enabled=True)
    tok, _ = acc.login("10.0.0.5", "giusta-123")
    assert tok and acc.session_ok(f"altro=1; sbx={tok}") and not acc.session_ok("sbx=inventato-xxxxxxxxxxxxxxxxxxxx")
    for _ in range(5):
        assert acc.login("10.0.0.9", "sbagliata")[0] is None
    tok2, msg = acc.login("10.0.0.9", "giusta-123")
    assert tok2 is None and "Troppi" in msg                  # bloccato anche con la password giusta
    acc.logout(f"sbx={tok}")
    assert not acc.session_ok(f"sbx={tok}")


def test_rotazione_immagine():
    """D-44: gradi in senso orario a passi di 1; quarti di giro esatti, il resto attorno al centro."""
    import numpy as np
    from smartbox.sources import rotate
    img = np.zeros((480, 640, 3), np.uint8)
    img[0:10, 0:10] = 255                              # quadratino in alto a sinistra
    assert rotate(img, 0) is img
    r90 = rotate(img, 90)                              # orario: l'angolo in alto a sinistra va in alto a destra
    assert r90.shape == (640, 480, 3) and r90[0:10, -10:].min() == 255
    assert rotate(img, 180)[-10:, -10:].min() == 255
    assert rotate(img, -90).shape == (640, 480, 3) and rotate(img, -90)[-10:, 0:10].min() == 255
    assert np.array_equal(rotate(img, 360), img) and np.array_equal(rotate(img, -270), r90)
    r5 = rotate(img, 5)
    assert r5.shape == img.shape                       # passi piccoli: stesse dimensioni
    line = np.zeros((480, 640), np.uint8)
    line[240, 100:540] = 255                           # riga orizzontale: con +10° (orario) la destra scende
    out = rotate(line[..., None].repeat(3, 2), 10)[..., 0]
    ys_left, ys_right = np.nonzero(out[:, 150])[0], np.nonzero(out[:, 490])[0]
    assert ys_left.mean() < 240 < ys_right.mean()
    assert rotate(img, 92).shape == (640, 480, 3)      # 92 = un quarto di giro + 2


def test_salva_rotazione(tmp_path):
    from smartbox import settings
    from smartbox.config import read_yaml, paths
    ok, _, deg = settings.save_rotation(str(tmp_path), 7)
    assert ok and deg == 7 and read_yaml(paths(str(tmp_path))["locale"])["video"]["rotation"] == 7
    assert settings.save_rotation(str(tmp_path), 190)[2] == -170
    assert settings.save_rotation(str(tmp_path), -180)[2] == 180
    assert settings.save_rotation(str(tmp_path), "x")[0] is False
    settings.reset(str(tmp_path))                      # «Valori di partenza» non cambia l'orientamento
    assert read_yaml(paths(str(tmp_path))["locale"])["video"]["rotation"] == 180


def _ind_pitch(pitch, face=True):
    from types import SimpleNamespace
    return SimpleNamespace(face_found=face, pitch=pitch, ear=0.3, blink=0.1, squint=0.1)


def _run_head(dritto, chino, face_chino=True, dt=0.1):
    from smartbox.calibration import HeadCalibration
    h, t = HeadCalibration(0.0, CAL), 0.0
    while h.phase(t)[0] != "fatto":
        name = h.phase(t)[0]
        h.update(_ind_pitch(chino if name in ("capo_china", "capo_chino") else dritto,
                            face_chino or name != "capo_chino"), t)
        t += dt
    return h, h.result()


def test_taratura_capo():
    """D-45: posizione dritta = zero del capo; soglia = 70% del chinamento misurato, in gradi interi."""
    h, (vals, mis) = _run_head(-11.0, 9.0)
    assert "fotogrammi" in h.summary()
    assert vals == {"pitch_baseline": -11.0, "pitch_nod_threshold": 14} and mis["chinamento"] == 20.0
    assert _run_head(0.0, 5.0)[1] is None                     # chinato troppo poco
    assert _run_head(0.0, 60.0)[1][0]["pitch_nod_threshold"] == 35   # limite superiore
    h, res = _run_head(0.0, 25.0, face_chino=False)            # volto perso a capo chino: misura non valida
    assert res is None and "volto perso" in h.error


def test_taratura_capo_salvataggio(tmp_path):
    """Il capo non tocca le soglie degli occhi; la taratura completa successiva conserva la soglia del capo."""
    from smartbox.config import save_calibration, save_head_calibration, load_config, read_yaml, paths
    from smartbox import settings
    d = str(tmp_path)
    save_calibration(d, {"blink_closed_threshold": 0.53, "pitch_baseline": -10.0}, {})
    save_head_calibration(d, {"pitch_baseline": -12.0, "pitch_nod_threshold": 14}, {"chinamento": 20.0})
    tar = read_yaml(paths(d)["taratura"])
    assert tar["indicators"]["blink_closed_threshold"] == 0.53 and tar["indicators"]["pitch_baseline"] == -12.0
    cfg, data = load_config("config.yaml", "nessuno.yaml", d)
    assert cfg["indicators"]["pitch_nod_threshold"] == 14 and data == tar["data"]
    settings.save(d, {"indicators.pitch_nod_threshold": 16})   # a mano dalla pagina: va nella taratura e conta
    assert load_config("config.yaml", "nessuno.yaml", d)[0]["indicators"]["pitch_nod_threshold"] == 16
    save_calibration(d, {"blink_closed_threshold": 0.6, "pitch_baseline": -9.0}, {})
    tar = read_yaml(paths(d)["taratura"])
    assert tar["indicators"]["pitch_nod_threshold"] == 16 and "data_capo" in tar
    assert tar["indicators"]["pitch_baseline"] == -9.0


# --- parametri da config.yaml, nessun numero nascosto nel codice (D-48) ---
def test_parametro_mancante_segnalato():
    """Se manca un parametro il programma si ferma e dice quale, invece di usare un valore di riserva."""
    import pytest
    from smartbox.config import check
    check(copy.deepcopy(CFG))                       # config.yaml del repository è completa
    cfg = copy.deepcopy(CFG)
    del cfg["alarm"]["yawn_opening_fraction"]; del cfg["calibration"]["head"]["fraction"]
    with pytest.raises(ValueError, match="alarm.yawn_opening_fraction.*calibration.head.fraction"):
        check(cfg)
    with pytest.raises(KeyError):                   # e la logica non ha più valori di riserva
        DrowsinessMonitor(cfg)
    cfg = copy.deepcopy(CFG); del cfg["gps"]["port"]; del cfg["power"]["shutdown_delay_seconds"]
    with pytest.raises(ValueError, match="gps.port.*power.shutdown_delay_seconds"):   # anche le impostazioni del dispositivo
        check(cfg)


def test_criteri_della_taratura_letti_dalla_configurazione():
    """Cambiare la frazione P80 o le fasi in config.yaml cambia la taratura, senza toccare il codice."""
    from smartbox.calibration import GuidedCalibration, HeadCalibration
    cal = copy.deepcopy(CAL)
    assert _taratura(SOG1)["blink_closed_threshold"] == round(0.16 + 0.8 * (0.70 - 0.16), 3)
    cal["fraction"] = 0.5
    assert blendshape_thresholds([SOG1["aperti"]] * 30, [SOG1["chiusi"]] * 15, cal)["blink_closed_threshold"] == 0.43
    cal["phases"] = {"prepara": 1.0, "aperti": 3.0, "chiudi": 4.0, "chiusi": 6.0}
    assert GuidedCalibration(0.0, cal).phase(5.0)[0] == "chiusi" and GuidedCalibration(0.0, CAL).phase(5.0)[0] == "aperti"
    assert HeadCalibration(0.0, CAL).phase(13.0)[0] == "capo_chino"
    cal["phases"]["chiudi"] = 2.0                   # fasi non in ordine: errore esplicito
    import pytest
    with pytest.raises(ValueError):
        GuidedCalibration(0.0, cal)


def test_config_js_allineato():
    """La demo nel browser usa smartbox-dashboard/config.js, generato da config.yaml: deve essere aggiornato."""
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
    from genera_config_js import render, OUT
    import pytest
    if not os.path.exists(OUT):
        pytest.skip("repository dashboard non clonato accanto a questo: config.js non verificabile")
    assert open(OUT, encoding="utf-8").read() == render(CFG), "rilanciare: python tools/genera_config_js.py"
