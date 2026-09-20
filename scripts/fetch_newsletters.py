#!/usr/bin/env python3
"""
fetch_newsletters.py — pull newsletter issues from Gmail and turn them into
the article feed the static site reads (data/articles.json).

Scope is deliberately narrow: only messages carrying the Gmail label passed
via --label (default "news") are ever read. Apply that label in Gmail to
whatever newsletters you want to show up here; nothing else is ever queried.

USAGE
-----
python fetch_newsletters.py
python fetch_newsletters.py --label news --days 3

Re-running is safe and idempotent: the feed is rebuilt from the last --days
of labelled mail each time, but any category you (or the scheduled agent)
already assigned to an article is preserved across rebuilds.
"""

import argparse
import base64
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from email.utils import parseaddr

from bs4 import BeautifulSoup
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

TOOL_DIR = "/Users/mxm/Documents/MxM Knowledge/_tools/gmail-attachment-fetcher"
DEFAULT_CREDENTIALS = os.path.join(TOOL_DIR, "client_secret.json")
DEFAULT_TOKEN = os.path.join(TOOL_DIR, "token.json")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DATA_DIR = os.path.join(os.path.dirname(SCRIPT_DIR), "data")

BORING_RE = re.compile(
    r"unsubscribe|view (this )?online|view in browser|web version|"
    r"manage (your )?(subscriptions?|preferences)|advertise with us|"
    r"advertise|sign up|update your preferences|privacy policy|"
    r"read online|share (this|on)|forward to a friend|follow us|"
    r"clicca qui|cancella l.iscrizione|annulla l.iscrizione|"
    r"gestisci le tue preferenze|leggi online|visualizza online|"
    r"^(iscriviti|accedi|scarica l.app)$|"
    r"abbonati|^regala$|scegli quali newsletter|scopri tutti i podcast|"
    r"scarica l.app del post|altre newsletter|le ultime uscite|"
    r"puoi disiscriverti|puoi iscriverti|^newsletter$|^app del post$|"
    r"scopri tutte le newsletter|"
    r"track your referrals|apply here|create your own role|"
    r"reserve your spot|register now|save your spot|book a demo|"
    r"get .*(free|started)$|see how it works|refer\.tldr|"
    r"linkedin\.com/in/",
    re.I,
)
TITLE_IS_URL_RE = re.compile(r"^https?://", re.I)
SPONSOR_RE = re.compile(r"\(sponsor\)", re.I)
SPONSOR_BLOCK_ID_RE = re.compile(r"sponsor|sponsy|together-with", re.I)
SIGNOFF_RE = re.compile(r"thanks for reading|if you have any comments or feedback", re.I)
TRACKING_IMG_RE = re.compile(r"track|pixel|beacon|open\.gif|spacer", re.I)
WELCOME_RE = re.compile(
    r"welcome to|you.re on the list|grazie per la registrazione|"
    r"thank(s| you) for (subscribing|signing up)|confirm your subscription|"
    r"you.re subscribed|benvenut|conferma (la tua )?iscrizione",
    re.I,
)

READER_ALLOWED_TAGS = {
    "p", "br", "strong", "b", "em", "i", "a", "img",
    "ul", "ol", "li", "blockquote", "h1", "h2", "h3", "h4",
}


def sanitize_fragment(container) -> str | None:
    """Turn an article's container element into safe, minimal HTML for the
    in-app reader: drop scripts/styles/attributes, tracking pixels, and
    boilerplate links, and unwrap every layout tag (div/td/table/span/...)
    down to a small set of structural tags. No JS is ever kept."""
    frag = BeautifulSoup(str(container), "html.parser")

    for tag in frag(["script", "style"]):
        tag.decompose()

    for img in frag.find_all("img"):
        src = img.get("src", "")
        if not src.startswith("http") or TRACKING_IMG_RE.search(src):
            img.decompose()
            continue
        alt = img.get("alt", "")
        img.attrs = {"src": src, **({"alt": alt} if alt else {})}

    for a in frag.find_all("a"):
        href = a.get("href", "")
        text = clean_text(a.get_text(" "))
        if not href.startswith("http") or BORING_RE.search(text) or BORING_RE.search(href):
            a.unwrap()
            continue
        a.attrs = {"href": href}

    for tag in frag.find_all(True):
        if tag.name not in READER_ALLOWED_TAGS:
            tag.unwrap()

    for tag in frag.find_all(["p", "li", "blockquote"]):
        if not tag.get_text(strip=True) and not tag.find("img"):
            tag.decompose()

    html = frag.decode_contents().strip()
    return html or None


def get_service(credentials_path: str, token_path: str):
    creds = None
    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(credentials_path):
                sys.exit(f"Can't find {credentials_path}. Pass --credentials.")
            flow = InstalledAppFlow.from_client_secrets_file(credentials_path, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(token_path, "w") as f:
            f.write(creds.to_json())

    return build("gmail", "v1", credentials=creds)


def list_message_ids(service, query: str):
    ids = []
    page_token = None
    while True:
        resp = (
            service.users()
            .messages()
            .list(userId="me", q=query, pageToken=page_token)
            .execute()
        )
        ids.extend(m["id"] for m in resp.get("messages", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return ids


def get_header(headers, name):
    for h in headers:
        if h["name"].lower() == name.lower():
            return h["value"]
    return ""


def extract_bodies(payload):
    """Walk the MIME tree and return (html, plaintext)."""
    html, text = None, None
    stack = [payload]
    while stack:
        part = stack.pop()
        if part.get("parts"):
            stack.extend(part["parts"])
        mime = part.get("mimeType", "")
        body = part.get("body", {})
        data = body.get("data")
        if not data:
            continue
        decoded = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode(
            "utf-8", errors="replace"
        )
        if mime == "text/html" and html is None:
            html = decoded
        elif mime == "text/plain" and text is None:
            text = decoded
    return html, text


def clean_text(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def find_image(container) -> str | None:
    img = container.find("img")
    if img is None:
        return None
    src = img.get("src", "")
    if not src.startswith("http"):
        return None
    if TRACKING_IMG_RE.search(src):
        return None
    width = img.get("width")
    height = img.get("height")
    try:
        if width and height and int(width) <= 2 and int(height) <= 2:
            return None
    except ValueError:
        pass
    return src


def extract_articles(html: str, plaintext: str, subject: str):
    if WELCOME_RE.search(subject):
        return _fallback_article(BeautifulSoup(html, "html.parser"), plaintext, subject)

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    for hidden in soup.find_all(style=re.compile(r"display:\s*none", re.I)):
        hidden.decompose()

    seen_href = set()
    articles = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.startswith("mailto:") or href in seen_href:
            continue
        strong = a.find(["strong", "b"])
        title = clean_text(strong.get_text(" ") if strong else a.get_text(" "))
        if len(title) < 8 or TITLE_IS_URL_RE.match(title):
            continue
        if "→" in title or "➡️" in title or "↗️" in title:
            continue
        if BORING_RE.search(title) or BORING_RE.search(href):
            continue
        if SPONSOR_RE.search(title):
            continue
        if a.find_parent(id=SPONSOR_BLOCK_ID_RE) is not None:
            continue

        container = a.find_parent(["div", "td", "p", "li"]) or a
        full_text = clean_text(container.get_text(" "))
        summary = full_text.replace(title, "", 1).strip(" -–—:|")
        if SIGNOFF_RE.search(summary) or SIGNOFF_RE.search(full_text):
            continue
        seen_href.add(href)
        if len(summary) > 400:
            summary = summary[:397].rsplit(" ", 1)[0] + "..."

        articles.append(
            {
                "title": title,
                "link": href,
                "summary": summary,
                "image": find_image(container),
                "content_html": sanitize_fragment(container),
            }
        )

    if articles:
        return articles

    return _fallback_article(soup, plaintext, subject)


def _fallback_article(soup, plaintext: str, subject: str):
    """No recognizable article blocks (welcome/confirmation email, or a
    format the heuristics don't understand) -> show the whole message as a
    single card."""
    link = ""
    for a in soup.find_all("a", href=True):
        if re.search(r"view (this )?online|web version|view in browser", a.get_text(" "), re.I):
            link = a["href"]
            break
    summary = clean_text(plaintext)[:400]
    return [{"title": subject, "link": link, "summary": summary, "image": None, "content_html": None}]


def stable_id(message_id: str, index: int, href: str) -> str:
    raw = f"{message_id}:{index}:{href}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def load_existing(data_path: str) -> dict:
    if os.path.exists(data_path):
        with open(data_path) as f:
            return json.load(f)
    return {"generated_at": None, "articles": []}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--label", default="news", help="Gmail label to fetch (default: news)")
    ap.add_argument("--days", type=int, default=3, help="Only keep articles from the last N days")
    ap.add_argument("--credentials", default=DEFAULT_CREDENTIALS)
    ap.add_argument("--token", default=DEFAULT_TOKEN)
    ap.add_argument("--data-dir", default=DEFAULT_DATA_DIR)
    args = ap.parse_args()

    articles_path = os.path.join(args.data_dir, "articles.json")
    existing = load_existing(articles_path)
    prior_categories = {a["id"]: a.get("category") for a in existing.get("articles", [])}

    service = get_service(args.credentials, args.token)

    query = f"label:{args.label} newer_than:{args.days}d"
    message_ids = list_message_ids(service, query)
    print(f"Found {len(message_ids)} message(s) matching {query!r}.")

    cutoff = datetime.now(timezone.utc) - timedelta(days=args.days)
    all_articles = []

    for mid in message_ids:
        msg = service.users().messages().get(userId="me", id=mid, format="full").execute()
        headers = msg["payload"].get("headers", [])
        subject = get_header(headers, "Subject") or "(no subject)"
        from_header = get_header(headers, "From")
        source_name, _ = parseaddr(from_header)
        if not source_name:
            source_name = from_header
        msg_date = datetime.fromtimestamp(int(msg["internalDate"]) / 1000, tz=timezone.utc)
        if msg_date < cutoff:
            continue

        html, plaintext = extract_bodies(msg["payload"])
        if not html and not plaintext:
            continue

        parsed = extract_articles(html or "", plaintext or "", subject)
        for i, item in enumerate(parsed):
            if not item["title"] or not item["link"]:
                continue
            aid = stable_id(mid, i, item["link"])
            all_articles.append(
                {
                    "id": aid,
                    "source": source_name,
                    "subject": subject,
                    "date": msg_date.isoformat(),
                    "title": item["title"],
                    "summary": item["summary"],
                    "link": item["link"],
                    "image": item["image"],
                    "content_html": item.get("content_html"),
                    "category": prior_categories.get(aid),
                }
            )

    all_articles.sort(key=lambda a: a["date"], reverse=True)

    os.makedirs(args.data_dir, exist_ok=True)
    with open(articles_path, "w") as f:
        json.dump(
            {"generated_at": datetime.now(timezone.utc).isoformat(), "articles": all_articles},
            f,
            indent=2,
            ensure_ascii=False,
        )

    uncategorized = sum(1 for a in all_articles if not a["category"])
    print(f"Wrote {len(all_articles)} article(s) to {articles_path} ({uncategorized} need a category).")


if __name__ == "__main__":
    main()
