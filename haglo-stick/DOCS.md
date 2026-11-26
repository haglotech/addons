# Haglo Stick-Erkennung

Läuft still im Hintergrund. Steckt ein Funkstick, prüft das Add-on einmal seine Firmware:

- **Thread-Stick:** OpenThread Border Router und Matter Server werden eingerichtet und gestartet.
  Danach erscheint unter *Einstellungen → Geräte & Dienste* „Entdeckt: OpenThread Border Router“ –
  einmal auf **Hinzufügen** klicken, fertig.
- **Zigbee-Stick:** Es ändert nichts. Home Assistant schlägt wie gewohnt ZHA vor.

- **Beide Sticks gleichzeitig:** geht. Der Stick, den ZHA benutzt, wird nicht angefasst; der Thread-Stick
  bekommt den Border Router.
- **Thread-Stick abgezogen:** Der Border Router wird angehalten (keine Fehlermeldungen). Steckt er wieder,
  läuft Thread von selbst weiter.

Keine Einstellungen nötig.
