#!/usr/bin/with-contenv bashio
# Telemetria Tesla: scrive la configurazione del server con le credenziali MQTT del Supervisor e avvia il server.
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

umask 077
jq -n --arg broker "${BROKER}" --arg utente "${UTENTE}" --arg parola "${PAROLA}" --arg livello "${LIVELLO}" \
      --arg cert "${CERT}" --arg chiave "${KEY}" '{
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
  tls: {server_cert: $cert, server_key: $chiave}
}' > /tmp/config.json

if bashio::config.true 'nascondi_errori_handshake'; then
    export SUPPRESS_TLS_HANDSHAKE_ERROR_LOGGING=true
fi

bashio::log.info "Telemetria Tesla: server sulla porta 4443, MQTT ${BROKER}, argomenti telemetria_tesla/..."
# il server e' esposto su internet: non gli serve il token del Supervisor
unset SUPERVISOR_TOKEN HASSIO_TOKEN
exec /usr/bin/fleet-telemetry -config=/tmp/config.json
