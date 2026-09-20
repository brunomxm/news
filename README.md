# news

A personal newsletter digest, styled like a headline-first news app. Static
site (`index.html` / `styles.css` / `app.js`), no build step, reads
`data/articles.json`.

## How content gets in

1. In Gmail, apply the label **`news`** to any newsletter issue you want to
   show up here. Nothing without that label is ever read.
2. Run the fetcher:
   ```
   scripts/venv/bin/python scripts/fetch_newsletters.py
   ```
   It rebuilds `data/articles.json` from the last 30 days of `label:news`
   mail, parsing each issue's HTML into individual article cards (title,
   summary, link, image if the newsletter hosts one externally).
3. Assign a `category` (`Tech`, `AI`, `Business`, `Politics`, `Italia`, or
   `Other`) to any article that comes out with `category: null` — this is a
   judgment call, done by whoever/whatever runs the fetch (see below), not
   by the script itself.
4. Commit and push. GitHub Pages redeploys automatically.

## Scheduled refresh

A recurring scheduled task re-runs step 2–4 automatically: fetch, categorize
any new `null` articles, prune anything older than 30 days, commit, push.

**Gmail auth risk**: the fetcher reuses the OAuth token from
`_tools/gmail-attachment-fetcher/` (kept outside this repo on purpose — the
credentials never touch git). If that Google Cloud OAuth client is still in
"Testing" publishing status, refresh tokens expire after ~7 days and the
scheduled run will start failing silently. Set it to "In production" in
Google Cloud Console to avoid that.

## Local preview

Just open `index.html` in a browser, or serve the folder locally.
