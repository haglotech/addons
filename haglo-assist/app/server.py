"""Haglo Assist – Home-Assistant-Add-on.

Liest die Geräte aus Home Assistant, lässt das Haglo-Backend daraus per KI ein
Dashboard bauen und speichert es in einem EIGENEN Dashboard ("haglo-ki").
Bestehende Dashboards des Kunden werden nie angefasst. Vor jedem Speichern wird
ein Backup angelegt, das per "Rückgängig" zurückgespielt werden kann.

Testphase, Zustimmung, Pläne und der KI-Aufruf liegen im Backend
(Standard https://api.haglotech.com). Im Add-on liegt kein API-Schlüssel.
"""

import json
import os
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

ADDON_VERSION = "0.7.0"
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


async def current_config(ha: HAClient):
    try:
        return await ha.call("lovelace/config", url_path=DASHBOARD_PATH)
    except HAError as e:
        if e.code == "config_not_found":
            return None
        raise


def write_backup(config) -> str | None:
    if config is None:
        return None
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    name = time.strftime("%Y%m%d-%H%M%S") + ".json"
    (BACKUP_DIR / name).write_text(json.dumps(config, ensure_ascii=False, indent=2))
    return name


# ---------------------------------------------------------------- HTTP-Routen
routes = web.RouteTableDef()


@routes.get("/")
async def index(_):
    return web.FileResponse(STATIC_DIR / "index.html")


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
        trial = trial_info((await backend(session, "GET", "/v1/status"))["status"])
    except BackendError:
        trial = trial_info(None)
    return web.json_response({
        "ok": True,
        "entities": len(entities),
        "areas": sorted({e["area"] for e in entities if e["area"]}),
        "has_dashboard": cfg is not None,
        "api_key": True,  # Der Schlüssel liegt im Backend; Feld bleibt für die Oberfläche.
        "backups": len(list(BACKUP_DIR.glob("*.json"))) if BACKUP_DIR.exists() else 0,
        "trial": trial,
    })


@routes.post("/api/consent")
async def consent(request):
    """Meldet die Zustimmung zu AGB und Datenschutz ans Backend (dort mit Zeitstempel
    als Nachweis gespeichert) und startet die Testphase."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    try:
        data = await backend(request.app["session"], "POST", "/v1/consent",
                             {"version": body.get("version") or TERMS_VERSION})
    except BackendError as e:
        return backend_failure(e)
    return web.json_response({"ok": True, "trial": trial_info(data["status"])})


@routes.post("/api/generate")
async def generate(request):
    session = request.app["session"]
    body = await request.json()
    wish = (body.get("wish") or "").strip()
    refine = bool(body.get("refine"))
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
    try:
        async with HAClient(session) as ha:
            entities = await collect_entities(ha)
            if not entities:
                raise RuntimeError("Keine Geräte gefunden.")
            previous = await current_config(ha) if refine else None
            result = await backend(session, "POST", "/v1/generate", {
                "wish": wish, "entities": entities, "previous": previous, "refine": refine,
            }, timeout=300)

            removed: list[str] = []
            config = clean_config(result["config"], {e["id"] for e in entities}, removed)
            config.setdefault("title", DASHBOARD_TITLE)

            await ensure_dashboard(ha)
            backup = write_backup(await current_config(ha))
            await ha.call("lovelace/config/save", url_path=DASHBOARD_PATH, config=config)
    except BackendError as e:
        return backend_failure(e)
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=500)

    views = config.get("views", [])
    cards = sum(
        len([c for s in v.get("sections", []) for c in s.get("cards", []) if c.get("type") != "heading"])
        + len(v.get("cards", []))
        for v in views
    )
    return web.json_response({
        "ok": True,
        "erklaerung": result.get("erklaerung", ""),
        "views": [v.get("title", "") for v in views],
        "cards": cards,
        "removed": sorted(set(removed)),
        "backup": backup,
        "dashboard_url": f"/{DASHBOARD_PATH}",
        "usage": result.get("usage", {}),
        "trial": trial_info(result["status"]),
    })


@routes.post("/api/undo")
async def undo(request):
    session = request.app["session"]
    files = sorted(BACKUP_DIR.glob("*.json")) if BACKUP_DIR.exists() else []
    if not files:
        return web.json_response({"ok": False, "error": "Kein Backup vorhanden."}, status=404)
    latest = files[-1]
    try:
        async with HAClient(session) as ha:
            await ha.call("lovelace/config/save", url_path=DASHBOARD_PATH,
                          config=json.loads(latest.read_text()))
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
    try:
        await ensure_registered(app["session"])
    except BackendError as e:
        print(f"Registrierung beim Haglo-Server noch nicht möglich: {e}")


async def on_cleanup(app):
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
