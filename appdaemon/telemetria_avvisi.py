"""Telemetria Tesla, avvisi dell'auto tradotti (app AppDaemon).

L'auto manda via Fleet Telemetry gli avvisi che mostra sullo schermo (gruppo "customer"). L'automazione del package
"Telemetria Tesla - Avvisi ed errori dell'auto" li riceve da MQTT e, solo se recenti, manda l'evento
telemetria_tesla_avviso {tipo, nome, dati}. Questa app:
- traduce il codice con il dizionario telemetria_avvisi_model3.json (1.456 codici della Model 3 2017-2023; testi italiani,
  traduzione non ufficiale del file alert_dictionary.csv di Tesla);
- decide per ogni avviso: "suono", "muto" o "registro" (campo p del dizionario). Con telegram: true manda un Telegram
  (telegram_bot.send_message, chat predefinita dell'integrazione) con o senza suono; altrimenti solo registro di HA;
- con la marcia in D o R il suono diventa muto: l'avviso e' gia' sullo schermo e il telefono suonerebbe in guida;
- non manda due volte lo stesso testo entro 6 ore (alcuni avvisi arrivano in coppia con lo stesso testo);
- avvisi di ricarica: aspetta 2 minuti e avvisa solo se nel frattempo non sono rientrati (aggancio del cavo);
- "corrente mancata alla colonnina" (campo pm del dizionario) con una delle entita' di entita_pausa_carica accesa adesso
  o negli ultimi 3 minuti (per esempio un relè che stacca la wallbox apposta): solo registro;
- un avviso arrivato gia' finito (iniziato e rientrato tra due invii dell'auto) avvisa lo stesso, muto, "(gia' rientrato)";
- pubblica sensor.<prefisso>ultimo_avviso e sensor.<prefisso>batteria_12v e conserva lo stato in un file, cosi' dopo un
  riavvio di HA o di AppDaemon i due sensori tornano com'erano;
- la stessa consegna (nome, StartedAt, EndedAt) arrivata due volte si conta una volta: l'auto rimanda gli avvisi gia'
  chiusi insieme a quelli nuovi. Le consegne viste restano nel file di stato per un'ora.
Non comanda niente e non fa letture all'auto.
Limite noto: un avviso la cui fine si perde (fine arrivata oltre i 15 minuti del filtro dell'automazione, o durante un
riavvio) resta tra gli attivi; per la 12V il sensore resta acceso.

Argomenti (apps.yaml), tutti facoltativi:
  prefisso: tesla_tele_          lo stesso del package
  nome: Tesla Telemetria         inizio dei nomi dei due sensori
  telegram: false                true per i Telegram (serve l'integrazione telegram_bot)
  entita_pausa_carica: []        entita' che, accese, vogliono dire "carica fermata apposta"
  dizionario: <percorso>         predefinito telemetria_avvisi_model3.json accanto all'app
  file_stato: /config/telemetria_avvisi.json
"""
import json
import os
import re
from datetime import datetime, timedelta, timezone

import appdaemon.plugins.hass.hassapi as hass

EVENTO = "telemetria_tesla_avviso"
RIPETI_ORE = 6
ATTESA_RICARICA_S = 120
DOPPIONI_MIN = 60  # consegne uguali entro un'ora contano una volta


class TelemetriaAvvisi(hass.Hass):
    def initialize(self):
        prefisso = str(self.args.get("prefisso") or "tesla_tele_")
        prefisso = prefisso if prefisso.endswith("_") else prefisso + "_"
        if not re.fullmatch(r"[a-z0-9_]+", prefisso):
            raise ValueError("prefisso: solo lettere minuscole, cifre e _ (lo stesso del package)")
        self.s_ultimo = f"sensor.{prefisso}ultimo_avviso"
        self.s_12v = f"sensor.{prefisso}batteria_12v"
        self.marcia = f"sensor.{prefisso}marcia"
        self.nome = str(self.args.get("nome") or "Tesla Telemetria")
        self.con_telegram = bool(self.args.get("telegram", False))
        self.pausa = [str(e) for e in (self.args.get("entita_pausa_carica") or [])]
        cartella = os.path.dirname(__file__)
        with open(self.args.get("dizionario") or os.path.join(cartella, "telemetria_avvisi_model3.json"), encoding="utf-8") as f:
            self.diz = json.load(f)
        self.file_stato = self.args.get("file_stato", "/config/telemetria_avvisi.json")
        self.stato = self.carica()
        self.in_attesa = {}
        self.listen_event(self.avviso, EVENTO)
        self.pubblica_12v()
        if self.stato.get("ultimo"):
            self.scrivi_ultimo(self.stato["ultimo"])
        else:
            # il sensore esiste dall'avvio, anche prima del primo avviso
            self.set_state(self.s_ultimo, state="nessuno", replace=True, check_existence=False, attributes={
                "friendly_name": f"{self.nome} Ultimo avviso dell'auto", "icon": "mdi:car-info"})
        self.log(f"Avvisi Tesla pronti: {len(self.diz)} codici nel dizionario, {len(self.stato['attivi'])} attivi, "
                 f"Telegram {'acceso' if self.con_telegram else 'spento'}")

    # ---------------------------------------------------------------- stato su file
    def carica(self):
        try:
            with open(self.file_stato, encoding="utf-8") as f:
                s = json.load(f)
        except (OSError, ValueError):
            s = {}
        return {"attivi": s.get("attivi") or {}, "ultimo": s.get("ultimo") or {}, "inviati": s.get("inviati") or {},
                "visti": s.get("visti") or {}}

    def salva(self):
        tmp = self.file_stato + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.stato, f, ensure_ascii=False)
        os.replace(tmp, self.file_stato)

    # ---------------------------------------------------------------- avvisi
    def avviso(self, evento, dati, kwargs):
        if (dati or {}).get("tipo") != "avviso":
            return
        nome = str(dati.get("nome") or "")
        corpo = dati.get("dati") if isinstance(dati.get("dati"), dict) else {}
        # l'auto rimanda gli avvisi gia' consegnati insieme ai nuovi; la stessa consegna conta una volta
        adesso = datetime.now(timezone.utc)
        limite = (adesso - timedelta(minutes=DOPPIONI_MIN)).isoformat(timespec="microseconds")
        self.stato["visti"] = {k: t for k, t in self.stato["visti"].items() if str(t) > limite}
        consegna = f"{nome}|{corpo.get('StartedAt')}|{corpo.get('EndedAt')}"
        if consegna in self.stato["visti"]:
            return
        self.stato["visti"][consegna] = adesso.isoformat(timespec="microseconds")  # salvato da salva() piu' sotto
        voce = self.diz.get(nome) or {"g": "Altro", "m1": f"Avviso {nome}", "m2": "", "p": "registro"}
        riga = voce["m1"] + (f" - {voce['m2']}" if voce.get("m2") else "")
        if corpo.get("EndedAt"):
            self.finito(nome, voce, riga)
            return
        self.stato["attivi"][nome] = self.ora()
        politica, nota = voce["p"], ""
        if voce.get("pm") and self.carica_fermata_apposta():
            politica, nota = "registro", " (carica fermata apposta: nessun guasto)"
        if politica == "suono" and self.get_state(self.marcia) in ("D", "R"):
            politica, nota = "muto", nota + " (in marcia: senza suono)"
        ultimo = {"testo": voce["m1"][:250], "seconda_riga": voce.get("m2") or "-", "gruppo": voce["g"], "codice": nome,
                  "quando": self.ora(), "invio": politica + nota}
        self.stato["ultimo"] = ultimo
        self.scrivi_ultimo(ultimo)
        self.pubblica_12v()
        self.registro(f"{riga} ({voce['g']}, {nome}){nota}")
        self.salva()
        if politica == "registro":
            return
        if voce.get("ric"):
            # aggancio del cavo e simili: avvisa solo se fra 2 minuti l'avviso e' ancora attivo
            self.annulla_attesa(nome)
            self.in_attesa[nome] = self.run_in(self.dopo_attesa, ATTESA_RICARICA_S, nome=nome, politica=politica, riga=riga, gruppo=voce["g"])
            return
        self.telegram(voce, riga, politica)

    def dopo_attesa(self, kwargs):
        nome = kwargs["nome"]
        self.in_attesa.pop(nome, None)
        if nome in self.stato["attivi"]:
            self.telegram(self.diz.get(nome, {"m1": kwargs["riga"], "g": kwargs["gruppo"]}), kwargs["riga"], kwargs["politica"])

    def finito(self, nome, voce, riga):
        attivo = self.stato["attivi"].pop(nome, None)
        in_attesa = self.annulla_attesa(nome)
        self.registro(f"Rientrato: {riga} ({voce['g']}, {nome})")
        if attivo is None and not in_attesa and voce["p"] != "registro" and not (voce.get("pm") and self.carica_fermata_apposta()):
            # iniziato e rientrato tra due invii dell'auto: arriva solo la fine
            self.telegram(voce, riga + " (gia' rientrato)", "muto")
        self.pubblica_12v()
        self.salva()

    def annulla_attesa(self, nome):
        h = self.in_attesa.pop(nome, None)
        if h is not None:
            try:
                self.cancel_timer(h)
            except Exception:  # noqa: BLE001 timer gia' scaduto
                pass
            return True
        return False

    def telegram(self, voce, riga, politica):
        if not self.con_telegram:
            return
        chiave = voce.get("m1", riga)
        adesso = datetime.now(timezone.utc)
        # il valore e' "ora|politica": un invio con suono passa anche se prima c'era stato solo un invio muto
        prima, _, prima_politica = str(self.stato["inviati"].get(chiave) or "").partition("|")
        try:
            if (prima and adesso - datetime.fromisoformat(prima) < timedelta(hours=RIPETI_ORE)
                    and not (politica == "suono" and prima_politica == "muto")):
                self.log(f"gia' avvisato da meno di {RIPETI_ORE} ore: {chiave[:60]}")
                return
        except (TypeError, ValueError):
            pass
        self.stato["inviati"][chiave] = adesso.isoformat() + "|" + politica
        limite = (adesso - timedelta(days=2)).isoformat()
        self.stato["inviati"] = {k: v for k, v in self.stato["inviati"].items() if str(v).partition("|")[0] > limite}
        self.salva()
        self.call_service("telegram_bot/send_message", parse_mode="plain_text", disable_notification=(politica != "suono"),
                          message=f"Avviso della Tesla ({voce.get('g', 'Altro')}): {riga}.")

    # ---------------------------------------------------------------- sensori
    def scrivi_ultimo(self, u):
        self.set_state(self.s_ultimo, state=u["testo"], replace=True, check_existence=False, attributes={
            "friendly_name": f"{self.nome} Ultimo avviso dell'auto", "icon": "mdi:car-info",
            "seconda_riga": u["seconda_riga"], "gruppo": u["gruppo"], "codice": u["codice"], "quando": u["quando"], "invio": u["invio"]})

    def pubblica_12v(self):
        attivi = [c for c in self.stato["attivi"] if (self.diz.get(c) or {}).get("g") == "Batteria 12V" and self.diz[c]["p"] == "suono"]
        if attivi:
            v = self.diz[attivi[-1]]
            stato, icona = v["m1"][:250], "mdi:car-battery-alert"
            dettaglio = f"Dal {self.stato['attivi'][attivi[-1]]}: {v.get('m2') or v['m1']}" + (f" (e altri {len(attivi) - 1})" if len(attivi) > 1 else "")
        else:
            stato, icona = "Nessun avviso attivo", "mdi:car-battery"
            dettaglio = "La diagnosi dell'auto non segnala problemi alla batteria 12V"
        self.set_state(self.s_12v, state=stato, replace=True, check_existence=False, attributes={
            "friendly_name": f"{self.nome} Batteria 12V", "icon": icona, "dettaglio": dettaglio, "attivi": ", ".join(attivi) or "-"})

    # ---------------------------------------------------------------- servizio
    def carica_fermata_apposta(self):
        """Una delle entita' di entita_pausa_carica accesa adesso o negli ultimi 3 minuti."""
        for e in self.pausa:
            if self.get_state(e) == "on":
                return True
            cambiato = self.get_state(e, attribute="last_changed")
            try:
                if cambiato and datetime.now(timezone.utc) - datetime.fromisoformat(cambiato) < timedelta(minutes=3):
                    return True
            except (TypeError, ValueError):
                pass
        return False

    def registro(self, testo):
        # senza entity_id: AppDaemon lo passava come lista e HA rifiutava la chiamata
        self.call_service("logbook/log", name="Avvisi Tesla", message=testo[:500])
        self.log(testo[:200])

    def ora(self):
        return datetime.now().strftime("%d/%m %H:%M")
