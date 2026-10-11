# Haglo Apps für Home Assistant

![Version](https://img.shields.io/badge/dynamic/yaml?url=https%3A%2F%2Fraw.githubusercontent.com%2Fhaglotech%2Faddons%2Fmain%2Fhaglo-assist%2Fconfig.yaml&query=%24.version&label=version&color=2e7d32)
![Maintained](https://img.shields.io/maintenance/yes/2026)
![Letzte Änderung](https://img.shields.io/github/last-commit/haglotech/addons?label=letzte%20%C3%84nderung)
![Supports aarch64](https://img.shields.io/badge/aarch64-yes-green)
![Supports amd64](https://img.shields.io/badge/amd64-yes-green)

## Haglo Assist

Sag in einem Satz, was du sehen willst – Haglo Assist baut daraus ein
Home-Assistant-Dashboard. Auf Deutsch, ohne YAML.

- **Dashboards:** baut ein eigenes „Haglo Dashboard“ und ändert auf Wunsch auch deine bestehenden.
  Vor jeder Änderung wird eine Version gesichert, die du wiederherstellen kannst.
- **Automationen, Skripte, Szenen:** „Wenn sich im Flur etwas bewegt, mach das Licht an.“ Du siehst
  erst einen Vorschlag und bestätigst ihn.
- **Schalten und fragen:** „Mach im Wohnzimmer das Licht aus“ oder „Wie warm ist es im Bad?“
- Free für alle. Bei Geräten von Haglo ist Lite mindestens 12 Monate inklusive.

### Installation

Voraussetzung: Home Assistant OS oder Supervised.

Hinweis: In neueren Home-Assistant-Versionen heißen Add-ons **Apps**. Der Weg ist derselbe.

1. **Einstellungen → Apps** (früher „Add-ons“) **→ App Store → ⋮ → Repositories**
2. Diese Adresse eintragen und hinzufügen:
   `https://github.com/haglotech/addons`
3. Im Store **Haglo Assist** auswählen → **Installieren** → **Starten**.
4. „In Seitenleiste anzeigen“ einschalten – links erscheint **Haglo Assist**.

[![Repository zu Home Assistant hinzufügen](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fhaglotech%2Faddons)

### Datenschutz

Zum Erstellen eines Dashboards überträgt das Add-on Gerätenamen, Räume, Gerätetypen
und Einheiten an den Haglo-Server (`api.haglotech.com`) und von dort an unseren
KI-Dienstleister Anthropic. Messwerte, Zustände und Verlaufsdaten werden nicht übertragen. Um die Testphase zu prüfen,
werden nach deiner Zustimmung Seriennummer und Netzwerkadresse des Geräts an Haglo gesendet.
Frühere Dashboard-Versionen bleiben nur lokal auf deinem Gerät.

Aktuelle Werte einzelner Geräte (für Fragen wie „Wie warm ist es im Bad?“), der Aufbau eigener
Dashboards und eigene Automationen werden nur übertragen, wenn du dem in der App ausdrücklich zustimmst.
Details: [Datenschutzerklärung](https://haglotech.com/policies/privacy-policy) ·
[AGB](https://haglotech.com/policies/terms-of-service)

### Hilfe

[Beratung buchen](https://haglotech.com/pages/termin) · info@haglotech.com

### Lizenz

Die App ist Open Source unter der [Apache-Lizenz 2.0](LICENSE). Die Namen „Haglo“ und „Haglo Assist“
und die Logos bleiben Kennzeichen von Haglo, siehe [NOTICE](NOTICE). Der Dienst hinter der App ist
nicht Teil dieser Software.
