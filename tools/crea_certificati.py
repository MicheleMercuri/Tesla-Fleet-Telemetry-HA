"""Certificati del server Fleet Telemetry.

Una CA propria (20 anni) e il certificato del server per telemetry_hostname (5 anni) firmato da lei. All'auto si manda
solo il certificato della CA (campo "ca" della configurazione): finche' la CA resta la stessa, rinnovare il certificato
del server NON richiede di riconfigurare l'auto. Niente Let's Encrypt: cambia ogni tre mesi e il server non lo
ricaricherebbe da solo; l'auto si fida della CA che le mandiamo noi.

Uso:  python crea_certificati.py crea      prima volta: CA e server (si ferma se la CA esiste gia')
      python crea_certificati.py rinnova   solo il certificato del server, con la CA esistente
      python crea_certificati.py mostra    date e impronte, niente chiavi

Tutto in private_dir (config.json, predefinito ~/.telemetria_tesla), mai nel repo. Dopo crea o rinnova:
copiare server.crt e server.key nella cartella addon_configs dell'add-on (vedi README) e riavviarlo.
"""
import datetime as dt
import os
import sys

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

import comune

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
CFG = comune.carica()
HOST = CFG["telemetry_hostname"]
PRIVATI = comune.percorso(CFG.get("private_dir") or "~/.telemetria_tesla")
ANNI_CA, GIORNI_SERVER = 20, 1826


def scrivi(p, dati, privato=False):
    """Scrittura atomica; le chiavi su Linux e macOS nascono gia' leggibili solo dall'utente che le crea (0600, cartella 0700)."""
    p.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = p.with_name(p.name + ".parziale")
    if os.path.exists(tmp):
        os.remove(tmp)
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600 if privato else 0o644)
    with os.fdopen(fd, "wb") as f:
        f.write(dati)
    os.replace(tmp, p)
    print(("chiave privata in " if privato else "scritto ") + str(p))


def pem_chiave(k):
    return k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())


def pem_cert(c):
    return c.public_bytes(serialization.Encoding.PEM)


def crea_ca():
    if (PRIVATI / "ca.key").exists():
        raise SystemExit("la CA esiste gia': per un nuovo certificato del server usare 'rinnova'")
    k = ec.generate_private_key(ec.SECP256R1())
    nome = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Telemetria Tesla CA")])
    ora = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=10)
    c = (x509.CertificateBuilder().subject_name(nome).issuer_name(nome).public_key(k.public_key())
         .serial_number(x509.random_serial_number()).not_valid_before(ora)
         .not_valid_after(ora + dt.timedelta(days=365 * ANNI_CA))
         .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
         .add_extension(x509.KeyUsage(digital_signature=True, key_cert_sign=True, crl_sign=True, content_commitment=False,
                                      key_encipherment=False, data_encipherment=False, key_agreement=False,
                                      encipher_only=False, decipher_only=False), critical=True)
         .add_extension(x509.SubjectKeyIdentifier.from_public_key(k.public_key()), critical=False)
         .sign(k, hashes.SHA256()))
    scrivi(PRIVATI / "ca.key", pem_chiave(k), privato=True)
    scrivi(PRIVATI / "ca.crt", pem_cert(c))
    return k, c


def carica_ca():
    k = serialization.load_pem_private_key((PRIVATI / "ca.key").read_bytes(), password=None)
    c = x509.load_pem_x509_certificate((PRIVATI / "ca.crt").read_bytes())
    return k, c


def crea_server(ca_k, ca_c):
    k = ec.generate_private_key(ec.SECP256R1())
    ora = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=10)
    c = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, HOST)]))
         .issuer_name(ca_c.subject).public_key(k.public_key()).serial_number(x509.random_serial_number())
         .not_valid_before(ora).not_valid_after(ora + dt.timedelta(days=GIORNI_SERVER))
         .add_extension(x509.SubjectAlternativeName([x509.DNSName(HOST)]), critical=False)
         .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
         .add_extension(x509.KeyUsage(digital_signature=True, key_cert_sign=False, crl_sign=False, content_commitment=False,
                                      key_encipherment=False, data_encipherment=False, key_agreement=False,
                                      encipher_only=False, decipher_only=False), critical=True)
         .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
         .add_extension(x509.SubjectKeyIdentifier.from_public_key(k.public_key()), critical=False)
         .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_k.public_key()), critical=False)
         .sign(ca_k, hashes.SHA256()))
    # il server manda il suo certificato e quello della CA (catena completa)
    scrivi(PRIVATI / "server.key", pem_chiave(k), privato=True)
    scrivi(PRIVATI / "server.crt", pem_cert(c) + pem_cert(ca_c))
    return c


def verifica(ca_c, srv_c):
    srv_c.verify_directly_issued_by(ca_c)  # firma, emittente: solleva se non torna
    k = serialization.load_pem_private_key((PRIVATI / "server.key").read_bytes(), password=None)
    assert k.public_key().public_numbers() == srv_c.public_key().public_numbers(), "chiave e certificato non vanno insieme"
    print("verifica: certificato del server firmato dalla CA, chiave corrispondente")


def mostra():
    for nome in ("ca.crt", "server.crt"):
        p = PRIVATI / nome
        if not p.exists():
            print(nome, "assente")
            continue
        c = x509.load_pem_x509_certificates(p.read_bytes())[0]
        soggetto = c.subject.rfc4514_string().replace(HOST, comune.maschera_host(HOST))
        print(f"{nome}: {soggetto} | dal {c.not_valid_before_utc.date()} al {c.not_valid_after_utc.date()} "
              f"| impronta sha256 {c.fingerprint(hashes.SHA256()).hex()[:16]}")


DOPO = ("ora: copiare server.crt e server.key in addon_configs/<slug dell'add-on>/ (Samba) e riavviare l'add-on; "
        "{}")

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "mostra"
    if cmd in ("crea", "rinnova") and HOST.endswith(("example.com", "example.org", "example.net")):
        raise SystemExit("telemetry_hostname in config.json e' ancora quello d'esempio: scrivere il proprio nome DNS")
    if cmd == "crea":
        ca_k, ca_c = crea_ca()
        verifica(ca_c, crea_server(ca_k, ca_c))
        mostra()
        print(DOPO.format("poi configura_auto.py anteprima e invia"))
    elif cmd == "rinnova":
        ca_k, ca_c = carica_ca()
        verifica(ca_c, crea_server(ca_k, ca_c))
        mostra()
        print(DOPO.format("l'auto non va riconfigurata (la CA e' la stessa)"))
    elif cmd == "mostra":
        mostra()
    else:
        raise SystemExit(__doc__)
