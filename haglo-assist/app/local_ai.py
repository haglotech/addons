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

LIMIT_S = 30             # danach übernimmt Claude (schwache Hubs, z. B. VM mit 2 GB, brauchen so lange)
LIMIT_PLAN_S = 120       # Timer, Automation, Dashboard dürfen länger dauern – das baut sonst Claude
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
        elif svc == "set_temperature" and "target_temp_low" in data:
            w = f"auf {fmt_num(data['target_temp_low'])}–{fmt_num(data.get('target_temp_high'))} °C".replace(".", ",")
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
PLAN = re.compile(r"\b(in|nach|f(ü|ue)r) (\d+|einer|ner|eine|zwei|drei|vier|fünf|fuenf|zehn|zwanzig|dreißig|anderthalb|halbe)\b.*(sek|min|stund)|"
                  r"\bwieder (aus|an|ein|zu|auf)\b|aber erst|timer|"
                  r"wenn |sobald|jeden |jede |täglich|taeglich|werktags|wochenende|immer |automati|bei (sonnen|bewegung)|"
                  r"sonnen(auf|unter)gang|dashboard|übersicht|uebersicht|seitenleiste|um \d|um halb|um viertel|seite f", re.I)
AUTO_CUE = re.compile(r"wenn |sobald|jeden |jede |täglich|taeglich|werktags|woche|immer |automati|bei |um \d|um halb|"
                      r"um viertel|sonnen|bewegung|nachts|morgens|abends", re.I)
DASH_CUE = re.compile(r"d\w{0,2}s\w{0,2}h?\w{0,2}b\w{0,2}r?d|oard|aord|dash|dahs|dsah|[üu]e?b\w{0,2}s\w{0,2}cht|seite|setie|steie|"
                      r"ansicht", re.I)
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
                attrs = by_id[idx[int((a.get("e") or [None])[0])]].get("attributes", {})
            except (KeyError, TypeError, ValueError):
                continue
            step = 1.5 if RE_WARMER.search(wish) and not RE_COOLER.search(wish) else -1.5
            if isinstance(attrs.get("temperature"), (int, float)):
                d["temperature"] = round(attrs["temperature"] + step, 1)
            elif isinstance(attrs.get("target_temp_low"), (int, float)) and isinstance(attrs.get("target_temp_high"), (int, float)):
                # Heizen/Kühlen mit Unter- und Obergrenze: beide Grenzen verschieben
                d.pop("temperature", None)
                d["target_temp_low"] = round(attrs["target_temp_low"] + step, 1)
                d["target_temp_high"] = round(attrs["target_temp_high"] + step, 1)
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


def automation_object(au: dict, idx: dict[int, str], by_id: dict[str, dict], text: str, areas: dict[str, str] | None = None) -> dict:
    """Plan → Automation im Format von /api/automations/apply (gleiche Bestätigung wie bei Claude)."""
    areas_of = lambda e: (areas or {}).get(e, "")
    w, b = au["w"], au.get("b") if isinstance(au.get("b"), dict) else {}
    trig, cond, wann, short = [], [], "", ""
    name = lambda i: (by_id[idx[int(i)]].get("attributes") or {}).get("friendly_name") or idx[int(i)]
    if "zeit" in w:
        trig.append({"trigger": "time", "at": f"{w['zeit']}:00"})
        days = [WEEKDAYS[x] for x in w.get("tage") or [] if x in WEEKDAYS]
        if days:
            cond.append({"condition": "time", "weekday": days})
        wann = f"um {w['zeit']} Uhr" + ({"mon,tue,wed,thu,fri": " an Werktagen", "sat,sun": " am Wochenende"}.get(",".join(days), "") if days else " jeden Tag")
        short = f"{w['zeit']} Uhr" + ({"mon,tue,wed,thu,fri": " werktags", "sat,sun": " am Wochenende"}.get(",".join(days), "") if days else "") + ": {was}"
    elif "sonne" in w:
        t = {"trigger": "sun", "event": "sunrise" if w["sonne"] == "auf" else "sunset"}
        m = int(w.get("min") or 0)
        if m:
            t["offset"] = f"{'-' if m < 0 else ''}00:{abs(m):02d}:00"
        trig.append(t)
        wann = ("bei Sonnenaufgang" if w["sonne"] == "auf" else "bei Sonnenuntergang") + (f" ({m:+d} min)" if m else "")
        short = ("Sonnenaufgang" if w["sonne"] == "auf" else "Sonnenuntergang") + (f" {m:+d} min" if m else "") + ": {was}"
    else:
        eid = idx[int(w["e"])]
        dur = ""
        if w.get("für"):
            dur = f"{int(w['für']) // 60} min" if int(w["für"]) >= 60 else f"{int(w['für'])} s"
        if "zu" in w:
            t = {"trigger": "state", "entity_id": eid, "to": w["zu"]}
            dc = (by_id[eid].get("attributes") or {}).get("device_class")
            if dc in MOTION_CLASSES:
                what = "Bewegung meldet" if w["zu"] == "on" else "keine Bewegung mehr meldet"
                short = ("{was} nach " + dur + " ohne Bewegung") if w["zu"] == "off" and dur else \
                    f"Bewegung{(' ' + areas_of(eid)) if areas_of(eid) else ''}: " + "{was}"
            else:
                what = "geöffnet wird" if w["zu"] == "on" else "geschlossen wird"
                short = f"{name(w['e'])} {'offen' if w['zu'] == 'on' else 'zu'}" + (f" ({dur})" if dur else "") + ": {was}"
        else:
            key = "über" if "über" in w else "unter"
            t = {"trigger": "numeric_state", "entity_id": eid, ("above" if key == "über" else "below"): w[key]}
            what = f"{key} {fmt_num(w[key])} liegt"
            short = f"{name(w['e'])} {key} {fmt_num(w[key])}: " + "{was}"
        if dur:
            t["for"] = {"seconds": int(w["für"])}
            what += f" (für {dur})"
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
        short += " (nachts)" if b.get("nach", "") >= "20:00" else f" ({b.get('nach', '00:00')}–{b.get('vor', '24:00')})"
    acts = to_actions({"a": au["a"]}, idx)
    names = {e: (by_id[e].get("attributes") or {}).get("friendly_name") or e for e in by_id}
    was = confirm_text(acts, names).rstrip(".")
    alias = short.replace("{was}", was)[:100]
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


# ---------------------------------------------------------------- Zahlen, Zeiten und Plausibilität macht der Code
NUMS = {"eine": 1, "einer": 1, "ner": 1, "einen": 1, "zwei": 2, "drei": 3, "vier": 4, "fünf": 5, "fuenf": 5, "sechs": 6,
        "sieben": 7, "acht": 8, "neun": 9, "zehn": 10, "elf": 11, "zwölf": 12, "zwoelf": 12, "fünfzehn": 15,
        "zwanzig": 20, "dreißig": 30, "dreissig": 30, "vierzig": 40, "fünfundvierzig": 45, "neunzig": 90}


def wish_duration(wish: str) -> int | None:
    """„in 10 Minuten“, „nach einer halben Stunde“, „30 sek“ → Sekunden. None, wenn nicht eindeutig."""
    w = norm(wish)
    fixed = [(r"\bhalben? minute", 30), (r"\bviertel ?stunde", 900),
             (r"(?<!anderthalb )\bhalben? stunde", 1800), (r"\banderthalb (stunden|std)", 5400),
             (r"\banderthalb minuten", 90), (r"\b(einer|ner|eine) stunde", 3600), (r"\b(einer|ner|eine) minute", 60)]
    hits = [sec for pat, sec in fixed if re.search(pat, w)]
    for num, unit in re.findall(r"\b(\d{1,3}|" + "|".join(NUMS) + r")\s*(sek\w*|s\b|min\w*|stund\w*|std)", w):
        if num in ("einer", "ner", "eine", "einen") and re.search(r"\bhalben?\b|viertel", w):
            continue
        n = int(num) if num.isdigit() else NUMS[num]
        hits.append(n * (1 if unit.startswith("s") and not unit.startswith("st") else 3600 if unit.startswith("st") else 60))
    return hits[0] if len(set(hits)) == 1 else None


def wish_clock(wish: str) -> str | None:
    """„um 7“, „um halb 8“, „viertel nach 6“, „um 18:30“, „jeden abend um 7“ → "HH:MM"."""
    w = re.sub(r"(\d)\.(\d\d)\b", r"\1:\2", wish.lower())
    evening = bool(re.search(r"\babend|\babends|\bnachmittag", w))
    m = re.search(r"\b(\d{1,2}):(\d{2})\b", w)
    if m:
        h, mi = int(m.group(1)), int(m.group(2))
    elif m := re.search(r"\bhalb (\d{1,2})\b", w):
        h, mi = int(m.group(1)) - 1, 30
    elif m := re.search(r"\bviertel nach (\d{1,2})\b", w):
        h, mi = int(m.group(1)), 15
    elif m := re.search(r"\bviertel vor (\d{1,2})\b", w):
        h, mi = int(m.group(1)) - 1, 45
    elif m := re.search(r"\bum (\d{1,2})( uhr)?\b(?!\s*(grad|%|prozent|min|sek))", w):
        h, mi = int(m.group(1)), 0
    else:
        return None
    if evening and h < 12:
        h += 12
    return f"{h % 24:02d}:{mi:02d}" if 0 <= h <= 24 and 0 <= mi < 60 else None


def wish_sun(wish: str) -> tuple[str | None, int | None]:
    w = norm(wish)
    side = "auf" if re.search(r"sonnenauf|sonne auf|aufgeht|aufgang|morgengrauen", w) else \
        "unter" if re.search(r"sonnenunter|untergeht|untergang|dunkel wird|daemmerung|dämmerung", w) else None
    m = re.search(r"(\d{1,3}) ?(minuten|min) (vor|nach) (sonnen|der sonne|dem sonnen)", w)
    return side, ((-1 if m.group(3) == "vor" else 1) * int(m.group(1)) if m else 0)


def rooms_named(wish: str, areas: dict[str, str]) -> set[str]:
    """Im Wunsch genannte Räume. Ganze Wörter (mit Tippfehlern) – „Waschküche“ nennt nicht auch „Küche“."""
    glued = norm(wish).replace(" ", "")
    ww = words(wish)
    out = set()
    for r in set(areas.values()):
        rw = words(r) if r else []
        if not rw:
            continue
        if len(rw) > 1 and "".join(rw) in glued:
            out.add(r)
        elif all(any(similar(x, n) for x in ww) for n in rw if len(n) >= 3):
            out.add(r)
    return out


def name_hit(wish: str, st: dict) -> bool:
    """Steht ein Wort aus dem Gerätenamen im Wunsch (ohne Raum- und Füllwörter)?"""
    a = st.get("attributes") or {}
    ww = words(wish)
    return any(similar(x, n) for x in ww for n in words(a.get("friendly_name") or "") if n not in STOP and len(n) >= 3)


def exact_hit(wish: str, st: dict, skip: set[str] = frozenset()) -> bool:
    """Ein Wort des Gerätenamens (ohne Raumwörter) steht genau so im Wunsch."""
    ww = set(words(wish))
    return any(n in ww for n in words((st.get("attributes") or {}).get("friendly_name") or "")
               if n not in STOP and n not in skip and len(n) >= 4)


def named_in_wish(wish: str, st: dict, areas: dict[str, str]) -> bool:
    """Kommt das Gerät im Wunsch vor (Name, Gerätart oder Raum)?"""
    a = st.get("attributes") or {}
    ww = words(wish)
    nw = [n for n in words(a.get("friendly_name") or st["entity_id"]) if n not in STOP]
    if any(similar(x, n) or (len(n) >= 5 and n in x) for x in ww for n in nw):
        return True
    k = kind_of(st) or ""
    return any(h in norm(wish) for h in HINTS.get(k, []))



# Neue Dashboards brauchen kein Modell: Räume und Gerätearten stehen im Satz, der Baukasten macht den Rest.
DASH_EDIT = re.compile(r"aufräum|aufraeum|schöner|schoener|änder|aender|hinzufüg|hinzufueg|füg|fueg|lösch|loesch|entfern|"
                       r"bearbeit|umbau|anpass|verschieb|mein dashboard|meine übersicht|das dashboard|dem dashboard|warum|wieso", re.I)
DASH_WORDS = {
    "licht": ["licht", "lichter", "lampen", "lampe", "beleuchtung"],
    "heizung": ["heizung", "heizungen", "thermostat", "thermostate", "klima", "heizkörper"],
    "rollo": ["rollladen", "rollläden", "rolladen", "rollos", "rollo", "jalousie", "jalousien", "beschattung", "markise"],
    "medien": ["musik", "lautsprecher", "medien", "fernseher", "tv", "radio"],
    "sensoren": ["sensoren", "temperatur", "temperaturen", "luftfeuchtigkeit", "messwerte", "klimawerte"],
    "fenster": ["fenster", "türen", "tueren", "tür", "tuer"],
    "energie": ["strom", "stromverbrauch", "energie", "verbrauch", "leistung", "solar", "pv"],
    "steckdosen": ["steckdosen", "steckdose", "schalter", "stecker"],
    "kamera": ["kamera", "kameras"],
    "wetter": ["wetter"],
    "sauger": ["saugroboter", "staubsauger", "sauger", "mähroboter"],
    "szenen": ["szenen", "stimmungen", "skripte"],
}


def dash_plan_from_wish(wish: str, areas: dict[str, str]) -> dict | None:
    """„Dashboard für Küche und Bad“, „Übersicht über Heizungen“ → Plan ohne Modell. None: kein neues Dashboard."""
    if not DASH_CUE.search(wish) or DASH_EDIT.search(wish) or RE_MULTI_DASH.search(wish) or RE_QUESTION.search(wish):
        return None
    ww = words(wish)
    rooms = sorted(rooms_named(wish, areas), key=lambda r: norm(wish).find(norm(r).strip().split(" ")[0]))
    kinds = [k for k, ws in DASH_WORDS.items()
             if any(x == norm(v).strip() or (len(x) >= 5 and similar(x, norm(v).strip())) for x in ww for v in ws)]
    name = " & ".join(rooms) if rooms else " & ".join(k.capitalize() for k in kinds) if kinds else "Mein Zuhause"
    if rooms and kinds:
        name = f"{' & '.join(rooms)} – {' & '.join(k.capitalize() for k in kinds)}"
    return {"n": name[:40], "r": rooms, "k": kinds}


RE_MULTI_DASH = re.compile(r"\b(und dann|danach|außerdem|ausserdem)\b|\b(schalte|mach) .* (an|aus)\b", re.I)


def code_rules(wish: str, out: dict, idx: dict[int, str], by_id: dict[str, dict], areas: dict[str, str]) -> dict | None:
    """Was das kleine Modell schlecht kann, macht der Code; was nicht zum Wunsch passt, geht an Claude (None)."""
    low = norm(wish)
    named = rooms_named(wish, areas)
    room_words = {x for r in set(areas.values()) for x in words(r)}
    acts_src = out["au"]["a"] if isinstance(out.get("au"), dict) else out.get("a") or []
    # 1. Jedes Gerät muss im Wunsch vorkommen und – wenn Räume genannt sind – in einem davon stehen
    for a in acts_src:
        for pos, e in enumerate(list(a.get("e") or [])):
            try:
                st = by_id[idx[int(e)]]
            except (KeyError, TypeError, ValueError):
                return None
            dom = st["entity_id"].split(".")[0]
            room = areas.get(st["entity_id"], "")
            if dom in ("scene", "script"):
                continue
            if named and room and room not in named:
                # Gerät aus einem nicht genannten Raum: zum gleichnamigen Gerät im genannten Raum wechseln;
                # beim Namen genanntes Gerät bei mehreren Befehlen behalten (der Raum gehört zum anderen Teil);
                # sonst nur, wenn es im genannten Raum genau ein Gerät dieser Art gibt
                there = [i for i, x in idx.items() if x.split(".")[0] == dom and areas.get(x) in named and i not in a["e"]]
                same = [i for i in there if name_hit(wish, by_id[idx[i]])]
                if len(same) == 1:
                    a["e"][pos] = same[0]
                elif name_hit(wish, st) and len(acts_src) > 1:
                    pass
                elif len(there) == 1 and not same:
                    a["e"][pos] = there[0]
                else:
                    return None
                st = by_id[idx[a["e"][pos]]]
            # Steht ein Gerätename wörtlich im Wunsch, hat er Vorrang vor einem nur ähnlichen („Deckenlicht“ ≠ „Deckenleuchte“)
            if not exact_hit(wish, st, room_words):
                exact = [i for i, x in idx.items() if x.split(".")[0] == dom and i not in a["e"]
                         and exact_hit(wish, by_id[x], room_words) and (not named or areas.get(x) in named)]
                if len(exact) == 1:
                    a["e"][pos] = exact[0]
                    st = by_id[idx[exact[0]]]
            if (len(acts_src) > 1 or "in" in out or "au" in out) and not named_in_wish(wish, st, areas):
                return None
    # 2. Dasselbe Gerät gleichzeitig an und aus → nicht raten
    seen = {}
    for a in acts_src:
        for e in a.get("e") or []:
            if e in seen and seen[e] != a.get("s"):
                return None
            seen[e] = a.get("s")
    # „überall/alle Lichter“: alle Geräte dieser Art aus der Auswahl, nicht nur ein paar
    if re.search(r"\b(ueberall|überall|alle\b|alles\b)", low) and not named and len(acts_src) == 1 and "au" not in out:
        a = acts_src[0]
        dom = str(a.get("s", "")).split(".")[0]
        if dom in ("light", "cover", "switch", "media_player") and str(a.get("s", "")).split(".")[-1] in (
                "turn_on", "turn_off", "open_cover", "close_cover"):
            a["e"] = [i for i, e in idx.items() if e.startswith(dom + ".")]
    # 3. Heller/dunkler: Richtung aus dem Wort
    for a in acts_src:
        d = a.get("d") if isinstance(a.get("d"), dict) else {}
        if "brightness_step_pct" in d:
            down = bool(re.search(r"dunkler|dunkel\b|dimm|weniger hell|nicht so hell|zu hell", low))
            up = not down and bool(re.search(r"heller|hell\b|aufhell|mehr licht|zu dunkel", low))
            if up != down:
                d["brightness_step_pct"] = abs(d["brightness_step_pct"] or 20) * (1 if up else -1)
    # 4. Timer: Dauer aus dem Satz
    if "in" in out:
        sec = wish_duration(wish)
        if sec:
            out["in"] = sec
    # 5. Automation: Uhrzeit, Sonne, Dauer und passender Sensor aus dem Satz
    if isinstance(out.get("au"), dict):
        w = out["au"].get("w") if isinstance(out["au"].get("w"), dict) else {}
        if "zeit" in w:
            w["zeit"] = wish_clock(wish) or w["zeit"]
            if re.search(r"jeden (tag|morgen|abend)|täglich|taeglich", low) and not re.search(r"werktag|woche", low):
                w.pop("tage", None)
        if "sonne" in w:
            side, off = wish_sun(wish)
            if side:
                w["sonne"] = side
            if off:
                w["min"] = off
            else:
                w.pop("min", None)
        if "zu" in w:
            want = MOTION_CLASSES if re.search(r"beweg|präsenz|praesenz|keiner|niemand", low) else DOOR_CLASSES
            try:
                cur = by_id[idx[int(w["e"])]]
            except (KeyError, TypeError, ValueError):
                return None
            fits = [i for i, e in idx.items() if e.startswith("binary_sensor.")
                    and (by_id[e].get("attributes") or {}).get("device_class") in want]
            in_room = [i for i in fits if areas.get(idx[i]) in named]
            cur_i = int(w["e"])
            if in_room:                      # Sensor im genannten Raum hat Vorrang
                if cur_i not in in_room:
                    if len(in_room) != 1:
                        return None
                    w["e"] = in_room[0]
            elif cur_i not in fits:
                if len(fits) != 1:
                    return None
                w["e"] = fits[0]
            elif len(fits) > 1 and named:
                return None                  # mehrere passende Sensoren, keiner im genannten Raum → nicht raten
            if w.get("für"):
                w["für"] = wish_duration(wish) or w["für"]
    return out


# ---------------------------------------------------------------- Timer und Zeit-Automationen ohne Modell
# Zeitangabe liest der Code, der Rest („Schreibtisch einschalten“) geht durch den Schnellweg. Das ist auf jedem Hub
# sofort da – das Modell kommt nur dran, wenn der Schnellweg den Rest nicht eindeutig versteht.
DUR_NUM = set(NUMS) | {"halbe", "halben", "viertel", "anderthalb"}
FILLER = {"in", "nach", "fuer", "erst", "aber", "und", "zwar", "dann", "danach", "spaeter", "bitte", "danke", "pls", "bitteschoen",
          "automatisch", "immer", "jeden", "jede", "taeglich", "werktags", "werktagen", "wochenende", "woche", "unter",
          "montags", "freitags", "bis", "samstags", "sonntags", "morgen", "morgens", "abend", "abends", "tag", "uhr", "um", "halb",
          "bei", "sonnenuntergang", "sonnenaufgang", "erstell", "erstelle", "automation", "timer", "stell", "stelle", "einen", "am"}
REVERSE = {"turn_on": "turn_off", "turn_off": "turn_on", "open_cover": "close_cover", "close_cover": "open_cover"}


def _is_dur_unit(t: str) -> bool:
    return bool(re.fullmatch(r"(sek\w*|s|min\w*|stund\w*|std|viertelstunde)", t))


def code_plan(wish: str, states: list[dict], areas: dict[str, str]) -> dict | None:
    """„in 10 min Licht aus“, „Schreibtisch für 5 Minuten an“, „an und nach 10 min wieder aus“,
    „jeden Abend um halb 8 Rollläden runter“, „bei Sonnenuntergang Licht an“ → Plan ohne Modell. Sonst None."""
    low = norm(wish)
    toks = low.split()
    sec = wish_duration(wish)
    clock = wish_clock(wish) if re.search(r"\bum\b|\d:\d\d|\bhalb\b|viertel (nach|vor)", wish.lower()) else None
    side, off = wish_sun(wish) if re.search(r"sonnen(auf|unter)gang", low) else (None, 0)
    if not (sec or clock or side) or RE_QUESTION.search(wish) or re.search(r"\b(wenn|falls|sobald|warum|wieso)\b", low):
        return None
    back = False
    keep, skip_next = [], False
    for i, t in enumerate(toks):
        if skip_next:
            skip_next = False
            continue
        nxt = toks[i + 1] if i + 1 < len(toks) else ""
        if similar(t, "wieder") or (nxt in ("aus", "an", "ein", "zu", "auf") and len(t) >= 5
                                    and difflib.SequenceMatcher(None, t, "wieder").ratio() >= 0.7):
            back = True
            if i + 1 < len(toks) and toks[i + 1] in ("aus", "an", "ein", "zu", "auf", "runter", "hoch"):
                skip_next = True
            continue
        if t in FILLER or similar(t, "danach") or t.isdigit() or t in DUR_NUM or _is_dur_unit(t) or \
                (len(t) >= 6 and any(similar(t, f) for f in ("danach", "minuten", "sekunden", "stunden"))):
            continue
        if t in ("vor",) and side:
            continue
        keep.append(t)
    rest = " ".join(keep)
    if re.search(r"\bfuer\b", low) and sec:
        back = True                      # „für 10 Minuten an“ = jetzt an, danach wieder aus
    if not rest or (clock and side) or (back and not sec):
        return None
    quick = fast_path(rest, states, areas)
    if not quick:
        return None
    acts = quick["actions"]
    names = {e: (st.get("attributes") or {}).get("friendly_name") or e for st in states for e in [st["entity_id"]]}
    if clock or side:
        trig = {"zeit": clock} if clock else {"sonne": side, **({"min": off} if off else {})}
        if clock:
            if re.search(r"werktag|montags bis freitags|unter der woche", low):
                trig["tage"] = ["mo", "di", "mi", "do", "fr"]
            elif re.search(r"wochenende|samstags und sonntags", low):
                trig["tage"] = ["sa", "so"]
        idx = {i: e for i, e in enumerate(sorted({e for a in acts for e in a["target"]["entity_id"]}), 1)}
        inv = {e: i for i, e in idx.items()}
        by_id = {st["entity_id"]: st for st in states}
        plan = {"w": trig, "a": [{"s": a["action"], "e": [inv[e] for e in a["target"]["entity_id"]], **({"d": a["data"]} if a.get("data") else {})} for a in acts]}
        obj = automation_object(plan, idx, by_id, "", areas)
        return {"kind": "automation", "object": obj, "text": f"Soll ich die Automation „{obj['alias']}“ anlegen? {obj['description']}"}
    if back:
        rev = []
        for a in acts:
            dom, svc = a["action"].split(".")
            if svc not in REVERSE or a.get("data"):
                return None
            rev.append({"action": f"{dom}.{REVERSE[svc]}", "target": a["target"]})
        when = nice_wait(sec)
        return {"kind": "timer", "now": acts, "actions": rev, "seconds": sec,
                "text": f"{confirm_text(acts, names)} In {when}: {confirm_text(rev, names)}"}
    return {"kind": "timer", "actions": acts, "seconds": sec, "text": f"In {nice_wait(sec)}: {confirm_text(acts, names)}"}


def nice_wait(sec: int) -> str:
    return f"{sec} Sekunden" if sec < 60 else f"{sec // 60} Minute{'n' if sec >= 120 else ''}" if sec < 3600 \
        else f"{sec / 3600:g} Stunde{'n' if sec != 3600 else ''}".replace(".", ",")


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
                    if re.search(r"error|failed|listening|model loaded|lora|prompt eval time|^\S*\s*eval time|       eval time|system_info", line, re.I):
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
        cp = code_plan(wish, states, areas) if planned else None
        if cp:
            cp["ms"] = self.last_ms = max(1, int((time.time() - t_start) * 1000))
            print(f"[Lokal] Plan ohne Modell {cp['ms']} ms | {wish!r} → {cp['kind']}: {cp['text']}", flush=True)
            return cp
        plan = dash_plan_from_wish(wish, areas)
        if plan:
            self.last_ms = max(1, int((time.time() - t_start) * 1000))
            print(f"[Lokal] Dashboard-Plan ohne Modell {self.last_ms} ms | {wish!r} → {plan}", flush=True)
            return {"kind": "dashboard", "plan": plan, "ms": self.last_ms}
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
        out = code_rules(wish, fix_values(wish, out, idx, by_id), idx, by_id, areas)
        if out is None:
            print(f"[Lokal] Prüfung: Gerät/Raum passt nicht zum Wunsch → Claude | {wish!r}", flush=True)
            return None
        names = {e: (st.get("attributes") or {}).get("friendly_name") or e for e, st in by_id.items()}
        if "au" in out:
            obj = automation_object(out["au"], idx, by_id, str(out.get("t") or ""), areas)
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
