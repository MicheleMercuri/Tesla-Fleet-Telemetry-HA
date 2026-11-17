"""Impostazioni comuni degli strumenti del PC.

Tutto cio' che riguarda il proprio impianto (dominio, indirizzo di Home Assistant, percorsi delle cartelle condivise,
entita') sta in config.json accanto a questi script: si copia esempio_config.json in config.json e lo si compila.
config.json e' escluso da git (.gitignore). Il VIN non si stampa mai per intero.
"""
import json
import os
import re
from pathlib import Path
from urllib.parse import urlparse

QUI = Path(__file__).resolve().parent
VIN_RE = re.compile(r"\b[A-HJ-NPR-Z0-9]{13}([0-9]{4})\b")


def carica():
    """config.json accanto agli script, oppure il file indicato da TELEMETRIA_CONFIG."""
    p = Path(os.environ.get("TELEMETRIA_CONFIG") or QUI / "config.json")
    if not p.exists():
        raise SystemExit(f"manca {p}: copiare esempio_config.json in config.json e compilarlo (vedi README)")
    return json.loads(p.read_text(encoding="utf-8"))


def percorso(valore):
    """Percorso con ~ e variabili d'ambiente espansi."""
    return Path(os.path.expandvars(os.path.expanduser(str(valore))))


def host_ha(cfg):
    """Nome o indirizzo della macchina di Home Assistant, preso da ha_url."""
    return urlparse(cfg["ha_url"]).hostname


def ws_ha(cfg):
    """Indirizzo del WebSocket di Home Assistant."""
    u = urlparse(cfg["ha_url"])
    return ("wss" if u.scheme == "https" else "ws") + "://" + u.netloc + "/api/websocket"


def token_ha(cfg):
    """Token di accesso di lunga durata di Home Assistant, letto da un file (mai scritto nel codice)."""
    return percorso(cfg["ha_token_file"]).read_text(encoding="utf-8").strip()


def maschera_vin(testo):
    return VIN_RE.sub(lambda m: "..." + m.group(1), testo)


def maschera_host(host):
    """Nome del server abbreviato nelle stampe (te***.org): l'output si puo' incollare senza rivelare il dominio."""
    h = str(host or "")
    parti = h.split(".")
    return (h[:2] + "***." + parti[-1]) if len(parti) > 1 else (h[:2] + "***")
