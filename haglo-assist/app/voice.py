"""Sprachsteuerung – macht ausgewählte Geräte per Matter für Alexa (und Google Home / Apple Home) sichtbar.

Im Add-on läuft dafür Matterbridge mit dem Home-Assistant-Plugin (matterbridge-hass), aber nur,
wenn der Kunde die Sprachsteuerung einschaltet. Welche Geräte sichtbar sind, steht in Home Assistant
selbst: als Label „Sprachsteuerung“ an der Entität. Die Oberfläche setzt und entfernt dieses Label;
der Kunde kann es genauso in Home Assistant pflegen.

Matterbridge spricht nicht direkt mit Home Assistant, sondern über einen kleinen WebSocket-Weiterleiter
hier im Add-on (nur 127.0.0.1). Der meldet sich mit dem Supervisor-Token an – einen langlebigen
Zugangsschlüssel des Kunden braucht es dadurch nicht.
"""

import asyncio
import json
import os
import re
import secrets
from pathlib import Path

import aiohttp
from aiohttp import web

VOICE_DIR = Path("/data/matter")                 # Speicher von Matterbridge (Kopplung, Geräte)
STATE_FILE = Path("/data/voice.json")            # {"enabled": bool}
LABEL = "Sprachsteuerung"
LABEL_ICON = "mdi:microphone-message"
PROXY_HOST, PROXY_PORT = "127.0.0.1", 18124      # Weiterleiter zu Home Assistant
FRONTEND_PORT = 18283                            # Matterbridge-Oberfläche, nur lokal, wird nicht genutzt
MATTER_PORT = 5550                               # nicht 5540: den nutzt der Matter-Server von Home Assistant
HA_WS_URL = "ws://supervisor/core/websocket"
TOKEN = os.environ.get("SUPERVISOR_TOKEN", "")
PLUGIN = "matterbridge-hass"

# Was Alexa schalten kann. Sensoren bleiben bewusst draußen – sonst wird die Geräteliste in Alexa unübersichtlich.
DOMAINS = ["light", "switch", "fan", "cover", "lock", "climate", "valve", "vacuum",
           "script", "scene", "input_boolean", "input_button", "automation"]
DOMAIN_NAMES = {"light": "Licht", "switch": "Schalter", "fan": "Ventilator", "cover": "Rollladen", "lock": "Schloss",
                "climate": "Heizung/Klima", "valve": "Ventil", "vacuum": "Saugroboter", "script": "Skript",
                "scene": "Szene", "input_boolean": "Schalter-Helfer", "input_button": "Taster-Helfer",
                "automation": "Automation"}

ANSI = re.compile(r"\x1b\[[0-9;]*m")
RE_MANUAL = re.compile(r"Manual pairing code (\d{4}-\d{3}-\d{4}|\d{11}|\d{4}-\d{3}-\d{4}-\d{4}-\d{3}-\d{4})")
RE_QR = re.compile(r"data=(MT:[A-Z0-9.\-]+)")


def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state))


class Voice:
    """Startet/stoppt Matterbridge und merkt sich, was es meldet (Kopplungscode, gekoppelt ja/nein)."""

    def __init__(self, session: aiohttp.ClientSession):
        self.session = session
        self.secret = secrets.token_urlsafe(32)   # nur Matterbridge kennt ihn; neu bei jedem Start des Add-ons
        self.proc: asyncio.subprocess.Process | None = None
        self.task: asyncio.Task | None = None
        self.runner: web.AppRunner | None = None
        self.lock = asyncio.Lock()
        self.manual = ""
        self.qr = ""
        self.paired = False
        self.running = False
        self.error = ""
        self.log: list[str] = []

    # ------------------------------------------------------------ Weiterleiter zu Home Assistant
    async def start_proxy(self):
        app = web.Application()
        app.router.add_get("/api/websocket", self.proxy)
        self.runner = web.AppRunner(app, access_log=None)
        await self.runner.setup()
        await web.TCPSite(self.runner, PROXY_HOST, PROXY_PORT).start()

    async def proxy(self, request):
        client = web.WebSocketResponse(heartbeat=30, max_msg_size=0)
        await client.prepare(request)
        try:
            async with self.session.ws_connect(HA_WS_URL, heartbeat=30, max_msg_size=0) as up:
                hello = await up.receive_json()                                  # auth_required
                await client.send_json(hello)
                auth = await client.receive_json(timeout=20)
                if auth.get("type") != "auth" or not secrets.compare_digest(str(auth.get("access_token", "")), self.secret):
                    await client.send_json({"type": "auth_invalid", "message": "Ungültiger Zugang"})
                    return client
                await up.send_json({"type": "auth", "access_token": TOKEN})
                ok = await up.receive_json()
                await client.send_json(ok)
                if ok.get("type") != "auth_ok":
                    return client

                async def pipe(src, dst):
                    async for msg in src:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            await dst.send_str(msg.data)
                        elif msg.type == aiohttp.WSMsgType.BINARY:
                            await dst.send_bytes(msg.data)
                        else:
                            break

                a = asyncio.create_task(pipe(client, up))
                b = asyncio.create_task(pipe(up, client))
                done, pending = await asyncio.wait({a, b}, return_when=asyncio.FIRST_COMPLETED)
                for t in pending:
                    t.cancel()
        except Exception as e:
            print(f"[Sprache] Weiterleitung zu Home Assistant beendet: {e}")
        finally:
            await client.close()
        return client

    # ------------------------------------------------------------ Matterbridge
    def config_file(self) -> Path:
        return VOICE_DIR / ".matterbridge" / f"{PLUGIN}.config.json"

    def write_config(self):
        f = self.config_file()
        f.parent.mkdir(parents=True, exist_ok=True)
        try:
            cfg = json.loads(f.read_text())
        except Exception:
            cfg = {}
        cfg.update({
            "name": PLUGIN, "type": "DynamicPlatform",
            "host": f"ws://{PROXY_HOST}:{PROXY_PORT}", "token": self.secret, "rejectUnauthorized": False,
            "reconnectTimeout": 30, "reconnectRetries": 1000,
            "filterByArea": "", "filterByLabel": LABEL,
            "whiteList": [], "blackList": [], "entityWhiteList": DOMAINS, "entityBlackList": [],
            "discardHiddenEntities": False, "unregisterOnShutdown": False, "debug": False,
        })
        f.write_text(json.dumps(cfg, indent=2))

    async def _cli(self, *args, timeout=90) -> str:
        p = await asyncio.create_subprocess_exec("matterbridge", *args, "--homedir", str(VOICE_DIR), "--no-ansi",
                                                 stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        try:
            out, _ = await asyncio.wait_for(p.communicate(), timeout)
        except asyncio.TimeoutError:
            p.kill()
            raise RuntimeError("Matterbridge reagiert nicht.")
        return ANSI.sub("", out.decode(errors="replace"))

    async def ensure_plugin(self):
        marker = VOICE_DIR / ".plugin-registered"
        if marker.exists():
            return
        VOICE_DIR.mkdir(parents=True, exist_ok=True)
        out = await self._cli("--add", PLUGIN)
        if "Added plugin" not in out and "already registered" not in out.lower():
            print(out[-2000:])
            raise RuntimeError("Das Sprach-Modul ließ sich nicht einrichten.")
        marker.write_text("1")

    def _line(self, line: str):
        line = ANSI.sub("", line).rstrip()
        if not line:
            return
        self.log = (self.log + [line])[-300:]
        m = RE_MANUAL.search(line)
        if m:
            self.manual, self.paired = m.group(1), False
        m = RE_QR.search(line)
        if m:
            self.qr = m.group(1)
        if "is already commissioned" in line or "commissioned successfully" in line:
            self.paired = True
        if "fully decommissioned" in line or "is not commissioned" in line:
            self.paired = False

    async def _run(self):
        delay = 5
        while load_state().get("enabled"):
            try:
                await self.ensure_plugin()
                self.write_config()
                self.proc = await asyncio.create_subprocess_exec(
                    "matterbridge", "--bridge", "--homedir", str(VOICE_DIR), "--frontend", str(FRONTEND_PORT), "--port", str(MATTER_PORT),
                    "--bind", "127.0.0.1", "--novirtual", "--no-ansi",
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
                self.running, self.error = True, ""
                started = asyncio.get_running_loop().time()
                async for raw in self.proc.stdout:
                    self._line(raw.decode(errors="replace"))
                code = await self.proc.wait()
                self.running = False
                if not load_state().get("enabled"):
                    break
                self.error = f"Das Sprach-Modul wurde beendet (Code {code}) und startet neu."
                print(f"[Sprache] Matterbridge beendet mit Code {code}")
                if asyncio.get_running_loop().time() - started > 300:
                    delay = 5
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.running, self.error = False, str(e)
                print(f"[Sprache] {e}")
            await asyncio.sleep(delay)
            delay = min(delay * 2, 300)

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
                    await asyncio.wait_for(self.proc.wait(), 20)
                except asyncio.TimeoutError:
                    self.proc.kill()
            self.task, self.proc, self.running = None, None, False

    async def restart(self):
        """Neu starten, damit neu freigegebene Geräte sofort erscheinen."""
        await self.stop()
        if load_state().get("enabled"):
            await self.start()

    async def reset_pairing(self):
        """Kopplung mit Alexa & Co. löschen – danach erscheint ein neuer Kopplungscode."""
        await self.stop()
        if VOICE_DIR.exists():
            await self._cli("--factoryreset", timeout=120)
            for f in (VOICE_DIR / ".plugin-registered",):
                f.unlink(missing_ok=True)
        self.manual, self.qr, self.paired = "", "", False
        if load_state().get("enabled"):
            await self.start()

    def info(self) -> dict:
        return {"enabled": bool(load_state().get("enabled")), "running": self.running, "paired": self.paired,
                "manual": self.manual, "qr": self.qr, "error": self.error}


# ---------------------------------------------------------------- Geräte auswählen (Label in Home Assistant)
async def label_id(ha, create: bool) -> str | None:
    for l in await ha.call("config/label_registry/list"):
        if l.get("name") == LABEL:
            return l["label_id"]
    if not create:
        return None
    l = await ha.call("config/label_registry/create", name=LABEL, icon=LABEL_ICON,
                      description="Von Haglo Assist: Geräte mit diesem Label sind für Alexa sichtbar")
    return l["label_id"]


async def candidates(ha) -> list[dict]:
    """Alles, was sich per Sprache schalten lässt – mit Haken, wenn es schon freigegeben ist."""
    lid = await label_id(ha, create=False)
    reg = {e["entity_id"]: e for e in await ha.call("config/entity_registry/list")}
    devices = {d["id"]: d for d in await ha.call("config/device_registry/list")}
    areas = {a["area_id"]: a["name"] for a in await ha.call("config/area_registry/list")}
    out = []
    for st in await ha.call("get_states"):
        eid = st["entity_id"]
        domain = eid.split(".")[0]
        if domain not in DOMAINS:
            continue
        r = reg.get(eid)
        if r is None or r.get("disabled_by") or r.get("entity_category"):
            continue        # ohne Registereintrag lässt sich kein Label setzen; Konfig-/Diagnose-Schalter nicht anbieten
        if domain == "automation" and lid not in (r.get("labels") or []):
            continue        # Automationen nur zeigen, wenn sie schon freigegeben sind (z. B. in Home Assistant)
        dev = devices.get(r.get("device_id") or "")
        area = areas.get(r.get("area_id") or (dev or {}).get("area_id") or "", "")
        name = st.get("attributes", {}).get("friendly_name") or eid
        # So heißt es später in Alexa: bei Geräten der Gerätename, sonst der Name der Entität
        alexa = ((dev or {}).get("name_by_user") or (dev or {}).get("name")) if dev else (r.get("name") or r.get("original_name") or name)
        out.append({"id": eid, "name": name, "alexa": alexa or name, "area": area,
                    "art": DOMAIN_NAMES.get(domain, domain), "domain": domain,
                    "on": bool(lid and lid in (r.get("labels") or []))})
    order = {d: i for i, d in enumerate(DOMAINS)}
    out.sort(key=lambda x: (not x["on"], order.get(x["domain"], 99), x["area"] or "~", x["name"].lower()))
    return out


async def set_selected(ha, wanted: set[str]) -> int:
    """Setzt das Label genau an den gewünschten Entitäten. Gibt die Zahl der Änderungen zurück."""
    lid = await label_id(ha, create=bool(wanted))
    if not lid:
        return 0
    changed = 0
    for r in await ha.call("config/entity_registry/list"):
        eid = r["entity_id"]
        if eid.split(".")[0] not in DOMAINS:
            continue
        labels = list(r.get("labels") or [])
        has, want = lid in labels, eid in wanted
        if has == want:
            continue
        labels = labels + [lid] if want else [x for x in labels if x != lid]
        await ha.call("config/entity_registry/update", entity_id=eid, labels=labels)
        changed += 1
    return changed
