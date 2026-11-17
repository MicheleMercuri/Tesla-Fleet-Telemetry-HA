# Proxy ufficiale di Tesla (vehicle-command 0.4.1, immagine distroless, ENTRYPOINT tesla-http-proxy), fissato per impronta.
# Firma la configurazione della telemetria con la chiave dell'applicazione (fleet-key.pem in addon_configs, solo per i
# minuti dell'invio). Nessun -verbose: il log non deve mostrare le richieste.
FROM tesla/vehicle-command:0.4.1@sha256:e8adac679bf5e72790c588e620bb4a7992a8f80d4c3bd98da6d1129f59daf482
CMD ["-tls-key", "/config/tls-key.pem", "-cert", "/config/tls-cert.pem", "-key-file", "/config/fleet-key.pem", "-host", "0.0.0.0", "-port", "4443"]
