# Telemetria Tesla

Server ufficiale [fleet-telemetry](https://github.com/teslamotors/fleet-telemetry) di Tesla (v0.10.0, immagine fissata
per digest) in un add-on di Home Assistant. Riceve dai veicoli lo stream in TLS mutuo sulla porta 4443/tcp, pubblica i
record su MQTT (Mosquitto, credenziali date dal Supervisor) e crea da solo le entita' in Home Assistant (MQTT discovery),
con avvisi e notifiche. Nessun file YAML e nessuna app AppDaemon da installare.

**Beta.** Versione di prova che precede la 1.0: usarla a proprio rischio, senza garanzie (licenza MIT). I problemi si
segnalano sul repository GitHub.

Add-on non ufficiale: non e' affiliato, approvato o collegato a Tesla, Inc. "Tesla" e' un marchio di Tesla, Inc., usato
solo per indicare la compatibilita'.

## Prima dell'avvio

1. `server.crt` (certificato del server e della CA) e `server.key` in `addon_configs/<slug dell'add-on>/`, dalla
   cartella condivisa `addon_configs` di Samba. Si creano con `tools/crea_certificati.py crea`. La cartella la crea il
   Supervisor al primo avvio dell'add-on, che senza certificati si ferma subito.
2. Router: inoltro TCP della porta 4443 verso la macchina di Home Assistant. Nessun reverse proxy davanti: l'auto si
   autentica con il suo certificato e la connessione TLS deve arrivare fino al server.
3. Mosquitto avviato (con l'integrazione MQTT di Home Assistant).
4. Watchdog acceso: il Supervisor riavvia il server se si ferma.
5. La configurazione della telemetria mandata all'auto (`tools/configura_auto.py`, vedi il README).

## Entita'

Con `discovery` accesa (predefinito) le entita' compaiono da sole nel dispositivo `nome` al primo messaggio dell'auto,
con gli entity_id `<dominio>.<prefisso><nome>` (per esempio `sensor.tesla_tele_batteria`). Oltre ai campi della
telemetria: porte e finestrini aperti, navigazione verso casa, distanza della destinazione da casa (in km, dalla zona
home di HA), ultimo allarme e ultima allerta della Sentinella, ultimo avviso dello schermo, batteria 12V, ultimo errore
della telemetria, potenza della batteria, squilibrio delle celle (ad auto ferma) e, se accesi, Sentinella dal parcheggio e capacita' misurata alla ricarica. Se lo scopritore si ferma,
i valori derivati risultano non disponibili.

## Opzioni

| Opzione | Significato |
|---|---|
| `log_level` | trace, debug, info, warn, error (log del server) |
| `nascondi_errori_handshake` | nasconde dal log gli handshake TLS non riusciti: il controllo del watchdog ogni due minuti ("EOF") e gli scanner di internet. Da accendere quando l'auto si collega correttamente: nasconde anche un errore TLS dell'auto |
| `discovery` | scopritore acceso (predefinito): entita' in HA, valori derivati, notifiche e registro. Spento, l'add-on fa solo da server della telemetria (i record vanno su MQTT); le entita' gia' create restano finche' non si usa `togli` |
| `prefisso` | inizio degli entity_id (predefinito `tesla_tele_`); la plancia del repository usa quello predefinito. Per cambiarlo, prima togliere le entita' col vecchio prefisso (`togli`), altrimenti restano anche quelle |
| `nome` | nome del dispositivo e inizio dei nomi delle entita' |
| `id_dispositivo`, `modello` | facoltativi: identificativo e modello del dispositivo in HA |
| `vin` | facoltativo: il VIN dell'auto da seguire; vuoto, si prende dal primo messaggio. Una sola auto per add-on |
| `notifica` | servizio di HA per le notifiche, per esempio `notify.mobile_app_telefono`, `telegram_bot.send_message` o `persistent_notification.create`. Vuoto: nessuna notifica. Con Telegram ogni messaggio va in testo semplice e "muta" vuol dire senza suono (`disable_notification`); con gli altri servizi le notifiche mute arrivano come le altre. Se HA non risponde o non ha ancora caricato il servizio, si riprova dopo 20, 60 e 120 s |
| `notifica_dati` | facoltativo, JSON aggiunto a ogni notifica, per esempio `{"target": 123456789}` per una chat Telegram precisa. Se il servizio rifiuta la notifica, il log dell'add-on dice il servizio, il codice HTTP e il messaggio di HA quando c'e' (spesso solo "400: Bad Request") |
| `avvisi_sentinella` | allarme della Sentinella (con suono) e allerta fuori casa (muta, al massimo una ogni 15 minuti) |
| `avviso_chiave_nuova` | notifica quando aumentano le chiavi abbinate (telefoni e chiavi), confrontate con l'ultimo valore salvato dall'add-on: una chiave aggiunta ad add-on fermo si notifica alla ripartenza |
| `avvisi_schermo` | avvisi dello schermo dell'auto tradotti, con suono, muti o solo nel registro secondo il codice; in D o R senza suono; avvisi di ricarica solo se ancora attivi dopo 2 minuti; lo stesso testo al massimo ogni 6 ore |
| `scadenza_configurazione` | vuota: si legge `input_datetime.telemetria_tesla_scadenza`, scritta da `configura_auto.py`; oppure una data `AAAA-MM-GG` o un'altra entita'. Promemoria alle 9, 30, 7 e 1 giorno prima e il giorno stesso |
| `contachilometri_confronto` | facoltativo: il contachilometri di un'altra integrazione (per esempio Tesla Fleet). Alle 9, se e' avanti di oltre 20 km e l'auto non manda dati da 2 ore, notifica "telemetria ferma" |
| `ore_senza_dati` | facoltativo (0 = spento): controllato alle 9, notifica muta se l'auto non manda dati da piu' di tante ore |
| `entita_pausa_carica` | facoltativo, entita' separate da virgole: accese ora o negli ultimi 3 minuti vogliono dire "carica fermata apposta" (per esempio un rele' che stacca la wallbox), e la "corrente mancata" va solo nel registro |
| `misura_capacita` | capacita' della batteria misurata a ogni ricarica di almeno 20 punti (kWh entrati / punti * 100, meno l'energia usata a cavo attaccato). Serve il firmware dell'auto 2026.32 o successivo (contatore dell'energia caricata): prima ogni misura risulta "dati non validi" |
| `contatore_wallbox` | facoltativo: contatore di energia della wallbox (kWh, Wh o MWh), per le perdite di ricarica a casa |
| `registro` | avvisi dello schermo, rientri ed errori della telemetria nel registro (logbook) di HA (predefinito acceso). Da spegnere solo se un'altra parte di HA li scrive gia' |
| `conteggio_parcheggio` | allarmi e allerte della Sentinella dall'ultimo parcheggio (marcia in P) |
| `togli`, `conferma_togli` | toglie da HA tutte le entita' del prefisso (anche dal registro delle entita'), solo se `conferma_togli` e' uguale al prefisso (con o senza il `_` finale); poi spegnere `togli` per ricrearle. Da fare anche prima di disinstallare l'add-on: le configurazioni restano in Mosquitto e le entita' in HA |

Gli eventi della Sentinella contano solo i cambi visti dal vivo: un valore conservato che MQTT riconsegna all'avvio o al
ricollegamento non e' un allarme. Chiavi, marcia e stato della carica si confrontano invece con l'ultimo valore salvato
dall'add-on.

## Argomenti MQTT

- `telemetria_tesla/<VIN>/v/<Campo>`: un messaggio conservato per campo, dal server.
- `telemetria_tesla/<VIN>/alerts/<nome>/current` e `/history`, `telemetria_tesla/<VIN>/errors/<nome>`,
  `telemetria_tesla/<VIN>/connectivity`: avvisi, errori e collegamento, dal server.
- `telemetria_tesla/<VIN>/derivati/<prefisso senza _>/<nome>`: valori derivati, dallo scopritore.
- `telemetria_tesla/scopritore/<prefisso senza _>/stato`: `online` o `offline`, disponibilita' dello scopritore.
- `homeassistant/<dominio>/<prefisso senza _>/<nome>/config`: configurazioni della scoperta.

## Sicurezza

- Il server accetta solo client con un certificato emesso da Tesla per un'auto, gira come utente `nobody`, senza il
  token del Supervisor ne' di Home Assistant, e non scrive i record nel log (contengono il VIN): vanno solo su MQTT.
- Il log del server passa da un filtro che toglie le chiusure normali della connessione e accorcia il VIN alle ultime
  4 cifre; anche lo scopritore scrive solo le ultime 4 cifre.
- Il token di Home Assistant (`homeassistant_api`) lo riceve solo lo scopritore, per notifiche, registro, zona di casa e
  le entita' scelte nelle opzioni.
