"""Firma e manda la configurazione della telemetria con il proxy ufficiale aggiornato di Tesla (vehicle-command 0.4.1),
per chi usa l'add-on "Tesla HTTP Proxy" di llamafilm (https://github.com/llamafilm/tesla-http-proxy-addon).

Perche': quell'add-on (2.3.0) usa il codice di Tesla del 2024 e inoltra la configurazione senza firmarla (Tesla risponde
"must be called through the Vehicle Command HTTP Proxy"). La chiave dell'applicazione sta cifrata nel suo portachiavi
(gpg + pass in /data) e l'add-on non la esporta. Questo script la prende, la usa per pochi minuti e la cancella:
nessuna copia della chiave resta in giro (e' anche una chiave virtuale dell'auto).

Passi:
0. controlli PRIMA di toccare la chiave: cartelle condivise, SSH e comando ha, gpg, chiave pubblicata, VIN, token,
   certificato della CA (configura_auto.py verifica), carica dell'auto, backup di Home Assistant in arrivo;
1. backup parziale del solo add-on proxy, in locale (senza password, nella cartella backup del server);
2. dal backup si prendono solo portachiavi GPG e archivio pass, in una cartella temporanea locale (0700);
3. gpg decifra la voce (passphrase vuota, come la crea l'add-on). Controllo: la parte pubblica deve essere quella
   pubblicata (public_key_url), altrimenti ci si ferma;
4. add-on temporaneo local_firma_telemetria (porta temp_proxy_port, solo in casa) con la chiave e un certificato TLS
   usa e getta; il file della chiave si toglie appena il proxy l'ha caricata;
5. configura_auto.py invia attraverso il proxy temporaneo;
6. pulizia SEMPRE, un passo alla volta (un passo che fallisce non salta gli altri): cartella locale, backup temporaneo,
   file sul server, add-on temporaneo; poi verifica con il Supervisor. La chiave non viene mai stampata.

ATTENZIONE: per qualche minuto il backup parziale (senza password) contiene il portachiavi dell'add-on, cioe' in pratica la
chiave dell'applicazione. Se un add-on o un agente di backup copia nel cloud anche i backup creati da altri (per esempio
un add-on di sincronizzazione), sospenderlo durante la procedura. Lo script si ferma se un backup automatico di Home
Assistant e' previsto entro 30 minuti.

Serve: add-on SSH con accesso a chiave (chiave dell'host gia' accettata: lo script usa BatchMode) e comando ha; add-on
Samba (cartelle addons, addon_configs, backup; su Linux e macOS montarle e indicare i percorsi in config.json); gpg (su
Windows di norma quello di Git for Windows, preso da solo se c'e'; con Gpg4win indicare gpg_exe e gpgconf_exe).
Uso:  python firma_una_volta.py            (solo con l'auto non in carica: il backup puo' fermare il proxy per qualche secondo)
Alla scadenza della configurazione o per cambiare i campi si rilancia uguale.
"""
import io
import ipaddress
import json
import os
import shutil
import signal
import socket
import ssl
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import websocket
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

import comune

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
QUI = Path(__file__).resolve().parent
CFG = comune.carica()
SERVER = comune.host_ha(CFG)
PORTA_TEMP = int(CFG.get("temp_proxy_port") or 4446)
PROXY_ADDON = CFG.get("proxy_addon_slug") or "c03d64a7_tesla_http_proxy"
TEMP_SLUG = "local_firma_telemetria"
TEMP_DIR_ADDON = comune.percorso(CFG["share_addons"]) / "firma_telemetria"
TEMP_CONFIG = comune.percorso(CFG["share_addon_configs"]) / TEMP_SLUG
BACKUP_DIR = comune.percorso(CFG["share_backup"])
CHIAVE_PUBBLICATA = CFG["public_key_url"]
TOK = comune.token_ha(CFG)
SU_WINDOWS = os.name == "nt"
GIT_BIN = Path(r"C:\Program Files\Git\usr\bin")
NOME_BACKUP = "temp_firma_telemetria"
STATO = {"slug": None, "tmp": None, "cli": "apps", "backup_richiesto": False, "prima": None}


def eseguibile(chiave, nome):
    """gpg, gpgconf o ssh: da config.json; su Windows, se non indicati, quelli di Git for Windows quando ci sono."""
    v = CFG.get(chiave) or nome
    if SU_WINDOWS and v == nome and (GIT_BIN / f"{nome}.exe").exists():
        return str(GIT_BIN / f"{nome}.exe")
    return v


GPG, GPGCONF, SSH = eseguibile("gpg_exe", "gpg"), eseguibile("gpgconf_exe", "gpgconf"), eseguibile("ssh_exe", "ssh")
# il gpg di Git for Windows (cartella ...\Git\usr\bin) vuole percorsi in stile /c/...; Gpg4win e gli altri no
GPG_MSYS = SU_WINDOWS and Path(GPG).parent.as_posix().lower().endswith("git/usr/bin")


def ssh(cmd, timeout=180):
    """Comando sul server via SSH; non solleva eccezioni (la pulizia deve poter continuare)."""
    try:
        r = subprocess.run([SSH, "-i", str(comune.percorso(CFG["ssh_key"])), "-o", "BatchMode=yes", "-o", "ConnectTimeout=15",
                            CFG["ssh_host"], cmd], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, f"ssh non riuscito: {type(e).__name__}"
    out = "\n".join(x for x in (r.stdout + r.stderr).splitlines() if "deprecated" not in x)
    return r.returncode, comune.maschera_vin(out)


def ha_ws(tipo, **k):
    c = websocket.create_connection(comune.ws_ha(CFG), timeout=600)
    try:
        c.recv()
        c.send(json.dumps({"type": "auth", "access_token": TOK}))
        c.recv()
        c.send(json.dumps({"id": 1, "type": tipo, **k}))
        while True:
            r = json.loads(c.recv())
            if r.get("id") == 1:
                return r
    finally:
        c.close()


def supervisor(endpoint, metodo="get", dati=None):
    k = {"endpoint": endpoint, "method": metodo, "timeout": 580}
    if dati is not None:
        k["data"] = dati
    return ha_ws("supervisor/api", **k)


def stato_ha(e):
    r = urllib.request.Request(CFG["ha_url"].rstrip("/") + f"/api/states/{e}", headers={"Authorization": "Bearer " + TOK})
    return json.loads(urllib.request.urlopen(r, timeout=30).read()).get("state")


def unix(p):
    """Percorso per il gpg di Git per Windows (MSYS): C:\\Users\\x -> /c/Users/x. Altrimenti resta com'e'."""
    p = Path(p).resolve()
    if not GPG_MSYS:
        return str(p)
    return "/" + p.drive[0].lower() + p.as_posix()[2:]


# ---------------------------------------------------------------- 0. controlli prima di toccare la chiave
def controlli_iniziali():
    for nome, cartella in (("backup", BACKUP_DIR), ("addons", TEMP_DIR_ADDON.parent), ("addon_configs", TEMP_CONFIG.parent)):
        if not cartella.is_dir():
            raise SystemExit(f"cartella condivisa {nome} non raggiungibile ({cartella}): su Windows aprirla prima con le "
                             "credenziali di Samba, su Linux e macOS montarla e indicare il percorso in config.json")
    rc, out = ssh("ha apps --help >/dev/null 2>&1 && echo apps || echo addons")
    if rc != 0 or out.strip().splitlines()[-1:] not in (["apps"], ["addons"]):
        raise SystemExit("SSH non riuscito (chiave, porta o chiave dell'host non ancora accettata): " + out[-200:])
    STATO["cli"] = out.strip().splitlines()[-1]
    for nome, prog in (("gpg", GPG), ("gpgconf", GPGCONF)):
        try:
            ok = subprocess.run([prog, "--version"], capture_output=True, timeout=30).returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            ok = False
        if not ok:
            raise SystemExit(f"{nome} non trovato ({prog}): indicarne il percorso in config.json ({nome}_exe)")
    pubblica = serialization.load_pem_public_key(
        urllib.request.urlopen(urllib.request.Request(CHIAVE_PUBBLICATA, headers={"User-Agent": "curl/8"}), timeout=20).read())
    r = subprocess.run([sys.executable, str(QUI / "configura_auto.py"), "verifica"], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=180)
    print((r.stdout + r.stderr).strip())
    if r.returncode != 0:
        raise SystemExit("configura_auto.py verifica non riuscito: niente e' stato toccato")
    condizioni()
    print("controlli iniziali: tutto a posto")
    return pubblica


def condizioni():
    """Le entita' di charging_check_entities (per esempio lo switch di carica dell'auto) devono essere spente, e nessun
    backup automatico di Home Assistant deve partire mentre la chiave e' fuori dal portachiavi (30 minuti di margine)."""
    accese = [e for e in CFG.get("charging_check_entities") or [] if stato_ha(e) == "on"]
    if accese:
        raise SystemExit(f"auto in carica ({', '.join(accese)}): il backup del proxy potrebbe fermarlo per qualche secondo. "
                         "Riprovare a carica finita.")
    info, conf = ha_ws("backup/info"), ha_ws("backup/config/info")
    if not info.get("success") or not conf.get("success"):
        raise SystemExit("stato dei backup di Home Assistant non letto: serve un token di un amministratore")
    stato = (info.get("result") or {}).get("state")
    if stato not in (None, "idle"):
        raise SystemExit(f"un backup di Home Assistant e' in corso ({stato}): riprovare quando e' finito")
    prossimo = ((conf.get("result") or {}).get("config") or {}).get("next_automatic_backup")
    if prossimo:
        mancano = (datetime.fromisoformat(prossimo) - datetime.now(timezone.utc)).total_seconds() / 60
        if -5 < mancano < 30:
            raise SystemExit(f"backup automatico di Home Assistant tra {mancano:.0f} minuti: riprovare dopo")


# ---------------------------------------------------------------- 1-3. backup, portachiavi, chiave
def backup_parziale():
    prima = {p.name for p in BACKUP_DIR.glob("*.tar")}
    elenco = supervisor("/backups")  # fotografia: un backup nuovo senza nome dopo questo istante e' il nostro
    if elenco.get("success"):
        STATO["prima"] = {b.get("slug") for b in (elenco.get("result") or {}).get("backups", [])}
    STATO["backup_richiesto"] = True  # da qui in poi la pulizia cerca il backup anche per nome
    r = supervisor("/backups/new/partial", "post", {"name": NOME_BACKUP, "addons": [PROXY_ADDON],
                                                     "homeassistant": False, "compressed": True, "location": ".local"})
    if not r.get("success"):
        raise SystemExit(f"backup non riuscito: {str(r.get('error'))[:200]}")
    STATO["slug"] = (r.get("result") or {}).get("slug")  # subito: la pulizia deve poterlo cancellare anche dopo un errore
    print("backup parziale del proxy creato:", STATO["slug"])
    for _ in range(60):
        nuovi = [p for p in BACKUP_DIR.glob("*.tar") if p.name not in prima]
        for p in nuovi:
            with tarfile.open(p) as t:
                try:
                    info = json.loads(t.extractfile("./backup.json").read())
                except KeyError:
                    info = json.loads(t.extractfile("backup.json").read())
            if info.get("slug") == STATO["slug"]:
                return p
        time.sleep(2)
    raise SystemExit("file del backup non trovato nella cartella backup")


def estrai(file_backup):
    """Solo data/gnugpg e data/password-store, controllando i percorsi (niente uscite dalla cartella)."""
    tmp = STATO["tmp"]
    with tarfile.open(file_backup) as esterno:
        interno = next(m for m in esterno.getmembers() if m.name.endswith(f"{PROXY_ADDON}.tar.gz"))
        dati = esterno.extractfile(interno).read()
    presi = 0
    with tarfile.open(fileobj=io.BytesIO(dati), mode="r:gz") as t:
        for m in t.getmembers():
            nome = m.name.lstrip("./")
            if not (nome.startswith("data/gnugpg") or nome.startswith("data/password-store")):
                continue
            if m.issym() or m.islnk() or ".." in Path(nome).parts or m.isdev():
                continue
            dest = tmp / nome
            if m.isdir():
                dest.mkdir(parents=True, exist_ok=True)
            elif m.isfile():
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(t.extractfile(m).read())
                presi += 1
    print("dal backup presi", presi, "file del portachiavi (in una cartella temporanea locale)")
    return tmp / "data" / "gnugpg", tmp / "data" / "password-store"


def prepara_gpg(gnugpg):
    """Portachiavi pulito con le sole chiavi segrete dell'add-on e la sua chiave pubblica, senza le impostazioni
    dell'add-on (che con GnuPG 2.4 recenti usa keyboxd, non sempre disponibile nel gpg di Git)."""
    tmp = STATO["tmp"]
    print("portachiavi dell'add-on:", sorted(p.name for p in gnugpg.iterdir()))
    nuovo = tmp / "g"
    nuovo.mkdir()
    shutil.copytree(gnugpg / "private-keys-v1.d", nuovo / "private-keys-v1.d")
    keyboxd = (gnugpg / "common.conf").exists() and "use-keyboxd" in (gnugpg / "common.conf").read_text(errors="replace")
    if (gnugpg / "pubring.kbx").exists() and not keyboxd:
        shutil.copyfile(gnugpg / "pubring.kbx", nuovo / "pubring.kbx")
        print("chiave pubblica da pubring.kbx")
    else:
        import sqlite3
        db = gnugpg / "public-keys.d" / "pubring.db"
        con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
        blocchi = []
        for (tabella,) in con.execute("select name from sqlite_master where type = 'table'"):
            for riga in con.execute(f'select * from "{tabella}"'):
                blocchi += [v for v in riga if isinstance(v, (bytes, bytearray)) and len(v) > 50 and v[0] in (0x99, 0x98, 0xC6)]
        con.close()
        print("chiavi pubbliche da keyboxd:", len(blocchi))
        for i, b in enumerate(blocchi):
            f = tmp / f"pub{i}.gpg"
            f.write_bytes(bytes(b))
            r = subprocess.run([GPG, "--homedir", unix(nuovo), "--batch", "--yes", "--quiet", "--import", unix(f)],
                               capture_output=True, timeout=60)
            print("  importata:", r.returncode == 0, r.stderr.decode("utf-8", "replace")[-120:].strip())
    return nuovo


def chiave_privata(gnupg, store, pubblica):
    voci = [p for p in store.rglob("*.gpg") if p.name != ".gpg-id"]
    if not voci:
        raise SystemExit("nessuna voce nel portachiavi pass")
    gnupg = prepara_gpg(gnupg)
    for voce in voci:
        r = subprocess.run([GPG, "--homedir", unix(gnupg), "--batch", "--yes", "--quiet", "--pinentry-mode", "loopback",
                            "--passphrase", "", "--decrypt", unix(voce)], capture_output=True, timeout=60)
        if r.returncode != 0:
            print("voce non decifrata:", voce.name, r.stderr.decode("utf-8", "replace")[-200:])
            continue
        grezzo = r.stdout
        try:
            dato = json.loads(grezzo)
            import base64
            grezzo = base64.b64decode(dato["Data"]) if isinstance(dato, dict) and "Data" in dato else grezzo
        except (ValueError, KeyError):
            pass
        k = None
        if grezzo.lstrip().startswith(b"-----BEGIN"):
            k = serialization.load_pem_private_key(grezzo, password=None)
        elif len(grezzo) == 32:
            k = ec.derive_private_key(int.from_bytes(grezzo, "big"), ec.SECP256R1())
        else:
            try:
                k = serialization.load_der_private_key(grezzo, password=None)
            except ValueError:
                pass
        if k is None:
            print("voce", voce.name, ": formato non riconosciuto (", len(grezzo), "byte )")
            continue
        uguale = k.public_key().public_numbers() == pubblica.public_numbers()
        print("voce", voce.name, ": chiave EC", k.curve.name, "| corrisponde alla chiave pubblicata:", uguale)
        if uguale:
            return k
    raise SystemExit("nessuna chiave del portachiavi corrisponde a quella pubblicata: mi fermo")


# ---------------------------------------------------------------- 4-5. proxy temporaneo e invio
def certificato_usa_e_getta():
    k = ec.generate_private_key(ec.SECP256R1())
    nome = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "firma-telemetria-temporanea")])
    ora = datetime.now(timezone.utc) - timedelta(minutes=5)
    try:
        san = x509.IPAddress(ipaddress.ip_address(SERVER))
    except ValueError:
        san = x509.DNSName(SERVER)
    c = (x509.CertificateBuilder().subject_name(nome).issuer_name(nome).public_key(k.public_key())
         .serial_number(x509.random_serial_number()).not_valid_before(ora).not_valid_after(ora + timedelta(days=2))
         .add_extension(x509.SubjectAlternativeName([san]), critical=False)
         .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
         .sign(k, hashes.SHA256()))
    return (k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()),
            c.public_bytes(serialization.Encoding.PEM))


def scrivi_server(p, dati):
    tmp = p.with_name(p.name + ".parziale")
    tmp.write_bytes(dati)
    os.replace(tmp, p)


def addon_temporaneo(chiave):
    cli = STATO["cli"]
    TEMP_DIR_ADDON.mkdir(exist_ok=True)
    modello = (QUI / "firma_temporanea" / "config.yaml.modello").read_bytes().replace(b"\r\n", b"\n")
    scrivi_server(TEMP_DIR_ADDON / "config.yaml", modello.replace(b"4443/tcp: 4446", f"4443/tcp: {PORTA_TEMP}".encode()))
    scrivi_server(TEMP_DIR_ADDON / "Dockerfile", (QUI / "firma_temporanea" / "Dockerfile").read_bytes().replace(b"\r\n", b"\n"))
    ssh("ha store reload")
    print("costruzione dell'add-on temporaneo (scarica l'immagine ufficiale di Tesla)...")
    rc, out = ssh(f"ha {cli} install {TEMP_SLUG} --no-progress", timeout=1500)
    if rc != 0:
        raise SystemExit("add-on temporaneo non installato: " + out[-400:])
    TEMP_CONFIG.mkdir(exist_ok=True)
    tls_key, tls_cert = certificato_usa_e_getta()
    (STATO["tmp"] / "tls-cert.pem").write_bytes(tls_cert)
    scrivi_server(TEMP_CONFIG / "tls-key.pem", tls_key)
    scrivi_server(TEMP_CONFIG / "tls-cert.pem", tls_cert)
    scrivi_server(TEMP_CONFIG / "fleet-key.pem", chiave.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                                                                       serialization.NoEncryption()))
    ssh(f"ha {cli} start {TEMP_SLUG} --no-progress", timeout=300)
    ctx = ssl.create_default_context(cafile=str(STATO["tmp"] / "tls-cert.pem"))
    ctx.check_hostname = False
    pronto = False
    for _ in range(30):
        time.sleep(2)
        try:
            with socket.create_connection((SERVER, PORTA_TEMP), timeout=5) as s, ctx.wrap_socket(s):
                pronto = True
        except OSError:
            continue
        break
    if not pronto:
        _, log = ssh(f"ha {cli} logs {TEMP_SLUG} 2>&1 | tail -n 15")
        raise SystemExit("il proxy temporaneo non risponde:\n" + log)
    print(f"proxy temporaneo pronto sulla {PORTA_TEMP}")
    # il proxy legge la chiave all'avvio: il file sul server non serve piu' (fuori dal try: un errore qui deve vedersi)
    (TEMP_CONFIG / "fleet-key.pem").unlink()
    print("file della chiave tolto dal server (resta solo nella memoria del proxy fino alla pulizia)")


def invia():
    env = dict(os.environ, TELEMETRIA_PROXY=f"https://{SERVER}:{PORTA_TEMP}", TELEMETRIA_PROXY_CERT=str(STATO["tmp"] / "tls-cert.pem"))
    r = subprocess.run([sys.executable, str(QUI / "configura_auto.py"), "invia"] + [a for a in sys.argv[1:] if a == "--forza"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=300)
    righe = [x for x in (r.stdout + r.stderr).splitlines() if "al massimo, solo se cambia" not in x]
    print("\n".join(righe))
    _, log = ssh(f"ha {STATO['cli']} logs {TEMP_SLUG} 2>&1 | grep -iE 'telemetry|error|warn' | tail -n 8")
    if log.strip():
        print("log del proxy temporaneo:\n" + log)
    return r.returncode == 0


# ---------------------------------------------------------------- 6. pulizia
def passo(nome, funzione):
    try:
        funzione()
    except Exception as e:  # noqa: BLE001 un passo che fallisce non deve saltare gli altri
        print(f"PULIZIA, passo non riuscito ({nome}): {type(e).__name__} {str(e)[:150]}")


def svuota(cartella):
    if cartella.exists():
        for f in cartella.iterdir():
            f.unlink()
        cartella.rmdir()


def backup_temporanei():
    """Backup con il nome di questo script (anche di giri vecchi), con lo slug di questo giro, o nuovi e senza nome rispetto
    alla fotografia presa prima della richiesta. Un backup con un altro nome non si tocca. None se l'elenco non si legge."""
    r = supervisor("/backups")
    if not r.get("success"):
        return None
    prima = STATO["prima"]
    return [b for b in (r.get("result") or {}).get("backups", [])
            if b.get("name") == NOME_BACKUP or (STATO["slug"] and b.get("slug") == STATO["slug"])
            or (prima is not None and b.get("slug") not in prima and not b.get("name"))]


def addon_temporaneo_installato():
    """True se installato, False se tolto o inesistente, None se il Supervisor non risponde."""
    try:
        r = supervisor(f"/addons/{TEMP_SLUG}/info")
    except Exception:  # noqa: BLE001
        return None
    if r.get("success"):
        return bool((r.get("result") or {}).get("version"))
    errore = str(r.get("error") or "").lower()
    return False if any(k in errore for k in ("not exist", "not found", "unknown", "doesn't exist", "does not exist")) else None


def pulizia():
    """Ogni passo protetto; un secondo Ctrl+C non la interrompe. Restituisce l'elenco di cio' che e' rimasto."""
    vecchio = signal.signal(signal.SIGINT, signal.SIG_IGN)
    try:
        return _pulizia()
    finally:
        signal.signal(signal.SIGINT, vecchio)


def _pulizia():
    print("pulizia...")
    tmp, slug, cli = STATO["tmp"], STATO["slug"], STATO["cli"]
    if tmp:
        for home in (tmp / "g", tmp / "data" / "gnugpg"):
            if home.exists():
                passo("agente gpg", lambda h=home: subprocess.run([GPGCONF, "--homedir", unix(h), "--kill", "gpg-agent"],
                                                                  capture_output=True, timeout=30))
        passo("attesa dell'agente", lambda: time.sleep(1))
        passo("cartella locale", lambda: shutil.rmtree(tmp, ignore_errors=True))
    if slug:
        passo("backup temporaneo", lambda: print("backup temporaneo cancellato:", supervisor(f"/backups/{slug}", "delete").get("success")))
    if STATO["backup_richiesto"]:
        # anche per nome: risposta del Supervisor persa (slug ignoto) o backup lasciati da giri vecchi
        def per_nome():
            for b in backup_temporanei() or []:
                print("backup temporaneo trovato per nome, cancellato:", supervisor(f"/backups/{b['slug']}", "delete").get("success"))
        passo("backup temporanei per nome", per_nome)
    for cartella in (TEMP_CONFIG, TEMP_DIR_ADDON):
        passo(f"cartella {cartella.name} sul server", lambda c=cartella: svuota(c))
    passo("add-on temporaneo", lambda: ssh(f"ha {cli} stop {TEMP_SLUG} --no-progress; ha {cli} uninstall {TEMP_SLUG} --no-progress", timeout=300))
    passo("store", lambda: ssh("ha store reload"))
    # verifica finale chiesta al Supervisor e alle cartelle: "completa" solo se tutto e' stato controllato davvero
    restano = []
    if tmp and tmp.exists():
        restano.append(f"cartella locale {tmp}")
    restano += [str(c) for c in (TEMP_CONFIG, TEMP_DIR_ADDON) if c.exists()]
    if STATO["backup_richiesto"]:
        try:
            rimasti = backup_temporanei()
        except Exception as e:  # noqa: BLE001
            rimasti = None
            print("elenco dei backup non letto:", type(e).__name__)
        if rimasti is None:
            restano.append(f"elenco dei backup non letto: controllare a mano che non resti nessun backup '{NOME_BACKUP}'")
        elif rimasti:
            restano.append("backup temporanei " + ", ".join(b.get("slug", "?") for b in rimasti))
    installato = addon_temporaneo_installato()
    if installato is None:
        restano.append("stato dell'add-on temporaneo non letto: controllare a mano che non sia installato")
    elif installato:
        restano.append("add-on temporaneo ancora installato")
    print("pulizia:", "completa, nessuna copia della chiave rimasta" if not restano else "RESTA QUALCOSA, togliere a mano: " + "; ".join(restano))
    return restano


def main():
    pubblica = controlli_iniziali()
    esito, restano = False, []
    try:
        STATO["tmp"] = Path(tempfile.mkdtemp(prefix="tf", dir=os.environ.get("LOCALAPPDATA") or None))
        file_backup = backup_parziale()
        gnupg, store = estrai(file_backup)
        chiave = chiave_privata(gnupg, store, pubblica)
        addon_temporaneo(chiave)
        del chiave
        esito = invia()
    finally:
        restano = pulizia()
    print("ESITO:", "configurazione accettata da Tesla" if esito else "configurazione NON accettata: vedi sopra")
    sys.exit(2 if restano else 0 if esito else 1)


if __name__ == "__main__":
    main()
