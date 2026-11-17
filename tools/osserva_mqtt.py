"""Ascolta per qualche minuto i messaggi della telemetria su MQTT (attraverso il WebSocket di HA, comando mqtt/subscribe)
e stampa per ogni campo il primo valore e quanti messaggi sono arrivati. VIN mascherato; posizioni mai stampate.

Uso:  python osserva_mqtt.py [minuti]      (predefinito 8)
Serve a vedere i formati veri dei messaggi.
"""
import json
import sys
import time

import websocket

import comune

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
CFG = comune.carica()
BASE = CFG.get("topic_base") or "telemetria_tesla"
NASCOSTI = ("Location", "DestinationLocation", "OriginLocation", "RouteLine", "DestinationName")


def main(minuti):
    c = websocket.create_connection(comune.ws_ha(CFG), timeout=30)
    c.recv()
    c.send(json.dumps({"type": "auth", "access_token": comune.token_ha(CFG)}))
    c.recv()
    c.send(json.dumps({"id": 1, "type": "mqtt/subscribe", "topic": BASE + "/#"}))
    fine = time.time() + minuti * 60
    visti, conta = {}, {}
    print(f"in ascolto per {minuti} minuti su {BASE}/# ...", flush=True)
    while time.time() < fine:
        c.settimeout(max(1, fine - time.time()))
        try:
            grezzo = c.recv()
        except websocket.WebSocketTimeoutException:
            break
        except websocket.WebSocketConnectionClosedException:
            grezzo = ""
        if not grezzo:
            # HA puo' chiudere il collegamento (recv vuoto): si chiude l'ascolto e si stampa il riepilogo
            print(f"{time.strftime('%H:%M:%S')} collegamento chiuso da HA: ascolto finito prima del tempo", flush=True)
            break
        r = json.loads(grezzo)
        if r.get("type") == "result":
            if not r.get("success"):
                raise SystemExit(f"iscrizione rifiutata: {r.get('error')}")
            continue
        ev = r.get("event") or {}
        topic = comune.maschera_vin(ev.get("topic", ""))
        chiave = topic.split("/", 2)[-1]
        conta[chiave] = conta.get(chiave, 0) + 1
        if chiave in visti:
            continue
        carico = ev.get("payload", "")
        if any(chiave.endswith("/" + n) for n in NASCOSTI):
            try:
                d = json.loads(carico)
                carico = f"[luogo non stampato: {('chiavi ' + str(sorted(d))) if isinstance(d, dict) else type(d).__name__}]"
            except ValueError:
                carico = "[luogo non stampato]"
        carico = comune.maschera_vin(carico)
        visti[chiave] = carico
        stato = "conservato" if ev.get("retain") else "nuovo"
        print(f"{time.strftime('%H:%M:%S')} {stato:10} {chiave} = {carico[:160]}", flush=True)
    c.close()
    print(f"\nargomenti diversi: {len(visti)} | messaggi: {sum(conta.values())}")
    for k in sorted(conta):
        if conta[k] > 1:
            print(f"  {k}: {conta[k]} messaggi")


if __name__ == "__main__":
    main(float(sys.argv[1]) if len(sys.argv) > 1 else 8)
