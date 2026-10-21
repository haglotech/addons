"""Haglo Assist – Home-Assistant-Add-on.

Liest die Geräte aus Home Assistant, lässt das Haglo-Backend daraus per KI ein
Dashboard bauen und speichert es in einem EIGENEN Dashboard ("haglo-ki").
Bestehende Dashboards des Kunden werden nie angefasst. Vor jedem Speichern wird
ein Backup angelegt, das per "Rückgängig" zurückgespielt werden kann.

Testphase, Zustimmung, Pläne und der KI-Aufruf liegen im Backend
(Standard https://api.haglotech.com). Im Add-on liegt kein API-Schlüssel.
"""

import asyncio
import ipaddress
import json
import os
import re
import secrets
import shutil
import time
from pathlib import Path
from urllib.parse import quote

import aiohttp
import warnings
from aiohttp import web

# aiohttp warnt bei jedem Zwischenspeichern im laufenden Server – harmlos, füllt aber das Protokoll
warnings.filterwarnings("ignore", message="Changing state of started or joined application is deprecated")

import local_ai
import voice

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

ADDON_VERSION = "0.28.0"
MAX_VERSIONS = 30                      # so viele Dashboard-Versionen bleiben lokal gespeichert
BAR_ENTITY = "input_text.haglo_assist"           # Eingabeleiste im Dashboard
BAR_STATUS = "input_text.haglo_assist_status"    # Rückmeldung darunter
DEFAULT_BACKEND_URL = "https://api.haglotech.com"
HUB_FILE = Path("/data/hub.json")
TERMS_VERSION = "2026-10-14"
# Nur als Rückfall, solange das Backend nicht erreichbar ist.
FALLBACK_URLS = {
    "terms_url": "https://haglotech.com/pages/haglo-assist-bedingungen",
    "privacy_url": "https://haglotech.com/pages/haglo-assist-datenschutz",
    "booking_url": "https://haglotech.com/pages/termin",
}


def load_options() -> dict:
    try:
        opts = json.loads(OPTIONS_FILE.read_text())
    except Exception:
        opts = {"backend_url": os.environ.get("HAGLO_BACKEND_URL", DEFAULT_BACKEND_URL)}
    try:
        # Nur in der Entwickler-Variante vorhanden: schaltet Funktionen zum Ausprobieren ein
        opts.update(json.loads(Path(__file__).with_name("dev_features.json").read_text()))
    except Exception:
        pass
    return opts


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


async def update_info(session) -> dict | None:
    """Neue Version der App verfügbar? → {version, current, notes, url}. Neuerungen aus dem Changelog dieser Version."""
    try:
        info = await supervisor_get(session, "/addons/self/info")
    except Exception:
        return None
    latest, current = info.get("version_latest"), info.get("version")
    if not info.get("update_available") or not latest or latest == current:
        return None
    notes = []
    try:
        async with session.get("http://supervisor/addons/self/changelog", headers={"Authorization": f"Bearer {TOKEN}"},
                               timeout=aiohttp.ClientTimeout(total=10)) as r:
            text = await r.text() if r.status == 200 else ""
        # Abschnitt der neuen Version: von „## <version>“ bis zur nächsten Überschrift
        m = re.search(rf"^##\s*{re.escape(latest)}\b.*?$(.*?)(?=^##\s|\Z)", text, re.M | re.S)
        for line in (m.group(1) if m else "").splitlines():
            line = line.strip()
            if line.startswith("- "):
                notes.append(line[2:])
            elif line and notes and not line.startswith("#"):
                notes[-1] += " " + line            # umbrochene Zeile gehört zum vorigen Punkt
    except Exception:
        pass
    return {"version": latest, "current": current, "notes": [n.replace("**", "") for n in notes][:6],
            "url": f"/hassio/addon/{info.get('slug') or 'self'}/info"}


def note_terms(status: dict | None) -> None:
    """Merkt sich, welcher Bedingungsversion zugestimmt wurde. Wer früher schon zugestimmt hat und der Haglo-Server
    nennt eine neuere Version, muss neu zustimmen (terms_update) – vorher geht nichts, auch nicht lokal."""
    if not status or not status.get("terms_version"):
        return
    prefs = load_prefs()
    before = dict(prefs)
    if status.get("accepted"):
        prefs["accepted_terms"] = status["terms_version"]
        prefs["terms_update"] = False
    elif prefs.get("accepted_terms"):
        prefs["terms_update"] = prefs["accepted_terms"] != status["terms_version"]
    if prefs != before:
        write_prefs(prefs)


PREFS_FILE = Path("/data/prefs.json")


def load_prefs() -> dict:
    """Kleine Einstellungen der App, lokal auf dem Gerät (z. B. weggeklickte Hinweise)."""
    try:
        return json.loads(PREFS_FILE.read_text())
    except Exception:
        return {}


MARKER_FILE = Path("/data/haglo_device.json")   # legt die Haglo-Einrichtung ab: {"sn": …, "mac": …}
WELCOME_ID = "haglo_assist_welcome"


def load_marker() -> dict:
    try:
        data = json.loads(MARKER_FILE.read_text())
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


HAGLO_MODELS = ("t630", "wyse 5070")   # Geräte, die Haglo ausliefert


def looks_like_haglo() -> bool:
    """Rein lokale Prüfung des Gerätemodells – für die Begrüßung, bevor irgendetwas übertragen wird.
    Ob Lite wirklich inklusive ist, entscheidet nach der Zustimmung der Server anhand der Seriennummer."""
    if load_prefs().get("preinstalled") or load_marker():
        return True
    try:
        model = Path("/sys/class/dmi/id/product_name").read_text().strip().lower()
    except OSError:
        return False
    return any(m in model for m in HAGLO_MODELS)


def write_prefs(prefs: dict) -> None:
    PREFS_FILE.parent.mkdir(parents=True, exist_ok=True)
    PREFS_FILE.write_text(json.dumps(prefs))


async def first_start_setup(session) -> str:
    """Einmalig nach der Installation. Wird wiederholt, bis alles geklappt hat (Home Assistant
    ist beim ersten Start des Geräts oft noch nicht bereit).

    Immer: in der Seitenleiste anzeigen, automatische Updates an.
    Nur auf vorinstallierten Haglo-Geräten (vor dem Onboarding installiert oder von der
    Einrichtung markiert): direkt unter „Übersicht“ einsortieren, als Startseite setzen, bis der
    Kunde losgelegt hat, und eine Willkommens-Nachricht anzeigen."""
    prefs = load_prefs()
    if prefs.get("first_start_done"):
        return "schon erledigt"
    head = {"Authorization": f"Bearer {TOKEN}"}
    try:
        async with session.post("http://supervisor/addons/self/options", headers=head,
                                json={"ingress_panel": True, "auto_update": True},
                                timeout=aiohttp.ClientTimeout(total=20)) as r:
            if r.status != 200:
                return f"HTTP {r.status}"
        if "preinstalled" not in prefs:
            async with session.get(f"{CORE_API}/onboarding", headers=head, timeout=aiohttp.ClientTimeout(total=15)) as r:
                # 404: Home Assistant ist längst eingerichtet – die Einrichtungs-Seite gibt es dann nicht mehr
                if r.status not in (200, 404):
                    return f"Home Assistant noch nicht bereit ({r.status})"
                steps = await r.json() if r.status == 200 else []
            fresh = any(x.get("step") == "user" and not x.get("done") for x in steps if isinstance(x, dict))
            prefs["preinstalled"] = bool(load_marker()) or fresh
            write_prefs(prefs)
        if prefs["preinstalled"]:
            await welcome_setup(session, prefs)
    except Exception as e:
        return f"{type(e).__name__}: {str(e)[:60]}"
    prefs["first_start_done"] = True
    write_prefs(prefs)
    return "ok (vorinstalliert)" if prefs["preinstalled"] else "ok"


async def welcome_setup(session, prefs: dict) -> None:
    """Haglo Assist nach vorn holen: Seitenleiste, Startseite, Willkommens-Nachricht."""
    slug = (await supervisor_get(session, "/addons/self/info")).get("slug")
    async with HAClient(session) as ha:
        panels = await ha.call("get_panels")
        if slug not in panels:
            raise RuntimeError("Eintrag in der Seitenleiste noch nicht da")
        if not ((await ha.call("frontend/get_system_data", key="sidebar")) or {}).get("value"):
            order = ["lovelace", slug] + [k for k in panels if k not in ("lovelace", slug)]
            await ha.call("frontend/set_system_data", key="sidebar", value={"panelOrder": order, "hiddenPanels": []})
        core_data = ((await ha.call("frontend/get_system_data", key="core")) or {}).get("value") or {}
        if core_data.get("default_panel") in (None, "", "home", "lovelace") and "default_panel_before" not in prefs:
            prefs["default_panel_before"] = core_data.get("default_panel") or ""
            prefs["default_panel_since"] = int(time.time())
            write_prefs(prefs)
            await ha.call("frontend/set_system_data", key="core", value={**core_data, "default_panel": slug})
        await ha.call("call_service", domain="persistent_notification", service="create", service_data={
            "notification_id": WELCOME_ID, "title": "Danke für deinen Kauf bei Haglo",
            "message": (f"Auf deinem Gerät ist **Haglo Assist** schon eingerichtet. Sag in einem Satz, was du brauchst – "
                        f"Haglo Assist baut Dashboards und Automationen und schaltet deine Geräte.\n\n"
                        f"[Haglo Assist öffnen](/{slug})")})


async def welcome_done(session) -> str:
    """Sobald der Kunde losgelegt hat (oder nach 14 Tagen): Startseite zurück auf die Übersicht."""
    prefs = load_prefs()
    if "default_panel_before" not in prefs:
        return "nichts zu tun"
    try:
        slug = (await supervisor_get(session, "/addons/self/info")).get("slug")
        async with HAClient(session) as ha:
            core_data = ((await ha.call("frontend/get_system_data", key="core")) or {}).get("value") or {}
            if core_data.get("default_panel") == slug:      # nur zurückstellen, wenn es noch unsere Einstellung ist
                value = {k: v for k, v in core_data.items() if k != "default_panel"}
                if prefs["default_panel_before"]:
                    value["default_panel"] = prefs["default_panel_before"]
                await ha.call("frontend/set_system_data", key="core", value=value)
            await ha.call("call_service", domain="persistent_notification", service="dismiss",
                          service_data={"notification_id": WELCOME_ID})
    except Exception as e:
        print(f"Startseite zurückstellen fehlgeschlagen: {e}")
        return f"FEHLER {type(e).__name__}: {str(e)[:80]}"
    prefs.pop("default_panel_before", None)
    prefs.pop("default_panel_since", None)
    write_prefs(prefs)
    return "ok"


# Neue Home-Assistant-Versionen (ab 2026.2) öffnen auf frischen Geräten das fest eingebaute „Home“ (/home).
# Das lässt sich nicht bearbeiten, und die alte Übersicht (/lovelace) gibt es dort nicht mehr als Startseite.
# Dort baut Haglo Assist die Startseite deshalb als eigenes Dashboard und stellt es als Startseite ein –
# sonst sähe der Kunde sein Ergebnis nie. „Rückgängig“ bis zum Ursprung stellt das wieder zurück.
START_PATH, START_TITLE = "mein-zuhause", "Mein Zuhause"
BUILTIN_START = (None, "", "home", "lovelace")


async def start_path(ha: "HAClient", create: bool = False) -> str | None:
    """url_path der Startseite, an der Haglo Assist arbeitet. None ist die klassische Übersicht."""
    listed = await ha.call("lovelace/dashboards/list")
    if not any(d.get("url_path") == START_PATH for d in listed):
        if ((await ha.call("get_panels")).get("lovelace") or {}).get("config"):
            return None
        if create:
            await ha.call("lovelace/dashboards/create", url_path=START_PATH, title=START_TITLE, icon="mdi:home-heart",
                          mode="storage", require_admin=False, show_in_sidebar=True)
    return START_PATH


async def show_start_page(session, ha: "HAClient", path: str | None) -> bool:
    """Macht die gebaute Seite zur Startseite, falls bisher das eingebaute „Home“ geöffnet wurde."""
    if path is None:
        return False                # klassische Übersicht: ist schon die Startseite
    prefs = load_prefs()
    core_data = ((await ha.call("frontend/get_system_data", key="core")) or {}).get("value") or {}
    current = core_data.get("default_panel")
    if "default_panel_before" in prefs:
        # Willkommens-Phase: Haglo Assist ist noch Startseite – danach geht es auf die gebaute Seite
        if prefs["default_panel_before"] in BUILTIN_START:
            prefs["start_before"] = prefs["default_panel_before"] or ""
            prefs["default_panel_before"] = path
            write_prefs(prefs)
        return False
    if current not in BUILTIN_START:
        return False
    prefs["start_before"] = current or ""
    write_prefs(prefs)
    await ha.call("frontend/set_system_data", key="core", value={**core_data, "default_panel": path})
    return True


async def reset_start_page(ha: "HAClient") -> None:
    """Übersicht ist wieder die automatische von Home Assistant: Startseite zurück auf den Stand davor."""
    prefs = load_prefs()
    if "start_before" not in prefs:
        return
    before = prefs.pop("start_before")
    if prefs.get("default_panel_before") == START_PATH:
        prefs["default_panel_before"] = before
    else:
        core_data = ((await ha.call("frontend/get_system_data", key="core")) or {}).get("value") or {}
        if core_data.get("default_panel") == START_PATH:    # nur zurückstellen, wenn es noch unsere Einstellung ist
            value = {k: v for k, v in core_data.items() if k != "default_panel"}
            if before:
                value["default_panel"] = before
            await ha.call("frontend/set_system_data", key="core", value=value)
    write_prefs(prefs)


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
    marker = load_marker()      # von der Haglo-Einrichtung hinterlegt: macht die Erkennung sicher
    serial = serial or str(marker.get("sn") or "")
    if marker.get("mac"):
        macs.add(str(marker["mac"]))
    if not serial and not macs:
        print("Hardware nicht lesbar:", "; ".join(notes))
    return {"serial": serial, "macs": sorted(macs), "note": "; ".join(notes)[:500] if not (serial and macs) else ""}


async def identify(app, status: dict) -> dict:
    """Meldet Seriennummer und MAC-Adressen ans Backend – erst nachdem der Nutzer zugestimmt
    hat, und nur einmal pro Start. Gibt den (ggf. neuen) Backend-Status zurück.

    Dass ein Gerät von Haglo stammt, weiß die App vorher schon aus dem Geräte-Zettel, den die
    Einrichtung lokal ablegt – dafür wird nichts übertragen."""
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
    await asyncio.sleep(6)   # warten, bis die App selbst erreichbar ist

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

    async def panel():
        prefs = load_prefs(); prefs.pop("first_start_done", None); PREFS_FILE.write_text(json.dumps(prefs))
        res = await first_start_setup(app["session"])
        info = await supervisor_get(app["session"], "/addons/self/info")
        return f"{res} panel={info.get('ingress_panel')} auto_update={info.get('auto_update')}"

    async def automation_api():
        base, head = "http://supervisor/core/api", {"Authorization": f"Bearer {TOKEN}"}
        aid = "haglo_selbsttest"
        cfg = {"alias": "Haglo Selbsttest (wird sofort gelöscht)", "description": "Test", "mode": "single",
               "triggers": [{"trigger": "time", "at": "03:33:00"}],
               "conditions": [{"condition": "template", "value_template": "{{ false }}"}],
               "actions": [{"action": "persistent_notification.create", "data": {"message": "Test"}}]}
        out = []
        async with app["session"].post(f"{base}/config/automation/config/{aid}", headers=head, json=cfg) as r:
            out.append(f"anlegen={r.status} {(await r.text())[:60]}")
        async with app["session"].get(f"{base}/config/automation/config/{aid}", headers=head) as r:
            got = await r.json() if r.status == 200 else {}
            out.append(f"lesen={r.status} keys={sorted(got)[:7]}")
        await asyncio.sleep(2)
        async with app["session"].get(f"{base}/states", headers=head) as r:
            states = await r.json()
            mine = [x for x in states if x["entity_id"].startswith("automation.") and x.get("attributes", {}).get("id") == aid]
            out.append(f"entity={mine[0]['entity_id'] if mine else None} automationen_gesamt={sum(1 for x in states if x['entity_id'].startswith('automation.'))}")
        async with app["session"].delete(f"{base}/config/automation/config/{aid}", headers=head) as r:
            out.append(f"loeschen={r.status}")
        return " ".join(out)

    async def automation_flow():
        """Ganzer Weg: KI schlägt vor -> anlegen -> auflisten -> löschen."""
        r = await build_dashboard(app, job["auto"], refine=False, dialog=[], can_ask=False, can_automate=True)
        if "automations" not in r:
            return f"kein Vorschlag (stattdessen {sorted(r)[:3]})"
        base = "http://127.0.0.1:8099"
        async with app["session"].post(f"{base}/api/automations/apply", json={"automations": r["automations"], "wish": "Selbsttest"}) as x:
            made = await x.json()
        if not made.get("ok"):
            return f"vorschlag ok, anlegen FEHLER: {str(made.get('error'))[:90]}"
        async with app["session"].get(f"{base}/api/automations") as x:
            listed = (await x.json()).get("automations", [])
        for c in made["created"]:
            async with app["session"].post(f"{base}/api/automations/delete", json={"id": c["id"]}) as x:
                gone = (await x.json()).get("ok")
        return f"vorschlag ok ({len(r['automations'])}), angelegt={len(made['created'])}, in liste={len(listed)}, an={listed[0]['on'] if listed else None}, geloescht={gone}, verlauf={len(load_history())}"

    async def everything():
        """Skript, Szene, Helfer anlegen; den Helfer schalten; eine Frage mit Zuständen; alles löschen."""
        base, out, made_ids = "http://127.0.0.1:8099", [], []

        async def post(path, body):
            async with app["session"].post(base + path, json=body) as x:
                return await x.json()

        items = [
            {"art": "helfer", "typ": "input_boolean", "alias": "Haglo Selbsttest Schalter", "einstellungen": {"icon": "mdi:test-tube"}},
            {"art": "skript", "alias": "Haglo Selbsttest Skript", "sequence": [{"delay": "00:00:01"}]},
        ]
        r = await post("/api/automations/apply", {"automations": items, "wish": "Selbsttest"})
        out.append(f"helfer+skript={'ok' if r.get('ok') else str(r.get('error'))[:70]}")
        made_ids += [c["id"] for c in r.get("created", [])]
        await asyncio.sleep(3)
        states = {x["entity_id"]: x for x in await all_states(app["session"])}
        helper = next((e for e in states if e.startswith("input_boolean.haglo_selbsttest")), None)
        if helper:
            r = await post("/api/automations/apply", {"automations": [{"art": "szene", "alias": "Haglo Selbsttest Szene", "entities": {helper: "on"}}]})
            out.append(f"szene={'ok' if r.get('ok') else str(r.get('error'))[:70]}")
            made_ids += [c["id"] for c in r.get("created", [])]
            r = await build_dashboard(app, "Schalte den Haglo Selbsttest Schalter jetzt ein", refine=False, dialog=[], can_ask=False, can_automate=True)
            await asyncio.sleep(2)
            now_state = {x["entity_id"]: x for x in await all_states(app["session"])}.get(helper, {}).get("state")
            out.append(f"schalten={'ok' if 'done' in r else sorted(r)[:2]} zustand={now_state}")
            r = await build_dashboard(app, "Ist der Haglo Selbsttest Schalter gerade an oder aus?", refine=False, dialog=[], can_ask=False, can_automate=True)
            out.append(f"frage-ohne-zustimmung={'fragt nach' if 'need_consent' in r else sorted(r)[:2]}")
            r = await build_dashboard(app, "Ist der Haglo Selbsttest Schalter gerade an oder aus?", refine=False, dialog=[], can_ask=False, can_automate=True, allow_states=True)
            out.append(f"antwort={str(r.get('answer'))[:60]!r}")
        else:
            out.append("helfer-entity nicht gefunden")
        gone = [(await post("/api/automations/delete", {"id": i})).get("ok") for i in made_ids]
        out.append(f"geloescht={gone}")
        bad = await post("/api/actions/run", {"actions": [{"action": "homeassistant.restart", "target": {"entity_id": ["light.x"]}}]})
        out.append(f"verbotene-aktion={'abgelehnt' if not bad.get('ok') else 'AUSGEFUEHRT!'}")
        return " ".join(out)

    async def probe():
        """Nur lesen: Wie heißt unser Eintrag in der Seitenleiste, und lässt sich die Startseite vorgeben?"""
        out = [f"config-ordner={HA_CONFIG.exists()} configuration.yaml={(HA_CONFIG / 'configuration.yaml').exists()}"]
        async with HAClient(app["session"]) as ha:
            panels = await ha.call("get_panels")
            mine = [k for k, v in panels.items() if "haglo" in k.lower() or "haglo" in str(v.get("title", "")).lower()]
            out.append(f"panels={mine} lovelace-default={[k for k, v in panels.items() if v.get('component_name') == 'lovelace'][:4]}")
            for cmd, kw in (("frontend/get_system_data", {"key": "core"}), ("frontend/get_user_data", {"key": "core"}),
                            ("frontend/get_user_data", {"key": "sidebar"}), ("frontend/get_system_data", {"key": "sidebar"})):
                try:
                    out.append(f"{cmd.split('/')[1]}[{kw['key']}]={json.dumps(await ha.call(cmd, **kw))[:110]}")
                except Exception as e:
                    out.append(f"{cmd.split('/')[1]}[{kw['key']}]=FEHLER {str(e)[:50]}")
        info = await supervisor_get(app["session"], "/addons/self/info")
        out.append(f"slug={info.get('slug')} ingress_url={info.get('ingress_url')} panel={info.get('ingress_panel')}")
        out.append(f"version={info.get('version')} config-ordner={HA_CONFIG.exists()} configuration.yaml={(HA_CONFIG / 'configuration.yaml').exists()}")
        return " ".join(out)

    async def sidebar_test():
        """Setzt die Seitenleisten-Reihenfolge für alle Benutzer ohne eigene Einstellung und liest sie zurück."""
        slug = (await supervisor_get(app["session"], "/addons/self/info")).get("slug")
        async with HAClient(app["session"]) as ha:
            panels = await ha.call("get_panels")
            order = ["lovelace", slug] + [k for k in panels if k not in ("lovelace", slug)]
            await ha.call("frontend/set_system_data", key="sidebar", value={"panelOrder": order, "hiddenPanels": []})
            back = await ha.call("frontend/get_system_data", key="sidebar")
            core_before = await ha.call("frontend/get_system_data", key="core")
            await ha.call("frontend/set_system_data", key="core", value=(core_before or {}).get("value") or {"default_panel": "lovelace"})
        return f"gesetzt={str(back)[:160]} core-schreiben=ok"

    async def welcome_test():
        prefs = load_prefs(); before = dict(prefs)
        prefs.pop("default_panel_before", None)
        await welcome_setup(app["session"], prefs)
        async with HAClient(app["session"]) as ha:
            mid = ((await ha.call("frontend/get_system_data", key="core")) or {}).get("value")
            notes = [n.get("notification_id") for n in await ha.call("persistent_notification/get")]
        done = await welcome_done(app["session"])
        async with HAClient(app["session"]) as ha:
            # Sicherheitsnetz für den Test: Startseite in jedem Fall wieder auf die Übersicht
            cur = ((await ha.call("frontend/get_system_data", key="core")) or {}).get("value") or {}
            if cur.get("default_panel") != "lovelace":
                await ha.call("frontend/set_system_data", key="core", value={**cur, "default_panel": "lovelace"})
            try:
                await ha.call("call_service", domain="persistent_notification", service="dismiss", service_data={"notification_id": WELCOME_ID})
            except Exception as e:
                done += f" / dismiss: {e}"
            after = ((await ha.call("frontend/get_system_data", key="core")) or {}).get("value")
            notes2 = [n.get("notification_id") for n in await ha.call("persistent_notification/get")]
        write_prefs(before)
        return f"zurueckstellen={done} | waehrend={mid} nachricht={WELCOME_ID in notes} danach={after} nachricht-weg={WELCOME_ID not in notes2} vorher-prefs={sorted(before)}"

    async def undo_test():
        """Dashboard ändern, den Eintrag im Verlauf rückgängig machen, prüfen, dass der Stand davor zurück ist."""
        base = "http://127.0.0.1:8099"
        async with HAClient(app["session"]) as ha:
            before = await current_config(ha)
        await build_dashboard(app, job["undo"], refine=before is not None, dialog=[], can_ask=False)
        async with HAClient(app["session"]) as ha:
            changed = await current_config(ha)
        async with app["session"].get(f"{base}/api/history") as x:
            last = (await x.json())["history"][-1]
        async with app["session"].post(f"{base}/api/history/undo", json={"id": last["id"]}) as x:
            res = await x.json()
        async with HAClient(app["session"]) as ha:
            after = await current_config(ha)
        async with app["session"].get(f"{base}/api/history") as x:
            last2 = (await x.json())["history"][-1]
        return (f"geaendert={changed != before} eintrag={last['kind']}/kann-rueckgaengig={last['can_undo']} undo={res.get('ok') or res.get('error')} "
                f"wieder-wie-vorher={after == before} markiert={last2.get('undone')}")

    async def dash_list():
        async with HAClient(app["session"]) as ha:
            items = await list_dashboards(ha)
        return " ; ".join(f"{d['label']}[{'auto' if d['auto'] else str(d['pages']) + ' Seiten'}{'' if d['editable'] else ',yaml'}{'' if d['allowed'] else ',fragt'}]" for d in items)

    async def http_test():
        """Genau der Weg der Oberfläche: Wunsch an /api/generate, gemeldet werden Status und Art der Antwort."""
        async with app["session"].post("http://127.0.0.1:8099/api/generate", json={"wish": job["http"], "dialog": []}) as x:
            text = await x.text()
        try:
            data = json.loads(text)
            return f"status={x.status} json keys={sorted(data)[:6]} error={str(data.get('error'))[:80]}"
        except ValueError:
            return f"status={x.status} KEIN JSON: {text[:300]!r}"

    if job.get("http"):
        await step("oberflaeche", http_test())
    elif job.get("dashlist"):
        await step("auswahl", dash_list())
    elif job.get("undo"):
        await step("rueckgaengig-im-faden", undo_test())
    elif job.get("welcome"):
        await step("willkommen", welcome_test())
    elif job.get("sidebar"):
        await step("seitenleiste-reihenfolge", sidebar_test())
    elif job.get("probe"):
        await step("probe", probe())
    elif job.get("selbst_update"):
        async def self_update():
            """Nur Entwickler-Variante: Supervisor die geänderte config.yaml einlesen lassen und sich selbst aktualisieren."""
            res = []
            for path in ("/store/reload", "/addons/self/update", "/addons/self/rebuild"):
                async with app["session"].post(f"http://supervisor{path}", headers={"Authorization": f"Bearer {TOKEN}"},
                                               timeout=aiohttp.ClientTimeout(total=120)) as r:
                    res.append(f"{path}={r.status}:{(await r.text())[:60]}")
            return " ".join(res)
        await step("selbst-update", self_update())
    elif job.get("full"):
        await step("alles", everything())
    elif job.get("auto"):
        await step("automation-ablauf", automation_flow())
    else:
        await step("automation", automation_api())
    if not any(job.get(k) for k in ("full", "probe", "sidebar", "welcome", "undo", "dashlist", "selbst_update")):
        await step("seitenleiste", panel())
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
        "terms_version": status.get("terms_version"),
        "trial_offer_days": status.get("trial_offer_days", 0),
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
HA_RESTARTING = "Home Assistant startet gerade neu oder ist nicht erreichbar. Bitte versuche es in einer Minute noch einmal."


class HAClient:
    """Minimaler WebSocket-Client für die Home-Assistant-API (über Supervisor)."""

    def __init__(self, session: aiohttp.ClientSession):
        self.session = session
        self.ws = None
        self.msg_id = 0

    async def __aenter__(self):
        try:
            self.ws = await self.session.ws_connect(HA_WS_URL, heartbeat=30)
            msg = await self.ws.receive_json()  # auth_required
        except (aiohttp.ClientError, TypeError, ValueError):
            # während eines Neustarts nimmt Home Assistant keine Verbindung an oder schließt sie sofort
            raise RuntimeError(HA_RESTARTING)
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
            try:
                msg = await self.ws.receive_json()
            except (TypeError, ValueError):
                # Verbindung geschlossen (z. B. Home Assistant startet neu) – statt "message 257 is not TEXT"
                raise RuntimeError(HA_RESTARTING)
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
            # aus welcher Integration die Entität stammt – damit die KI "ist das doppelt?" beantworten kann
            "integration": str(r.get("platform") or "")[:40],
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


async def create_sidebar_dashboard(ha: "HAClient", wish: str, result: dict) -> dict:
    """Legt ein neues Dashboard mit eigenem Eintrag in der Seitenleiste an und füllt es."""
    new = result["neues_dashboard"]
    title = new["titel"]
    base = "haglo-" + (re.sub(r"[^a-z0-9]+", "-", title.lower().replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")).strip("-") or "dashboard")
    taken = {d.get("url_path") for d in await ha.call("lovelace/dashboards/list")} | set((await ha.call("get_panels")).keys())
    url_path, n = base[:40], 2
    while url_path in taken:
        url_path, n = f"{base[:36]}-{n}", n + 1
    removed: list[str] = []
    config = clean_config(result["config"], {s["entity_id"] for s in await ha.call("get_states")}, removed)
    if not config.get("views"):
        raise RuntimeError("Das Ergebnis war leer. Es wurde nichts angelegt.")
    config.setdefault("title", title)
    item = await ha.call("lovelace/dashboards/create", url_path=url_path, title=title, icon=new.get("icon") or "mdi:view-dashboard",
                         mode="storage", require_admin=False, show_in_sidebar=True)
    try:
        await ha.call("lovelace/config/save", url_path=url_path, config=config)
    except Exception:
        await ha.call("lovelace/dashboards/delete", dashboard_id=item["id"])   # nichts Halbes stehen lassen
        raise
    # von Haglo Assist gebaut: darf künftig ohne Rückfrage weiterbearbeitet werden
    prefs = load_prefs()
    prefs["built"] = sorted(set(prefs.get("built", [])) | {url_path})
    write_prefs(prefs)
    ALLOWED_FILE.parent.mkdir(parents=True, exist_ok=True)
    ALLOWED_FILE.write_text(json.dumps(sorted(allowed_dashboards() | {url_path})))
    text = (result.get("erklaerung", "") + f" Du findest es links in der Seitenleiste unter „{title}“.").strip()
    add_history("dashboard", wish, text, url=f"/{url_path}", target=title, created=True,
                undo={"type": "new_dashboard", "id": item["id"], "url_path": url_path})
    return {"erklaerung": text, "config": config, "removed": sorted(set(removed)), "backup": None,
            "usage": result.get("usage", {}), "status": result["status"], "dashboard_url": f"/{url_path}"}


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


def allowed_dashboards() -> set[str]:
    """Dashboards, deren Bearbeitung (und Übertragung) der Nutzer ausdrücklich bestätigt hat."""
    try:
        return set(json.loads(ALLOWED_FILE.read_text()))
    except Exception:
        return set()


def is_auto(config) -> bool:
    """Von Home Assistant automatisch erzeugt (oder leer): Es gibt nichts Eigenes zu erhalten."""
    return config is None or "strategy" in config or not config.get("views")


async def list_dashboards(ha: HAClient) -> list[dict]:
    """Alle Dashboards zur Auswahl. id ist der url_path; "" ist die Startseite („Übersicht“).

    Reihenfolge: Startseite, weitere Dashboards des Nutzers, zuletzt das separate Haglo Dashboard.
    allowed = darf ohne Rückfrage bearbeitet werden (eigenes, automatisch erzeugtes oder freigegebenes)."""
    allowed = allowed_dashboards()
    listed = list(await ha.call("lovelace/dashboards/list"))
    # Neuere Home-Assistant-Versionen führen die Startseite selbst als „lovelace“ in der Liste – nicht doppelt zeigen
    items = [{"url_path": "", "title": "Übersicht", "mode": "storage"}] \
        + [d for d in listed if d.get("url_path") not in (DASHBOARD_PATH, START_PATH, "lovelace", "", None)]
    items.append(next((d for d in listed if d.get("url_path") == DASHBOARD_PATH),
                      {"url_path": DASHBOARD_PATH, "title": DASHBOARD_TITLE, "mode": "storage"}))
    out, seen, start = [], {}, await start_path(ha)
    for d in items:
        path = d.get("url_path") or ""
        editable = d.get("mode", "storage") == "storage"
        try:
            cfg = await current_config(ha, path or start) if editable else None
        except Exception:
            cfg = None
        own, auto = path == DASHBOARD_PATH, is_auto(cfg)
        title = d.get("title") or path
        seen[title] = seen.get(title, 0) + 1
        out.append({"id": path, "title": title, "own": own, "start": path == "", "editable": editable, "auto": auto,
                    "pages": 0 if auto else len(cfg.get("views", [])), "allowed": own or auto or path in allowed})
    # Verständliche, eindeutige Namen
    for d in out:
        if d["start"]:
            d["label"] = "Startseite"
        elif d["own"]:
            d["label"] = "Separates Haglo Dashboard"
        else:
            d["label"] = d["title"] + (f" (/{d['id']})" if seen[d["title"]] > 1 or d["title"] == "Übersicht" else "")
    return out


REBUILD_WORDS = re.compile(r"komplett neu|von grund auf|ganz neu|neu aufbauen|neu bauen|von vorne|von vorn\b", re.I)


async def pick_dashboard(ha: HAClient, wish: str) -> tuple[str, str]:
    """Woran gearbeitet wird, ohne dass der Nutzer etwas auswählen muss: immer die Startseite –
    außer er nennt im Wunsch ausdrücklich ein anderes Dashboard beim Namen
    („im Dashboard Technik …“, „auf dem Haglo Dashboard …“). Gibt (url_path, Anzeigename) zurück."""
    text = wish.lower()
    try:
        listed = list(await ha.call("lovelace/dashboards/list"))
    except Exception:
        listed = []
    names = [(d.get("url_path") or "", d.get("title") or "") for d in listed
             if d.get("url_path") not in ("lovelace", START_PATH, "", None) and d.get("mode", "storage") == "storage"]
    if not any(p == DASHBOARD_PATH for p, _ in names):
        names.append((DASHBOARD_PATH, DASHBOARD_TITLE))
    for path, title in sorted(names, key=lambda n: -len(n[1])):
        t = re.escape(title.lower().replace(" dashboard", "").strip())
        if not t or t in ("übersicht", "startseite"):
            continue
        # nur wenn klar ein Dashboard gemeint ist – „zeig eine Karte“ meint nicht das Dashboard „Karte“
        if re.search(rf"dashboard\s+[„\"']?{t}\b|\b{t}[\s-]+dashboard|\b(im|in|auf|ins|zum)\s+(dem\s+|der\s+|das\s+)?[„\"']?{t}[“\"']?\s+(dashboard|reiter|seite)", text):
            return path, title
    return "", "Startseite"


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
    """Sichert den bisherigen Stand des Dashboards lokal auf dem Gerät, bevor er überschrieben wird.
    config=None heißt: Das Dashboard wurde bisher von Home Assistant automatisch erzeugt."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    name = time.strftime("%Y%m%d-%H%M%S") + ".json"
    (BACKUP_DIR / name).write_text(json.dumps(
        {"haglo_version": 1, "saved_at": int(time.time()), "label": label[:200], "dashboard": dashboard,
         "config": config or {}, "auto": config is None},
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
            {"type": "heading", "heading": "Haglo Assist", "icon": "mdi:head-lightbulb-outline"},
            {"type": "entities", "entities": [{"entity": BAR_ENTITY, "name": "Was soll ich ändern?"}]},
            {"type": "markdown", "content": "{{ states('" + BAR_STATUS + "') }}"},
        ]}] + [s for s in views[0].get("sections", []) if not is_bar(s)]
    return config


# ---------------------------------------------------------------- Hinweis-Karte auf Haglo-Geräten
CARD_MARK = "<!-- haglo-assist-karte -->"


def has_haglo_card(config: dict | None) -> bool:
    return CARD_MARK in json.dumps(config or {}, ensure_ascii=False)


def add_haglo_card(config: dict, slug: str, until: int | None) -> dict:
    """Schmale Karte ganz oben: Lite ist beim Gerät dabei, mit Knopf zu Haglo Assist. Der Kunde kann sie
    wie jede Karte löschen – dann kommt sie nicht wieder."""
    views = config.get("views") or []
    if not views or has_haglo_card(config):
        return config
    bis = f" – mindestens bis {time.strftime('%d.%m.%Y', time.localtime(until))}" if until else " – mindestens 12 Monate"
    card = {"type": "markdown", "content": (
        f"{CARD_MARK}\n**Haglo Assist** · Lite ist bei deinem Gerät inklusive{bis}. Kein Abo.\n\n"
        f"Sag in einem Satz, was du brauchst: Dashboards, Automationen, Schalten.\n\n[Haglo Assist öffnen](/{slug})")}
    if views[0].get("type") == "sections":
        views[0]["sections"] = [{"type": "grid", "cards": [card]}] + list(views[0].get("sections") or [])
    else:
        views[0]["cards"] = [card] + list(views[0].get("cards") or [])
    return config


async def ensure_bar_helpers(ha: HAClient):
    have = {i.get("id") for i in await ha.call("input_text/list")}
    if "haglo_assist" not in have:
        await ha.call("input_text/create", name="Haglo Assist", icon="mdi:head-lightbulb-outline", max=255, mode="text")
    if "haglo_assist_status" not in have:
        await ha.call("input_text/create", name="Haglo Assist Status", icon="mdi:head-lightbulb-outline", max=255, mode="text")


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


# ---------------------------------------------------------------- Verlauf (nur lokal)
HISTORY_FILE = Path("/data/history.json")
MAX_HISTORY = 30


def load_history() -> list:
    try:
        return json.loads(HISTORY_FILE.read_text())
    except Exception:
        return []


def save_history(items: list) -> None:
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    HISTORY_FILE.write_text(json.dumps(items[-MAX_HISTORY:], ensure_ascii=False))


ROUTE: dict | None = None      # Weg der gerade laufenden Anfrage (für die Anzeige im Verlauf)

# Woran Auto erkennt, dass ein Wunsch Claude Präzise braucht
COMPLEX = re.compile(
    r"dashboard|übersicht|seite|reiter|karte|kachel|layout|automati|(wenn|falls) .+ (dann|soll|sollen)|immer wenn|sobald|"
    r"jeden (tag|morgen|abend)|am meisten|welche geräte|"
    r"zeitplan|um \d{1,2}(:\d\d)? ?uhr|warum|wieso|weshalb|erklär|fehler|repar|kaputt|geht nicht|funktioniert nicht|"
    r"einricht|integration|hinzufüg|verbind|update|aktualisier|umbenenn|benenn|lösch|loesch|entfern|kennung|aufräum|optimier|vorschl|plan|analys|"
    r"verbrauch der letzten|verlauf|statistik|vergleich|yaml|konfigur|skript (bau|erstell|schreib)|bau|erstell", re.I)


def is_complex(wish: str) -> bool:
    return len(wish) > 120 or len(wish.split()) > 18 or bool(COMPLEX.search(wish))


def add_history(kind: str, wish: str, text: str, **extra) -> str:
    """Merkt sich, was gemacht wurde – das ist der Gesprächsverlauf in der Oberfläche. Bleibt auf dem
    Gerät. `undo` beschreibt, wie sich der Eintrag zurücknehmen lässt:
    {"type": "version", "id": <Datei>} oder {"type": "objects", "items": [{"id", "backup"}]}."""
    items = load_history()
    hid = f"{int(time.time() * 1000)}"
    if ROUTE and "local" not in extra:
        extra = {**extra, "route": ROUTE}      # über welchen Weg (Claude Schnell/Präzise, Auto) beantwortet
    items.append({"id": hid, "ts": int(time.time()), "kind": kind, "wish": wish[:300], "text": text[:6000],
                  **{k: v for k, v in extra.items() if v is not None}})
    save_history(items)
    return hid


def can_undo(entry: dict, reg: dict) -> bool:
    undo = entry.get("undo")
    if not undo or entry.get("undone"):
        return False
    if undo["type"] == "version":
        return (BACKUP_DIR / str(undo["id"])).exists()
    if undo["type"] == "new_dashboard":
        return True
    if undo["type"] == "timer":
        return undo["id"] in load_timers()
    if undo["type"] == "yaml_file":
        return any((HA_CONFIG / i["file"]).exists() for i in undo.get("items", []))
    return any((i.get("backup") and (AUTO_BACKUP_DIR / i["backup"]).exists()) or i["id"] in reg for i in undo.get("items", []))


# ---------------------------------------------------------------- Automationen, Skripte, Szenen, Helfer
CORE_API = "http://supervisor/core/api"
AUTOMATIONS_FILE = Path("/data/automations.json")   # von Haglo Assist Angelegtes: {id: {art, alias, wann, was, created}}
AUTO_BACKUP_DIR = Path("/data/automation_backups")  # frühere Fassungen geänderter eigener Automationen des Kunden
KINDS = ["automation", "skript", "szene", "helfer"]
HELPER_TYPES = ("input_boolean", "input_number", "input_text", "input_select", "input_datetime", "timer", "counter")
CONFIG_PATH = {"automation": "automation", "skript": "script", "szene": "scene"}


def load_automations() -> dict:
    try:
        return json.loads(AUTOMATIONS_FILE.read_text())
    except Exception:
        return {}


def save_automations(reg: dict) -> None:
    AUTOMATIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    AUTOMATIONS_FILE.write_text(json.dumps(reg, ensure_ascii=False))


async def core(session, method: str, path: str, body=None, timeout: int = 30):
    """Aufruf der Home-Assistant-API. Gibt (Status, Antwort) zurück."""
    async with session.request(method, CORE_API + path, json=body, headers={"Authorization": f"Bearer {TOKEN}"},
                               timeout=aiohttp.ClientTimeout(total=timeout)) as r:
        try:
            data = await r.json()
        except Exception:
            data = {}
        return r.status, data


async def all_states(session) -> list[dict]:
    status, states = await core(session, "GET", "/states")
    if status != 200 or not isinstance(states, list):
        raise RuntimeError("Home Assistant antwortet nicht.")
    return [x for x in states if isinstance(x, dict) and "entity_id" in x]


OBJECT_FIELDS = {"automation": ("alias", "description", "mode", "triggers", "conditions", "actions"),
                 "skript": ("alias", "description", "mode", "sequence"),
                 "szene": ("name", "entities")}


async def own_objects(session, states: list[dict] | None = None) -> list[dict]:
    """Was die KI ändern darf: von Haglo Assist Angelegtes – und die eigenen Automationen des
    Kunden nur, wenn er das ausdrücklich erlaubt hat (dann werden sie dafür übertragen)."""
    reg, out, changed = load_automations(), [], False
    for oid, meta in list(reg.items()):
        art = meta.get("art", "automation")
        if art not in CONFIG_PATH:
            continue
        status, cfg = await core(session, "GET", f"/config/{CONFIG_PATH[art]}/config/{oid}")
        if status == 200 and isinstance(cfg, dict):
            item = {"id": oid, "art": art, **{k: cfg[k] for k in OBJECT_FIELDS[art] if k in cfg}}
            if art == "szene":
                item["alias"] = item.pop("name", meta.get("alias", ""))
            out.append(item)
        elif status == 404:
            del reg[oid]            # in Home Assistant gelöscht
            changed = True
    if changed:
        save_automations(reg)
    if load_prefs().get("foreign_automations") and states is not None:
        size = 0
        for st in states:
            aid = st.get("attributes", {}).get("id")
            if not st["entity_id"].startswith("automation.") or not aid or aid in reg:
                continue
            status, cfg = await core(session, "GET", f"/config/automation/config/{aid}")
            if status != 200 or not isinstance(cfg, dict):
                continue            # z. B. in YAML angelegt: nicht änderbar
            item = {"id": aid, "art": "automation", "eigene": True,
                    **{k: cfg[k] for k in OBJECT_FIELDS["automation"] if k in cfg}}
            size += len(json.dumps(item))
            if size > 120000 or len(out) >= 150:
                break
            out.append(item)
    return out


def entity_refs(node, found: set) -> set:
    """Alle entity_ids, die in einer Automation oder einem Skript vorkommen."""
    if isinstance(node, list):
        for n in node:
            entity_refs(n, found)
    elif isinstance(node, dict):
        for key, value in node.items():
            if key == "entity_id":
                found.update([value] if isinstance(value, str) else [v for v in value if isinstance(v, str)] if isinstance(value, list) else [])
            else:
                entity_refs(value, found)
    return found


# ---------------------------------------------------------------- Geräte schalten
# Nur Dienste dieser Bereiche darf die KI auslösen – nichts, was Home Assistant selbst betrifft.
CONTROL_DOMAINS = {
    "light", "switch", "climate", "cover", "fan", "media_player", "scene", "script", "vacuum", "humidifier",
    "water_heater", "lock", "alarm_control_panel", "input_boolean", "input_number", "input_select", "input_text",
    "input_datetime", "input_button", "button", "number", "select", "timer", "counter", "automation", "siren",
    "valve", "lawn_mower", "remote",
}
RISKY_COVERS = {"garage", "door", "gate"}


def risky_action(action: dict, states: dict) -> bool:
    """Schlösser, Alarmanlagen sowie Tore und Türen: nur nach ausdrücklicher Bestätigung."""
    domain, service = action["action"].split(".", 1)
    if domain in ("lock", "alarm_control_panel"):
        return True
    if domain == "cover" and "close" not in service and "stop" not in service:
        return any(states.get(e, {}).get("attributes", {}).get("device_class") in RISKY_COVERS
                   for e in action["target"]["entity_id"])
    return False


def check_actions(actions: list, states: dict) -> str | None:
    """Fehlertext, wenn eine Aktion nicht erlaubt ist oder ein Gerät nicht existiert."""
    for a in actions:
        if not isinstance(a, dict) or not isinstance(a.get("action"), str) or a["action"].count(".") != 1:
            return "Ungültige Aktion."
        domain = a["action"].split(".", 1)[0]
        if domain not in CONTROL_DOMAINS:
            return f"„{a['action']}“ darf Haglo Assist nicht ausführen."
        ids = (a.get("target") or {}).get("entity_id") or []
        if not isinstance(ids, list) or not ids:
            return "Bei einer Aktion fehlt das Gerät."
        unknown = [e for e in ids if e not in states]
        if unknown:
            return f"Dieses Gerät gibt es nicht: {unknown[0]}"
    return None


async def run_actions(session, actions: list) -> None:
    for a in actions:
        domain, service = a["action"].split(".", 1)
        body = {**(a.get("data") if isinstance(a.get("data"), dict) else {}), "entity_id": a["target"]["entity_id"]}
        status, data = await core(session, "POST", f"/services/{domain}/{service}", body)
        if status != 200:
            msg = (data.get("message") if isinstance(data, dict) else "") or f"Fehler {status}"
            raise RuntimeError(f"Home Assistant hat „{a['action']}“ abgelehnt: {msg[:160]}")


def state_text(st: dict) -> str:
    """Zustand eines Geräts als kurzer Text – nur das, wonach die KI gefragt hat."""
    attrs = st.get("attributes", {})
    text = str(st.get("state", ""))
    if attrs.get("unit_of_measurement"):
        text += f" {attrs['unit_of_measurement']}"
    extra = [f"{k}={attrs[k]}" for k in ("current_temperature", "temperature", "brightness", "current_position", "hvac_action", "media_title")
             if attrs.get(k) is not None]
    return (text + (" (" + ", ".join(extra) + ")" if extra else ""))[:120]


# ---------------------------------------------------------------- Neues Gerät verbinden
# Der Kunde nennt die Adresse eines Geräts im Heimnetz. Die App prüft es selbst (nichts davon geht
# an den Server) und verbindet es erst nach Bestätigung mit Home Assistant.
MY_ADDON = "https://my.home-assistant.io/redirect/supervisor_addon/?addon="


def device_host(address: str) -> str:
    """Nur Adressen im eigenen Heimnetz: private IPv4-Adresse, ohne Port und Pfad."""
    host = re.sub(r"^https?://", "", address.strip(), flags=re.I).split("/")[0].split(":")[0]
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        raise RuntimeError("Bitte nenne die IP-Adresse des Geräts, zum Beispiel 192.168.1.50.")
    if ip.version != 4 or not ip.is_private or ip.is_loopback or ip.is_link_local:
        raise RuntimeError("Ich verbinde nur Geräte in deinem Heimnetz (Adressen wie 192.168.x.x).")
    return str(ip)


async def device_get(session, host: str, path: str):
    async with session.get(f"http://{host}/{path}", timeout=aiohttp.ClientTimeout(total=6)) as r:
        if r.status != 200:
            return None
        try:
            return json.loads(await r.text())
        except ValueError:
            return None


async def probe_device(session, address: str) -> dict:
    """Erkennt, was unter der Adresse antwortet. Gibt nur aus, was für die Rückfrage nötig ist."""
    host = device_host(address)
    try:
        st = await device_get(session, host, "cm?cmnd=Status%200")
    except Exception:
        raise RuntimeError(f"Unter {host} antwortet kein Gerät. Ist es eingeschaltet und im selben Netz?")
    if isinstance(st, dict) and "Status" in st and "StatusFWR" in st:
        mqtt = st.get("StatusMQT") or {}
        return {"kind": "tasmota", "host": host, "label": "Tasmota",
                "name": str((st["Status"].get("DeviceName") or "Tasmota"))[:60],
                "version": str(st["StatusFWR"].get("Version", "")).split("(")[0][:20],
                "mac": str((st.get("StatusNET") or {}).get("Mac", "")).lower(),
                "connected": bool(mqtt.get("MqttHost")) and int(mqtt.get("MqttCount") or 0) > 0}
    if isinstance(st, dict) and "WARNING" in st:
        raise RuntimeError("Das Gerät ist mit einem Passwort geschützt. Bitte entferne das Web-Passwort in Tasmota oder richte es von Hand ein.")
    try:
        sh = await device_get(session, host, "shelly")
    except Exception:
        sh = None
    if isinstance(sh, dict) and ("type" in sh or "model" in sh or "app" in sh):
        return {"kind": "shelly", "host": host, "label": "Shelly", "name": str(sh.get("name") or sh.get("app") or sh.get("type") or "Shelly")[:60],
                "version": str(sh.get("ver") or sh.get("fw") or "")[:20], "mac": str(sh.get("mac", "")).lower(), "connected": False,
                "auth": bool(sh.get("auth") or sh.get("auth_en"))}
    raise RuntimeError(f"Unter {host} antwortet ein Gerät, das ich nicht selbst verbinden kann. "
                       "Bisher geht das mit Tasmota und Shelly. Sag mir die Marke, dann zeige ich dir den Weg.")


async def flow_finish(session, result: dict, answers: dict | None = None) -> tuple[bool, str]:
    """Führt einen Einrichtungsdialog von Home Assistant zu Ende, soweit er keine Eingaben verlangt."""
    for _ in range(4):
        kind = result.get("type")
        if kind == "create_entry":
            return True, ""
        if kind == "abort":
            ok = result.get("reason") in ("already_configured", "single_instance_allowed", "already_in_progress")
            return ok, str(result.get("reason") or "")
        if kind != "form" or not result.get("flow_id"):
            return False, str(result.get("message") or kind or "unbekannt")
        if result.get("errors"):
            return False, json.dumps(result["errors"], ensure_ascii=False)[:120]
        fields = {f.get("name") for f in result.get("data_schema") or []}
        body = {k: v for k, v in (answers or {}).items() if k in fields}
        required = {f.get("name") for f in result.get("data_schema") or [] if f.get("required") and "default" not in f}
        if required - set(body):
            return False, "braucht Angaben: " + ", ".join(sorted(required - set(body)))
        _, result = await core(session, "POST", f"/config/config_entries/flow/{result['flow_id']}", body)
    return False, "zu viele Schritte"


async def ensure_integration(session, domain: str, answers: dict | None = None) -> tuple[bool, str]:
    status, entries = await core(session, "GET", "/config/config_entries/entry")
    if status == 200 and any(e.get("domain") == domain for e in entries or []):
        return True, "vorhanden"
    # Hat Home Assistant die Integration schon entdeckt, wartet ein Dialog auf Bestätigung
    _, flows = await core(session, "GET", "/config/config_entries/flow")
    for f in flows if isinstance(flows, list) else []:
        if f.get("handler") == domain:
            _, step = await core(session, "GET", f"/config/config_entries/flow/{f['flow_id']}")
            done, why = await flow_finish(session, step if isinstance(step, dict) else {}, answers)
            if done:
                return True, "bestätigt"
    _, first = await core(session, "POST", "/config/config_entries/flow", {"handler": domain, "show_advanced_options": False})
    return await flow_finish(session, first if isinstance(first, dict) else {}, answers)


async def find_ha_device(session, mac: str, domain: str, tries: int = 15) -> list[str]:
    """Wartet, bis die Integration das Gerät angelegt hat, und gibt dessen Entitäten zurück.
    Dieselbe MAC-Adresse kann Home Assistant schon vom Router kennen (Anwesenheit im Netz) –
    das zählt nicht: Gewartet wird auf Entitäten der Integration selbst."""
    found: list[str] = []
    for _ in range(tries):
        async with HAClient(session) as ha:
            devices = await ha.call("config/device_registry/list")
            dev = next((d for d in devices or [] if any(c[0] == "mac" and c[1].lower() == mac for c in d.get("connections") or [])), None)
            if dev:
                ents = await ha.call("config/entity_registry/list")
                found = sorted(e["entity_id"] for e in ents or [] if e.get("device_id") == dev["id"] and e.get("platform") == domain)
                if found:
                    return found
        await asyncio.sleep(4)
    return found


async def system_messages(session) -> list[str]:
    """Offene Reparaturhinweise, verfügbare Updates und gestoppte Apps – als kurze Zeilen.
    Verlässt das Gerät nur nach Zustimmung des Nutzers. Keine Protokolle, keine Zugangsdaten."""
    lines: list[str] = []
    try:
        async with HAClient(session) as ha:
            issues = [i for i in (await ha.call("repairs/list_issues") or {}).get("issues", []) if not i.get("ignored")]
            titles = {}
            domains = sorted({i.get("domain") for i in issues if i.get("domain")})
            if domains:
                try:
                    res = (await ha.call("frontend/get_translations", language="de", category="issues", integration=domains)).get("resources", {})
                    titles = {k: v for k, v in res.items() if k.endswith(".title")}
                except Exception:
                    pass
            for i in issues[:15]:
                key = i.get("translation_key") or i.get("issue_id")
                title = titles.get(f"component.{i.get('domain')}.issues.{key}.title") or str(key).replace("_", " ")
                for k, v in (i.get("translation_placeholders") or {}).items():
                    title = title.replace("{" + k + "}", str(v))
                fix = ""
                if i.get("is_fixable"):
                    opts = await repair_options(session, i.get("domain"), i.get("issue_id"))
                    labels = (await issue_texts(session, i))[1] if opts else {}
                    fix = ", behebbar" + (" mit den Optionen " + ", ".join(f"{o} ({labels[o]})" if o in labels else o for o in opts) if opts else "")
                lines.append(f"Reparatur ({ {'error': 'Fehler', 'critical': 'kritisch'}.get(i.get('severity'), 'Hinweis') }, "
                             f"Integration {i.get('domain')}, seit {str(i.get('created') or '')[:10]}, "
                             f"Kennung domain={i.get('domain')} issue_id={i.get('issue_id')}{fix}): {title}"[:400])
    except Exception as e:
        lines.append(f"Reparaturen konnten nicht gelesen werden ({type(e).__name__}).")
    try:
        for st in await all_states(session):
            if st["entity_id"].startswith("update.") and st.get("state") == "on":
                a = st.get("attributes", {})
                lines.append(f"Update verfügbar: {a.get('friendly_name') or st['entity_id']} {a.get('installed_version')} → {a.get('latest_version')} "
                             f"(Kennung {st['entity_id']}{', installiert gerade' if a.get('in_progress') else ''}"
                             f"{'' if int(a.get('supported_features') or 0) & 1 else ', nur von Hand installierbar'})"[:260])
    except Exception:
        pass
    try:
        for a in (await supervisor_get(session, "/addons")).get("addons", []):
            if a.get("state") not in ("started", None):
                lines.append(f"App nicht gestartet: {a.get('name')} (Zustand: {a.get('state')})"[:200])
    except Exception:
        pass
    return lines[:40]


# ---------------------------------------------------------------- Fehlerprotokolle und Reparaturen
# Nach Zustimmung liest die App die aktuellen Fehler und Warnungen von Home Assistant (system_log),
# die Integrationen, die nicht laufen, und die Systemmeldungen. Bevor etwas das Gerät verlässt, werden
# Zugangsdaten geschwärzt. Reparieren kann die App nur wenige, harmlose Schritte – und erst nach Bestätigung.
REDACT = [
    # nur Werte hinter "Bearer " oder "passwort=" / "token: " – "Token expired" bleibt lesbar
    (re.compile(r"(?i)(bearer\s+|(?:access_token|token|api[_-]?key|apikey|password|passwort|passwd|secret|pin)[\"']?\s*[:=]\s*[\"']?)([^\s'\",;&]+)"), r"\1[geschwärzt]"),
    (re.compile(r"(?i)(://)[^/\s:@]+:[^/\s@]+@"), r"\1[Zugang]@"),
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "[E-Mail]"),
    (re.compile(r"\b(?=[A-Za-z0-9_\-]*\d)(?=[A-Za-z0-9_\-]*[A-Za-z])[A-Za-z0-9_\-]{28,}\b"), "[Schlüssel]"),
    (re.compile(r"\b(?!(?:10|127)\.)(?!192\.168\.)(?!172\.(?:1[6-9]|2\d|3[01])\.)(?!169\.254\.)\d{1,3}(?:\.\d{1,3}){3}\b"), "[öffentliche IP]"),
]


def redact(text: str) -> str:
    """Schwärzt Passwörter, Schlüssel, Zugangsdaten in Adressen, E-Mail-Adressen und öffentliche IP-Adressen."""
    for pat, sub in REDACT:
        text = pat.sub(sub, text)
    return text


async def log_lines(session) -> list[str]:
    """Fehler und Warnungen, nicht laufende Integrationen und Systemmeldungen – geschwärzt, als kurze Zeilen."""
    lines: list[str] = []
    try:
        status, entries = await core(session, "GET", "/config/config_entries/entry")
        for e in entries if status == 200 and isinstance(entries, list) else []:
            if e.get("disabled_by"):
                lines.append(f"Integration ausgeschaltet: {e.get('domain')} „{e.get('title')}“ (Eintrag {e.get('entry_id')}, von {e.get('disabled_by')})")
            elif e.get("state") not in ("loaded", None):
                why = f": {e.get('reason')}" if e.get("reason") else ""
                lines.append(f"Integration läuft nicht: {e.get('domain')} „{e.get('title')}“ (Eintrag {e.get('entry_id')}, Zustand {e.get('state')}{why})")
    except Exception as e:
        lines.append(f"Integrationen konnten nicht gelesen werden ({type(e).__name__}).")
    try:
        async with HAClient(session) as ha:
            logs = await ha.call("system_log/list") or []
        logs = [x for x in logs if x.get("level") in ("ERROR", "CRITICAL", "WARNING")]
        # Fehler vor Warnungen, dann die neuesten zuerst
        logs.sort(key=lambda x: (x.get("level") == "WARNING", -(x.get("timestamp") or 0)))
        for x in logs[:30]:
            when = time.strftime("%d.%m. %H:%M", time.localtime(x.get("timestamp") or 0))
            msg = " | ".join(str(m) for m in (x.get("message") or [])[:2])
            exc = str(x.get("exception") or "").strip().splitlines()
            tail = f" | {exc[-1]}" if exc else ""
            lines.append(f"[{x.get('level')}] {x.get('count', 1)}×, zuletzt {when}, {x.get('name')}: {msg}{tail}"[:600])
    except Exception as e:
        lines.append(f"Protokoll konnte nicht gelesen werden ({type(e).__name__}).")
    lines += await system_messages(session)
    return [redact(l) for l in lines][:60]


# ---------------------------------------------------------------- Reparaturen von Home Assistant und YAML
FIX = "/repairs/issues/fix"


async def issue_texts(session, issue: dict) -> tuple[str, dict]:
    """Verständlicher Titel eines Reparaturhinweises und die Beschriftungen seiner Lösungswege – aus den
    Übersetzungen von Home Assistant (z. B. „Netzwerkspeicher-Gerät ausgefallen“, „Neu laden“, „Entfernen“)."""
    domain, key = issue.get("domain"), issue.get("translation_key") or issue.get("issue_id")
    res = {}
    for lang in ("de", "en"):
        try:
            async with HAClient(session) as ha:
                res = (await ha.call("frontend/get_translations", language=lang, category="issues", integration=[domain])).get("resources", {})
        except Exception:
            res = {}
        if res.get(f"component.{domain}.issues.{key}.title"):
            break
    title = res.get(f"component.{domain}.issues.{key}.title") or str(key).replace("_", " ")
    for k, v in (issue.get("translation_placeholders") or {}).items():
        title = title.replace("{" + k + "}", str(v))
    prefix = f"component.{domain}.issues.{key}.fix_flow.step."
    labels = {k.rsplit(".", 1)[1]: v for k, v in res.items() if k.startswith(prefix) and ".menu_options." in k}
    return title, labels


async def repair_options(session, domain: str, issue_id: str) -> list[str]:
    """Welche Wahl die eingebaute Reparatur anbietet (z. B. Netzwerkspeicher neu verbinden / entfernen).
    Öffnet den Reparatur-Dialog nur zum Lesen und schließt ihn sofort wieder."""
    try:
        status, step = await core(session, "POST", FIX, {"handler": domain, "issue_id": issue_id})
    except Exception:
        return []
    step = step if isinstance(step, dict) else {}
    if step.get("flow_id"):
        await core(session, "DELETE", f"{FIX}/{step['flow_id']}")
    if status == 200 and step.get("type") == "menu":
        opts = step.get("menu_options") or []
        return list(opts.keys()) if isinstance(opts, dict) else [str(o) for o in opts]
    return []


async def repair_issue(session, domain: str, issue_id: str) -> dict | None:
    async with HAClient(session) as ha:
        issues = (await ha.call("repairs/list_issues") or {}).get("issues", [])
    return next((i for i in issues if i.get("domain") == domain and i.get("issue_id") == issue_id), None)


async def fix_post(session, fid: str, body: dict):
    """Einen Schritt der eingebauten Reparatur absenden. Manche Reparaturen dauern (z. B. Netzwerkspeicher neu
    verbinden, wenn er nicht antwortet) – dann nicht still hängen, sondern ehrlich sagen, dass es noch läuft."""
    try:
        return await core(session, "POST", f"{FIX}/{fid}", body, timeout=90)
    except asyncio.TimeoutError:
        raise RuntimeError("Home Assistant arbeitet nach 90 Sekunden noch daran – meist, weil das Gerät (z. B. der Netzwerkspeicher) "
                           "nicht antwortet. Schau in ein paar Minuten unter Reparaturen nach, ob der Hinweis weg ist.")


async def repair_fix(session, domain: str, issue_id: str, option: str = "") -> str:
    """Führt die eingebaute Reparatur aus: Menüpunkt wählen, Bestätigungen absenden. Fragt der Dialog nach
    Eingaben, die die App nicht kennt, wird er abgebrochen – dann ist nichts geändert."""
    status, step = await core(session, "POST", FIX, {"handler": domain, "issue_id": issue_id})
    step = step if isinstance(step, dict) else {}
    for _ in range(5):
        kind = step.get("type")
        if kind == "create_entry":
            return "erledigt"
        if kind == "abort":
            why = step.get("reason") or ""
            text = {"apply_suggestion_fail": "Home Assistant konnte das nicht erledigen. Ist das Gerät (z. B. der Netzwerkspeicher) "
                                             "weiterhin nicht erreichbar, klappt „Neu laden“ erst, wenn es wieder läuft – sonst „Entfernen“ wählen.",
                    "issue_not_found": "Den Hinweis gibt es nicht mehr – vermutlich hat er sich schon erledigt."}.get(why)
            return f"nicht geklappt: {text or why or 'ohne Angabe eines Grundes'}"
        if status != 200 or not step.get("flow_id"):
            raise RuntimeError("Die Reparatur ließ sich nicht starten.")
        fid = step["flow_id"]
        if kind == "menu":
            opts = step.get("menu_options") or []
            opts = list(opts.keys()) if isinstance(opts, dict) else [str(o) for o in opts]
            pick = option if option in opts else (opts[0] if len(opts) == 1 else "")
            if not pick:
                await core(session, "DELETE", f"{FIX}/{fid}")
                raise RuntimeError(f"Die Reparatur bietet mehrere Wege an ({', '.join(opts)}) – bitte sag, welcher.")
            status, step = await fix_post(session, fid, {"next_step_id": pick})
        elif kind == "form":
            # nur reine Bestätigungen: Felder ohne Vorgabe kann die App nicht ausfüllen
            missing = [f.get("name") for f in step.get("data_schema") or [] if f.get("required") and "default" not in f]
            if missing:
                await core(session, "DELETE", f"{FIX}/{fid}")
                raise RuntimeError("Die Reparatur braucht Angaben, die nur du machen kannst – bitte direkt unter Reparaturen erledigen.")
            status, step = await fix_post(session, fid, {})
        else:
            await core(session, "DELETE", f"{FIX}/{fid}")
            raise RuntimeError("Die Reparatur lief anders als erwartet. Es wurde nichts geändert.")
        step = step if isinstance(step, dict) else {}
    raise RuntimeError("Die Reparatur hat zu viele Schritte.")


# Konfigurationsdateien: Haglo Assist sieht /homeassistant (Zuordnung homeassistant_config). Gelesen wird nur nach
# Zustimmung, secrets.yaml nie; Zugangsdaten werden geschwärzt. Geändert wird nur ein exakt gefundener Ausschnitt,
# mit Sicherung in /data und Prüfung der Konfiguration – schlägt die fehl, wird der alte Stand zurückgeschrieben.
HA_CONFIG = Path("/homeassistant")
YAML_BACKUPS = Path("/data/yaml-backups")
YAML_SECRET = re.compile(r"^(\s*-?\s*[\w-]*(?:password|passwort|token|api_key|apikey|secret|client_id|pin)[\w-]*\s*:\s*)(?!!secret)(\S.*)$", re.I | re.M)


def yaml_path(name: str) -> Path | None:
    name = str(name).strip().lstrip("/")
    if not re.fullmatch(r"[\w\-./]+\.ya?ml", name) or ".." in name or name.split("/")[-1] == "secrets.yaml" \
            or any(part.startswith(".") for part in name.split("/")):
        return None
    p = (HA_CONFIG / name).resolve()
    return p if str(p).startswith(str(HA_CONFIG.resolve()) + "/") else None


def yaml_redact(text: str) -> str:
    return redact(YAML_SECRET.sub(r"\1[geschwärzt]", text))


async def read_yaml(names: list[str]) -> list[str]:
    """configuration.yaml, die dort eingebundenen Dateien und die gewünschten – geschwärzt, zusammen höchstens 40.000 Zeichen."""
    want = ["configuration.yaml"]
    main = yaml_path("configuration.yaml")
    if main and main.exists():
        want += re.findall(r"!include\s+([\w\-./]+\.ya?ml)", main.read_text(errors="replace"))
    want += [n for n in names if isinstance(n, str)]
    out, used, seen = [], 0, set()
    for name in want:
        p = yaml_path(name)
        if not p or p in seen:
            continue
        seen.add(p)
        if not p.exists():
            out.append(f"Datei {name}: gibt es nicht")
            continue
        text = yaml_redact(p.read_text(errors="replace"))
        if used + len(text) > 40000:
            out.append(f"Datei {name}: zu groß, nicht mitgeschickt")
            continue
        used += len(text)
        out.append(f"Datei {name}:\n{text}")
    return out


def yaml_check(step: dict) -> tuple[Path, str]:
    p = yaml_path(step.get("datei", ""))
    if not p or not p.exists():
        raise RuntimeError(f"Die Datei „{step.get('datei')}“ darf Haglo Assist nicht ändern oder es gibt sie nicht.")
    alt, neu = step.get("alt") or "", step.get("neu") or ""
    if "[geschwärzt]" in alt + neu or "[Schlüssel]" in alt + neu or "[E-Mail]" in alt + neu:
        raise RuntimeError("Die Änderung berührt geschwärzte Zugangsdaten – das macht Haglo Assist nicht.")
    text = p.read_text(errors="replace")
    if text.count(alt) != 1:
        raise RuntimeError(f"Die zu ändernde Stelle in „{step.get('datei')}“ habe ich nicht eindeutig gefunden. Es wurde nichts geändert.")
    return p, text


PACKAGE_FILE = re.compile(r"packages/haglo_[a-z0-9_]{1,40}\.yaml")
PACKAGES_LINE = "packages: !include_dir_named packages"


def packages_included(text: str) -> str | None:
    """configuration.yaml mit eingebundenem Ordner packages. None, wenn sie Pakete anders einbindet –
    dann fasst Haglo Assist sie nicht an."""
    if re.search(r"^\s+packages\s*:", text, re.M):
        return text if re.search(r"^\s+packages\s*:\s*!include_dir_(merge_)?named\s+packages\s*$", text, re.M) else None
    m = re.search(r"^homeassistant\s*:[ \t]*(#.*)?$", text, re.M)
    if m:
        nxt = re.search(r"^([ \t]+)\S", text[m.end():].lstrip("\n"), re.M)
        indent = nxt.group(1) if nxt else "  "
        return text[:m.end()] + f"\n{indent}{PACKAGES_LINE}" + text[m.end():]
    if re.search(r"^homeassistant\s*:", text, re.M):
        return None                 # z. B. "homeassistant: !include …"
    return text.rstrip("\n") + f"\n\nhomeassistant:\n  {PACKAGES_LINE}\n"


def package_check(step: dict) -> tuple[Path, str]:
    name, body = str(step.get("datei") or ""), str(step.get("inhalt") or "")
    if not PACKAGE_FILE.fullmatch(name):
        raise RuntimeError("Haglo Assist legt nur eigene Dateien im Ordner packages an. Es wurde nichts geändert.")
    if not body.strip() or "[geschwärzt]" in body or "[Schlüssel]" in body or "\t" in body:
        raise RuntimeError("Der Inhalt der Datei war unvollständig. Es wurde nichts geändert.")
    main = HA_CONFIG / "configuration.yaml"
    if not main.exists() or packages_included(main.read_text(errors="replace")) is None:
        raise RuntimeError("Deine configuration.yaml bindet Pakete auf eigene Art ein – das richte bitte von Hand ein. Es wurde nichts geändert.")
    return HA_CONFIG / name, body.rstrip() + "\n"


YAML_SKIP_DIRS = {".storage", "custom_components", "www", "blueprints", "deps", "tts", "backups", "haglo-local", "node_modules"}


def host_in_yaml(host: str, skip: set[str] = frozenset()) -> list[str]:
    """Wo eine Adresse schon in den YAML-Dateien steht (Datei und Zeile). secrets.yaml wird nie gelesen."""
    hits, pat = [], re.compile(rf"(?<![\d.]){re.escape(host)}(?![\d])")
    root = HA_CONFIG.resolve()
    for p in sorted(root.rglob("*.y*ml")):
        rel = p.relative_to(root).as_posix()
        if rel in skip or p.name == "secrets.yaml" or len(p.parts) - len(root.parts) > 3 \
                or any(part.startswith(".") or part in YAML_SKIP_DIRS for part in p.relative_to(root).parts[:-1]):
            continue
        try:
            for no, line in enumerate(p.read_text(errors="replace").splitlines(), 1):
                if pat.search(line) and not line.lstrip().startswith("#"):
                    hits.append(f"{rel} (Zeile {no})")
                    break
        except OSError:
            continue
    return hits


async def host_in_devices(session, host: str) -> str:
    """Name des Geräts, das in Home Assistant schon unter dieser Adresse eingerichtet ist (über eine Integration)."""
    try:
        async with HAClient(session) as ha:
            devices = await ha.call("config/device_registry/list") or []
    except Exception:
        return ""
    dev = next((d for d in devices if re.search(rf"//{re.escape(host)}(?:[:/]|$)", str(d.get("configuration_url") or ""))), None)
    return (dev.get("name_by_user") or dev.get("name") or "ohne Namen") if dev else ""


async def already_there(session, host: str, skip: set[str] = frozenset()) -> str:
    """Leer, wenn das Gerät noch nirgends eingebunden ist – sonst ein Satz, wo es schon steht."""
    files, dev = host_in_yaml(host, skip), await host_in_devices(session, host)
    parts = ([f"in {', '.join(files[:3])}"] if files else []) + ([f"als Gerät „{dev}“ über eine Integration"] if dev else [])
    return f"Das Gerät {host} ist schon eingebunden: {' und '.join(parts)}." if parts else ""


async def template_errors(session, body: str) -> str:
    """Die Konfigurationsprüfung von Home Assistant prüft Vorlagen ({{ … }}) nicht. Darum hier: jede Vorlage
    einmal von Home Assistant übersetzen lassen und nur echte Schreibfehler melden."""
    for m in re.finditer(r"^\s*-?\s*\w*template\s*:\s*(.+?)\s*$", body, re.M):
        tpl = m.group(1)
        if tpl[:1] in "\"'" and tpl[-1:] == tpl[:1]:
            tpl = tpl[1:-1].replace('\\"', '"') if tpl[0] == '"' else tpl[1:-1].replace("''", "'")
        if "{" not in tpl:
            continue
        status, res = await core(session, "POST", "/template", {"template": tpl, "variables": {"value_json": {}, "value": ""}})
        text = res if isinstance(res, str) else json.dumps(res, ensure_ascii=False)
        if status != 200 and re.search(r"TemplateSyntaxError|unexpected|expected token", text):
            return f"Schreibfehler in der Vorlage {tpl[:80]}"
    return ""


async def probe_lines(session, req: dict) -> list[str]:
    """Ruft eine Adresse im Heimnetz einmal ab (nur lesen, nur private IP-Adressen) – für Geräte ohne Integration."""
    host = device_host(str(req.get("adresse") or ""))
    path = str(req.get("pfad") or "").lstrip("/")
    if ".." in path or not re.fullmatch(r"[\w\-./?=&%]{0,120}", path):
        path = ""
    try:
        async with session.get(f"http://{host}/{path}", timeout=aiohttp.ClientTimeout(total=8), allow_redirects=False) as r:
            text = (await r.content.read(8000)).decode("utf-8", "replace")
            kind = r.headers.get("Content-Type", "")[:60]
            out = [f"Abruf von {host}/{path}: HTTP {r.status} ({kind})\n{redact(text)}"]
    except Exception as e:
        return [f"Abruf von {host}/{path}: keine Antwort ({type(e).__name__}). Ist das Gerät eingeschaltet und im selben Netz?"]
    there = await already_there(session, host)
    if there:
        out.append("Hinweis der App: " + there + " Nichts doppelt anlegen.")
    return out


async def entry_by_id(session, entry_id: str) -> dict | None:
    status, entries = await core(session, "GET", "/config/config_entries/entry")
    return next((e for e in entries if isinstance(e, dict) and e.get("entry_id") == entry_id), None) if isinstance(entries, list) else None


async def repair_plan(session, steps: list[dict]) -> list[dict]:
    """Prüft die vorgeschlagenen Schritte vor Ort und beschreibt sie für die Bestätigung (ändert nichts)."""
    out = []
    for st in steps[:15]:
        art = st.get("art")
        if art == "hinweis":
            continue
        if art == "home_assistant_neu_starten":
            out.append({"art": art, "title": "Home Assistant neu starten", "sub": "Die Konfiguration wird vorher geprüft. Dauert 1–3 Minuten."})
            continue
        if art in ("reparatur_ignorieren", "reparatur_beheben"):
            issue = await repair_issue(session, str(st.get("domain")), str(st.get("issue_id")))
            if not issue:
                raise RuntimeError("Diesen Reparaturhinweis gibt es nicht mehr. Es wurde nichts geändert.")
            name, labels = await issue_texts(session, issue)
            opt = str(st.get("option") or "")
            if art == "reparatur_ignorieren":
                title, sub = f"Hinweis ausblenden: {name}", "erscheint nicht mehr unter Reparaturen"
            else:
                how = labels.get(opt) or (opt.rsplit("_", 1)[-1].capitalize() if opt else "")
                title = f"{name} beheben" + (f": {how}" if how else "")
                sub = "mit der eingebauten Reparatur von Home Assistant"
            out.append({"art": art, "domain": issue["domain"], "issue_id": issue["issue_id"], "option": opt, "title": title, "sub": sub})
            continue
        if art == "update_installieren":
            eid = str(st.get("entity_id") or "")
            status, ent = await core(session, "GET", f"/states/{eid}") if eid.startswith("update.") else (404, {})
            if status != 200 or not isinstance(ent, dict) or ent.get("state") != "on":
                raise RuntimeError(f"Für „{eid}“ gibt es kein offenes Update. Es wurde nichts geändert.")
            a = ent.get("attributes", {})
            if not int(a.get("supported_features") or 0) & 1:           # UpdateEntityFeature.INSTALL fehlt
                out.append({"art": "hinweis", "title": f"Update: {a.get('friendly_name') or eid}",
                            "sub": "lässt sich nicht automatisch installieren – bitte unter Einstellungen → Updates"})
                continue
            backup = bool(int(a.get("supported_features") or 0) & 8)     # UpdateEntityFeature.BACKUP
            out.append({"art": art, "entity_id": eid, "backup": backup,
                        "title": f"Update: {a.get('friendly_name') or eid} {a.get('installed_version')} → {a.get('latest_version')}",
                        "sub": ("mit Sicherung vorher" if backup else "ohne eigene Sicherung (bietet dieses Update nicht an)")
                               + (" · Home Assistant startet danach neu" if eid.endswith("home_assistant_core_update") else "")
                               + (" · Haglo Assist startet danach neu" if "haglo" in eid else "")})
            continue
        if art == "yaml_aendern":
            yaml_check(st)
            alt, neu = str(st.get("alt") or "").strip(), str(st.get("neu") or "").strip()
            short = lambda t: (t.splitlines()[0][:70] + (" …" if len(t.splitlines()) > 1 or len(t) > 70 else "")) if t else "–"
            out.append({"art": art, "datei": st["datei"], "alt": st["alt"], "neu": st.get("neu") or "",
                        "title": f"{st['datei']} ändern" + (" (Eintrag entfernen)" if not neu else ""),
                        "sub": f"bisher: {short(alt)} → neu: {short(neu) if neu else 'entfernt'} · wird vorher gesichert und danach geprüft"})
            continue
        if art == "yaml_datei":
            p, body = package_check(st)
            # Doppelt angelegt wird nie: Steht eine Adresse aus der neuen Datei schon woanders, lehnt die App ab –
            # außer ein Schritt davor entfernt sie dort (yaml_aendern), oder es ist genau diese Datei (ersetzen).
            removed = {h for x in steps if x.get("art") == "yaml_aendern"
                       for h in re.findall(r"(?:\d{1,3}\.){3}\d{1,3}", str(x.get("alt") or "")) if h not in str(x.get("neu") or "")}
            for host in dict.fromkeys(re.findall(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d])", body)):
                if host in removed:
                    continue
                there = await already_there(session, host, {st["datei"]})
                if there:
                    raise RuntimeError(there + " Ein zweiter Eintrag würde das Gerät doppelt abfragen und Sensoren doppelt anlegen – "
                                       "deshalb lege ich nichts an. Sag „ersetze den alten Eintrag“, wenn ich ihn durch den neuen ersetzen soll.")
            lines = body.count("\n")
            out.append({"art": art, "datei": st["datei"], "inhalt": body, "code": body,
                        "title": f"{st['datei']} {'ersetzen' if p.exists() else 'anlegen'}",
                        "sub": f"{lines} Zeilen · wird danach geprüft, bei Fehlern nimmt Haglo Assist alles zurück"})
            continue
        e = await entry_by_id(session, str(st.get("eintrag") or ""))
        if not e or art not in ("integration_neu_laden", "integration_einschalten"):
            raise RuntimeError("Den Reparaturvorschlag konnte ich nicht zuordnen. Es wurde nichts geändert.")
        name = f"{e.get('title') or e.get('domain')} ({e.get('domain')})"
        out.append({"art": art, "eintrag": e["entry_id"], "domain": e.get("domain"),
                    "title": ("Integration neu laden: " if art == "integration_neu_laden" else "Integration einschalten: ") + name,
                    "sub": f"Zustand jetzt: {'ausgeschaltet' if e.get('disabled_by') else e.get('state')}"})
    return out


async def repair_apply(session, steps: list[dict]) -> list[dict]:
    """Führt die bestätigten Schritte aus. Gibt je Schritt eine Zeile für den Verlauf zurück."""
    done, updates = [], []
    for st in await repair_plan(session, steps):
        if st["art"] == "integration_neu_laden":
            status, res = await core(session, "POST", f"/config/config_entries/entry/{st['eintrag']}/reload")
            ok = status == 200
            sub = ("neu geladen" + (" – Home Assistant braucht danach einen Neustart" if isinstance(res, dict) and res.get("require_restart") else "")) if ok else f"fehlgeschlagen ({status})"
        elif st["art"] == "hinweis":
            ok, sub = True, st["sub"]
        elif st["art"] == "update_installieren":
            # Updates dauern Minuten: im Hintergrund nacheinander einspielen, die Oberfläche wartet nicht darauf
            updates.append(st)
            ok, sub = True, "wird installiert – den Fortschritt siehst du unter Einstellungen → Updates"
        elif st["art"] == "reparatur_ignorieren":
            async with HAClient(session) as ha:
                await ha.call("repairs/ignore_issue", domain=st["domain"], issue_id=st["issue_id"], ignore=True)
            ok, sub = True, "ausgeblendet"
        elif st["art"] == "reparatur_beheben":
            try:
                sub = await repair_fix(session, st["domain"], st["issue_id"], st.get("option", ""))
                ok = sub == "erledigt"
            except RuntimeError as e:
                ok, sub = False, str(e)
        elif st["art"] == "yaml_aendern":
            p, text = yaml_check(st)
            YAML_BACKUPS.mkdir(parents=True, exist_ok=True)
            backup = YAML_BACKUPS / f"{p.name}.{time.strftime('%Y%m%d-%H%M%S')}"
            backup.write_text(text)
            p.write_text(text.replace(st["alt"], st.get("neu") or "", 1))
            status, res = await core(session, "POST", "/config/core/check_config")
            if status == 200 and isinstance(res, dict) and res.get("result") == "valid":
                ok, sub = True, f"geändert, Konfiguration geprüft: in Ordnung (Sicherung: {backup.name})"
            else:
                p.write_text(text)       # alter Stand zurück
                errors = redact(str((res or {}).get("errors") or "unbekannter Fehler"))[:240] if isinstance(res, dict) else ""
                ok, sub = False, f"nicht übernommen – die Prüfung meldete Fehler, der alte Stand ist wiederhergestellt: {errors}"
        elif st["art"] == "yaml_datei":
            p, body = package_check(st)
            problem = await template_errors(session, body)
            if problem:
                done.append({"icon": "action", "title": st["title"], "url": None, "failed": True,
                             "sub": f"nicht angelegt – {problem}. Es wurde nichts geändert."})
                break
            main = HA_CONFIG / "configuration.yaml"
            main_old = main.read_text(errors="replace")
            main_new = packages_included(main_old)
            old = p.read_text(errors="replace") if p.exists() else None
            YAML_BACKUPS.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime('%Y%m%d-%H%M%S')
            if main_new != main_old:
                (YAML_BACKUPS / f"configuration.yaml.{stamp}").write_text(main_old)
                main.write_text(main_new)
            if old is not None:
                (YAML_BACKUPS / f"{p.name}.{stamp}").write_text(old)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(body)
            status, res = await core(session, "POST", "/config/core/check_config")
            if status == 200 and isinstance(res, dict) and res.get("result") == "valid":
                ok, sub = True, "angelegt, Konfiguration geprüft: in Ordnung"
                st["undo"] = {"type": "yaml_file", "file": st["datei"], "backup": f"{p.name}.{stamp}" if old is not None else None}
            else:
                # alles zurück: Datei, configuration.yaml
                if old is None:
                    p.unlink(missing_ok=True)
                else:
                    p.write_text(old)
                if main_new != main_old:
                    main.write_text(main_old)
                errors = redact(str((res or {}).get("errors") or "unbekannter Fehler"))[:240] if isinstance(res, dict) else ""
                ok, sub = False, f"nicht übernommen – die Prüfung meldete Fehler, der alte Stand ist wiederhergestellt: {errors}"
        elif st["art"] == "integration_einschalten":
            async with HAClient(session) as ha:
                res = await ha.call("config_entries/disable", entry_id=st["eintrag"], disabled_by=None)
            ok, sub = True, "eingeschaltet" + (" – Home Assistant braucht danach einen Neustart" if isinstance(res, dict) and res.get("require_restart") else "")
        else:
            status, res = await core(session, "POST", "/config/core/check_config")
            if status != 200 or not isinstance(res, dict) or res.get("result") != "valid":
                errors = redact(str((res or {}).get("errors") or "unbekannter Fehler"))[:300] if isinstance(res, dict) else ""
                done.append({"icon": "action", "title": st["title"], "sub": f"nicht ausgeführt – die Konfiguration hat Fehler: {errors}",
                             "url": "/config/logs", "failed": True})
                break
            status, _ = await core(session, "POST", "/services/homeassistant/restart", {})
            ok, sub = status in (200, 201, 502, 504), "Neustart läuft – in 1–3 Minuten ist alles wieder da"
        url = "/config/repairs" if st["art"].startswith("reparatur_") else "/config/logs" if st["art"] in ("yaml_aendern", "yaml_datei") \
            else "/config/updates" if st["art"] == "update_installieren" \
            else f"/config/integrations/integration/{st['domain']}" if st.get("domain") else None
        done.append({"icon": "action", "title": st["title"], "sub": sub, "url": url, **({"failed": True} if not ok else {}),
                     **({"undo": st["undo"]} if st.get("undo") else {})})
        if not ok:
            break
    if updates:
        asyncio.create_task(install_updates(session, updates))
    return done


async def install_updates(session, steps: list[dict]) -> None:
    """Spielt Updates nacheinander ein. Home Assistant selbst und Haglo Assist zuletzt – danach wird neu gestartet."""
    last = lambda st: ("haglo" in st["entity_id"], st["entity_id"].endswith("home_assistant_core_update"))
    for st in sorted(steps, key=last):
        body = {"entity_id": st["entity_id"], **({"backup": True} if st.get("backup") else {})}
        try:
            status, res = await core(session, "POST", "/services/update/install", body, timeout=1800)
            print(f"Update {st['entity_id']}: {status}")
        except Exception as e:
            # beim Update von Home Assistant selbst oder dieser App bricht die Verbindung ab – das ist der Neustart
            print(f"Update {st['entity_id']}: {type(e).__name__} {e}")



# ---------------------------------------------------------------- Einstellungen von Integrationen
# Haglo Assist kann die Optionen einer Integration (Geräte & Dienste -> Konfigurieren) lesen und nach
# Bestätigung ändern. Gelesen wird nur nach Zustimmung des Nutzers; Felder mit Zugangsdaten verlassen
# das Gerät nie (nur ihr Name, als "geheim" markiert). Geht nur bei Integrationen, deren Optionen aus
# einem einzigen Formular bestehen – alles andere wird ehrlich als "nicht änderbar" gemeldet.
SECRET_FIELD = re.compile(r"pass|token|secret|key|credential|auth|api|pin$|code$", re.I)
OPT = "/config/config_entries/options/flow"


def option_field(f: dict) -> dict:
    """Ein Feld des Options-Formulars in einer einfachen Form: art, aktueller Wert, erlaubte Werte."""
    name, sel = str(f.get("name")), f.get("selector") if isinstance(f.get("selector"), dict) else {}
    cur = (f.get("description") or {}).get("suggested_value", f.get("default"))
    out = {"name": name, "art": "fest", "cur": cur, "required": bool(f.get("required")), "raw": f}
    kind, cfg = (next(iter(sel.items())) if sel else (f.get("type"), {}))
    cfg = cfg if isinstance(cfg, dict) else {}
    if kind in ("boolean",):
        out["art"] = "ja/nein"
    elif kind in ("integer", "float", "number"):
        out["art"] = "zahl"
        step = cfg.get("step") or 1
        out["int"] = kind == "integer" or (kind == "number" and step != "any" and float(step).is_integer())
        out["min"] = cfg.get("min", f.get("valueMin")); out["max"] = cfg.get("max", f.get("valueMax"))
    elif kind in ("select", "multi_select"):
        raw = cfg.get("options") if sel else f.get("options")
        raw = list(raw.items()) if isinstance(raw, dict) else raw or []
        opts = [(o.get("value"), o.get("label")) if isinstance(o, dict) else (o[0], o[1]) if isinstance(o, (list, tuple)) else (o, o) for o in raw]
        out["art"], out["options"] = "auswahl", [str(v) for v, _ in opts]
        out["labels"] = {str(v): str(l) for v, l in opts if l is not None and str(l) != str(v)}
        out["multi"] = kind == "multi_select" or bool(cfg.get("multiple"))
    elif kind in ("string", "text"):
        out["art"] = "text"
        if cfg.get("type") == "password":
            out["secret"] = True
    elif kind == "entity":
        out["art"], out["multi"] = "entitaet", bool(cfg.get("multiple"))
    elif kind == "expandable":
        out["sub"] = [option_field(x) for x in f.get("schema") or [] if isinstance(x, dict) and x.get("name")]
    if SECRET_FIELD.search(name):
        out["secret"] = True
    return out


async def read_options(session, entry: dict) -> dict:
    """Öffnet das Options-Formular einer Integration, liest die Felder und schließt es wieder (ändert nichts)."""
    info = {"entry": entry["entry_id"], "domain": entry.get("domain"), "title": entry.get("title") or entry.get("domain"),
            "fields": [], "why": ""}
    if not entry.get("supports_options"):
        info["why"] = "hat keine Einstellungen zum Ändern"
        return info
    status, step = await core(session, "POST", OPT, {"handler": entry["entry_id"], "show_advanced_options": False})
    step = step if isinstance(step, dict) else {}
    try:
        if status != 200 or step.get("type") != "form":
            info["why"] = ("hat mehrere Einstellungsbereiche (Menü)" if step.get("type") == "menu"
                           else "lässt sich gerade nicht öffnen")
            return info
        info["step"] = step.get("step_id")
        info["fields"] = [option_field(f) for f in step.get("data_schema") or [] if isinstance(f, dict) and f.get("name")]
        if not info["fields"]:
            info["why"] = "hat keine Einstellungen zum Ändern"
    finally:
        if step.get("flow_id"):
            await core(session, "DELETE", f"{OPT}/{step['flow_id']}")
    # Verständliche Feldnamen aus den Übersetzungen der Integration (Deutsch, sonst Englisch)
    for lang in ("de", "en"):
        if not info["fields"] or all(f.get("label") for f in info["fields"]):
            break
        try:
            async with HAClient(session) as ha:
                res = (await ha.call("frontend/get_translations", language=lang, category="options",
                                     integration=[info["domain"]])).get("resources", {})
            for f in info["fields"]:
                f.setdefault("label", res.get(f"component.{info['domain']}.options.step.{info.get('step')}.data.{f['name']}"))
                if not f["label"]:
                    f.pop("label")
        except Exception:
            pass
    return info


def option_line(f: dict) -> str:
    label = f" „{f['label']}“" if f.get("label") else ""
    if f.get("secret"):
        return f"  {f['name']}{label} (geheim – nicht änderbar)"
    if f["art"] == "fest":
        return f"  {f['name']}{label} (nicht änderbar)"
    bits = [f["art"] + (", mehrere möglich" if f.get("multi") else "")]
    if f["art"] == "zahl":
        bits[0] = "ganze Zahl" if f.get("int") else "Zahl"
        if f.get("min") is not None or f.get("max") is not None:
            bits.append(f"von {f.get('min', '–')} bis {f.get('max', '–')}")
    if f["art"] == "auswahl":
        bits.append("erlaubt: " + ", ".join(f"{o} ({f['labels'][o]})" if o in f.get("labels", {}) else o for o in f["options"][:40]))
    bits.append("aktuell: " + (json.dumps(f["cur"], ensure_ascii=False)[:160] if f["cur"] is not None else "leer"))
    return f"  {f['name']}{label} ({'; '.join(bits)})"


def squash(t: str) -> str:
    return re.sub(r"[^a-z0-9äöüß]", "", str(t).lower())


async def integration_lines(session, query: str) -> list[str]:
    """Die Einstellungen der Integrationen, die zum genannten Namen passen – als Textblöcke für die KI."""
    status, entries = await core(session, "GET", "/config/config_entries/entry")
    entries = [e for e in entries if isinstance(e, dict) and e.get("entry_id")] if status == 200 and isinstance(entries, list) else []
    q, words = squash(query), [squash(w) for w in re.split(r"[\W_]+", query) if len(squash(w)) > 3]
    def score(e):
        names = [squash(e.get("title")), squash(e.get("domain"))]
        if q and any(q in n or (n and n in q) for n in names):
            return 2
        # ein Wort reicht („adaptive Beleuchtung“ findet adaptive_lighting); die KI wählt dann den passenden Eintrag
        return 1 if any(w in n for n in names for w in words) else 0
    hits = sorted((e for e in entries if score(e)), key=lambda e: (-score(e), not e.get("supports_options")))[:4]
    out = []
    for e in hits:
        info = await read_options(session, e)
        head = f"Eintrag {info['entry']} | Integration {info['domain']} | Name „{info['title']}“"
        if info["why"]:
            out.append(f"{head}: {info['why']} – nicht über Haglo Assist änderbar.")
        else:
            out.append(head + "\n" + "\n".join(option_line(f) for f in info["fields"][:60]))
    if not out:
        have = sorted({f"{e.get('domain')} („{e.get('title')}“)" for e in entries if e.get("supports_options")})[:50]
        out.append(f"Keine Integration passt zu „{query}“. Integrationen mit Einstellungen: " + (", ".join(have) or "keine"))
    return out


def option_value(f: dict, v):
    """Prüft einen neuen Wert gegen das Feld und bringt ihn in die richtige Form. Fehler als ValueError."""
    name = f.get("label") or f["name"]
    if f.get("secret") or f["art"] == "fest":
        raise ValueError(f"„{name}“ lässt sich über Haglo Assist nicht ändern.")
    if f["art"] == "ja/nein":
        if isinstance(v, bool):
            return v
        if str(v).lower() in ("true", "ja", "an", "1", "false", "nein", "aus", "0"):
            return str(v).lower() in ("true", "ja", "an", "1")
        raise ValueError(f"„{name}“ braucht ja oder nein.")
    if f["art"] == "zahl":
        try:
            n = float(str(v).replace(",", "."))
        except ValueError:
            raise ValueError(f"„{name}“ braucht eine Zahl.")
        if (f.get("min") is not None and n < float(f["min"])) or (f.get("max") is not None and n > float(f["max"])):
            raise ValueError(f"„{name}“ muss zwischen {f.get('min', '–')} und {f.get('max', '–')} liegen.")
        return int(n) if f.get("int") or (n.is_integer() and isinstance(f.get("cur"), int) and not isinstance(f.get("cur"), bool)) else n
    if f["art"] == "auswahl":
        vals = [str(x) for x in (v if isinstance(v, list) else [v])]
        if any(x not in f["options"] for x in vals) or (not f.get("multi") and len(vals) != 1):
            raise ValueError(f"„{name}“ erlaubt nur: {', '.join(f['options'][:20])}.")
        return vals if f.get("multi") else vals[0]
    if f["art"] == "entitaet":
        vals = [str(x) for x in (v if isinstance(v, list) else [v])]
        if any(not re.fullmatch(r"[a-z0-9_]+\.[a-z0-9_]+", x) for x in vals):
            raise ValueError(f"„{name}“ braucht ein Gerät aus der Liste.")
        return vals if f.get("multi") else vals[0]
    return str(v)[:500]


def option_defaults(fields: list[dict]) -> dict:
    """Alle Felder mit ihrem aktuellen Wert – das Formular will immer alles zurück, nicht nur die Änderung."""
    out = {}
    for f in fields:
        if f.get("sub") is not None:
            out[f["name"]] = option_defaults(f["sub"])
        elif f["cur"] is not None:
            out[f["name"]] = f["cur"]
    return out


async def integration_change(session, entry_id: str, values: dict) -> dict:
    """Prüft eine vorgeschlagene Änderung vor Ort und beschreibt sie für die Bestätigung (ändert nichts)."""
    status, entries = await core(session, "GET", "/config/config_entries/entry")
    entry = next((e for e in entries if isinstance(e, dict) and e.get("entry_id") == entry_id), None) if isinstance(entries, list) else None
    if not entry:
        raise RuntimeError("Diese Integration habe ich nicht gefunden. Bitte versuche es noch einmal.")
    info = await read_options(session, entry)
    if info["why"]:
        raise RuntimeError(f"„{info['title']}“ {info['why']} – das geht nur direkt in Home Assistant.")
    by = {f["name"]: f for f in info["fields"]}
    changes, clean = [], {}
    for k, v in values.items():
        if k not in by:
            raise RuntimeError(f"Die Einstellung „{k}“ gibt es bei „{info['title']}“ nicht.")
        try:
            new = option_value(by[k], v)
        except ValueError as e:
            raise RuntimeError(str(e))
        if new == by[k]["cur"]:
            continue
        clean[k] = new
        show = lambda x, f=by[k]: ("ja" if x is True else "nein" if x is False else "leer" if x is None
                                   else ", ".join(f.get("labels", {}).get(str(i), str(i)) for i in x) if isinstance(x, list)
                                   else f.get("labels", {}).get(str(x), str(x)))
        changes.append({"field": k, "label": by[k].get("label") or k.replace("_", " "), "old": show(by[k]["cur"]), "new": show(new)})
    return {"entry": entry_id, "title": info["title"], "domain": info["domain"], "changes": changes, "values": clean,
            "defaults": option_defaults(info["fields"])}


async def integration_apply(session, entry_id: str, values: dict) -> dict:
    """Speichert die Änderung über das Options-Formular von Home Assistant. Gibt die Beschreibung zurück."""
    plan = await integration_change(session, entry_id, values)
    if not plan["changes"]:
        return plan
    status, step = await core(session, "POST", OPT, {"handler": entry_id, "show_advanced_options": False})
    step = step if isinstance(step, dict) else {}
    if status != 200 or step.get("type") != "form" or not step.get("flow_id"):
        raise RuntimeError("Die Einstellungen ließen sich nicht öffnen.")
    status, res = await core(session, "POST", f"{OPT}/{step['flow_id']}", {**plan["defaults"], **plan["values"]})
    res = res if isinstance(res, dict) else {}
    if status == 200 and res.get("type") == "create_entry":
        return plan
    # nicht gespeichert: Formular wieder schließen, damit nichts offen bleibt
    await core(session, "DELETE", f"{OPT}/{res.get('flow_id') or step['flow_id']}")
    if res.get("type") == "form" and res.get("errors"):
        raise RuntimeError("Home Assistant hat die Werte abgelehnt: " + ", ".join(f"{k}: {v}" for k, v in res["errors"].items()))
    if res.get("type") in ("form", "menu"):
        raise RuntimeError("Diese Integration fragt weitere Schritte ab. Das geht nur direkt in Home Assistant – es wurde nichts geändert.")
    raise RuntimeError(f"Speichern fehlgeschlagen ({status}). Es wurde nichts geändert.")


async def history_lines(session, req: dict, by_id: dict) -> list[str]:
    """Langzeitdaten aus Home Assistant für die gewünschten Entitäten und den Zeitraum, je Entität eine
    kurze Zeile. Verlässt das Gerät nur nach ausdrücklicher Zustimmung zu genau dieser Anfrage."""
    from datetime import date, datetime, timedelta, timezone
    start, end = date.fromisoformat(req["von"]), min(date.fromisoformat(req["bis"]), date.today()) + timedelta(days=1)
    if end <= start:
        start = end - timedelta(days=1)
    days = (end - start).days
    step = req.get("schritt") or "tag"
    step = "monat" if days > 92 else "tag" if (step == "stunde" and days > 3) else step     # nie mehr als ~90 Werte
    period = {"stunde": "hour", "tag": "day", "monat": "month"}[step]
    ids = [e for e in req.get("entity_ids") or [] if e in by_id][:6]
    async with HAClient(session) as ha:
        data = await ha.call("recorder/statistics_during_period", statistic_ids=ids, period=period,
                             start_time=datetime(start.year, start.month, start.day, tzinfo=timezone.utc).isoformat(),
                             end_time=datetime(end.year, end.month, end.day, tzinfo=timezone.utc).isoformat(),
                             types=["change", "mean", "min", "max"]) or {}
    fmt = {"hour": "%d.%m. %Hh", "day": "%d.%m.", "month": "%Y-%m"}[period]
    num = lambda v: f"{v:.2f}".rstrip("0").rstrip(".") if abs(v) < 100 else f"{v:.0f}"
    lines = []
    for e in ids:
        a = by_id[e].get("attributes", {})
        name, unit, rows = a.get("friendly_name", e), a.get("unit_of_measurement", ""), data.get(e) or []
        if not rows:
            lines.append(f"{e} „{name}“: dafür speichert Home Assistant keine Langzeitdaten.")
            continue
        when = lambda r: datetime.fromtimestamp(r["start"] / 1000).strftime(fmt)
        if any(r.get("change") is not None for r in rows):
            vals = [(when(r), r.get("change") or 0) for r in rows]
            lines.append(f"{e} „{name}“ ({unit}, Summe je {step.capitalize()}, {req['von']} bis {req['bis']}): "
                         + "; ".join(f"{w}: {num(v)}" for w, v in vals) + f" | Gesamt: {num(sum(v for _, v in vals))} {unit}")
        else:
            vals = [(when(r), r.get("mean")) for r in rows if r.get("mean") is not None]
            lo = min((r["min"] for r in rows if r.get("min") is not None), default=None)
            hi = max((r["max"] for r in rows if r.get("max") is not None), default=None)
            lines.append(f"{e} „{name}“ ({unit}, Mittelwert je {step.capitalize()}, {req['von']} bis {req['bis']}): "
                         + "; ".join(f"{w}: {num(v)}" for w, v in vals)
                         + (f" | kleinster Wert: {num(lo)} {unit}, größter Wert: {num(hi)} {unit}" if lo is not None and hi is not None else ""))
    return [l[:3000] for l in lines]


TALK_GAP = 5 * 60


async def address_hints(session, wish: str) -> list[str]:
    """Nennt der Wunsch eine Adresse im Heimnetz, sieht die App selbst nach, welches Gerät das in
    Home Assistant ist – damit die KI nicht danach fragen muss."""
    hints = []
    for raw in list(dict.fromkeys(re.findall(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", wish)))[:2]:
        try:
            host = device_host(raw)
        except RuntimeError:
            continue
        mac = ""
        try:
            st = await device_get(session, host, "cm?cmnd=Status%205")
            mac = str(((st or {}).get("StatusNET") or {}).get("Mac", "")).lower()
            if not mac:
                sh = await device_get(session, host, "shelly")
                mac = ":".join(re.findall("..", str((sh or {}).get("mac", "")).lower())) if (sh or {}).get("mac") else ""
        except Exception:
            pass
        async with HAClient(session) as ha:
            devices = await ha.call("config/device_registry/list") or []
            dev = next((d for d in devices if re.search(rf"//{re.escape(host)}(?:[:/]|$)", str(d.get("configuration_url") or ""))), None)
            if not dev and mac:
                dev = next((d for d in devices if any(c[0] == "mac" and c[1].lower() == mac for c in d.get("connections") or [])), None)
            if not dev:
                hints.append(f"Unter {host} ist in Home Assistant noch kein Gerät eingerichtet"
                             + (" (dort antwortet aber ein Gerät – es lässt sich verbinden)." if mac else "."))
                continue
            ents = [e["entity_id"] for e in await ha.call("config/entity_registry/list") or []
                    if e.get("device_id") == dev["id"] and not e.get("disabled_by") and not e["entity_id"].startswith("device_tracker.")]
        name = dev.get("name_by_user") or dev.get("name") or "ohne Namen"
        hints.append(f"Unter {host} ist das Gerät „{name}“ mit den Entitäten: {', '.join(ents[:10]) or 'keine'}.")
    return hints


def talk_active(prefs: dict, history: list[dict]) -> float:
    """Wann der Nutzer zuletzt im Gespräch aktiv war: letzte Nachricht oder letzte Regung in der Oberfläche."""
    return max(prefs.get("talk_active", 0), history[-1].get("ts", 0) if history else 0)


def talk_touch() -> int:
    """Der Nutzer ist gerade aktiv. War er länger als TALK_GAP weg, beginnt damit ein neues Gespräch.
    Gibt zurück, seit wann er durchgehend aktiv ist."""
    prefs, now = load_prefs(), int(time.time())
    if now - talk_active(prefs, load_history()) > TALK_GAP:
        prefs["talk_reset"] = now
    prefs["talk_active"] = now
    write_prefs(prefs)
    return now


def talk_items() -> list[dict]:
    """Die Einträge des laufenden Gesprächs. Es endet nach TALK_GAP Sekunden ohne Regung des Nutzers –
    nicht schon nach TALK_GAP Sekunden ohne Nachricht: Wer liest, tippt oder scrollt, bleibt im Gespräch."""
    prefs, history, now = load_prefs(), load_history(), time.time()
    reset = prefs.get("talk_reset", 0)
    if now - talk_active(prefs, history) > TALK_GAP:
        return []
    items, last = [], now
    for h in reversed(history):
        ts = h.get("ts", 0)
        if ts <= reset:
            break
        # Ohne gemerkte Regung (ältere Version) gilt wie früher: höchstens TALK_GAP zwischen zwei Nachrichten
        if "talk_active" not in prefs and last - ts > TALK_GAP:
            break
        items.insert(0, h)
        last = ts
    return items


def recent_context() -> list[dict]:
    """Die Wechsel des laufenden Gesprächs (höchstens 10), damit die KI Bezüge wie „es“ versteht.
    Ein Gespräch endet nach TALK_GAP Sekunden ohne Regung des Nutzers – danach beginnt ein neues, und das
    Frühere wird nicht mehr mitgeschickt. Die Oberfläche zeigt diese Grenze an."""
    items = [h for h in talk_items() if h.get("wish")][-10:]
    return [{"wunsch": str(h["wish"])[:300], "antwort": re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", str(h.get("text") or ""))[:600]} for h in items]


def device_url(device_id: str | None) -> str:
    return f"/config/devices/device/{device_id}" if device_id else "/config/entities"


async def rename_items(session, items: list) -> list[dict]:
    """Benennt Geräte oder einzelne Entitäten um oder ordnet sie einem Raum zu. Die entity_id bleibt gleich.
    Gibt die Schritte für die Anzeige zurück: {"icon", "title", "sub", "url"}."""
    steps, areas = [], None
    async with HAClient(session) as ha:
        registry = {e["entity_id"]: e for e in await ha.call("config/entity_registry/list") or []}
        devices = {d["id"]: d for d in await ha.call("config/device_registry/list") or []}
        for it in items[:5]:
            ent, name = registry.get(str(it.get("entity_id"))), str(it.get("name") or "").strip()[:60]
            room = str(it.get("raum") or "").strip()[:60]
            if not ent or not (name or room):
                raise RuntimeError("Dieses Gerät kenne ich nicht – nenn mir bitte seinen bisherigen Namen.")
            whole = it.get("geraet") is not False and ent.get("device_id")
            dev = devices.get(ent.get("device_id")) or {}
            old = ((dev.get("name_by_user") or dev.get("name")) if whole else (ent.get("name") or ent.get("original_name"))) or ent["entity_id"]
            change, created = {}, False
            if room:
                # Raum suchen (Groß-/Kleinschreibung egal), sonst anlegen
                if areas is None:
                    areas = {a["name"].strip().lower(): a["area_id"] for a in await ha.call("config/area_registry/list") or []}
                if room.lower() not in areas:
                    areas[room.lower()] = (await ha.call("config/area_registry/create", name=room))["area_id"]
                    created = True
                change["area_id"] = areas[room.lower()]
            if whole:
                await ha.call("config/device_registry/update", device_id=ent["device_id"], **change, **({"name_by_user": name} if name else {}))
            else:
                await ha.call("config/entity_registry/update", entity_id=ent["entity_id"], **change, **({"name": name} if name else {}))
            url = device_url(ent.get("device_id"))
            if name:
                steps.append({"icon": "name", "title": name, "sub": f"hieß vorher „{old}“", "url": url})
            if room:
                steps.append({"icon": "room", "title": f"{name or old} → {room}", "sub": "Raum neu angelegt" if created else "Raum zugeordnet", "url": url})
    return steps


async def action_steps(session, actions: list, by_id: dict, registry: dict | None = None) -> list[dict]:
    """Was geschaltet wurde, für die Anzeige: je Gerät eine Zeile mit Link."""
    if registry is None:
        try:
            async with HAClient(session) as ha:
                registry = {e["entity_id"]: e for e in await ha.call("config/entity_registry/list") or []}
        except Exception:
            registry = {}
    words = {"turn_on": ("on", "eingeschaltet"), "turn_off": ("off", "ausgeschaltet"), "toggle": ("action", "umgeschaltet"),
             "open_cover": ("on", "geöffnet"), "close_cover": ("off", "geschlossen"), "lock": ("off", "verriegelt"), "unlock": ("on", "entriegelt")}
    steps = []
    for a in actions:
        service = a["action"].split(".", 1)[1]
        icon, word = words.get(service, ("action", service.replace("_", " ")))
        data = a.get("data") if isinstance(a.get("data"), dict) else {}
        extra = ", ".join(f"{k}: {v}" for k, v in list(data.items())[:2])
        for e in ((a.get("target") or {}).get("entity_id") or [])[:6]:
            name = (by_id.get(e) or {}).get("attributes", {}).get("friendly_name") or e
            steps.append({"icon": icon, "title": name, "sub": word + (f" ({extra})" if extra else ""),
                          "url": device_url((registry.get(e) or {}).get("device_id"))})
    return steps[:12]


class NeedMqtt(Exception):
    """Die Zugangsdaten des MQTT-Dienstes sind nicht zu bekommen: Die Oberfläche fragt den Nutzer danach."""

    def __init__(self, reason: str, ha_ip: str):
        super().__init__(reason)
        self.reason, self.ha_ip = reason, ha_ip


async def add_device(session, address: str, name: str, manual: dict | None = None) -> str:
    """Verbindet das Gerät mit Home Assistant. Gibt einen kurzen Bericht (Markdown) zurück.
    manual: vom Nutzer eingegebene MQTT-Zugangsdaten (bleiben lokal, gehen nur an das Gerät)."""
    info = await probe_device(session, address)
    host, name = info["host"], re.sub(r"[;\"\\]", "", name).strip()[:40]
    if info["kind"] == "shelly":
        if info.get("auth"):
            raise RuntimeError("Die Shelly ist mit einem Passwort geschützt. Bitte füge sie von Hand hinzu.")
        ok, why = await ensure_host_entry(session, "shelly", host)
        if not ok:
            raise RuntimeError(f"Home Assistant hat die Shelly nicht angenommen ({why}).")
        ents = await find_ha_device(session, info["mac"], "shelly", tries=6) if info["mac"] else []
        return ("Die Shelly ist jetzt mit Home Assistant verbunden."
                + (f" Neue Entitäten: {', '.join(f'`{e}`' for e in ents[:6])}." if ents else "")
                + "\n\n[Geräte öffnen](/config/devices/dashboard)")

    # Tasmota spricht über MQTT mit Home Assistant: Es braucht den MQTT-Dienst, die Integrationen und die Zugangsdaten.
    mqtt, why_not = {}, ""
    try:
        mqtt = await supervisor_get(session, "/services/mqtt")
    except Exception as e:
        why_not = str(e)
    ha_ip = ""
    try:
        for i in (await supervisor_get(session, "/network/info")).get("interfaces", []):
            for a in (i.get("ipv4") or {}).get("address") or []:
                net = ipaddress.ip_interface(a)
                if not ha_ip or ipaddress.ip_address(host) in net.network:
                    ha_ip = str(net.ip)
    except Exception:
        pass
    status, entries = await core(session, "GET", "/config/config_entries/entry")
    has_mqtt = status == 200 and any(e.get("domain") == "mqtt" for e in entries or [])
    broker_ip = ha_ip
    if manual and manual.get("username"):
        mqtt = {"host": "core-mosquitto", "port": int(manual.get("port") or 1883), "username": str(manual["username"])[:60],
                "password": str(manual.get("password") or "")[:120]}
        broker_ip = str(manual.get("host") or "").strip() or ha_ip
        if broker_ip != ha_ip:
            broker_ip = device_host(broker_ip)
    elif not mqtt.get("host"):
        if not has_mqtt:
            return ("Für Tasmota braucht Home Assistant einen MQTT-Dienst, und ich finde keinen. So geht es weiter:\n\n"
                    "1. Öffne die App **Mosquitto broker**, installiere und starte sie.\n"
                    "2. Schick mir danach denselben Wunsch noch einmal – den Rest erledige ich.\n\n"
                    f"[Mosquitto broker öffnen]({MY_ADDON}core_mosquitto)")
        # MQTT läuft, aber die App kommt nicht an die Zugangsdaten (fremder Dienst oder fehlende Berechtigung)
        raise NeedMqtt("403" if "403" in why_not or "401" in why_not else "extern", ha_ip)
    if not ha_ip and not (manual and manual.get("host")):
        raise RuntimeError("Ich konnte die Adresse deines Home Assistant im Heimnetz nicht ermitteln.")
    done = []
    ok, why = (True, "vorhanden") if has_mqtt else await ensure_integration(
        session, "mqtt", {"broker": mqtt["host"], "port": mqtt.get("port", 1883),
                          "username": mqtt.get("username", ""), "password": mqtt.get("password", "")})
    if not ok:
        raise RuntimeError(f"Die MQTT-Integration ließ sich nicht einrichten ({why}). Bitte füge sie unter Geräte & Dienste hinzu.")
    done.append("MQTT in Home Assistant: " + (why or "eingerichtet"))
    ok, why = await ensure_integration(session, "tasmota")
    if not ok:
        raise RuntimeError(f"Die Tasmota-Integration ließ sich nicht einrichten ({why}). Bitte füge sie unter Geräte & Dienste hinzu.")
    done.append("Tasmota-Integration: " + (why or "eingerichtet"))
    cmds = [f"MqttUser {mqtt.get('username', '')}", f"MqttPassword {mqtt.get('password', '')}", f"MqttPort {mqtt.get('port', 1883)}", "SetOption19 0"]
    if name:
        cmds += [f"DeviceName {name}", f"FriendlyName1 {name}"]
    cmds.append(f"MqttHost {broker_ip}")       # zuletzt: danach startet das Gerät neu
    if any(";" in c for c in cmds):
        for c in cmds:
            await device_get(session, host, "cm?cmnd=" + quote(c))
    else:
        await device_get(session, host, "cm?cmnd=" + quote("Backlog " + "; ".join(cmds)))
    done.append(f"Gerät eingestellt: meldet sich jetzt bei {broker_ip}" + (f", Name „{name}“" if name else ""))
    ents = await find_ha_device(session, info["mac"], "tasmota") if info["mac"] else []
    steps = "\n".join(f"- {d}" for d in done)
    if not ents:
        return (f"Ich habe alles eingestellt, aber Home Assistant zeigt das Gerät noch nicht an:\n\n{steps}\n\n"
                "Das dauert manchmal eine Minute. Erscheint es nicht, prüfe in der Weboberfläche des Geräts, ob MQTT verbunden ist.\n\n"
                f"[Geräte & Dienste öffnen](/config/integrations/dashboard)\n\n[Weboberfläche des Geräts](http://{host}/)")
    return (f"**{name or info['name']}** ist jetzt mit Home Assistant verbunden.\n\n{steps}\n- Neue Entitäten: "
            + ", ".join(f"`{e}`" for e in ents[:8])
            + "\n\nSchaltet die Steckdose nicht oder fehlen Messwerte, braucht sie in Tasmota noch die passende Gerätevorlage.\n\n"
            f"[Geräte öffnen](/config/devices/dashboard)\n\n[Weboberfläche des Geräts](http://{host}/)")


async def ensure_host_entry(session, domain: str, host: str) -> tuple[bool, str]:
    _, first = await core(session, "POST", "/config/config_entries/flow", {"handler": domain, "show_advanced_options": False})
    return await flow_finish(session, first if isinstance(first, dict) else {}, {"host": host})


# ---------------------------------------------------------------- Dashboard bauen
async def build_dashboard(app, wish: str, refine: bool, dialog: list, can_ask: bool,
                          dashboard: str | None = DASHBOARD_PATH, can_automate: bool = False, allow_states: bool = False,
                          allow_diagnose: bool = False, allow_history: bool = False,
                          allow_integrations: bool = False, allow_logs: bool = False, allow_yaml: bool = False,
                          allow_probe: bool = False) -> dict:
    """Holt das Dashboard vom Backend und speichert es. Gibt entweder eine Rückfrage
    ({"question": …}) oder das Ergebnis zurück. Wirft BackendError bei Backend-Fehlern.

    dashboard ist der url_path; "" ist die Standard-Übersicht. Fremde, vom Kunden selbst
    gebaute Dashboards werden nur gezielt geändert und nur nach ausdrücklicher Freigabe."""
    session = app["session"]
    by_id: dict = {}
    async with HAClient(session) as ha:
        label = DASHBOARD_TITLE
        if dashboard is None:       # keine Vorgabe: Startseite, außer der Wunsch nennt ein anderes Dashboard
            dashboard, label = await pick_dashboard(ha, wish)
            refine = not REBUILD_WORDS.search(wish)
        own = dashboard == DASHBOARD_PATH
        path = dashboard or await start_path(ha)
        entities = [e for e in await collect_entities(ha) if e["id"] not in (BAR_ENTITY, BAR_STATUS)]
        if not entities:
            raise RuntimeError("Keine Geräte gefunden.")
        before = await current_config(ha, path)
        auto = is_auto(before)
        # Neu bauen, wenn es nichts Eigenes gibt (automatisch erzeugt) oder der Nutzer es ausdrücklich will.
        # Sonst: gezielt ändern. Dashboards, die der Kunde selbst gebaut hat, werden dabei besonders geschont.
        rebuild = auto or not refine
        # Der Kunde hat dieses Dashboard selbst gestaltet: Sein Aufbau wird erst nach Freigabe übertragen.
        # Bis dahin geht alles, was das Dashboard nicht braucht (anlegen, schalten, fragen).
        locked = not own and not auto and dashboard not in allowed_dashboards()
        if locked and not can_automate:
            return {"need_dashboard_consent": {"id": dashboard, "label": label}}
        built_by_haglo = own or dashboard in load_prefs().get("built", [])
        extra = {}
        if can_automate:
            raw = await all_states(session)
            by_id = {x["entity_id"]: x for x in raw}
            extra = {"kinds": KINDS, "can_control": True, "can_add_device": True, "can_rename": True, "can_diagnose": True, "can_history": True, "can_integrations": True, "can_logs": True, "can_yaml": HA_CONFIG.exists(),
                     "can_probe": True, "can_yaml_file": HA_CONFIG.exists(),
                     "context": recent_context(), "hints": (await address_hints(session, wish))[:2] + ([] if HA_CONFIG.exists() else [
                         "Die App hat noch keinen Zugriff auf die Konfigurationsdateien (configuration.yaml) – sie wurde vor dem Update "
                         "installiert, das diesen Zugriff mitbringt. Muss eine YAML-Datei geändert werden, schreib nicht, du dürftest sie "
                         "nicht lesen, sondern: Einmal Haglo Assist aktualisieren (Einstellungen → Apps → Haglo Assist → Aktualisieren), "
                         "danach erledige ich das selbst."]),
                     "automations": await own_objects(session, raw)}
        payload = {
            "wish": wish, "entities": entities, "mode": app.get("effective_mode") or ("praezise" if is_complex(wish) else "schnell"),
            "previous": None if rebuild or locked else (strip_bar(before) if own else before),
            "refine": not rebuild, "foreign": not rebuild and not built_by_haglo, "dialog": dialog, "can_ask": can_ask,
            "dashboard_locked": locked,
            **extra,
        }
        result = await backend(session, "POST", "/v1/generate", payload, timeout=300)
        # Die KI fragt Daten an (Verlauf, Protokolle, YAML, Integrationen, Systemmeldungen, Zustände) – auch mehrere
        # nacheinander. Erst danach wird die eigentliche Antwort ausgewertet, egal welche Art sie dann hat.
        for _ in range(5):
            kind = result.get("type")
            if not can_automate or kind not in ("need_history", "need_logs", "need_yaml", "need_integrations", "need_diagnose", "need_states", "need_probe"):
                break
            if kind == "need_history":
                # Verlaufsdaten gehen nur raus, wenn der Nutzer genau dieser Anfrage zugestimmt hat – jedes Mal neu.
                req = result.get("request") or {}
                if not allow_history:
                    names = [by_id[e].get("attributes", {}).get("friendly_name", e) for e in req.get("entity_ids") or [] if e in by_id]
                    return {"need_history_consent": {"names": names, "von": req.get("von"), "bis": req.get("bis")}, "status": result["status"]}
                payload["history"] = await history_lines(session, req, by_id)
            elif kind == "need_probe":
                # Die Antwort eines Geräts im Heimnetz ist eine eigene Art von Daten: jedes Mal nur mit Zustimmung.
                req = result.get("request") or {}
                if not allow_probe:
                    try:
                        host = device_host(str(req.get("adresse") or ""))
                    except RuntimeError as e:
                        return {"answer": str(e), "status": result["status"]}
                    return {"need_probe_consent": {"adresse": host, "pfad": str(req.get("pfad") or "")[:120]}, "status": result["status"]}
                payload["probe"] = await probe_lines(session, req)
            elif kind == "need_logs":
                # Fehlerprotokolle sind eine eigene Art von Daten: nur mit Zustimmung, und geschwärzt.
                if not (allow_logs or load_prefs().get("logs_allowed")):
                    return {"need_logs_consent": True, "status": result["status"]}
                payload["logs"] = await log_lines(session)
            elif kind == "need_yaml":
                # Konfigurationsdateien: eigene Art von Daten, nur mit Zustimmung, geschwärzt, nie secrets.yaml
                if not (allow_yaml or load_prefs().get("yaml_allowed")):
                    return {"need_yaml_consent": {"files": result.get("files") or ["configuration.yaml"]}, "status": result["status"]}
                payload["yaml"] = await read_yaml(result.get("files") or [])
            elif kind == "need_integrations":
                # Namen und Einstellungen von Integrationen sind eine eigene Art von Daten: nur mit Zustimmung.
                if not (allow_integrations or load_prefs().get("integrations_allowed")):
                    return {"need_integrations_consent": {"query": str(result.get("query") or "")}, "status": result["status"]}
                payload["integrations"] = await integration_lines(session, str(result.get("query") or ""))
            elif kind == "need_diagnose":
                # Systemmeldungen sind eine eigene Art von Daten: Sie gehen nur mit Zustimmung raus.
                if not (allow_diagnose or load_prefs().get("diagnose_allowed")):
                    return {"need_diagnose_consent": True, "status": result["status"]}
                payload["diagnose"] = await system_messages(session)
            else:
                # Die KI braucht aktuelle Zustände. Die verlassen das Gerät nur mit Zustimmung des Nutzers –
                # und nur für genau die Geräte, nach denen gefragt wurde.
                wanted = [e for e in result.get("entities", []) if e in by_id][:30]
                if not (allow_states or load_prefs().get("states_allowed")):
                    return {"need_consent": [{"id": e, "name": by_id[e].get("attributes", {}).get("friendly_name", e)} for e in wanted],
                            "status": result["status"]}
                payload["states"] = {e: state_text(by_id[e]) for e in wanted}
            result = await backend(session, "POST", "/v1/generate", payload, timeout=300)
        if result.get("type") == "rename":
            steps = await rename_items(session, result.get("items") or [])
            text = result.get("erklaerung") or "Erledigt."
            actions = result.get("aktionen") or []
            if actions:
                # im selben Satz auch schalten: gleiche Prüfungen wie beim Schalten allein
                by_now = {x["entity_id"]: x for x in await all_states(session)}
                error = check_actions(actions, by_now)
                if error:
                    add_history("control", wish, text + " Das Schalten ging nicht: " + error, steps=steps)
                    return {"done": text, "status": result["status"]}
                if any(risky_action(a, by_now) for a in actions):
                    add_history("control", wish, text, steps=steps)
                    return {"confirm_actions": actions, "erklaerung": text, "status": result["status"]}
                await run_actions(session, actions)
                steps += await action_steps(session, actions, by_now)
            add_history("control", wish, text, steps=steps)
            return {"done": text, "status": result["status"]}
        if result.get("type") == "add_device":
            # Die KI hat nur Adresse und Namen herausgelesen. Geprüft wird hier, im Heimnetz.
            want = result.get("device") or {}
            info = await probe_device(session, str(want.get("adresse") or ""))
            return {"confirm_device": {**info, "wanted_name": str(want.get("name") or "")[:40]}, "status": result["status"]}
        if locked and not result.get("neues_dashboard") and result.get("type") not in ("objects", "actions", "answer", "need_states", "need_diagnose", "need_history", "question", "need_integrations", "integration_set", "need_logs", "repair", "need_yaml", "need_probe"):
            # Der Wunsch betrifft das Dashboard: Die Oberfläche fragt mit einer Karte nach der Freigabe.
            return {"need_dashboard_consent": {"id": dashboard, "label": label}, "status": result.get("status")}
        if result.get("type") == "repair":
            plan = await repair_plan(session, result.get("steps") or [])
            keys = ("art", "eintrag", "domain", "issue_id", "option", "datei", "alt", "neu", "entity_id", "inhalt")
            return {"confirm_repair": {"steps": [{k: p[k] for k in keys if k in p} for p in plan if p["art"] != "hinweis"],
                                       "show": [{"title": p["title"], "sub": p["sub"], **({"code": p["code"]} if p.get("code") else {})} for p in plan]},
                    "erklaerung": result.get("erklaerung", ""), "status": result["status"]}
        if result.get("type") == "integration_set":
            change = result.get("change") or {}
            plan = await integration_change(session, str(change.get("eintrag") or ""), change.get("werte") or {})
            if not plan["changes"]:
                text = f"„{plan['title']}“ ist schon so eingestellt."
                add_history("answer", wish, text)
                return {"answer": text, "status": result["status"]}
            plan.pop("defaults")
            return {"confirm_integration": plan, "erklaerung": result.get("erklaerung", ""), "status": result["status"]}
        if result.get("type") == "objects":
            # Vorschlag anzeigen; angelegt wird erst, wenn der Nutzer bestätigt.
            return {"objects": result["objekte"], "erklaerung": result.get("erklaerung", ""), "status": result["status"]}
        if result.get("type") == "actions":
            actions = result["aktionen"]
            error = check_actions(actions, by_id)
            if error:
                raise RuntimeError(error)
            if any(risky_action(a, by_id) for a in actions):
                return {"confirm_actions": actions, "erklaerung": result.get("erklaerung", ""), "status": result["status"]}
            await run_actions(session, actions)
            add_history("control", wish, result.get("erklaerung", ""), steps=await action_steps(session, actions, by_id))
            return {"done": result.get("erklaerung", ""), "status": result["status"]}
        if result.get("type") == "answer":
            add_history("answer", wish, result["antwort"])
            return {"answer": result["antwort"], "status": result["status"]}
        if result.get("type") == "need_states":
            raise RuntimeError("Das konnte ich gerade nicht beantworten. Bitte versuche es noch einmal.")
        if result.get("type") == "question":
            # Die KI braucht noch eine Angabe: Rückfrage anzeigen, nichts speichern.
            return {"question": {"frage": result["frage"], "optionen": result["optionen"],
                                 "mehrfach": bool(result.get("mehrfach"))}, "status": result["status"]}

        if result.get("neues_dashboard"):
            # Eigenes Dashboard mit Eintrag in der Seitenleiste – das bisherige bleibt unangetastet
            return await create_sidebar_dashboard(ha, wish, result)

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
        elif not rebuild:
            # Sicherheitsnetz: Beim Ändern darf nicht plötzlich die Hälfte fehlen.
            had, has = count_all_cards(before), count_all_cards(config)
            if not config.get("views") or (had > 6 and has < had * 0.6):
                raise RuntimeError("Die Änderung hätte große Teile deines Dashboards entfernt. Es wurde nichts "
                                   "gespeichert. Bitte beschreibe genauer, was sich ändern soll.")

        if not config.get("views"):
            raise RuntimeError("Das Ergebnis war leer. Es wurde nichts gespeichert.")
        # Auf Haglo-Geräten: Hinweis-Karte, sobald Haglo Assist die Seite baut – einmal. War sie schon da, bleibt sie.
        st, prefs = result.get("status") or {}, load_prefs()
        if st.get("haglo_device") and st.get("included_plan") and (
                has_haglo_card(before) or (rebuild and (own or dashboard == "") and not prefs.get("haglo_card"))):
            try:
                slug = (await supervisor_get(session, "/addons/self/info")).get("slug")
                if slug:
                    config = add_haglo_card(config, slug, st.get("included_until"))
                    write_prefs({**prefs, "haglo_card": True})
            except Exception:
                pass
        backup = write_backup(before, f"vor: {wish}", dashboard)
        start_switched = False
        if dashboard == "":
            path = await start_path(ha, create=True)
        await ha.call("lovelace/config/save", url_path=path, config=config)
        if dashboard == "":
            try:
                start_switched = await show_start_page(session, ha, path)
            except Exception as e:
                print(f"Startseite umstellen fehlgeschlagen: {e}")
        if not own and rebuild:
            # Von Haglo Assist gebaut: darf künftig ohne Rückfrage weiterbearbeitet werden
            prefs = load_prefs()
            prefs["built"] = sorted(set(prefs.get("built", [])) | {dashboard})
            write_prefs(prefs)
            ALLOWED_FILE.parent.mkdir(parents=True, exist_ok=True)
            ALLOWED_FILE.write_text(json.dumps(sorted(allowed_dashboards() | {dashboard})))
    if start_switched:
        result["erklaerung"] = (result.get("erklaerung", "") + " Home Assistant öffnet jetzt diese Seite als Startseite.").strip()
    add_history("dashboard", wish, result.get("erklaerung", ""), url=f"/{path or 'lovelace'}", target=label,
                undo={"type": "version", "id": backup} if backup else None)
    return {"erklaerung": result.get("erklaerung", ""), "config": config, "removed": sorted(set(removed)),
            "backup": backup, "usage": result.get("usage", {}), "status": result["status"],
            "dashboard_url": f"/{path or 'lovelace'}", "start_switched": start_switched}


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
    if not load_prefs().get("first_start_done"):
        print("Einrichtung:", await first_start_setup(session))
    since = load_prefs().get("default_panel_since")
    if since and time.time() - since > 14 * 86400:
        await welcome_done(session)
    try:
        st = (await backend(session, "GET", "/v1/status"))["status"]
        note_terms(st)
        trial = trial_info(await identify(request.app, st))
    except BackendError:
        trial = trial_info(None)
    return web.json_response({
        "ok": True,
        "terms_update": bool(load_prefs().get("terms_update")),
        "update": await update_info(session),
        "entities": len(entities),
        "areas": sorted({e["area"] for e in entities if e["area"]}),
        "has_dashboard": cfg is not None,
        # Woran gearbeitet wird: die Wahl des Nutzers, sonst das vorhandene Haglo Dashboard, sonst die Startseite
        "dashboard": load_prefs().get("dashboard", DASHBOARD_PATH if cfg is not None else ""),
        "api_key": True,  # Der Schlüssel liegt im Backend; Feld bleibt für die Oberfläche.
        "backups": len(version_files()),
        "prefs": load_prefs(),
        "features": {"automations": True, "control": True, **FEATURES},
        # Rein lokal: "sicher" = Geräte-Zettel der Haglo-Einrichtung, "vermutlich" = nur das Modell passt
        "preinstalled": looks_like_haglo(),
        "haglo_marker": bool(load_marker()),
        "hub_id": load_hub()["hub_id"],
        "busy": request.app.get("job"),
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
        note_terms(data["status"])
    except BackendError as e:
        return backend_failure(e)
    await welcome_done(request.app["session"])
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
    talk_touch()        # eine Nachricht ist auch eine Regung; nach langer Pause beginnt hier das neue Gespräch
    if load_prefs().get("terms_update"):
        # neue Nutzungsbedingungen/Datenschutzerklärung: erst zustimmen – auch für lokale Antworten
        return web.json_response({"ok": False, "error": "consent_required", "terms_update": True}, status=403)
    mode = user_mode()
    complex_wish = is_complex(wish)
    if not dialog and not refine:
        # Schnellweg (Auto, Lokal, Schnell) und Haglo Local (Auto bei einfachen Wünschen, Lokal immer):
        # direkt auf dem Hub, ohne Internet und Kontingent
        try:
            # Cloud: nur der Schnellweg (eindeutige Schaltbefehle ohne KI, kostet nichts) – alles andere Claude
            done = await local_answer(request.app, wish, use_model=mode == "lokal" or (
                mode == "auto" and (not complex_wish or bool(local_ai.PLAN.search(wish)) or bool(local_ai.DASH_CUE.search(wish)))),
                code_only=mode == "cloud")
        except Exception as e:
            print(f"[Lokal] übersprungen: {e}", flush=True)
            done = None
        if done:
            return web.json_response({"ok": True, **done})
    if mode == "lokal":
        # Lokal heißt lokal: nie still an Claude weitergeben. Ehrlich sagen, was lokal nicht geht.
        text = ("Das geht lokal nicht – dafür „Auto“ oder „Cloud“ wählen.") if complex_wish else \
               ("Das habe ich lokal nicht sicher verstanden. Gerät oder Raum genauer nennen – oder „Auto“ wählen.")
        print(f"[Lokal] nicht lokal lösbar, Cloud NICHT gefragt (Modus Lokal) | {wish!r}", flush=True)
        add_history("answer", wish, text, local=1)
        return web.json_response({"ok": True, "answer": text, "local": 1})
    # Welche Claude-Stufe? Die App wählt selbst: große Aufgaben, Rückfragen und Verfeinern gründlich, sonst schnell.
    cloud_mode = "praezise" if (complex_wish or refine or dialog) else "schnell"
    request.app["effective_mode"] = cloud_mode
    global ROUTE
    ROUTE = {"auto": mode == "auto", "via": cloud_mode}
    try:
        # Vorab prüfen, damit ohne Zustimmung oder Kontingent keine Gerätedaten gesendet werden.
        trial = trial_info((await backend(session, "GET", "/v1/status"))["status"])
    except BackendError as e:
        return backend_failure(e)
    if not trial["accepted"]:
        return web.json_response({"ok": False, "error": "consent_required", "trial": trial}, status=403)
    if trial["expired"]:
        return web.json_response({"ok": False, "error": "trial_expired", "trial": trial}, status=402)
    dashboard = body.get("dashboard") if isinstance(body.get("dashboard"), str) else None
    # Merken, dass gerade gebaut wird: Wer die Seite verlässt und zurückkommt, sieht das weiterhin.
    request.app["job"] = {"wish": wish, "since": int(time.time())}
    def build():
        return build_dashboard(request.app, wish, refine, dialog, can_ask=True, dashboard=dashboard,
                               can_automate=True, allow_states=bool(body.get("allow_states")),
                               allow_diagnose=bool(body.get("allow_diagnose")),
                               allow_integrations=bool(body.get("allow_integrations")),
                               allow_logs=bool(body.get("allow_logs")),
                               allow_yaml=bool(body.get("allow_yaml")),
                               allow_probe=bool(body.get("allow_probe")),
                               allow_history=bool(body.get("allow_history")))
    try:
        try:
            result = await build()
        except BackendError:
            raise                   # Kontingent, Zustimmung, Server: nicht nochmal versuchen
        except Exception as e:
            if cloud_mode != "schnell":
                raise
            # schnelles Modell gescheitert → einmal gründlich
            print(f"[Cloud] schnell gescheitert ({str(e)[:120]}) – gründlich nochmal", flush=True)
            request.app["effective_mode"] = "praezise"
            ROUTE["via"] = "praezise"
            result = await build()
    except BackendError as e:
        return backend_failure(e)
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=500)
    finally:
        request.app["job"] = None
        request.app["effective_mode"] = None
        ROUTE = None
    for key in ("objects", "confirm_actions", "confirm_device", "need_consent", "need_history_consent", "need_diagnose_consent", "need_integrations_consent", "confirm_integration", "need_logs_consent", "confirm_repair", "need_yaml_consent", "need_probe_consent", "need_dashboard_consent", "done", "answer"):
        if key in result:
            return web.json_response({"ok": True, key: result[key], "erklaerung": result.get("erklaerung", ""),
                                      "trial": trial_info(result["status"]) if result.get("status") else trial})
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


# Symbole für Knöpfe zu Integrationen und Apps. Der Browser lädt sie über die App, nicht von fremden
# Servern: Integrationen holt die App einmal aus dem offiziellen Symbol-Verzeichnis von Home Assistant
# (brands.home-assistant.io, wie Home Assistant selbst) und merkt sie sich, App-Symbole kommen vom Supervisor.
ICON_DIR = Path("/data/icons")


async def icon_response(data: bytes | None, ctype: str = "image/png"):
    if not data:
        return web.Response(status=404)
    return web.Response(body=data, content_type=ctype, headers={"Cache-Control": "max-age=604800"})


@routes.get("/api/icon/integration/{domain}")
async def icon_integration(request):
    domain = request.match_info["domain"]
    if not re.fullmatch(r"[a-z0-9_]{2,64}", domain):
        return web.Response(status=404)
    f = ICON_DIR / f"i_{domain}.png"
    if f.exists():
        return await icon_response(f.read_bytes() or None)
    data = None
    try:
        async with request.app["session"].get(f"https://brands.home-assistant.io/{domain}/icon.png",
                                              timeout=aiohttp.ClientTimeout(total=8)) as r:
            if r.status == 200 and r.content_type == "image/png":
                data = (await r.read())[:300000]
    except Exception:
        return web.Response(status=404)
    ICON_DIR.mkdir(parents=True, exist_ok=True)
    f.write_bytes(data or b"")      # auch "gibt es nicht" merken, damit nicht jedes Mal gefragt wird
    return await icon_response(data)


@routes.get("/api/icon/app/{slug}")
async def icon_app(request):
    slug = request.match_info["slug"]
    if not re.fullmatch(r"[a-z0-9_]{2,64}", slug):
        return web.Response(status=404)
    try:
        async with request.app["session"].get(f"http://supervisor/addons/{slug}/icon", headers={"Authorization": f"Bearer {TOKEN}"},
                                              timeout=aiohttp.ClientTimeout(total=8)) as r:
            return await icon_response((await r.read())[:300000] if r.status == 200 else None)
    except Exception:
        return web.Response(status=404)


@routes.get("/api/history")
async def history(_):
    """Der Gesprächsverlauf (lokal gespeichert), älteste zuerst."""
    reg = load_automations()
    items = [{**{k: v for k, v in h.items() if k != "undo"}, "id": h.get("id") or str(h.get("ts")),
              "can_undo": can_undo(h, reg)} for h in load_history()]
    talk = talk_items()
    # talk_since: ab wann das laufende Gespräch geht – die Oberfläche zeigt es beim Öffnen wieder an
    return web.json_response({"ok": True, "history": items, "talk_since": talk[0].get("ts") if talk else None,
                              "talk_active": talk_active(load_prefs(), load_history()) if talk else None})


@routes.post("/api/history/undo")
async def undo_history(request):
    """Nimmt genau diesen Eintrag zurück: Dashboard auf den Stand davor, Angelegtes löschen,
    geänderte eigene Automationen wiederherstellen."""
    try:
        hid = str((await request.json()).get("id", ""))
    except Exception:
        hid = ""
    items = load_history()
    entry = next((h for h in items if (h.get("id") or str(h.get("ts"))) == hid), None)
    reg = load_automations()
    if not entry or not can_undo(entry, reg):
        return web.json_response({"ok": False, "error": "Das lässt sich nicht mehr rückgängig machen."}, status=404)
    session, undo = request.app["session"], entry["undo"]
    try:
        if undo["type"] == "version":
            # keep=True: Der jetzige Stand wird vorher gesichert, das Rückgängig lässt sich also selbst zurücknehmen
            await restore_version(request.app, BACKUP_DIR / undo["id"], keep=True, log=False)
        elif undo["type"] == "timer":
            cancel_timer(request.app, undo["id"])
        elif undo["type"] == "yaml_file":
            # eigene Paket-Datei entfernen bzw. die vorige Fassung zurück, prüfen, neu starten
            for item in undo["items"]:
                p = HA_CONFIG / item["file"]
                if not PACKAGE_FILE.fullmatch(item["file"]):
                    continue
                if item.get("backup") and (YAML_BACKUPS / item["backup"]).exists():
                    p.write_text((YAML_BACKUPS / item["backup"]).read_text())
                else:
                    p.unlink(missing_ok=True)
            status, res = await core(session, "POST", "/config/core/check_config")
            if status != 200 or not isinstance(res, dict) or res.get("result") != "valid":
                raise RuntimeError("Die Konfiguration hat danach Fehler – bitte unter Einstellungen → System → Protokolle nachsehen.")
            await core(session, "POST", "/services/homeassistant/restart", {})
        elif undo["type"] == "new_dashboard":
            # neu angelegtes Dashboard wieder löschen (verschwindet aus der Seitenleiste)
            async with HAClient(session) as ha:
                try:
                    await ha.call("lovelace/dashboards/delete", dashboard_id=undo["id"])
                except HAError as e:
                    if "not_found" not in str(e):
                        raise
        else:
            for item in undo["items"]:
                if item.get("backup") and (AUTO_BACKUP_DIR / item["backup"]).exists():
                    saved = json.loads((AUTO_BACKUP_DIR / item["backup"]).read_text())
                    cfg = {k: v for k, v in saved["config"].items() if k != "id"}
                    status, _ = await core(session, "POST", f"/config/automation/config/{saved['id']}", cfg)
                    if status != 200:
                        raise RuntimeError(f"Fehler {status}")
                elif item["id"] in reg:
                    art = reg[item["id"]].get("art", "automation")
                    if art == "helfer":
                        typ, helper_id = item["id"].split(":", 1)
                        async with HAClient(session) as ha:
                            await ha.call(f"{typ}/delete", **{f"{typ}_id": helper_id})
                    else:
                        status, _ = await core(session, "DELETE", f"/config/{CONFIG_PATH[art]}/config/{item['id']}")
                        if status not in (200, 404):
                            raise RuntimeError(f"Fehler {status}")
                    reg.pop(item["id"], None)
                    save_automations(reg)
    except Exception as e:
        return web.json_response({"ok": False, "error": f"Rückgängig fehlgeschlagen: {str(e)[:160]}"}, status=500)
    items = load_history()
    for h in items:
        if (h.get("id") or str(h.get("ts"))) == hid:
            h["undone"] = True
    save_history(items)
    return web.json_response({"ok": True})


@routes.post("/api/history/clear")
async def clear_history(_):
    """Neu beginnen: leert den Gesprächsverlauf. Versionen und Angelegtes bleiben erhalten."""
    save_history([])
    return web.json_response({"ok": True})


@routes.get("/api/automations")
async def list_objects(request):
    """Von Haglo Assist Angelegtes: Automationen, Skripte, Szenen und Helfer."""
    session = request.app["session"]
    try:
        states = await all_states(session)
        configs = {o["id"]: o for o in await own_objects(session)}
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=500)
    auto_on = {x["attributes"].get("id"): x["state"] == "on" for x in states if x["entity_id"].startswith("automation.")}
    reg, out = load_automations(), []
    for oid, meta in reg.items():
        art = meta.get("art", "automation")
        if art in CONFIG_PATH and oid not in configs:
            continue
        out.append({"id": oid, "art": art, "alias": (configs.get(oid) or {}).get("alias") or meta.get("alias", ""),
                    "wann": meta.get("wann", ""), "was": meta.get("was", ""), "created": meta.get("created"),
                    "on": auto_on.get(oid, True) if art == "automation" else True})
    prefs = load_prefs()
    return web.json_response({"ok": True, "automations": sorted(out, key=lambda a: -(a["created"] or 0)),
                              "foreign_automations": bool(prefs.get("foreign_automations")),
                              "states_allowed": bool(prefs.get("states_allowed"))})


@routes.post("/api/automations/apply")
async def apply_objects(request):
    """Legt vom Nutzer bestätigte Automationen, Skripte, Szenen und Helfer in Home Assistant an
    oder ersetzt vorhandene. Eigene Automationen des Kunden nur, wenn er das erlaubt hat –
    ihre bisherige Fassung wird vorher gesichert."""
    session = request.app["session"]
    try:
        body = await request.json()
    except Exception:
        body = {}
    items = [a for a in body.get("automations") or [] if isinstance(a, dict)][:6]
    if not items:
        return web.json_response({"ok": False, "error": "Nichts übergeben."}, status=400)
    try:
        states = await all_states(session)
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=500)
    valid = {x["entity_id"] for x in states}
    foreign_ids = {x["attributes"].get("id") for x in states if x["entity_id"].startswith("automation.")} \
        if load_prefs().get("foreign_automations") else set()
    for a in items:
        art = a.get("art", "automation")
        if art not in KINDS or (art == "helfer" and a.get("typ") not in HELPER_TYPES):
            return web.json_response({"ok": False, "error": "Unbekannte Art."}, status=400)
        refs = entity_refs([a.get("triggers"), a.get("conditions"), a.get("actions"), a.get("sequence")], set())
        if art == "szene":
            refs |= set((a.get("entities") or {}).keys())
        unknown = sorted(refs - valid)
        if unknown:
            return web.json_response({"ok": False, "error": f"Diese Geräte gibt es nicht: {', '.join(unknown[:3])}. Nichts wurde angelegt."}, status=400)
    reg, done = load_automations(), []
    for i, a in enumerate(items):
        art, alias = a.get("art", "automation"), str(a.get("alias") or "Haglo")[:120]
        note = (str(a.get("description") or "") + " (angelegt mit Haglo Assist)").strip()[:300]
        backup = None
        try:
            if art == "helfer":
                settings = {k: v for k, v in (a.get("einstellungen") or {}).items()
                            if k in ("min", "max", "step", "mode", "unit_of_measurement", "options", "has_date", "has_time",
                                     "duration", "icon", "initial", "restore", "pattern", "minimum", "maximum")}
                async with HAClient(session) as ha:
                    made = await ha.call(f"{a['typ']}/create", name=alias, **settings)
                oid = f"{a['typ']}:{made['id']}"
            else:
                mine, own_change = a.get("id") in reg, a.get("id") in foreign_ids and art == "automation"
                oid = a["id"] if (mine or own_change) else f"haglo_{int(time.time())}_{i}"
                if art == "automation":
                    cfg = {"alias": alias, "description": note, "mode": a.get("mode") or "single", "triggers": a.get("triggers") or [],
                           "conditions": a.get("conditions") or [], "actions": a.get("actions") or []}
                elif art == "skript":
                    cfg = {"alias": alias, "description": note, "mode": a.get("mode") or "single", "sequence": a.get("sequence") or []}
                else:
                    cfg = {"id": oid, "name": alias, "entities": a.get("entities") or {}}
                if own_change and not mine:
                    # eigene Automation des Kunden: bisherige Fassung sichern, Beschreibung nicht anfassen
                    status, old = await core(session, "GET", f"/config/automation/config/{oid}")
                    if status != 200:
                        raise RuntimeError("Die bisherige Automation ließ sich nicht lesen.")
                    AUTO_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
                    backup = f"{int(time.time())}_{i}.json"
                    (AUTO_BACKUP_DIR / backup).write_text(json.dumps({"id": oid, "config": old}, ensure_ascii=False))
                    cfg["description"] = str(a.get("description") or old.get("description") or "")[:300]
                status, data = await core(session, "POST", f"/config/{CONFIG_PATH[art]}/config/{oid}", cfg)
                if status != 200:
                    raise RuntimeError((data.get("message") if isinstance(data, dict) else "") or f"Fehler {status}")
        except Exception as e:
            return web.json_response({"ok": False, "error": f"Home Assistant hat „{alias}“ abgelehnt: {str(e)[:200]}",
                                      "created": done}, status=400)
        if not backup:
            reg[oid] = {"art": art, "alias": alias, "wann": str(a.get("wann") or "")[:300], "was": str(a.get("was") or "")[:300],
                        "created": reg.get(oid, {}).get("created") or int(time.time())}
            save_automations(reg)
        done.append({"id": oid, "alias": alias, "art": art, "backup": backup})
    add_history("automation", str(body.get("wish") or ""), ", ".join(d["alias"] for d in done),
                undo={"type": "objects", "items": [{"id": d["id"], "backup": d["backup"]} for d in done]})
    return web.json_response({"ok": True, "created": done})


@routes.post("/api/automations/delete")
async def delete_object(request):
    """Löscht etwas, das Haglo Assist angelegt hat. Eigenes des Kunden wird nie gelöscht."""
    try:
        oid = str((await request.json()).get("id", ""))
    except Exception:
        oid = ""
    reg = load_automations()
    if oid not in reg:
        return web.json_response({"ok": False, "error": "Das wurde nicht von Haglo Assist angelegt."}, status=404)
    art = reg[oid].get("art", "automation")
    try:
        if art == "helfer":
            typ, hid = oid.split(":", 1)
            async with HAClient(request.app["session"]) as ha:
                await ha.call(f"{typ}/delete", **{f"{typ}_id": hid})
        else:
            status, _ = await core(request.app["session"], "DELETE", f"/config/{CONFIG_PATH[art]}/config/{oid}")
            if status not in (200, 404):
                raise RuntimeError(f"Fehler {status}")
    except HAError as e:
        if e.code != "not_found":
            return web.json_response({"ok": False, "error": f"Löschen fehlgeschlagen: {e}"}, status=500)
    except Exception as e:
        return web.json_response({"ok": False, "error": f"Löschen fehlgeschlagen: {e}"}, status=500)
    alias = reg.pop(oid).get("alias", "")
    save_automations(reg)
    add_history("automation_deleted", "", alias)
    return web.json_response({"ok": True})


@routes.post("/api/automations/restore")
async def restore_automation(request):
    """Stellt die frühere Fassung einer geänderten eigenen Automation des Kunden wieder her."""
    try:
        name = str((await request.json()).get("backup", ""))
    except Exception:
        name = ""
    path = AUTO_BACKUP_DIR / name
    if not re.fullmatch(r"\d+_\d+\.json", name) or not path.exists():
        return web.json_response({"ok": False, "error": "Diese Sicherung gibt es nicht mehr."}, status=404)
    saved = json.loads(path.read_text())
    cfg = {k: v for k, v in saved["config"].items() if k != "id"}
    status, data = await core(request.app["session"], "POST", f"/config/automation/config/{saved['id']}", cfg)
    if status != 200:
        return web.json_response({"ok": False, "error": f"Wiederherstellen fehlgeschlagen ({status})."}, status=500)
    add_history("restore", "", "Automation wiederhergestellt: " + str(cfg.get("alias", "")))
    return web.json_response({"ok": True})


@routes.post("/api/repair/apply")
async def repair_apply_route(request):
    """Der Nutzer hat die angezeigten Reparaturschritte bestätigt."""
    body = await request.json()
    steps = [x for x in body.get("steps") or [] if isinstance(x, dict)]
    try:
        done = await repair_apply(request.app["session"], steps)
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=400)
    files = [d.pop("undo") for d in done if d.get("undo")]
    add_history("control", str(body.get("wish") or ""), str(body.get("erklaerung") or "Reparatur ausgeführt."), steps=done,
                undo={"type": "yaml_file", "items": files} if files else None)
    # Hat ein Schritt nicht geklappt, sagt die Oberfläche das deutlich statt still den Verlauf zu zeigen
    failed = [d for d in done if d.get("failed")]
    # Klappte eine eingebaute Reparatur nicht, gleich die anderen Wege anbieten (z. B. „Entfernen“ statt „Neu laden“)
    alternatives = []
    if failed:
        for st in steps:
            if st.get("art") != "reparatur_beheben":
                continue
            try:
                issue = await repair_issue(request.app["session"], str(st.get("domain")), str(st.get("issue_id")))
                if not issue:
                    continue
                opts = await repair_options(request.app["session"], issue["domain"], issue["issue_id"])
                name, labels = await issue_texts(request.app["session"], issue)
            except Exception:
                continue
            base = {"domain": issue["domain"], "issue_id": issue["issue_id"]}
            for o in opts:
                if o != st.get("option"):
                    alternatives.append({"label": f"Stattdessen: {labels.get(o) or o.rsplit('_', 1)[-1].capitalize()}", "hint": name,
                                         "step": {"art": "reparatur_beheben", **base, "option": o}})
            alternatives.append({"label": "Hinweis ausblenden", "hint": name, "step": {"art": "reparatur_ignorieren", **base}})
    return web.json_response({"ok": True, "failed": [f"{d['title']}: {d['sub']}" for d in failed], "alternatives": alternatives[:6]})


@routes.post("/api/integrations/apply")
async def integrations_apply(request):
    """Der Nutzer hat die angezeigte Änderung bestätigt: Einstellungen der Integration speichern."""
    body = await request.json()
    values = body.get("values") if isinstance(body.get("values"), dict) else {}
    try:
        plan = await integration_apply(request.app["session"], str(body.get("entry") or ""), values)
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=400)
    url = f"/config/integrations/integration/{plan['domain']}"
    add_history("control", str(body.get("wish") or ""), str(body.get("erklaerung") or "Einstellungen geändert."),
                steps=[{"icon": "action", "title": f"{plan['title']}: {c['label']}", "sub": f"{c['old']} → {c['new']}", "url": url}
                       for c in plan["changes"]])
    return web.json_response({"ok": True})


@routes.post("/api/talk/touch")
async def talk_touch_route(_):
    """Die Oberfläche meldet, dass der Nutzer gerade etwas tut (tippen, scrollen, klicken)."""
    return web.json_response({"ok": True, "active": talk_touch()})


@routes.post("/api/talk/reset")
async def talk_reset(_):
    """Der Nutzer beginnt von sich aus ein neues Gespräch: Das Bisherige wird nicht mehr mitgeschickt."""
    write_prefs({**load_prefs(), "talk_reset": int(time.time())})
    return web.json_response({"ok": True})


@routes.post("/api/devices/add")
async def add_device_route(request):
    """Verbindet ein Gerät, nachdem der Nutzer die Rückfrage bestätigt hat."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    wish = str(body.get("wish") or "")
    request.app["job"] = {"wish": wish, "since": int(time.time())}
    try:
        text = await add_device(request.app["session"], str(body.get("host") or ""), str(body.get("name") or ""),
                                body.get("mqtt") if isinstance(body.get("mqtt"), dict) else None)
    except NeedMqtt as e:
        return web.json_response({"ok": True, "need_mqtt": {"reason": e.reason, "host": e.ha_ip}})
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e) or type(e).__name__}, status=500)
    finally:
        request.app["job"] = None
    add_history("answer", wish, text)
    return web.json_response({"ok": True})


@routes.post("/api/actions/run")
async def run_confirmed_actions(request):
    """Führt Aktionen aus, die der Nutzer ausdrücklich bestätigt hat (Schlösser, Tore, Alarm)."""
    session = request.app["session"]
    try:
        body = await request.json()
    except Exception:
        body = {}
    actions = [a for a in body.get("actions") or [] if isinstance(a, dict)][:12]
    try:
        by_id = {x["entity_id"]: x for x in await all_states(session)}
        error = check_actions(actions, by_id) if actions else "Keine Aktion übergeben."
        if error:
            return web.json_response({"ok": False, "error": error}, status=400)
        await run_actions(session, actions)
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=500)
    add_history("control", str(body.get("wish") or ""), str(body.get("erklaerung") or "Ausgeführt"),
                steps=await action_steps(session, actions, by_id))
    return web.json_response({"ok": True})


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
async def prefs_route(request):
    """Merkt sich weggeklickte Hinweise dauerhaft – für alle Browser und Geräte dieses Hubs."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    prefs = load_prefs()
    if body.get("owner_hint_dismissed") is True:
        prefs["owner_hint_dismissed"] = True
    # Zustimmungen: Zustände für Fragen übertragen; eigene Automationen bearbeiten (und dafür übertragen)
    for key in ("states_allowed", "foreign_automations", "diagnose_allowed", "integrations_allowed", "logs_allowed", "yaml_allowed"):
        if isinstance(body.get(key), bool):
            prefs[key] = body[key]
    if isinstance(body.get("dashboard"), str) and len(body["dashboard"]) <= 100:
        prefs["dashboard"] = body["dashboard"]
    if body.get("mode") in ("auto", "lokal", "cloud", "schnell", "praezise"):
        prefs["mode"] = "cloud" if body["mode"] in ("schnell", "praezise") else body["mode"]   # Auto, Lokal oder Cloud
    write_prefs(prefs)
    if "mode" in body and request.app.get("local"):
        # Haglo Local starten (lädt beim ersten Mal das Modell), wenn Lokal gewählt ist – oder Auto auf einem
        # Gerät mit genug Arbeitsspeicher; sonst Speicher wieder freigeben
        want = local_wanted(prefs.get("mode"))
        if want != bool(local_ai.load_state().get("enabled")):
            local_ai.save_state({**local_ai.load_state(), "enabled": want})
            await (request.app["local"].start() if want else request.app["local"].stop())
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
                    "auto": bool(v.get("auto")),
                    "views": [x.get("title", "") for x in v["config"].get("views", [])],
                    "cards": count_cards(v["config"])})
    return web.json_response({"ok": True, "versions": out, "max": MAX_VERSIONS})


async def restore_version(app, path: Path, keep: bool, log: bool = True) -> None:
    """Spielt eine Version zurück. Mit keep=True wird vorher der aktuelle Stand gesichert,
    damit auch das Wiederherstellen rückgängig gemacht werden kann."""
    version = read_backup(path)
    dashboard = version["dashboard"]
    async with HAClient(app["session"]) as ha:
        path = dashboard or await start_path(ha, create=not version.get("auto"))
        if keep:
            write_backup(await current_config(ha, path), "vor Wiederherstellung", dashboard)
        if dashboard == DASHBOARD_PATH:
            await ensure_dashboard(ha)
        if version.get("auto"):
            # zurück zur automatisch erzeugten Ansicht von Home Assistant
            try:
                await ha.call("lovelace/config/delete", url_path=path)
            except HAError as e:
                if e.code != "config_not_found":
                    raise
            if dashboard == "":
                await reset_start_page(ha)
        else:
            await ha.call("lovelace/config/save", url_path=path, config=version["config"])
            if dashboard == "":
                await show_start_page(app["session"], ha, path)
    if log:
        add_history("restore", "", "Frühere Version wiederhergestellt", url=f"/{path or 'lovelace'}")


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



# ---------------------------------------------------------------- Sprachsteuerung (Alexa & Co. über Matter)
@routes.get("/api/voice")
async def voice_status(request):
    """Stand der Sprachsteuerung und die Geräte, die man freigeben kann."""
    v = request.app["voice"]
    try:
        async with HAClient(request.app["session"]) as ha:
            items = await voice.candidates(ha)
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=500)
    return web.json_response({"ok": True, **v.info(), "items": items})


@routes.post("/api/voice")
async def voice_set(request):
    """Ein-/Ausschalten und Geräteauswahl. Ausgewählte Geräte bekommen in Home Assistant das Label „Sprachsteuerung“."""
    v = request.app["voice"]
    try:
        body = await request.json()
    except Exception:
        body = {}
    try:
        changed = 0
        if isinstance(body.get("entities"), list):
            wanted = {str(e) for e in body["entities"] if isinstance(e, str)}
            async with HAClient(request.app["session"]) as ha:
                allowed = {c["id"] for c in await voice.candidates(ha)}
                changed = await voice.set_selected(ha, wanted & allowed)
        state = voice.load_state()
        if isinstance(body.get("enabled"), bool) and body["enabled"] != bool(state.get("enabled")):
            voice.save_state({**state, "enabled": body["enabled"]})
            if body["enabled"]:
                await v.start()
                add_history("answer", "Sprachsteuerung eingeschaltet", "Sprachsteuerung ist an. Jetzt in der Alexa-App koppeln.")
            else:
                await v.stop()
                add_history("answer", "Sprachsteuerung ausgeschaltet", "Sprachsteuerung ist aus. Alexa sieht die Geräte nicht mehr.")
        elif changed and state.get("enabled"):
            await v.restart()   # neu freigegebene Geräte erscheinen nach dem Neustart in Alexa
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=500)
    return web.json_response({"ok": True, "changed": changed, **v.info()})


@routes.post("/api/voice/reset")
async def voice_reset(request):
    """Kopplung löschen (z. B. Alexa-Konto gewechselt) – danach gibt es einen neuen Kopplungscode."""
    try:
        await request.app["voice"].reset_pairing()
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=500)
    return web.json_response({"ok": True, **request.app["voice"].info()})


# ---------------------------------------------------------------- Haglo Assist als Assistent in Home Assistant
# Die App legt die Integration „haglo_assist“ in /config/custom_components an. Dort ist Haglo Assist unter
# Einstellungen → Sprachassistenten als Konversationsagent wählbar (Text, Sprache, Companion-App, Satelliten).
# Die Integration ruft /api/agent/conversation mit einem geheimen Token – sonst bleibt die App nur über Ingress erreichbar.
AGENT_SRC = Path(__file__).parent / "ha_integration" / "haglo_assist"
AGENT_DST = Path("/homeassistant/custom_components/haglo_assist")
AGENT_TOKEN_FILE = Path("/data/agent_token")
AGENT_PENDING: dict[str, dict] = {}          # conversation_id → wartende Bestätigung oder Rückfrage
RE_YES = re.compile(r"^\W*(ja|jo|jep|jawohl|genau|klar|ok|okay|mach|mach das|gerne|bitte|passt|yes)\b", re.I)
RE_NO = re.compile(r"^\W*(nein|nee|ne|nö|lieber nicht|abbrechen|stopp?|no)\b", re.I)


def agent_token() -> str:
    try:
        return AGENT_TOKEN_FILE.read_text().strip()
    except OSError:
        tok = secrets.token_urlsafe(32)
        AGENT_TOKEN_FILE.write_text(tok)
        return tok


class _Req:
    """Aufruf eines eigenen Handlers ohne HTTP (gleiche Logik wie in der Oberfläche)."""
    def __init__(self, app, body):
        self.app, self._body = app, body

    async def json(self):
        return self._body


async def _call(handler, app, body) -> dict:
    resp = await handler(_Req(app, body))
    try:
        return json.loads(resp.text or "{}")
    except Exception:
        return {}


def _speech_of(data: dict) -> str:
    """Antwort der App → ein gesprochener Satz (ohne Markdown)."""
    text = str(data.get("done") or data.get("answer") or data.get("erklaerung") or "")
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)          # Links: nur der Text
    return re.sub(r"[*_`#>]+", "", text).strip()


async def agent_reply(app, text: str, cid: str) -> dict:
    pend = AGENT_PENDING.pop(cid, None)
    if pend and pend["type"] in ("objects", "actions"):
        if RE_NO.search(text):
            return {"speech": "Okay, ich mache nichts."}
        if RE_YES.search(text):
            if pend["type"] == "objects":
                r = await _call(apply_objects, app, {"automations": pend["objects"], "wish": pend["wish"]})
                names = ", ".join(d["alias"] for d in r.get("created") or [])
                return {"speech": f"Angelegt: {names}." if r.get("ok") else f"Das hat nicht geklappt: {r.get('error')}"}
            r = await _call(run_confirmed_actions, app, {"actions": pend["actions"], "wish": pend["wish"],
                                                         "erklaerung": pend["text"]})
            return {"speech": "Erledigt." if r.get("ok") else f"Das hat nicht geklappt: {r.get('error')}"}
        # weder ja noch nein: neuer Wunsch – die Bestätigung verfällt
    body = {"wish": text}
    if pend and pend["type"] == "question":
        body = {"wish": pend["wish"], "dialog": pend["dialog"] + [{"frage": pend["frage"], "antwort": text}]}
    data = await _call(generate, app, body)
    wish = body["wish"]
    if data.get("question"):
        q = data["question"]
        opts = [str(o) for o in q.get("optionen") or []][:4]
        AGENT_PENDING[cid] = {"type": "question", "wish": wish, "frage": q.get("frage", ""), "dialog": body.get("dialog") or []}
        return {"speech": q.get("frage", "") + (f" Zum Beispiel: {', '.join(opts)}." if opts else ""), "continue": True}
    if data.get("objects"):
        AGENT_PENDING[cid] = {"type": "objects", "wish": wish, "objects": data["objects"]}
        intro = _speech_of(data) or "Ich würde das so anlegen."
        if "anlegen?" not in intro:
            intro += " Soll ich das anlegen?"
        return {"speech": intro + " Sag ja oder nein.", "continue": True}
    if data.get("confirm_actions"):
        AGENT_PENDING[cid] = {"type": "actions", "wish": wish, "actions": data["confirm_actions"], "text": _speech_of(data)}
        return {"speech": (_speech_of(data) or "Das ist eine sicherheitsrelevante Aktion.") + " Soll ich das wirklich machen? Sag ja oder nein.",
                "continue": True}
    if any(k in data for k in ("need_consent", "need_diagnose_consent", "need_logs_consent", "need_yaml_consent", "need_probe_consent",
                               "need_integrations_consent", "need_history_consent", "need_dashboard_consent",
                               "confirm_repair", "confirm_integration", "confirm_device")):
        return {"speech": "Dafür brauche ich einmal deine Freigabe in der App Haglo Assist. Öffne sie in der Seitenleiste "
                          "und stell die Frage dort."}
    if not data.get("ok", True) or data.get("error"):
        err = data.get("error")
        if err == "consent_required":
            if data.get("terms_update"):
                return {"speech": "Es gibt neue Nutzungsbedingungen. Bitte öffne einmal die App Haglo Assist und stimm zu."}
            return {"speech": "Bitte öffne einmal die App Haglo Assist und stimm den Bedingungen zu."}
        if err == "trial_expired":
            return {"speech": "Dein Kontingent ist gerade aufgebraucht. Details stehen in der App Haglo Assist."}
        return {"speech": f"Das hat nicht geklappt: {err}"}
    return {"speech": _speech_of(data) or "Erledigt."}


@routes.post("/api/agent/conversation")
async def agent_conversation(request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    text = str(body.get("text") or "").strip()[:500]
    cid = str(body.get("conversation_id") or "")[:80]
    if not text:
        return web.json_response({"speech": "Ich habe nichts verstanden."})
    try:
        out = await agent_reply(request.app, text, cid)
    except Exception as e:
        print(f"[Assistent] Fehler: {e}", flush=True)
        out = {"speech": f"Da ist etwas schiefgelaufen: {str(e)[:120]}"}
    print(f"[Assistent] {text!r} → {out['speech'][:160]!r}", flush=True)
    return web.json_response(out)


AGENT_READY = {"notification_id": "haglo_assist_agent", "title": "Haglo Assist als Assistent",
               "message": "Haglo Assist ist jetzt als Assistent verfügbar: **Einstellungen → Sprachassistenten** – beim "
                          "Assistenten (oder „Assistent hinzufügen“) als *Konversationsagent* „Haglo Assist“ wählen."}


async def install_agent(app):
    """Integration anlegen/aktualisieren und – sobald Home Assistant sie kennt – einrichten."""
    if not Path("/homeassistant").is_dir() or not AGENT_SRC.is_dir():
        return
    try:
        info = await supervisor_get(app["session"], "/addons/self/info")
        host = "127.0.0.1" if info.get("host_network") else (info.get("hostname") or info.get("ip_address"))
        conn = {"url": f"http://{host}:8099", "token": agent_token()}
        fresh = not (AGENT_DST / "manifest.json").exists()
        AGENT_DST.mkdir(parents=True, exist_ok=True)
        changed = False
        for src in AGENT_SRC.rglob("*"):
            if src.is_file() and "__pycache__" not in src.parts:
                dst = AGENT_DST / src.relative_to(AGENT_SRC)
                dst.parent.mkdir(parents=True, exist_ok=True)
                if not dst.exists() or dst.read_bytes() != src.read_bytes():
                    dst.write_bytes(src.read_bytes())
                    changed = True
        conn_file = AGENT_DST / "connection.json"
        if not conn_file.exists() or json.loads(conn_file.read_text() or "{}") != conn:
            conn_file.write_text(json.dumps(conn))
        session = app["session"]
        status, entries = await core(session, "GET", "/config/config_entries/entry?domain=haglo_assist")
        if status == 200 and not entries:
            st, flow = await core(session, "POST", "/config/config_entries/flow", {"handler": "haglo_assist"})
            if st != 200 and not app.get("agent_retry"):
                # Home Assistant kennt die Integration erst nach einem Neustart – bis dahin jede Minute nachsehen
                async def retry():
                    while True:
                        await asyncio.sleep(60)
                        s2, e2 = await core(session, "GET", "/config/config_entries/entry?domain=haglo_assist")
                        if s2 == 200 and e2:
                            return
                        s3, f3 = await core(session, "POST", "/config/config_entries/flow", {"handler": "haglo_assist"})
                        if s3 == 200 and f3.get("type") == "create_entry":
                            print("[Assistent] Integration eingerichtet", flush=True)
                            await core(session, "POST", "/services/persistent_notification/create", AGENT_READY)
                            return
                app["agent_retry"] = asyncio.create_task(retry())
            if st == 200 and flow.get("type") == "create_entry":
                print("[Assistent] Integration eingerichtet – unter Einstellungen → Sprachassistenten auswählbar", flush=True)
                await core(session, "POST", "/services/persistent_notification/create", AGENT_READY)
            elif fresh or changed:
                print("[Assistent] Integration abgelegt – Home Assistant einmal neu starten", flush=True)
                await core(session, "POST", "/services/persistent_notification/create", {
                    "notification_id": "haglo_assist_agent", "title": "Haglo Assist als Assistent",
                    "message": "Damit du Haglo Assist auch als Sprach- und Textassistent auswählen kannst, starte Home "
                               "Assistant einmal neu (Einstellungen → System → Neu starten)."})
    except Exception as e:
        print(f"[Assistent] Einrichtung übersprungen: {e}", flush=True)


# ---------------------------------------------------------------- Haglo Local (eigenes Modell auf dem Hub)
def ram_gb() -> float:
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1024 ** 3
    except (ValueError, OSError):
        return 0


# Was das Image mitbringt: Die schlanke Ausgabe (main) hat weder llama.cpp noch Matterbridge – dann gibt es kein
# Sprachmodell (die Regeln laufen trotzdem) und keine Alexa-Sprachsteuerung; die Oberfläche blendet beides aus.
FEATURES = {"model": shutil.which("llama-server") is not None, "voice": shutil.which("matterbridge") is not None}


def local_wanted(mode: str | None) -> bool:
    """Modell laden? Bei „Lokal“ immer, bei „Auto“ ab 3 GB Arbeitsspeicher (Wyse 5070 / T630 mit 8 GB)."""
    if not FEATURES["model"]:
        return False
    return mode == "lokal" or ((mode or "auto") == "auto" and ram_gb() >= 3)


async def area_map(app, force: bool = False) -> dict[str, str]:
    """entity_id → Raumname. Zwischengespeichert, nach einer Minute im Hintergrund aufgefrischt."""
    cached = app.get("areas_cache")
    if cached and not force and time.time() - cached[0] < 600:
        if time.time() - cached[0] > 60 and not app.get("areas_refresh"):
            # im Hintergrund auffrischen – der Befehl wartet nicht darauf
            async def refresh():
                try:
                    await area_map(app, force=True)
                except Exception:
                    pass
                finally:
                    app["areas_refresh"] = None
            app["areas_refresh"] = asyncio.create_task(refresh())
        return cached[1]
    async with HAClient(app["session"]) as ha:
        ents = await ha.call("config/entity_registry/list") or []
        devs = {d["id"]: d for d in await ha.call("config/device_registry/list") or []}
        names = {a["area_id"]: a["name"] for a in await ha.call("config/area_registry/list") or []}
    app["registry_cache"] = {e["entity_id"]: e for e in ents}
    out = {}
    for e in ents:
        aid = e.get("area_id") or (devs.get(e.get("device_id") or "") or {}).get("area_id")
        if aid in names:
            out[e["entity_id"]] = names[aid]
    app["areas_cache"] = (time.time(), out)
    return out


# Timer von Haglo Local („in 10 Minuten Licht aus“). Liegen in /data, damit sie einen Neustart überstehen.
TIMERS_FILE = Path("/data/timers.json")


def load_timers() -> dict:
    try:
        return json.loads(TIMERS_FILE.read_text())
    except Exception:
        return {}


def save_timers(timers: dict) -> None:
    TIMERS_FILE.write_text(json.dumps(timers, ensure_ascii=False))


async def run_timer(app, tid: str):
    t = load_timers().get(tid)
    if not t:
        return
    await asyncio.sleep(max(0, t["at"] - time.time()))
    timers = load_timers()
    if tid not in timers:
        return              # inzwischen zurückgenommen
    timers.pop(tid)
    save_timers(timers)
    try:
        await run_actions(app["session"], t["actions"])
        print(f"[Timer] ausgeführt: {t['text']}", flush=True)
    except Exception as e:
        print(f"[Timer] fehlgeschlagen: {t['text']} – {e}", flush=True)
    finally:
        app.setdefault("timer_tasks", {}).pop(tid, None)


def start_timer(app, actions: list, seconds: int, text: str) -> str:
    tid = f"t{int(time.time() * 1000)}"
    timers = load_timers()
    timers[tid] = {"at": time.time() + seconds, "actions": actions, "text": text}
    save_timers(timers)
    app.setdefault("timer_tasks", {})[tid] = asyncio.create_task(run_timer(app, tid))
    return tid


def cancel_timer(app, tid: str) -> None:
    timers = load_timers()
    timers.pop(tid, None)
    save_timers(timers)
    task = app.setdefault("timer_tasks", {}).pop(tid, None)
    if task:
        task.cancel()


def resume_timers(app) -> None:
    """Nach einem Neustart: offene Timer weiterlaufen lassen. Bis 10 Minuten verpasste holen wir nach,
    ältere verfallen (nachts um drei soll nicht plötzlich das Licht angehen)."""
    timers = load_timers()
    stale = [k for k, t in timers.items() if t["at"] < time.time() - 600]
    for k in stale:
        timers.pop(k)
    if stale:
        save_timers(timers)
    for tid in timers:
        app.setdefault("timer_tasks", {})[tid] = asyncio.create_task(run_timer(app, tid))


def user_mode() -> str:
    """Auto, Lokal oder Cloud. Frühere Wahl „Schnell“/„Präzise“ heißt jetzt Cloud (Stufe wählt die App)."""
    m = load_prefs().get("mode") or "auto"
    return "cloud" if m in ("schnell", "praezise") else m if m in ("auto", "lokal", "cloud") else "auto"


async def local_answer(app, wish: str, use_model: bool = True, code_only: bool = False) -> dict | None:
    loc = app.get("local")
    if not loc:
        return None
    t0 = time.time()
    # Sicherheit: Wenn-dann-Wünsche, Zeitpläne, Automationen und Dashboards schaltet der Schnellweg nie sofort.
    # Das Modell darf sie planen (Timer, Automation mit Bestätigung, neues Dashboard mit Rückgängig);
    # Fehlersuche und alles andere Komplexe geht an Claude.
    if is_complex(wish) and not (local_ai.PLAN.search(wish) or local_ai.DASH_CUE.search(wish) or local_ai.time_cue(wish)):
        print(f"[Lokal] komplex → nicht lokal | {wish!r}", flush=True)
        return None
    if code_only and local_ai.DASH_CUE.search(wish):
        return None             # Cloud: Dashboards baut Claude (schöner als der Baukasten)
    session = app["session"]
    if app.get("areas_cache") or local_ai.PLAN.search(wish):
        # Planwünsche (Timer, Automation, Dashboard) brauchen die Räume – darauf lohnt sich das Warten
        states, areas = await asyncio.gather(all_states(session), area_map(app))
    else:
        # Raumliste noch nicht da (gerade gestartet): nicht darauf warten – Namen reichen fürs Schalten
        if not app.get("areas_refresh"):
            app["areas_refresh"] = asyncio.create_task(area_map(app, force=True))
            app["areas_refresh"].add_done_callback(lambda _t: app.__setitem__("areas_refresh", None))
        states, areas = await all_states(session), {}
    res = await loc.understand(wish, states, areas, use_model=use_model)
    if not res:
        return None
    if res["kind"] in ("answer", "question"):
        ms = int((time.time() - t0) * 1000)
        add_history("answer", wish, res["text"], local=ms)
        return {"answer": res["text"], "local": ms}
    by_id = {x["entity_id"]: x for x in states}
    if res["kind"] == "automation":
        obj = res["object"]
        if check_actions(obj["actions"], by_id):
            print(f"[Lokal] Automation abgelehnt ({check_actions(obj['actions'], by_id)}) | {wish!r}", flush=True)
            return None
        # gleiche Bestätigungskarte wie bei Claude – angelegt wird erst nach „Ja“
        return {"objects": [obj], "erklaerung": res["text"], "local": int((time.time() - t0) * 1000)}
    if res["kind"] == "dashboard":
        title, cfg = local_ai.dashboard_config(res["plan"], states, areas, app.get("registry_cache"))
        if not cfg.get("views"):
            print(f"[Lokal] Dashboard-Plan ohne passende Geräte → Cloud | {wish!r}", flush=True)
            return None
        cards = sum(len(sec["cards"]) - 1 for sec in cfg["views"][0]["sections"])
        async with HAClient(session) as ha:
            made = await create_sidebar_dashboard(ha, wish, {
                "neues_dashboard": {"titel": title}, "config": cfg, "status": None,
                "erklaerung": f"Neues Dashboard „{title}“ mit {cards} Karten, nach Räumen sortiert."})
        ms = int((time.time() - t0) * 1000)
        print(f"[Lokal] Dashboard „{title}“ ({cards} Karten) in {ms} ms | {wish!r}", flush=True)
        return {"erklaerung": made["erklaerung"], "dashboard_url": made["dashboard_url"], "local": ms}
    acts = res["actions"]
    if check_actions(acts, by_id):
        print(f"[Lokal] Aktion abgelehnt ({check_actions(acts, by_id)}) | {wish!r}", flush=True)
        return None
    if any(risky_action(a, by_id) for a in acts):
        if res["kind"] == "timer":
            return None         # Riskantes (Tore, Schlösser) nie zeitversetzt ohne Bestätigung im Moment
        return {"confirm_actions": acts, "erklaerung": res["text"]}
    if res["kind"] == "timer":
        if res.get("now"):
            if check_actions(res["now"], by_id) or any(risky_action(x, by_id) for x in res["now"]):
                return None
            await run_actions(session, res["now"])          # „für 10 Minuten an“: jetzt an, Timer schaltet zurück
        tid = start_timer(app, acts, res["seconds"], "In " + res["text"].split(" In ", 1)[-1] if res.get("now") else res["text"])
        ms = int((time.time() - t0) * 1000)
        print(f"[Lokal] Timer {res['seconds']} s gestellt in {ms} ms | {wish!r}", flush=True)
        add_history("control", wish, f"⏱ {res['text']}", local=ms, undo={"type": "timer", "id": tid})
        return {"done": res["text"], "local": ms}
    await run_actions(session, acts)
    ms = int((time.time() - t0) * 1000)        # ehrliche Zeit: vom Befehl bis Home Assistant geschaltet hat
    print(f"[Lokal] gesamt {ms} ms (erkannt in {res['ms']} ms) | {wish!r}", flush=True)
    add_history("control", wish, res["text"], steps=await action_steps(session, acts, by_id, app.get("registry_cache")), local=ms)
    return {"done": res["text"], "local": ms}


@routes.get("/api/local")
async def local_status(request):
    return web.json_response({"ok": True, **request.app["local"].info()})


@routes.post("/api/local")
async def local_set(request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    loc = request.app["local"]
    if body.get("enabled") is True and not FEATURES["model"]:
        return web.json_response({"ok": False, "error": "Das Sprachmodell ist in dieser Ausgabe nicht enthalten."}, status=400)
    if isinstance(body.get("enabled"), bool):
        local_ai.save_state({**local_ai.load_state(), "enabled": body["enabled"]})
        prefs = load_prefs()
        if body["enabled"] or prefs.get("mode") == "lokal":
            write_prefs({**prefs, "mode": "lokal" if body["enabled"] else "auto"})
        if body["enabled"]:
            await loc.start()
        else:
            await loc.stop()
    return web.json_response({"ok": True, **loc.info()})


# Mit Host-Netzwerk (nötig für Matter) wäre Port 8099 im ganzen Heimnetz offen. Die Oberfläche
# darf aber nur über Home Assistant (Ingress, 172.30.32.2) erreichbar sein.
INGRESS_IPS = {"172.30.32.2"}                # nur der Ingress-Proxy von Home Assistant


@web.middleware
async def only_ingress(request, handler):
    if request.path == "/api/agent/conversation" and secrets.compare_digest(
            request.headers.get("Authorization", ""), f"Bearer {agent_token()}"):
        return await handler(request)            # Haglo-Integration in Home Assistant (Token, siehe install_agent)
    if request.remote not in INGRESS_IPS:
        return web.Response(status=403, text="Haglo Assist ist nur über Home Assistant erreichbar.")
    return await handler(request)


START_DELAY = 45          # Sekunden nach dem Start, bevor Modell und Matterbridge laden


async def on_startup(app):
    app["session"] = aiohttp.ClientSession()
    app["bar_lock"] = asyncio.Lock()
    app["bar_enabled"] = bool(load_options().get("dashboard_bar"))
    print("Erster Start:", await first_start_setup(app["session"]))
    try:
        await ensure_registered(app["session"])
        await identify(app, (await backend(app["session"], "GET", "/v1/status"))["status"])
    except BackendError as e:
        print(f"Registrierung beim Haglo-Server noch nicht möglich: {e}")
    if app["bar_enabled"]:
        app["bar_task"] = asyncio.create_task(bar_listener(app))
    asyncio.create_task(selftest(app))
    app["local"] = local_ai.LocalAI(app["session"])
    resume_timers(app)
    asyncio.create_task(install_agent(app))
    asyncio.create_task(area_map(app))          # Raumliste vorab laden – der erste Befehl ist dann schon schnell
    app["voice"] = voice.Voice(app["session"])
    if FEATURES["voice"]:
        try:
            await app["voice"].start_proxy()
        except Exception as e:
            print(f"[Sprache] Weiterleitung fehlgeschlagen: {e}")

    async def later():
        # Modell und Matterbridge erst nach dem Start: beide belasten CPU und Home Assistant spürbar.
        # In der Zeit soll Schalten (Schnellweg) sofort reagieren.
        await asyncio.sleep(START_DELAY)
        try:
            want = local_wanted(load_prefs().get("mode"))
            local_ai.save_state({**local_ai.load_state(), "enabled": want})
            if want:
                await app["local"].start()
            if FEATURES["voice"] and voice.load_state().get("enabled"):
                await app["voice"].start()
        except Exception as e:
            print(f"[Start] {e}")
    app["later_task"] = asyncio.create_task(later())


async def on_cleanup(app):
    if app.get("later_task"):
        app["later_task"].cancel()
    if app.get("local"):
        await app["local"].stop()
    if app.get("voice"):
        await app["voice"].stop()
        if app["voice"].runner:
            await app["voice"].runner.cleanup()
    if app.get("bar_task"):
        app["bar_task"].cancel()
    await app["session"].close()


def make_app() -> web.Application:
    app = web.Application(middlewares=[only_ingress])
    app.add_routes(routes)
    app.router.add_static("/static/", STATIC_DIR)
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    return app


if __name__ == "__main__":
    web.run_app(make_app(), host="0.0.0.0", port=8099)
