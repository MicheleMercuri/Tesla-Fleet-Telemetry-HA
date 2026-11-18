#!/usr/bin/with-contenv bashio
# Telemetria Tesla: configurazione del server con le credenziali MQTT del Supervisor, scopritore (entita' in HA e avvisi)
# in sottofondo, server senza privilegi.
# La configurazione con la password resta solo dentro il contenitore (/tmp), mai in /config.
# Tutti i record (dati, avvisi, errori, collegamento) vanno solo su MQTT: niente dispatcher "logger", che stamperebbe
# i messaggi interi con il VIN nel log dell'add-on.

CERT=/config/server.crt
KEY=/config/server.key

if ! bashio::services.available "mqtt"; then
    bashio::exit.nok "MQTT non disponibile: serve l'add-on Mosquitto avviato"
fi
if [ ! -s "${CERT}" ] || [ ! -s "${KEY}" ]; then
    bashio::exit.nok "manca server.crt o server.key nella cartella addon_configs di questo add-on"
fi

BROKER="$(bashio::services mqtt "host"):$(bashio::services mqtt "port")"
UTENTE="$(bashio::services mqtt "username")"
PAROLA="$(bashio::services mqtt "password")"
LIVELLO="$(bashio::config 'log_level')"

# Scopritore: entita' in HA (MQTT discovery), valori derivati e avvisi. Processo a parte, riavviato se si ferma. Solo
# lui ha il token di Home Assistant (notifiche, registro, zona di casa).
if bashio::config.true 'discovery' || bashio::config.true 'togli'; then
    (
        # bashio attiva errexit: senza questo, uno scopritore uscito con un errore chiuderebbe il ciclo invece di ripartire
        set +e
        export MQTT_HOST="$(bashio::services mqtt "host")" MQTT_PORT="$(bashio::services mqtt "port")"
        export MQTT_USER="${UTENTE}" MQTT_PASS="${PAROLA}"
        export TOPIC_BASE="telemetria_tesla" DISCOVERY_PREFIX="homeassistant" FILE_STATO="/data/stato_scopritore.json"
        export PREFISSO="$(bashio::config 'prefisso')" NOME="$(bashio::config 'nome')"
        export ORE_SENZA_DATI="$(bashio::config 'ore_senza_dati')"
        for coppia in id_dispositivo:ID_DISPOSITIVO modello:MODELLO vin:VIN notifica:NOTIFICA notifica_dati:NOTIFICA_DATI \
                      scadenza_configurazione:SCADENZA_CONFIGURAZIONE contachilometri_confronto:CONTACHILOMETRI_CONFRONTO \
                      entita_pausa_carica:ENTITA_PAUSA_CARICA contatore_wallbox:CONTATORE_WALLBOX; do
            if bashio::config.has_value "${coppia%%:*}"; then
                export "${coppia##*:}=$(bashio::config "${coppia%%:*}")"
            fi
        done
        for coppia in avvisi_sentinella:AVVISI_SENTINELLA avviso_chiave_nuova:AVVISO_CHIAVE_NUOVA avvisi_schermo:AVVISI_SCHERMO \
                      misura_capacita:MISURA_CAPACITA conteggio_parcheggio:CONTEGGIO_PARCHEGGIO registro:REGISTRO; do
            if bashio::config.true "${coppia%%:*}"; then export "${coppia##*:}=true"; else export "${coppia##*:}=false"; fi
        done
        if bashio::config.true 'togli'; then
            if bashio::config.has_value 'conferma_togli'; then export CONFERMA_TOGLI="$(bashio::config 'conferma_togli')"; fi
            TOGLI=1 python3 /scopritore/scopritore.py
        else
            while true; do
                python3 /scopritore/scopritore.py
                codice=$?
                if [ "${codice}" -eq 78 ]; then
                    echo "scopritore fermo per opzioni non valide (vedi sopra): correggerle e riavviare l'add-on"
                    break
                fi
                echo "scopritore fermo (codice ${codice}): riparte fra 10 s"
                sleep 10
            done
        fi
    ) &
fi

# Server senza privilegi: certificato, chiave e configurazione copiati in /tmp, leggibili solo dall'utente nobody
umask 077
install -d -m 0700 -o nobody -g nogroup /tmp/ft
install -m 0400 -o nobody -g nogroup "${CERT}" /tmp/ft/server.crt
install -m 0400 -o nobody -g nogroup "${KEY}" /tmp/ft/server.key
jq -n --arg broker "${BROKER}" --arg utente "${UTENTE}" --arg parola "${PAROLA}" --arg livello "${LIVELLO}" '{
  host: "0.0.0.0",
  port: 4443,
  log_level: $livello,
  json_log_enable: false,
  namespace: "tesla_telemetry",
  rate_limit: {enabled: true, message_interval_time: 30, message_limit: 1000},
  records: {
    V: ["mqtt"],
    alerts: ["mqtt"],
    errors: ["mqtt"],
    connectivity: ["mqtt"]
  },
  mqtt: {
    broker: $broker,
    client_id: "telemetria-tesla-casa",
    username: $utente,
    password: $parola,
    topic_base: "telemetria_tesla",
    qos: 1,
    retained: true
  },
  tls: {server_cert: "/tmp/ft/server.crt", server_key: "/tmp/ft/server.key"}
}' > /tmp/ft/config.json
chown nobody:nogroup /tmp/ft/config.json
chmod 0400 /tmp/ft/config.json

if bashio::config.true 'nascondi_errori_handshake'; then
    export SUPPRESS_TLS_HANDSHAKE_ERROR_LOGGING=true
fi

bashio::log.info "Telemetria Tesla: server sulla porta 4443, MQTT ${BROKER}, argomenti telemetria_tesla/..."
# il server e' esposto su internet: niente token del Supervisor e utente nobody. Il suo log passa da un filtro che toglie
# le chiusure normali della connessione (il server le scrive come errore) e accorcia il VIN alle ultime 4 cifre.
unset SUPERVISOR_TOKEN HASSIO_TOKEN
# l'ambiente del contenitore che s6 conserva su file (con il token del Supervisor) resta leggibile solo da root; prova
# fatta da qui: se il server, come nobody, riuscisse lo stesso a leggere il token, il log lo dice
chmod 0700 /run/s6/container_environment 2>/dev/null || true
if setpriv --reuid=nobody --regid=nogroup --clear-groups cat /run/s6/container_environment/SUPERVISOR_TOKEN >/dev/null 2>&1; then
    bashio::log.warning "il token del Supervisor e' leggibile dall'utente del server: segnalarlo"
fi
exec setpriv --reuid=nobody --regid=nogroup --clear-groups --no-new-privs /usr/bin/fleet-telemetry -config=/tmp/ft/config.json \
    > >(exec sed -u -E '/closeNotify|websocket: close sent/d; s/\b[A-HJ-NPR-Z0-9]{13}([0-9]{4})\b/...\1/g') 2>&1
