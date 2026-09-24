# The Daily

A personal, finite, editorial reading page assembled from newsletters — a
print-inspired "Chronicle" design: warm paper background, Playfair Display
headlines, DM Sans metadata, full-color editorial photography, a single
restrained brick-red accent, no cards, no build step. Static site
(`index.html` / `styles.css` / `app.js`), reads `data/articles.json`.
Headlines open an internal reader page (`article.html` / `reader.js`) that
shows the newsletter's own sanitized HTML, with "Read the original" as a
fallback link.

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

A hero + "Latest dispatches" rail sit side by side at the top: the hero is
picked deterministically (`config.js`'s `SOURCE_PRIORITY`/`SOURCE_TYPE`/
`TYPE_HERO_BONUS` — a strong analysis/long-form source can outrank a newer
but slighter item; image availability only nudges, never decides), and the
rail is the `Latest` section's wire items. Below that, each remaining
non-empty section renders in a fixed order with its own template — one
secondary story + compacts for World & Ideas, a 3-column compact grid (plus
an analysis-source lead pulled out for breathing room) for Technology & AI, a
feature + compacts for Culture, compacts for Music & Industry. A section with
zero items simply doesn't render — the page is finite and adapts to what's
actually in the mailbox that day, rather than forcing a fixed shape. Any
article whose body exceeds ~300 words is pulled out as a distinct "long read"
band. A missing/broken image never leaves an empty box — the container is
hidden and the layout falls back to a text-only composition.

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

A handful of CSS custom properties for color (paper `#F5F2E9`, ink `#242720`,
muted `#77776C`, one editorial red `#AA3E2B` — no other colors anywhere),
Playfair Display for headlines, DM Sans for metadata/labels, zero
border-radius and zero box-shadow anywhere, a hairline rule system instead of
containers, and the red used only for the eyebrow dot, section-rule segment,
long-read tag, link hover, and the closing end-mark. Article photography
keeps its original colors. Per-section accent colors are
wired through CSS variables (`--accent-latest`, `--accent-tech`, ...) but all
currently point at the same red, matching the source design; repointing one
section to a different color is a one-line change. Dark mode is intentionally
deferred (colors are already CSS variables, so it's a future token swap, not
a refactor). Fonts are loaded from Google Fonts rather than self-hosted, as a
pragmatic simplification.

## Local preview

Just open `index.html` in a browser, or serve the folder locally.
