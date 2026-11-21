"""Informazioni sul dispositivo per i messaggi di stato."""


def cpu_temperature():
    """Temperatura della CPU in °C (Raspberry Pi e Linux), None se non disponibile."""
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            return round(int(f.read()) / 1000, 1)
    except Exception:
        return None
