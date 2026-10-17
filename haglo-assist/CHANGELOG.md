# Änderungen

## 0.28.0 (Beta, Branch feat/haglo-local)

- Haglo Local kann jetzt mehr direkt auf dem Hub, ohne Internet und Kontingent: Timer („in 10 Minuten Licht aus“), einfache
  Automationen (Uhrzeit und Wochentage, Sonnenauf- und -untergang, Bewegung, Tür/Fenster, Temperatur und Luftfeuchtigkeit) und
  neue Dashboards für Räume oder Gerätearten mit Eintrag in der Seitenleiste
- Automationen legt Haglo Local erst nach „Ja“ an, wie bei Claude; Timer und neue Dashboards lassen sich rückgängig machen
- Timer überstehen einen Neustart; was länger als 10 Minuten verpasst wurde, verfällt
- Neues Sprachmodell (Qwen3-0.6B + Haglo-Adapter); „wärmer/kühler“ und Farbtemperaturen rechnet der Code, nicht das Modell
- Was lokal nicht sicher geht (Fehlersuche, Energie-Auswertungen, Umbauten), übernimmt wie bisher Claude
- „Schreibtisch für 10 Minuten an“, „Licht an und in 5 Minuten wieder aus“, „in 1 Minute …“ und Zeit-Automationen
  („jeden Abend um halb 8 …“, „bei Sonnenuntergang …“) erkennt die App sofort, ohne Sprachmodell
- Neu: Haglo Assist als Assistent in Home Assistant – unter Einstellungen → Sprachassistenten als Konversationsagent
  wählbar (Sprache, Companion-App, Satelliten). Die App legt dafür die Integration „Haglo Assist“ an; nach einem
  Neustart von Home Assistant richtet sie sich selbst ein. Bestätigungen und Rückfragen beantwortet man mit „Ja“ oder „Nein“

## 0.27.0 (Beta, Branch feat/sprachsteuerung)

- Neu: Sprachsteuerung (Beta) – Geräte per Alexa schalten, auch Google Home und Apple Home. Unter dem Eingabefeld „Sprache“
  öffnen, einschalten, Geräte anhaken und mit dem angezeigten Code in der Alexa-App koppeln. Läuft lokal über Matter,
  ohne Haglo-Server
- Auch Skripte, Szenen und Schalter-Helfer lassen sich per Sprache starten („Alexa, schalte Radio ein“)
- Freigegebene Geräte tragen in Home Assistant das Label „Sprachsteuerung“ und lassen sich auch dort pflegen
- Die App nutzt jetzt das Host-Netzwerk (für Matter nötig); die Oberfläche bleibt nur über Home Assistant erreichbar

## 0.26.2

- Voreinstellung ist jetzt „Schnell“; „Präzise“ denkt gründlicher nach, verbraucht etwas mehr und dauert etwas länger

## 0.26.1

- Handy: Die Zeile unter dem Eingabefeld passt wieder in die Breite (Verlauf, Automationen und Versionen als Symbole)

## 0.26.0

- Neu: Schalter „Schnell“ / „Präzise“ unter dem Eingabefeld. Schnell antwortet zügig und verbraucht wenig, Präzise denkt gründlicher
  (Voreinstellung)
- Mehr Kontingent in allen Plänen

## 0.25.0

- Neu: Haglo Assist legt auf Wunsch ein eigenes Dashboard mit Eintrag in der Seitenleiste an („eigener Reiter in der Seitenleiste nur mit Schaltern“) – „Rückgängig“ löscht es wieder
- Behoben: Eine neue Ansicht auf der Startseite wurde als „Reiter in der Seitenleiste“ bezeichnet

## 0.24.0

- Neu: Haglo Assist spielt Updates ein („mach alle Updates“) – nach deiner Bestätigung, wo möglich mit Sicherung vorher,
  im Hintergrund nacheinander; Home Assistant selbst und Haglo Assist kommen zuletzt, weil danach neu gestartet wird

## 0.23.7

- Die kleine Zeile nach einer Zustimmung zeigt richtig an, ob einmal oder immer erlaubt wurde

## 0.23.6

- Erteilte Zustimmungen schrumpfen im Gespräch zu einer kleinen Zeile („✓ Konfigurationsdateien – erlaubt“) statt als große Karte stehen zu bleiben

## 0.23.5

- Klappt eine Reparatur nicht, bietet Haglo Assist die anderen Wege direkt als Knopf an (z. B. „Stattdessen: Entfernen“ oder „Hinweis ausblenden“)

## 0.23.4

- Fehlt der App noch der Zugriff auf die Konfigurationsdateien, erklärt Haglo Assist, wie man ihn per Update bekommt

## 0.23.3

- Beim Bestätigen einer Reparatur, Einstellung oder Aktion zeigt der Knopf sofort „Wird repariert …“ bzw. „Wird gespeichert …“
- Dauert eine Reparatur länger als 90 Sekunden (z. B. weil ein Netzwerkspeicher nicht antwortet), sagt die App das, statt still zu warten

## 0.23.2

- Reparaturen sind verständlich beschriftet („Netzwerkspeicher-Gerät ausgefallen beheben: Neu laden“) statt mit technischen Namen
- Links in Erklärungen erscheinen als Knöpfe statt als Text in eckigen Klammern
- Klappt eine Reparatur nicht (z. B. weil das NAS weiter nicht erreichbar ist), sagt die App das deutlich und warum

## 0.23.1

- Behoben: Folgte auf die Systemmeldungen ein Reparaturvorschlag, brach die App mit „'config'“ ab
- Behoben: Bei manchen Integrationen brach das Lesen der Einstellungen ab
- Während Home Assistant neu startet, erscheint jetzt ein verständlicher Hinweis statt einer technischen Meldung
- Haglo Assist fragt bei sehr offenen Wünschen („mach was Schönes“) erst nach und öffnet Türschlösser per Aufschließen

## 0.23.0

- Neu: Haglo Assist erledigt Reparaturen selbst – nach deiner Bestätigung: Hinweise ausblenden, die eingebaute Reparatur
  von Home Assistant ausführen (z. B. Netzwerkspeicher neu verbinden oder entfernen) und Einträge in der configuration.yaml
  anpassen oder entfernen. Vor jeder YAML-Änderung wird gesichert und danach die Konfiguration geprüft; bei Fehlern wird
  automatisch der alte Stand wiederhergestellt
- Die App braucht dafür Zugriff auf den Konfigurationsordner von Home Assistant; die Datei secrets.yaml liest sie nie

## 0.22.1

- Haglo Assist weiß jetzt, aus welcher Integration ein Gerät stammt – und kann z. B. sagen, ob met.no doppelt eingerichtet ist
- Knöpfe zu Integrationen und Apps zeigen deren Symbol
- Behoben: Eine Rückfrage wie „Wieso nicht?“ wurde als Zustimmung verstanden und änderte die Startseite
- Einstellungen einer Integration (z. B. Adaptive Lighting) werden dauerhaft gespeichert statt über einen Dienst,
  der nur bis zum nächsten Neustart wirkt; genannte Lampen werden der Integration zugeordnet

## 0.22.0

- Neu: Haglo Assist liest bei Fehlern die Fehlerprotokolle von Home Assistant – erst nach deiner Zustimmung,
  Passwörter, Schlüssel und E-Mail-Adressen werden vorher geschwärzt – und sagt, was zu tun ist
- Neu: Einfache Reparaturen führt Haglo Assist nach Bestätigung selbst aus: Integration neu laden oder
  einschalten, Home Assistant neu starten (mit vorheriger Prüfung der Konfiguration)

## 0.21.0

- Neu: Haglo Assist kann die Einstellungen einer Integration ändern („Stell Adaptive Lighting so ein, dass …“).
  Die Einstellungen werden erst nach deiner Zustimmung gelesen, Zugangsdaten nie übertragen, und gespeichert wird
  erst, wenn du die angezeigte Änderung bestätigst. Geht bei Integrationen mit einem einfachen Einstellungs-Formular

## 0.20.5

- Ein Gespräch endet erst nach 5 Minuten, in denen du nichts tust – nicht mehr 5 Minuten nach der letzten
  Nachricht. Wer liest, tippt oder scrollt, bleibt im Gespräch

## 0.20.4

- Behoben: 0.20.3 startete nicht

## 0.20.3

- Behoben: Auf neuen Home-Assistant-Versionen blieb die Startseite scheinbar unverändert, obwohl Haglo Assist sie
  gebaut hatte. Home Assistant öffnete weiter sein eingebautes „Home“. Jetzt wird die gebaute Übersicht zur
  Startseite; „Rückgängig“ stellt das wieder zurück

## 0.20.2

- Auf Geräten von Haglo: neue Begrüßung, die auf einen Blick zeigt, dass Lite 12 Monate dabei ist
- Baut Haglo Assist deine Startseite, steht dort oben eine schmale Karte mit dem Hinweis und einem Knopf zu Haglo Assist

## 0.20.1

- Fragen über einen Zeitraum („Wie viel habe ich diesen Monat erzeugt?“): Haglo Assist liest dafür die
  Langzeitdaten aus Home Assistant – jedes Mal erst nach deiner Zustimmung
- „Beratung buchen“ ist vorerst aus der App entfernt

## 0.20.0

- „Gibt es Probleme?“: Haglo Assist sieht nach offenen Reparaturhinweisen, Updates und gestoppten Apps
  und sagt, was zu tun ist – nach deiner Zustimmung

## 0.19.9

- Öffnest du Haglo Assist innerhalb von 5 Minuten wieder, steht das laufende Gespräch noch da.
  Danach wechselt die App zurück zur aufgeräumten Startansicht

## 0.19.8

- Unter der Eingabe steht, wie lange sich Haglo Assist das Gespräch noch merkt; ein Klick darauf beginnt ein neues

## 0.19.7

- Ein Gespräch endet nach 5 Minuten ohne Nachricht: Eine Linie „Neues Gespräch“ zeigt, ab wo Haglo Assist
  sich nichts mehr merkt

## 0.19.6

- Erledigtes wird anschaulich gezeigt: je Gerät eine Zeile mit Symbol (eingeschaltet, umbenannt, Raum)
  und Link direkt zum Gerät

## 0.19.5

- Geräte lassen sich einem Raum zuordnen („Ordne die Kaffeemaschine der Küche zu“); fehlt der Raum, wird er angelegt
- Mehreres in einem Satz: „Benenne sie um und schalte sie ein“ wird in einem Schritt erledigt

## 0.19.4

- Nennst du eine Adresse im Heimnetz (z. B. 192.168.1.50), sieht Haglo Assist selbst nach, welches
  Gerät das ist, statt nachzufragen

## 0.19.3

- Oben rechts steht jetzt „Feedback“ statt „Beratung buchen“: Schreib uns direkt aus der App

## 0.19.2

- Haglo Assist merkt sich die letzten Nachrichten des laufenden Gesprächs: „Benenn es um“ bezieht sich
  auf das Gerät von eben
- Geräte und Entitäten lassen sich umbenennen
- Gerät verbinden: wartet jetzt auf die echten Entitäten des Geräts, nicht auf den Netzwerk-Eintrag vom Router

## 0.19.1

- Gerät verbinden: Läuft MQTT schon, kennt die App aber die Zugangsdaten nicht, fragt sie danach,
  statt die Installation von Mosquitto vorzuschlagen

## 0.19.0

- Neues Gerät verbinden: Nenn die Adresse eines Tasmota- oder Shelly-Geräts („Füge die Steckdose unter
  192.168.1.50 als Kühlschrank hinzu“) – Haglo Assist prüft es im Heimnetz und verbindet es nach deiner
  Bestätigung mit Home Assistant
- Dafür fragt die App Home Assistant nach dem MQTT-Dienst (neue Berechtigung „MQTT“)

## 0.18.9

- Handy: kein seitliches Scrollen mehr, flacherer Kopf und Fuß, mehr Platz für das Gespräch

## 0.18.8

- Hilfe beim Einrichten: Haglo Assist erklärt, wie du ein Gerät oder eine Integration hinzufügst, und
  führt dich mit Knöpfen direkt zur richtigen Seite in Home Assistant

## 0.18.7

- Nach einer beantworteten Rückfrage bleiben im Gespräch nur Frage und Antwort stehen
- Neutraler Wartetext („Ich überlege …“)

## 0.18.6

- Behoben: Bei einer selbst gestalteten Startseite endete jede Anfrage mit „Ungültige Antwort“
- Fragen, Schalten und Automationen gehen jetzt auch ohne Freigabe des eigenen Dashboards; gefragt wird
  nur noch, wenn der Wunsch wirklich das Dashboard betrifft

## 0.18.5

- Eigene Nutzungsbedingungen und Datenschutzhinweise für Haglo Assist verlinkt
- Die Hub-ID steht jetzt unter „Plan & Abo“ (für Auskunft oder Löschung deiner Daten)

## 0.18.4

- Diagramme im hellen Modus in neutralen Farben

## 0.18.3

- Haglo Assist ist als Beta gekennzeichnet

## 0.18.2

- Neues Symbol in der Seitenleiste und grünes Haglo-H als App-Symbol

## 0.18.1

- Neues App-Symbol mit dem Haglo-H; auch in der Seitenleiste steht jetzt ein H

## 0.18.0

- Keine Dashboard-Auswahl mehr: Haglo Assist arbeitet einfach an deiner Startseite. Meinst du ein
  anderes Dashboard, nenn es im Satz beim Namen, zum Beispiel „im Dashboard Technik …“.
- Ist die Startseite noch automatisch erzeugt, baut Haglo Assist sie neu. „Rückgängig“ bringt die
  automatische Ansicht zurück. Hast du sie selbst gestaltet, fragt die App einmal nach.
- Im Gespräch steht die Uhrzeit jetzt unter der Nachricht
- Antworten werden formatiert angezeigt: fett, Listen, Tabellen, Code und Diagramme

## 0.16.2

- Aufgeräumte Leiste unter der Eingabe: links das Dashboard, rechts Verlauf, Automationen und Versionen

## 0.16.1

- Beim Öffnen ist die Oberfläche immer leer und aufgeräumt. Der Gesprächsfaden erscheint mit deiner
  ersten Nachricht, Früheres steht eingeklappt darüber oder unter „Verlauf“

## 0.16.0

- Neue Oberfläche als ein Gesprächsfaden: deine Wünsche und die Antworten untereinander, die Eingabe
  steht fest unten
- Jede Änderung hat ihr eigenes „Rückgängig“ – auch später noch
- Ältere Einträge klappen sich ein. Mit „Neu beginnen“ leerst du den Faden, deine Dashboards,
  Automationen und Versionen bleiben erhalten

## 0.15.2

- Geräte von Haglo zeigen schon beim ersten Start, dass Lite inklusive ist – ohne dass dafür vor
  deiner Zustimmung etwas übertragen wird

## 0.15.0

- Begrüßung auf Haglo-Geräten: Dank für den Kauf, kurze Einführung und der Hinweis auf das inklusive Lite
- Auf vorinstallierten Geräten steht Haglo Assist direkt unter „Übersicht“, öffnet sich beim ersten
  Start und meldet sich mit einer Willkommens-Nachricht

## 0.14.0

- Geräte schalten: „Mach im Wohnzimmer das Licht aus“ wird sofort ausgeführt. Bei Schlössern, Toren
  und Alarmanlagen fragt Haglo Assist vorher nach.
- Fragen beantworten: „Wie warm ist es im Bad?“ Dafür werden aktuelle Werte nur nach deiner
  Zustimmung übertragen, und nur die der betroffenen Geräte.
- Automationen, Skripte, Szenen und Helfer in normaler Sprache anlegen und ändern. Alles wird erst
  als Vorschlag gezeigt und nach deiner Bestätigung angelegt.
- Eigene Dashboards bearbeiten: Auswahl direkt unter dem Eingabefeld.
- Eigene Automationen ändern, wenn du es unter „Automationen“ erlaubst. Die bisherige Fassung wird gesichert.

## 0.13.0

- Verlauf: Unter „Zuletzt“ steht, was Haglo Assist für dich gemacht hat. Getipptes und offene
  Rückfragen bleiben erhalten, wenn du die Seite verlässt und zurückkommst. Der Verlauf liegt nur
  auf deinem Gerät.
- Läuft noch eine Anfrage, während du weg warst, zeigt die App das an und wartet auf das Ergebnis.
- Zum Ausprobieren: Automationen in normaler Sprache anlegen (Option „automations“ in der
  Konfiguration). Jede Automation wird erst als Vorschlag gezeigt und nach deiner Bestätigung angelegt.

## 0.12.6

- Beim ersten Start erscheint Haglo Assist von selbst in der Seitenleiste, automatische Updates sind an

## 0.12.5

- „Kostenlos starten“ oben rechts ist jetzt ein Knopf und öffnet direkt die Zustimmung

## 0.12.4

- Neues Symbol in der Seitenleiste

## 0.12.1

- Der Hinweis zum inklusiven Plan lässt sich mit × dauerhaft ausblenden

## 0.12.0

- Ruhigerer, neutraler Hintergrund – Grün nur noch als Akzent
- Auf Haglo-Geräten ist Lite mindestens 12 Monate inklusive. Die App zeigt das direkt an,
  mit dem Datum, bis zu dem es mindestens gilt. Eine gesonderte Testphase gibt es nicht mehr.

## 0.11.0

- Nutzungslimits: ein 5-Stunden-Limit und ein Wochenlimit, angezeigt in Prozent.
  Kleine Änderungen verbrauchen weniger als große Dashboards.

## 0.10.0

- Neue Pläne: Free für alle, dazu Lite, Pro und Max. Auf Haglo-Geräten ist Lite mindestens 12 Monate inklusive.
- Nutzungslimits laufen jetzt in einem Zeitfenster von einigen Stunden und pro Woche und füllen sich
  von selbst wieder auf. „Plan & Abo“ zeigt den Verbrauch und wann es weitergeht.
- Zum Ausprobieren: eigene Dashboards bearbeiten (Option „own_dashboards“ in der Konfiguration)

## 0.9.0

- Versionen: Vor jeder Änderung wird dein Dashboard lokal gesichert. Unter „Versionen“ stellst du
  jeden der letzten 30 Stände wieder her. Die Versionen verlassen dein Gerät nicht.
- Die 30-Tage-Testphase gibt es auf von Haglo ausgelieferten Geräten, auf anderer Hardware startet
  die App im kostenlosen Plan. Seriennummer und Netzwerkadresse werden dafür erst nach deiner
  Zustimmung übertragen.
- Neues Symbol in der Seitenleiste
- Zum Ausprobieren: Eingabeleiste direkt im Dashboard (Option „dashboard_bar“ in der Konfiguration)

## 0.8.0

- Rückfragen mit Auswahl: Ist ein Wunsch noch unklar, fragt Haglo Assist kurz nach, statt zu raten.
  Rückfragen zählen nicht als Änderung.
- „Beratung buchen“, AGB und Datenschutz funktionieren auch, wenn der Server gerade nicht erreichbar ist

## 0.7.0

- „Plan & Abo“ direkt in der App: Pläne ansehen, monatlich oder jährlich wählen
- Bezahlen, Abo verwalten und Kündigen laufen vollständig über Stripe
- Integration direkt in der App anfragen

## 0.6.1

- Oberfläche überarbeitet: Schriftzug „HAGLO ASSIST“, mittige Anordnung, Vorschläge mit Symbolen
- Anzeige oben rechts zeigt Plan und verbleibende Änderungen und führt zu „Plan & Abo“
- Neues Symbol in der Seitenleiste und neues App-Icon

## 0.6.0

- Kein API-Schlüssel mehr in der App: Die KI läuft über den Haglo-Server
- Testphase, Zustimmung und Pläne werden zentral verwaltet
