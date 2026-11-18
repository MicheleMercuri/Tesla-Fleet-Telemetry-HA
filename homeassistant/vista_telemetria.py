"""Vista "Telemetria Tesla" per Home Assistant, sui sensori creati dall'add-on (scoperta MQTT).

Uso:  python vista_telemetria.py [--prefisso tesla_tele_] [--senza-mappa] [--senza-avvisi] [--senza-stile]
                                 [--titolo "Telemetria Tesla"] [--uscita plancia_telemetria.yaml]
Scrive una plancia (title + views) da incollare in una plancia nuova: Impostazioni, Plance, Aggiungi plancia, poi
Modifica, Editor di configurazione testuale. Il prefisso deve essere lo stesso dell'opzione prefisso dell'add-on.
Nel repository c'e' gia' plancia_telemetria.yaml, fatta con il prefisso predefinito tesla_tele_.

Schede richieste (HACS): mushroom, apexcharts-card; card-mod per lo stile (con --senza-stile non serve).
--senza-avvisi: senza le righe della batteria 12V e dell'ultimo avviso.
--senza-mappa: senza la mappa con la posizione dell'auto (per esempio per le foto da pubblicare).
Colori per chi non distingue rosso e verde: blu, azzurro, arancio, viola (Okabe-Ito), mai rosso contro verde.
"""
import argparse
import re
import sys
from pathlib import Path

import yaml

from stile import Stile

QUI = Path(__file__).resolve().parent
BLU, ARANCIO, AZZURRO, VIOLA = "#0072B2", "#E69F00", "#56B4E9", "#CC79A7"
INFO = {"tap_action": {"action": "more-info"}, "hold_action": {"action": "more-info"}}


def val(e, dec=None, unita=""):
    """Valore di un'entita' per il testo delle schede: numero arrotondato, oppure '-' se non noto."""
    if dec is None:
        return "{% set s = states('" + e + "') %}{{ s if s not in ['unknown', 'unavailable'] else '-' }}" + unita
    return ("{% set s = states('" + e + "') %}{{ (s | float | round(" + str(dec) + ")) | string | replace('.', ',') "
            "if is_number(s) else '-' }}" + unita)


def migliaia(e):
    """Numero intero con il punto delle migliaia (12.345), oppure '-'."""
    return "{% set s = states('" + e + "') %}{{ '{:,.0f}'.format(s | float) | replace(',', '.') if is_number(s) else '-' }}"


def sino(e, si="sì", no="no"):
    return "{% set s = states('" + e + "') %}{{ '" + si + "' if s == 'on' else '" + no + "' if s == 'off' else '-' }}"


def quando(e, mai="mai"):
    return "{% set t = as_timestamp(states('" + e + "'), 0) %}{{ t | timestamp_custom('%d/%m %H:%M') if t > 0 else '" + mai + "' }}"


def costruisci(px, mappa=True, avvisi=True, con_css=True, titolo="Telemetria Tesla", path="telemetria-tesla"):
    P, B = "sensor." + px, "binary_sensor." + px
    st = Stile(con_css)
    col_sentinella = ("{% set s = states('" + P + "sentinella') %}{{ 'pink' if s == 'Allarme' else 'amber' if s == 'Allerta' "
                      "else 'blue' if s in ['Inserita', 'A riposo', 'Silenziosa'] else 'grey' }}")
    ico_sentinella = ("{% set s = states('" + P + "sentinella') %}{{ 'mdi:shield-alert' if s == 'Allarme' else 'mdi:shield-search' "
                      "if s == 'Allerta' else 'mdi:shield-car' }}")

    def tessera(entita, primaria, secondaria, icona, colore_m, colore):
        return st.tessera({"type": "custom:mushroom-template-card", "entity": entita, "primary": primaria, "secondary": secondaria,
                           "icon": icona, "color": colore_m, "multiline_secondary": True, **INFO}, colore)

    def riga(entita, primaria, secondaria, icona, colore_m):
        return st.riga({"type": "custom:mushroom-template-card", "entity": entita, "primary": primaria, "secondary": secondaria,
                        "icon": icona, "color": colore_m, "multiline_secondary": True, **INFO})

    def grafico(span, serie, altezza=200, yaxis=None, extra=None, valori=False, barre=False, marcatori=False):
        """Curve morbide, aree sfumate, barre arrotondate, griglia leggera; con valori=True i valori attuali in grande
        nell'intestazione. La sfumatura solo per aree e colonne (sulle linee le renderebbe quasi trasparenti);
        marcatori=True disegna anche i punti isolati (per esempio un solo giorno di dati)."""
        sfumatura = ({"type": "gradient", "gradient": {"shade": "dark", "type": "vertical", "opacityFrom": 0.95, "opacityTo": 0.55,
                                                       "stops": [0, 100]}} if barre else
                     {"type": "gradient", "gradient": {"shadeIntensity": 1, "opacityFrom": 0.45, "opacityTo": 0.03, "stops": [0, 90, 100]}})
        apex = {"chart": {"height": altezza, "foreColor": "#c9cdd6", "fontFamily": "inherit", "toolbar": {"show": False},
                          "zoom": {"enabled": False}, "background": "transparent"},
                "grid": {"borderColor": "rgba(255,255,255,0.07)", "strokeDashArray": 0, "xaxis": {"lines": {"show": False}},
                         "padding": {"left": 4, "right": 8, "top": -8}},
                "legend": {"show": not valori, "position": "top", "horizontalAlign": "left", "fontSize": "12px",
                           "markers": {"width": 10, "height": 10, "radius": 10}, "itemMargin": {"horizontal": 8}},
                "dataLabels": {"enabled": False},
                "tooltip": {"theme": "dark"},
                "xaxis": {"axisBorder": {"show": False}, "axisTicks": {"show": False}},
                "plotOptions": {"bar": {"borderRadius": 5, "borderRadiusApplication": "end", "columnWidth": "60%"}}}
        tipi = [s.get("type", "line") for s in serie]
        if any(t in ("area", "column") for t in tipi):
            apex["fill"] = dict(sfumatura, type=["gradient" if t in ("area", "column") else "solid" for t in tipi])
        if marcatori:
            apex["markers"] = {"size": 4, "strokeWidth": 0}
        for s in serie:
            s.setdefault("show", {})["legend_value"] = False
        g = {"type": "custom:apexcharts-card", "graph_span": span, "apex_config": apex, "series": serie,
             "header": {"show": True, "show_states": True, "colorize_states": True} if valori else {"show": False}}
        if yaxis:
            g["yaxis"] = yaxis
        g.update(extra or {})
        return st.riga(g)

    giornaliero = {"span": {"end": "day"}}

    def per_giorno(entita, nome, colore, tipo="change"):
        return {"entity": entita, "name": nome, "type": "column", "color": colore,
                "statistics": {"type": tipo, "period": "day", "align": "middle"}}

    def linea(entita, nome, colore, yaxis_id=None, media=None, tipo="line"):
        s = {"entity": entita, "name": nome, "type": tipo, "color": colore, "stroke_width": 2.5, "curve": "smooth"}
        if yaxis_id:
            s["yaxis_id"] = yaxis_id
        if media:
            s["group_by"] = {"func": "avg", "duration": media}
        return s

    tessere = [
        tessera(P + "sentinella", val(P + "sentinella"), "Sentinella", ico_sentinella, col_sentinella, "blu"),
        tessera(P + "batteria", val(P + "batteria", 0, "%"), val(P + "stato_carica"), "mdi:battery-charging-60", "cyan", "ciano"),
        tessera(B + "serratura",
                "{% set s = states('" + B + "serratura') %}{{ 'Chiusa' if s == 'off' else 'Aperta' if s == 'on' else '-' }}",
                val(P + "porte_aperte"), "mdi:car-door-lock",
                "{{ 'amber' if is_state('" + B + "serratura', 'on') or states('" + P + "porte_aperte') not in ['Tutte chiuse', 'unknown', 'unavailable'] else 'teal' }}",
                "teal"),
        tessera(B + "collegata",
                "{% set s = states('" + B + "collegata') %}{{ 'Collegata' if s == 'on' else 'In attesa' if s == 'off' else 'Mai vista' }}",
                "Ultimo collegamento " + quando(P + "ultimo_collegamento", "-"), "mdi:access-point-network",
                "{{ 'purple' if is_state('" + B + "collegata', 'on') else 'grey' }}", "viola"),
    ]
    sicurezza = [
        st.titolo("SENTINELLA E SICUREZZA", "🛡️", "blu"),
        riga(P + "sentinella", "Sentinella: " + val(P + "sentinella"),
             "Ultimo allarme " + quando(P + "ultimo_allarme") + " · ultima allerta " + quando(P + "ultima_allerta"),
             ico_sentinella, col_sentinella),
        riga(B + "serratura",
             "{% set s = states('" + B + "serratura') %}{{ 'Bloccata' if s == 'off' else 'Sbloccata' if s == 'on' else 'Serratura: -' }}",
             "Chiavi abbinate " + val(P + "chiavi") + " · conducente seduto " + sino(B + "conducente"),
             "mdi:lock", "{{ 'amber' if is_state('" + B + "serratura', 'on') else 'blue' }}"),
        riga(P + "porte_aperte", "Porte: " + val(P + "porte_aperte"), "Finestrini: " + val(P + "finestrini_aperti"),
             "mdi:car-door", "{{ 'amber' if states('" + P + "porte_aperte') not in ['Tutte chiuse', 'unknown', 'unavailable'] else 'teal' }}"),
        riga(P + "sentinella_parcheggio",
             "{% set s = states('" + P + "sentinella_parcheggio') %}{% set n = s | int(0) %}"
             "{% set l = state_attr('" + P + "sentinella_parcheggio', 'allerte') | int(0) %}"
             "Dal parcheggio: {{ (n ~ (' allarme' if n == 1 else ' allarmi')) if is_number(s) else '-' }}, "
             "{{ l }} {{ 'allerta' if l == 1 else 'allerte' }}",
             "{% set t = state_attr('" + P + "sentinella_parcheggio', 'parcheggiata_dalle') %}"
             "{{ ('Parcheggiata alle ' ~ (as_timestamp(t) | timestamp_custom('%H:%M del %d/%m')) ~ ', ' ~ "
             "(state_attr('" + P + "sentinella_parcheggio', 'dove') or '')) if t else 'Il conteggio riparte a ogni parcheggio (marcia in P)' }}",
             "mdi:shield-car", "{{ 'orange' if states('" + P + "sentinella_parcheggio') | int(0) > 0 else 'blue' }}"),
        riga(B + "quattro_frecce", "Quattro frecce " + sino(B + "quattro_frecce", "accese", "spente"), "Schermo: " + val(P + "schermo"),
             "mdi:car-light-alert", "{{ 'orange' if is_state('" + B + "quattro_frecce', 'on') else 'grey' }}"),
    ]
    viaggio = [
        st.titolo("POSIZIONE E VIAGGIO", "🧭", "ciano"),
        riga("device_tracker." + px + "posizione",
             # prima il GPS (zona di HA), poi "A casa" dell'auto: il flag dell'auto puo' restare acceso con l'auto lontana
             "{% set t = 'device_tracker." + px + "posizione' %}{% set s = states(t) %}"
             "{{ ('A casa' if s == 'home' else 'Fuori casa' if s == 'not_home' else s) "
             "if has_value(t) and (state_attr(t, 'latitude') | float(0)) != 0 else "
             "'A casa' if is_state('" + B + "a_casa', 'on') else 'Fuori casa' if is_state('" + B + "a_casa', 'off') else 'Posizione: -' }}"
             # a vettura ferma senza conducente l'auto manda la marcia null: niente "marcia -"
             "{% set m = states('" + P + "marcia') %}{{ ' · marcia ' ~ m if m not in ['unknown', 'unavailable'] else '' }}",
             "Contachilometri " + val(P + "contachilometri", 0, " km"), "mdi:map-marker", "cyan"),
        # navigazione in corso quando ci sono i minuti all'arrivo (MinutesToArrival e' null senza percorso), anche con una
        # destinazione senza nome; solo allora km, % e traffico. Con l'auto scollegata l'ora del dato invece dell'arrivo.
        riga(P + "destinazione",
             "{% set d = states('" + P + "destinazione') %}{% set m = states('" + P + "minuti_arrivo') %}"
             "{% if d not in ['Nessuna', 'unknown', 'unavailable'] %}Destinazione: {{ d }}"
             "{% elif is_number(m) %}Navigazione in corso{% else %}Destinazione: Nessuna{% endif %}",
             "{% set d = states('" + P + "destinazione') %}{% set m = states('" + P + "minuti_arrivo') %}"
             "{% if not is_number(m) %}{{ 'Nessuna navigazione in corso' if d in ['Nessuna', 'unknown', 'unavailable'] else 'Percorso in calcolo' }}"
             "{% else %}{% set k = states('" + P + "km_arrivo') %}{% set b = states('" + P + "batteria_arrivo') %}"
             "{% set r = states('" + P + "ritardo_traffico') %}{% set c = states('" + P + "destinazione_distanza') %}"
             "{% set mi = m | float | round(0) | int %}"
             "{% set durata = (mi // 60) ~ ' h ' ~ ('%02d' % (mi % 60)) ~ ' min' if mi >= 60 else mi ~ ' min' %}"
             "{% set e = states." + P + "minuti_arrivo %}"
             "{% if is_state('" + B + "collegata', 'on') %}{% set t = as_timestamp(now()) + mi * 60 %}"
             "{% set q = 'arrivo alle ' ~ (t | timestamp_custom('%H:%M')) ~ (' di domani' if (t | timestamp_custom('%Y%m%d')) != now().strftime('%Y%m%d') else '') %}"
             "{% else %}{% set q = 'ultimo dato alle ' ~ (as_timestamp(e.last_changed) | timestamp_custom('%H:%M')) if e is not none else 'auto non collegata' %}{% endif %}"
             "{% set p = [durata ~ ', ' ~ q] %}"
             "{% if is_number(k) %}{% set p = p + ['mancano ' ~ ((k | float) | round(1) | string).replace('.', ',') ~ ' km'] %}{% endif %}"
             "{% set p = p + ['batteria all\\'arrivo ' ~ (((b | float) | round(0) | int) ~ '%' if is_number(b) else 'in calcolo')] %}"
             "{% if is_number(r) and r | float >= 1 %}{% set p = p + ['+' ~ (r | float | round(0) | int) ~ ' min di traffico'] %}{% endif %}"
             "{% if is_number(c) and c | float >= 0.3 %}{% set p = p + ['destinazione a ' ~ ((c | float) | round(1) | string).replace('.', ',') ~ ' km da casa'] %}{% endif %}"
             "{{ p | join(' · ') }}{% endif %}",
             "mdi:map-marker-path", "{{ 'blue' if is_state('" + B + "verso_casa', 'on') else 'cyan' }}"),
    ]
    if mappa:
        viaggio.append(st.riga({"type": "map", "entities": [{"entity": "device_tracker." + px + "posizione"}], "default_zoom": 15,
                                "hours_to_show": 0, "theme_mode": "dark", "aspect_ratio": "16:9"}))
    ricarica = [
        st.titolo("RICARICA", "⚡", "ambra"),
        riga(P + "stato_carica", val(P + "stato_carica") + " · AC " + val(P + "potenza_ac", 1, " kW") + " · DC " + val(P + "potenza_dc", 1, " kW"),
             "Batteria " + val(P + "batteria", 0, "%") + " (utilizzabile " + val(P + "batteria_utilizzabile", 1, "%") + ") · " +
             val(P + "energia_batteria", 1, " kWh") + " · limite " + val(P + "limite_carica", 0, "%"),
             "mdi:ev-station", "amber"),
        # fasi e inizio programmato l'auto li manda null quando non ci sono: si mostrano solo quando ci sono
        riga(P + "ampere", val(P + "ampere", 0, " A") + " su " + val(P + "ampere_richiesti", 0, " A") + " richiesti",
             "Massimo " + val(P + "ampere_massimi", 0, " A") +
             "{% set f = states('" + P + "fasi') %}{{ ' · fasi ' ~ f if is_number(f) else '' }}" + " · carica " +
             sino(B + "carica_abilitata", "abilitata", "ferma"), "mdi:current-ac", "amber"),
        # "Agganciato" vale anche a cavo staccato (e' il fermo dello sportello): il cavo si dice dallo stato della carica
        riga(B + "sportello", "Sportello " + sino(B + "sportello", "aperto", "chiuso") +
             "{% set s = states('" + P + "stato_carica') %}{% set l = states('" + P + "aggancio_cavo') %}"
             "{% if s == 'Scollegata' %} · cavo scollegato{% elif s not in ['unknown', 'unavailable'] %} · cavo "
             "{{ 'collegato' if l in ['Agganciato', 'unknown', 'unavailable'] else l | lower }}{% endif %}",
             "Ricarica programmata: " + val(P + "ricarica_programmata") +
             "{{ ', in attesa dell\\'orario' if is_state('" + B + "in_attesa_orario', 'on') else '' }}" +
             "{% set i = states('" + P + "inizio_programmato') %}{{ ' · inizio ' ~ i if i not in ['unknown', 'unavailable'] else '' }}",
             "mdi:ev-plug-type2", "{{ 'amber' if is_state('" + B + "in_attesa_orario', 'on') else 'blue' }}"),
    ]
    g_carica = [
        st.titolo("BATTERIA E CARICA, 24 ORE", "📈", "ambra"),
        grafico("24h", [linea(P + "batteria", "Batteria", BLU, "pct", tipo="area"),
                        linea(P + "potenza_ac", "Potenza AC", ARANCIO, "kw", tipo="area")],
                yaxis=[{"id": "pct", "min": 0, "max": 100, "decimals": 0},
                       {"id": "kw", "opposite": True, "min": 0, "decimals": 1}], valori=True),
    ]
    batteria = [
        st.titolo("BATTERIA", "🔋", "teal"),
        riga(P + "capacita_batteria", "Capacità " + val(P + "capacita_batteria", 1, " kWh"),
             "Carica al 100% completata " + sino(B + "carica_100") + " · riscaldamento " + sino(B + "riscaldamento_batteria", "acceso", "spento"),
             "mdi:battery-heart-variant", "teal"),
        riga(P + "capacita_misurata",
             "{% set s = states('" + P + "capacita_misurata') %}Capacità misurata alla ricarica: "
             "{{ (s ~ ' kWh') if is_number(s) else 'nessuna ancora' }}",
             "{% set e = state_attr('" + P + "capacita_misurata', 'ultima_esito') %}{% set d = state_attr('" + P + "capacita_misurata', 'ultima_data') %}"
             "{{ ('Ultima ricarica: ' ~ e ~ ((' il ' ~ (as_timestamp(d) | timestamp_custom('%d/%m'))) if d else '')) if e "
             "else 'Si misura a ogni ricarica di almeno 20 punti' }}",
             "mdi:battery-check-outline", "teal"),
        riga(P + "temp_batteria_max", "Moduli da " + val(P + "temp_batteria_min", 0) + " a " + val(P + "temp_batteria_max", 0, " °C"),
             "Il freddo rallenta la carica: sotto i 10 °C l'auto scalda la batteria", "mdi:thermometer", "teal"),
        riga(P + "energia_caricata",
             "Caricati " + migliaia(P + "energia_caricata") + " · usati " + migliaia(P + "energia_usata") + " kWh",
             "Di cui DC " + migliaia(P + "energia_caricata_dc") + " kWh" +
             # LifetimeEnergyGainedRegen in pratica l'auto non lo manda: la frenata compare solo se arriva
             "{% if is_number(states('" + P + "energia_recuperata')) %} · recuperati in frenata " + migliaia(P + "energia_recuperata") +
             " kWh{% endif %} · media di sempre " +
             "{% set u = states('" + P + "energia_usata') %}{% set k = states('" + P + "contachilometri') %}"
             "{{ ((u | float * 1000) / (k | float)) | round(0) | int if is_number(u) and is_number(k) and k | float > 0 else '-' }}"
             " Wh/km, soste e clima comprese", "mdi:counter", "teal"),
        st.titolo("CAPACITÀ DELLA BATTERIA, 1 ANNO", "📉", "teal"),
        grafico("365d", [per_giorno(P + "capacita_batteria", "Stima del BMS", BLU, "mean") | {"type": "line", "curve": "smooth", "stroke_width": 2.5}],
                altezza=190, extra=giornaliero, marcatori=True),
    ]
    clima = [
        st.titolo("CLIMA E SEDILI", "🌡️", "viola"),
        riga(P + "temp_interna", "Interna " + val(P + "temp_interna", 1, " °C") + " · esterna " + val(P + "temp_esterna", 1, " °C"),
             "Impostata " + val(P + "temp_impostata", 1, " °C") + " · clima " + val(P + "clima"), "mdi:thermometer", "purple"),
        riga(P + "modalita_clima", "Modalità clima: " + val(P + "modalita_clima"),
             "Protezione dal caldo: " + val(P + "protezione_caldo") + " · precondizionamento " +
             sino(B + "precondizionamento", "acceso", "spento"), "mdi:fan", "purple"),
        riga(P + "sedile_guida",
             "Guida " + val(P + "sedile_guida") + "{{ ' auto' if is_state('" + B + "sedile_guida_auto', 'on') else '' }} · passeggero " +
             val(P + "sedile_passeggero") + "{{ ' auto' if is_state('" + B + "sedile_passeggero_auto', 'on') else '' }}",
             "Posteriori: sinistro " + val(P + "sedile_post_sx") + " · centro " + val(P + "sedile_post_centro") + " · destro " + val(P + "sedile_post_dx"),
             "mdi:car-seat-heater", "purple"),
        st.titolo("TEMPERATURE, 24 ORE", "🌡️", "viola"),
        grafico("24h", [linea(P + "temp_interna", "Interna", ARANCIO, media="10min"),
                        linea(P + "temp_esterna", "Esterna", AZZURRO, media="10min"),
                        linea(P + "temp_batteria_max", "Batteria", VIOLA, media="10min")], valori=True),
    ]
    gomme = [
        st.titolo("GOMME", "🛞", "blu"),
        riga(P + "gomma_ant_sx", "Anteriori " + val(P + "gomma_ant_sx", 2) + " | " + val(P + "gomma_ant_dx", 2, " bar"),
             "Posteriori " + val(P + "gomma_post_sx", 2) + " | " + val(P + "gomma_post_dx", 2, " bar") +
             "{{ ' · AVVISO DELL\\'AUTO' if is_state('" + B + "avviso_gomme', 'on') or is_state('" + B + "allarme_gomme', 'on') else '' }}",
             "mdi:tire", "{{ 'orange' if is_state('" + B + "avviso_gomme', 'on') or is_state('" + B + "allarme_gomme', 'on') else 'blue' }}"),
        grafico("7d", [dict(linea(P + g, n, c, media="1h"), float_precision=2) for g, n, c in
                       (("gomma_ant_sx", "Ant. sx", BLU), ("gomma_ant_dx", "Ant. dx", AZZURRO),
                        ("gomma_post_sx", "Post. sx", ARANCIO), ("gomma_post_dx", "Post. dx", VIOLA))],
                yaxis=[{"decimals": 2}], altezza=190),
    ]
    software = [st.titolo("AVVISI E SOFTWARE" if avvisi else "SOFTWARE", "🔔", "blu")]
    if avvisi:
        v12, ua = P + "batteria_12v", P + "ultimo_avviso"
        software += [
            riga(v12, "{% set s = states('" + v12 + "') %}Batteria 12V: {{ s if s not in ['unknown', 'unavailable'] else 'nessun avviso' }}",
                 "{{ state_attr('" + v12 + "', 'dettaglio') or 'La diagnosi dell\\'auto non ha segnalato problemi' }}",
                 "mdi:car-battery",
                 "{{ 'blue' if states('" + v12 + "') in ['Nessun avviso', 'Nessun avviso attivo', 'unknown', 'unavailable'] else 'orange' }}"),
            riga(ua, "{% set s = states('" + ua + "') %}Ultimo avviso: {{ s if s not in ['unknown', 'unavailable'] else 'nessuno' }}",
                 "{% set q = state_attr('" + ua + "', 'quando') %}{{ (q ~ ' · ' ~ (state_attr('" + ua + "', 'gruppo') or '') ~ ' · ' ~ "
                 "(state_attr('" + ua + "', 'seconda_riga') or '')) if q else 'Arrivano solo gli avvisi che l\\'auto mostra sullo schermo' }}",
                 "mdi:car-info", "blue"),
        ]
    software.append(riga(P + "versione", "Versione " + val(P + "versione"),
                         "Aggiornamento: " + val(P + "aggiornamento") + "{{ ', in corso' if is_state('" + B + "aggiornamento_in_corso', 'on') else '' }}"
                         " · modo assistenza " + sino(B + "modo_assistenza", "acceso", "spento"), "mdi:update", "blue"))
    energia = [
        st.titolo("ENERGIA AL GIORNO, 30 GIORNI", "📊", "ambra"),
        grafico("30d", [per_giorno(P + "energia_caricata", "Caricata", BLU), per_giorno(P + "energia_caricata_dc", "di cui DC", ARANCIO),
                        per_giorno(P + "energia_usata", "Usata", AZZURRO)],
                altezza=210, extra=giornaliero, barre=True),
    ]
    km = [
        st.titolo("KM AL GIORNO, 30 GIORNI", "🛣️", "ciano"),
        grafico("30d", [per_giorno(P + "contachilometri", "km", BLU)], altezza=210, extra=giornaliero, barre=True),
    ]
    sezioni = [st.intestazione(st.banner(titolo.upper(), "📡", "blu"), tessere),
               st.colonna(sicurezza, viaggio, energia, software), st.colonna(ricarica, g_carica, batteria),
               st.colonna(clima, gomme, km)]
    return st.vista(titolo, path, "mdi:car-connected", sezioni)


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    a = argparse.ArgumentParser(description="Plancia della Fleet Telemetry Tesla")
    a.add_argument("--prefisso", default="tesla_tele_", help="lo stesso dell'opzione prefisso dell'add-on (predefinito tesla_tele_)")
    a.add_argument("--titolo", default="Telemetria Tesla")
    a.add_argument("--senza-mappa", action="store_true")
    a.add_argument("--senza-avvisi", action="store_true")
    a.add_argument("--senza-stile", action="store_true", help="niente card-mod")
    a.add_argument("--uscita", default=str(QUI / "plancia_telemetria.yaml"))
    x = a.parse_args()
    px = x.prefisso if x.prefisso.endswith("_") else x.prefisso + "_"
    if not re.fullmatch(r"[a-z0-9_]+", px):
        raise SystemExit("--prefisso: solo lettere minuscole, cifre e _ (lo stesso dell'opzione prefisso dell'add-on)")
    v = costruisci(px, mappa=not x.senza_mappa, avvisi=not x.senza_avvisi, con_css=not x.senza_stile, titolo=x.titolo)
    testo = yaml.safe_dump({"title": x.titolo, "views": [v]}, allow_unicode=True, sort_keys=False, width=4000, default_flow_style=False)
    Path(x.uscita).write_text(testo, encoding="utf-8", newline="\n")
    print(f"scritta {x.uscita}: {len(v['sections'])} sezioni, prefisso {px}")


if __name__ == "__main__":
    main()
