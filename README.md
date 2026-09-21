# The Daily

A personal, finite, editorial reading page assembled from newsletters — the
"Broadsheet Quiet" direction: warm paper background, serif headlines, hairline
rules, no cards, no images by default. Static site (`index.html` /
`styles.css` / `app.js`), no build step, reads `data/articles.json`. Headlines
open an internal reader page (`article.html` / `reader.js`) that shows the
newsletter's own sanitized HTML, with "Read the original" as a fallback link.

The publication name is set in one place: `config.js`.

## How content gets in

1. In Gmail, apply the label **`news`** to any newsletter issue you want to
   show up here. Nothing without that label is ever read.
2. Run the fetcher:
   ```
   scripts/venv/bin/python scripts/fetch_newsletters.py
   ```
   It rebuilds `data/articles.json` from the last 3 days of `label:news`
   mail, parsing each issue's HTML into individual article cards: title,
   summary, link, image (only if the newsletter hosts one externally and it
   passes basic admission filtering), word count, and `content_html` — a
   sanitized fragment of that article's own HTML for the reader page.
   Sanitization is plain rule-based code (BeautifulSoup): scripts/styles
   stripped, tracking pixels and boilerplate links (ads, unsubscribe,
   sponsor blocks, logos/banners) removed, every tag not in a small
   allowlist (`p`, `a`, `img`, `strong`, lists, headings, blockquote)
   unwrapped, all attributes dropped except `href`/`src`/`alt`. No LLM is
   involved in this step.
3. Section assignment is **deterministic**, not judged: `SECTION_MAP` in
   `scripts/fetch_newsletters.py` maps each newsletter's display name to one
   of five sections (`Latest`, `World & Ideas`, `Technology & AI`, `Culture`,
   `Music & Industry`). Add a new source there when you label a new
   newsletter; anything unmapped defaults to `Latest`. There is no AI
   classification step and nothing to preserve between runs.
4. Commit and push. GitHub Pages redeploys automatically.

## Homepage composition

One sitewide hero (the most recent article overall), then each non-empty
section in a fixed order, each with its own template — pure headline list for
Latest, one secondary story + compacts for World & Ideas, a 3-column compact
grid for Technology & AI, a feature + compacts for Culture, compacts (+ an
optional long read) for Music & Industry. A section with zero items simply
doesn't render — the page is finite and adapts to what's actually in the
mailbox that day, rather than forcing a fixed shape. Any article whose body
exceeds ~300 words is pulled out as a distinct "long read" band.

## Scheduled refresh

A recurring scheduled task re-runs the fetch and pushes the updated
`data/articles.json` — no categorization step needed now that sectioning is
deterministic.

**Gmail auth**: the fetcher reuses the OAuth token from
`_tools/gmail-attachment-fetcher/` (kept outside this repo on purpose — the
credentials never touch git). That OAuth client's Google Auth Platform user
type is **Internal** (tied to the musixmatch.com Workspace), so the usual
7-day refresh-token expiry for "Testing" External apps doesn't apply here.

## Design system

Implements the "Broadsheet Quiet" spec: 10 CSS custom properties for color
(no other colors anywhere), Source Serif 4 for headlines/body/deks, Libre
Franklin for metadata/labels, zero border-radius and zero box-shadow anywhere
including images, a hairline rule system instead of containers, and an oxblood
accent used only for the section-rule segment, the long-read tag, link hover,
and the closing end-mark. Dark mode is intentionally deferred (colors are
already CSS variables, so it's a future token swap, not a refactor). Fonts
are loaded from Google Fonts rather than self-hosted, as a pragmatic
simplification — see the implementation notes below for other deviations.

## Local preview

Just open `index.html` in a browser, or serve the folder locally.
