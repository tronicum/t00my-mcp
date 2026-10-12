# TODOs: Werkzeuge, die wir haben wollen

Stand: Oktober 2026. Reihenfolge innerhalb der Abschnitte = Priorität.
Legende: **[ ]** offen · **[~]** teilweise · **[x]** erledigt

Grundsätze für alles hier: nur lesen und vorbereiten, nichts abschicken, was Kontaktdaten oder Geld
betrifft; höfliche Rate (≥ 0,5 s Abstand, nur erste Ergebnisseite, nie `+` oder `page=` in URLs wegen
`robots.txt`); jede neue Funktion mit Mock-Test gegen aufgezeichnete Antworten und, wo sinnvoll, einem
Live-Contract-Test (`TOOMY_LIVE=1`).

## Schon da

- [x] `find_markets`, `set_market`: Markt per Stadt, PLZ, Name, ID
- [x] `search_products`: Angebote, Sortierung, Preis, Marke, Verfügbarkeit, eigene Filter
- [x] `search_filters`: welche Filter toom für einen Begriff anbietet
- [x] Warenkorb pro Markt: `add_to_basket`, `view_basket`, `set_quantity`, `remove_from_basket`, `clear_basket`
- [x] `compare_markets`, `copy_basket`, `plan_pickup`
- [x] `prepare_article_request` (bereitet "Artikel anfragen" vor, sendet nichts)
- [x] `export_session` (mit Rückbestätigung)
- [x] `get_article`: Details, Merkmale, EAN, Bestand in einem oder mehreren Märkten
- [x] `markets_nearby`, `find_article_nearby`: Umkreissuche (Luftlinie) mit Bestand je Markt

## Als Nächstes (klein, hoher Nutzen)

- [ ] **Standardmarkt / Heimatmärkte per Umgebungsvariable** (`TOOMY_DEFAULT_MARKET`, `TOOMY_HOME_MARKETS`),
      damit `set_market` nicht jede Sitzung nötig ist und `compare_markets`/`plan_pickup` ohne Liste auskommen.
- [ ] **`market_hours(market)`**: Öffnungszeiten stehen schon in der Marktliste (`openingTimes`); "hat der Markt
      jetzt auf / bis wann" für die Abholfahrt. Auch als Feld in `find_article_nearby`.
- [ ] **Wiederholung bei HTTP 5xx** für lesende Aufrufe und den Bestandsendpunkt (mit kurzem Backoff, max. 1–2×).
      Der Bestandsendpunkt hat zweimal vereinzelt 500 geliefert; Ursache unbekannt, erst reproduzieren.
- [ ] **`offers_overview(market)`**: alle Angebote eines Marktes über Kategorien/Marken zerlegt und
      zusammengeführt (umgeht die 72er Grenze ohne `page=`).

## Entscheidungshilfe

- [ ] **`compare_articles([sku, …])`**: Merkmale nebeneinander (Maße, Material, Preis, Bestand), z. B. mehrere
      Türgrößen oder Bohrmaschinen.
- [ ] **Varianten**: `variantsEndpoint` der Produktseite auslesen (andere Größen/Farben desselben Artikels),
      `get_article` zeigt bisher `variants` nur roh.
- [ ] **Passt es ins Fahrzeug?** Fahrzeugprofil (Laderaum/Maße) als Konfiguration und `fits_vehicle(sku)` aus
      den Merkmalen (Breite, Höhe, Länge, Gewicht), damit `bulky_skus` für `plan_pickup` nicht von Hand kommt.
      Unbekannte Maße = "unklar", nie "passt".
- [ ] **`find_article_nearby` mit Fahrzeit**: Luftlinie ist nur ein Näherungswert. Optional (opt-in, externer
      Routing-Dienst, kein Standard): Fahrstrecke/-zeit. Ohne Opt-in bleibt alles lokal.
- [ ] **Geocoding für Orte ohne toom-Markt** (opt-in): bisher nur Markt, PLZ oder Koordinaten.

## Beobachten und erinnern

- [ ] **`watch_article`**: Bestand in ausgewählten Märkten in Abständen prüfen (≥ 15 min) und melden, wenn er
      auftaucht oder verschwindet. Lokal, ohne Konto.
- [ ] **Verfügbarkeitsbenachrichtigung von toom** (`availabilitynotification` im Seitencode): braucht E-Mail
      des Nutzers, daher nur als Vorbereitung wie `prepare_article_request`.
- [ ] **Preisbeobachtung**: Preis eines Artikels über Zeit festhalten (toom liefert keinen früheren Preis).

## Warenkorb und Sitzung

- [ ] **Sitzung überlebt Neustarts**: Cookies je Markt auf Platte (Rechte 0600), damit Warenkörbe bleiben.
- [ ] **Eigene Browser-Sitzung einlesen** (Gegenrichtung zu `export_session`, mit Rückbestätigung).
- [ ] **Einkaufsliste in Freitext → Artikel**: Liste auflösen (Assistent sucht je Zeile), danach direkt
      `plan_pickup`; Mehrdeutiges nachfragen statt raten.

## Mit Konto (nur auf ausdrücklichen Wunsch des Nutzers)

- [ ] **Login und Reservieren** (Click & Reserve) mit dem eigenen Konto: braucht Zugangsdaten, 2FA/Captcha
      möglich. Nur mit Rückbestätigung je Aktion und nie automatisch. Erst klären, ob das ohne
      Umgehung von Schutzmechanismen geht.
- [ ] **Merkliste** (`wishlist`-Endpunkte) lesen und schreiben.

## Betrieb und Veröffentlichung

- [ ] **GitHub-Repo anlegen** (öffentlich, AGPL-3.0), CI wie bei iceicebaby (liegt als `ci.yml` bereit).
- [ ] **Geplanter Live-Contract-Lauf** (wöchentlich, ohne Bestellen), um Änderungen an toom.de früh zu merken.
- [ ] **PyPI-Release** (Trusted Publishing) wie bei iceicebaby, Paketname vorher prüfen.
- [ ] **README-Beispiele je Werkzeug** mit echten, anonymisierten Antworten.

## Bewusst nicht

- Captcha oder Bot-Schutz umgehen (kein cloudscraper, keine Lösedienste): auch nicht für den eigenen Gebrauch.
- "Artikel anfragen" automatisch absenden: enthält Kontaktdaten und liegt hinter einem Captcha, das macht der
  Nutzer selbst; `prepare_article_request` bereitet alles vor.
- Weitere Ergebnisseiten (`page=`) abrufen, Massenabfragen, Dauerabfragen im Sekundentakt.
- Bezahlen oder verbindlich bestellen ohne ausdrückliche Bestätigung des Nutzers.
