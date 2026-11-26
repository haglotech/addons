#!/usr/bin/env python3
"""Haglo Stick-Erkennung: ein Hub-Image für Zigbee und Thread.

Steckt ein USB-Funkstick am Hub, fragt dieses Add-on einmal die Firmware ab:
- Thread (OpenThread RCP, spricht Spinel): OpenThread Border Router und Matter Server
  einrichten und starten, den falschen ZHA-Vorschlag für diesen Stick ausblenden.
  Den letzten Klick („Entdeckt: OpenThread Border Router“) macht der Kunde, erst dabei
  entsteht sein eigenes Thread-Netz.
- Zigbee (EmberZNet NCP, spricht EZSP/ASH): nichts tun, der ZHA-Vorschlag bleibt wie gehabt.

Im Leerlauf liest es alle 5 s /sys/class/tty (kein Gerätezugriff, keine API-Aufrufe).
Nur Python-Standardbibliothek. Lokal testen: python3 stick.py --probe /dev/cu.usbserial-XXXX
"""

import base64
import http.client
import json
import os
import select
import socket
import sys
import termios
import time

OTBR = 'core_openthread_border_router'
MATTER = 'core_matter_server'
IGNORE_TITLE = 'Thread-Stick (Haglo)'


def _token():
    t = os.environ.get('SUPERVISOR_TOKEN', '')
    if not t:   # unter s6 landet die Umgebung nur hier
        try:
            with open('/run/s6/container_environment/SUPERVISOR_TOKEN') as f:
                t = f.read().strip()
        except OSError:
            pass
    return t


TOKEN = _token()


def log(msg):
    print(time.strftime('%H:%M:%S'), msg, flush=True)


# ---------------------------------------------------------------- serielle Abfrage

def open_serial(path, baud):
    fd = os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    speed = getattr(termios, f'B{baud}', baud)   # macOS kennt kein B460800, nimmt den Zahlenwert
    attrs = termios.tcgetattr(fd)
    attrs[0] = 0                                                    # iflag: roh, keine XON/XOFF
    attrs[1] = 0                                                    # oflag
    attrs[2] = termios.CS8 | termios.CREAD | termios.CLOCAL        # cflag: 8N1, kein RTS/CTS
    attrs[3] = 0                                                    # lflag
    attrs[4] = attrs[5] = speed
    attrs[6][termios.VMIN] = 0
    attrs[6][termios.VTIME] = 0
    termios.tcsetattr(fd, termios.TCSANOW, attrs)
    return fd


def exchange(path, baud, frame, wait):
    """Frame senden, bis zu `wait` s lang Antwort sammeln."""
    fd = open_serial(path, baud)
    try:
        time.sleep(0.2)
        termios.tcflush(fd, termios.TCIOFLUSH)
        os.write(fd, frame)
        data, end = b'', time.time() + wait
        while time.time() < end:
            r, _, _ = select.select([fd], [], [], max(0.0, end - time.time()))
            if r:
                chunk = os.read(fd, 512)
                if chunk:
                    data += chunk
                    if len(data) > 4096:
                        break
        return data
    finally:
        os.close(fd)


def crc16_x25(data):          # HDLC-FCS, für Spinel
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0x8408 if crc & 1 else crc >> 1
    return crc ^ 0xFFFF


def crc16_ccitt(data):        # CRC-CCITT (0x1021, Start 0xFFFF), für ASH
    crc = 0xFFFF
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def unstuff(chunk):
    out, esc = bytearray(), False
    for b in chunk:
        if esc:
            out.append(b ^ 0x20)
            esc = False
        elif b == 0x7D:
            esc = True
        else:
            out.append(b)
    return bytes(out)


def spinel_frame():
    # Header 0x81 (IID 0, TID 1), CMD_PROP_VALUE_GET, PROP_PROTOCOL_VERSION
    body = bytes([0x81, 0x02, 0x01])
    fcs = crc16_x25(body)
    raw = body + bytes([fcs & 0xFF, fcs >> 8])
    out = bytearray([0x7E])
    for b in raw:
        if b in (0x7E, 0x7D, 0x11, 0x13, 0xF8):
            out += bytes([0x7D, b ^ 0x20])
        else:
            out.append(b)
    out.append(0x7E)
    return bytes(out)


def is_spinel(data):
    for chunk in data.split(b'\x7e'):
        f = unstuff(chunk)
        if len(f) >= 4 and (f[0] & 0xC0) == 0x80 and crc16_x25(f[:-2]) == (f[-2] | f[-1] << 8):
            return True
    return False


ASH_RST = bytes([0x1A, 0xC0, 0x38, 0xBC, 0x7E])   # Abbruch + RST


def is_ezsp(data):
    for chunk in data.split(b'\x7e'):
        f = unstuff(chunk.lstrip(b'\x1a\x18'))
        # RSTACK (0xC1) oder ERROR (0xC2): Steuerbyte, Version, Code, CRC
        if len(f) == 5 and f[0] in (0xC1, 0xC2) and crc16_ccitt(f[:3]) == (f[3] << 8 | f[4]):
            return True
    return False


def probe(path):
    """'thread', 'zigbee' oder None."""
    for kind, baud, frame, wait, check in (
        ('thread', 460800, spinel_frame(), 1.5, is_spinel),
        ('zigbee', 115200, ASH_RST, 2.5, is_ezsp),
        ('thread', 115200, spinel_frame(), 1.5, is_spinel),
        ('zigbee', 460800, ASH_RST, 2.5, is_ezsp),
    ):
        try:
            if check(exchange(path, baud, frame, wait)):
                return kind
        except OSError as e:
            log(f'{path}: {e}')
            return None
    return None


# ---------------------------------------------------------------- Supervisor und Home Assistant

def sup(method, path, body=None, timeout=30):
    c = http.client.HTTPConnection('supervisor', 80, timeout=timeout)
    try:
        c.request(method, path, json.dumps(body) if body is not None else None,
                  {'Authorization': f'Bearer {TOKEN}', 'Content-Type': 'application/json'})
        r = c.getresponse()
        raw = r.read()
        try:
            data = json.loads(raw or b'{}')
        except ValueError:
            data = {'message': raw[:200].decode('utf-8', 'replace')}
        if r.status >= 400:
            raise RuntimeError(f'{method} {path}: {r.status} {data.get("message", "")}')
        return data.get('data', data) if isinstance(data, dict) else data
    finally:
        c.close()


class HaSocket:
    """Kleinster WebSocket-Client für ws://supervisor/core/websocket."""

    def __init__(self):
        self.s = socket.create_connection(('supervisor', 80), timeout=20)
        key = base64.b64encode(os.urandom(16)).decode()
        self.s.sendall((f'GET /core/websocket HTTP/1.1\r\nHost: supervisor\r\nUpgrade: websocket\r\n'
                        f'Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n').encode())
        head = b''
        while b'\r\n\r\n' not in head:
            chunk = self.s.recv(1024)
            if not chunk:
                raise RuntimeError('WebSocket: Verbindung zu')
            head += chunk
        if b' 101 ' not in head.split(b'\r\n')[0]:
            raise RuntimeError('WebSocket: kein Upgrade')
        self.buf = head.split(b'\r\n\r\n', 1)[1]
        self.id = 0
        self.recv()                                             # auth_required
        self.send({'type': 'auth', 'access_token': TOKEN})
        if self.recv().get('type') != 'auth_ok':
            raise RuntimeError('WebSocket: Anmeldung abgelehnt')

    def _read(self, n):
        while len(self.buf) < n:
            chunk = self.s.recv(65536)
            if not chunk:
                raise RuntimeError('WebSocket: Verbindung zu')
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def send(self, obj):
        data = json.dumps(obj).encode()
        n = len(data)
        head = bytes([0x81]) + (bytes([0x80 | n]) if n < 126 else bytes([0x80 | 126]) + n.to_bytes(2, 'big')
                                 if n < 65536 else bytes([0x80 | 127]) + n.to_bytes(8, 'big'))
        mask = os.urandom(4)
        self.s.sendall(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def recv(self):
        msg = b''
        while True:
            b0, b1 = self._read(2)
            n = b1 & 0x7F
            if n == 126:
                n = int.from_bytes(self._read(2), 'big')
            elif n == 127:
                n = int.from_bytes(self._read(8), 'big')
            payload = self._read(n)
            op = b0 & 0x0F
            if op == 0x9:                                       # Ping -> Pong
                self.s.sendall(bytes([0x8A, 0x80]) + os.urandom(4))
                continue
            if op == 0x8:
                raise RuntimeError('WebSocket: geschlossen')
            msg += payload
            if b0 & 0x80:
                return json.loads(msg)

    def call(self, **cmd):
        self.id += 1
        self.send({'id': self.id, **cmd})
        while True:
            m = self.recv()
            if m.get('id') == self.id:
                if not m.get('success', True):
                    raise RuntimeError(f'{cmd["type"]}: {m.get("error")}')
                return m.get('result')

    def close(self):
        try:
            self.s.close()
        except OSError:
            pass


def ha(fn):
    ws = None
    try:
        ws = HaSocket()
        return fn(ws)
    except Exception as e:          # HA startet noch o. ä. – nächster Durchlauf versucht es wieder
        log(f'Home Assistant nicht erreichbar: {e}')
        return None
    finally:
        if ws:
            ws.close()


def zha_loaded(ws):
    return any(e.get('state') == 'loaded' and e.get('source') != 'ignore'
               for e in ws.call(type='config_entries/get', domain='zha') or [])


def ignore_zha_flows(ws, serials):
    """ZHA-Vorschläge für Thread-Sticks ausblenden (der Kunde soll dort nicht klicken)."""
    serials = {s for s in serials if s}
    for f in ws.call(type='config_entries/flow/progress') or []:
        ctx = f.get('context') or {}
        uid = str(ctx.get('unique_id') or '')
        if f.get('handler') == 'zha' and ctx.get('source') == 'usb' and (not serials or any(s in uid for s in serials)):
            ws.call(type='config_entries/ignore_flow', flow_id=f['flow_id'], title=IGNORE_TITLE)
            log('ZHA-Vorschlag für den Thread-Stick ausgeblendet')


def unignore_zha(ws):
    """Stick wurde auf Zigbee umgeflasht: unsere ZHA-Sperre wieder aufheben."""
    for e in ws.call(type='config_entries/get', domain='zha') or []:
        if e.get('source') == 'ignore' and e.get('title') == IGNORE_TITLE:
            sup('DELETE', f'/core/api/config/config_entries/entry/{e["entry_id"]}')
            log('ZHA-Sperre aufgehoben (Stick ist jetzt Zigbee)')


def ensure_addon(slug, options=None):
    if not sup('GET', f'/store/addons/{slug}').get('installed'):
        log(f'Installiere {slug} …')
        sup('POST', f'/store/addons/{slug}/install', timeout=1200)
    if options:
        current = sup('GET', f'/addons/{slug}/info').get('options') or {}
        sup('POST', f'/addons/{slug}/options', {'options': {**current, **options}})
    sup('POST', f'/addons/{slug}/options', {'boot': 'auto', 'watchdog': True})
    if sup('GET', f'/addons/{slug}/info').get('state') != 'started':
        sup('POST', f'/addons/{slug}/start', timeout=300)
        log(f'{slug} gestartet')


# ---------------------------------------------------------------- Ablauf

def present_ttys():
    try:
        return {n for n in os.listdir('/sys/class/tty') if n.startswith(('ttyUSB', 'ttyACM'))}
    except OSError:
        return set()


def hw_device(name):
    for d in sup('GET', '/hardware/info').get('devices') or []:
        if d.get('dev_path') == f'/dev/{name}':
            return d
    return {}


def otbr_device():
    try:
        info = sup('GET', f'/addons/{OTBR}/info')
        return info.get('state'), (info.get('options') or {}).get('device')
    except Exception:
        return None, None


class Watcher:
    def __init__(self):
        self.known = set()
        self.retry = {}            # Stick -> Zeitpunkt des nächsten Versuchs nach einem Fehler
        self.thread_serials = set()
        self.sweep_until = 0.0
        self.next_sweep = 0.0
        self.restarted = 0.0

    def handle(self, name):
        dev = f'/dev/{name}'
        hw = hw_device(name)
        by_id = hw.get('by_id') or dev
        serial = (hw.get('attributes') or {}).get('ID_SERIAL_SHORT', '')

        if not os.path.exists(dev):
            # Nachträglich eingesteckt und nicht in den Container gereicht: einmal neu starten.
            if time.time() - self.restarted > 600:
                log(f'{dev} ist im Add-on nicht sichtbar – starte neu')
                self.restarted = time.time()
                sup('POST', '/addons/self/restart')
            return

        state, device = otbr_device()
        if state == 'started' and device in (by_id, dev):
            log(f'{by_id}: Thread läuft schon')
            self.thread_serials.add(serial)
            return
        if ha(zha_loaded):
            log(f'{by_id}: ZHA ist eingerichtet – Stick bleibt bei Zigbee, keine Abfrage')
            return

        kind = None
        for attempt in range(3):            # frisch eingesteckt bootet der Stick noch
            kind = probe(dev)
            if kind:
                break
            time.sleep(3)
        log(f'{by_id}: {"Thread" if kind == "thread" else "Zigbee" if kind == "zigbee" else "unbekannte Firmware"}')

        if kind == 'thread':
            ensure_addon(OTBR, {'device': by_id, 'baudrate': '460800', 'flow_control': False, 'autoflash_firmware': False})
            try:
                ensure_addon(MATTER)
            except Exception as e:
                log(f'Matter Server: {e}')
            self.thread_serials.add(serial)
            self.sweep_until = time.time() + 300   # ZHA-Vorschlag kann noch nachkommen
            self.next_sweep = 0
        elif kind == 'zigbee':
            ha(unignore_zha)

    def run(self):
        log(f'Stick-Erkennung läuft{"" if TOKEN else " – OHNE Supervisor-Zugang (kein Token)"}')
        while True:
            now = present_ttys()
            for name in sorted(now - self.known):
                if time.time() < self.retry.get(name, 0):
                    continue
                try:
                    self.handle(name)
                    self.known.add(name)
                    self.retry.pop(name, None)
                except Exception as e:
                    log(f'/dev/{name}: {e} – neuer Versuch in 30 s')
                    self.retry[name] = time.time() + 30
            self.known &= now
            t = time.time()
            if t < self.sweep_until and t >= self.next_sweep:
                self.next_sweep = t + 20
                ha(lambda ws: ignore_zha_flows(ws, self.thread_serials))
            time.sleep(5)


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--probe':
        print(probe(sys.argv[2]) or 'unbekannt')
    else:
        Watcher().run()
