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
- [x] Härtung nach Review: fremde/kaputte Antworten werden zu `ToolError`, Eingaben geprüft (Mengen, Limits,
      höchstens 10 Artikel je Aufruf), "50667 Köln" und mehrdeutige Orte werden sauber behandelt

## Neu seit dem letzten Stand
- [x] `market_info` (Öffnungszeiten, Feiertage, Services, Abholstation), `open_now` in `find_article_nearby`
- [x] Standardmarkt und Heimatmärkte per Umgebungsvariable
- [x] `compare_articles`, `fits_space` ("passt in den Raum", ehrlich bei fehlenden Maßen)
- [x] Bewertungs-Rangfolge (`best_rated`, `min_reviews`)
- [x] `delivery_info` (Abholen oder liefern), `get_related` (Zubehör und Ähnliches von toom)

## Als Nächstes (klein, hoher Nutzen)

- [ ] **Wiederholung bei HTTP 5xx** für lesende Aufrufe und den Bestandsendpunkt (mit kurzem Backoff, max. 1–2×).
      Der Bestandsendpunkt hat zweimal vereinzelt 500 geliefert; Ursache unbekannt, erst reproduzieren.
- [ ] **`offers_overview(market)`**: alle Angebote eines Marktes über Kategorien/Marken zerlegt und
      zusammengeführt (umgeht die 72er Grenze ohne `page=`).

## Entscheidungshilfe

- [ ] **Varianten**: `variantsEndpoint` der Produktseite auslesen (andere Größen/Farben desselben Artikels),
      `get_article` zeigt bisher `variants` nur roh.
- [ ] **"Passt ins Auto" als Zwischending**: `fits_space` gibt es schon (Maße des Platzes von Hand). Offen:
      Fahrzeugprofil (Laderaum, Rückbank umgeklappt, Dachlast) als Konfiguration, damit `fits_vehicle(sku)` ohne
      Maße auskommt und `plan_pickup` das `bulky_skus` selbst ableitet. Unbekannte Maße = "unklar", nie "passt".
- [ ] **Zubehör gezielt suchen**: `get_related` liefert toom's Vorschläge (Kaufdaten). Offen: nach Kategorie
      filtern ("nur Befestigung"), `selling_points` ("passend für …") gegen den Artikel abgleichen und eine
      Zubehörliste direkt in `plan_pickup`/Warenkorb übernehmen.
- [ ] **Angebotszeitraum sichtbar machen**: `offer_period` steht in `get_related`; in `search_products` fehlt er.
- [ ] **Rabattarten einzeln filtern**: Set-Angebote und Mengenrabatte stehen als Werte des Filters `offers`
      (`search_filters` zeigt sie); mit `filters` übergebbar, aber für diese Werte noch nicht einzeln getestet.
- [ ] **Vorteilskartenpreis**: das Feld `loyaltyCardPrice` war in allen Stichproben leer; erst belegen, dann ausgeben.
- [ ] **Gutscheincodes im Warenkorb** (`voucherConfig`) lesen.
- [ ] **`find_article_nearby` mit Fahrzeit**: Luftlinie ist nur ein Näherungswert. Optional (opt-in, externer
      Routing-Dienst, kein Standard): Fahrstrecke/-zeit. Ohne Opt-in bleibt alles lokal.
- [ ] **Geocoding für Orte ohne toom-Markt** (opt-in): bisher nur Markt, PLZ oder Koordinaten. "Köln" meint
      derzeit den Standort des einzigen Kölner Marktes, nicht die Stadtmitte; für Bonn etc. Koordinaten angeben.

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
