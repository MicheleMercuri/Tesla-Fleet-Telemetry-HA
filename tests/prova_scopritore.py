"""Prove dello scopritore senza rete: MQTT finto, Home Assistant finto e orologio finto, dati inventati.

Uso:  python tests/prova_scopritore.py      (serve solo PyYAML, come nell'add-on)
"""
import io
import json
import os
import subprocess
import sys
import tempfile
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "telemetria_tesla" / "scopritore"))
import scopritore as sc  # noqa: E402
import tabelle  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:
    pass
VIN = "LRW3E7EB0PC000123"           # inventato
CASA = (45.4642, 9.1900)            # inventata
ALTROVE = (45.0703, 7.6869)
ESITI = []


def esito(nome, ok, dettaglio=""):
    ESITI.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + nome + (f"  -> {dettaglio}" if dettaglio and not ok else ""))


class HAFinto:
    attivo = True

    def __init__(self):
        self.stati_ = {"zone.home": {"entity_id": "zone.home", "state": "0",
                                     "attributes": {"latitude": CASA[0], "longitude": CASA[1], "radius": 100}}}
        self.chiamate = []
        self.giu = False        # HA che non risponde (per esempio mentre riparte)
        self.rifiuta = None     # codice HTTP con cui HA rifiuta i servizi
        self.lento = False      # i compiti restano in coda finche' non si chiama esegui()
        self.coda = []
        self.notifiche_in_coda = 0

    def in_coda(self, compito, notifica=False):
        self.notifiche_in_coda += bool(notifica)
        if self.lento:
            self.coda.append(compito)
        else:
            compito(self)
        return True

    def esegui(self):
        coda, self.coda = self.coda, []
        for compito in coda:
            compito(self)

    def stato(self, e, errori=False):
        if self.giu:
            if errori:
                raise sc.NonRisponde(e)
            return None
        return self.stati_.get(e)

    def stati(self):
        return list(self.stati_.values())

    def servizio(self, servizio, dati):
        if self.rifiuta:
            raise urllib.error.HTTPError("http://ha/api", self.rifiuta, "errore", None, io.BytesIO(b'{"message": "Service not found"}'))
        self.chiamate.append((servizio, dati))


class Orologio:
    def __init__(self):
        self.t = datetime(2026, 3, 10, 8, 0).timestamp()

    def __call__(self):
        return self.t


def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat().replace("+00:00", "Z")


def nuovo(cartella, ha, ora, **opz):
    pubblicati = []
    o = {"topic_base": "telemetria_tesla", "discovery_prefix": "homeassistant", "prefisso": "tesla_tele_", "nome": "Tesla Telemetria",
         "notifica": "telegram_bot.send_message", "avvisi_sentinella": True, "avviso_chiave_nuova": True, "avvisi_schermo": True,
         "misura_capacita": True, "conteggio_parcheggio": True, "vin": VIN}
    o.update(opz)
    diz = json.loads((Path(sc.QUI) / "telemetria_avvisi_model3.json").read_text(encoding="utf-8"))
    righe = []
    s = sc.Scopritore(lambda t, p, r: pubblicati.append((t, p, r)), o, diz, Path(cartella) / "stato.json", log=righe.append, ora=ora, ha=ha)
    return s, pubblicati, righe, diz


def v(s, campo, valore, conservato=False):
    s.messaggio(f"telemetria_tesla/{VIN}/v/{campo}", json.dumps(valore), conservato)


def notifiche(ha, da=0):
    return [d for servizio, d in ha.chiamate[da:] if servizio == "telegram_bot.send_message"]


def registro(ha, da=0):
    return [d["message"] for servizio, d in ha.chiamate[da:] if servizio == "logbook.log"]


def ultimo(pubblicati, s, nome):
    x = [p for t, p, r in pubblicati if t == s.derivato(nome)]
    return json.loads(x[-1]) if x and x[-1] else None


with tempfile.TemporaryDirectory() as tmp:
    ha, ora = HAFinto(), Orologio()
    s, pub, righe, diz = nuovo(tmp, ha, ora)

    # 1) scoperta e disponibilita'
    s.collegato()
    conf = [(t, json.loads(p)) for t, p, r in pub if t.endswith("/config") and p]
    attese = len(tabelle.NUMERI) + len(tabelle.ENUM) + len(tabelle.TESTI) + 2 + len(tabelle.BINARI) + 1 + 1 + 8 + 2
    esito(f"scoperta: {len(conf)} entita' con capacita' e parcheggio accesi", len(conf) == attese, f"attese {attese}")
    der = [x for _, x in conf if x["state_topic" if "state_topic" in x else "json_attributes_topic"].startswith(f"telemetria_tesla/{VIN}/derivati/")]
    esito("valori derivati con la disponibilita' dello scopritore", der and all(x.get("availability_topic") == s.disponibile for _, x in conf
                                                                           if "/derivati/" in str(x.get("state_topic"))))
    esito("disponibilita' 'online' conservata", (s.disponibile, "online", True) in pub)
    dd = next(x for _, x in conf if x["unique_id"] == "tesla_tele_destinazione_distanza")
    esito("distanza della destinazione dal messaggio della navigazione (km calcolati qui)", dd["state_topic"].endswith("/navigazione"))

    # 2) zona di casa e navigazione
    s.tick()  # legge la zona home
    v(s, "MinutesToArrival", 12.4)
    v(s, "DestinationLocation", {"latitude": CASA[0] + 0.0005, "longitude": CASA[1]})
    nav = ultimo(pub, s, "navigazione")
    esito("navigazione verso casa: km da casa calcolati (0,1)", nav and nav["km_da_casa"] == 0.1 and nav["minuti"] == 12.4, nav)
    v(s, "DestinationLocation", {"latitude": ALTROVE[0], "longitude": ALTROVE[1]})
    nav = ultimo(pub, s, "navigazione")
    esito("navigazione verso un altro posto: km da casa > 100", nav and nav["km_da_casa"] > 100, nav)
    v(s, "DestinationLocation", {"latitude": 0, "longitude": 0})
    esito("destinazione 0,0: km sconosciuti", ultimo(pub, s, "navigazione")["km_da_casa"] is None)

    # 3) posizione e "dove" dal GPS anche senza LocatedAtHome
    v(s, "LocatedAtHome", False, True)
    v(s, "Location", {"latitude": CASA[0] + 0.0003, "longitude": CASA[1]}, True)
    esito("a casa dal GPS dentro la zona home anche con LocatedAtHome falso", s.a_casa())
    v(s, "Location", {"latitude": ALTROVE[0], "longitude": ALTROVE[1]}, True)
    esito("fuori casa dal GPS", not s.a_casa())

    # 4) marcia: parcheggio azzera il conteggio; il valore conservato uguale non conta
    v(s, "Gear", "ShiftStateD")
    v(s, "Gear", "ShiftStateP")
    pa = ultimo(pub, s, "sentinella_parcheggio")
    esito("parcheggio (D -> P): conteggio azzerato con ora e luogo", pa and pa["stato"] == 0 and pa["allerte"] == 0 and pa["dove"] == "fuori casa"
          and pa["parcheggiata_dalle"], pa)
    n = len(pub)
    v(s, "Gear", "ShiftStateP", True)
    esito("marcia conservata uguale: nessun nuovo parcheggio", not [t for t, p, r in pub[n:] if "sentinella_parcheggio" in t])

    # 5) Sentinella: allarme con suono e conteggio; allerte fuori casa mute, una ogni 15 minuti
    c0 = len(ha.chiamate)
    v(s, "SentryMode", "SentryModeStateArmed", True)
    v(s, "SentryMode", "SentryModeStatePanic")
    nt = notifiche(ha, c0)
    esito("allarme: notifica con suono, 1° allarme dal parcheggio, posizione fuori casa",
          len(nt) == 1 and not nt[0]["disable_notification"] and "1° allarme" in nt[0]["message"] and "google.com/maps" in nt[0]["message"],
          nt)
    esito("allarme: ultimo allarme pubblicato 'fuori casa'", ultimo(pub, s, "ultimo_allarme")["dove"] == "fuori casa")
    v(s, "SentryMode", "SentryModeStateArmed")
    c0 = len(ha.chiamate)
    v(s, "SentryMode", "SentryModeStateAware")
    nt = notifiche(ha, c0)
    esito("allerta fuori casa: notifica muta, 1ª allerta e 1 allarme", len(nt) == 1 and nt[0]["disable_notification"]
          and "1ª allerta" in nt[0]["message"] and "e 1 allarme" in nt[0]["message"], nt)
    v(s, "SentryMode", "SentryModeStateArmed")
    ora.t += 300
    c0 = len(ha.chiamate)
    v(s, "SentryMode", "SentryModeStateAware")
    esito("seconda allerta entro 15 minuti: niente notifica", not notifiche(ha, c0))
    pa = ultimo(pub, s, "sentinella_parcheggio")
    esito("conteggio dal parcheggio: 1 allarme, 2 allerte", pa["stato"] == 1 and pa["allerte"] == 2, pa)
    v(s, "Location", {"latitude": CASA[0], "longitude": CASA[1]}, True)
    v(s, "SentryMode", "SentryModeStateArmed")
    ora.t += 1000
    c0 = len(ha.chiamate)
    v(s, "SentryMode", "SentryModeStateAware")
    esito("allerta a casa: niente notifica", not notifiche(ha, c0))
    s.scollegato()
    s.collegato()
    c0 = len(ha.chiamate)
    v(s, "SentryMode", "SentryModeStatePanic", True)
    esito("dopo il ricollegamento il Panic conservato non e' un allarme", not notifiche(ha, c0))

    # 6) chiavi
    c0 = len(ha.chiamate)
    v(s, "PairedPhoneKeyAndKeyFobQty", 2, True)
    esito("prima lettura delle chiavi: niente notifica", not notifiche(ha, c0))
    v(s, "PairedPhoneKeyAndKeyFobQty", 3)
    nt = notifiche(ha, c0)
    esito("chiave nuova (2 -> 3): notifica con suono", len(nt) == 1 and "da 2 a 3" in nt[0]["message"] and not nt[0]["disable_notification"])
    v(s, "PairedPhoneKeyAndKeyFobQty", 3, True)
    v(s, "PairedPhoneKeyAndKeyFobQty", 2)
    esito("chiavi uguali o in meno: niente notifica", len(notifiche(ha, c0)) == 1)

    # 7) avvisi dello schermo
    at = f"telemetria_tesla/{VIN}/alerts/"
    suono = next(k for k, x in diz.items() if x["p"] == "suono" and not x.get("ric") and not x.get("pm") and x["g"] != "Batteria 12V")
    muto = next(k for k, x in diz.items() if x["p"] == "muto" and not x.get("ric") and not x.get("pm"))
    reg = next(k for k, x in diz.items() if x["p"] == "registro")
    ric = next(k for k, x in diz.items() if x.get("ric") and x["p"] != "registro" and not x.get("pm"))
    pm = next((k for k, x in diz.items() if x.get("pm") and x["p"] != "registro"), None)
    v(s, "Gear", "ShiftStateP")
    c0 = len(ha.chiamate)
    s.messaggio(at + suono + "/current", json.dumps({"StartedAt": iso(ora.t)}))
    nt = notifiche(ha, c0)
    esito("avviso 'suono': registro e notifica con suono", len(nt) == 1 and not nt[0]["disable_notification"] and registro(ha, c0))
    s.messaggio(at + suono + "/current", json.dumps({"StartedAt": iso(ora.t), "EndedAt": iso(ora.t + 10)}))
    ora.t += 60
    c0 = len(ha.chiamate)
    s.messaggio(at + suono + "/current", json.dumps({"StartedAt": iso(ora.t)}))
    esito("stesso testo entro 6 ore: niente seconda notifica", not notifiche(ha, c0) and registro(ha, c0))
    c0 = len(ha.chiamate)
    s.messaggio(at + muto + "/current", json.dumps({"StartedAt": iso(ora.t)}))
    nt = notifiche(ha, c0)
    esito("avviso 'muto': notifica senza suono", len(nt) == 1 and nt[0]["disable_notification"])
    c0 = len(ha.chiamate)
    s.messaggio(at + reg + "/current", json.dumps({"StartedAt": iso(ora.t)}))
    esito("avviso 'registro': solo registro", not notifiche(ha, c0) and registro(ha, c0))
    suono2 = next(k for k, x in diz.items() if x["p"] == "suono" and not x.get("ric") and not x.get("pm") and k != suono
                  and x["m1"] != diz[suono]["m1"] and x["g"] != "Batteria 12V")
    v(s, "Gear", "ShiftStateD")
    c0 = len(ha.chiamate)
    s.messaggio(at + suono2 + "/current", json.dumps({"StartedAt": iso(ora.t)}))
    nt = notifiche(ha, c0)
    esito("avviso 'suono' in marcia: senza suono", len(nt) == 1 and nt[0]["disable_notification"]
          and any("in marcia" in r for r in registro(ha, c0)))
    v(s, "Gear", "ShiftStateP")
    c0 = len(ha.chiamate)
    s.messaggio(at + ric + "/current", json.dumps({"StartedAt": iso(ora.t)}))
    s.tick()
    esito("avviso di ricarica: niente subito", not notifiche(ha, c0))
    ora.t += sc.ATTESA_RICARICA_S + 1
    s.tick()
    esito("avviso di ricarica ancora attivo dopo 2 minuti: notifica", len(notifiche(ha, c0)) == 1)
    ric2 = next(k for k, x in diz.items() if x.get("ric") and x["p"] != "registro" and not x.get("pm") and k != ric and x["m1"] != diz[ric]["m1"])
    c0 = len(ha.chiamate)
    s.messaggio(at + ric2 + "/current", json.dumps({"StartedAt": iso(ora.t)}))
    s.messaggio(at + ric2 + "/current", json.dumps({"StartedAt": iso(ora.t), "EndedAt": iso(ora.t + 30)}))
    ora.t += sc.ATTESA_RICARICA_S + 1
    s.tick()
    esito("avviso di ricarica rientrato prima dei 2 minuti: niente notifica", not notifiche(ha, c0))
    usati = (suono, suono2, muto, reg, ric, ric2)
    gia = next(k for k, x in diz.items() if x["p"] == "suono" and not x.get("pm") and not x.get("ric") and k not in usati
               and x["m1"] not in [diz[u]["m1"] for u in usati])
    c0 = len(ha.chiamate)
    s.messaggio(at + gia + "/current", json.dumps({"StartedAt": iso(ora.t - 20), "EndedAt": iso(ora.t - 5)}))
    nt = notifiche(ha, c0)
    esito("avviso arrivato gia' finito: notifica muta '(gia' rientrato)'", len(nt) == 1 and nt[0]["disable_notification"]
          and "gia' rientrato" in nt[0]["message"])
    if pm:
        (Path(tmp) / "pm").mkdir()
        s2, pub2, _, _ = nuovo(Path(tmp) / "pm", ha, ora, entita_pausa_carica=["input_boolean.pausa"])
        ha.stati_["input_boolean.pausa"] = {"state": "on", "last_changed": iso(ora.t - 3600)}
        c0 = len(ha.chiamate)
        s2.messaggio(at + pm + "/current", json.dumps({"StartedAt": iso(ora.t)}))
        if diz[pm].get("ric"):
            ora.t += sc.ATTESA_RICARICA_S + 1
            s2.tick()
        esito("corrente mancata con la pausa della carica accesa: solo registro", not notifiche(ha, c0)
              and any("fermata apposta" in r for r in registro(ha, c0)))
    vecchio = json.dumps({"StartedAt": iso(ora.t - 7200)})
    c0 = len(ha.chiamate)
    s.messaggio(at + muto + "x/current", vecchio)
    esito("avviso conservato vecchio: niente registro ne' notifica", not ha.chiamate[c0:])

    # 8) batteria 12V: la fine di un'occorrenza vecchia non chiude quella nuova
    cod12 = next(k for k, x in diz.items() if x["g"] == "Batteria 12V" and x["p"] == "suono")
    s.messaggio(at + cod12 + "/current", json.dumps({"StartedAt": iso(ora.t)}))
    s.messaggio(at + cod12 + "/current", json.dumps({"StartedAt": iso(ora.t - 86400), "EndedAt": iso(ora.t - 80000)}))
    b = ultimo(pub, s, "batteria_12v")
    esito("12V: fine vecchia ignorata, avviso ancora attivo", cod12 in b["attivi"], b)
    s.messaggio(at + cod12 + "/current", json.dumps({"StartedAt": iso(ora.t), "EndedAt": iso(ora.t + 5)}))
    esito("12V: fine dell'occorrenza giusta, nessun avviso attivo", ultimo(pub, s, "batteria_12v")["stato"] == "Nessun avviso attivo")

    # 9) capacita' misurata
    ora.t += 7200
    v(s, "LifetimeEnergyChargedKwh", 1000.0, True)
    v(s, "BatteryLevel", 40.0, True)
    v(s, "LifetimeEnergyUsed", 500.0, True)
    v(s, "DetailedChargeState", "DetailedChargeStateDisconnected", True)
    v(s, "DetailedChargeState", "DetailedChargeStateCharging")
    esito("aggancio: sessione aperta con contatori e %", s.stato["sessione"].get("energia") == 1000.0 and s.stato["sessione"].get("batteria") == 40.0)
    ora.t += 4 * 3600
    v(s, "LifetimeEnergyChargedKwh", 1030.0, True)
    v(s, "BatteryLevel", 80.0, True)
    v(s, "LifetimeEnergyUsed", 500.5, True)
    v(s, "DetailedChargeState", "DetailedChargeStateDisconnected")
    cap = ultimo(pub, s, "capacita_misurata")
    esito("stacco: capacita' misurata (29,5 kWh netti su 40 punti = 73,8 kWh)", cap and cap["stato"] == 73.8 and cap["ultima_esito"] == "misurata", cap)
    v(s, "DetailedChargeState", "DetailedChargeStateCharging")
    v(s, "BatteryLevel", 85.0, True)
    v(s, "LifetimeEnergyChargedKwh", 1033.0)
    v(s, "DetailedChargeState", "DetailedChargeStateDisconnected")
    esito("stacco con il contatore appena mosso: si aspetta il valore finale", s.stato["sessione"].get("fine_attesa"))
    v(s, "LifetimeEnergyChargedKwh", 1034.0)
    cap = ultimo(pub, s, "capacita_misurata")
    esito("valore finale arrivato: misura scartata (meno di 20 punti)", cap["ultima_esito"].startswith("scartata: meno di 20") and cap["stato"] == 73.8, cap)
    (Path(tmp) / "wb").mkdir()
    s3, pub3, _, _ = nuovo(Path(tmp) / "wb", ha, ora, contatore_wallbox="sensor.wallbox_energia")
    s3.collegato()
    s3.tick()
    for campo, val in (("LifetimeEnergyChargedKwh", 2000.0), ("BatteryLevel", 30.0), ("LifetimeEnergyUsed", 900.0), ("LocatedAtHome", True),
                       ("DetailedChargeState", "DetailedChargeStateDisconnected")):
        v(s3, campo, val, True)
    ha.stati_["sensor.wallbox_energia"] = {"state": "100.0"}
    v(s3, "DetailedChargeState", "DetailedChargeStateCharging")
    ora.t += 5 * 3600
    ha.stati_["sensor.wallbox_energia"] = {"state": "136.0"}
    for campo, val in (("LifetimeEnergyChargedKwh", 2033.0), ("BatteryLevel", 75.0)):
        v(s3, campo, val, True)
    v(s3, "DetailedChargeState", "DetailedChargeStateDisconnected")
    cap = ultimo(pub3, s3, "capacita_misurata")
    esito("con la wallbox a casa: perdite di ricarica (1 - 33/36 = 8,3%)", cap and cap["ultima_perdite_pct"] == 8.3, cap)

    # 10) controlli giornalieri: scadenza e nessun dato
    oggi9 = datetime.fromtimestamp(ora.t).replace(hour=9, minute=1).timestamp()
    ora.t = oggi9 if oggi9 > ora.t else oggi9 + 86400
    (Path(tmp) / "sc").mkdir()
    s4, _, _, _ = nuovo(Path(tmp) / "sc", ha, ora, scadenza_configurazione=(datetime.fromtimestamp(ora.t).date() + timedelta(days=7)).isoformat(),
                        ore_senza_dati=48)
    s4.stato["ultimo_dato"] = ora.t - 72 * 3600
    c0 = len(ha.chiamate)
    s4.tick()
    msg = [d["message"] for d in notifiche(ha, c0)]
    esito("alle 9: promemoria della scadenza fra 7 giorni", any("scade il" in m for m in msg), msg)
    esito("alle 9: nessun dato da oltre 48 ore (muta)", any("Nessun dato" in m for m in msg))
    c0 = len(ha.chiamate)
    s4.tick()
    esito("controlli una volta al giorno", not notifiche(ha, c0))
    (Path(tmp) / "se").mkdir()
    s7, _, _, _ = nuovo(Path(tmp) / "se", ha, ora)
    ha.stati_[sc.ENTITA_SCADENZA] = {"state": (datetime.fromtimestamp(ora.t).date() + timedelta(days=1)).isoformat() + " 00:00:00"}
    c0 = len(ha.chiamate)
    s7.tick()
    esito("scadenza letta dall'entita' scritta da configura_auto.py (domani)", any("scade il" in d["message"] for d in notifiche(ha, c0)))
    (Path(tmp) / "km").mkdir()
    s8, _, _, _ = nuovo(Path(tmp) / "km", ha, ora, contachilometri_confronto="sensor.altra_auto_contachilometri")
    s8.stato["km"], s8.stato["ultimo_dato"] = 1000.0, ora.t - 3 * 3600
    ha.stati_["sensor.altra_auto_contachilometri"] = {"state": "640", "attributes": {"unit_of_measurement": "mi"}}
    c0 = len(ha.chiamate)
    s8.tick()
    esito("telemetria ferma: contachilometri di confronto in miglia convertito (640 mi = 1030 km)",
          any("30 km senza mandare dati" in d["message"] for d in notifiche(ha, c0)), [d["message"][:60] for d in notifiche(ha, c0)])

    # 10b) Home Assistant che non risponde: zona di casa e controlli ritentati; notifica rifiutata con il motivo
    (Path(tmp) / "giu").mkdir()
    ha2 = HAFinto()
    ha2.giu = True
    s9, pub9, righe9, _ = nuovo(Path(tmp) / "giu", ha2, ora)
    s9.tick()
    esito("HA giu' all'avvio: zona di casa non letta, nuovo tentativo dopo un minuto", s9.casa is None and s9.casa_prossima <= ora.t + 60)
    c0 = len(ha2.chiamate)
    ha2.giu = False
    ora.t += 61
    s9.tick()
    esito("HA tornato: zona di casa letta al nuovo tentativo", s9.casa is not None)
    ha3 = HAFinto()
    ha3.stati_[sc.ENTITA_SCADENZA] = {"state": (datetime.fromtimestamp(ora.t).date() + timedelta(days=30)).isoformat()}
    ha3.giu = True
    (Path(tmp) / "giu2").mkdir()
    s10, _, _, _ = nuovo(Path(tmp) / "giu2", ha3, ora)
    s10.tick()
    esito("controllo delle 9 con HA giu': non segnato come fatto", s10.stato["giorni"].get("scadenza") is None)
    ha3.giu = False
    s10.tick()
    esito("subito dopo: niente nuovo tentativo prima dei 5 minuti", not notifiche(ha3))
    ora.t += sc.RIPROVA_S + 1
    s10.tick()
    esito("dopo 5 minuti con HA tornato: promemoria della scadenza (30 giorni)", any("scade il" in d["message"] for d in notifiche(ha3)))
    nt = notifiche(ha3)
    esito("Telegram in testo semplice", nt and all(d.get("parse_mode") == "plain_text" for d in nt))
    esito("le notifiche vanno nella coda delle notifiche", ha3.notifiche_in_coda > 0)
    ha3.rifiuta = 400
    n0 = len(righe9)
    s10.log = righe9.append
    attese = []
    vero_sleep, sc.time.sleep = sc.time.sleep, attese.append
    try:
        s10.notifica("prova di notifica rifiutata")
        ha3.rifiuta = 401
        s10.notifica("prova di notifica con il token sbagliato")
    finally:
        sc.time.sleep = vero_sleep
    rifiutate = [r for r in righe9[n0:] if "NON inviata" in r]
    esito("notifica rifiutata (400, servizio non ancora caricato?): 4 tentativi dopo 20, 60 e 120 s, poi nel log servizio, codice e motivo",
          len(rifiutate) == 2 and "4 tentativi" in rifiutate[0] and "HTTP 400" in rifiutate[0] and "Service not found" in rifiutate[0]
          and attese[:4] == [0, 20, 60, 120], (rifiutate, attese))
    esito("token rifiutato (401): nessun nuovo tentativo", "1 tentativi" in rifiutate[1] and "HTTP 401" in rifiutate[1], rifiutate[-1:])
    ha3.rifiuta = None
    # HA lento: due copie dello stesso controllo in coda non danno due notifiche
    (Path(tmp) / "lento").mkdir()
    ha4 = HAFinto()
    ha4.stati_[sc.ENTITA_SCADENZA] = {"state": (datetime.fromtimestamp(ora.t).date() + timedelta(days=7)).isoformat()}
    ha4.lento = True
    s12, _, _, _ = nuovo(Path(tmp) / "lento", ha4, ora)
    s12.tick()
    ora.t += sc.RIPROVA_S + 1
    s12.tick()
    ha4.lento = False
    ha4.esegui()
    esito("HA lento, due letture della scadenza in coda: un solo promemoria", sum("scade il" in d["message"] for d in notifiche(ha4)) == 1,
          [d["message"][:40] for d in notifiche(ha4)])
    # contachilometri di confronto non ancora pronto (HA che carica le integrazioni): si riprova, non vale come fatto
    (Path(tmp) / "nonpronto").mkdir()
    ha5 = HAFinto()
    ha5.stati_["sensor.altro_km"] = {"state": "unavailable", "attributes": {}}
    s13, _, _, _ = nuovo(Path(tmp) / "nonpronto", ha5, ora, contachilometri_confronto="sensor.altro_km")
    s13.stato["km"], s13.stato["ultimo_dato"] = 1000.0, ora.t - 3 * 3600
    s13.tick()
    esito("contachilometri non disponibile: controllo non segnato come fatto", s13.stato["giorni"].get("ferma") is None)
    ha5.stati_["sensor.altro_km"] = {"state": "1050", "attributes": {"unit_of_measurement": "km"}}
    ora.t += sc.RIPROVA_S + 1
    s13.tick()
    esito("contachilometri pronto al nuovo tentativo: 'telemetria ferma' (50 km)", any("50 km" in d["message"] for d in notifiche(ha5)))
    (Path(tmp) / "wh").mkdir()
    s11, pub11, _, _ = nuovo(Path(tmp) / "wh", ha, ora, contatore_wallbox="sensor.wallbox_wh")
    s11.collegato()
    for campo, val in (("LifetimeEnergyChargedKwh", 3000.0), ("BatteryLevel", 20.0), ("LifetimeEnergyUsed", 900.0), ("LocatedAtHome", True),
                       ("DetailedChargeState", "DetailedChargeStateDisconnected")):
        v(s11, campo, val, True)
    ha.stati_["sensor.wallbox_wh"] = {"state": "500000", "attributes": {"unit_of_measurement": "Wh"}}
    v(s11, "DetailedChargeState", "DetailedChargeStateCharging")
    ora.t += 5 * 3600
    ha.stati_["sensor.wallbox_wh"] = {"state": "540000", "attributes": {"unit_of_measurement": "Wh"}}
    for campo, val in (("LifetimeEnergyChargedKwh", 3036.0), ("BatteryLevel", 70.0)):
        v(s11, campo, val, True)
    v(s11, "DetailedChargeState", "DetailedChargeStateDisconnected")
    cap = ultimo(pub11, s11, "capacita_misurata")
    esito("wallbox in Wh convertita in kWh: perdite 1 - 36/40 = 10%", cap and cap["ultima_perdite_pct"] == 10.0, cap)
    r = subprocess.run([sys.executable, str(Path(sc.QUI) / "scopritore.py")], capture_output=True, text=True, encoding="utf-8", errors="replace",
                       env=dict(os.environ, NOTIFICA_DATI="{non json", FILE_STATO=str(Path(tmp) / "x.json"), PYTHONDONTWRITEBYTECODE="1"), timeout=60)
    esito("opzioni non valide: lo scopritore esce con il codice 78 e lo dice", r.returncode == 78 and "opzioni non valide" in r.stdout, r.stdout[-200:])

    # 11) altro: errori, rimozione, file di stato, VIN mai intero nel log
    c0 = len(ha.chiamate)
    s.messaggio(f"telemetria_tesla/{VIN}/errors/unsupported_field", json.dumps({"CreatedAt": iso(ora.t), "Tags": {"field_name": "X"}}))
    esito("errore recente nel registro", any("unsupported_field" in r for r in registro(ha, c0)))
    rim = dict(s.messaggi_rimozione())
    esito("rimozione: anche capacita', parcheggio e disponibilita'", s.disponibile in rim and s.topic_config("sensor", "capacita_misurata") in rim
          and s.derivato("sentinella_parcheggio") in rim and all(p == "" for p in rim.values()))
    vecchio_stato = Path(tmp) / "v02" / "stato.json"
    vecchio_stato.parent.mkdir()
    vecchio_stato.write_text(json.dumps({"vin": VIN, "attivi": {cod12: "10/03 08:00"}}), encoding="utf-8")
    s5, pub5, _, _ = nuovo(vecchio_stato.parent, ha, ora)
    s5.collegato()
    esito("file di stato della 0.2: avvisi attivi letti", ultimo(pub5, s5, "batteria_12v")["attivi"] == cod12)
    (Path(tmp) / "sn").mkdir()
    s6, _, _, _ = nuovo(Path(tmp) / "sn", sc.NessunHA(), ora)
    s6.collegato()
    v(s6, "SentryMode", "SentryModeStateArmed")
    v(s6, "SentryMode", "SentryModeStatePanic")
    esito("senza token di HA: niente notifiche, nessun errore", True)
    esito("nessuna riga di log con il VIN intero", not any(VIN in r for r in righe))
    for sbagliato in ({"notifica": "notify"}, {"prefisso": "_"}, {"vin": "X"}, {"scadenza_configurazione": "12/11/2027"}):
        try:
            nuovo(tmp, ha, ora, **sbagliato)
            esito(f"opzione non valida rifiutata: {sbagliato}", False)
        except ValueError:
            esito(f"opzione non valida rifiutata: {sbagliato}", True)

print(f"\n{sum(ESITI)}/{len(ESITI)} PASS")
sys.exit(0 if all(ESITI) else 1)
