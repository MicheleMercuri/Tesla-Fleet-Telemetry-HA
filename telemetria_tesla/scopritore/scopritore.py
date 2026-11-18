"""Scoperta automatica dei sensori della telemetria Tesla in Home Assistant (MQTT discovery) e avvisi, dentro l'add-on.

Legge i messaggi che il server Fleet Telemetry pubblica su MQTT (<topic_base>/<VIN>/v/<campo>, alerts, errors) e:
- pubblica a Home Assistant le configurazioni di discovery (conservate) di tutte le entita' (tabelle.py). Il VIN e' quello
  dell'opzione vin, o quello salvato nel file di stato, o (solo la prima volta) quello del primo messaggio;
- calcola e pubblica i valori derivati: porte e finestrini aperti, navigazione (minuti e km della destinazione da casa,
  calcolati qui dalla zona home di HA, sempre in km), "verso casa", ultimo allarme e ultima allerta della Sentinella,
  ultimo avviso dello schermo, batteria 12V, ultimo errore della telemetria e, se accesi, conteggio della Sentinella dal
  parcheggio e capacita' misurata a ogni ricarica. I valori derivati hanno una loro disponibilita': se lo scopritore si
  ferma, in HA risultano non disponibili (messaggio "testamento" di MQTT);
- manda le notifiche scelte con un servizio di HA (opzione notifica): allarme e allerta fuori casa della Sentinella,
  chiave nuova abbinata, avvisi dello schermo tradotti (dizionario telemetria_avvisi_model3.json, con le regole
  dell'app AppDaemon di prima), scadenza della configurazione dell'auto, telemetria ferma; e scrive avvisi ed errori
  nel registro di HA.
Nessun evento nasce da un valore conservato che MQTT riconsegna all'avvio o al ricollegamento: la Sentinella conta solo
i cambi tra due stati noti nella stessa connessione; marcia, carica e chiavi si confrontano con l'ultimo valore salvato.
Una sola auto: un altro VIN viene ignorato (con una riga nel log). Stato tra un riavvio e l'altro in FILE_STATO.

Impostazioni da variabili d'ambiente (le mette run.sh dalle opzioni dell'add-on e dal Supervisor):
MQTT_HOST, MQTT_PORT, MQTT_USER, MQTT_PASS, TOPIC_BASE, DISCOVERY_PREFIX, PREFISSO, NOME, ID_DISPOSITIVO, MODELLO, VIN,
FILE_STATO, TOGLI e CONFERMA_TOGLI; NOTIFICA (dominio.servizio), NOTIFICA_DATI (JSON), AVVISI_SENTINELLA,
AVVISO_CHIAVE_NUOVA, AVVISI_SCHERMO, SCADENZA_CONFIGURAZIONE (AAAA-MM-GG), CONTACHILOMETRI_CONFRONTO, ORE_SENZA_DATI,
ENTITA_PAUSA_CARICA (elenco separato da virgole), MISURA_CAPACITA, CONTATORE_WALLBOX, CONTEGGIO_PARCHEGGIO, REGISTRO;
SUPERVISOR_TOKEN (dal Supervisor) per parlare con HA.
"""
import json
import math
import os
import re
import sys
import threading
import time
import urllib.error
from datetime import date, datetime, timezone
from pathlib import Path

import tabelle
from ha_api import HA, NonRisponde, dettaglio

QUI = Path(__file__).resolve().parent
VERSIONE = "0.3.3"
VIN_RE = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")
PORTE = {"DriverFront": "Guida", "DriverRear": "Post. guida", "PassengerFront": "Passeggero", "PassengerRear": "Post. passeggero",
         "TrunkFront": "Frunk", "TrunkRear": "Bagagliaio"}
FINESTRINI = {"FdWindow": "Guida", "FpWindow": "Passeggero", "RdWindow": "Post. guida", "RpWindow": "Post. passeggero"}
APERTI = ("WindowStatePartiallyOpen", "WindowStateOpened")
RECENTE_S = 900            # avvisi ed errori: solo quelli di meno di 15 minuti contano come nuovi
DOPPIONI_S = 3600          # la stessa consegna di un avviso entro un'ora conta una volta
RIPETI_S = 6 * 3600        # lo stesso testo si notifica al massimo ogni 6 ore (con suono passa se prima era muto)
ATTESA_RICARICA_S = int(os.environ.get("ATTESA_RICARICA_S", "120"))  # avvisi di ricarica: solo se ancora attivi dopo 2 minuti
ATTESA_CONTATORE_S = 360   # allo stacco del cavo si aspetta fino a 6 minuti il valore finale dell'energia caricata
PAUSA_S = 180              # entita' di pausa della carica accesa ora o negli ultimi 3 minuti
ALLERTA_OGNI_S = 900       # allerta della Sentinella fuori casa: una notifica ogni 15 minuti al massimo
ORA_CONTROLLI = 9          # scadenza e telemetria ferma: una volta al giorno dalle 9 (ritentati se HA non risponde)
RIPROVA_S = 300            # controllo giornaliero non riuscito perche' HA non rispondeva: si riprova dopo 5 minuti
ATTESE_NOTIFICA = (20, 60, 120)  # una notifica non riuscita si riprova dopo 20 s, 60 s e 120 s (copre HA che riparte)
OPZIONI_NON_VALIDE = 78    # codice di uscita per opzioni sbagliate: run.sh non fa ripartire lo scopritore
ENTITA_SCADENZA = "input_datetime.telemetria_tesla_scadenza"  # la scrive tools/configura_auto.py quando l'auto accetta la configurazione
DERIVATI = ("porte_aperte", "finestrini_aperti", "navigazione", "ultimo_allarme", "ultima_allerta", "ultimo_avviso", "batteria_12v",
            "ultimo_errore", "sentinella_parcheggio", "capacita_misurata", "potenza_batteria")
NAVIGAZIONE = ("MinutesToArrival", "DestinationLocation")
POTENZA = ("PackVoltage", "PackCurrent")  # potenza della batteria = tensione per corrente
ORIGINE = {"name": "Telemetria Tesla", "sw_version": VERSIONE, "support_url": "https://github.com/MicheleMercuri/Tesla-Fleet-Telemetry-HA"}


def maschera(vin):
    return "..." + str(vin)[-4:] if vin else "-"


def quando_ts(testo):
    """Ora ISO di Tesla (con Z) in secondi; 0 se non leggibile."""
    try:
        return datetime.fromisoformat(str(testo).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return 0


def ora_locale(ts, formato="%d/%m %H:%M"):
    return datetime.fromtimestamp(ts).strftime(formato)


def numero(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def km_tra(lat1, lon1, lat2, lon2):
    """Distanza in km sulla sfera (haversine)."""
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def kwh(stato):
    """Valore di un contatore di energia di HA in kWh (anche se e' in Wh o MWh), o None."""
    if not stato:
        return None
    x = numero(stato.get("state"))
    unita = str((stato.get("attributes") or {}).get("unit_of_measurement") or "").strip()
    if x is None:
        return None
    return x / 1000 if unita == "Wh" else x * 1000 if unita == "MWh" else x


def coordinate(v):
    """(lat, lon) da un valore Location della telemetria, o None (anche per 0,0)."""
    if not isinstance(v, dict):
        return None
    lat, lon = numero(v.get("latitude")), numero(v.get("longitude"))
    if lat is None or lon is None or (lat == 0 and lon == 0):
        return None
    return lat, lon


class NessunHA:
    """Senza token del Supervisor: niente notifiche, registro ne' stati di HA."""
    attivo = False

    def in_coda(self, compito, notifica=False):
        return False

    def stato(self, entity_id, errori=False):
        return None

    def stati(self):
        return None


class Scopritore:
    def __init__(self, pubblica, opzioni, dizionario, file_stato, log=print, ora=time.time, ha=None):
        self.pubblica = pubblica  # funzione(topic, payload, retain) -> oggetto con is_published() o None
        self.base = str(opzioni["topic_base"]).strip("/")
        self.disc = str(opzioni["discovery_prefix"]).strip("/")
        if not re.fullmatch(r"[A-Za-z0-9_-]+", self.base) or not re.fullmatch(r"[A-Za-z0-9_-]+", self.disc):
            raise ValueError("topic_base e discovery_prefix: un solo livello, senza /")
        p = str(opzioni["prefisso"])
        self.P = p if p.endswith("_") else p + "_"
        if not re.fullmatch(r"[a-z0-9_]+", self.P) or self.P == "_":
            raise ValueError("prefisso: solo lettere minuscole, cifre e _")
        self.nodo = self.P.rstrip("_")
        self.nome = opzioni["nome"]
        self.id_disp = opzioni.get("id_dispositivo") or f"telemetria_tesla_{self.nodo}"
        self.modello = opzioni.get("modello") or "Fleet Telemetry"
        self.diz = dizionario
        self.file_stato = Path(file_stato)
        self.log = log
        self.ora = ora
        self.ha = ha or NessunHA()
        self.blocco = threading.RLock()  # un solo filo alla volta sullo stato (MQTT, controlli periodici, compiti verso HA)
        # opzioni delle notifiche e delle misure
        self.servizio_notifica = (opzioni.get("notifica") or "").strip()
        if self.servizio_notifica and not re.fullmatch(r"[a-z0-9_]+\.[a-z0-9_]+", self.servizio_notifica):
            raise ValueError("notifica: un servizio di HA nella forma dominio.servizio (per esempio notify.mobile_app_telefono)")
        self.dati_notifica = opzioni.get("notifica_dati") or {}
        if not isinstance(self.dati_notifica, dict):
            raise ValueError("notifica_dati: un oggetto JSON, per esempio {\"target\": \"...\"}")
        self.avvisi_sentinella = bool(opzioni.get("avvisi_sentinella", False))
        self.avviso_chiave = bool(opzioni.get("avviso_chiave_nuova", False))
        self.avvisi_schermo = bool(opzioni.get("avvisi_schermo", False))
        # scadenza della configurazione dell'auto: una data, o un'entita' (predefinita quella che scrive configura_auto.py)
        self.scadenza, self.entita_scadenza = None, ENTITA_SCADENZA
        v = str(opzioni.get("scadenza_configurazione") or "").strip()
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
            try:
                self.scadenza = date.fromisoformat(v)
            except ValueError as e:
                raise ValueError("scadenza_configurazione: data non valida") from e
        elif re.fullmatch(r"[a-z_]+\.[a-z0-9_]+", v):
            self.entita_scadenza = v
        elif v:
            raise ValueError("scadenza_configurazione: una data AAAA-MM-GG o un'entita' come input_datetime.telemetria_tesla_scadenza")
        self.confronto_km = (opzioni.get("contachilometri_confronto") or "").strip()
        self.ore_senza_dati = int(opzioni.get("ore_senza_dati") or 0)
        self.entita_pausa = [e.strip() for e in (opzioni.get("entita_pausa_carica") or []) if e and e.strip()]
        self.misura_capacita = bool(opzioni.get("misura_capacita", False))
        self.contatore_wallbox = (opzioni.get("contatore_wallbox") or "").strip()
        self.conteggio_parcheggio = bool(opzioni.get("conteggio_parcheggio", False))
        # avvisi, rientri ed errori nel registro (logbook) di HA; spento se un'altra parte di HA li scrive gia'
        self.scrivi_registro = bool(opzioni.get("registro", True))
        self.stato = self.carica()
        vin = (opzioni.get("vin") or "").strip().upper()
        if vin and not VIN_RE.match(vin):
            raise ValueError("vin non valido (17 caratteri)")
        self.vin = vin or self.stato.get("vin") or None
        if vin:
            self.stato["vin"] = vin
        self.disponibile = f"{self.base}/scopritore/{self.nodo}/stato"
        self.altri_vin = set()
        self.ultimi = {}
        self.agg_ts = {}           # ora dell'ultimo messaggio nuovo (non conservato) di ogni campo
        self.sentinella = None     # stato noto in questa connessione (None = sconosciuto)
        self.pubblicati = {}       # ultimo valore pubblicato per ogni valore derivato (si ripubblica solo se cambia)
        self.fin_completi = False  # i finestrini si pubblicano dalla prima volta che sono noti tutti e 4
        self.in_attesa = {}        # avvisi di ricarica in attesa dei 2 minuti: nome -> dati
        self.casa = None           # (lat, lon, raggio in m) dalla zona home di HA
        self.ultima_posizione = None  # ultima Location valida: un valore nullo o 0,0 non la cancella
        self.casa_prossima = 0.0   # prossima lettura della zona home (ogni ora; dopo un minuto se HA non rispondeva)
        self.riprova = {}          # controllo giornaliero -> ora del prossimo tentativo (HA non rispondeva)
        self.da_salvare = False
        self.salvato = 0.0

    # ------------------------------------------------------------------ stato su file
    def carica(self):
        try:
            s = json.loads(self.file_stato.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            s = {}
        s = s if isinstance(s, dict) else {}

        def diz(nome, filtro):
            v = s.get(nome)
            return {k: x for k, x in v.items() if isinstance(k, str) and filtro(x)} if isinstance(v, dict) else {}

        def voce(nome):
            v = s.get(nome)
            return v if isinstance(v, dict) else {}

        def attivo(x):
            # 0.2: solo l'ora locale; dalla 0.3 anche l'inizio originale (per non far chiudere un avviso nuovo da una fine vecchia)
            if isinstance(x, str):
                return {"inizio": None, "ora": x}
            if isinstance(x, dict) and isinstance(x.get("ora"), str):
                return {"inizio": x.get("inizio") if isinstance(x.get("inizio"), str) else None, "ora": x["ora"]}
            return None

        vin = s.get("vin") if isinstance(s.get("vin"), str) and VIN_RE.match(s.get("vin")) else None
        attivi = {k: a for k, a in ((k, attivo(x)) for k, x in (s.get("attivi") or {}).items() if isinstance(k, str)) if a}
        testo = lambda n: s.get(n) if isinstance(s.get(n), str) else None  # noqa: E731
        return {"vin": vin, "attivi": attivi, "visti": diz("visti", lambda x: isinstance(x, (int, float))), "ultimo": voce("ultimo"),
                "allarme": voce("allarme"), "allerta": voce("allerta"), "errore": voce("errore"),
                "inviati": diz("inviati", lambda x: isinstance(x, str)), "marcia": testo("marcia"), "carica": testo("carica"),
                "chiavi": s.get("chiavi") if isinstance(s.get("chiavi"), int) else None, "km": numero(s.get("km")),
                "parcheggio": voce("parcheggio"), "sessione": voce("sessione"), "capacita": voce("capacita"),
                "ultimo_dato": numero(s.get("ultimo_dato")) or 0.0, "giorni": diz("giorni", lambda x: isinstance(x, str)),
                "allerta_notificata": numero(s.get("allerta_notificata")) or 0.0}

    def salva(self):
        try:
            tmp = self.file_stato.with_name(self.file_stato.name + ".tmp")
            tmp.write_text(json.dumps(self.stato, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self.file_stato)
            self.da_salvare, self.salvato = False, self.ora()
        except OSError as e:
            self.log(f"stato non salvato ({type(e).__name__}): si continua")

    # ------------------------------------------------------------------ scoperta
    def derivato(self, nome):
        return f"{self.base}/{self.vin}/derivati/{self.nodo}/{nome}"

    def dispositivo(self):
        return {"identifiers": [self.id_disp], "name": self.nome, "manufacturer": "Tesla", "model": self.modello, "sw_version": VERSIONE}

    def configurazioni(self, tutte=False):
        """(componente, id, configurazione) delle entita' dell'auto; tutte=True anche quelle delle misure spente (per toglierle)."""
        dev = self.dispositivo()
        P, d = self.P, self.derivato
        disp = {"availability_topic": self.disponibile, "payload_available": "online", "payload_not_available": "offline"}
        out = []
        for comp, voci in tabelle.mqtt(self.vin, self.P, self.base, dev).items():
            for c in voci:
                i = c["unique_id"][len(self.P):]
                if i == "destinazione_distanza":
                    # km da casa calcolati qui (zona home di HA, sempre in km), nello stesso messaggio dei minuti
                    c["state_topic"] = d("navigazione")
                    c["value_template"] = "{% set v = value_json %}{{ v.km_da_casa if v is mapping and v.km_da_casa is not none else 'None' }}"
                    c.update(disp)
                out.append((comp, i, c))
        json_stato = "{{ value_json.stato if value_json is mapping and value_json.stato is not none else 'None' }}"
        derivati = [
            ("sensor", "porte_aperte", {"name": "Porte aperte", "state_topic": d("porte_aperte"), "value_template": json_stato,
                                         "icon": "mdi:car-door"}),
            ("sensor", "finestrini_aperti", {"name": "Finestrini aperti", "state_topic": d("finestrini_aperti"),
                                              "value_template": json_stato, "icon": "mdi:car-door-lock"}),
            ("binary_sensor", "verso_casa", {
                # minuti e km da casa nello stesso messaggio; minuti arrotondati come il sensore dei minuti all'arrivo
                "name": "Navigazione verso casa", "state_topic": d("navigazione"),
                "value_template": ("{% set v = value_json %}{% if v is mapping and v.km_da_casa is not none %}"
                                   "{{ 'ON' if (v.km_da_casa | float(99)) < 0.3 and (v.minuti | float(0) | round(0)) > 0 else 'OFF' }}"
                                   "{% else %}None{% endif %}"),
                "payload_on": "ON", "payload_off": "OFF", "icon": "mdi:home-import-outline"}),
            ("sensor", "ultimo_allarme", {"name": "Ultimo allarme Sentinella", "state_topic": d("ultimo_allarme"),
                                           "value_template": "{{ value_json.quando if value_json is mapping else 'None' }}",
                                           "json_attributes_topic": d("ultimo_allarme"),
                                           "json_attributes_template": "{{ {'dove': value_json.dove} | tojson }}",
                                           "device_class": "timestamp", "icon": "mdi:shield-alert"}),
            ("sensor", "ultima_allerta", {"name": "Ultima allerta Sentinella", "state_topic": d("ultima_allerta"),
                                           "value_template": "{{ value_json.quando if value_json is mapping else 'None' }}",
                                           "json_attributes_topic": d("ultima_allerta"),
                                           "json_attributes_template": "{{ {'dove': value_json.dove} | tojson }}",
                                           "device_class": "timestamp", "icon": "mdi:shield-search"}),
            ("sensor", "ultimo_avviso", {"name": "Ultimo avviso dell'auto", "state_topic": d("ultimo_avviso"),
                                          "value_template": "{{ value_json.testo | truncate(250, true, '') }}",
                                          "json_attributes_topic": d("ultimo_avviso"),
                                          "json_attributes_template": ("{{ {'seconda_riga': value_json.seconda_riga, 'gruppo': value_json.gruppo, "
                                                                       "'codice': value_json.codice, 'quando': value_json.quando, "
                                                                       "'importanza': value_json.importanza} | tojson }}"),
                                          "icon": "mdi:car-info"}),
            ("sensor", "batteria_12v", {"name": "Batteria 12V", "state_topic": d("batteria_12v"),
                                         "value_template": "{{ value_json.stato }}", "json_attributes_topic": d("batteria_12v"),
                                         "json_attributes_template": "{{ {'dettaglio': value_json.dettaglio, 'attivi': value_json.attivi} | tojson }}",
                                         "icon": "mdi:car-battery"}),
            ("sensor", "ultimo_errore", {"name": "Ultimo errore della telemetria", "state_topic": d("ultimo_errore"),
                                          "value_template": "{{ value_json.stato | truncate(250, true, '') }}",
                                          "json_attributes_topic": d("ultimo_errore"),
                                          "json_attributes_template": "{{ {'quando': value_json.quando} | tojson }}",
                                          "icon": "mdi:alert-circle-outline"}),
            ("sensor", "potenza_batteria", {"name": "Potenza della batteria", "state_topic": d("potenza_batteria"),
                                             "value_template": json_stato, "unit_of_measurement": "kW", "device_class": "power",
                                             "state_class": "measurement", "suggested_display_precision": 1, "icon": "mdi:flash"}),
        ]
        if self.conteggio_parcheggio or tutte:
            derivati.append(("sensor", "sentinella_parcheggio", {
                "name": "Sentinella dal parcheggio", "state_topic": d("sentinella_parcheggio"), "value_template": json_stato,
                "json_attributes_topic": d("sentinella_parcheggio"),
                "json_attributes_template": ("{{ {'allerte': value_json.allerte, 'parcheggiata_dalle': value_json.parcheggiata_dalle, "
                                             "'dove': value_json.dove} | tojson }}"),
                "icon": "mdi:shield-car"}))
        if self.misura_capacita or tutte:
            derivati.append(("sensor", "capacita_misurata", {
                "name": "Capacita' misurata alla ricarica", "state_topic": d("capacita_misurata"), "value_template": json_stato,
                "json_attributes_topic": d("capacita_misurata"),
                "json_attributes_template": ("{% set v = value_json %}{{ {'ultima_esito': v.get('ultima_esito'), "
                                             "'ultima_kwh_batteria': v.get('ultima_kwh_batteria'), "
                                             "'ultima_kwh_usati_a_cavo': v.get('ultima_kwh_usati_a_cavo'), 'ultima_punti': v.get('ultima_punti'), "
                                             "'ultima_kwh_wallbox': v.get('ultima_kwh_wallbox'), 'ultima_perdite_pct': v.get('ultima_perdite_pct'), "
                                             "'ultima_data': v.get('ultima_data'), 'misure': v.get('misure') or []} | tojson }}"),
                "unit_of_measurement": "kWh", "device_class": "energy_storage", "state_class": "measurement",
                "icon": "mdi:battery-check-outline"}))
        for comp, i, c in derivati:
            c.update(unique_id=P + i, default_entity_id=f"{comp}.{P}{i}", device=dev, **disp)
            out.append((comp, i, c))
        for _, _, c in out:
            c["origin"] = ORIGINE
        return out

    def topic_config(self, comp, i):
        return f"{self.disc}/{comp}/{self.nodo}/{i}/config"

    def pubblica_scoperta(self):
        conf = self.configurazioni()
        for comp, i, c in conf:
            self.pubblica(self.topic_config(comp, i), json.dumps(c, ensure_ascii=False), True)
        if not self.conteggio_parcheggio:
            self.pubblica(self.topic_config("sensor", "sentinella_parcheggio"), "", True)
        if not self.misura_capacita:
            self.pubblica(self.topic_config("sensor", "capacita_misurata"), "", True)
        self.pubblica(self.disponibile, "online", True)
        self.log(f"scoperta pubblicata: {len(conf)} entita' per l'auto {maschera(self.vin)}")
        self.ripubblica_derivati()

    def messaggi_rimozione(self):
        """(topic, "") per togliere da HA tutte le entita', i valori derivati conservati e la disponibilita'."""
        out = [(self.topic_config(comp, i), "") for comp, i, _ in self.configurazioni(tutte=True)]
        if self.vin:
            out += [(self.derivato(n), "") for n in DERIVATI]
        out.append((self.disponibile, ""))
        return out

    def ripubblica_derivati(self):
        """Dopo un riavvio: i valori derivati com'erano (dal file di stato)."""
        ultimo = self.stato["ultimo"] or {"testo": "Nessun avviso", "seconda_riga": "-", "gruppo": "-", "codice": "-", "quando": None,
                                          "importanza": "-"}
        self.pubblica_derivato("ultimo_avviso", ultimo, forza=True)
        for chiave, nome in (("allarme", "ultimo_allarme"), ("allerta", "ultima_allerta"), ("errore", "ultimo_errore")):
            if self.stato[chiave]:
                self.pubblica_derivato(nome, self.stato[chiave], forza=True)
        self.pubblica_12v(forza=True)
        if self.conteggio_parcheggio:
            self.pubblica_parcheggio(forza=True)
        if self.misura_capacita:
            self.pubblica_derivato("capacita_misurata", self.stato["capacita"] or {"stato": None, "ultima_esito": "nessuna misura ancora"},
                                   forza=True)

    def pubblica_derivato(self, nome, dati, forza=False):
        if not self.vin:
            return
        testo = json.dumps(dati, ensure_ascii=False)
        if not forza and self.pubblicati.get(nome) == testo:
            return
        self.pubblicati[nome] = testo
        self.pubblica(self.derivato(nome), testo, True)

    # ------------------------------------------------------------------ connessione
    def iscrizioni(self):
        out = [f"{self.base}/+/v/#", f"{self.base}/+/alerts/+/current", f"{self.base}/+/errors/+", f"{self.disc}/status"]
        if self.disc != "homeassistant":
            out.append("homeassistant/status")  # l'avviso di nascita di HA va sempre li', qualunque sia il prefisso della discovery
        return out

    def collegato(self):
        """A ogni (ri)collegamento: con un VIN gia' noto la scoperta si pubblica subito."""
        self.sentinella = None
        self.pubblicati.clear()
        if self.vin:
            self.pubblica_scoperta()
        else:
            self.pubblica(self.disponibile, "online", True)

    def scollegato(self):
        """Lo stato della Sentinella vale solo dentro una connessione: dopo, i conservati riconsegnati non sono eventi."""
        self.sentinella = None

    # ------------------------------------------------------------------ messaggi
    def messaggio(self, topic, payload, conservato=False):
        if isinstance(payload, (bytes, bytearray)):
            payload = payload.decode("utf-8", errors="replace")
        if topic in (f"{self.disc}/status", "homeassistant/status"):
            if payload == "online" and self.vin:
                self.log("Home Assistant ripartito: ripubblico la scoperta")
                self.pubblica_scoperta()
            return
        parti = topic.split("/")
        if len(parti) < 4 or parti[0] != self.base or not VIN_RE.match(parti[1]):
            return
        vin = parti[1]
        if self.vin is None:
            self.vin = vin
            self.stato["vin"] = vin
            self.salva()
            self.pubblica_scoperta()
        elif vin != self.vin:
            if vin not in self.altri_vin:
                self.altri_vin.add(vin)
                self.log(f"altra auto {maschera(vin)} ignorata: questo add-on segue {maschera(self.vin)}")
            return
        if not conservato:
            self.stato["ultimo_dato"] = self.ora()
            self.da_salvare = True
        if parti[2] == "v" and len(parti) == 4:
            self.campo(parti[3], payload, conservato)
        elif parti[2] == "alerts" and len(parti) == 5 and parti[4] == "current":
            self.avviso(parti[3], payload)
        elif parti[2] == "errors" and len(parti) == 4:
            self.errore(parti[3], payload)

    def campo(self, nome, payload, conservato=False):
        try:
            v = json.loads(payload) if payload not in ("", None) else None
        except ValueError:
            v = payload
        self.ultimi[nome] = v
        if not conservato:
            self.agg_ts[nome] = self.ora()
        if nome == "DoorState":
            self.pubblica_porte(v)
        elif nome in FINESTRINI:
            self.pubblica_finestrini()
        elif nome == "SentryMode":
            self.sentinella_cambiata(v)
        elif nome == "Gear":
            self.marcia_cambiata(v)
        elif nome == "DetailedChargeState":
            self.carica_cambiata(v)
        elif nome == "PairedPhoneKeyAndKeyFobQty":
            self.chiavi_cambiate(v)
        elif nome == "Odometer":
            km = numero(v)
            if km is not None:
                self.stato["km"] = round(km * tabelle.MIGLIA_KM, 1)
                self.da_salvare = True
        elif nome == "LifetimeEnergyChargedKwh" and (self.stato["sessione"] or {}).get("fine_attesa") and not conservato:
            self.chiudi_sessione()  # e' arrivato il valore finale atteso allo stacco del cavo
        if nome in NAVIGAZIONE:
            self.pubblica_navigazione()
        elif nome in POTENZA:
            self.pubblica_potenza()

    def pubblica_porte(self, v):
        if isinstance(v, dict):
            aperte = [n for k, n in PORTE.items() if v.get(k)]
            stato = ", ".join(aperte) if aperte else "Tutte chiuse"
        else:
            stato = None
        self.pubblica_derivato("porte_aperte", {"stato": stato})

    def pubblica_finestrini(self):
        noti = {k: self.ultimi.get(k) for k in FINESTRINI}
        if not self.fin_completi:
            if not all(isinstance(x, str) and x.startswith("WindowState") for x in noti.values()):
                return  # all'avvio si aspetta che siano noti tutti e 4: niente valori parziali
            self.fin_completi = True
        aperti = [FINESTRINI[k] for k in FINESTRINI if noti[k] in APERTI]
        self.pubblica_derivato("finestrini_aperti", {"stato": ", ".join(aperti) if aperti else "Tutti chiusi"})

    def km_da_casa(self, valore):
        c = coordinate(valore)
        if c is None or self.casa is None:
            return None
        return round(km_tra(c[0], c[1], self.casa[0], self.casa[1]), 1)

    def pubblica_navigazione(self):
        if not all(k in self.ultimi for k in NAVIGAZIONE):
            return  # all'avvio si aspetta che siano arrivati tutti e due (niente valori parziali)
        self.pubblica_derivato("navigazione", {"minuti": self.ultimi.get("MinutesToArrival"),
                                               "km_da_casa": self.km_da_casa(self.ultimi.get("DestinationLocation"))})

    def posizione(self):
        """Ultima posizione valida: un Location nullo (dato Invalid dell'auto) o 0,0 non cancella quella di prima."""
        c = coordinate(self.ultimi.get("Location"))
        if c is not None:
            self.ultima_posizione = c
        return self.ultima_posizione

    def pubblica_potenza(self):
        """Potenza della batteria in kW, tensione per corrente: positiva quando la batteria si scarica (marcia, consumi a
        auto ferma), negativa quando si carica (frenata, ricarica), come nel pannello Energia di HA. L'auto manda la
        corrente negativa in scarica (visto il 18/11/2026: -44 kW in accelerazione, +28 in frenata), da qui il meno.
        Senza tutti e due i valori non si pubblica niente."""
        if not all(k in self.ultimi for k in POTENZA):
            return
        v, i = numero(self.ultimi.get("PackVoltage")), numero(self.ultimi.get("PackCurrent"))
        self.pubblica_derivato("potenza_batteria", {"stato": round(-v * i / 1000, 2) + 0.0 if v is not None and i is not None else None})

    def a_casa(self):
        """Prima la posizione GPS dentro la zona home di HA; solo se mancano la posizione o la zona, "A casa" dell'auto
        (LocatedAtHome). LocatedAtHome puo' restare true con l'auto lontana (visto con l'auto a 4 km da casa per ore):
        se venisse prima, un'allerta fuori casa risulterebbe a casa e non verrebbe notificata."""
        c = self.posizione()
        if c is not None and self.casa is not None:
            return km_tra(c[0], c[1], self.casa[0], self.casa[1]) * 1000 <= max(self.casa[2], 50)
        v = self.ultimi.get("LocatedAtHome")
        return v is True or str(v).lower() == "true"

    def dove(self):
        return "a casa" if self.a_casa() else "fuori casa"

    def link_posizione(self):
        c = self.posizione()
        return f"https://www.google.com/maps?q={c[0]:.5f},{c[1]:.5f}" if c else None

    # ------------------------------------------------------------------ Sentinella e parcheggio
    def sentinella_cambiata(self, v):
        nuovo = str(v).replace("SentryModeState", "") if isinstance(v, str) else None
        nuovo = None if nuovo in (None, "Unknown", "") else nuovo
        prec, self.sentinella = self.sentinella, nuovo
        if prec is None or nuovo is None or nuovo == prec:
            return  # da uno stato sconosciuto (anche il conservato all'avvio o al ricollegamento) nessun evento
        if nuovo not in ("Panic", "Aware"):
            return
        adesso = self.ora()
        chiave, nome = ("allarme", "ultimo_allarme") if nuovo == "Panic" else ("allerta", "ultima_allerta")
        evento = {"quando": datetime.fromtimestamp(adesso, timezone.utc).isoformat(timespec="seconds"), "dove": self.dove()}
        self.stato[chiave] = evento
        if self.conteggio_parcheggio:
            pa = self.stato["parcheggio"]
            campo = "allarmi" if nuovo == "Panic" else "allerte"
            pa[campo] = int(pa.get(campo) or 0) + 1
        self.salva()  # prima si salva, poi si pubblica: il file non e' mai piu' vecchio del broker
        self.pubblica_derivato(nome, evento, forza=True)
        if self.conteggio_parcheggio:
            self.pubblica_parcheggio()
        self.log(f"Sentinella: {chiave} ({evento['dove']})")
        if not self.avvisi_sentinella:
            return
        ora = ora_locale(adesso, "%H:%M")
        posizione = self.link_posizione()
        if nuovo == "Panic":
            testo = f"ALLARME della Sentinella alle {ora}: urto, vetro o apertura sull'auto ({evento['dove']})."
            testo += self.testo_conta("allarme")
            if evento["dove"] == "fuori casa" and posizione:
                testo += f" Posizione: {posizione}"
            self.notifica(testo, suono=True)
        elif evento["dove"] == "fuori casa" and adesso - self.stato["allerta_notificata"] > ALLERTA_OGNI_S:
            self.stato["allerta_notificata"] = adesso
            self.da_salvare = True
            testo = f"Sentinella in allerta alle {ora}: qualcuno vicino all'auto fuori casa." + self.testo_conta("allerta")
            if posizione:
                testo += f" Posizione: {posizione}"
            self.notifica(testo, suono=False)

    def testo_conta(self, tipo):
        if not self.conteggio_parcheggio:
            return ""
        pa = self.stato["parcheggio"]
        n, l, dalle = int(pa.get("allarmi") or 0), int(pa.get("allerte") or 0), pa.get("parcheggiata_dalle")
        ts = quando_ts(dalle)
        da = ""
        if ts:
            da = (f" dal parcheggio delle {ora_locale(ts, '%H:%M')}" if ora_locale(ts, "%Y%m%d") == ora_locale(self.ora(), "%Y%m%d")
                  else f" dal parcheggio del {ora_locale(ts, '%d/%m alle %H:%M')}")
        if tipo == "allarme":
            return f" E' il {n}° allarme{da}" + (f" (e {l} {'allerta' if l == 1 else 'allerte'})" if l else "") + "."
        return f" E' la {l}ª allerta{da}" + (f" (e {n} {'allarme' if n == 1 else 'allarmi'})" if n else "") + "."

    def marcia_cambiata(self, v):
        nuova = str(v).replace("ShiftState", "") if isinstance(v, str) else None
        nuova = nuova if nuova in ("P", "R", "N", "D") else None
        if nuova is None:
            return
        prec = self.stato["marcia"]
        if nuova == prec:
            return
        self.stato["marcia"] = nuova
        self.da_salvare = True
        if self.conteggio_parcheggio and prec in ("D", "R", "N") and nuova == "P":
            self.stato["parcheggio"] = {"allarmi": 0, "allerte": 0, "dove": self.dove(),
                                        "parcheggiata_dalle": datetime.fromtimestamp(self.ora(), timezone.utc).isoformat(timespec="seconds")}
            self.salva()
            self.pubblica_parcheggio()

    def pubblica_parcheggio(self, forza=False):
        pa = self.stato["parcheggio"]
        self.pubblica_derivato("sentinella_parcheggio", {"stato": int(pa.get("allarmi") or 0), "allerte": int(pa.get("allerte") or 0),
                                                         "parcheggiata_dalle": pa.get("parcheggiata_dalle"), "dove": pa.get("dove")},
                               forza=forza)

    def chiavi_cambiate(self, v):
        n = numero(v)
        if n is None:
            return
        n, prec = int(n), self.stato["chiavi"]
        if n == prec:
            return
        self.stato["chiavi"] = n
        self.salva()
        if prec is not None and n > prec and self.avviso_chiave:
            self.notifica(f"{self.nome}: le chiavi abbinate sono passate da {prec} a {n}. Se non hai aggiunto tu un telefono o una chiave, "
                          "controlla subito nell'app Tesla (Sicurezza, Chiavi).", suono=True)

    # ------------------------------------------------------------------ ricarica e capacita'
    def carica_cambiata(self, v):
        nuovo = str(v).replace("DetailedChargeState", "") if isinstance(v, str) else None
        nuovo = None if nuovo in (None, "", "Unknown") else nuovo
        if nuovo is None:
            return
        prec = self.stato["carica"]
        if nuovo == prec:
            return
        self.stato["carica"] = nuovo
        self.da_salvare = True
        if not self.misura_capacita or prec is None:
            return
        if prec == "Disconnected":
            self.apri_sessione()
        elif nuovo == "Disconnected":
            sess = self.stato["sessione"]
            if not sess:
                self.esito_capacita("scartata: inizio non visto")
                return
            ultimo = self.agg_ts.get("LifetimeEnergyChargedKwh", 0)
            if self.ora() - ultimo < ATTESA_CONTATORE_S:
                sess["fine_attesa"] = self.ora() + ATTESA_CONTATORE_S  # il contatore si muoveva: si aspetta il valore finale
                self.salva()
            else:
                self.chiudi_sessione()

    def apri_sessione(self):
        sess = {"energia": numero(self.ultimi.get("LifetimeEnergyChargedKwh")), "batteria": numero(self.ultimi.get("BatteryLevel")),
                "usata": numero(self.ultimi.get("LifetimeEnergyUsed")), "a_casa": self.a_casa(),
                "ora": datetime.fromtimestamp(self.ora(), timezone.utc).isoformat(timespec="seconds"), "wallbox": None}
        self.stato["sessione"] = sess
        self.salva()
        if self.contatore_wallbox:
            def leggi(ha, ora=sess["ora"]):
                w = kwh(ha.stato(self.contatore_wallbox))
                with self.blocco:
                    if (self.stato["sessione"] or {}).get("ora") == ora:
                        self.stato["sessione"]["wallbox"] = w
                        self.salva()
            self.ha.in_coda(leggi)

    def chiudi_sessione(self):
        sess = dict(self.stato["sessione"])
        self.stato["sessione"] = {}
        self.salva()
        fine = {"energia": numero(self.ultimi.get("LifetimeEnergyChargedKwh")), "batteria": numero(self.ultimi.get("BatteryLevel")),
                "usata": numero(self.ultimi.get("LifetimeEnergyUsed")), "ora": datetime.fromtimestamp(self.ora(), timezone.utc).isoformat(timespec="seconds")}
        # a casa all'inizio o alla fine: la posizione arriva ogni 60 s, col cavo attaccato subito dopo l'arrivo l'inizio
        # puo' avere ancora la posizione di prima della zona home
        sess["a_casa"] = bool(sess.get("a_casa") or self.a_casa())
        if self.contatore_wallbox and sess.get("wallbox") is not None and sess["a_casa"]:
            def leggi(ha):
                w = kwh(ha.stato(self.contatore_wallbox))
                with self.blocco:
                    self.misura(sess, dict(fine, wallbox=w))
            if self.ha.in_coda(leggi):
                return
        self.misura(sess, fine)

    def misura(self, sess, fine):
        """Capacita' = kWh entrati in batteria (meno quelli usati a cavo attaccato) / punti di batteria guadagnati * 100, se
        i punti sono almeno 20; perdite di ricarica a casa = 1 - kWh in batteria / kWh della wallbox."""
        if sess.get("energia") is None or sess.get("batteria") is None or fine["energia"] is None or fine["batteria"] is None:
            self.esito_capacita("scartata: dati non validi")
            return
        de, dp = fine["energia"] - sess["energia"], fine["batteria"] - sess["batteria"]
        du = 0.0
        if sess.get("usata") is not None and fine.get("usata") is not None and fine["usata"] - sess["usata"] > 0:
            du = fine["usata"] - sess["usata"]
        netta = de - du
        if dp < 20:
            self.esito_capacita(f"scartata: meno di 20 punti ({int(round(dp))})")
            return
        if netta <= 0:
            self.esito_capacita("scartata: dati non validi")
            return
        perdite, dw = None, None
        if sess.get("wallbox") is not None and fine.get("wallbox") is not None:
            dw = fine["wallbox"] - sess["wallbox"]
            if sess.get("a_casa") and de < dw < de * 2:
                perdite = round((1 - de / dw) * 100, 1)
        capacita = round(netta / dp * 100, 1)
        c = dict(self.stato["capacita"] or {})
        misure = list(c.get("misure") or [])[-9:] + [{"data": fine["ora"][:10], "kwh": round(de, 2), "usati": round(du, 2), "punti": round(dp, 1),
                                                      "capacita": capacita, "perdite": perdite}]
        self.stato["capacita"] = {"stato": capacita, "ultima_esito": "misurata", "ultima_kwh_batteria": round(de, 2),
                                  "ultima_kwh_usati_a_cavo": round(du, 2), "ultima_punti": round(dp, 1),
                                  "ultima_kwh_wallbox": round(dw, 2) if perdite is not None else None, "ultima_perdite_pct": perdite,
                                  "ultima_data": fine["ora"], "misure": misure}
        self.salva()
        self.pubblica_derivato("capacita_misurata", self.stato["capacita"])
        self.log(f"capacita' misurata: {capacita} kWh ({round(dp, 1)} punti, {round(de, 2)} kWh)")

    def esito_capacita(self, esito):
        c = dict(self.stato["capacita"] or {"stato": None})
        c["ultima_esito"] = esito
        self.stato["capacita"] = c
        self.salva()
        self.pubblica_derivato("capacita_misurata", c)
        self.log(f"capacita': {esito}")

    # ------------------------------------------------------------------ avvisi dello schermo
    def avviso(self, nome, payload):
        try:
            corpo = json.loads(payload)
        except ValueError:
            return
        if not isinstance(corpo, dict):
            return
        adesso = self.ora()
        voce = self.diz.get(nome) or {"g": "Altro", "m1": f"Avviso {nome}", "m2": "", "p": "registro"}
        riga = voce["m1"] + (f" - {voce['m2']}" if voce.get("m2") else "")
        # 1) avvisi attivi: dallo stato vero dell'auto (anche dai conservati, anche le fini in ritardo); una fine di
        #    un'occorrenza piu' vecchia non chiude quella nuova
        prima = {k: dict(v) for k, v in self.stato["attivi"].items()}
        era_attivo = nome in self.stato["attivi"]
        if corpo.get("EndedAt"):
            att = self.stato["attivi"].get(nome)
            inizio_fine = quando_ts(corpo.get("StartedAt"))
            if att and att.get("inizio") and inizio_fine and inizio_fine < quando_ts(att["inizio"]):
                pass  # fine di un'occorrenza precedente
            else:
                self.stato["attivi"].pop(nome, None)
        else:
            inizio = quando_ts(corpo.get("StartedAt")) or adesso
            self.stato["attivi"][nome] = {"inizio": corpo.get("StartedAt") if isinstance(corpo.get("StartedAt"), str) else None,
                                          "ora": ora_locale(inizio)}
        # 2) avvisi nuovi: solo recenti e una volta per consegna
        t = quando_ts(corpo.get("EndedAt") or corpo.get("CreatedAt") or corpo.get("StartedAt"))
        visti = {k: s for k, s in self.stato["visti"].items() if s > adesso - DOPPIONI_S}
        cambiato = len(visti) != len(self.stato["visti"]) or self.stato["attivi"] != prima
        self.stato["visti"] = visti
        consegna = f"{nome}|{corpo.get('StartedAt')}|{corpo.get('EndedAt')}"
        nuovo = t >= adesso - RECENTE_S and consegna not in visti
        if nuovo:
            visti[consegna] = adesso
            cambiato = True
            if not corpo.get("EndedAt"):
                self.stato["ultimo"] = {"testo": voce["m1"], "seconda_riga": voce.get("m2") or "-", "gruppo": voce["g"], "codice": nome,
                                        "quando": ora_locale(adesso), "importanza": voce["p"]}
        if cambiato:
            self.salva()  # prima si salva, poi si pubblica; niente scritture se non cambia niente
        if nuovo and not corpo.get("EndedAt"):
            self.pubblica_derivato("ultimo_avviso", self.stato["ultimo"])
            self.log(f"avviso: {voce['m1'][:80]} ({voce['g']}, {nome})")
        if self.stato["attivi"] != prima:
            self.pubblica_12v()
        if nuovo:
            self.decidi_notifica(nome, voce, riga, finito=bool(corpo.get("EndedAt")), era_attivo=era_attivo)

    def decidi_notifica(self, nome, voce, riga, finito, era_attivo):
        """Le regole dell'app di prima: registro sempre; suono, muto o niente dal dizionario; in D o R senza suono;
        ricarica dopo 2 minuti se ancora attivo; carica fermata apposta solo registro; gia' rientrato muto."""
        if finito:
            in_attesa = self.in_attesa.pop(nome, None) is not None
            self.registro(f"Rientrato: {riga} ({voce['g']}, {nome})")
            if not era_attivo and not in_attesa and voce["p"] != "registro":
                self.notifica_avviso(voce, riga + " (gia' rientrato)", "muto")
            return
        politica, nota = voce["p"], ""
        if politica == "suono" and self.stato["marcia"] in ("D", "R"):
            politica, nota = "muto", " (in marcia: senza suono)"
        self.registro(f"{riga} ({voce['g']}, {nome}){nota}")
        if politica == "registro":
            return
        if voce.get("ric"):
            self.in_attesa[nome] = {"scade": self.ora() + ATTESA_RICARICA_S, "voce": voce, "riga": riga, "politica": politica}
            return
        self.notifica_avviso(voce, riga, politica)

    def notifica_avviso(self, voce, riga, politica):
        if not self.avvisi_schermo or not self.servizio_notifica:
            return
        testo = f"Avviso dell'auto ({voce.get('g', 'Altro')}): {riga}."

        def manda(ha=None):
            # carica fermata apposta (per esempio un rele' che stacca la wallbox): solo registro
            if voce.get("pm") and ha is not None and self.carica_fermata_apposta(ha):
                self.registro(f"{riga}: carica fermata apposta, nessuna notifica")
                return
            with self.blocco:
                chiave = voce.get("m1", riga)
                adesso = self.ora()
                prima, _, prima_politica = str(self.stato["inviati"].get(chiave) or "").partition("|")
                p = numero(prima)
                if p and adesso - p < RIPETI_S and not (politica == "suono" and prima_politica == "muto"):
                    self.log(f"gia' notificato da meno di 6 ore: {chiave[:60]}")
                    return
                self.stato["inviati"][chiave] = f"{adesso}|{politica}"
                self.stato["inviati"] = {k: v for k, v in self.stato["inviati"].items() if (numero(v.partition("|")[0]) or 0) > adesso - 2 * 86400}
                self.salva()
                self.notifica(testo, suono=politica == "suono")

        if voce.get("pm") and self.entita_pausa:
            self.ha.in_coda(manda)  # serve leggere le entita' di pausa da HA, nel filo di lavoro
        else:
            manda()

    def carica_fermata_apposta(self, ha):
        for e in self.entita_pausa:
            st = ha.stato(e) or {}
            if st.get("state") == "on":
                return True
            if time.time() - quando_ts(st.get("last_changed")) < PAUSA_S:
                return True
        return False

    def pubblica_12v(self, forza=False):
        attivi = [c for c in self.stato["attivi"] if (self.diz.get(c) or {}).get("g") == "Batteria 12V" and self.diz[c]["p"] == "suono"]
        if attivi:
            v = self.diz[attivi[-1]]
            dati = {"stato": v["m1"][:250], "dettaglio": f"Dal {self.stato['attivi'][attivi[-1]]['ora']}: {v.get('m2') or v['m1']}"
                    + (f" (e altri {len(attivi) - 1})" if len(attivi) > 1 else ""), "attivi": ", ".join(attivi)}
        else:
            dati = {"stato": "Nessun avviso attivo", "dettaglio": "La diagnosi dell'auto non segnala problemi alla batteria 12V", "attivi": "-"}
        self.pubblica_derivato("batteria_12v", dati, forza=forza)

    def errore(self, nome, payload):
        try:
            corpo = json.loads(payload)
        except ValueError:
            corpo = {}
        corpo = corpo if isinstance(corpo, dict) else {}
        nuovo_ts = quando_ts(corpo.get("CreatedAt"))
        vecchio = self.stato["errore"] or {}
        vecchio_ts = quando_ts(vecchio.get("quando"))
        campo = (corpo.get("Tags") or {}).get("field_name")
        stato = nome + (f": {campo}" if campo else "")
        if vecchio_ts and (not nuovo_ts or nuovo_ts < vecchio_ts or (nuovo_ts == vecchio_ts and stato >= str(vecchio.get("stato")))):
            return  # vale il piu' recente; senza data non sostituisce uno datato; a pari ora decide il nome (ordine fisso)
        if vecchio == {"stato": stato, "quando": corpo.get("CreatedAt")}:
            return
        self.stato["errore"] = {"stato": stato, "quando": corpo.get("CreatedAt")}
        self.salva()
        self.pubblica_derivato("ultimo_errore", self.stato["errore"])
        if nuovo_ts >= self.ora() - RECENTE_S:
            self.registro(f"errore della telemetria: {stato}")

    # ------------------------------------------------------------------ Home Assistant
    def notifica(self, testo, suono=True):
        """Notifica con il servizio scelto; con Telegram (telegram_bot.*) i messaggi sono in testo semplice e "muto" vuol dire
        senza suono. Se HA non risponde, risponde 5xx o non conosce ancora il servizio (400 o 404 mentre riparte e carica le
        integrazioni) si riprova dopo 20, 60 e 120 s, nel filo delle notifiche; un 401 o 403 no. Il log dice com'e' andata."""
        if not self.servizio_notifica:
            return
        dati = dict(self.dati_notifica)
        dati.update(message=testo[:1000], title=self.nome)
        if self.servizio_notifica.startswith("telegram_bot."):
            dati["disable_notification"] = not suono
            dati.setdefault("parse_mode", "plain_text")
        servizio, breve, tipo = self.servizio_notifica, testo[:70], "" if suono else " muta"

        def manda(ha):
            motivo = "-"
            for tentativo, attesa in enumerate((0,) + ATTESE_NOTIFICA, 1):
                time.sleep(attesa)
                try:
                    ha.servizio(servizio, dati)
                    self.log(f"notifica{tipo} inviata: {breve}")
                    return
                except urllib.error.HTTPError as e:
                    motivo = f"HTTP {e.code} {dettaglio(e)}".strip()
                    if e.code in (401, 403):
                        break
                except (urllib.error.URLError, OSError) as e:
                    motivo = type(e).__name__
            self.log(f"notifica NON inviata dopo {tentativo} tentativi con {servizio} ({motivo}): {breve}")
        self.ha.in_coda(manda, notifica=True)

    def registro(self, testo):
        if not self.scrivi_registro:
            return
        nome = self.nome
        self.ha.in_coda(lambda ha: ha.servizio("logbook.log", {"name": nome, "message": testo[:500]}))

    def leggi_casa(self):
        """Zona home di HA ogni ora; se HA non risponde (per esempio mentre riparte) si riprova dopo un minuto."""
        def leggi(ha):
            try:
                st = ha.stato("zone.home", errori=True) or {}
            except NonRisponde:
                with self.blocco:
                    self.casa_prossima = self.ora() + 60
                return
            a = st.get("attributes") or {}
            lat, lon, r = numero(a.get("latitude")), numero(a.get("longitude")), numero(a.get("radius"))
            with self.blocco:
                if lat is None or lon is None:
                    self.casa_prossima = self.ora() + 60  # zona non ancora pronta (HA che parte): si riprova presto
                    return
                self.casa_prossima = self.ora() + 3600
                nuova = (lat, lon, r or 100.0)
                if nuova != self.casa:
                    self.casa = nuova
                    self.pubblica_navigazione()
        self.casa_prossima = self.ora() + 120  # finche' la lettura in coda non risponde, nessun'altra lettura
        self.ha.in_coda(leggi)

    # ------------------------------------------------------------------ controlli periodici (ogni 5 s)
    def tick(self):
        adesso = self.ora()
        for nome, a in list(self.in_attesa.items()):
            if adesso >= a["scade"]:
                del self.in_attesa[nome]
                if nome in self.stato["attivi"]:
                    self.notifica_avviso(a["voce"], a["riga"], a["politica"])
        fine = (self.stato["sessione"] or {}).get("fine_attesa")
        if fine and adesso >= fine:
            self.chiudi_sessione()
        if self.ha.attivo and adesso >= self.casa_prossima:
            self.leggi_casa()
        if int(ora_locale(adesso, "%H")) >= ORA_CONTROLLI:
            self.controlli_giornalieri(adesso)
        if self.da_salvare and adesso - self.salvato > 60:
            self.salva()

    def avviso_scadenza(self, scadenza, adesso):
        giorni = (scadenza - datetime.fromtimestamp(adesso).date()).days
        data = scadenza.strftime("%d/%m/%Y")
        if giorni in (30, 7, 1, 0):
            self.notifica(f"La configurazione della telemetria nell'auto scade il {data}: va rimandata all'auto (README, "
                          "configurazione dell'auto).", suono=True)
        elif -3 <= giorni < 0:
            self.notifica(f"La configurazione della telemetria nell'auto e' scaduta il {data}: l'auto non manda piu' dati finche' "
                          "non viene rimandata.", suono=True)

    def da_fare(self, controllo, adesso):
        """Controllo giornaliero non ancora riuscito oggi e non in attesa di un nuovo tentativo."""
        return self.stato["giorni"].get(controllo) != ora_locale(adesso, "%Y-%m-%d") and adesso >= self.riprova.get(controllo, 0)

    def fatto(self, controllo, adesso):
        self.stato["giorni"][controllo] = ora_locale(adesso, "%Y-%m-%d")
        self.riprova.pop(controllo, None)
        self.da_salvare = True

    def controlli_giornalieri(self, adesso):
        """Dalle 9, una volta al giorno: un controllo vale come fatto solo se HA ha risposto; se no si riprova fra 5 minuti."""
        if self.servizio_notifica and self.da_fare("scadenza", adesso):
            if self.scadenza:
                self.fatto("scadenza", adesso)
                self.avviso_scadenza(self.scadenza, adesso)
            else:
                entita = self.entita_scadenza
                self.riprova["scadenza"] = adesso + RIPROVA_S  # niente doppioni mentre la lettura e' in coda

                def leggi_scadenza(ha):
                    try:
                        st = ha.stato(entita, errori=True)
                    except NonRisponde:
                        return  # si riprova fra 5 minuti
                    stato = str((st or {}).get("state") or "")
                    with self.blocco:
                        if self.stato["giorni"].get("scadenza") == ora_locale(adesso, "%Y-%m-%d"):
                            return  # gia' fatto da un'altra copia di questo controllo (HA lento)
                        if st is not None and stato in ("unavailable", "unknown", ""):
                            return  # entita' non ancora pronta (HA che parte): si riprova fra 5 minuti
                        self.fatto("scadenza", adesso)
                        try:
                            scadenza = date.fromisoformat(stato[:10])
                        except ValueError:
                            return  # entita' assente o senza data: nessun promemoria
                        self.avviso_scadenza(scadenza, adesso)
                self.ha.in_coda(leggi_scadenza)
        if self.ore_senza_dati and self.da_fare("senza_dati", adesso):
            self.fatto("senza_dati", adesso)
            ultimo = self.stato["ultimo_dato"]
            if ultimo and adesso - ultimo > self.ore_senza_dati * 3600:
                self.notifica(f"Nessun dato dall'auto da oltre {self.ore_senza_dati} ore (ultimo il {ora_locale(ultimo)}). Normale se "
                              "l'auto e' ferma e addormentata; altrimenti controllare l'add-on, la porta 4443 e la configurazione "
                              "dell'auto.", suono=False)
        if self.confronto_km and self.stato["km"] is not None and self.da_fare("ferma", adesso):
            nostri = self.stato["km"]
            self.riprova["ferma"] = adesso + RIPROVA_S

            def confronta(ha):
                try:
                    st = ha.stato(self.confronto_km, errori=True) or {}
                except NonRisponde:
                    return  # si riprova fra 5 minuti
                altro = numero(st.get("state"))
                if altro is not None and str((st.get("attributes") or {}).get("unit_of_measurement", "")).lower() in ("mi", "miles"):
                    altro *= tabelle.MIGLIA_KM  # HA in unita' imperiali: il nostro contachilometri e' in km
                with self.blocco:
                    if altro is None or self.stato["giorni"].get("ferma") == ora_locale(adesso, "%Y-%m-%d"):
                        return  # contachilometri non ancora pronto (si riprova fra 5 minuti) o controllo gia' fatto
                    self.fatto("ferma", adesso)
                    fermo = self.ora() - (self.stato["ultimo_dato"] or 0) > 7200
                    if altro is not None and altro - nostri > 20 and fermo:
                        self.notifica(f"Telemetria ferma: l'auto ha fatto {int(round(altro - nostri))} km senza mandare dati. Controllare "
                                      "l'add-on, la porta 4443 sul router e la configurazione dell'auto.", suono=True)
            self.ha.in_coda(confronta)


# ---------------------------------------------------------------------- collegamento a MQTT
def crea_client(client_id):
    import paho.mqtt.client as mqtt
    try:
        return mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id=client_id)
    except AttributeError:  # paho-mqtt 1.x
        return mqtt.Client(client_id=client_id)


def vuoto_se_null(v):
    v = (v or "").strip()
    return "" if v.lower() == "null" else v


def si(nome, predefinito=False):
    v = vuoto_se_null(os.environ.get(nome)).lower()
    return predefinito if not v else v in ("1", "true", "si", "yes", "on")


def opzioni_da_ambiente():
    dati = vuoto_se_null(os.environ.get("NOTIFICA_DATI"))
    try:
        dati = json.loads(dati) if dati else {}
    except ValueError as e:
        raise ValueError("notifica_dati: JSON non valido") from e
    try:
        ore = int(vuoto_se_null(os.environ.get("ORE_SENZA_DATI")) or 0)
    except ValueError:
        ore = 0
    return {"topic_base": os.environ.get("TOPIC_BASE", "telemetria_tesla"), "discovery_prefix": os.environ.get("DISCOVERY_PREFIX", "homeassistant"),
            "prefisso": os.environ.get("PREFISSO", "tesla_tele_"), "nome": os.environ.get("NOME", "Tesla Telemetria"),
            "id_dispositivo": vuoto_se_null(os.environ.get("ID_DISPOSITIVO")), "modello": vuoto_se_null(os.environ.get("MODELLO")),
            "vin": vuoto_se_null(os.environ.get("VIN")), "notifica": vuoto_se_null(os.environ.get("NOTIFICA")), "notifica_dati": dati,
            "avvisi_sentinella": si("AVVISI_SENTINELLA"), "avviso_chiave_nuova": si("AVVISO_CHIAVE_NUOVA"), "avvisi_schermo": si("AVVISI_SCHERMO"),
            "scadenza_configurazione": vuoto_se_null(os.environ.get("SCADENZA_CONFIGURAZIONE")),
            "contachilometri_confronto": vuoto_se_null(os.environ.get("CONTACHILOMETRI_CONFRONTO")), "ore_senza_dati": ore,
            "entita_pausa_carica": [e for e in vuoto_se_null(os.environ.get("ENTITA_PAUSA_CARICA")).split(",") if e.strip()],
            "misura_capacita": si("MISURA_CAPACITA"), "contatore_wallbox": vuoto_se_null(os.environ.get("CONTATORE_WALLBOX")),
            "conteggio_parcheggio": si("CONTEGGIO_PARCHEGGIO"), "registro": si("REGISTRO", True)}


def main():
    log = lambda testo: print(f"[{datetime.now():%H:%M:%S}] {testo}", flush=True)  # noqa: E731
    diz = json.loads((QUI / "telemetria_avvisi_model3.json").read_text(encoding="utf-8"))
    togli = os.environ.get("TOGLI") == "1"
    file_stato = Path(os.environ.get("FILE_STATO", "/data/stato_scopritore.json"))
    segno = file_stato.with_name("togli_fatto")
    cliente = []  # il client MQTT nasce dopo il controllo delle opzioni
    try:
        opzioni = opzioni_da_ambiente()
        ha = HA(log) if os.environ.get("SUPERVISOR_TOKEN") else NessunHA()
        s = Scopritore(lambda t, p, r: cliente[0].publish(t, p, qos=1, retain=r), opzioni, diz, file_stato, log, ha=ha)
    except ValueError as e:
        # run.sh non lo fa ripartire ogni 10 s: si aspetta che le opzioni vengano corrette
        log(f"opzioni non valide, scopritore fermo: {e}")
        return OPZIONI_NON_VALIDE
    client = crea_client(f"telemetria-tesla-scoperta-{s.nodo}" + ("-togli" if togli else ""))
    cliente.append(client)
    if os.environ.get("MQTT_USER"):
        client.username_pw_set(os.environ["MQTT_USER"], os.environ.get("MQTT_PASS", ""))
    host, porta = os.environ.get("MQTT_HOST", "core-mosquitto"), int(os.environ.get("MQTT_PORT", "1883"))
    if togli:
        conferma = vuoto_se_null(os.environ.get("CONFERMA_TOGLI"))
        conferma = conferma if not conferma or conferma.endswith("_") else conferma + "_"  # come il prefisso
        if conferma != s.P:
            log(f"rimozione NON fatta: per togliere le entita' {s.P}* scrivere lo stesso prefisso in conferma_togli")
            return 1
        if segno.exists() and segno.read_text(encoding="utf-8").strip() == s.P:
            log(f"rimozione delle entita' {s.P}* gia' fatta: spegnere l'opzione togli per ricrearle")
            return 0
        rc = togli_tutto(client, s, host, porta, log, ha=ha)
        if rc == 0:
            try:
                segno.write_text(s.P, encoding="utf-8")
            except OSError:
                pass
        return rc
    try:
        segno.unlink()
    except OSError:
        pass

    def collegato(c, userdata, flags, rc):
        if rc != 0:
            log(f"MQTT ha rifiutato il collegamento (codice {rc}): nuovo tentativo")
            return
        log("MQTT collegato")
        try:
            with s.blocco:
                for t in s.iscrizioni():
                    c.subscribe(t, qos=1)
                s.collegato()
        except Exception as e:  # noqa: BLE001 un errore qui non deve fermare il programma
            log(f"errore al collegamento: {type(e).__name__} {e}")

    def scollegato(c, userdata, rc):
        with s.blocco:
            s.scollegato()
        if rc != 0:
            log(f"MQTT scollegato (codice {rc}): ricollegamento automatico")

    def arrivato(c, userdata, m):
        try:
            with s.blocco:
                s.messaggio(m.topic, m.payload, bool(m.retain))
        except Exception as e:  # noqa: BLE001 un messaggio strano non deve fermare il programma
            log(f"messaggio non gestito su {m.topic.replace(s.vin or '-', maschera(s.vin))}: {type(e).__name__} {e}")

    client.on_connect, client.on_disconnect, client.on_message = collegato, scollegato, arrivato
    client.reconnect_delay_set(min_delay=2, max_delay=60)
    client.suppress_exceptions = True
    # se il processo si ferma senza salutare, il broker pubblica "offline": i valori derivati in HA risultano non disponibili
    client.will_set(s.disponibile, "offline", qos=1, retain=True)
    log(f"scopritore {VERSIONE}: in ascolto su {opzioni['topic_base']}/+/..., prefisso delle entita' {s.P}, "
        f"auto {maschera(s.vin) if s.vin else 'ancora da scoprire'}, notifiche {s.servizio_notifica or 'spente'}, "
        f"Home Assistant {'raggiungibile' if ha.attivo else 'senza token: niente notifiche ne registro'}")
    client.connect_async(host, porta, keepalive=60)
    client.loop_start()
    while True:
        time.sleep(5)
        try:
            with s.blocco:
                s.tick()
        except Exception as e:  # noqa: BLE001 un controllo andato male non deve fermare il programma
            log(f"errore nel controllo periodico: {type(e).__name__} {e}")


def togli_tutto(client, s, host, porta, log, attesa_s=60, ha=None):
    """Toglie le entita' aspettando la conferma di OGNI messaggio (paho ne tiene in volo pochi alla volta); con HA
    raggiungibile controlla poi che in HA non ne resti nessuna."""
    visto = []
    client.on_message = lambda c, u, m: visto.append(m.topic.split("/")[1]) if VIN_RE.match(m.topic.split("/")[1]) else None
    try:
        client.connect(host, porta, keepalive=60)
    except OSError as e:
        log(f"rimozione: MQTT non raggiungibile ({type(e).__name__}), niente tolto")
        return 1
    client.loop_start()
    try:
        if not s.vin:
            # nessun VIN salvato: lo si prende dal primo messaggio (servono solo i topic dei valori derivati)
            client.subscribe(f"{s.base}/+/v/#", qos=1)
            fine = time.time() + 15
            while not visto and time.time() < fine:
                time.sleep(0.2)
            s.vin = visto[0] if visto else None
            client.unsubscribe(f"{s.base}/+/v/#")
        messaggi = s.messaggi_rimozione()
        info = [client.publish(t, p, qos=1, retain=True) for t, p in messaggi]
        fine = time.time() + attesa_s
        while time.time() < fine and not all(pubblicato(i) for i in info):
            time.sleep(0.2)
        confermati = sum(1 for i in info if pubblicato(i))
        log(f"rimozione: {confermati} di {len(info)} messaggi confermati dal broker"
            + ("" if s.vin else " (VIN sconosciuto: valori derivati non toccati)"))
        ok = confermati == len(info) and bool(s.vin)
        if ok and ha is not None and ha.attivo:
            # solo le entita' dello scopritore (un'altra con lo stesso inizio, per esempio un helper, non conta)
            nostre = {c["default_entity_id"] for _, _, c in s.configurazioni(tutte=True)}
            restano = None
            fine = time.time() + 30
            while time.time() < fine:
                stati = ha.stati()
                restano = None if stati is None else [x["entity_id"] for x in stati if x["entity_id"] in nostre]
                if restano == []:
                    break
                time.sleep(3)
            if restano:
                log(f"rimozione: in Home Assistant restano {len(restano)} entita' {s.P}* (HA acceso e collegato a MQTT?)")
                ok = False
            elif restano is None:
                log("rimozione: Home Assistant non risponde, controllo delle entita' non fatto")
        return 0 if ok else 1
    finally:
        client.disconnect()
        client.loop_stop()


def pubblicato(info):
    """is_published() di paho solleva un errore per un messaggio non partito (collegamento caduto): vale come non confermato."""
    try:
        return info.is_published()
    except (RuntimeError, ValueError):
        return False


if __name__ == "__main__":
    sys.exit(main())
