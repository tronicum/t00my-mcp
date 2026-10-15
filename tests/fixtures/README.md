# Test fixtures

Short, trimmed recordings of public responses from toom.de (a product page, a search page, a basket line, product
recommendations and a subset of the market list). They exist only so that the test suite runs offline and
reproducibly against the real *shape* of toom's answers.

- Contact data was replaced by placeholders (`+49 000 0000000`, `*@example.invalid`, a made-up manufacturer address).
  Market addresses and coordinates stay, because the radius search is tested with them.
- Texts, names, prices and image links remain the property of their owners. They are kept short and are **not**
  covered by this project's AGPL license, which applies to the code.
- Nothing here is current: prices, stock and opening hours are snapshots. The opt-in live tests
  (`T00MY_LIVE=1 uv run pytest -m live`) check that the real site still has these shapes.
