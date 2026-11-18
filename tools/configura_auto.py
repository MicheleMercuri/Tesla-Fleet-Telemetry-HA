"""Configura l'auto per la Fleet Telemetry: le dice a quale server mandare quali campi, con quale intervallo minimo.

La configurazione va firmata con la chiave dell'applicazione Tesla (la stessa dei comandi): la firma la fa il proxy
ufficiale di Tesla (vehicle-command). Il proxy dell'add-on "Tesla HTTP Proxy" di llamafilm 2.3.0 usa un vehicle-command
del 2024 che NON firma la telemetria: in quel caso si usa firma_una_volta.py, che avvia per pochi minuti il proxy
ufficiale aggiornato e passa qui il suo indirizzo (variabili TELEMETRIA_PROXY e TELEMETRIA_PROXY_CERT).

Uso:  python configura_auto.py anteprima    cosa si manderebbe (niente rete verso Tesla)
      python configura_auto.py verifica     VIN, token, certificato della CA, firmware: tutto pronto per l'invio?
      python configura_auto.py invia        manda la configurazione (create o update); esce con 1 se non accettata
      python configura_auto.py stato        configurazione letta da Tesla: synced, limite, campi, scadenza
      python configura_auto.py errori       errori riportati dall'auto dopo aver ricevuto la configurazione
      python configura_auto.py togli si     toglie la configurazione dall'auto (l'auto smette di mandare)

Token Tesla (token_source in config.json):
- "tesla_custom": quello dell'integrazione tesla_custom, letto solo in memoria da .storage/core.config_entries (cartella
  condivisa config di Samba), mai stampato ne' salvato. MAI rinnovato da qui: il refresh token di Tesla vale una volta
  sola e rinnovarlo da fuori romperebbe tesla_custom. Se e' scaduto si aspetta che tesla_custom lo rinnovi.
- "file": un access token valido scritto in token_file.
Il VIN non viene mai stampato per intero. Ogni chiamata e' una lettura o un comando a pagamento (frazioni di centesimo).
L'auto riceve la configurazione quando si sveglia da sola: niente sveglie forzate.
"""
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime

import comune

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
CFG = comune.carica()
HOST, PORTA = CFG["telemetry_hostname"], int(CFG.get("telemetry_port") or 4443)
CA = comune.percorso(CFG.get("private_dir") or "~/.telemetria_tesla") / "ca.crt"
PROXY = os.environ.get("TELEMETRIA_PROXY") or CFG["proxy_url"]
PROXY_CERT = comune.percorso(os.environ.get("TELEMETRIA_PROXY_CERT") or CFG["proxy_cert"])
HA_REST = CFG["ha_url"].rstrip("/")
GIORNI_VALIDITA = int(CFG.get("config_days") or 360)  # la configurazione scade: rimandarla prima (promemoria in HA)
SCADENZA_HELPER = CFG.get("expiry_helper") or ""

# Campi e intervallo minimo in secondi: l'auto manda un campo solo quando cambia, non piu' spesso dell'intervallo.
# Nomi controllati sul vehicle_data.proto di Tesla (fleet-telemetry, cartella protos).
CAMPI = {
    # Sentinella e sicurezza
    "SentryMode": 1, "Gear": 1, "Locked": 1, "DoorState": 1, "DriverSeatOccupied": 1,
    "FdWindow": 5, "FpWindow": 5, "RdWindow": 5, "RpWindow": 5, "LightsHazardsActive": 5,
    "PairedPhoneKeyAndKeyFobQty": 300, "CenterDisplay": 10,
    # posizione e navigazione: senza percorso MinutesToArrival arriva null; la % all'arrivo arriva null mentre l'auto
    # calcola il percorso (con un intervallo lungo resterebbe vuota a lungo)
    "LocatedAtHome": 10, "LocatedAtWork": 10, "Location": 15,
    "DestinationName": 30, "DestinationLocation": 30, "MinutesToArrival": 30, "MilesToArrival": 30,
    "ExpectedEnergyPercentAtTripArrival": 30, "RouteTrafficMinutesDelay": 30,
    # ricarica
    "ChargePortDoorOpen": 5, "ChargePortLatch": 5, "DetailedChargeState": 10, "ChargeEnableRequest": 5,
    "BatteryLevel": 60, "Soc": 60, "EnergyRemaining": 60, "ChargeLimitSoc": 60,
    # 18/11: potenza della batteria (tensione per corrente) in marcia, in frenata e in carica
    "PackVoltage": 10, "PackCurrent": 10,
    "ACChargingPower": 30, "DCChargingPower": 30, "ChargeAmps": 30,
    "ChargeCurrentRequest": 5, "ChargeCurrentRequestMax": 60, "ChargerPhases": 60,
    # ScheduledDepartureTime non c'e': un'auto con il firmware 2026.38.6 risponde unsupported_field
    "ScheduledChargingMode": 60, "ScheduledChargingPending": 60, "ScheduledChargingStartTime": 60,
    # batteria
    "NominalFullPackEnergyKwh": 3600, "BmsFullchargecomplete": 10, "BatteryHeaterOn": 10,
    "ModuleTempMin": 300, "ModuleTempMax": 300,
    # clima e sedili
    "InsideTemp": 300, "OutsideTemp": 300, "HvacPower": 10, "HvacLeftTemperatureRequest": 30,
    "PreconditioningEnabled": 30, "ClimateKeeperMode": 30, "CabinOverheatProtectionMode": 300,
    "SeatHeaterLeft": 10, "SeatHeaterRight": 10, "SeatHeaterRearLeft": 10, "SeatHeaterRearRight": 10,
    "SeatHeaterRearCenter": 10, "AutoSeatClimateLeft": 10, "AutoSeatClimateRight": 10,
    # contatori e gomme (LifetimeEnergyGainedRegen: in pratica l'auto non lo manda, issue #398 e #436 di fleet-telemetry)
    "Odometer": 300, "LifetimeEnergyChargedKwh": 300, "LifetimeDcChargeEnergyKwh": 300,
    "LifetimeEnergyUsed": 300, "LifetimeEnergyGainedRegen": 300,
    "TpmsPressureFl": 600, "TpmsPressureFr": 600, "TpmsPressureRl": 600, "TpmsPressureRr": 600,
    "TpmsSoftWarnings": 60, "TpmsHardWarnings": 60,
    # software
    "Version": 3600, "SoftwareUpdateVersion": 3600, "SoftwareUpdateInProgress": 60, "ServiceMode": 300,
}
# due campi chiedono un firmware recente (vehicle_data.proto): LifetimeEnergyChargedKwh 2026.32,
# LifetimeDcChargeEnergyKwh 2026.32.300 o 2026.38.6. Con un firmware piu' vecchio l'auto risponde unsupported_field per
# quei campi (configura_auto.py errori) e i due sensori restano sconosciuti: e' solo un avviso.
FIRMWARE_MINIMO = (2026, 38, 6)
HELPER_PREDEFINITO = "input_datetime.telemetria_tesla_scadenza"


def token_ha():
    return comune.token_ha(CFG)


def stato_ha(entita):
    r = urllib.request.Request(HA_REST + "/api/states/" + entita, headers={"Authorization": "Bearer " + token_ha()})
    try:
        return json.loads(urllib.request.urlopen(r, timeout=30).read())
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def vin():
    """Dal campo vin di config.json, o dall'attributo vin di un'entita' (per esempio binary_sensor.<auto>_online di
    tesla_custom)."""
    v = (CFG.get("vin") or "").strip()
    if not v and CFG.get("vin_entity"):
        v = ((stato_ha(CFG["vin_entity"]) or {}).get("attributes") or {}).get("vin") or ""
    v = str(v).strip().upper()
    if not (len(v) == 17 and v.isalnum()):
        raise SystemExit("VIN non trovato: scriverlo in config.json (vin) o indicare vin_entity")
    return v


def maschera(testo, v):
    """VIN e nome del server mascherati in quello che si stampa."""
    return testo.replace(v, "..." + v[-4:]).replace(HOST, comune.maschera_host(HOST))


def token(margine=300):
    """Access token Tesla, solo in memoria (vedi la nota in testa). margine: secondi di validita' minima richiesti."""
    if CFG.get("token_source") == "file":
        acc = comune.percorso(CFG["token_file"]).read_text(encoding="utf-8").strip()
        if not acc:
            raise SystemExit("token_file vuoto")
        return acc
    dati = json.loads(comune.percorso(CFG["core_config_entries"]).read_text(encoding="utf-8"))
    voci = [e for e in dati["data"]["entries"] if e.get("domain") == "tesla_custom"]
    if len(voci) != 1:
        raise SystemExit(f"voci tesla_custom: {len(voci)}")
    d = voci[0].get("data", {})
    acc, scad = d.get("access_token"), d.get("expiration")
    if not acc or not scad:
        raise SystemExit(f"token non trovato; chiavi presenti (solo i nomi): {sorted(d.keys())}")
    resta = float(scad) - time.time()
    if resta < margine:
        raise SystemExit(f"token di tesla_custom valido ancora {resta / 60:.0f} minuti, ne servono {margine / 60:.0f}: "
                         "riprovare dopo il suo rinnovo")
    print(f"token di tesla_custom valido ancora {resta / 3600:.1f} ore (non stampato)")
    return acc


def chiama(metodo, percorso, corpo=None):
    ctx = ssl.create_default_context(cafile=str(PROXY_CERT))
    ctx.check_hostname = False  # certificato del proxy per il suo nome interno: si fissa il certificato, non il nome
    r = urllib.request.Request(PROXY + percorso, method=metodo, data=None if corpo is None else json.dumps(corpo).encode(),
                               headers={"Authorization": "Bearer " + token(), "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=90, context=ctx) as x:
            return x.status, json.loads(x.read() or b"null")
    except urllib.error.HTTPError as e:
        testo = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(testo)
        except ValueError:
            return e.code, testo[:500]


def configurazione():
    return {
        "hostname": HOST,
        "port": PORTA,
        "ca": CA.read_text(encoding="ascii"),
        "prefer_typed": True,
        "exp": int(time.time()) + GIORNI_VALIDITA * 86400,
        # solo gli avvisi che l'auto mostra sullo schermo (gruppo customer); i "service" sono diagnostica per l'officina
        "alert_types": ["customer"],
        "fields": {k: {"interval_seconds": s} for k, s in CAMPI.items()},
    }


def anteprima():
    c = configurazione()
    print(f"server {comune.maschera_host(c['hostname'])}:{c['port']}, CA {CA.name} ({len(c['ca'])} caratteri), scadenza "
          f"{datetime.fromtimestamp(c['exp']):%d/%m/%Y}, {len(c['fields'])} campi:")
    for k, v in c["fields"].items():
        print(f"  {k}: ogni {v['interval_seconds']} s al massimo, solo se cambia")


def avviso_firmware():
    """Solo un avviso: con un firmware vecchio alcuni campi tornano come unsupported_field."""
    if not CFG.get("firmware_entity"):
        print("firmware dell'auto non controllato (firmware_entity vuoto)")
        return
    s = stato_ha(CFG["firmware_entity"]) or {}
    ver = str((s.get("attributes") or {}).get("installed_version") or "")
    numeri = tuple(int(x) for x in ver.split(" ")[0].split(".")[:3] if x.isdigit())
    if len(numeri) == 3 and numeri >= FIRMWARE_MINIMO:
        print("firmware dell'auto:", ver)
    else:
        print(f"AVVISO firmware dell'auto {ver or '?'}: sotto il 2026.38.6 alcuni contatori Lifetime potrebbero tornare come "
              "unsupported_field (vedere configura_auto.py errori dopo la sincronizzazione)")


def verifica():
    """Tutto cio' che serve all'invio, senza contattare Tesla: esce con 1 al primo problema."""
    v = vin()
    print("VIN trovato: ..." + v[-4:])
    if not CA.exists():
        raise SystemExit(f"manca il certificato della CA ({CA}): prima crea_certificati.py crea")
    print("certificato della CA:", CA.name)
    srv = CA.with_name("server.crt")
    if srv.exists():
        from cryptography import x509
        c = x509.load_pem_x509_certificates(srv.read_bytes())[0]
        try:
            nomi = c.extensions.get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(x509.DNSName)
        except x509.ExtensionNotFound:
            nomi = []
        if HOST not in nomi:
            raise SystemExit("server.crt non e' per telemetry_hostname: rifarlo con crea_certificati.py rinnova "
                             "(la CA resta la stessa) e ricopiarlo nell'add-on")
        print("certificato del server per", comune.maschera_host(HOST))
    else:
        print("AVVISO: server.crt non trovato in private_dir, nome del server non controllato")
    token(margine=1800)  # la firma (add-on temporaneo compreso) puo' durare parecchi minuti
    avviso_firmware()
    if SCADENZA_HELPER and SCADENZA_HELPER != HELPER_PREDEFINITO and stato_ha(SCADENZA_HELPER) is None:
        raise SystemExit(f"expiry_helper {SCADENZA_HELPER} non esiste in Home Assistant: crearlo o lasciare il valore predefinito")
    print("verifica: tutto pronto per l'invio")


def salva_scadenza(exp):
    """La scadenza va in un input_datetime di HA: l'add-on lo legge e ricorda di rimandare la configurazione."""
    if not SCADENZA_HELPER:
        print("scadenza non salvata in Home Assistant (expiry_helper vuoto): annotarla",
              datetime.fromtimestamp(exp).strftime("%d/%m/%Y"))
        return
    import websocket
    quando = datetime.fromtimestamp(exp)
    c = websocket.create_connection(comune.ws_ha(CFG), timeout=30)
    try:
        c.recv()
        c.send(json.dumps({"type": "auth", "access_token": token_ha()}))
        c.recv()
        # l'helper si crea da solo solo con l'id predefinito (HA deriva l'id dal nome); un id diverso va creato a mano
        if stato_ha(SCADENZA_HELPER) is None and SCADENZA_HELPER == HELPER_PREDEFINITO:
            c.send(json.dumps({"id": 1, "type": "input_datetime/create", "name": "Telemetria Tesla scadenza",
                               "has_date": True, "has_time": False, "icon": "mdi:calendar-alert"}))
            r = json.loads(c.recv())
            print("helper della scadenza creato:", r.get("success"), (r.get("error") or ""))
            time.sleep(2)
    finally:
        c.close()
    r = urllib.request.Request(HA_REST + "/api/services/input_datetime/set_datetime", method="POST",
                               data=json.dumps({"entity_id": SCADENZA_HELPER, "date": quando.strftime("%Y-%m-%d")}).encode(),
                               headers={"Authorization": "Bearer " + token_ha(), "Content-Type": "application/json"})
    urllib.request.urlopen(r, timeout=30).read()
    print("scadenza salvata in HA:", (stato_ha(SCADENZA_HELPER) or {}).get("state"))


def invia():
    v = vin()
    avviso_firmware()
    anteprima()
    cfg = configurazione()
    codice, risposta = chiama("POST", "/api/1/vehicles/fleet_telemetry_config", {"vins": [v], "config": cfg})
    print("risposta", codice, maschera(json.dumps(risposta, ensure_ascii=False), v)[:1500])
    res = (risposta or {}).get("response") if isinstance(risposta, dict) else None
    if codice == 200 and isinstance(res, dict) and not (res.get("skipped_vehicles") or {}):
        try:
            salva_scadenza(cfg["exp"])
        except Exception as e:  # noqa: BLE001 la configurazione e' gia' accettata: la scadenza non salvata e' solo un avviso
            print(f"AVVISO: configurazione accettata, ma scadenza non salvata in HA ({type(e).__name__}): annotarla",
                  datetime.fromtimestamp(cfg["exp"]).strftime("%d/%m/%Y"))
        print("ora l'auto la riceve al prossimo risveglio; poi: python configura_auto.py stato / errori")
    else:
        print("CONFIGURAZIONE NON ACCETTATA o auto saltata (per esempio missing_key: chiave non abbinata): vedi la risposta sopra")
        sys.exit(1)


def stato():
    v = vin()
    codice, r = chiama("GET", f"/api/1/vehicles/{v}/fleet_telemetry_config")
    if codice != 200 or not isinstance(r, dict):
        print("risposta", codice, maschera(json.dumps(r, ensure_ascii=False), v)[:800])
        return
    res = r.get("response") or {}
    cfg = res.get("config") or {}
    print("synced:", res.get("synced"), "| limit_reached:", res.get("limit_reached"), "| key_paired:", res.get("key_paired"))
    if cfg:
        print("server:", comune.maschera_host(cfg.get("hostname")), cfg.get("port"), "| campi:", len(cfg.get("fields") or {}),
              "| scade:", datetime.fromtimestamp(cfg["exp"]).strftime("%d/%m/%Y") if cfg.get("exp") else "?")
    altre = {k: res[k] for k in res if k not in ("config", "synced", "limit_reached", "key_paired")}
    if altre:
        print("altro:", maschera(json.dumps(altre, ensure_ascii=False), v)[:500])


def errori():
    v = vin()
    codice, r = chiama("GET", f"/api/1/vehicles/{v}/fleet_telemetry_errors")
    print("risposta", codice, maschera(json.dumps(r, ensure_ascii=False), v)[:3000])


def togli():
    if sys.argv[2:3] != ["si"]:
        raise SystemExit("per togliere la configurazione dall'auto: python configura_auto.py togli si")
    v = vin()
    codice, r = chiama("DELETE", f"/api/1/vehicles/{v}/fleet_telemetry_config")
    print("risposta", codice, maschera(json.dumps(r, ensure_ascii=False), v)[:800])


if __name__ == "__main__":
    comandi = {"anteprima": anteprima, "verifica": verifica, "invia": invia, "stato": stato, "errori": errori, "togli": togli}
    cmd = sys.argv[1] if len(sys.argv) > 1 else "anteprima"
    if cmd not in comandi:
        raise SystemExit(__doc__)
    comandi[cmd]()
