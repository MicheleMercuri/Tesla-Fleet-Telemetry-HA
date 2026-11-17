# Telemetria Tesla (non ufficiale)

Server ufficiale [fleet-telemetry](https://github.com/teslamotors/fleet-telemetry) di Tesla (v0.10.0, immagine fissata
per digest) in un add-on di Home Assistant. Riceve dai veicoli lo stream in TLS mutuo sulla porta 4443/tcp e pubblica i
record su MQTT (Mosquitto, credenziali date dal Supervisor).

Add-on non ufficiale: non e' affiliato, approvato o collegato a Tesla, Inc. "Tesla" e' un marchio di Tesla, Inc., usato
solo per indicare la compatibilita'.

## Argomenti MQTT

- `telemetria_tesla/<VIN>/v/<Campo>`: un messaggio conservato per campo. JSON: numeri, booleani, testi con il prefisso
  dell'enum, oggetti per porte e posizione, `null` quando il dato non c'e'.
- `telemetria_tesla/<VIN>/alerts/<nome>/current` e `/history`: avvisi dello schermo.
- `telemetria_tesla/<VIN>/errors/<nome>`: errori riportati dall'auto, per esempio `unsupported_field`.
- `telemetria_tesla/<VIN>/connectivity`: collegamento e scollegamento (`Status`, `CreatedAt`).

## Prima dell'avvio

1. `server.crt` (certificato del server e della CA) e `server.key` in `addon_configs/<slug dell'add-on>/`, dalla
   cartella condivisa `addon_configs` di Samba. Si creano con `tools/crea_certificati.py crea`. La cartella la crea il
   Supervisor al primo avvio dell'add-on, che senza certificati si ferma subito.
2. Router: inoltro TCP della porta 4443 verso la macchina di Home Assistant. Nessun reverse proxy davanti: l'auto si
   autentica con il suo certificato e la connessione TLS deve arrivare fino al server.
3. Mosquitto avviato.
4. Watchdog acceso: il Supervisor riavvia il server se si ferma.

## Opzioni

- `log_level`: trace, debug, info, warn, error.
- `nascondi_errori_handshake`: nasconde dal log gli handshake TLS non riusciti, cioe' il controllo del watchdog ogni due
  minuti ("EOF") e gli scanner di internet. Da accendere quando l'auto si collega correttamente: nasconde anche un
  eventuale errore TLS dell'auto.

## Sicurezza

- Il server accetta solo client con un certificato emesso da Tesla per un'auto.
- Lo script di avvio toglie il token del Supervisor dall'ambiente del server e non gli da' nessun token di Home
  Assistant. Il server gira pero' come root dentro il contenitore.
- Nel log non finiscono i record (contengono il VIN): vanno solo su MQTT.
