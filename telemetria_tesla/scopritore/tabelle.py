"""Genera il package di Home Assistant con i sensori della Fleet Telemetry Tesla (entita' MQTT).

Uso:  python genera_package.py --vin <VIN> [--prefisso tesla_tele_] [--nome "Tesla Telemetria"] [--topic telemetria_tesla]
                               [--scadenza input_datetime.telemetria_tesla_scadenza] [--uscita telemetria_tesla.yaml]
Il VIN si puo' dare anche con la variabile d'ambiente TESLA_VIN. Il file generato contiene il VIN: va solo in
/config/packages/ di Home Assistant, non in git ne' in giro.

Formati MQTT (mqtt_payload.go del server Tesla): numeri e booleani come JSON semplice; gli stati come testo con il
prefisso dell'enum ("SentryModeStateAware", "ShiftStateR"); DoorState come oggetto di sei booleani; Location come
{"latitude", "longitude"}; null quando il dato non c'e' (marcia a vettura ferma, minuti all'arrivo senza percorso).
Tesla avverte che un numero puo' arrivare anche come testo: i modelli reggono i due casi. Un modello che produce "None"
lascia l'entita' in stato sconosciuto (regola dell'MQTT di HA).
Filtro per VIN (README di Tesla, sicurezza): gli argomenti contengono il VIN esatto, mai il carattere jolly.
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path

import yaml

QUI = Path(__file__).resolve().parent
MIGLIA_KM = 1.609344
ATM_BAR = 1.01325  # la telemetria manda le gomme in atmosfere (come converte l'integrazione teslemetry di HA)


def t_num(dec, fattore=1.0):
    espr = "v" if fattore == 1.0 else f"(v * {fattore})"
    fine = f"{espr} | round({dec})" + (" | int" if dec == 0 else "")
    return "{% set v = value_json | float(none) %}{{ (" + fine + ") if v is not none else 'None' }}"


def t_enum(prefisso, mappa):
    m = json.dumps(mappa, ensure_ascii=False)
    return ("{% set m = " + m + " %}{% set v = value_json %}{% if v is none or v is mapping %}None{% else %}"
            "{% set k = (v | string) | replace('" + prefisso + "', '') %}{{ m.get(k, k) }}{% endif %}")


def t_bool(inverso=False):
    """Solo valori riconosciuti; un valore inatteso resta sconosciuto (non diventa 'aperto' o 'sbloccata')."""
    si, no = ("OFF", "ON") if inverso else ("ON", "OFF")
    return ("{% set v = value_json %}{% set s = v | string | lower %}{% if v is none or v is mapping %}None"
            "{% elif s in ['true', '1', '1.0'] %}" + si + "{% elif s in ['false', '0', '0.0'] %}" + no + "{% else %}None{% endif %}")


def t_porta(chiave):
    return "{% set v = value_json %}{% if v is mapping %}{{ 'ON' if v.get('" + chiave + "') else 'OFF' }}{% else %}None{% endif %}"


T_FINESTRINO = ("{% set v = value_json | string %}{% if v == 'WindowStateClosed' %}OFF"
                "{% elif v in ['WindowStatePartiallyOpen', 'WindowStateOpened'] %}ON{% else %}None{% endif %}")
# gli avvisi delle gomme sono un oggetto con una voce per gomma ({} se nessuna), non un booleano
T_AVVISI_GOMME = ("{% set v = value_json %}{% if v is mapping %}{{ 'ON' if v.values() | select | list | count > 0 else 'OFF' }}"
                  "{% elif (v | string | lower) in ['true', '1'] %}ON{% elif (v | string | lower) in ['false', '0'] %}OFF{% else %}None{% endif %}")


def t_testo(vuoto="None"):
    # SoftwareUpdateVersion arriva come " " (uno spazio) quando non c'e' un aggiornamento
    return ("{% set v = value_json %}{% if v is none or v is mapping or (v | string | trim) == '' %}" + vuoto +
            "{% else %}{{ v | string | truncate(250, true, '') }}{% endif %}")


T_ORA = ("{% set v = value_json %}{% if v is none or v is mapping or (v | string | trim) in ['', '0', '0.0'] %}None"
         "{% elif v is number and v > 1000000000000 %}{{ ((v / 1000) | int) | timestamp_custom('%d/%m %H:%M') }}"
         "{% elif v is number and v > 1000000000 %}{{ (v | int) | timestamp_custom('%d/%m %H:%M') }}"
         "{% elif v is number and 0 < v < 1440 %}{{ '%02d:%02d' % ((v // 60) | int, (v % 60) | int) }}"
         "{% else %}{{ v | string | truncate(60, true, '') }}{% endif %}")
T_DIST = ("{% set v = value_json %}{% if v is mapping and v.get('latitude') is not none and v.get('longitude') is not none "
          "and ((v.latitude | float(0)) != 0 or (v.longitude | float(0)) != 0) %}"
          "{% set d = distance(v.latitude | float, v.longitude | float) %}{{ d | round(1) if d is not none else 'None' }}"
          "{% else %}None{% endif %}")

# (campo, id, nome, unita', device_class, state_class, decimali, fattore, icona)
NUMERI = [
    ("BatteryLevel", "batteria", "Batteria", "%", "battery", "measurement", 2, 1.0, None),
    ("Soc", "batteria_utilizzabile", "Batteria utilizzabile", "%", "battery", "measurement", 1, 1.0, None),
    ("EnergyRemaining", "energia_batteria", "Energia nella batteria", "kWh", "energy_storage", "measurement", 1, 1.0, None),
    ("NominalFullPackEnergyKwh", "capacita_batteria", "Capacità della batteria", "kWh", "energy_storage", "measurement", 1, 1.0, "mdi:battery-heart-variant"),
    ("ChargeLimitSoc", "limite_carica", "Limite di carica", "%", None, "measurement", 0, 1.0, "mdi:battery-arrow-up"),
    ("ACChargingPower", "potenza_ac", "Potenza di carica AC", "kW", "power", "measurement", 1, 1.0, None),
    ("DCChargingPower", "potenza_dc", "Potenza di carica DC", "kW", "power", "measurement", 1, 1.0, None),
    ("ChargeAmps", "ampere", "Corrente di carica", "A", "current", "measurement", 0, 1.0, None),
    ("ChargeCurrentRequest", "ampere_richiesti", "Corrente richiesta", "A", "current", "measurement", 0, 1.0, None),
    ("ChargeCurrentRequestMax", "ampere_massimi", "Corrente massima", "A", "current", "measurement", 0, 1.0, None),
    ("ChargerPhases", "fasi", "Fasi del caricatore", None, None, None, 0, 1.0, "mdi:sine-wave"),
    ("ModuleTempMin", "temp_batteria_min", "Temperatura batteria minima", "°C", "temperature", "measurement", 1, 1.0, None),
    ("ModuleTempMax", "temp_batteria_max", "Temperatura batteria massima", "°C", "temperature", "measurement", 1, 1.0, None),
    ("Odometer", "contachilometri", "Contachilometri", "km", "distance", "total_increasing", 1, MIGLIA_KM, None),
    ("LifetimeEnergyChargedKwh", "energia_caricata", "Energia caricata totale", "kWh", "energy", "total_increasing", 2, 1.0, None),
    ("LifetimeDcChargeEnergyKwh", "energia_caricata_dc", "Energia caricata DC totale", "kWh", "energy", "total_increasing", 2, 1.0, None),
    ("LifetimeEnergyUsed", "energia_usata", "Energia usata totale", "kWh", "energy", "total_increasing", 2, 1.0, None),
    ("LifetimeEnergyGainedRegen", "energia_recuperata", "Energia recuperata totale", "kWh", "energy", "total_increasing", 2, 1.0, None),
    ("TpmsPressureFl", "gomma_ant_sx", "Gomma anteriore sinistra", "bar", "pressure", "measurement", 2, ATM_BAR, "mdi:tire"),
    ("TpmsPressureFr", "gomma_ant_dx", "Gomma anteriore destra", "bar", "pressure", "measurement", 2, ATM_BAR, "mdi:tire"),
    ("TpmsPressureRl", "gomma_post_sx", "Gomma posteriore sinistra", "bar", "pressure", "measurement", 2, ATM_BAR, "mdi:tire"),
    ("TpmsPressureRr", "gomma_post_dx", "Gomma posteriore destra", "bar", "pressure", "measurement", 2, ATM_BAR, "mdi:tire"),
    ("InsideTemp", "temp_interna", "Temperatura interna", "°C", "temperature", "measurement", 1, 1.0, None),
    ("OutsideTemp", "temp_esterna", "Temperatura esterna", "°C", "temperature", "measurement", 1, 1.0, None),
    ("HvacLeftTemperatureRequest", "temp_impostata", "Temperatura impostata", "°C", "temperature", None, 1, 1.0, None),
    ("SeatHeaterLeft", "sedile_guida", "Sedile guida", None, None, "measurement", 0, 1.0, "mdi:car-seat-heater"),
    ("SeatHeaterRight", "sedile_passeggero", "Sedile passeggero", None, None, "measurement", 0, 1.0, "mdi:car-seat-heater"),
    ("SeatHeaterRearLeft", "sedile_post_sx", "Sedile posteriore sinistro", None, None, "measurement", 0, 1.0, "mdi:car-seat-heater"),
    ("SeatHeaterRearRight", "sedile_post_dx", "Sedile posteriore destro", None, None, "measurement", 0, 1.0, "mdi:car-seat-heater"),
    ("SeatHeaterRearCenter", "sedile_post_centro", "Sedile posteriore centrale", None, None, "measurement", 0, 1.0, "mdi:car-seat-heater"),
    ("MinutesToArrival", "minuti_arrivo", "Minuti all'arrivo", "min", "duration", None, 0, 1.0, "mdi:timer-outline"),
    ("ExpectedEnergyPercentAtTripArrival", "batteria_arrivo", "Batteria all'arrivo", "%", "battery", None, 0, 1.0, None),
    # km alla destinazione (l'auto li manda in miglia) e ritardo per il traffico
    ("MilesToArrival", "km_arrivo", "Km all'arrivo", "km", "distance", None, 1, MIGLIA_KM, "mdi:road-variant"),
    ("RouteTrafficMinutesDelay", "ritardo_traffico", "Ritardo per il traffico", "min", "duration", None, 0, 1.0, "mdi:car-clock"),
    ("PairedPhoneKeyAndKeyFobQty", "chiavi", "Chiavi abbinate", None, None, None, 0, 1.0, "mdi:key-chain"),
]

# (campo, id, nome, prefisso, mappa, icona); voce con valore "None" = stato sconosciuto
ENUM = [
    ("SentryMode", "sentinella", "Sentinella", "SentryModeState",
     {"Off": "Spenta", "Idle": "A riposo", "Armed": "Inserita", "Aware": "Allerta", "Panic": "Allarme", "Quiet": "Silenziosa", "Unknown": "None"}, "mdi:shield-car"),
    ("Gear", "marcia", "Marcia", "ShiftState", {"P": "P", "R": "R", "N": "N", "D": "D", "Invalid": "None", "SNA": "None", "Unknown": "None"}, "mdi:car-shift-pattern"),
    ("DetailedChargeState", "stato_carica", "Stato della carica", "DetailedChargeState",
     {"Disconnected": "Scollegata", "NoPower": "Senza corrente", "Starting": "Avvio", "Charging": "In carica", "Complete": "Completata", "Stopped": "Ferma", "Unknown": "None"}, "mdi:ev-station"),
    ("ChargePortLatch", "aggancio_cavo", "Aggancio del cavo", "ChargePortLatch",
     {"Disengaged": "Sganciato", "Engaged": "Agganciato", "Blocking": "Bloccato", "SNA": "None", "Unknown": "None"}, "mdi:ev-plug-type2"),
    ("ScheduledChargingMode", "ricarica_programmata", "Ricarica programmata", "ScheduledChargingMode",
     {"Off": "Spenta", "StartAt": "Inizio a un orario", "DepartBy": "Partenza programmata", "Unknown": "None"}, "mdi:calendar-clock"),
    ("HvacPower", "clima", "Clima", "HvacPowerState",
     {"Off": "Spento", "On": "Acceso", "Precondition": "Precondizionamento", "OverheatProtect": "Protezione dal caldo", "Unknown": "None"}, "mdi:fan"),
    ("ClimateKeeperMode", "modalita_clima", "Modalità clima", "ClimateKeeperModeState",
     {"Off": "Normale", "On": "Mantieni", "Dog": "Cane", "Party": "Campeggio", "Unknown": "None"}, "mdi:dog-side"),
    ("CabinOverheatProtectionMode", "protezione_caldo", "Protezione dal caldo", "CabinOverheatProtectionModeState",
     {"Off": "Spenta", "On": "Accesa", "FanOnly": "Solo ventola", "Unknown": "None"}, "mdi:thermometer-alert"),
    ("CenterDisplay", "schermo", "Schermo", "DisplayState",
     {"Off": "Spento", "Dim": "Attenuato", "Accessory": "Accessori", "On": "Acceso", "Driving": "Guida", "Charging": "Carica",
      "Lock": "Bloccato", "Sentry": "Sentinella", "Dog": "Modalità cane", "Entertainment": "Intrattenimento", "Unknown": "None"}, "mdi:monitor"),
]

# (campo, id, nome, modello, icona)
TESTI = [
    ("DestinationName", "destinazione", "Destinazione", t_testo("Nessuna"), "mdi:map-marker-path"),
    ("Version", "versione", "Versione software", t_testo(), "mdi:information-outline"),
    ("SoftwareUpdateVersion", "aggiornamento", "Aggiornamento disponibile", t_testo("Nessuno"), "mdi:package-down"),
    ("ScheduledChargingStartTime", "inizio_programmato", "Inizio ricarica programmata", T_ORA, "mdi:clock-start"),
]

# (campo, id, nome, device_class, modello, icona)
BINARI = [
    ("Locked", "serratura", "Serratura", "lock", t_bool(inverso=True), None),
    ("DoorState", "porta_guida", "Porta guida", "door", t_porta("DriverFront"), None),
    ("DoorState", "porta_post_guida", "Porta posteriore guida", "door", t_porta("DriverRear"), None),
    ("DoorState", "porta_passeggero", "Porta passeggero", "door", t_porta("PassengerFront"), None),
    ("DoorState", "porta_post_passeggero", "Porta posteriore passeggero", "door", t_porta("PassengerRear"), None),
    ("DoorState", "frunk", "Frunk", "opening", t_porta("TrunkFront"), "mdi:car-select"),
    ("DoorState", "bagagliaio", "Bagagliaio", "opening", t_porta("TrunkRear"), "mdi:car-back"),
    ("FdWindow", "finestrino_guida", "Finestrino guida", "window", T_FINESTRINO, None),
    ("FpWindow", "finestrino_passeggero", "Finestrino passeggero", "window", T_FINESTRINO, None),
    ("RdWindow", "finestrino_post_guida", "Finestrino posteriore guida", "window", T_FINESTRINO, None),
    ("RpWindow", "finestrino_post_passeggero", "Finestrino posteriore passeggero", "window", T_FINESTRINO, None),
    ("LightsHazardsActive", "quattro_frecce", "Quattro frecce", None, t_bool(), "mdi:car-light-alert"),
    ("DriverSeatOccupied", "conducente", "Conducente seduto", "occupancy", t_bool(), None),
    ("LocatedAtHome", "a_casa", "A casa", "presence", t_bool(), "mdi:home-account"),
    ("LocatedAtWork", "al_lavoro", "Al lavoro", "presence", t_bool(), "mdi:briefcase"),
    ("ChargePortDoorOpen", "sportello", "Sportello di ricarica", "opening", t_bool(), "mdi:ev-plug-type2"),
    ("ChargeEnableRequest", "carica_abilitata", "Carica abilitata", None, t_bool(), "mdi:power-plug-battery"),
    ("ScheduledChargingPending", "in_attesa_orario", "Carica in attesa dell'orario", None, t_bool(), "mdi:timer-sand"),
    ("BmsFullchargecomplete", "carica_100", "Carica al 100% completata", None, t_bool(), "mdi:battery-check"),
    ("BatteryHeaterOn", "riscaldamento_batteria", "Riscaldamento batteria", "heat", t_bool(), None),
    ("AutoSeatClimateLeft", "sedile_guida_auto", "Sedile guida in automatico", None, t_bool(), "mdi:car-seat-heater"),
    ("AutoSeatClimateRight", "sedile_passeggero_auto", "Sedile passeggero in automatico", None, t_bool(), "mdi:car-seat-heater"),
    ("PreconditioningEnabled", "precondizionamento", "Precondizionamento", None, t_bool(), "mdi:thermostat-auto"),
    ("TpmsSoftWarnings", "avviso_gomme", "Avviso gomme", "problem", T_AVVISI_GOMME, None),
    ("TpmsHardWarnings", "allarme_gomme", "Allarme gomme", "problem", T_AVVISI_GOMME, None),
    ("SoftwareUpdateInProgress", "aggiornamento_in_corso", "Aggiornamento in corso", "update", t_bool(), None),
    ("ServiceMode", "modo_assistenza", "Modo assistenza", None, t_bool(), "mdi:wrench"),
]

NON_DA_IGNOTO = ["unknown", "unavailable"]


def dispositivo(prefisso, nome):
    return {"identifiers": ["telemetria_tesla_" + prefisso.rstrip("_")], "name": nome, "manufacturer": "Tesla",
            "model": "Fleet Telemetry"}


def mqtt(vin, P, base, dev):
    radice = f"{base}/{vin}"
    v = lambda campo: f"{radice}/v/{campo}"  # noqa: E731
    sensori, binari = [], []
    for campo, i, nome, unita, dc, sc, dec, fatt, icona in NUMERI:
        s = {"name": nome, "unique_id": P + i, "default_entity_id": f"sensor.{P}{i}", "state_topic": v(campo),
             "value_template": t_num(dec, fatt), "device": dev}
        if unita:
            s["unit_of_measurement"] = unita
        if dc:
            s["device_class"] = dc
        if sc:
            s["state_class"] = sc
        if icona:
            s["icon"] = icona
        sensori.append(s)
    for campo, i, nome, pref, mappa, icona in ENUM:
        sensori.append({"name": nome, "unique_id": P + i, "default_entity_id": f"sensor.{P}{i}", "state_topic": v(campo),
                        "value_template": t_enum(pref, mappa), "icon": icona, "device": dev})
    for campo, i, nome, modello, icona in TESTI:
        sensori.append({"name": nome, "unique_id": P + i, "default_entity_id": f"sensor.{P}{i}", "state_topic": v(campo),
                        "value_template": modello, "icon": icona, "device": dev})
    sensori.append({"name": "Distanza della destinazione da casa", "unique_id": P + "destinazione_distanza",
                    "default_entity_id": f"sensor.{P}destinazione_distanza", "state_topic": v("DestinationLocation"),
                    "value_template": T_DIST, "unit_of_measurement": "km", "device_class": "distance",
                    "icon": "mdi:map-marker-distance", "device": dev})
    sensori.append({"name": "Ultimo collegamento", "unique_id": P + "ultimo_collegamento",
                    "default_entity_id": f"sensor.{P}ultimo_collegamento", "state_topic": f"{radice}/connectivity",
                    "value_template": "{% set v = value_json %}{{ v.get('CreatedAt') if v is mapping and v.get('CreatedAt') else 'None' }}",
                    "device_class": "timestamp", "icon": "mdi:access-point-network", "device": dev})
    for campo, i, nome, dc, modello, icona in BINARI:
        b = {"name": nome, "unique_id": P + i, "default_entity_id": f"binary_sensor.{P}{i}", "state_topic": v(campo),
             "value_template": modello, "payload_on": "ON", "payload_off": "OFF", "device": dev}
        if dc:
            b["device_class"] = dc
        if icona:
            b["icon"] = icona
        binari.append(b)
    binari.append({"name": "Collegata", "unique_id": P + "collegata", "default_entity_id": f"binary_sensor.{P}collegata",
                   "state_topic": f"{radice}/connectivity", "device_class": "connectivity", "payload_on": "ON", "payload_off": "OFF",
                   "value_template": "{% set v = value_json %}{% if v is mapping and v.get('Status') in ['CONNECTED', 'DISCONNECTED'] %}"
                                     "{{ 'ON' if v.get('Status') == 'CONNECTED' else 'OFF' }}{% else %}None{% endif %}",
                   "device": dev})
    tracker = [{"name": "Posizione", "unique_id": P + "posizione", "default_entity_id": f"device_tracker.{P}posizione",
                "json_attributes_topic": v("Location"), "source_type": "gps", "icon": "mdi:car-connected", "device": dev}]
    return {"sensor": sensori, "binary_sensor": binari, "device_tracker": tracker}


def template(P, nome):
    porte = {"porta_guida": "Guida", "porta_post_guida": "Post. guida", "porta_passeggero": "Passeggero",
             "porta_post_passeggero": "Post. passeggero", "frunk": "Frunk", "bagagliaio": "Bagagliaio"}
    fin = {"finestrino_guida": "Guida", "finestrino_passeggero": "Passeggero", "finestrino_post_guida": "Post. guida",
           "finestrino_post_passeggero": "Post. passeggero"}
    # prima il GPS (zona home di HA), poi LocatedAtHome: il flag dell'auto puo' restare true con l'auto lontana
    a_casa = (f"(is_state('device_tracker.{P}posizione', 'home') if (has_value('device_tracker.{P}posizione') and "
              f"(state_attr('device_tracker.{P}posizione', 'latitude') | float(0)) != 0) else "
              f"is_state('binary_sensor.{P}a_casa', 'on'))")

    def elenco(mappa, tutte):
        m = json.dumps(mappa, ensure_ascii=False)
        stato = ("{% set ns = namespace(l=[]) %}{% for k, n in " + m + ".items() %}{% if is_state('binary_sensor." + P +
                 "' ~ k, 'on') %}{% set ns.l = ns.l + [n] %}{% endif %}{% endfor %}{{ ns.l | join(', ') if ns.l else '" + tutte + "' }}")
        disp = ("{{ " + m + ".keys() | map('regex_replace', '^', 'binary_sensor." + P + "') | select('has_value') | list | count > 0 }}")
        return stato, disp

    s_porte, d_porte = elenco(porte, "Tutte chiuse")
    s_fin, d_fin = elenco(fin, "Tutti chiusi")
    return [
        {"sensor": [
            {"name": f"{nome} Porte aperte", "unique_id": P + "porte_aperte", "default_entity_id": f"sensor.{P}porte_aperte",
             "icon": "mdi:car-door", "state": s_porte, "availability": d_porte},
            {"name": f"{nome} Finestrini aperti", "unique_id": P + "finestrini_aperti", "default_entity_id": f"sensor.{P}finestrini_aperti",
             "icon": "mdi:car-door-lock", "state": s_fin, "availability": d_fin},
        ],
         "binary_sensor": [
            {"name": f"{nome} Navigazione verso casa", "unique_id": P + "verso_casa",
             "default_entity_id": f"binary_sensor.{P}verso_casa", "icon": "mdi:home-import-outline",
             "state": f"{{{{ states('sensor.{P}destinazione_distanza') | float(99) < 0.3 and states('sensor.{P}minuti_arrivo') | float(0) > 0 }}}}",
             "availability": f"{{{{ has_value('sensor.{P}destinazione_distanza') }}}}"},
        ]},
        # momenti degli ultimi eventi della Sentinella: solo da uno stato noto (un valore conservato dopo un riavvio non conta)
        {"triggers": [{"trigger": "state", "entity_id": f"sensor.{P}sentinella", "to": "Allarme", "not_from": NON_DA_IGNOTO}],
         "sensor": [{"name": f"{nome} Ultimo allarme Sentinella", "unique_id": P + "ultimo_allarme",
                     "default_entity_id": f"sensor.{P}ultimo_allarme", "device_class": "timestamp", "icon": "mdi:shield-alert",
                     "state": "{{ now().isoformat() }}", "attributes": {"dove": "{{ 'a casa' if " + a_casa + " else 'fuori casa' }}"}}]},
        {"triggers": [{"trigger": "state", "entity_id": f"sensor.{P}sentinella", "to": "Allerta", "not_from": NON_DA_IGNOTO}],
         "sensor": [{"name": f"{nome} Ultima allerta Sentinella", "unique_id": P + "ultima_allerta",
                     "default_entity_id": f"sensor.{P}ultima_allerta", "device_class": "timestamp", "icon": "mdi:shield-search",
                     "state": "{{ now().isoformat() }}", "attributes": {"dove": "{{ 'a casa' if " + a_casa + " else 'fuori casa' }}"}}]},
    ]


def automazioni(vin, base, scadenza):
    avvisi = {
        "id": "telemetria_tesla_avvisi_ed_errori", "alias": "Telemetria Tesla - Avvisi ed errori dell'auto",
        "description": ("Errori della telemetria (per esempio un campo non accettato) nel registro di HA. Avvisi dello schermo dell'auto "
                        "(gruppo customer): evento telemetria_tesla_avviso per l'app AppDaemon telemetria_avvisi, che li traduce. "
                        "Solo se recenti: i messaggi conservati da MQTT dopo un riavvio non si ripetono."),
        "mode": "queued", "max": 20,
        "triggers": [{"trigger": "mqtt", "topic": f"{base}/{vin}/errors/+"},
                     {"trigger": "mqtt", "topic": f"{base}/{vin}/alerts/+/current"}],
        # per un avviso finito conta l'ora di fine (l'inizio puo' essere di ore prima)
        "conditions": [{"condition": "template",
                        "value_template": ("{% set p = trigger.payload_json if trigger.payload_json is defined and trigger.payload_json is mapping else {} %}"
                                           "{% set t = p.get('EndedAt') or p.get('CreatedAt') or p.get('StartedAt') %}"
                                           "{{ t is not none and as_timestamp(t, 0) > as_timestamp(now()) - 900 }}")}],
        "actions": [{"if": [{"condition": "template", "value_template": "{{ '/alerts/' in trigger.topic }}"}],
                     "then": [{"event": "telemetria_tesla_avviso",
                               "event_data": {"tipo": "avviso", "nome": "{{ trigger.topic.split('/')[3] }}",
                                              "dati": "{{ trigger.payload_json }}"}}],
                     "else": [{"action": "logbook.log", "data": {"name": "Telemetria Tesla",
                                                                  "message": "errore {{ trigger.topic.split('/')[3] }}: {{ trigger.payload[:200] }}"}}]}],
    }
    promemoria = {
        "id": "telemetria_tesla_promemoria_scadenza", "alias": "Telemetria Tesla - Promemoria scadenza della configurazione",
        "description": (f"La configurazione dell'auto scade ({scadenza}, scritto da configura_auto.py invia): notifica di HA "
                        "30, 7 e 1 giorno prima e il giorno stesso."),
        "mode": "single",
        "triggers": [{"trigger": "time", "at": "09:05:00"}],
        "conditions": [{"condition": "template",
                        "value_template": ("{% set s = states('" + scadenza + "') %}"
                                           "{% set d = as_timestamp(s ~ 'T00:00:00', 0) if s | regex_match('^\\\\d{4}-\\\\d{2}-\\\\d{2}$') else 0 %}"
                                           "{{ d > 0 and ((d - as_timestamp(now().strftime('%Y-%m-%d') ~ 'T00:00:00')) / 86400) | round(0) | int in [30, 7, 1, 0] }}")}],
        "actions": [{"action": "persistent_notification.create",
                     "data": {"notification_id": "telemetria_tesla_scadenza", "title": "Telemetria Tesla",
                              "message": ("La configurazione della telemetria scade il {{ as_timestamp(states('" + scadenza +
                                          "') ~ 'T00:00:00') | timestamp_custom('%d/%m/%Y') }}: va rimandata con "
                                          "configura_auto.py invia (o firma_una_volta.py).")}}],
    }
    return [avvisi, promemoria]


INTESTAZIONE = """# Telemetria Tesla, generato da genera_package.py: NON modificare a mano, cambiare la tabella e rigenerare.
# I dati arrivano dall'add-on Tesla Fleet Telemetry (server ufficiale di Tesla) via MQTT: {base}/<VIN>/v/<campo>.
# L'auto manda un campo solo quando cambia: nessuna lettura a pagamento. Le entita' sono del dispositivo "{nome}".
# Contiene il VIN: tenerlo solo in /config/packages/.
"""


def testo(vin, P, nome, base, scadenza):
    pacchetto = {"mqtt": mqtt(vin, P, base, dispositivo(P, nome)), "template": template(P, nome),
                 "automation": automazioni(vin, base, scadenza)}
    return (INTESTAZIONE.format(base=base, nome=nome) +
            yaml.safe_dump(pacchetto, allow_unicode=True, sort_keys=False, width=4000, default_flow_style=False))


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    a = argparse.ArgumentParser(description="Package HA dei sensori della Fleet Telemetry Tesla")
    a.add_argument("--vin", default=os.environ.get("TESLA_VIN", ""), help="VIN dell'auto (o variabile TESLA_VIN)")
    a.add_argument("--prefisso", default="tesla_tele_", help="inizio degli entity_id (predefinito tesla_tele_)")
    a.add_argument("--nome", default="Tesla Telemetria", help="nome del dispositivo in HA")
    a.add_argument("--topic", default="telemetria_tesla", help="topic_base dell'add-on (predefinito telemetria_tesla)")
    a.add_argument("--scadenza", default="input_datetime.telemetria_tesla_scadenza", help="helper con la scadenza")
    a.add_argument("--uscita", default=str(QUI / "telemetria_tesla.yaml"), help="file da scrivere")
    x = a.parse_args()
    vin = x.vin.strip().upper()
    if not (len(vin) == 17 and vin.isalnum()):
        raise SystemExit("serve il VIN di 17 caratteri: --vin oppure la variabile TESLA_VIN")
    prefisso = x.prefisso if x.prefisso.endswith("_") else x.prefisso + "_"
    if not re.fullmatch(r"[a-z0-9_]+", prefisso):
        raise SystemExit("--prefisso: solo lettere minuscole, cifre e _ (HA cambierebbe gli entity_id e la plancia non li troverebbe)")
    t = testo(vin, prefisso, x.nome, x.topic.strip("/"), x.scadenza)
    Path(x.uscita).write_text(t, encoding="utf-8", newline="\n")
    d = yaml.safe_load(t)
    print(f"scritto {x.uscita}: {len(d['mqtt']['sensor'])} sensori, {len(d['mqtt']['binary_sensor'])} binari, "
          f"{len(d['mqtt']['device_tracker'])} posizione, "
          f"{sum(len(g.get('sensor', [])) + len(g.get('binary_sensor', [])) for g in d['template'])} template, "
          f"{len(d['automation'])} automazioni. VIN ...{vin[-4:]}")


if __name__ == "__main__":
    main()
