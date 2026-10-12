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

ADDON_VERSION = "0.18.8"
MAX_VERSIONS = 30                      # so viele Dashboard-Versionen bleiben lokal gespeichert
BAR_ENTITY = "input_text.haglo_assist"           # Eingabeleiste im Dashboard
BAR_STATUS = "input_text.haglo_assist_status"    # Rückmeldung darunter
DEFAULT_BACKEND_URL = "https://api.haglotech.com"
HUB_FILE = Path("/data/hub.json")
TERMS_VERSION = "2026-10"
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
                if r.status != 200:
                    return f"Home Assistant noch nicht bereit ({r.status})"
                steps = await r.json()
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
        if core_data.get("default_panel") in (None, "lovelace") and "default_panel_before" not in prefs:
            prefs["default_panel_before"] = core_data.get("default_panel") or "lovelace"
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
                await ha.call("frontend/set_system_data", key="core",
                              value={**core_data, "default_panel": prefs["default_panel_before"]})
            await ha.call("call_service", domain="persistent_notification", service="dismiss",
                          service_data={"notification_id": WELCOME_ID})
    except Exception as e:
        print(f"Startseite zurückstellen fehlgeschlagen: {e}")
        return f"FEHLER {type(e).__name__}: {str(e)[:80]}"
    prefs.pop("default_panel_before", None)
    prefs.pop("default_panel_since", None)
    write_prefs(prefs)
    return "ok"


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
        out = []
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
    elif job.get("full"):
        await step("alles", everything())
    elif job.get("auto"):
        await step("automation-ablauf", automation_flow())
    else:
        await step("automation", automation_api())
    if not any(job.get(k) for k in ("full", "probe", "sidebar", "welcome", "undo", "dashlist")):
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
        + [d for d in listed if d.get("url_path") not in (DASHBOARD_PATH, "lovelace", "", None)]
    items.append(next((d for d in listed if d.get("url_path") == DASHBOARD_PATH),
                      {"url_path": DASHBOARD_PATH, "title": DASHBOARD_TITLE, "mode": "storage"}))
    out, seen = [], {}
    for d in items:
        path = d.get("url_path") or ""
        editable = d.get("mode", "storage") == "storage"
        try:
            cfg = await current_config(ha, path or None) if editable else None
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
             if d.get("url_path") not in ("lovelace", "", None) and d.get("mode", "storage") == "storage"]
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


def add_history(kind: str, wish: str, text: str, **extra) -> str:
    """Merkt sich, was gemacht wurde – das ist der Gesprächsverlauf in der Oberfläche. Bleibt auf dem
    Gerät. `undo` beschreibt, wie sich der Eintrag zurücknehmen lässt:
    {"type": "version", "id": <Datei>} oder {"type": "objects", "items": [{"id", "backup"}]}."""
    items = load_history()
    hid = f"{int(time.time() * 1000)}"
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


async def core(session, method: str, path: str, body=None):
    """Aufruf der Home-Assistant-API. Gibt (Status, Antwort) zurück."""
    async with session.request(method, CORE_API + path, json=body, headers={"Authorization": f"Bearer {TOKEN}"},
                               timeout=aiohttp.ClientTimeout(total=30)) as r:
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


# ---------------------------------------------------------------- Dashboard bauen
async def build_dashboard(app, wish: str, refine: bool, dialog: list, can_ask: bool,
                          dashboard: str | None = DASHBOARD_PATH, can_automate: bool = False, allow_states: bool = False) -> dict:
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
        path = dashboard or None
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
            extra = {"kinds": KINDS, "can_control": True, "automations": await own_objects(session, raw)}
        payload = {
            "wish": wish, "entities": entities,
            "previous": None if rebuild or locked else (strip_bar(before) if own else before),
            "refine": not rebuild, "foreign": not rebuild and not built_by_haglo, "dialog": dialog, "can_ask": can_ask,
            "dashboard_locked": locked,
            **extra,
        }
        result = await backend(session, "POST", "/v1/generate", payload, timeout=300)
        if locked and result.get("type") not in ("objects", "actions", "answer", "need_states", "question"):
            # Der Wunsch betrifft das Dashboard: Die Oberfläche fragt mit einer Karte nach der Freigabe.
            return {"need_dashboard_consent": {"id": dashboard, "label": label}, "status": result.get("status")}
        if result.get("type") == "need_states" and can_automate:
            # Die KI braucht aktuelle Zustände. Die verlassen das Gerät nur mit Zustimmung des Nutzers –
            # und nur für genau die Geräte, nach denen gefragt wurde.
            wanted = [e for e in result.get("entities", []) if e in by_id][:30]
            if not (allow_states or load_prefs().get("states_allowed")):
                return {"need_consent": [{"id": e, "name": by_id[e].get("attributes", {}).get("friendly_name", e)} for e in wanted],
                        "status": result["status"]}
            payload["states"] = {e: state_text(by_id[e]) for e in wanted}
            result = await backend(session, "POST", "/v1/generate", payload, timeout=300)
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
            add_history("control", wish, result.get("erklaerung", ""))
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
        backup = write_backup(before, f"vor: {wish}", dashboard)
        await ha.call("lovelace/config/save", url_path=path, config=config)
        if not own and rebuild:
            # Von Haglo Assist gebaut: darf künftig ohne Rückfrage weiterbearbeitet werden
            prefs = load_prefs()
            prefs["built"] = sorted(set(prefs.get("built", [])) | {dashboard})
            write_prefs(prefs)
            ALLOWED_FILE.parent.mkdir(parents=True, exist_ok=True)
            ALLOWED_FILE.write_text(json.dumps(sorted(allowed_dashboards() | {dashboard})))
    add_history("dashboard", wish, result.get("erklaerung", ""), url=f"/{dashboard or 'lovelace'}", target=label,
                undo={"type": "version", "id": backup} if backup else None)
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
    if not load_prefs().get("first_start_done"):
        print("Einrichtung:", await first_start_setup(session))
    since = load_prefs().get("default_panel_since")
    if since and time.time() - since > 14 * 86400:
        await welcome_done(session)
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
        # Woran gearbeitet wird: die Wahl des Nutzers, sonst das vorhandene Haglo Dashboard, sonst die Startseite
        "dashboard": load_prefs().get("dashboard", DASHBOARD_PATH if cfg is not None else ""),
        "api_key": True,  # Der Schlüssel liegt im Backend; Feld bleibt für die Oberfläche.
        "backups": len(version_files()),
        "prefs": load_prefs(),
        "features": {"automations": True, "control": True},
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
    try:
        result = await build_dashboard(request.app, wish, refine, dialog, can_ask=True, dashboard=dashboard,
                                       can_automate=True, allow_states=bool(body.get("allow_states")))
    except BackendError as e:
        return backend_failure(e)
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=500)
    finally:
        request.app["job"] = None
    for key in ("objects", "confirm_actions", "need_consent", "need_dashboard_consent", "done", "answer"):
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


@routes.get("/api/history")
async def history(_):
    """Der Gesprächsverlauf (lokal gespeichert), älteste zuerst."""
    reg = load_automations()
    items = [{**{k: v for k, v in h.items() if k != "undo"}, "id": h.get("id") or str(h.get("ts")),
              "can_undo": can_undo(h, reg)} for h in load_history()]
    return web.json_response({"ok": True, "history": items})


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
    add_history("control", str(body.get("wish") or ""), str(body.get("erklaerung") or "Ausgeführt"))
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
    for key in ("states_allowed", "foreign_automations"):
        if isinstance(body.get(key), bool):
            prefs[key] = body[key]
    if isinstance(body.get("dashboard"), str) and len(body["dashboard"]) <= 100:
        prefs["dashboard"] = body["dashboard"]
    write_prefs(prefs)
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
        if keep:
            write_backup(await current_config(ha, dashboard or None), "vor Wiederherstellung", dashboard)
        if dashboard == DASHBOARD_PATH:
            await ensure_dashboard(ha)
        if version.get("auto"):
            # zurück zur automatisch erzeugten Ansicht von Home Assistant
            try:
                await ha.call("lovelace/config/delete", url_path=dashboard or None)
            except HAError as e:
                if e.code != "config_not_found":
                    raise
        else:
            await ha.call("lovelace/config/save", url_path=dashboard or None, config=version["config"])
    if log:
        add_history("restore", "", "Frühere Version wiederhergestellt", url=f"/{dashboard or 'lovelace'}")


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
    print("Erster Start:", await first_start_setup(app["session"]))
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
