"""Home Assistant da dentro l'add-on (API del Supervisor, homeassistant_api: true): servizi e stati.

Ogni chiamata gira in un filo di lavoro con una coda: un Home Assistant lento o irraggiungibile non ferma mai la lettura
di MQTT. Il token (SUPERVISOR_TOKEN) lo riceve solo questo processo: il server Fleet Telemetry, esposto su internet, gira
senza.
"""
import json
import os
import queue
import threading
import urllib.error
import urllib.request


class NonRisponde(Exception):
    """Home Assistant non raggiungibile (per esempio mentre riparte): il controllo va riprovato."""


class HA:
    def __init__(self, log, token=None, base=None):
        self.token = os.environ.get("SUPERVISOR_TOKEN", "") if token is None else token
        self.base = (base or os.environ.get("HA_URL") or "http://supervisor/core/api").rstrip("/")
        self.log = log
        self.coda = queue.Queue(maxsize=500)            # letture e registro
        self.coda_notifiche = queue.Queue(maxsize=200)  # notifiche: i loro tentativi non fanno aspettare le letture
        self.attivo = bool(self.token)
        if self.attivo:
            threading.Thread(target=self._lavora, args=(self.coda,), daemon=True, name="ha").start()
            threading.Thread(target=self._lavora, args=(self.coda_notifiche,), daemon=True, name="ha-notifiche").start()

    # ---------------------------------------------------------------- HTTP
    def _richiesta(self, metodo, percorso, corpo=None, timeout=15):
        r = urllib.request.Request(self.base + percorso, method=metodo,
                                   data=None if corpo is None else json.dumps(corpo).encode(),
                                   headers={"Authorization": "Bearer " + self.token, "Content-Type": "application/json"})
        with urllib.request.urlopen(r, timeout=timeout) as risposta:
            testo = risposta.read()
        return json.loads(testo) if testo else None

    def stato(self, entity_id, errori=False):
        """Stato di un'entita' (il dizionario di HA) o None se manca o HA non risponde. Con errori=True, se HA non
        risponde solleva NonRisponde (un'entita' che non esiste resta None). Solo dal filo di lavoro."""
        if not self.attivo or not entity_id:
            return None
        try:
            return self._richiesta("GET", f"/states/{entity_id}", timeout=10)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            self.log(f"Home Assistant: stato di {entity_id} non letto (HTTP {e.code})")
        except (urllib.error.URLError, OSError, ValueError) as e:
            self.log(f"Home Assistant: stato di {entity_id} non letto ({type(e).__name__})")
        if errori:
            raise NonRisponde(entity_id)
        return None

    def stati(self):
        """Tutti gli stati (lista) o None. Solo dal filo di lavoro."""
        if not self.attivo:
            return None
        try:
            return self._richiesta("GET", "/states", timeout=30)
        except (urllib.error.URLError, OSError, ValueError) as e:
            self.log(f"Home Assistant: stati non letti ({type(e).__name__})")
            return None

    def servizio(self, dominio_servizio, dati):
        """Chiama un servizio come notify.mobile_app_telefono o logbook.log. Solo dal filo di lavoro; se fallisce solleva."""
        dominio, _, servizio = str(dominio_servizio).partition(".")
        if not dominio or not servizio:
            raise ValueError(f"servizio '{dominio_servizio}' non nella forma dominio.servizio")
        self._richiesta("POST", f"/services/{dominio}/{servizio}", dati)

    # ---------------------------------------------------------------- coda
    def in_coda(self, compito, notifica=False):
        """Esegue compito(ha) in un filo di lavoro (le notifiche nel loro). False se HA non c'e' o la coda e' piena."""
        if not self.attivo:
            return False
        try:
            (self.coda_notifiche if notifica else self.coda).put_nowait(compito)
        except queue.Full:
            self.log("coda verso Home Assistant piena: una chiamata persa")
            return False
        return True

    def _lavora(self, coda):
        while True:
            compito = coda.get()
            try:
                compito(self)
            except urllib.error.HTTPError as e:
                self.log(f"chiamata a Home Assistant non riuscita: HTTP {e.code} {dettaglio(e)}")
            except Exception as e:  # noqa: BLE001 il filo di lavoro deve sopravvivere a qualunque compito
                self.log(f"chiamata a Home Assistant non riuscita: {type(e).__name__} {str(e)[:120]}")


def dettaglio(errore_http):
    """Il testo di errore che HA manda con un HTTP 4xx o 5xx (per capire un servizio o dati sbagliati)."""
    try:
        return errore_http.read()[:200].decode("utf-8", errors="replace").strip()
    except Exception:  # noqa: BLE001
        return ""
