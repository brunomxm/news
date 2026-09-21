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

Re-running is safe and idempotent: the feed is fully rebuilt from the last
--days of labelled mail each time. Section assignment is deterministic (see
SECTION_MAP below) — no judgment call, no LLM, nothing to preserve.
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
    r"scopri tutte le newsletter|continua (a leggere)?\s*(sul |su )?\w*$|"
    r"track your referrals|apply here|create your own role|"
    r"reserve your spot|register now|save your spot|book a demo|"
    r"get .*(free|started)$|see how it works|refer\.tldr|"
    r"linkedin\.com/in/|"
    r"^read more$|^privacy (statement|policy)$|^terms (&|and) conditions$|"
    r"^terms of (service|use)$|^cookie policy$|"
    r"\d+% off|for \d+ months?$|"
    r"is one of inc\.'?s best|powered by beehiiv|^curated by",
    re.I,
)
TITLE_IS_URL_RE = re.compile(r"^https?://", re.I)
SPONSOR_RE = re.compile(r"\(sponsor\)", re.I)
SPONSOR_BLOCK_ID_RE = re.compile(r"sponsor|sponsy|together-with", re.I)
SIGNOFF_RE = re.compile(r"thanks for reading|if you have any comments or feedback", re.I)
AUTHOR_CLASS_RE = re.compile(r"author|byline", re.I)
POST_TITLE_CLASS_RE = re.compile(r"post-title", re.I)
READ_IN_APP_RE = re.compile(r"read in app", re.I)
HERO_BODY_STOP_RE = re.compile(
    r"upgrade to paid|unsubscribe|get the app|start writing|©|"
    r"subscribe to .* to unlock|read in app|listen to post|"
    r"leave a comment|give a gift subscription|claim my free post|"
    r"share this post|subscribe here for more",
    re.I,
)
TRACKING_IMG_RE = re.compile(
    r"track|pixel|beacon|open\.gif|spacer|logo|banner|header|footer|"
    r"icon|avatar|badge|divider|sponsor|\bad\b",
    re.I,
)

# Deterministic source -> section mapping. No AI/LLM classification — this is
# the single place to edit when a new newsletter is labelled "news".
SECTION_MAP = {
    "Reuters": "Latest",
    "Reuters Daily Briefing": "Latest",
    "The Conversation": "World & Ideas",
    "The Conversation AI": "World & Ideas",
    "Da Costa a Costa": "World & Ideas",
    "Francesco Costa": "World & Ideas",
    "TLDR": "Technology & AI",
    "TLDR AI": "Technology & AI",
    "TLDR Hardware": "Technology & AI",
    "TLDR Founders": "Technology & AI",
    "TLDR Product": "Technology & AI",
    "Interconnects": "Technology & AI",
    "Interconnects by Nathan Lambert": "Technology & AI",
    "Stratechery": "Technology & AI",
    "Azeem Azhar": "Technology & AI",
    "Azeem Azhar, Exponential View": "Technology & AI",
    "Exponential View": "Technology & AI",
    "Razib Khan": "World & Ideas",
    "Razib Khan's Unsupervised Learning": "World & Ideas",
    "Quanta": "Culture",
    "Quanta Magazine": "Culture",
    "Works in Progress": "Culture",
    "The New Yorker": "Culture",
    "New Yorker Books": "Culture",
    "Il Post": "Culture",
    "Il Post - Colonne": "Culture",
    "Il Post - Ok Boomer!": "Culture",
    "Water & Music": "Music & Industry",
    "Water and Music": "Music & Industry",
    "Music Business Worldwide": "Music & Industry",
}
SECTION_ORDER = ["Latest", "World & Ideas", "Technology & AI", "Culture", "Music & Industry"]
DEFAULT_SECTION = "Latest"
WELCOME_RE = re.compile(
    r"welcome to|you.re on the list|grazie per la registrazione|"
    r"thank(s| you) for (subscribing|signing up)|confirm your subscription|"
    r"you.re subscribed|benvenut|conferma (la tua )?iscrizione",
    re.I,
)

# Gmail-forwarded newsletters (subject starts with "Fwd:"/"Fw:") carry the
# real sender in a quoted header block in the plaintext body, not in the
# message's own From header -- without this, every forwarded issue gets
# attributed to whoever hit "Forward", not the newsletter.
FWD_SUBJECT_RE = re.compile(r"^\s*(fwd|fw)\s*:", re.I)
FORWARDED_FROM_RE = re.compile(
    r"-{5,}\s*forwarded message\s*-{5,}.*?\nFrom:\s*([^\n]+)", re.I | re.S
)

READER_ALLOWED_TAGS = {
    "p", "br", "strong", "b", "em", "i", "a", "img",
    "ul", "ol", "li", "blockquote", "h1", "h2", "h3", "h4",
}


def sanitize_fragment(container, title: str = "") -> str | None:
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

    # The reader page already shows the title as its own headline; drop a
    # leading anchor/strong that just repeats it so the body doesn't open
    # with the same line twice.
    if title:
        first = frag.find(True)
        if first is not None and clean_text(first.get_text(" ")) == clean_text(title):
            first.decompose()
            for _ in range(2):
                nxt = frag.find(True)
                if nxt is not None and nxt.name == "br":
                    nxt.decompose()
                else:
                    break

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


def sentence_containing(full_text: str, fragment: str) -> str | None:
    """Return the full sentence inside full_text that contains fragment, or
    None if it can't be found. Used to recover real context for titles that
    are just a lowercase mid-sentence link fragment (common in Reuters-style
    briefings, where a whole paragraph of prose carries several inline
    links rather than one link per headline)."""
    if not fragment:
        return None
    sentences = re.split(r"(?<=[.!?])\s+", full_text)
    for s in sentences:
        if fragment.lower() in s.lower():
            return clean_text(s)
    return None


def recover_substack_post(soup) -> dict | None:
    """Substack renders each edition as a single essay with its own
    <h1 class="post-title"> -- sometimes wrapped in a permalink <a>
    (Interconnects, Razib Khan), sometimes not (Azeem Azhar's Monday-data
    cross-promotion inserts). Either way, treating it as a link-list digest
    and running the generic per-anchor scan over the rest of the post is
    wrong: every inline citation link, "Leave a comment", "Upgrade to paid",
    audio-player caption, etc. inside the essay body would each spawn its
    own bogus "article". Extract the whole post as ONE article instead;
    extract_articles returns this directly, skipping the per-anchor loop."""
    heading = soup.find(["h1", "h2", "h3"], class_=POST_TITLE_CLASS_RE)
    if heading is None:
        return None

    title_link = heading.find("a", href=True) or heading.find_parent("a", href=True)
    hero_href = title_link["href"] if title_link else None

    if not hero_href:
        for a in soup.find_all("a", href=True):
            if READ_IN_APP_RE.search(clean_text(a.get_text(" "))):
                hero_href = a["href"]
                break

    if not hero_href:
        for a in soup.find_all("a", href=True):
            href = a["href"]
            text = clean_text(a.get_text(" "))
            if href.startswith("mailto:") or len(text) < 8:
                continue
            if a.find_parent(class_=AUTHOR_CLASS_RE) is not None:
                continue
            if BORING_RE.search(text) or BORING_RE.search(href):
                continue
            hero_href = href
            break
    if not hero_href:
        return None

    title = clean_text(heading.get_text(" "))
    body_parts = []
    body_len = 0
    for el in heading.find_all_next(["p", "h3"]):
        if el.get("class") and POST_TITLE_CLASS_RE.search(" ".join(el.get("class"))):
            continue
        if el.find_parent(class_=AUTHOR_CLASS_RE) is not None:
            continue
        text = clean_text(el.get_text(" "))
        if not text:
            continue
        if HERO_BODY_STOP_RE.search(text):
            break
        body_parts.append(el)
        body_len += len(text)
        if body_len > 4000:
            break

    hero_html = "".join(str(el) for el in body_parts)
    hero_fragment = BeautifulSoup(f"<div>{hero_html}</div>", "html.parser")
    summary = clean_text(hero_fragment.get_text(" "))[:400]

    return {
        "title": title,
        "link": hero_href,
        "summary": summary,
        "image": find_image(hero_fragment),
        "content_html": sanitize_fragment(hero_fragment, title),
        "_consumed_href": hero_href,
    }


def extract_articles(html: str, plaintext: str, subject: str):
    if WELCOME_RE.search(subject):
        return _fallback_article(BeautifulSoup(html, "html.parser"), plaintext, subject)

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    for hidden in soup.find_all(style=re.compile(r"display:\s*none", re.I)):
        hidden.decompose()

    seen_href = set()
    seen_containers = set()
    articles = []

    substack_post = recover_substack_post(soup)
    if substack_post is not None:
        substack_post.pop("_consumed_href")
        return [substack_post]

    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.startswith("mailto:") or href in seen_href:
            continue
        if a.find_parent(class_=AUTHOR_CLASS_RE) is not None:
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
        # Long-form essay newsletters (e.g. Francesco Costa, Il Post's evening
        # digest) put many links inside one shared paragraph. Without this,
        # every link in that paragraph becomes its own "article" with the
        # same body, flooding the feed with near-duplicates of one piece.
        container_key = id(container)
        if container_key in seen_containers:
            continue

        full_text = clean_text(container.get_text(" "))
        # An anchor that doesn't start its container's text is a link
        # embedded mid-paragraph (typical of link-dense briefing prose, e.g.
        # Reuters Daily Briefing) rather than a real headline -- "UN General
        # Assembly," or "parliamentary election" read like fragments because
        # they are. Swap in the full sentence they came from when we can
        # find it, regardless of the fragment's own capitalization.
        if not full_text.lower().startswith(title.lower()):
            better = sentence_containing(full_text, title)
            if better:
                if BORING_RE.search(better) or SIGNOFF_RE.search(better):
                    continue
                if len(better) > 200:
                    better = better[:197].rsplit(" ", 1)[0] + "…"
                title = better
        summary = full_text.replace(title, "", 1).strip(" -–—:|")
        if SIGNOFF_RE.search(summary) or SIGNOFF_RE.search(full_text):
            continue
        seen_href.add(href)
        seen_containers.add(container_key)
        if len(summary) > 400:
            summary = summary[:397].rsplit(" ", 1)[0] + "..."

        articles.append(
            {
                "title": title,
                "link": href,
                "summary": summary,
                "image": find_image(container),
                "content_html": sanitize_fragment(container, title),
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


def word_count(content_html: str | None) -> int:
    if not content_html:
        return 0
    text = BeautifulSoup(content_html, "html.parser").get_text(" ")
    return len(text.split())


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--label", default="news", help="Gmail label to fetch (default: news)")
    ap.add_argument("--days", type=int, default=3, help="Only keep articles from the last N days")
    ap.add_argument("--credentials", default=DEFAULT_CREDENTIALS)
    ap.add_argument("--token", default=DEFAULT_TOKEN)
    ap.add_argument("--data-dir", default=DEFAULT_DATA_DIR)
    args = ap.parse_args()

    articles_path = os.path.join(args.data_dir, "articles.json")

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

        if FWD_SUBJECT_RE.match(subject):
            fwd_match = FORWARDED_FROM_RE.search(plaintext or "")
            if fwd_match:
                fwd_name, _ = parseaddr(fwd_match.group(1).strip())
                if fwd_name:
                    source_name = fwd_name

        parsed = extract_articles(html or "", plaintext or "", subject)
        for i, item in enumerate(parsed):
            if not item["title"] or not item["link"]:
                continue
            aid = stable_id(mid, i, item["link"])
            wc = word_count(item.get("content_html"))
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
                    "word_count": wc,
                    "reading_time_min": max(1, round(wc / 200)) if wc >= 60 else None,
                    "section": SECTION_MAP.get(source_name, DEFAULT_SECTION),
                }
            )

    # Substack sometimes delivers the same edition as two separate messages
    # (e.g. a post notification plus a comment/cross-post trigger) -- collapse
    # those to the first-seen copy so the feed doesn't show one essay twice.
    seen_source_title = set()
    deduped = []
    for a in all_articles:
        key = (a["source"], a["title"])
        if key in seen_source_title:
            continue
        seen_source_title.add(key)
        deduped.append(a)
    all_articles = deduped

    all_articles.sort(key=lambda a: a["date"], reverse=True)

    os.makedirs(args.data_dir, exist_ok=True)
    with open(articles_path, "w") as f:
        json.dump(
            {"generated_at": datetime.now(timezone.utc).isoformat(), "articles": all_articles},
            f,
            indent=2,
            ensure_ascii=False,
        )

    print(f"Wrote {len(all_articles)} article(s) to {articles_path}.")


if __name__ == "__main__":
    main()
