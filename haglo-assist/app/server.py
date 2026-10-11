"""Haglo Assist – Home-Assistant-Add-on.

Liest die Geräte aus Home Assistant, lässt das Haglo-Backend daraus per KI ein
Dashboard bauen und speichert es in einem EIGENEN Dashboard ("haglo-ki").
Bestehende Dashboards des Kunden werden nie angefasst. Vor jedem Speichern wird
ein Backup angelegt, das per "Rückgängig" zurückgespielt werden kann.

Testphase, Zustimmung, Pläne und der KI-Aufruf liegen im Backend
(Standard https://api.haglotech.com). Im Add-on liegt kein API-Schlüssel.
"""

import asyncio
import json
import os
import re
import secrets
import time
from pathlib import Path

import aiohttp
from aiohttp import web

OPTIONS_FILE = Path("/data/options.json")
BACKUP_DIR = Path("/data/backups")
STATIC_DIR = Path(__file__).parent / "static"

HA_WS_URL = "ws://supervisor/core/websocket"
TOKEN = os.environ.get("SUPERVISOR_TOKEN", "")

DASHBOARD_PATH = "haglo-ki"
DASHBOARD_TITLE = "Haglo Dashboard"
MAX_ENTITIES = 400

SKIP_DOMAINS = {
    "update", "event", "tts", "stt", "wake_word", "conversation", "tag",
    "zone", "persistent_notification", "assist_satellite", "ai_task",
}

ADDON_VERSION = "0.12.4"
MAX_VERSIONS = 30                      # so viele Dashboard-Versionen bleiben lokal gespeichert
BAR_ENTITY = "input_text.haglo_assist"           # Eingabeleiste im Dashboard
BAR_STATUS = "input_text.haglo_assist_status"    # Rückmeldung darunter
DEFAULT_BACKEND_URL = "https://api.haglotech.com"
HUB_FILE = Path("/data/hub.json")
TERMS_VERSION = "2026-10"
# Nur als Rückfall, solange das Backend nicht erreichbar ist.
FALLBACK_URLS = {
    "terms_url": "https://haglotech.com/policies/terms-of-service",
    "privacy_url": "https://haglotech.com/policies/privacy-policy",
    "booking_url": "https://haglotech.com/pages/termin",
}


def load_options() -> dict:
    try:
        return json.loads(OPTIONS_FILE.read_text())
    except Exception:
        return {"backend_url": os.environ.get("HAGLO_BACKEND_URL", DEFAULT_BACKEND_URL)}


def backend_url() -> str:
    return (load_options().get("backend_url") or DEFAULT_BACKEND_URL).rstrip("/")


# ---------------------------------------------------------------- Haglo-Backend
class BackendError(Exception):
    def __init__(self, code: str, message: str, status: int = 502, data: dict | None = None):
        super().__init__(message)
        self.code, self.status, self.data = code, status, data or {}


def load_hub() -> dict:
    """Hub-ID und Geheimnis; werden beim ersten Start zufällig erzeugt."""
    try:
        hub = json.loads(HUB_FILE.read_text())
        if hub.get("hub_id") and hub.get("secret"):
            return hub
    except Exception:
        pass
    hub = {"hub_id": secrets.token_hex(16), "secret": secrets.token_urlsafe(32), "registered": False}
    save_hub(hub)
    return hub


def save_hub(hub: dict):
    HUB_FILE.parent.mkdir(parents=True, exist_ok=True)
    HUB_FILE.write_text(json.dumps(hub, indent=2))
    try:
        HUB_FILE.chmod(0o600)
    except OSError:
        pass


async def _backend_request(session, method: str, path: str, body, headers: dict, timeout: int) -> dict:
    try:
        async with session.request(method, backend_url() + path, json=body, headers=headers,
                                   timeout=aiohttp.ClientTimeout(total=timeout)) as r:
            try:
                data = await r.json()
            except Exception:
                data = {}
            if r.status != 200 or not data.get("ok"):
                raise BackendError(data.get("error") or "backend_error",
                                   data.get("message") or f"Haglo-Server antwortet mit Fehler {r.status}.",
                                   r.status, data)
            return data
    except (aiohttp.ClientError, TimeoutError):
        raise BackendError("backend_unreachable", "Der Haglo-Server ist gerade nicht erreichbar. Bitte später erneut versuchen.")


async def ensure_registered(session) -> dict:
    hub = load_hub()
    if not hub.get("registered"):
        await _backend_request(session, "POST", "/v1/hubs/register",
                               {"hub_id": hub["hub_id"], "secret": hub["secret"], "addon_version": ADDON_VERSION},
                               {}, 30)
        hub["registered"] = True
        save_hub(hub)
    return hub


async def backend(session, method: str, path: str, body: dict | None = None, timeout: int = 30) -> dict:
    """Authentifizierter Aufruf des Haglo-Backends."""
    hub = await ensure_registered(session)
    headers = {"X-Hub-Id": hub["hub_id"], "X-Hub-Secret": hub["secret"]}
    try:
        return await _backend_request(session, method, path, body, headers, timeout)
    except BackendError as e:
        if e.status != 401:
            raise
        # Das Backend kennt den Hub nicht (mehr): neu registrieren und einmal wiederholen.
        hub["registered"] = False
        save_hub(hub)
        await ensure_registered(session)
        return await _backend_request(session, method, path, body, headers, timeout)


async def supervisor_get(session, path: str) -> dict:
    async with session.get(f"http://supervisor{path}", headers={"Authorization": f"Bearer {TOKEN}"},
                           timeout=aiohttp.ClientTimeout(total=15)) as r:
        if r.status != 200:
            raise RuntimeError(f"HTTP {r.status}")
        return (await r.json()).get("data") or {}


async def read_hardware(session) -> dict:
    """Seriennummer und MAC-Adressen des Geräts. Damit erkennt das Backend von Haglo
    ausgelieferte Geräte (sie bekommen die Testphase). `note` nennt nur, was nicht lesbar war."""
    serial, notes = "", []
    for name in ("product_serial", "board_serial", "chassis_serial"):
        try:
            serial = Path(f"/sys/class/dmi/id/{name}").read_text().strip()
        except OSError as e:
            notes.append(f"{name}: {type(e).__name__}")
            continue
        if serial:
            break
    macs: set[str] = set()
    try:
        for i in (await supervisor_get(session, "/network/info")).get("interfaces", []):
            if i.get("mac"):
                macs.add(i["mac"])
    except Exception as e:
        notes.append(f"network/info: {e}")
    if not macs:
        # Zweiter Weg: dieselbe Auskunft über die Home-Assistant-Verbindung der App
        try:
            async with HAClient(session) as ha:
                info = await ha.call("supervisor/api", endpoint="/network/info", method="get")
            for i in (info or {}).get("interfaces", []):
                if i.get("mac"):
                    macs.add(i["mac"])
        except Exception as e:
            notes.append(f"ws network/info: {e}")
    if not serial and not macs:
        print("Hardware nicht lesbar:", "; ".join(notes))
    return {"serial": serial, "macs": sorted(macs), "note": "; ".join(notes)[:500] if not (serial and macs) else ""}


async def identify(app, status: dict) -> dict:
    """Meldet Seriennummer und MAC-Adressen ans Backend – erst nachdem der Nutzer zugestimmt
    hat, und nur einmal pro Start. Gibt den (ggf. neuen) Backend-Status zurück."""
    if app.get("identified") or not status.get("accepted"):
        return status
    data = await backend(app["session"], "POST", "/v1/hubs/identify", await read_hardware(app["session"]))
    app["identified"] = True
    return data["status"]


async def selftest(app) -> None:
    """Nur in der Entwickler-Variante: prüft die Home-Assistant-Zugriffe auf dem echten Gerät.
    Läuft, wenn neben server.py eine Datei selftest.flag liegt ({"id": …, "wish": …}), je id einmal.
    Gemeldet werden nur Prüfergebnisse (ok/Fehlertext), keine Inhalte."""
    flag = Path(__file__).with_name("selftest.flag")
    done_file = Path("/data/selftest_done.json")
    try:
        job = json.loads(flag.read_text())
        done = json.loads(done_file.read_text()) if done_file.exists() else []
    except Exception:
        return
    if job.get("id") in done:
        return
    done_file.write_text(json.dumps(done + [job.get("id")]))
    out = [f"selftest {job.get('id')}"]

    async def step(name, coro):
        try:
            out.append(f"{name}: {await coro}")
        except Exception as e:
            out.append(f"{name}: FEHLER {type(e).__name__} {str(e)[:60]}")

    async def hw():
        h = await read_hardware(app["session"])
        extra = ""
        if not h["macs"]:
            try:
                async with HAClient(app["session"]) as ha:
                    info = await ha.call("supervisor/api", endpoint="/network/info", method="get")
                extra = f" ws-keys={sorted((info or {}).keys())[:6]} if0={sorted(((info or {}).get('interfaces') or [{}])[0].keys())[:12]}"
            except Exception as e:
                extra = f" ws={type(e).__name__}:{str(e)[:80]}"
        def dmi(name):
            try:
                return Path(f"/sys/class/dmi/id/{name}").read_text().strip()[:30] or "(leer)"
            except OSError as e:
                return type(e).__name__
        model = f" dmi={dmi('sys_vendor')}/{dmi('product_name')} sn-laenge={len(h['serial'])} uuid={'ja' if len(dmi('product_uuid')) > 20 else dmi('product_uuid')}"
        return f"serial={'ja' if h['serial'] else 'leer'} macs={len(h['macs'])}{model}"

    async def dashes():
        async with HAClient(app["session"]) as ha:
            items = await list_dashboards(ha)
            main = await current_config(ha, None)
            kind = "auto" if main is None or "strategy" in main else f"gespeichert/{count_all_cards(main)} Karten"
            return f"{len(items)} ({sum(1 for d in items if not d['editable'])} yaml), uebersicht={kind}"

    async def version_roundtrip():
        async with HAClient(app["session"]) as ha:
            cfg = await current_config(ha)
        if cfg is None:
            return "kein Haglo Dashboard"
        name = write_backup(cfg, "selftest")
        same = read_backup(BACKUP_DIR / name)["config"] == cfg
        (BACKUP_DIR / name).unlink()
        return "ok" if same else "ungleich"

    async def helpers():
        async with HAClient(app["session"]) as ha:
            return f"{len(await ha.call('input_text/list'))} input_text"

    async def build():
        r = await build_dashboard(app, job["wish"], refine=False, dialog=[], can_ask=False)
        return f"ok {len(r['config'].get('views', []))} Seiten {count_cards(r['config'])} Kacheln, entfernt={len(r['removed'])}, backup={bool(r['backup'])}"

    async def undo_build():
        files = version_files()
        if not files:
            return "kein Backup"
        await restore_version(app, files[-1], keep=False)
        files[-1].unlink()
        return "ok"

    await step("hw", hw())
    await step("dashboards", dashes())
    await step("version", version_roundtrip())
    await step("helper", helpers())
    if job.get("wish"):
        await step("bauen", build())
        if job.get("undo"):
            await step("rueckgaengig", undo_build())
    try:
        hwdata = await read_hardware(app["session"])
        hwdata["note"] = " | ".join(out)[:500]
        await backend(app["session"], "POST", "/v1/hubs/identify", hwdata)
    except Exception as e:
        print(f"Selbsttest: Meldung fehlgeschlagen: {e}")
    print(" | ".join(out))


def trial_info(status: dict | None) -> dict:
    """Übersetzt den Backend-Status in das Format, das die Oberfläche erwartet."""
    if status is None:
        return {"accepted": False, "days_left": 30, "requests_left": 30, "expired": False, **FALLBACK_URLS}
    return {
        "accepted": status["accepted"],
        "days_left": status["days_left"],
        "requests_left": status["requests_left"],
        "expired": status["expired"],
        "plan": status["plan"],
        "haglo_device": status.get("haglo_device", False),
        "level": status.get("level"),
        "included_plan": status.get("included_plan"),
        "included_until": status.get("included_until"),
        "dev": status.get("dev", False),
        "plan_override": status.get("plan_override"),
        "window_hours": status.get("window_hours"),
        "window": status.get("window"),
        "week": status.get("week"),
        "reset_at": status.get("reset_at"),
        "plan_name": status["plan_name"],
        "limit": status["limit"],
        "subscription": status["subscription"],
        "terms_url": status["terms_url"],
        "privacy_url": status["privacy_url"],
        "booking_url": status["booking_url"],
    }


def backend_failure(e: BackendError):
    """Antwort für die Oberfläche bei einem Backend-Fehler."""
    code = {"quota_exceeded": "trial_expired", "consent_required": "consent_required"}.get(e.code)
    body = {"ok": False, "error": code or str(e)}
    if e.data.get("status"):
        body["trial"] = trial_info(e.data["status"])
    return web.json_response(body, status=e.status if 400 <= e.status < 600 else 502)


# ---------------------------------------------------------------- HA-WebSocket
class HAClient:
    """Minimaler WebSocket-Client für die Home-Assistant-API (über Supervisor)."""

    def __init__(self, session: aiohttp.ClientSession):
        self.session = session
        self.ws = None
        self.msg_id = 0

    async def __aenter__(self):
        self.ws = await self.session.ws_connect(HA_WS_URL, heartbeat=30)
        msg = await self.ws.receive_json()  # auth_required
        await self.ws.send_json({"type": "auth", "access_token": TOKEN})
        msg = await self.ws.receive_json()
        if msg.get("type") != "auth_ok":
            raise RuntimeError(f"Anmeldung bei Home Assistant fehlgeschlagen: {msg}")
        return self

    async def __aexit__(self, *exc):
        if self.ws is not None:
            await self.ws.close()

    async def call(self, msg_type: str, **payload):
        self.msg_id += 1
        await self.ws.send_json({"id": self.msg_id, "type": msg_type, **payload})
        while True:
            msg = await self.ws.receive_json()
            if msg.get("id") == self.msg_id and msg.get("type") == "result":
                if not msg.get("success"):
                    err = msg.get("error", {})
                    raise HAError(err.get("code", "unknown"), err.get("message", ""))
                return msg.get("result")


class HAError(Exception):
    def __init__(self, code, message):
        super().__init__(f"{code}: {message}")
        self.code = code


async def collect_entities(ha: HAClient) -> list[dict]:
    states = await ha.call("get_states")
    ent_reg = await ha.call("config/entity_registry/list")
    dev_reg = await ha.call("config/device_registry/list")
    areas = await ha.call("config/area_registry/list")
    area_names = {a["area_id"]: a["name"] for a in areas}
    dev_area = {d["id"]: d.get("area_id") for d in dev_reg}
    reg = {e["entity_id"]: e for e in ent_reg}

    out = []
    for s in states:
        eid = s["entity_id"]
        domain = eid.split(".", 1)[0]
        if domain in SKIP_DOMAINS:
            continue
        r = reg.get(eid, {})
        if r.get("hidden_by") or r.get("disabled_by") or r.get("entity_category"):
            continue
        attrs = s.get("attributes", {})
        area_id = r.get("area_id") or dev_area.get(r.get("device_id"))
        out.append({
            "id": eid,
            "name": attrs.get("friendly_name", eid),
            "area": area_names.get(area_id, ""),
            "device_class": attrs.get("device_class", ""),
            "unit": attrs.get("unit_of_measurement", ""),
        })
    out.sort(key=lambda e: (e["area"] == "", e["area"], e["id"]))
    return out[:MAX_ENTITIES]


# ---------------------------------------------------------------- Prüfung
def clean_config(node, valid: set[str], removed: list[str]):
    """Entfernt erfundene entity_ids und dadurch leere Karten."""
    if isinstance(node, list):
        out = []
        for item in node:
            cleaned = clean_config(item, valid, removed)
            if cleaned is not None:
                out.append(cleaned)
        return out
    if not isinstance(node, dict):
        return node

    node = dict(node)
    if "entity" in node and isinstance(node["entity"], str):
        if node["entity"] not in valid:
            removed.append(node["entity"])
            return None
    if "entities" in node and isinstance(node["entities"], list):
        kept = []
        for e in node["entities"]:
            eid = e if isinstance(e, str) else (e.get("entity") if isinstance(e, dict) else None)
            if eid is None or eid in valid:
                kept.append(e)
            else:
                removed.append(eid)
        if not kept and node.get("type") != "heading":
            return None
        node["entities"] = kept
    for key in ("cards", "sections", "views"):
        if key in node and isinstance(node[key], list):
            node[key] = clean_config(node[key], valid, removed)
            if key == "cards" and not [c for c in node[key] if c.get("type") != "heading"]:
                if node.get("type") in ("grid", "vertical-stack", "horizontal-stack"):
                    return None
    return node


# ---------------------------------------------------------------- Speichern
async def ensure_dashboard(ha: HAClient):
    dashboards = await ha.call("lovelace/dashboards/list")
    if any(d.get("url_path") == DASHBOARD_PATH for d in dashboards):
        return
    await ha.call(
        "lovelace/dashboards/create",
        url_path=DASHBOARD_PATH,
        title=DASHBOARD_TITLE,
        icon="mdi:alpha-h-box-outline",
        mode="storage",
        require_admin=False,
        show_in_sidebar=True,
    )


async def current_config(ha: HAClient, path: str | None = DASHBOARD_PATH):
    """Konfiguration eines Dashboards; path=None ist die Standard-Übersicht."""
    try:
        return await ha.call("lovelace/config", url_path=path)
    except HAError as e:
        if e.code == "config_not_found":
            return None
        raise


# ---------------------------------------------------------------- Eigene Dashboards des Kunden
ALLOWED_FILE = Path("/data/allowed_dashboards.json")


PREFS_FILE = Path("/data/prefs.json")


def load_prefs() -> dict:
    """Kleine Einstellungen der Oberfläche, lokal auf dem Gerät (z. B. weggeklickte Hinweise)."""
    try:
        return json.loads(PREFS_FILE.read_text())
    except Exception:
        return {}


def allowed_dashboards() -> set[str]:
    """Dashboards, deren Bearbeitung (und Übertragung) der Nutzer ausdrücklich bestätigt hat."""
    try:
        return set(json.loads(ALLOWED_FILE.read_text()))
    except Exception:
        return set()


async def list_dashboards(ha: HAClient) -> list[dict]:
    """Alle Dashboards. id ist der url_path; "" steht für die Standard-Übersicht."""
    allowed = allowed_dashboards()
    out = [{"id": DASHBOARD_PATH, "title": DASHBOARD_TITLE, "own": True, "editable": True, "allowed": True}]
    if not load_options().get("own_dashboards"):
        return out  # Bearbeiten eigener Dashboards ist noch eine Option zum Ausprobieren
    items = [{"url_path": "", "title": "Übersicht", "mode": "storage"}] + list(await ha.call("lovelace/dashboards/list"))
    for d in items:
        path = d.get("url_path") or ""
        if path == DASHBOARD_PATH:
            continue
        out.append({"id": path, "title": d.get("title") or path, "own": False,
                    "editable": d.get("mode", "storage") == "storage", "allowed": path in allowed})
    return out


def count_all_cards(node) -> int:
    """Zählt alle Karten, egal wie tief verschachtelt (auch in Stacks und Abschnitten)."""
    if isinstance(node, list):
        return sum(count_all_cards(n) for n in node)
    if not isinstance(node, dict):
        return 0
    own = 1 if isinstance(node.get("type"), str) and "views" not in node and "sections" not in node else 0
    return own + sum(count_all_cards(v) for k, v in node.items() if k in ("views", "sections", "cards", "card", "badges"))


# ---------------------------------------------------------------- Versionen (nur lokal)
def write_backup(config, label: str = "", dashboard: str = DASHBOARD_PATH) -> str | None:
    """Sichert den bisherigen Stand des Dashboards lokal auf dem Gerät, bevor er überschrieben wird."""
    if config is None:
        return None
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    name = time.strftime("%Y%m%d-%H%M%S") + ".json"
    (BACKUP_DIR / name).write_text(json.dumps(
        {"haglo_version": 1, "saved_at": int(time.time()), "label": label[:200], "dashboard": dashboard, "config": config},
        ensure_ascii=False, indent=2))
    for old in sorted(BACKUP_DIR.glob("*.json"))[:-MAX_VERSIONS]:
        old.unlink(missing_ok=True)
    return name


def read_backup(path: Path) -> dict:
    """Liest eine Version; ältere Sicherungen enthalten nur die Dashboard-Konfiguration."""
    data = json.loads(path.read_text())
    if isinstance(data, dict) and data.get("haglo_version") and isinstance(data.get("config"), dict):
        data.setdefault("dashboard", DASHBOARD_PATH)
        return data
    return {"saved_at": int(path.stat().st_mtime), "label": "", "dashboard": DASHBOARD_PATH, "config": data}


def version_files() -> list[Path]:
    return sorted(BACKUP_DIR.glob("*.json")) if BACKUP_DIR.exists() else []


def count_cards(config: dict) -> int:
    return sum(
        len([c for s in v.get("sections", []) for c in s.get("cards", []) if c.get("type") != "heading"])
        + len(v.get("cards", []))
        for v in config.get("views", [])
    )


# ---------------------------------------------------------------- Eingabeleiste im Dashboard
def is_bar(section) -> bool:
    return isinstance(section, dict) and any(
        isinstance(c, dict) and c.get("type") == "entities"
        and any((e.get("entity") if isinstance(e, dict) else e) == BAR_ENTITY for e in c.get("entities", []))
        for c in section.get("cards", []))


def strip_bar(config: dict | None) -> dict | None:
    """Entfernt die Eingabeleiste, bevor das Dashboard zur Bearbeitung ans Backend geht."""
    if not config:
        return config
    config = json.loads(json.dumps(config))
    for view in config.get("views", []):
        if isinstance(view.get("sections"), list):
            view["sections"] = [s for s in view["sections"] if not is_bar(s)]
    return config


def add_bar(config: dict) -> dict:
    """Setzt die Eingabeleiste als ersten Abschnitt in die erste Ansicht."""
    views = config.get("views") or []
    if views and views[0].get("type") == "sections":
        views[0]["sections"] = [{"type": "grid", "cards": [
            {"type": "heading", "heading": "Haglo Assist", "icon": "mdi:blur-radial"},
            {"type": "entities", "entities": [{"entity": BAR_ENTITY, "name": "Was soll ich ändern?"}]},
            {"type": "markdown", "content": "{{ states('" + BAR_STATUS + "') }}"},
        ]}] + [s for s in views[0].get("sections", []) if not is_bar(s)]
    return config


async def ensure_bar_helpers(ha: HAClient):
    have = {i.get("id") for i in await ha.call("input_text/list")}
    if "haglo_assist" not in have:
        await ha.call("input_text/create", name="Haglo Assist", icon="mdi:blur-radial", max=255, mode="text")
    if "haglo_assist_status" not in have:
        await ha.call("input_text/create", name="Haglo Assist Status", icon="mdi:blur-radial", max=255, mode="text")


async def set_text(ha: HAClient, entity: str, value: str):
    await ha.call("call_service", domain="input_text", service="set_value",
                  service_data={"value": value[:255]}, target={"entity_id": entity})


BAR_ERRORS = {
    "consent_required": "Bitte öffne zuerst Haglo Assist in der Seitenleiste und stimme den Bedingungen zu.",
    "quota_exceeded": "Dein Nutzungslimit ist erreicht. Mehr dazu in Haglo Assist in der Seitenleiste.",
}


async def handle_bar_wish(app, wish: str):
    """Ein Wunsch aus der Eingabeleiste im Dashboard: ändert das bestehende Dashboard direkt."""
    session = app["session"]
    async with app["bar_lock"]:
        try:
            async with HAClient(session) as ha:
                await set_text(ha, BAR_STATUS, "Ich baue um … das dauert einen Moment.")
            result = await build_dashboard(app, wish, refine=True, dialog=[], can_ask=False)
            message = "Fertig: " + (result.get("erklaerung") or "Dashboard geändert.")
        except BackendError as e:
            message = BAR_ERRORS.get(e.code, str(e))
        except Exception as e:
            message = f"Das hat nicht geklappt: {e}"
        try:
            async with HAClient(session) as ha:
                await set_text(ha, BAR_ENTITY, "")
                await set_text(ha, BAR_STATUS, message)
        except Exception as e:
            print(f"Eingabeleiste: Rückmeldung fehlgeschlagen: {e}")


async def bar_listener(app):
    """Wartet auf Eingaben in der Leiste. Verbindet sich nach Abbrüchen neu."""
    while True:
        try:
            async with HAClient(app["session"]) as ha:
                await ensure_bar_helpers(ha)
                await ha.ws.send_json({"id": 1000, "type": "subscribe_trigger",
                                       "trigger": {"platform": "state", "entity_id": BAR_ENTITY}})
                print("Eingabeleiste im Dashboard ist aktiv.")
                while True:
                    msg = await ha.ws.receive_json()
                    if msg.get("type") != "event":
                        continue
                    state = (msg["event"].get("variables", {}).get("trigger", {}).get("to_state") or {}).get("state", "")
                    wish = state.strip()
                    if wish and wish not in ("unknown", "unavailable"):
                        asyncio.create_task(handle_bar_wish(app, wish))
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"Eingabeleiste: Verbindung verloren ({e}), neuer Versuch in 20 s")
            await asyncio.sleep(20)


# ---------------------------------------------------------------- Dashboard bauen
async def build_dashboard(app, wish: str, refine: bool, dialog: list, can_ask: bool,
                          dashboard: str = DASHBOARD_PATH) -> dict:
    """Holt das Dashboard vom Backend und speichert es. Gibt entweder eine Rückfrage
    ({"question": …}) oder das Ergebnis zurück. Wirft BackendError bei Backend-Fehlern.

    dashboard ist der url_path; "" ist die Standard-Übersicht. Fremde, vom Kunden selbst
    gebaute Dashboards werden nur gezielt geändert und nur nach ausdrücklicher Freigabe."""
    session = app["session"]
    own = dashboard == DASHBOARD_PATH
    if not own and not load_options().get("own_dashboards"):
        raise RuntimeError("Das Bearbeiten eigener Dashboards ist in der Konfiguration der App ausgeschaltet.")
    if not own and dashboard not in allowed_dashboards():
        raise RuntimeError("Bitte bestätige zuerst, dass Haglo Assist dieses Dashboard bearbeiten darf.")
    path = dashboard or None
    async with HAClient(session) as ha:
        entities = [e for e in await collect_entities(ha) if e["id"] not in (BAR_ENTITY, BAR_STATUS)]
        if not entities:
            raise RuntimeError("Keine Geräte gefunden.")
        before = await current_config(ha, path)
        if not own and (before is None or "strategy" in before or not before.get("views")):
            raise RuntimeError("Dieses Dashboard wird von Home Assistant automatisch erzeugt. Öffne es, wähle "
                               "„Dashboard bearbeiten“ und „Kontrolle übernehmen“ – danach kann Haglo Assist es ändern.")
        result = await backend(session, "POST", "/v1/generate", {
            "wish": wish, "entities": entities,
            "previous": (strip_bar(before) if own else before) if (refine or not own) else None,
            "refine": refine or not own, "foreign": not own, "dialog": dialog, "can_ask": can_ask,
        }, timeout=300)
        if result.get("type") == "question":
            # Die KI braucht noch eine Angabe: Rückfrage anzeigen, nichts speichern.
            return {"question": {"frage": result["frage"], "optionen": result["optionen"],
                                 "mehrfach": bool(result.get("mehrfach"))}, "status": result["status"]}

        removed: list[str] = []
        if own:
            valid = {e["id"] for e in entities}
        else:
            # Im Dashboard des Kunden dürfen auch versteckte oder Diagnose-Geräte stehen bleiben.
            valid = {s["entity_id"] for s in await ha.call("get_states")}
        config = clean_config(result["config"], valid, removed)
        if own:
            config.setdefault("title", DASHBOARD_TITLE)
            if app.get("bar_enabled"):
                config = add_bar(config)
            await ensure_dashboard(ha)
        else:
            # Sicherheitsnetz: Bei einem fremden Dashboard darf nicht plötzlich die Hälfte fehlen.
            had, has = count_all_cards(before), count_all_cards(config)
            if not config.get("views") or (had > 6 and has < had * 0.6):
                raise RuntimeError("Die Änderung hätte große Teile deines Dashboards entfernt. Es wurde nichts "
                                   "gespeichert. Bitte beschreibe genauer, was sich ändern soll.")

        backup = write_backup(before, f"vor: {wish}", dashboard)
        await ha.call("lovelace/config/save", url_path=path, config=config)
    return {"erklaerung": result.get("erklaerung", ""), "config": config, "removed": sorted(set(removed)),
            "backup": backup, "usage": result.get("usage", {}), "status": result["status"],
            "dashboard_url": f"/{dashboard or 'lovelace'}"}


# ---------------------------------------------------------------- HTTP-Routen
routes = web.RouteTableDef()


@routes.get("/")
async def index(_):
    return web.FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})


@routes.get("/api/status")
async def status(request):
    session = request.app["session"]
    try:
        async with HAClient(session) as ha:
            entities = await collect_entities(ha)
            cfg = await current_config(ha)
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=500)
    try:
        st = (await backend(session, "GET", "/v1/status"))["status"]
        trial = trial_info(await identify(request.app, st))
    except BackendError:
        trial = trial_info(None)
    return web.json_response({
        "ok": True,
        "entities": len(entities),
        "areas": sorted({e["area"] for e in entities if e["area"]}),
        "has_dashboard": cfg is not None,
        "api_key": True,  # Der Schlüssel liegt im Backend; Feld bleibt für die Oberfläche.
        "backups": len(version_files()),
        "prefs": load_prefs(),
        "trial": trial,
    })


@routes.post("/api/consent")
async def consent(request):
    """Meldet die Zustimmung zu AGB und Datenschutz ans Backend (dort mit Zeitstempel
    als Nachweis gespeichert). Erst danach wird die Hardware gemeldet, an der die
    Testphase hängt."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    try:
        data = await backend(request.app["session"], "POST", "/v1/consent",
                             {"version": body.get("version") or TERMS_VERSION})
        st = await identify(request.app, data["status"])
    except BackendError as e:
        return backend_failure(e)
    return web.json_response({"ok": True, "trial": trial_info(st)})


@routes.post("/api/generate")
async def generate(request):
    session = request.app["session"]
    body = await request.json()
    wish = (body.get("wish") or "").strip()
    refine = bool(body.get("refine"))
    dialog = body.get("dialog") if isinstance(body.get("dialog"), list) else []
    if not wish:
        return web.json_response({"ok": False, "error": "Bitte einen Wunsch eingeben."}, status=400)
    try:
        # Vorab prüfen, damit ohne Zustimmung oder Kontingent keine Gerätedaten gesendet werden.
        trial = trial_info((await backend(session, "GET", "/v1/status"))["status"])
    except BackendError as e:
        return backend_failure(e)
    if not trial["accepted"]:
        return web.json_response({"ok": False, "error": "consent_required", "trial": trial}, status=403)
    if trial["expired"]:
        return web.json_response({"ok": False, "error": "trial_expired", "trial": trial}, status=402)
    dashboard = body.get("dashboard") if isinstance(body.get("dashboard"), str) else DASHBOARD_PATH
    try:
        result = await build_dashboard(request.app, wish, refine, dialog, can_ask=True, dashboard=dashboard)
    except BackendError as e:
        return backend_failure(e)
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=500)
    if "question" in result:
        return web.json_response({"ok": True, "question": result["question"], "trial": trial_info(result["status"])})

    config = result["config"]
    return web.json_response({
        "ok": True,
        "erklaerung": result["erklaerung"],
        "views": [v.get("title", "") for v in config.get("views", [])],
        "cards": count_cards(config),
        "removed": result["removed"],
        "backup": result["backup"],
        "dashboard_url": result["dashboard_url"],
        "usage": result["usage"],
        "trial": trial_info(result["status"]),
    })


@routes.post("/api/dev")
async def dev_settings(request):
    """Nur für Entwickler-Hubs (legt das Backend fest): Plan umschalten, Limits zurücksetzen."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    try:
        data = await backend(request.app["session"], "POST", "/v1/dev/settings", body)
    except BackendError as e:
        return backend_failure(e)
    return web.json_response({"ok": True, "trial": trial_info(data["status"])})


@routes.post("/api/prefs")
async def save_prefs(request):
    """Merkt sich weggeklickte Hinweise dauerhaft – für alle Browser und Geräte dieses Hubs."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    prefs = load_prefs()
    if body.get("owner_hint_dismissed") is True:
        prefs["owner_hint_dismissed"] = True
    PREFS_FILE.parent.mkdir(parents=True, exist_ok=True)
    PREFS_FILE.write_text(json.dumps(prefs))
    return web.json_response({"ok": True, "prefs": prefs})


@routes.get("/api/dashboards")
async def dashboards(request):
    """Alle Dashboards zur Auswahl in der Oberfläche."""
    try:
        async with HAClient(request.app["session"]) as ha:
            return web.json_response({"ok": True, "dashboards": await list_dashboards(ha)})
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=500)


@routes.post("/api/dashboards/allow")
async def allow_dashboard(request):
    """Der Nutzer bestätigt, dass Haglo Assist ein eigenes Dashboard bearbeiten darf."""
    try:
        name = (await request.json()).get("id")
    except Exception:
        name = None
    if not isinstance(name, str) or len(name) > 100:
        return web.json_response({"ok": False, "error": "Ungültiges Dashboard."}, status=400)
    allowed = allowed_dashboards() | {name}
    ALLOWED_FILE.parent.mkdir(parents=True, exist_ok=True)
    ALLOWED_FILE.write_text(json.dumps(sorted(allowed)))
    return web.json_response({"ok": True})


@routes.get("/api/versions")
async def versions(request):
    """Lokal gespeicherte Dashboard-Versionen, neueste zuerst."""
    titles = {}
    try:
        async with HAClient(request.app["session"]) as ha:
            titles = {d["id"]: d["title"] for d in await list_dashboards(ha)}
    except Exception:
        pass
    out = []
    for f in reversed(version_files()):
        try:
            v = read_backup(f)
        except Exception:
            continue
        out.append({"id": f.name, "saved_at": v["saved_at"], "label": v.get("label", ""),
                    "dashboard": titles.get(v["dashboard"], v["dashboard"] or "Übersicht"),
                    "views": [x.get("title", "") for x in v["config"].get("views", [])],
                    "cards": count_cards(v["config"])})
    return web.json_response({"ok": True, "versions": out, "max": MAX_VERSIONS})


async def restore_version(app, path: Path, keep: bool) -> None:
    """Spielt eine Version zurück. Mit keep=True wird vorher der aktuelle Stand gesichert,
    damit auch das Wiederherstellen rückgängig gemacht werden kann."""
    version = read_backup(path)
    dashboard = version["dashboard"]
    async with HAClient(app["session"]) as ha:
        if keep:
            write_backup(await current_config(ha, dashboard or None), "vor Wiederherstellung", dashboard)
        if dashboard == DASHBOARD_PATH:
            await ensure_dashboard(ha)
        await ha.call("lovelace/config/save", url_path=dashboard or None, config=version["config"])


@routes.post("/api/restore")
async def restore(request):
    try:
        name = str((await request.json()).get("id", ""))
    except Exception:
        name = ""
    path = BACKUP_DIR / name
    if not re.fullmatch(r"\d{8}-\d{6}\.json", name) or not path.exists():
        return web.json_response({"ok": False, "error": "Diese Version gibt es nicht mehr."}, status=404)
    try:
        await restore_version(request.app, path, keep=True)
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=500)
    return web.json_response({"ok": True, "restored": name})


@routes.post("/api/undo")
async def undo(request):
    """Macht die letzte Änderung rückgängig (stellt die neueste Version wieder her)."""
    files = version_files()
    if not files:
        return web.json_response({"ok": False, "error": "Kein Backup vorhanden."}, status=404)
    latest = files[-1]
    try:
        await restore_version(request.app, latest, keep=False)
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=500)
    latest.unlink()
    return web.json_response({"ok": True, "restored": latest.name})


@routes.get("/api/plans")
async def plans(request):
    """Pläne und Preise aus dem Backend."""
    try:
        return web.json_response(await backend(request.app["session"], "GET", "/v1/plans"))
    except BackendError as e:
        return backend_failure(e)


async def forward(request, path: str):
    try:
        body = await request.json()
    except Exception:
        body = {}
    try:
        return web.json_response(await backend(request.app["session"], "POST", path, body))
    except BackendError as e:
        return backend_failure(e)


@routes.post("/api/request")
async def create_request(request):
    """Integrations-Anfrage oder Vormerkung einer Erweiterung: {kind, item, message, contact}."""
    return await forward(request, "/v1/requests")


@routes.post("/api/checkout")
async def checkout(request):
    """Abo abschließen: {plan, interval} -> {url} (Stripe Checkout)."""
    return await forward(request, "/v1/billing/checkout")


@routes.post("/api/portal")
async def portal(request):
    """Abo verwalten oder kündigen -> {url} (Stripe-Kundenportal)."""
    return await forward(request, "/v1/billing/portal")


async def on_startup(app):
    app["session"] = aiohttp.ClientSession()
    app["bar_lock"] = asyncio.Lock()
    app["bar_enabled"] = bool(load_options().get("dashboard_bar"))
    try:
        await ensure_registered(app["session"])
        await identify(app, (await backend(app["session"], "GET", "/v1/status"))["status"])
    except BackendError as e:
        print(f"Registrierung beim Haglo-Server noch nicht möglich: {e}")
    if app["bar_enabled"]:
        app["bar_task"] = asyncio.create_task(bar_listener(app))
    asyncio.create_task(selftest(app))


async def on_cleanup(app):
    if app.get("bar_task"):
        app["bar_task"].cancel()
    await app["session"].close()


def make_app() -> web.Application:
    app = web.Application()
    app.add_routes(routes)
    app.router.add_static("/static/", STATIC_DIR)
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    return app


if __name__ == "__main__":
    web.run_app(make_app(), host="0.0.0.0", port=8099)
