"""Tests der Add-on-Seite der MANGFALL-Karten: Prüfung der Karten aus der KI-Antwort, Erkennen installierter
Karten und das Ablegen der Kartendatei samt Dashboard-Ressource. Aufruf: python -m pytest haglo-assist/tests"""
import asyncio
import re
import sys
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parent.parent / "app"
sys.path.insert(0, str(APP))
import server  # noqa: E402

JS = (APP / "cards" / "mangfall-cards.js").read_text(encoding="utf-8")
JS_CARDS = set(re.findall(r'\["(mangfall-[a-z-]+-card)", Mangfall', JS))
VALID = {"sensor.netz", "sensor.pv", "sensor.zisterne", "lawn_mower.robbi", "sensor.wohnzimmer_temp", "light.kueche"}
CARDS = {"installed": ["mangfall-cards"], "types": set(), "keep": set()}


def clean(card):
    removed = []
    return server.clean_config([card], VALID, removed, CARDS), removed


def test_add_on_kennt_genau_die_karten_der_datei():
    assert JS_CARDS == server.MANGFALL_CARDS
    assert len(JS_CARDS) >= 26


def test_version_der_kartendatei_wird_gelesen():
    v = server.mangfall_version()
    assert re.fullmatch(r"\d+\.\d+\.\d+", v), v


def test_installierte_karten_werden_erkannt():
    names = server.card_names([{"url": "/local/haglo/mangfall-cards.js?v=1.5.0"}, {"url": "/hacsfiles/mini-graph-card/mini-graph-card-bundle.js"}])
    assert "mangfall-cards" in names and "mini-graph-card" in names
    for t in JS_CARDS:
        assert server.card_installed("custom:" + t, names), t


@pytest.mark.parametrize("card", sorted(JS_CARDS))
def test_jede_karte_ohne_felder_bleibt_stehen(card):
    out, removed = clean({"type": "custom:" + card})
    assert out == [{"type": "custom:" + card}], removed


def test_erfundene_karte_faellt_weg():
    out, removed = clean({"type": "custom:mangfall-toaster-card"})
    assert out == []


def test_erfundenes_geraet_faellt_weg_karte_bleibt():
    out, removed = clean({"type": "custom:mangfall-energy-card", "grid": "sensor.gibt_es_nicht", "solar": "sensor.pv"})
    assert out == [{"type": "custom:mangfall-energy-card", "solar": "sensor.pv"}]
    assert "sensor.gibt_es_nicht" in removed
    out, removed = clean({"type": "custom:mangfall-level-card", "entity": "sensor.erfunden", "capacity": 5000})
    assert out == [{"type": "custom:mangfall-level-card", "capacity": 5000}]
    out, _ = clean({"type": "custom:mangfall-presence-card", "entities": ["person.niemand"]})
    assert out and out[0]["type"] == "custom:mangfall-presence-card" and not out[0].get("entities")


def test_raumklima_nur_raeume_mit_thermometer():
    out, _ = clean({"type": "custom:mangfall-climate-card", "rooms": [{"name": "Wohnzimmer", "temperature": "sensor.wohnzimmer_temp"}, {"name": "Bad"}]})
    assert out[0]["rooms"] == [{"name": "Wohnzimmer", "temperature": "sensor.wohnzimmer_temp"}]
    out, _ = clean({"type": "custom:mangfall-climate-card", "rooms": [{"name": "Bad"}]})
    assert out == [{"type": "custom:mangfall-climate-card"}]


def test_maehroboter_nur_mit_lawn_mower():
    out, _ = clean({"type": "custom:mangfall-mower-card", "entity": "sensor.zisterne"})
    assert out == [{"type": "custom:mangfall-mower-card"}]
    out, _ = clean({"type": "custom:mangfall-mower-card", "entity": "lawn_mower.robbi"})
    assert out[0]["entity"] == "lawn_mower.robbi"


def test_nicht_installiert_ohne_add_on_karten():
    removed = []
    out = server.clean_config([{"type": "custom:mangfall-energy-card"}], VALID, removed, {"installed": [], "types": set(), "keep": set()})
    assert out == [] and removed
    assert "MANGFALL-Karten bringt Haglo Assist mit" in server.missing_cards_text(removed)


class FakeHA:
    def __init__(self, resources):
        self.resources, self.calls = resources, []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def call(self, typ, **kw):
        self.calls.append((typ, kw))
        return self.resources if typ == "lovelace/resources" else {}


def run_install(monkeypatch, tmp_path, resources):
    ha_root = tmp_path / "homeassistant"
    (ha_root / "www").mkdir(parents=True)
    real_path = server.Path
    monkeypatch.setattr(server, "Path", lambda p, *a: real_path(str(p).replace("/homeassistant", str(ha_root)), *a))
    monkeypatch.setattr(server, "MANGFALL_DST", ha_root / "www" / "haglo" / "mangfall-cards.js")
    fake = FakeHA(resources)
    monkeypatch.setattr(server, "HAClient", lambda session: fake)

    async def served(session, url):
        return True
    monkeypatch.setattr(server, "mangfall_served", served)
    asyncio.run(server.install_mangfall({"session": None}))
    return fake, ha_root / "www" / "haglo" / "mangfall-cards.js"


def test_install_legt_datei_ab_und_traegt_ressource_ein(monkeypatch, tmp_path):
    fake, dst = run_install(monkeypatch, tmp_path, [])
    assert dst.read_bytes() == (APP / "cards" / "mangfall-cards.js").read_bytes()
    created = [kw for typ, kw in fake.calls if typ == "lovelace/resources/create"]
    assert created == [{"res_type": "module", "url": f"/local/haglo/mangfall-cards.js?v={server.mangfall_version()}"}]
    assert server.MANGFALL["ready"] is True and server.MANGFALL["own"] is True


def test_install_aktualisiert_alte_version_und_entfernt_doppelte(monkeypatch, tmp_path):
    old = [{"id": "a", "url": "/local/haglo/mangfall-cards.js?v=1.0.0", "type": "module"},
           {"id": "b", "url": "/local/haglo/mangfall-cards.js?v=0.9", "type": "module"}]
    fake, _ = run_install(monkeypatch, tmp_path, old)
    kinds = [typ for typ, _ in fake.calls]
    assert "lovelace/resources/update" in kinds and ("lovelace/resources/delete", {"resource_id": "b"}) in fake.calls
    assert "lovelace/resources/create" not in kinds


def test_install_laesst_eigene_installation_in_ruhe(monkeypatch, tmp_path):
    fake, dst = run_install(monkeypatch, tmp_path, [{"id": "x", "url": "/hacsfiles/mangfall-cards/mangfall-cards.js", "type": "module"}])
    assert not dst.exists()
    assert [typ for typ, _ in fake.calls] == ["lovelace/resources"]
