"""Haglo Local – kleines, eigenes Sprachmodell, das einfache Befehle direkt auf dem Hub versteht.

Ablauf je Befehl:
1. Vorauswahl hier im Code: aus allen Geräten die (höchstens 10) passenden heraussuchen – nach Name,
   Raum und Gerätart. Das hält den Text für das Modell kurz und damit schnell.
2. Das Modell (Qwen3-0.6B + Haglo-Adapter, läuft mit llama.cpp lokal) antwortet mit kompaktem JSON:
   Aktionen, eine Antwort, eine Rückfrage, einen Timer ("in"), eine Automation ("au"), einen
   Dashboard-Plan ("db") – oder {"c":1}: „zu komplex, bitte an Claude“.
   Rechnen (wärmer/kühler), Farbwerte und das Zusammenbauen von Automation und Dashboard macht der Code.
3. Aktionen laufen durch dieselben Prüfungen wie bei der Cloud (erlaubte Bereiche, Schlösser/Tore nur
   nach Bestätigung). Nichts verlässt das Heimnetz.

Ist das Modell nicht da, nicht sicher oder der Wunsch zu groß, übernimmt wie bisher Claude.
"""

import asyncio
import difflib
import hashlib
import json
import os
import re
import time
import unicodedata
from pathlib import Path

import aiohttp

DIR = Path("/data/local-ai")
STATE_FILE = Path("/data/local-ai.json")       # {"enabled": bool}
PORT = 18180
BASE = {"file": "Qwen3-0.6B-Q8_0.gguf",      # Apache 2.0
        "url": "https://huggingface.co/Qwen/Qwen3-0.6B-GGUF/resolve/main/Qwen3-0.6B-Q8_0.gguf"}
# Der Haglo-Adapter (LoRA) ist Haglo-eigen und liegt NICHT im öffentlichen Repo. Bezug: vom Haglo-Server
# (models.json mit Teilen und Prüfsummen) – oder als Datei im Home-Assistant-Ordner /config/haglo-local/.
MODELS_URL = "https://api.haglotech.com/v1/models/haglo-local/models.json"
EXTRA_DIRS = [Path("/homeassistant/haglo-local")]

SYSTEM = ("Du bist Haglo Local, der lokale Smart-Home-Assistent. Antworte nur mit JSON: "
          '{"a":[{"s":Dienst,"e":[Nummern],"d":{Daten}}],"t":Bestätigung} oder {"t":Antwort} '
          'oder {"f":Rückfrage} oder {"c":1} wenn es zu komplex ist.')

LIMIT_S = 5              # länger wartet niemand: danach übernimmt Claude
LIMIT_PLAN_S = 25        # Timer, Automation, Dashboard dürfen länger dauern – das baut sonst Claude
MAX_LEN = 140            # längere Wünsche sind fast nie „schalte X“ – direkt an Claude
MAX_DEVICES = 10
SHOWN = {"light", "switch", "cover", "climate", "lock", "media_player", "vacuum", "scene", "script",
         "fan", "input_boolean"}
SENSOR_CLASSES = {"temperature", "humidity", "power"}
DOOR_CLASSES = {"door", "window", "garage_door", "opening"}
MOTION_CLASSES = {"motion", "occupancy", "presence"}
ALLOWED_SERVICES = {
    "light": {"turn_on", "turn_off", "toggle"}, "switch": {"turn_on", "turn_off", "toggle"},
    "input_boolean": {"turn_on", "turn_off", "toggle"}, "fan": {"turn_on", "turn_off"},
    "cover": {"open_cover", "close_cover", "stop_cover", "set_cover_position"},
    "climate": {"set_temperature", "set_hvac_mode"}, "lock": {"lock", "unlock"},
    "media_player": {"turn_on", "turn_off", "media_play", "media_pause", "volume_set", "volume_up", "volume_down"},
    "vacuum": {"start", "return_to_base"}, "scene": {"turn_on"}, "script": {"turn_on"},
}

# Wörter, die auf eine Geräteart hindeuten (für die Vorauswahl)
HINTS = {
    "light": ["licht", "lampe", "leuchte", "hell", "dunkel", "dimm", "spots", "strahler", "led", "rot", "blau", "grün",
              "gruen", "gelb", "lila", "orange", "warmweiss", "kaltweiss", "farbe", "lichterkette"],
    "switch": ["steckdose", "stecker", "strom an", "maschine"],
    "cover": ["rollladen", "rolladen", "rollo", "jalousie", "markise", "raffstore", "hoch", "runter", "rauf"],
    "climate": ["heizung", "heiz", "thermostat", "warm", "kalt", "grad", "klima", "kühl", "kuehl", "heiß", "heiss"],
    "sensor:temperature": ["temperatur", "warm", "kalt", "grad", "thermometer"],
    "sensor:humidity": ["feucht"],
    "sensor:power": ["strom", "verbrauch", "leistung", "watt", "bezieh", "bezug", "einspeis", "netz"],
    "binary_sensor": ["fenster", "tür", "tuer", "tor", "offen", "zu", "aufgeht", "zugeht"],
    "binary_sensor:motion": ["bewegung", "bewegt", "bewegungsmelder", "präsenz", "praesenz", "keiner", "niemand"],
    "lock": ["schloss", "abschließ", "abschliess", "verriegel", "aufschließ", "aufschliess", "sperr"],
    "media_player": ["musik", "lautsprecher", "fernseher", "tv", "leiser", "lauter", "pause", "lautstärke", "lautstaerke", "radio", "sonos"],
    "vacuum": ["saug", "staub", "putz"],
}
STOP = {"bitte", "mach", "mache", "mal", "die", "der", "das", "den", "dem", "des", "ein", "eine", "einen", "im", "in", "ins",
        "auf", "an", "aus", "und", "ist", "es", "mir", "zu", "von", "vom", "noch", "doch", "schalte", "schalt", "kannst", "du",
        "ich", "hätte", "gern", "wie", "was", "alle", "alles", "etwas", "danke", "der", "zum", "zur", "am", "um"}


def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state))


def norm(s: str) -> str:
    s = s.lower().replace("ß", "ss").replace("ä", "ae").replace("ö", "oe").replace("ü", "ue")
    s = unicodedata.normalize("NFKD", s)
    return "".join(ch if ch.isalnum() else " " for ch in s)


def words(s: str) -> list[str]:
    return [w for w in norm(s).split() if (len(w) >= 3 or w.isdigit()) and w not in STOP]


def fmt_num(v) -> str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    return str(int(f)) if f == int(f) else str(round(f, 1))


def similar(x: str, n: str) -> bool:
    """Wort aus dem Befehl passt zu einem Namenswort – auch mit Tippfehler oder Wortende."""
    if x == n:
        return True
    # Wortformen (Licht/Lichter, Rollladen/Rollläden): gleicher Anfang, kaum Längenunterschied – aber
    # kein Treffer bei zusammengesetzten Wörtern („Lichterkette“ ist nicht „Licht“)
    if len(x) >= 4 and abs(len(x) - len(n)) <= 3 and (n.startswith(x[:max(4, len(x) - 2)]) or x.startswith(n[:max(4, len(n) - 2)])):
        return True
    return len(x) >= 5 and len(n) >= 5 and difflib.SequenceMatcher(None, x, n).ratio() >= 0.8


def state_line(st: dict) -> str | None:
    """Zustand so formuliert wie im Training."""
    eid, s, a = st["entity_id"], st.get("state"), st.get("attributes", {})
    d = eid.split(".")[0]
    if s in ("unavailable", "unknown", None):
        return "nicht erreichbar"
    if d in ("light",):
        if s != "on":
            return "aus"
        b = a.get("brightness")
        return f"an, {round(b / 255 * 100)}%" if isinstance(b, (int, float)) else "an"
    if d in ("switch", "input_boolean", "fan"):
        return "an" if s == "on" else "aus"
    if d == "cover":
        p = a.get("current_position")
        if isinstance(p, (int, float)):
            return "zu" if p == 0 else "offen" if p == 100 else f"{int(p)}% offen"
        return "zu" if s == "closed" else "offen"
    if d == "climate":
        cur, tgt = a.get("current_temperature"), a.get("temperature")
        if s == "off":
            return f"{fmt_num(cur)} °C, aus" if cur is not None else "aus"
        return f"{fmt_num(cur)} °C, Ziel {fmt_num(tgt)} °C"
    if d == "sensor":
        unit = a.get("unit_of_measurement") or ""
        return f"{fmt_num(s)} {unit}".strip() if unit else fmt_num(s)
    if d == "binary_sensor":
        if a.get("device_class") in MOTION_CLASSES:
            return "Bewegung" if s == "on" else "keine Bewegung"
        return "offen" if s == "on" else "geschlossen"
    if d == "lock":
        return {"locked": "verriegelt", "unlocked": "entriegelt"}.get(s, s)
    if d == "media_player":
        return {"playing": "spielt", "paused": "pausiert", "off": "aus"}.get(s, s)
    if d == "vacuum":
        return {"docked": "angedockt", "cleaning": "reinigt"}.get(s, s)
    return ""


def kind_of(st: dict) -> str | None:
    d = st["entity_id"].split(".")[0]
    dc = st.get("attributes", {}).get("device_class")
    if d in SHOWN:
        return d
    if d == "sensor" and dc in SENSOR_CLASSES:
        return f"sensor:{dc}"
    if d == "binary_sensor" and dc in DOOR_CLASSES:
        return "binary_sensor"
    if d == "binary_sensor" and dc in MOTION_CLASSES:
        return "binary_sensor:motion"
    return None


def preselect(wish: str, states: list[dict], areas: dict[str, str]) -> list[dict]:
    """Höchstens MAX_DEVICES passende Geräte, bestes zuerst. Leere Liste = nichts Passendes."""
    w = words(wish)
    low = norm(wish)
    if not w:
        return []
    scored = []
    room_hit = {r for r in set(areas.values()) if r and norm(r).strip() and norm(r).strip() in low}
    for st in states:
        k = kind_of(st)
        if not k:
            continue
        a = st.get("attributes", {})
        if a.get("hidden") or a.get("entity_category"):
            continue
        name = a.get("friendly_name") or st["entity_id"]
        room = areas.get(st["entity_id"], "")
        nw = set(words(name))
        score = 0.0
        for x in w:
            if x in nw:
                score += 3
            elif any(similar(x, n) for n in nw):
                score += 2
            elif any(len(n) >= 5 and n in x for n in nw):
                score += 1.5        # Wortteil: „zisternenpumpe“ ↔ „Zisternensensor Pumpe“ (nur für das Modell)
        if room and room in room_hit:
            score += 2
        if any(h in low for h in HINTS.get(k, [])):
            score += 1.5
            if k == "sensor:power" and re.search(r"netz|bezug|verbrauch|gesamt|total", norm(name)):
                score += 1      # bei „wie viel Strom …“ zuerst die Gesamt-/Netzwerte
        if k in ("scene", "script") and score < 3:
            continue        # Szenen/Skripte nur bei Namenstreffer
        if score >= 1.5:
            scored.append((score, st, room))
    scored.sort(key=lambda x: -x[0])
    best = scored[0][0] if scored else 0
    # Raum genannt: alle passenden Geräte dieses Raums behalten; sonst nur die deutlich besten
    # Raum genannt: alle passenden Geräte dieses Raums; Name genannt (≥ 3 Punkte): immer – sonst fiele bei
    # „Schreibtisch aus und Garage-Steckdose an“ eines der beiden Geräte heraus
    keep = [s for s in scored if s[0] >= best - 3 or s[0] >= 3 or (s[2] in room_hit)]
    return [{"st": st, "room": room} for _, st, room in keep[:MAX_DEVICES]]


def build_prompt(wish: str, picks: list[dict]) -> tuple[str, dict[int, str]]:
    lines, idx = [], {}
    for i, p in enumerate(picks, 1):
        st = p["st"]
        name = st.get("attributes", {}).get("friendly_name") or st["entity_id"]
        parts = [str(i), st["entity_id"], name, p["room"] or "-"]
        s = state_line(st)
        if s:
            parts.append(s)
        lines.append(" | ".join(parts))
        idx[i] = st["entity_id"]
    return f"Geräte:\n" + "\n".join(lines) + f"\nBefehl: {wish}", idx


def parse(text: str) -> dict | None:
    text = text.strip()
    for cand in (text, text + "}", text + "]}", text + "}]}"):     # durch „stop“ abgeschnittenes Ende ergänzen
        m = re.search(r"\{.*\}", cand, re.S)
        if not m:
            continue
        try:
            return json.loads(m.group(0))
        except Exception:
            continue
    return None


WORDS = {"turn_on": "an", "turn_off": "aus", "toggle": "umgeschaltet", "open_cover": "fährt hoch", "close_cover": "fährt runter",
         "stop_cover": "gestoppt", "set_cover_position": "auf {position} %", "set_temperature": "auf {temperature} °C",
         "set_hvac_mode": "{hvac_mode}", "lock": "verriegelt", "unlock": "entriegelt", "media_play": "spielt",
         "media_pause": "pausiert", "volume_set": "Lautstärke geändert", "volume_up": "lauter", "volume_down": "leiser",
         "start": "legt los", "return_to_base": "fährt zur Station"}


def confirm_text(acts: list[dict], names: dict[str, str]) -> str:
    out = []
    for a in acts:
        dom, svc = a["action"].split(".")
        data = a.get("data") or {}
        if dom in ("scene", "script"):
            out.append(f"„{names.get(a['target']['entity_id'][0], '')}“ gestartet")
            continue
        if svc == "turn_on" and "brightness_pct" in data:
            w = f"auf {data['brightness_pct']} %"
        elif svc == "turn_on" and "brightness_step_pct" in data:
            w = "heller" if data["brightness_step_pct"] > 0 else "dunkler"
        else:
            try:
                w = WORDS.get(svc, svc).format(**{k: str(v).replace(".", ",") for k, v in data.items()})
            except (KeyError, IndexError):
                w = WORDS.get(svc, svc)
            w = {"off": "aus", "heat": "an"}.get(w, w)
        n = ", ".join(names.get(e, e) for e in a["target"]["entity_id"][:4])
        out.append(f"{n} {w}")
    return (". ".join(out) + ".") if out else "Erledigt."


def to_actions(out: dict, idx: dict[int, str]) -> list[dict] | None:
    """Modell-Ausgabe → Aktionen im Format von server.run_actions. None, wenn etwas nicht passt."""
    acts = []
    for a in out.get("a") or []:
        if not isinstance(a, dict):
            return None
        svc = str(a.get("s") or "")
        if svc.count(".") != 1:
            return None
        dom, s = svc.split(".")
        if s not in ALLOWED_SERVICES.get(dom, set()):
            return None
        ids = []
        for e in a.get("e") or []:
            try:
                ids.append(idx[int(e)])
            except (KeyError, ValueError, TypeError):
                return None
        if not ids:
            return None
        if any(i.split(".")[0] != dom for i in ids):
            # An/Aus/Umschalten ist für alle schaltbaren Typen gleich gemeint („Steckdose“ kann technisch ein
            # light sein) – dann je Gerät den passenden Dienst nehmen. Alles andere: lieber Claude.
            if s not in ("turn_on", "turn_off", "toggle"):
                return None
            data = a.get("d") if isinstance(a.get("d"), dict) else {}
            for i in ids:
                d2 = i.split(".")[0]
                if s not in ALLOWED_SERVICES.get(d2, set()):
                    return None
                acts.append({"action": f"{d2}.{s}", "target": {"entity_id": [i]}, **({"data": data} if data else {})})
            continue
        data = a.get("d") if isinstance(a.get("d"), dict) else {}
        acts.append({"action": svc, "target": {"entity_id": ids}, **({"data": data} if data else {})})
    return acts


# ---------------------------------------------------------------- Schnellweg (ohne Modell, < 1 s)
OFF_WORDS = {"aus", "ab", "ausschalten", "abschalten", "ausmachen", "abdrehen", "ausknipsen"}
ON_WORDS = {"an", "ein", "einschalten", "anschalten", "anmachen", "aufdrehen", "anknipsen"}
RE_OPEN = re.compile(r"\b(auf\w*|oeffne\w*|hoch|rauf)\b")
RE_CLOSE = re.compile(r"\b(zu|schliess\w*|runter)\b")


def model_guard(wish: str, acts: list[dict], states: list[dict], areas: dict[str, str]) -> dict | None:
    """Prüft die Aktionen des kleinen Modells gegen den Wortlaut, bevor etwas schaltet.
    → None: in Ordnung. → {"kind": "question", ...}: lieber nachfragen. → {"kind": "claude"}: Claude übernimmt."""
    w = norm(wish)
    toks = set(w.split())
    by_id = {s["entity_id"]: s for s in states}
    for a in acts:
        dom, svc = a["action"].split(".", 1)
        # Schlösser nie über das kleine Modell (auf/zu verwechselt = Haustür offen)
        if dom in ("lock", "alarm_control_panel"):
            return {"kind": "claude"}
        # Richtung muss zum Wortlaut passen (Szenen/Skripte heißen oft „Alles aus“, Helligkeit ist kein An)
        directional = dom not in ("scene", "script") and not a.get("data")
        if directional and svc == "turn_on" and toks & OFF_WORDS and not toks & ON_WORDS:
            return {"kind": "claude"}
        if directional and svc == "turn_off" and toks & ON_WORDS and not toks & OFF_WORDS:
            return {"kind": "claude"}
        if dom == "cover" and ("close" in svc and RE_OPEN.search(w) or "open" in svc and RE_CLOSE.search(w)):
            return {"kind": "claude"}
        # Gleicher Name in mehreren Räumen und kein Raum genannt → nachfragen statt raten
        for e in a["target"]["entity_id"]:
            name = norm((by_id.get(e, {}).get("attributes") or {}).get("friendly_name") or "").strip()
            if not name:
                continue
            twins = [s["entity_id"] for s in states if s["entity_id"].split(".")[0] in ACTIONABLE
                     and norm((s.get("attributes") or {}).get("friendly_name") or "").strip() == name]
            rooms = sorted({areas.get(t) for t in twins if areas.get(t)})
            if set(twins) <= set(a["target"]["entity_id"]):
                continue  # alle gemeint („überall das Licht aus“)
            ww = words(wish)
            glued = w.replace(" ", "")
            if len(twins) > 1 and len(rooms) > 1 and not any(
                    norm(r).replace(" ", "") in glued or any(similar(x, n) for x in ww for n in words(r)) for r in rooms):
                label = (by_id[e].get("attributes") or {}).get("friendly_name")
                return {"kind": "question", "text": f"Welches meinst du? {label} in: {', '.join(rooms)}?"}
    return None


RE_QUESTION = re.compile(r"\?|^\W*(ist|is|isst|sind|wie|was|welche|wieviel|wie ?viel|läuft|laeuft|hab|habe|warum|wann|wo|status)\b", re.I)
RE_MULTI = re.compile(r"\b(und|dann|danach|außerdem|ausserdem|sowie|wenn|falls|sobald)\b|,(?!\d)", re.I)
RE_PCT = re.compile(r"(\d{1,3})\s*(%|prozent)", re.I)
RE_DEG = re.compile(r"(\d{1,2}(?:[.,]5)?)\s*(°|grad)", re.I)
ON = re.compile(r"\b(an|ein|einschalten|anschalten|anmachen|anknipsen|starte|starten|aktivier\w*)\b", re.I)
OFF = re.compile(r"\b(aus|ausschalten|ausmachen|abschalten|ab|ausknipsen|deaktivier\w*)\b", re.I)
UP = re.compile(r"\b(hoch|rauf|öffne\w*|oeffne\w*|auf)\b", re.I)
DOWN = re.compile(r"\b(runter|zu|schließ\w*|schliess\w*|herunter)\b", re.I)
STOPW = re.compile(r"\b(stopp\w*|stop|anhalten|halt)\b", re.I)
SWITCHY = {"light", "switch", "fan", "input_boolean", "media_player"}
ACTIONABLE = SWITCHY | {"cover", "climate", "scene", "script"}


def fast_path(wish: str, states: list[dict], areas: dict[str, str]) -> dict | None:
    """Eindeutige, kurze Schaltbefehle direkt ausführen – ohne Modell. Alles Unklare: None."""
    w = wish.strip()
    if len(w) > 60 or RE_QUESTION.search(w) or RE_MULTI.search(w):
        return None
    low = norm(w)
    picks = preselect(w, states, areas)
    if not picks:
        return None
    # Raum erkennen – auch mit Tippfehler („dachbdoen“); Raumwörter zählen nicht als Gerätename
    cmd_words = words(w)
    room_names = {r for r in areas.values() if r}
    room_words = {x for r in room_names for x in words(r)}
    said = [r for r in room_names if words(r) and all(any(similar(x, y) for x in cmd_words) for y in words(r))]
    room = said[0] if len(said) == 1 else None
    dev_words = [x for x in cmd_words if not any(similar(x, y) for y in room_words)]

    def name_words(p):
        return [n for n in words(p["st"].get("attributes", {}).get("friendly_name") or "") if n not in room_words]

    def score_of(p):
        nw = name_words(p)
        return sum(1 for x in dev_words if any(similar(x, n) for n in nw))

    def in_room(p):
        return room is not None and (p["room"] == room or norm(room).strip() in norm(p["st"].get("attributes", {}).get("friendly_name") or ""))

    light_word = any(similar(x, y) for x in low.split() for y in ("licht", "lichter", "lampen")) or \
        re.search(r"\b(licht|lichter|lampen)\b", low)
    all_lights = light_word and re.search(r"\balle\b|\balles\b|\büberall\b|\bueberall\b|\bsämtliche\b", low)
    generic_light = light_word and not any(score_of(p) >= 2 for p in picks)
    if all_lights or (generic_light and room):
        lights = [{"st": s_, "room": areas.get(s_["entity_id"], "")} for s_ in states if s_["entity_id"].startswith("light.")
                  and not s_.get("attributes", {}).get("hidden")]
        targets = [p for p in lights if in_room(p)] if room else (lights if all_lights else [])
        # „das Licht im Hobbyraum“ und dort heißt ein Gerät schlicht „Licht“: genau das ist gemeint
        named = [p for p in targets if name_words(p) == ["licht"]]
        if room and not all_lights and len(named) == 1:
            targets = named
        if not targets:
            return None
    else:
        # Nur Schaltbares zählt (Messwerte wie „Schreibtisch Leistung“ nicht)
        act = [p for p in picks if p["st"]["entity_id"].split(".")[0] in ACTIONABLE]
        if room:
            act = [p for p in act if in_room(p)]
        if room and not any(score_of(p) >= 1 for p in act):
            # Nur der Raum genannt („Schalte Garage ein“): eindeutig, wenn dort genau ein Schaltbares ist
            only = [p for p in act if p["st"]["entity_id"].split(".")[0] in SWITCHY]
            act = only if len(only) == 1 else []
        else:
            act = [p for p in act if score_of(p) >= 1]
        if not act:
            return None
        best = max(score_of(p) for p in act)
        top = [p for p in act if score_of(p) == best]
        if len(top) > 1:
            # Gleichstand: „Schreibtisch“ schlägt „Schreibtisch Lampe“ (zusätzliches Gerätewort) – aber
            # „Aquarium“ und „Aquarium Hobbyraum“ unterscheiden sich nur im Raum → unklar, lieber nachfragen
            extra = lambda p: len(name_words(p)) - score_of(p)
            top.sort(key=extra)
            if extra(top[0]) == extra(top[1]):
                return None
        targets = [top[0]]
    doms = {t["st"]["entity_id"].split(".")[0] for t in targets}
    if len(doms) != 1:
        return None
    dom = doms.pop()
    ids = [t["st"]["entity_id"] for t in targets]
    pct, deg = RE_PCT.search(w), RE_DEG.search(w)
    act = None
    if dom in ("scene", "script"):
        act = {"action": f"{dom}.turn_on"}
    elif dom == "light" and pct:
        act = {"action": "light.turn_on", "data": {"brightness_pct": max(1, min(100, int(pct.group(1))))}}
    elif dom == "cover":
        if pct:
            act = {"action": "cover.set_cover_position", "data": {"position": max(0, min(100, int(pct.group(1))))}}
        elif STOPW.search(w):
            act = {"action": "cover.stop_cover"}
        elif DOWN.search(w) and not UP.search(w):
            act = {"action": "cover.close_cover"}
        elif UP.search(w) and not DOWN.search(w):
            act = {"action": "cover.open_cover"}
    elif dom == "climate" and deg:
        act = {"action": "climate.set_temperature", "data": {"temperature": float(deg.group(1).replace(",", "."))}}
    elif dom in SWITCHY:
        on, off = bool(ON.search(w)), bool(OFF.search(w))
        if on != off:
            act = {"action": f"{dom}.turn_{'on' if on else 'off'}"}
    if not act:
        return None
    act["target"] = {"entity_id": ids}
    names = {t["st"]["entity_id"]: t["st"].get("attributes", {}).get("friendly_name") or t["st"]["entity_id"] for t in targets}
    return {"kind": "actions", "actions": [act], "text": confirm_text([act], names), "fast": True}


# ---------------------------------------------------------------- Timer, Automation, Dashboard (Plan vom Modell, Bau im Code)
# Wünsche, die geplant statt sofort geschaltet werden – an denen darf der Schnellweg nie sofort schalten
PLAN = re.compile(r"\b(in|nach) (\d+|einer|ner|eine|zwei|drei|fünf|fuenf|zehn|anderthalb|halbe)\b.*(sek|min|stund)|aber erst|timer|"
                  r"wenn |sobald|jeden |jede |täglich|taeglich|werktags|wochenende|immer |automati|bei (sonnen|bewegung)|"
                  r"sonnen(auf|unter)gang|dashboard|übersicht|uebersicht|seitenleiste|um \d|um halb|um viertel|seite f", re.I)
AUTO_CUE = re.compile(r"wenn |sobald|jeden |jede |täglich|taeglich|werktags|woche|immer |automati|bei |um \d|um halb|"
                      r"um viertel|sonnen|bewegung|nachts|morgens|abends", re.I)
DASH_CUE = re.compile(r"d\w{0,2}s\w{0,2}h?\w{0,2}b\w{0,2}r?d|board|baord|[üu]bers|seite|setie|steie|ansicht", re.I)
TIMER_CUE = re.compile(r"\b(in|nach|erst)\b", re.I)
RE_WARM = re.compile(r"wärmer|waermer|kalt|frier|kühl|kuehl|warm|heiß|heiss", re.I)
RE_WARMER = re.compile(r"wärmer|waermer|kalt|frier", re.I)
RE_COOLER = re.compile(r"zu warm|heiß|heiss|kühler|kuehler", re.I)
DASH_KINDS = {  # Kartenart aus dem Plan → welche Geräte dazugehören
    "licht": lambda d, dc: d == "light",
    "heizung": lambda d, dc: d in ("climate", "water_heater"),
    "rollo": lambda d, dc: d == "cover",
    "medien": lambda d, dc: d == "media_player",
    "sensoren": lambda d, dc: d == "sensor" and dc in ("temperature", "humidity", "illuminance", "co2", "pm25"),
    "fenster": lambda d, dc: d == "binary_sensor" and dc in DOOR_CLASSES,
    "energie": lambda d, dc: d == "sensor" and dc in ("power", "energy"),
    "steckdosen": lambda d, dc: d in ("switch", "fan", "input_boolean"),
    "kamera": lambda d, dc: d == "camera",
    "wetter": lambda d, dc: d == "weather",
    "sauger": lambda d, dc: d == "vacuum",
    "szenen": lambda d, dc: d in ("scene", "script"),
}
WEEKDAYS = {"mo": "mon", "di": "tue", "mi": "wed", "do": "thu", "fr": "fri", "sa": "sat", "so": "sun"}


def raw_prompt(user: str) -> str:
    """Genau die Form aus dem Training (Qwen3, ohne Denken)."""
    return (f"<|im_start|>system\n{SYSTEM}<|im_end|>\n<|im_start|>user\n{user}<|im_end|>\n"
            "<|im_start|>assistant\n<think>\n\n</think>\n\n")


def fix_values(wish: str, out: dict, idx: dict[int, str], by_id: dict[str, dict]) -> dict:
    """Rechnen und Farbwerte macht der Code: „wärmer/kühler“ = Ziel ± 1,5 °C, kaltweiß 6000 K, warmweiß 2700 K."""
    for a in out.get("a") or []:
        d = a.get("d") if isinstance(a, dict) and isinstance(a.get("d"), dict) else None
        if d is None:
            continue
        if a.get("s") == "climate.set_temperature" and RE_WARM.search(wish) and not RE_DEG.search(wish):
            try:
                st = by_id[idx[int((a.get("e") or [None])[0])]]
                goal = float(st.get("attributes", {}).get("temperature"))
            except (KeyError, TypeError, ValueError):
                continue
            up = bool(RE_WARMER.search(wish)) and not RE_COOLER.search(wish)
            d["temperature"] = round(goal + (1.5 if up else -1.5), 1)
        if a.get("s") == "light.turn_on" and "color_temp_kelvin" in d:
            low = wish.lower()
            d["color_temp_kelvin"] = 6000 if "kalt" in low else 2700 if "warm" in low else d["color_temp_kelvin"]
    return out


def plan_ok(wish: str, out: dict, idx: dict[int, str], by_id: dict[str, dict]) -> bool:
    """Feste Prüfung von Timer/Automation/Dashboard – lieber Claude fragen als etwas Falsches anlegen."""
    if "au" in out:
        au = out["au"] if isinstance(out["au"], dict) else {}
        w = au.get("w") if isinstance(au.get("w"), dict) else {}
        if not AUTO_CUE.search(wish) or not au.get("a"):
            return False
        if "e" in w:
            try:
                st = by_id[idx[int(w["e"])]]
            except (KeyError, TypeError, ValueError):
                return False
            dom = st["entity_id"].split(".")[0]
            if "zu" in w:
                if dom != "binary_sensor" or w["zu"] not in ("on", "off"):
                    return False
            elif dom != "sensor" or not any(isinstance(w.get(k), (int, float)) for k in ("über", "unter")):
                return False
        elif not (re.fullmatch(r"\d\d:\d\d", str(w.get("zeit") or "")) or w.get("sonne") in ("auf", "unter")):
            return False
        acts = to_actions({"a": au["a"]}, idx)
        return bool(acts) and not any(x["action"].split(".")[0] in ("lock", "alarm_control_panel") for x in acts)
    if "db" in out:
        return bool(DASH_CUE.search(wish)) and isinstance(out["db"], dict)
    if "in" in out:
        try:
            return bool(TIMER_CUE.search(wish)) and 5 <= int(out["in"]) <= 86400 and bool(out.get("a"))
        except (TypeError, ValueError):
            return False
    return True


def automation_object(au: dict, idx: dict[int, str], by_id: dict[str, dict], text: str) -> dict:
    """Plan → Automation im Format von /api/automations/apply (gleiche Bestätigung wie bei Claude)."""
    w, b = au["w"], au.get("b") if isinstance(au.get("b"), dict) else {}
    trig, cond, wann = [], [], ""
    name = lambda i: (by_id[idx[int(i)]].get("attributes") or {}).get("friendly_name") or idx[int(i)]
    if "zeit" in w:
        trig.append({"trigger": "time", "at": f"{w['zeit']}:00"})
        days = [WEEKDAYS[x] for x in w.get("tage") or [] if x in WEEKDAYS]
        if days:
            cond.append({"condition": "time", "weekday": days})
        wann = f"um {w['zeit']} Uhr" + ({"mon,tue,wed,thu,fri": " an Werktagen", "sat,sun": " am Wochenende"}.get(",".join(days), "") if days else " jeden Tag")
    elif "sonne" in w:
        t = {"trigger": "sun", "event": "sunrise" if w["sonne"] == "auf" else "sunset"}
        m = int(w.get("min") or 0)
        if m:
            t["offset"] = f"{'-' if m < 0 else ''}00:{abs(m):02d}:00"
        trig.append(t)
        wann = ("bei Sonnenaufgang" if w["sonne"] == "auf" else "bei Sonnenuntergang") + (f" ({m:+d} min)" if m else "")
    else:
        eid = idx[int(w["e"])]
        if "zu" in w:
            t = {"trigger": "state", "entity_id": eid, "to": w["zu"]}
            dc = (by_id[eid].get("attributes") or {}).get("device_class")
            what = ("Bewegung" if w["zu"] == "on" else "keine Bewegung") if dc in MOTION_CLASSES else \
                ("offen" if w["zu"] == "on" else "geschlossen")
        else:
            key = "über" if "über" in w else "unter"
            t = {"trigger": "numeric_state", "entity_id": eid, ("above" if key == "über" else "below"): w[key]}
            what = f"{key} {fmt_num(w[key])}"
        if w.get("für"):
            t["for"] = {"seconds": int(w["für"])}
            what += f" für {int(w['für']) // 60} min" if int(w["für"]) >= 60 else f" für {int(w['für'])} s"
        trig.append(t)
        wann = f"wenn {name(w['e'])} {what}"
    if b.get("nach") or b.get("vor"):
        c = {"condition": "time"}
        if b.get("nach"):
            c["after"] = f"{b['nach']}:00"
        if b.get("vor"):
            c["before"] = f"{b['vor']}:00"
        cond.append(c)
        wann += f", nur zwischen {b.get('nach', '00:00')} und {b.get('vor', '24:00')} Uhr"
    acts = to_actions({"a": au["a"]}, idx)
    names = {e: (by_id[e].get("attributes") or {}).get("friendly_name") or e for e in by_id}
    was = confirm_text(acts, names).rstrip(".")
    alias = (re.search(r"„(.+?)“", text or "") or [None, f"{wann[:1].upper()}{wann[1:]}: {was}"])[1][:100]
    return {"art": "automation", "alias": alias, "description": f"{wann[:1].upper()}{wann[1:]}: {was}.",
            "mode": "single", "triggers": trig, "conditions": cond,
            "actions": [{"action": x["action"], "target": x["target"], **({"data": x["data"]} if x.get("data") else {})} for x in acts],
            "wann": wann, "was": was}


def dashboard_config(plan: dict, states: list[dict], areas: dict[str, str], registry: dict | None = None) -> tuple[str, dict]:
    """Plan → fertiges Dashboard (Sections-Ansicht, je Raum ein Abschnitt mit Kacheln)."""
    known = sorted({r for r in areas.values() if r})
    rooms, kinds = [], [str(k).lower() for k in plan.get("k") or [] if str(k).lower() in DASH_KINDS]
    for r in plan.get("r") or []:
        r = str(r)
        if r.lower() in DASH_KINDS:
            kinds.append(r.lower())
            continue
        hit = [n for n in known if norm(n) == norm(r)] or [n for n in known if similar(norm(r).strip(), norm(n).strip())]
        if hit and hit[0] not in rooms:
            rooms.append(hit[0])
    kinds = list(dict.fromkeys(kinds))
    reg = registry or {}
    pick = []
    for st in states:
        eid, a = st["entity_id"], st.get("attributes") or {}
        d, dc = eid.split(".")[0], a.get("device_class")
        e = reg.get(eid) or {}
        if a.get("hidden") or a.get("entity_category") or e.get("hidden_by") or e.get("entity_category") or e.get("disabled_by"):
            continue
        k = next((k for k, f in DASH_KINDS.items() if f(d, dc)), None)
        if not k or (kinds and k not in kinds):
            continue
        room = areas.get(eid, "")
        if k != "wetter" and rooms and room not in rooms:
            continue
        if k in ("energie",) and not kinds and rooms:
            continue
        pick.append((room, k, eid))
    order = list(DASH_KINDS)
    sections = []
    for room in (rooms or sorted({p[0] for p in pick if p[0]}) + [""]):
        cards = []
        for _, k, eid in sorted((p for p in pick if p[0] == room and p[1] != "wetter"), key=lambda p: (order.index(p[1]), p[2]))[:24]:
            cards.append({"type": "picture-entity", "entity": eid} if k == "kamera" else
                         {"type": "tile", "entity": eid, **({"features": [{"type": "target-temperature"}]} if k == "heizung" else {})})
        if cards:
            sections.append({"type": "grid", "cards": [{"type": "heading", "heading": room or "Weitere Geräte"}] + cards})
    weather = [p[2] for p in pick if p[1] == "wetter"][:1]
    if weather and (not kinds or "wetter" in kinds):
        sections.insert(0, {"type": "grid", "cards": [{"type": "heading", "heading": "Wetter"},
                                                      {"type": "weather-forecast", "entity": weather[0], "forecast_type": "daily"}]})
    title = str(plan.get("n") or " & ".join(rooms) or "Mein Zuhause")[:40]
    return title, {"title": title, "views": [{"title": title, "path": "start", "type": "sections", "max_columns": 4,
                                              "sections": sections}] if sections else []}


class LocalAI:
    def __init__(self, session: aiohttp.ClientSession):
        self.session = session
        self.proc: asyncio.subprocess.Process | None = None
        self.task: asyncio.Task | None = None
        self.status = "aus"           # aus | lädt Modell | startet | bereit | Fehler: …
        self.progress = 0
        self.lock = asyncio.Lock()
        self.last_ms = None

    # ------------------------------------------------------------ Modell holen und starten
    async def _download(self, url: str, dest: Path, sha: str | None = None, part: str = ""):
        if dest.exists() and (not sha or hashlib.sha256(dest.read_bytes()).hexdigest() == sha):
            return
        tmp = dest.with_suffix(dest.suffix + ".tmp")
        async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=3600)) as r:
            if r.status != 200:
                raise RuntimeError(f"Download fehlgeschlagen ({r.status}): {part or dest.name}")
            total = int(r.headers.get("content-length") or 0)
            got = 0
            h = hashlib.sha256()
            with open(tmp, "wb") as f:
                async for chunk in r.content.iter_chunked(1 << 20):
                    f.write(chunk)
                    h.update(chunk)
                    got += len(chunk)
                    if total:
                        self.progress = int(got / total * 100)
        if sha and h.hexdigest() != sha:
            tmp.unlink(missing_ok=True)
            raise RuntimeError(f"Prüfsumme stimmt nicht: {part or dest.name}")
        tmp.rename(dest)

    async def _ensure_files(self) -> tuple[Path, Path | None]:
        DIR.mkdir(parents=True, exist_ok=True)
        self.status = "lädt Modell"
        adapter = None
        try:
            async with self.session.get(MODELS_URL, timeout=aiohttp.ClientTimeout(total=30)) as r:
                spec = json.loads(await r.text()) if r.status == 200 else None
        except Exception:
            spec = None
        # Der Haglo-Server sagt, welches Basismodell zum Adapter gehört (Wechsel ohne App-Update möglich)
        bspec = (spec or {}).get("base") or BASE
        base = DIR / bspec["file"]
        await self._download(bspec["url"], base, bspec.get("sha256"), part="Basismodell")
        for old in DIR.glob("*.gguf"):
            if old != base and not old.name.startswith("haglo-local-") and spec:
                old.unlink(missing_ok=True)       # altes Basismodell aufräumen (Speicherplatz auf dem Hub)
        if spec:
            adapter = DIR / spec["adapter"]["file"]
            if not adapter.exists() or hashlib.sha256(adapter.read_bytes()).hexdigest() != spec["adapter"]["sha256"]:
                parts = []
                for i, p in enumerate(spec["adapter"]["parts"]):
                    dest = DIR / f"{spec['adapter']['file']}.part{i}"
                    await self._download(p["url"], dest, p["sha256"], part=f"Adapter Teil {i + 1}")
                    parts.append(dest)
                with open(adapter, "wb") as f:
                    for p in parts:
                        f.write(p.read_bytes())
                for p in parts:
                    p.unlink(missing_ok=True)
                if hashlib.sha256(adapter.read_bytes()).hexdigest() != spec["adapter"]["sha256"]:
                    adapter.unlink(missing_ok=True)
                    raise RuntimeError("Haglo-Adapter beschädigt")
        else:
            found = sorted(f for d in [DIR, *EXTRA_DIRS] if d.exists() for f in d.glob("haglo-local-*.gguf"))
            adapter = found[-1] if found else None
        return base, adapter

    async def _run(self):
        delay = 5
        while load_state().get("enabled"):
            try:
                base, adapter = await self._ensure_files()
                self.status = "startet"
                threads = str(max(1, min(4, os.cpu_count() or 2)))
                args = ["llama-server", "-m", str(base), "--host", "127.0.0.1", "--port", str(PORT),
                        "-c", "2048", "-t", threads, "-np", "1"]
                if adapter:
                    args += ["--lora", str(adapter)]
                # niedrige Priorität: Schalten und Home Assistant selbst gehen immer vor
                self.proc = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE,
                                                                 stderr=asyncio.subprocess.STDOUT,
                                                                 preexec_fn=lambda: os.nice(10))
                asyncio.create_task(self._watch_ready())
                async for raw in self.proc.stdout:
                    line = raw.decode(errors="replace").rstrip()
                    if re.search(r"error|failed|listening|model loaded|lora", line, re.I):
                        print("[Lokal]", line[:300], flush=True)
                code = await self.proc.wait()
                if not load_state().get("enabled"):
                    break
                self.status = f"Fehler: Modell beendet (Code {code})"
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.status = f"Fehler: {e}"
                print("[Lokal]", e, flush=True)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 600)
        self.status = "aus"

    async def _watch_ready(self):
        for _ in range(240):
            await asyncio.sleep(1)
            try:
                async with self.session.get(f"http://127.0.0.1:{PORT}/health", timeout=aiohttp.ClientTimeout(total=3)) as r:
                    if r.status == 200:
                        self.status = "bereit"
                        return
            except Exception:
                pass

    async def start(self):
        async with self.lock:
            if self.task and not self.task.done():
                return
            self.task = asyncio.create_task(self._run())

    async def stop(self):
        async with self.lock:
            if self.task:
                self.task.cancel()
            if self.proc and self.proc.returncode is None:
                self.proc.terminate()
                try:
                    await asyncio.wait_for(self.proc.wait(), 15)
                except asyncio.TimeoutError:
                    self.proc.kill()
            self.task, self.proc, self.status = None, None, "aus"

    def info(self) -> dict:
        return {"enabled": bool(load_state().get("enabled")), "status": self.status, "progress": self.progress,
                "last_ms": self.last_ms}

    # ------------------------------------------------------------ Befehl verstehen
    async def understand(self, wish: str, states: list[dict], areas: dict[str, str], use_model: bool = True) -> dict | None:
        """Gibt {"kind": "actions"|"answer"|"question"|"timer"|"automation"|"dashboard", ...} zurück –
        oder None (→ Claude übernimmt)."""
        t_start = time.time()
        planned = bool(PLAN.search(wish))
        quick = None if planned else fast_path(wish, states, areas)
        if quick:
            quick["ms"] = self.last_ms = max(1, int((time.time() - t_start) * 1000))
            print(f"[Lokal] Schnellweg {quick['ms']} ms | {wish!r} → {quick['actions']}", flush=True)
            return quick
        if not use_model or self.status != "bereit" or len(wish) > MAX_LEN:
            why = "Modell aus" if not use_model else f"Modell {self.status}" if self.status != "bereit" else "zu lang"
            print(f"[Lokal] Schnellweg passt nicht, {why} | {wish!r}", flush=True)
            return None
        picks = preselect(wish, states, areas)
        if not picks and DASH_CUE.search(wish):
            # Für einen Dashboard-Plan braucht das Modell keine bestimmten Geräte – ein paar zur Orientierung reichen
            picks = [{"st": st, "room": areas.get(st["entity_id"], "")} for st in states if kind_of(st)][:6]
        if not picks:
            print(f"[Lokal] kein passendes Gerät gefunden | {wish!r}", flush=True)
            return None
        user, idx = build_prompt(wish, picks)
        by_id = {x["entity_id"]: x for x in states}
        t0 = time.time()
        limit = LIMIT_PLAN_S if planned else LIMIT_S
        body = {"prompt": raw_prompt(user), "temperature": 0, "n_predict": 200 if planned else 120, "cache_prompt": True,
                # Nach der Aktionsliste aufhören – die Bestätigung schreibt der Code (spart Zeit)
                "stop": [',"t":', "<|im_end|>"]}
        try:
            async with self.session.post(f"http://127.0.0.1:{PORT}/completion", json=body,
                                         timeout=aiohttp.ClientTimeout(total=limit)) as r:
                if r.status != 200:
                    return None
                data = await r.json()
        except asyncio.TimeoutError:
            print(f"[Lokal] über {limit} s – Claude übernimmt: {wish!r}", flush=True)
            return None
        except Exception:
            return None
        self.last_ms = int((time.time() - t0) * 1000)
        text = data.get("content") or ""
        out = parse(text)
        print(f"[Lokal] {self.last_ms} ms | {wish!r} → {text.strip()[:240]}", flush=True)
        if not isinstance(out, dict) or out.get("c"):
            return None
        if not plan_ok(wish, out, idx, by_id):
            print(f"[Lokal] Prüfung: Plan passt nicht zum Wunsch → Claude | {wish!r}", flush=True)
            return None
        out = fix_values(wish, out, idx, by_id)
        names = {e: (st.get("attributes") or {}).get("friendly_name") or e for e, st in by_id.items()}
        if "au" in out:
            obj = automation_object(out["au"], idx, by_id, str(out.get("t") or ""))
            return {"kind": "automation", "object": obj, "ms": self.last_ms,
                    "text": f"Soll ich die Automation „{obj['alias']}“ anlegen? {obj['description']}"}
        if "db" in out:
            return {"kind": "dashboard", "plan": out["db"], "ms": self.last_ms}
        if out.get("a"):
            acts = to_actions(out, idx)
            if not acts:
                return None
            guard = model_guard(wish, acts, states, areas)
            if guard:
                print(f"[Lokal] Prüfung: {guard} | {wish!r}", flush=True)
                return None if guard["kind"] == "claude" else {**guard, "ms": self.last_ms}
            if "in" in out:
                sec = int(out["in"])
                when = f"{sec} Sekunden" if sec < 60 else f"{sec // 60} Minute{'n' if sec >= 120 else ''}" if sec < 3600 \
                    else f"{sec / 3600:g} Stunde{'n' if sec != 3600 else ''}".replace(".", ",")
                return {"kind": "timer", "actions": acts, "seconds": sec, "ms": self.last_ms,
                        "text": f"In {when}: {confirm_text(acts, names)}"}
            return {"kind": "actions", "actions": acts, "text": confirm_text(acts, names), "ms": self.last_ms}
        if out.get("f"):
            return {"kind": "question", "text": str(out["f"]), "ms": self.last_ms}
        if out.get("t"):
            return {"kind": "answer", "text": str(out["t"]), "ms": self.last_ms}
        return None
