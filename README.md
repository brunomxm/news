# news

A personal newsletter digest, styled like a headline-first news app. Static
site (`index.html` / `styles.css` / `app.js`), no build step, reads
`data/articles.json`. Headlines open an internal reader page
(`article.html` / `reader.js`) that shows the newsletter's own sanitized
HTML — text, links, images — with a "Read the original" link as a fallback.

## How content gets in

1. In Gmail, apply the label **`news`** to any newsletter issue you want to
   show up here. Nothing without that label is ever read.
2. Run the fetcher:
   ```
   scripts/venv/bin/python scripts/fetch_newsletters.py
   ```
   It rebuilds `data/articles.json` from the last 3 days of `label:news`
   mail, parsing each issue's HTML into individual article cards (title,
   summary, link, image if the newsletter hosts one externally, and
   `content_html` — a sanitized fragment of that article's own HTML for the
   reader page). Sanitization is plain rule-based code (BeautifulSoup):
   scripts/style stripped, tracking pixels and boilerplate links (ads,
   unsubscribe, sponsor blocks) removed, every tag not in a small allowlist
   (`p`, `a`, `img`, `strong`, lists, etc.) unwrapped, all attributes
   dropped except `href`/`src`/`alt`. No LLM is involved in this step.
3. Assign a `category` (`Tech`, `AI`, `Business`, `Politics`, `Italia`, or
   `Other`) to any article that comes out with `category: null` — this is a
   judgment call, done by whoever/whatever runs the fetch (see below), not
   by the script itself.
4. Commit and push. GitHub Pages redeploys automatically.

## Scheduled refresh

A recurring scheduled task re-runs step 2–4 automatically: fetch, categorize
any new `null` articles, prune anything older than 3 days, commit, push.

**Gmail auth**: the fetcher reuses the OAuth token from
`_tools/gmail-attachment-fetcher/` (kept outside this repo on purpose — the
credentials never touch git). That OAuth client's Google Auth Platform user
type is **Internal** (tied to the musixmatch.com Workspace), so the usual
7-day refresh-token expiry for "Testing" External apps doesn't apply here.

## Local preview

Just open `index.html` in a browser, or serve the folder locally.
