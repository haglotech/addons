# Haglo Apps für Home Assistant

## Haglo Assist

Sag in einem Satz, was du sehen willst – Haglo Assist baut daraus ein
Home-Assistant-Dashboard. Auf Deutsch, ohne YAML.

- Speichert in ein **eigenes Dashboard „Haglo Dashboard“**. Deine bestehenden
  Dashboards werden nie angefasst.
- Legt vor jeder Änderung ein Backup an, „Rückgängig“ stellt es wieder her.
- 30 Tage kostenlos testen. Die Testphase endet automatisch, ohne Zahlungsdaten.

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
KI-Dienstleister Anthropic. Messwerte und Verlaufsdaten werden nicht übertragen.
Details: [Datenschutzerklärung](https://haglotech.com/policies/privacy-policy) ·
[AGB](https://haglotech.com/policies/terms-of-service)

### Hilfe

[Beratung buchen](https://haglotech.com/pages/termin) · info@haglotech.com
