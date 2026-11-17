"""Stile della vista: titolo con riga animata, tessere, titoli di sezione e righe con sfondo scuro (richiede card-mod).
CSS originali di questo progetto (MIT). Solo calcoli, niente rete. I colori di base dei CSS (rosso) si sostituiscono con
quelli della PALETTE."""
import copy

PALETTE = {
    "ciano": ((0, 188, 212), "#00bcd4", (38, 198, 218), "#26c6da"),
    "teal": ((38, 166, 154), "#26a69a", (77, 182, 172), "#4db6ac"),
    "blu": ((33, 150, 243), "#2196f3", (66, 165, 245), "#42a5f5"),
    "ambra": ((255, 179, 0), "#ffb300", (255, 202, 40), "#ffca28"),
    "viola": ((206, 147, 216), "#ce93d8", (186, 104, 200), "#ba68c8"),
}
BANNER_CSS = 'ha-card {\n  background: linear-gradient(135deg,\n    rgba(15, 15, 28, 0.97) 0%,\n    rgba(22, 30, 48, 0.95) 50%,\n    rgba(15, 22, 40, 0.97) 100%);\n  border: 1px solid rgba(244, 67, 54, 0.2);\n  border-radius: 24px;\n  padding: 18px 24px 14px 24px;\n  position: relative;\n  overflow: hidden;\n}\nha-card .header,\nha-card .container {\n  padding: 0 !important;\n  margin: 0 !important;\n}\nha-card .title {\n  margin: 0 !important;\n  padding: 0 !important;\n  font-size: 28px !important;\n  font-weight: 700 !important;\n  line-height: 1.1 !important;\n  display: inline-flex !important;\n  align-items: center !important;\n  gap: 10px !important;\n  width: max-content;\n  background: linear-gradient(90deg, #f44336, #ef5350, #f44336);\n  background-size: 200% auto;\n  background-position: 0% center;\n  -webkit-background-clip: text;\n  -webkit-text-fill-color: transparent;\n  animation: shine 3s linear infinite;\n  pointer-events: none;\n}\nha-card .title::before {\n  content: "⚡";\n  font-size: 26px;\n  line-height: 1;\n  -webkit-text-fill-color: #f44336;\n  background: none !important;\n  filter: drop-shadow(0 0 5px rgba(244,67,54,0.55));\n}\nha-card .subtitle { display: none !important; }\nha-card::after {\n  content: "";\n  display: block;\n  width: 100%;\n  height: 4px;\n  margin-top: 12px;\n  border-radius: 999px;\n  background-image:\n    linear-gradient(90deg, rgba(244,67,54,0.20), rgba(239,83,80,0.20)),\n    linear-gradient(90deg,\n      transparent 0%,\n      rgba(244,67,54,0.00) 18%,\n      rgba(244,67,54,0.70) 40%,\n      rgba(239,83,80,0.85) 47%,\n      rgba(255,255,255,1.00) 50%,\n      rgba(239,83,80,0.85) 53%,\n      rgba(244,67,54,0.70) 60%,\n      rgba(244,67,54,0.00) 82%,\n      transparent 100%),\n    radial-gradient(circle,\n      rgba(255,255,255,1.00) 0%,\n      rgba(255,255,255,0.55) 35%,\n      rgba(244,67,54,0.22) 58%,\n      transparent 75%);\n  background-size: 100% 100%, 200% 100%, 18px 18px;\n  background-repeat: no-repeat;\n  background-position: 0% 0, 110% 0, -12px 50%;\n  animation: sweep-main 2.2s ease-in-out infinite alternate;\n  will-change: background-position, opacity;\n  box-shadow:\n    0 0 8px rgba(244,67,54,0.35),\n    0 0 16px rgba(239,83,80,0.22);\n  filter:\n    drop-shadow(0 0 6px rgba(255,255,255,0.18))\n    drop-shadow(0 0 10px rgba(244,67,54,0.14));\n  pointer-events: none;\n}\n@keyframes shine { to { background-position: 200% center; } }\n@keyframes sweep-main {\n  0%   { background-position: 0% 0, 110% 0, -12px 50%; opacity: 0.65; }\n  50%  { opacity: 1; }\n  100% { background-position: 0% 0, -10% 0, calc(100% + 12px) 50%; opacity: 0.65; }\n}\n'
TESSERA_CSS = 'ha-card {\n  --pulse-color: 0,188,212;\n  border: 1px solid rgba(0,188,212,0.35);\n  background: linear-gradient(145deg,\n    rgba(var(--pulse-color), 0.10) 0%,\n    rgba(var(--pulse-color), 0.02) 100%);\n  border-radius: 20px;\n  backdrop-filter: blur(12px);\n  --icon-size: 48px;\n  position: relative;\n  overflow: hidden;\n}\nmushroom-shape-icon {\n  filter: drop-shadow(0 0 8px rgba(0,188,212,0.6)) drop-shadow(0 0 20px rgba(0,188,212,0.3));\n}\n.primary {\n  font-size: 17px !important;\n  font-weight: 800 !important;\n  letter-spacing: 0.5px;\n  text-transform: uppercase;\n}\n.secondary {\n  font-size: 11px !important;\n  font-weight: 500 !important;\n  opacity: 0.85;\n}\nha-card::after {\n  content: "";\n  position: absolute;\n  bottom: 0; left: 0; right: 0;\n  height: 3px;\n  border-radius: 0 0 20px 20px;\n  background: linear-gradient(90deg,\n    transparent 0%, rgba(var(--pulse-color), 0.0) 15%,\n    rgba(var(--pulse-color), 0.8) 40%, rgba(var(--pulse-color), 1.0) 50%,\n    rgba(var(--pulse-color), 0.8) 60%, rgba(var(--pulse-color), 0.0) 85%,\n    transparent 100%);\n  background-size: 200% 100%;\n  animation: sweep-bar 2.5s ease-in-out infinite alternate;\n}\n@keyframes pulse-card {\n  0%, 100% { box-shadow: 0 0 10px rgba(var(--pulse-color), 0.15), 0 0 30px rgba(var(--pulse-color), 0.08); }\n  50% { box-shadow: 0 0 18px rgba(var(--pulse-color), 0.35), 0 0 45px rgba(var(--pulse-color), 0.15); }\n}\n@keyframes sweep-bar {\n  0%   { background-position: 120% 0; }\n  100% { background-position: -20% 0; }\n}\n'
SEZIONE_CSS = 'ha-card { padding: 12px 16px 8px 16px; }\n\nha-card .title {\n  margin: 0 !important;\n  font-size: 20px !important;\n  font-weight: 700 !important;\n  line-height: 1.1;\n  display: inline-flex;\n  align-items: center;\n  gap: 10px;\n  background: linear-gradient(90deg, #f44336, #ef5350, #f44336);\n  background-size: 200% auto;\n  -webkit-background-clip: text;\n  -webkit-text-fill-color: transparent;\n  animation: shine 3s linear infinite;\n}\n\nha-card .title::before {\n  content: "📊";\n  font-size: 20px;\n  line-height: 1;\n  -webkit-text-fill-color: #f44336;\n  background: none !important;\n  filter: drop-shadow(0 0 8px rgba(244,67,54,0.75));\n}\n\nha-card .subtitle { display: none !important; }\n\nha-card::after {\n  content: "";\n  display: block;\n  width: 100%;\n  height: 4px;\n  margin-top: 10px;\n  border-radius: 999px;\n  background-image:\n    linear-gradient(90deg, rgba(244,67,54,0.30), rgba(239,83,80,0.30)),\n    linear-gradient(90deg,\n      transparent 0%,\n      rgba(244,67,54,0.00) 10%,\n      rgba(244,67,54,0.85) 28%,\n      rgba(239,83,80,0.95) 45%,\n      rgba(255,255,255,1.00) 50%,\n      rgba(239,83,80,0.95) 55%,\n      rgba(244,67,54,0.85) 72%,\n      rgba(244,67,54,0.00) 90%,\n      transparent 100%),\n    radial-gradient(circle,\n      rgba(255,255,255,1.00) 0%,\n      rgba(255,255,255,0.80) 30%,\n      rgba(244,67,54,0.50) 55%,\n      transparent 72%);\n  background-size: 100% 100%, 240% 100%, 24px 24px;\n  background-repeat: no-repeat;\n  background-position: 0% 0, 120% 0, -14px 50%;\n  animation: sweep-sec 2.7s ease-in-out infinite alternate;\n  will-change: background-position, opacity;\n  box-shadow:\n    0 0 14px rgba(244,67,54,0.70),\n    0 0 28px rgba(239,83,80,0.45),\n    0 0 4px rgba(255,255,255,0.30);\n  filter:\n    drop-shadow(0 0 8px rgba(255,255,255,0.25))\n    drop-shadow(0 0 14px rgba(244,67,54,0.30));\n  pointer-events: none;\n}\n\n@keyframes shine { to { background-position: 200% center; } }\n\n@keyframes sweep-sec {\n  0%   { background-position: 0% 0, 120% 0, -14px 50%; opacity: 0.70; }\n  50%  { opacity: 1; }\n  100% { background-position: 0% 0, -20% 0, calc(100% + 14px) 50%; opacity: 0.70; }\n}\n'
RIGA_CSS = "ha-card {\n  background: rgba(30,30,40,0.6);\n  border: 1px solid rgba(100,100,100,0.3);\n  border-radius: 16px;\n  backdrop-filter: blur(10px);\n}\n"


def _ricolora(css, colore, emoji=None, vecchia=None):
    (a, b, c), h1, (d, e, f), h2 = PALETTE[colore]
    for vecchio, nuovo in (("244, 67, 54", f"{a}, {b}, {c}"), ("244,67,54", f"{a},{b},{c}"), ("239, 83, 80", f"{d}, {e}, {f}"),
                           ("239,83,80", f"{d},{e},{f}"), ("#f44336", h1), ("#ef5350", h2)):
        css = css.replace(vecchio, nuovo)
    if emoji and vecchia:
        css = css.replace(vecchia, emoji)
    return css


class Stile:
    """con_css=False: niente card-mod, schede semplici."""

    def __init__(self, con_css=True):
        self.con_css = con_css

    def _mod(self, scheda, css):
        if self.con_css:
            scheda["card_mod"] = {"style": css}
        return scheda

    def banner(self, titolo, emoji, colore):
        return self._mod({"type": "custom:mushroom-title-card", "title": titolo, "subtitle": ""},
                         _ricolora(BANNER_CSS, colore, emoji, "\u26a1"))

    def titolo(self, titolo, emoji, colore):
        return self._mod({"type": "custom:mushroom-title-card", "title": titolo if self.con_css else f"{emoji} {titolo}", "subtitle": ""},
                         _ricolora(SEZIONE_CSS, colore, emoji, "\U0001f4ca"))

    def tessera(self, scheda, colore):
        c = copy.deepcopy(scheda)
        c.pop("grid_options", None)
        a, b, d = PALETTE[colore][0]
        c["vertical"] = True
        return self._mod(c, TESSERA_CSS.replace("0,188,212", f"{a},{b},{d}"))

    def riga(self, scheda):
        c = copy.deepcopy(scheda)
        c.pop("grid_options", None)
        return self._mod(c, RIGA_CSS)

    @staticmethod
    def vista(titolo, path, icona, sezioni, **extra):
        return {"title": titolo, "path": path, "icon": icona, "type": "sections", "max_columns": 3,
                "dense_section_placement": True, "sections": sezioni, **extra}

    @staticmethod
    def intestazione(banner, tessere):
        return {"type": "grid", "column_span": 3, "cards": [{"type": "vertical-stack", "grid_options": {"columns": "full"},
                                                             "cards": [banner, {"type": "horizontal-stack", "cards": tessere}]}]}

    @staticmethod
    def colonna(*pile):
        return {"type": "grid", "column_span": 1,
                "cards": [{"type": "vertical-stack", "grid_options": {"columns": "full"}, "cards": p} for p in pile]}
