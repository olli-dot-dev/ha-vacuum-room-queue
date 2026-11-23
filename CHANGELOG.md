# Changelog

## 0.2.4 – 2026-11-23

- Einrichtung/Optionen: Die Hinweise „Räume zuordnen“ und „Räume zuerst benennen“
  (vorher „Räume noch nicht zugeordnet/benannt“) sagen kürzer, was zu tun ist, in
  nummerierten Schritten. Liefert der Roboter nur Nummern statt Raumnamen, zeigt der
  Hinweis diese Nummern jetzt an

## 0.2.3 – 2026-11-23

- Einrichtung/Optionen, Schritt „Räume“: nennt jetzt die Räume, die der Saugroboter
  kennt, und erklärt kurz, dass die Karte ohne Auswahl alle anbietet. Das Auswahlfeld
  zeigt nur noch diese Räume statt aller Bereiche des Hauses; die Reihenfolge ergibt
  sich aus der Auswahl. Ein früher gespeicherter, inzwischen nicht mehr zugeordneter
  Raum bleibt sichtbar und wird als Fehler gemeldet, damit man ihn entfernen kann

## 0.2.2 – 2026-11-23

- Behoben: Beim Löschen der Integration blieben die gespeicherte Raumreihenfolge
  (`.storage/vacuum_room_queue.<id>`) und – nach dem letzten Eintrag – die Karte als
  Dashboard-Ressource zurück. Jetzt räumt die Integration beides selbst auf
- README: Installation über den Marketplace (Home Assistant 2026.11), Logo, Badges,
  Screenshot

## 0.2.1 – 2026-11-19

- Behoben: Die Integration erschien nicht unter *Einstellungen → Geräte & Dienste →
  Integrationen*, sondern nur versteckt unter „Helfer“ (sie war als Helfer eingestuft).
  Jetzt ist sie als Integration sichtbar, mit Icon und „Konfigurieren“; bestehende
  Einträge, Entitäten und Raumreihenfolgen bleiben erhalten

## 0.2.0 – 2026-11-19

- Eigenes Icon für die Integration (`brand/`), statt des Platzhalters in Home Assistant
- Neue Sprachen: Niederländisch, Französisch, Spanisch, Polnisch, Russisch – für
  Einrichtung, Optionen, Dienste und Meldungen ebenso wie für die Karte. Die Karte lädt
  ihre Texte jetzt aus `card_translations/<sprache>.json` über den WebSocket-Befehl
  `vacuum_room_queue/get_translations` (Rückfall auf Englisch); ein Test prüft
  Vollständigkeit und Platzhalter aller Sprachen
- README auf Englisch, deutsche Fassung als `README.de.md`
- Behoben: Sensor-Name eines Saugroboters ohne Gerät war immer Deutsch
  („… Raumreihenfolge“). Jetzt bildet Home Assistant Name und Entitäts-ID selbst über
  einen übersetzten Namen mit Platzhalter – nach denselben Sprachregeln wie bei
  Robotern mit Gerät (Entitäts-ID in der Systemsprache bei lateinischer Schrift, sonst
  Englisch)
- Kartenauswahl: Beschreibung in der Sprache des Nutzers und Live-Vorschau; ohne
  gewählte Entität zeigt die Karte einen Hinweis statt eines Fehlers. README: Karte
  Schritt für Schritt über den Bearbeitungsdialog hinzufügen
- Stopp/Heimfahrt aus Home Assistant (Kachel, Automation, Sprachassistent) beendet den
  Lauf wie der Stopp-Knopf der Karte, statt als „Raum fertig“ den nächsten Raum zu
  schicken
- README: am echten Eureka/Valetudo-Roboter getestet, Abschnitt „Stoppen“,
  Sensor-Attribute, Optionen, Grenzen ergänzt
- Kleine Historie in der Karte: erledigte Räume dieses Laufs mit Uhrzeit und darunter der
  letzte Lauf; neues Sensor-Attribut `history`, übersteht Neustarts
- „Zuletzt gereinigt“ je Raum: die Karte zeigt unter „Raum hinzufügen“ bei jedem Raum,
  wann er zuletzt fertig wurde („gestern“, „vor 3 Wochen“); neues Sensor-Attribut
  `last_cleaned`, z. B. für Automationen. Bestehende Installationen übernehmen die Zeiten
  aus der Historie
- Behoben: ein erledigter Raum verschwand aus „Fertig“, solange derselbe Raum (per
  „Nochmal“) gerade wieder gesaugt wurde
- Wartezeit bis zum Start in den Optionen einstellbar (1–60 Minuten), Standard von 5
  auf 10 Minuten erhöht – eine Eureka-Station mit Valetudo wäscht vor dem Losfahren
  knapp 3 Minuten die Mopps
- Erfolgsmeldung nach der Einrichtung weist darauf hin, die Browserseite neu zu laden –
  offene Tabs kennen die frisch registrierte Karte sonst nicht („Konfigurationsfehler“)
- Einrichtung fragt nicht mehr nach der Methode, sondern nimmt die native
  Bereichsreinigung, wenn der Roboter sie kann, sonst das Skript; verständlichere
  Texte in den Optionen
- Einrichtung erkennt fehlende Bereichszuordnung: statt eines leeren Eintrags ein
  Hinweis mit „Erneut prüfen“ bzw. Wechsel zur Skript-Methode; haben die Segmente nur
  Nummern (z. B. Valetudo), zusätzlich der Hinweis, die Räume erst in der App zu benennen
- Räume ohne Segment werden bei der Auswahl abgelehnt statt später beim Fahren
- Karte zeigt bei fehlender Zuordnung einen konkreten Hinweis und einen Knopf zum
  Saugroboter; neues Sensor-Attribut `rooms_hint`

## 0.1.0 – 2026-11-11

Erste Version.

- Raumreihenfolge pro Saugroboter: die Integration schickt den Roboter Raum für Raum
  los, die Raumreihenfolge bleibt jederzeit änderbar – auch während er fährt
- Räume dürfen mehrfach in der Raumreihenfolge stehen (mehrfach saugen/wischen),
  „Nochmal“-Knopf in der Karte, `remove_area` mit `position`
- Native Bereichsreinigung (`vacuum.clean_area`) oder eigenes Skript pro Raum
- Dashboard-Karte mit Drag & Drop (Maus, Touch, Tastatur), wird automatisch als
  Ressource eingetragen
- Dienste `set_queue`, `add_area`, `remove_area`, `start`, `pause`, `skip`, `stop`,
  `clear`
- Erkennt Pause/Fehler/Fortsetzen auch aus der Hersteller-App; Raumreihenfolge übersteht
  Neustarts
