# toomy 🔨

**EN** · [DE](#deutsch)

An MCP server (Python, [FastMCP](https://gofastmcp.com)) for the [toom.de](https://toom.de) DIY online shop:
search articles, fill a basket per market, see which market actually has what in stock, and copy a basket from
one market to another. Unofficial; it talks to the same web endpoints the toom.de site uses.

## What it does

| Tool | What it does |
|---|---|
| `find_markets`, `set_market` | Find a toom market ("Markt") by city, name, postal code or id and select it |
| `search_products` | Search for articles (first result page, price and toom's availability hints) |
| `add_to_basket`, `view_basket`, `set_quantity`, `remove_from_basket`, `clear_basket` | A basket **per market**; pickup (reserve and collect) or delivery |
| `compare_markets` | Real stock per article in several markets, no basket needed |
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

## Examples

- *"Find the toom markets in Berlin."* → `find_markets("berlin")`
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
- **Unofficial and undocumented.** toom.de can change its endpoints any time; the live contract tests
  (`TOOMY_LIVE=1 uv run pytest -m live`) are there to notice that.
- **Polite by design:** requests are throttled (0.5 s apart), search reads only the first result page (toom's
  `robots.txt` disallows `?page=`), and the client identifies itself. Personal use, please.
- Search results decode toom's `availability_code` the way the product page does: `market_reservable` (reserve for
  pickup), `market_orderable` ("Bestellbar in <Markt>": not in stock, but on the product page toom offers "Artikel
  anfragen" and the market orders it for you, possibly plus transport costs; this server does not send that
  inquiry, do it on toom.de) and `home_delivery`. For real stock quantities use `compare_markets`
  (capped at the quantity you ask for). Search by words: toom redirects an exact article number to its product page.

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
| `search_products` | Artikel suchen (erste Ergebnisseite, Preis, Verfügbarkeitshinweise von toom) |
| `add_to_basket`, `view_basket`, `set_quantity`, `remove_from_basket`, `clear_basket` | Ein Warenkorb **pro Markt**; Abholung (reservieren) oder Lieferung |
| `compare_markets` | Echter Bestand pro Artikel in mehreren Märkten, ohne Warenkorb |
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
