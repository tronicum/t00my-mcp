# toomy 🔨

**EN** · [DE](#deutsch)

An MCP server (Python, [FastMCP](https://gofastmcp.com)) for the [toom.de](https://toom.de) DIY online shop:
search articles, fill a basket per market, see which market actually has what in stock, and copy a basket from
one market to another. Unofficial; it talks to the same web endpoints the toom.de site uses.

## What it does

| Tool | What it does |
|---|---|
| `find_markets`, `set_market` | Find a toom market ("Markt") by city, name, postal code or id and select it |
| `search_products` | Search for articles, optionally narrowed down: `offers_only`, `sort` (relevance, price, offers, rating, `best_rated`), `min_reviews`, `min_price`/`max_price`, `brands`, `availability` (in stock here / orderable / delivery) or toom's own `filters`. Each hit has `on_offer`/`promotion` and the decoded availability |
| `search_filters` | Which filters toom offers for a search term (brand, cordless/corded, battery included, ...) with hit counts, to narrow a broad search down |
| `add_to_basket`, `view_basket`, `set_quantity`, `remove_from_basket`, `clear_basket` | A basket **per market**; pickup (reserve and collect) or delivery |
| `get_article` | Details of one article by number: brand, price, EAN, characteristics (dimensions, scope of delivery, ...), categories, images, manufacturer, plus stock in one or more markets |
| `markets_nearby` | toom markets within a radius of a place (straight-line distance, nearest first) |
| `find_article_nearby` | Where within a radius can an article (or several) be picked up right now? Real stock per market, nearest first, with phone numbers |
| `market_info` | Open now?, opening hours, upcoming closed days (holidays), services (rental van, trailer, wood cutting, ...), where the pickup point is, phone |
| `compare_articles` | 2–6 articles side by side: price, dimensions in cm/kg, stock, and every characteristic with what differs first |
| `fits_space` | Does an article fit into a space (car load area, doorway)? Honest: *fits*, *does not fit* or *unclear* when toom does not state a dimension. A side you leave out is unlimited (a door goes through a narrow opening edge-on), so give all three for a car |
| `delivery_info` | Pick up or delivery? Stock, lead time and the shipping cost actually charged side by side (toom's free-shipping limit is for parcels; bulky goods ship as freight and always pay). Uses a throw-away basket, yours is untouched |
| `get_related` | toom's own accessory and alternative suggestions: "Häufig dazu gekauft" (from purchases) and "Ähnliche Produkte" |
| `compare_markets` | Real stock per article in several markets, no basket needed |
| `export_session` | Write a market's session cookies to a local file (mode 0600) so you can look at that basket in your own browser: import it with a cookie manager extension (e.g. Cookie-Editor), then open toom.de/warenkorb. **Asks for confirmation first**: importing replaces the browser's toom.de cookies |
| `prepare_article_request` | For an article that is not in stock in a market ("Auf Anfrage bestellbar"): stock, product link, the market's phone number and a ready-made text for "Artikel anfragen" or a call. Sends nothing |
| `copy_basket` | Build a basket anew in another market and report per article what is available there |
| `plan_pickup` | Pickup plan: your primary market first, fallback markets only for what it lacks and what is small enough to transport; says when everything is at the primary market |

Prices and availability depend on the market, so every basket belongs to one market. A market switch means a new
basket there: `copy_basket` does that for you and the report shows what the other market has, in what quantity,
and price changes. `compare_markets` and `plan_pickup` only read stock (toom's stock endpoint) and never touch a basket;
`plan_pickup(apply=True)` can also build the planned baskets.

`plan_pickup` priorities: 1) everything the primary market has is picked up there; 2) only what it lacks comes
from a fallback market, and only if it is not in `bulky_skus` (articles that do not fit your transport are never
taken from a fallback); fallbacks are chosen for as few stops as possible. If `all_at_primary_market` is true, one
stop covers everything. Stock is live and can change between planning and pickup, so re-check before you go.

## Install

```sh
claude mcp add toomy -- uv --directory /path/to/toomy run toomy
```

stdio transport, no port. Any MCP client can launch `uv run toomy` the same way.

## Configuration (optional)

| Variable | Meaning |
|---|---|
| `TOOMY_DEFAULT_MARKET` | Market used when none is selected (name or id), so `set_market` is not needed |
| `TOOMY_HOME_MARKETS` | Your markets, separated by `;` (e.g. `Berlin-Friedrichshain;Berlin-Weißensee`): the default for `compare_markets` and the fallbacks of `plan_pickup` |

With these set, `markets_nearby` and `find_article_nearby` also work without `near` (around the default market).

## Examples

- *"Find the toom markets in Berlin."* → `find_markets("berlin")`
- *"Which drills are on offer?"* → `search_products("bohrmaschine", offers_only=True, sort="price_asc")`
- *"Cordless screwdrivers from Bosch or Makita, under 150 €, in stock in Prenzlau."* →
  `search_products("akkuschrauber", brands=["Bosch","Makita"], max_price=150, availability="in_market")`
- *"Where within 50 km of Cologne can I pick up this article right now?"* →
  `find_article_nearby(near="Köln", sku="<article number>", radius_km=50)`
- *"What are the dimensions and what is in the box?"* → `get_article("<article number>")`
- *"Search for cordless drills in Prenzlau."* → `set_market("Prenzlau")`, `search_products("akkuschrauber")`
- *"Which of these has all three articles in stock: Friedrichshain, Weißensee, Prenzlau?"*
  → `compare_markets(["Friedrichshain", "Weißensee", "Prenzlau"], items=[{"sku": "1490300", "quantity": 2}, ...])`
- *"Copy my basket from Friedrichshain to Weißensee."* → `copy_basket(from_market="Friedrichshain", to_market="Weißensee")`
- *"Pick everything up in market X; only if something is not there and fits my car, use another market."*
  → `plan_pickup(primary_market="X", fallback_markets=[...], bulky_skus=["<sku of what does not fit>"], items=[...])`

## Limits (please read)

- **No checkout.** The basket is an anonymous session held by the server process, not your toom account, so it does
  not show up in your browser. Reserving and paying happens on toom.de; the tools give you the article numbers,
  quantities and product links to do that.
- **Article inquiries are yours to send.** For articles that are only "Auf Anfrage bestellbar" in a market, toom.de
  offers "Artikel anfragen" on the product page. The form carries your contact data and sits behind a captcha, so
  `toomy` prepares everything (`prepare_article_request`) but does not send it and does not try to get past the captcha.
- **Distances are straight-line**, not driving distances, and no geocoder is used: `near` is a market name or id,
  a postal code, words in a market's name or city, or coordinates `lat,lon`. Stock is live and can change; the
  phone number of each market is included so you can call before a long drive.
- **Prices:** `price_eur` is what you pay. For struck-through prices toom shows, `was_price_eur` and `saving` give the
  old price and the saving. Offers (Aktion) carry no old price; their offer period is in `get_related`.
- **Accessories are toom's suggestions, not a catalogue**: "Häufig dazu gekauft" comes from purchase data, so it can
  contain other sizes of the same article (marked `same_category`) and unrelated things.
- **Unofficial and undocumented.** toom.de can change its endpoints any time; the live contract tests
  (`TOOMY_LIVE=1 uv run pytest -m live`) are there to notice that.
- **Polite by design:** requests are throttled (0.5 s apart), search reads only the first result page (toom's
  `robots.txt` disallows `?page=`; filters and sorting narrow a search down instead, and no `+` is ever put in a URL), and the client identifies itself. Personal use, please.
- Search results decode toom's `availability_code` the way the product page does: `market_reservable` (reserve for
  pickup), `market_orderable` ("Bestellbar in <Markt>": not in stock, but on the product page toom offers "Artikel
  anfragen" and the market orders it for you, possibly plus transport costs; this server does not send that
  inquiry, do it on toom.de) and `home_delivery`. For real stock quantities use `compare_markets`
  (capped at the quantity you ask for). Search by words: toom redirects an exact article number to its product page.

Planned tools and decisions are in [TODOs.md](TODOs.md).

## Development

```sh
uv run pytest                           # mocked tests against recorded real response shapes, no network
TOOMY_LIVE=1 uv run pytest -m live      # contract tests against toom.de (anonymous session, orders nothing)
```

`tests/conftest.py` is a fake toom.de (`httpx.MockTransport`) with per-market baskets and stock; its fixtures are
recorded from the real site. Behaviour that was not verified against the real site is marked `ASSUMPTION` there.

## License

AGPL-3.0-or-later, see [LICENSE](LICENSE). Not affiliated with toom Baumarkt GmbH.

---

## Deutsch

Ein MCP-Server (Python, [FastMCP](https://gofastmcp.com)) für den [toom.de](https://toom.de)-Onlineshop:
Artikel suchen, pro Markt einen Warenkorb füllen, sehen, welcher Markt was wirklich auf Lager hat, und einen
Warenkorb von einem Markt in einen anderen kopieren. Inoffiziell; er nutzt dieselben Web-Endpunkte wie die Seite.

### Tools

| Tool | Funktion |
|---|---|
| `find_markets`, `set_market` | Markt per Stadt, Name, PLZ oder ID finden und auswählen |
| `search_products` | Artikel suchen, optional eingegrenzt: `offers_only`, `sort` (Relevanz, Preis, Angebote, Bewertung, `best_rated`), `min_reviews`, `min_price`/`max_price`, `brands`, `availability` (hier vorrätig / bestellbar / Lieferung) oder toom's eigene `filters`. Jeder Treffer hat `on_offer`/`promotion` und die dekodierte Verfügbarkeit |
| `search_filters` | Welche Filter toom für einen Suchbegriff anbietet (Marke, Akku/Netz, Akku inklusive, ...) mit Trefferzahlen, um eine breite Suche einzugrenzen |
| `add_to_basket`, `view_basket`, `set_quantity`, `remove_from_basket`, `clear_basket` | Ein Warenkorb **pro Markt**; Abholung (reservieren) oder Lieferung |
| `get_article` | Details zu einem Artikel per Nummer: Marke, Preis, EAN, Merkmale (Maße, Lieferumfang, ...), Kategorien, Bilder, Hersteller, dazu der Bestand in einem oder mehreren Märkten |
| `markets_nearby` | toom-Märkte im Umkreis eines Ortes (Luftlinie, nächster zuerst) |
| `find_article_nearby` | Wo im Umkreis gibt es einen Artikel (oder mehrere) jetzt zum Abholen? Echter Bestand je Markt, nächster zuerst, mit Telefonnummer |
| `market_info` | Jetzt geöffnet?, Öffnungszeiten, kommende Schließtage (Feiertage), Services (Miettransporter, Anhänger, Holzzuschnitt, ...), wo die Abholstation ist, Telefon |
| `compare_articles` | 2–6 Artikel nebeneinander: Preis, Maße in cm/kg, Bestand und alle Merkmale, Unterschiede zuerst |
| `fits_space` | Passt ein Artikel in einen Raum (Laderaum, Durchgang)? Ehrlich: *passt*, *passt nicht* oder *unklar*, wenn toom ein Maß nicht nennt. Eine weggelassene Seite gilt als unbegrenzt (eine Tür geht hochkant durch eine schmale Öffnung), für ein Auto also alle drei angeben |
| `delivery_info` | Abholen oder liefern lassen? Bestand, Lieferzeit und die tatsächlich berechneten Versandkosten nebeneinander (die Freigrenze von toom gilt für Pakete; Sperrgut geht per Spedition und zahlt immer). Wegwerf-Warenkorb, deiner bleibt unberührt |
| `get_related` | toom's eigene Zubehör- und Alternativvorschläge: „Häufig dazu gekauft“ (aus Käufen) und „Ähnliche Produkte“ |
| `compare_markets` | Echter Bestand pro Artikel in mehreren Märkten, ohne Warenkorb |
| `export_session` | Schreibt die Session-Cookies eines Marktes in eine lokale Datei (Rechte 0600), damit du den Warenkorb im eigenen Browser ansehen kannst: mit einer Cookie-Erweiterung (z. B. Cookie-Editor) importieren, dann toom.de/warenkorb öffnen. **Fragt vorher nach**: der Import ersetzt die toom.de-Cookies des Browsers |
| `prepare_article_request` | Für Artikel, die im Markt nicht vorrätig sind („Auf Anfrage bestellbar“): Bestand, Produktlink, Telefonnummer des Marktes und ein fertiger Text für „Artikel anfragen“ oder einen Anruf. Sendet nichts |
| `copy_basket` | Warenkorb in einem anderen Markt neu aufbauen und pro Artikel berichten, was dort verfügbar ist |
| `plan_pickup` | Abholplan: zuerst der Hauptmarkt, Ausweichmärkte nur für Fehlendes, das klein genug zum Transport ist; meldet, wenn alles im Hauptmarkt liegt |

Preise und Verfügbarkeit hängen vom Markt ab, deshalb gehört jeder Warenkorb zu genau einem Markt. Ein Marktwechsel
heißt: neuer Warenkorb dort. `copy_basket` macht das und zeigt, was der andere Markt hat, in welcher Menge, und
Preisänderungen. `compare_markets` und `plan_pickup` lesen nur den Bestand und fassen keinen Warenkorb an;
`plan_pickup(apply=True)` kann die geplanten Warenkörbe auch aufbauen.

Prioritäten von `plan_pickup`: 1) Alles, was der Hauptmarkt hat, wird dort abgeholt; 2) nur Fehlendes kommt aus einem
Ausweichmarkt, und nur wenn es nicht in `bulky_skus` steht (Artikel, die nicht ins Transportmittel passen, werden nie
aus einem Ausweichmarkt genommen); Ausweichmärkte werden mit möglichst wenigen Stopps gewählt. Ist
`all_at_primary_market` wahr, deckt ein Stopp alles ab. Der Bestand ist live und kann sich zwischen Planung und
Abholung ändern, bitte vor der Fahrt noch einmal prüfen.

### Installation

```sh
claude mcp add toomy -- uv --directory /pfad/zu/toomy run toomy
```

stdio, kein Port. Andere MCP-Clients starten einfach `uv run toomy`.

### Grenzen (bitte lesen)

- **Kein Checkout.** Der Warenkorb ist eine anonyme Session im Serverprozess, nicht dein toom-Konto, und taucht
  daher nicht in deinem Browser auf. Reservieren und Bezahlen passiert auf toom.de; die Tools liefern dir
  Artikelnummern, Mengen und Produktlinks dafür.
- **Artikelanfragen schickst du selbst ab.** Für Artikel, die in einem Markt nur „Auf Anfrage bestellbar“ sind, bietet
  toom.de auf der Produktseite „Artikel anfragen“ an. Das Formular enthält deine Kontaktdaten und liegt hinter einem
  Captcha; `toomy` bereitet alles vor (`prepare_article_request`), schickt es aber nicht ab und umgeht das Captcha nicht.
- **Entfernungen sind Luftlinie**, keine Fahrstrecke, und es wird kein Geocoder benutzt: `near` ist ein
  Marktname oder eine ID, eine PLZ, Wörter aus Name oder Stadt eines Marktes oder Koordinaten `lat,lon`. Der
  Bestand ist live und kann sich ändern; die Telefonnummer jedes Marktes steht dabei, damit du vor einer langen
  Fahrt anrufen kannst.
- **Preise:** `price_eur` ist, was du zahlst. Bei durchgestrichenen Preisen stehen `was_price_eur` und `saving` für
  den alten Preis und die Ersparnis. Angebote (Aktion) haben keinen alten Preis; ihr Zeitraum steht in `get_related`.
- **Zubehör sind Vorschläge von toom, kein Katalog**: „Häufig dazu gekauft“ stammt aus Kaufdaten und kann andere
  Größen desselben Artikels (`same_category`) oder Unzusammenhängendes enthalten.
- **Inoffiziell und undokumentiert.** toom.de kann die Endpunkte jederzeit ändern; die Live-Tests
  (`TOOMY_LIVE=1 uv run pytest -m live`) sind dafür da, das zu bemerken.
- **Höflich gebaut:** gedrosselte Requests (0,5 s Abstand), die Suche liest nur die erste Ergebnisseite (die
  `robots.txt` von toom sperrt `?page=`), und der Client gibt sich zu erkennen. Bitte nur persönliche Nutzung.
- Suchergebnisse entschlüsseln toom's `availability_code` wie die Produktseite: `market_reservable` (zur Abholung
  reservierbar), `market_orderable` („Bestellbar in <Markt>“: nicht vorrätig, aber toom bietet auf der Produktseite „Artikel
  anfragen“ an und der Markt bestellt es für dich, ggf. zzgl. Transportkosten; diese Anfrage sendet der Server
  nicht, bitte auf toom.de machen) und `home_delivery`. Echte Bestandsmengen liefert
  `compare_markets` (gedeckelt auf die angefragte Menge). Mit Wörtern suchen: toom leitet eine exakte Artikelnummer
  auf die Produktseite um.

### Entwicklung

```sh
uv run pytest                           # Mock-Tests gegen aufgezeichnete echte Antwortformate, ohne Netzwerk
TOOMY_LIVE=1 uv run pytest -m live      # Contract-Tests gegen toom.de (anonyme Session, bestellt nichts)
```

### Lizenz

AGPL-3.0-or-later, siehe [LICENSE](LICENSE). Nicht mit der toom Baumarkt GmbH verbunden.
