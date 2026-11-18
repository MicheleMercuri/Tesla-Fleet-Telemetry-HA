# Fleet Telemetry di Tesla (server ufficiale, Apache 2.0) a versione fissa, anche per impronta.
# L'immagine di Tesla e' distroless (niente shell): se ne prende solo il programma e lo si mette sulla base Debian
# bookworm di Home Assistant, che ha bashio e jq per leggere dal Supervisor le credenziali MQTT. Il programma e'
# compilato da Tesla su Debian bookworm (glibc della stessa famiglia della base): se nel log compare "GLIBC_" o
# "cannot open shared object", la base va cambiata.
# Per aggiornare: cambiare tag e impronta qui, alzare "version" in config.yaml e ricostruire l'add-on.
FROM tesla/fleet-telemetry:v0.10.0@sha256:5b84420d8c18fe1379f79a4ca7aef59a9096c9ff9f18a0a9519b4bdb6989cdc1 AS ft

FROM ghcr.io/home-assistant/amd64-base-debian:bookworm@sha256:2ac5052621ce75d8af64e07a1c7fe2315c763b21fcd9bbe6843455b3a09ea001
# scopritore (entita' in HA e avvisi): Python con paho-mqtt 1.6 e PyYAML dai pacchetti di Debian bookworm
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-paho-mqtt python3-yaml \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ft /fleet-telemetry /usr/bin/fleet-telemetry
COPY scopritore /scopritore
COPY run.sh /run.sh
RUN chmod 755 /run.sh /usr/bin/fleet-telemetry
CMD ["/run.sh"]
