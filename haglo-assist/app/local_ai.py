"""Haglo Local – kleines, eigenes Sprachmodell, das einfache Befehle direkt auf dem Hub versteht.

Ablauf je Befehl:
1. Vorauswahl hier im Code: aus allen Geräten die (höchstens 10) passenden heraussuchen – nach Name,
   Raum und Gerätart. Das hält den Text für das Modell kurz und damit schnell.
2. Das Modell (Qwen2.5-0.5B + Haglo-Adapter, läuft mit llama.cpp lokal) antwortet mit kompaktem JSON:
   Aktionen, eine Antwort, eine Rückfrage – oder {"c":1}: „zu komplex, bitte an Claude“.
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
BASE = {"file": "qwen2.5-0.5b-instruct-q4_k_m.gguf",
        "url": "https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct-GGUF/resolve/main/qwen2.5-0.5b-instruct-q4_k_m.gguf"}
# Der Haglo-Adapter (LoRA) liegt in Teilen im Haglo-Repo; Name und Prüfsumme legt models.json fest.
MODELS_URL = "https://raw.githubusercontent.com/haglotech/addons/haglo-local-modelle/haglo-local/models.json"

SYSTEM = ("Du bist Haglo Local, der lokale Smart-Home-Assistent. Antworte nur mit JSON: "
          '{"a":[{"s":Dienst,"e":[Nummern],"d":{Daten}}],"t":Bestätigung} oder {"t":Antwort} '
          'oder {"f":Rückfrage} oder {"c":1} wenn es zu komplex ist.')

LIMIT_S = 5              # länger wartet niemand: danach übernimmt Claude
MAX_LEN = 140            # längere Wünsche sind fast nie „schalte X“ – direkt an Claude
MAX_DEVICES = 10
SHOWN = {"light", "switch", "cover", "climate", "lock", "media_player", "vacuum", "scene", "script",
         "fan", "input_boolean"}
SENSOR_CLASSES = {"temperature", "humidity", "power"}
DOOR_CLASSES = {"door", "window", "garage_door", "opening"}
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
    "binary_sensor": ["fenster", "tür", "tuer", "tor", "offen", "zu"],
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
        base = DIR / BASE["file"]
        await self._download(BASE["url"], base, part="Basismodell")
        adapter = None
        try:
            async with self.session.get(MODELS_URL, timeout=aiohttp.ClientTimeout(total=30)) as r:
                spec = json.loads(await r.text()) if r.status == 200 else None
        except Exception:
            spec = None
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
        elif any(DIR.glob("haglo-local-*.gguf")):
            adapter = sorted(DIR.glob("haglo-local-*.gguf"))[-1]
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
        """Gibt {"kind": "actions"|"answer"|"question", ...} zurück – oder None (→ Claude übernimmt)."""
        t_start = time.time()
        quick = fast_path(wish, states, areas)
        if quick:
            quick["ms"] = self.last_ms = max(1, int((time.time() - t_start) * 1000))
            print(f"[Lokal] Schnellweg {quick['ms']} ms | {wish!r} → {quick['actions']}", flush=True)
            return quick
        if not use_model or self.status != "bereit" or len(wish) > MAX_LEN:
            return None
        picks = preselect(wish, states, areas)
        if not picks:
            return None
        user, idx = build_prompt(wish, picks)
        t0 = time.time()
        body = {"messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}],
                "temperature": 0, "max_tokens": 120, "cache_prompt": True,
                # Bei Aktionen nach der Aktionsliste aufhören – die Bestätigung schreibt der Code (spart Zeit)
                "stop": [',"t":']}
        try:
            async with self.session.post(f"http://127.0.0.1:{PORT}/v1/chat/completions", json=body,
                                         timeout=aiohttp.ClientTimeout(total=LIMIT_S)) as r:
                if r.status != 200:
                    return None
                data = await r.json()
        except asyncio.TimeoutError:
            print(f"[Lokal] über {LIMIT_S} s – Claude übernimmt: {wish!r}", flush=True)
            return None
        except Exception:
            return None
        self.last_ms = int((time.time() - t0) * 1000)
        text = data["choices"][0]["message"]["content"]
        out = parse(text)
        print(f"[Lokal] {self.last_ms} ms | {wish!r} → {text.strip()[:200]}", flush=True)
        if not isinstance(out, dict) or out.get("c"):
            return None
        if out.get("a"):
            acts = to_actions(out, idx)
            if not acts:
                return None
            names = {p["st"]["entity_id"]: p["st"].get("attributes", {}).get("friendly_name") or p["st"]["entity_id"] for p in picks}
            return {"kind": "actions", "actions": acts, "text": confirm_text(acts, names), "ms": self.last_ms}
        if out.get("f"):
            return {"kind": "question", "text": str(out["f"]), "ms": self.last_ms}
        if out.get("t"):
            return {"kind": "answer", "text": str(out["t"]), "ms": self.last_ms}
        return None
