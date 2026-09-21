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
import html
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from email.utils import parseaddr

from bs4 import BeautifulSoup, Tag
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
    r"is one of inc\.'?s best|powered by beehiiv|^curated by|"
    r"^book a call$|^tutte le newsletter$",
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
    "The AI Musicpreneur": "Music & Industry",
}
SECTION_ORDER = ["Latest", "World & Ideas", "Technology & AI", "Culture", "Music & Industry"]
DEFAULT_SECTION = "Latest"
WELCOME_RE = re.compile(
    r"welcome to|you.re on the list|grazie per la registrazione|"
    r"thank(s| you) for (subscribing|signing up)|confirm your subscription|"
    r"you.re subscribed|benvenut|conferma (la tua )?iscrizione|"
    r"questo messaggio .{0,20}confermare la tua iscrizione",
    re.I,
)

# Some newsletters are a single flowing digest/letter with no per-topic
# headline markup at all -- unlike Il Post's digest style (each item is its
# own short blurb) or Substack's h1.post-title, every inline citation link
# here would otherwise become its own fake "article" titled with whatever
# sentence it sits in, and even the best-recovered sentence reads badly as
# a bold headline since there never was a real one -- the message's own
# subject line is. Known senders in this format get the whole-message
# treatment instead of the per-link digest scan.
FULL_LETTER_SENDERS = {"Francesco Costa", "Reuters Daily Briefing", "Reuters"}

# Gmail-forwarded newsletters (subject starts with "Fwd:"/"Fw:") carry the
# real sender in a quoted header block in the plaintext body, not in the
# message's own From header -- without this, every forwarded issue gets
# attributed to whoever hit "Forward", not the newsletter.
FWD_SUBJECT_RE = re.compile(r"^\s*(fwd|fw)\s*:", re.I)
FORWARDED_FROM_RE = re.compile(
    r"-{5,}\s*forwarded message\s*-{5,}.*?\nFrom:\s*([^\n]+)", re.I | re.S
)
FORWARDED_SUBJECT_RE = re.compile(
    r"-{5,}\s*forwarded message\s*-{5,}.*?\nSubject:\s*([^\n]+)", re.I | re.S
)
# Gmail's own human-readable format, e.g. "Sat, Sep 19, 2026 at 8:16 AM" --
# no timezone given, so the parsed value is treated as UTC as an
# approximation; good enough for the date/time shown in the UI, and far
# better than showing the forward's own send time as if it were the
# newsletter's original date.
FORWARDED_DATE_RE = re.compile(
    r"-{5,}\s*forwarded message\s*-{5,}.*?\nDate:\s*([^\n]+)", re.I | re.S
)
FORWARD_HEADER_RE = re.compile(r"-{5,}\s*forwarded message\s*-{5,}\n.*?\n\s*\n", re.I | re.S)

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

    # a/img attrs were already reduced to href/src/alt above; strip every
    # other attribute (style, class, data-*, ...) from the remaining tags so
    # no inline styling or tracking metadata survives into the reader page.
    for tag in frag.find_all(True):
        if tag.name not in ("a", "img"):
            tag.attrs = {}

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


def parse_forwarded_date(date_str: str) -> "datetime | None":
    """Parse Gmail's own human-readable forwarded-header date, e.g. "Sat,
    Sep 19, 2026 at 8:16 AM". Returns None if it doesn't match that shape
    (a differently-worded mail client, a non-English locale, ...) so the
    caller can fall back to the message's own received date."""
    cleaned = clean_text(date_str).replace(" at ", " ")
    try:
        return datetime.strptime(cleaned, "%a, %b %d, %Y %I:%M %p").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        return None


def br_split_groups(container):
    """If `container` directly stacks multiple <br>-separated items -- e.g.
    Il Post's digest putting several unrelated one-line news items (each
    with its own link) in one shared <td>, split only by <br> -- return
    each item's own direct children as a separate group, so callers can
    build a per-item container instead of one that bleeds all the items'
    content together. Returns None when there's at most one anchor-bearing
    group, which means this is just ordinary prose with an incidental line
    break, not a stacked list of separate items -- the existing
    whole-container behavior (correct for Reuters/Costa-style flowing
    paragraphs with many links in one sentence) is left alone."""
    if container.find("br") is None:
        return None
    groups, current = [], []
    for child in container.children:
        if getattr(child, "name", None) == "br":
            groups.append(current)
            current = []
        else:
            current.append(child)
    groups.append(current)
    groups = [g for g in groups if any(clean_text(str(n)) for n in g)]

    def has_anchor(group):
        for n in group:
            if isinstance(n, Tag) and (n.name == "a" or n.find("a") is not None):
                return True
        return False

    if sum(1 for g in groups if has_anchor(g)) < 2:
        return None
    return groups


def group_index_containing(groups, anchor) -> int | None:
    for i, g in enumerate(groups):
        for n in g:
            if n is anchor or (isinstance(n, Tag) and n.find(lambda t: t is anchor) is not None):
                return i
    return None


def raw_text_with_line_breaks(container) -> str:
    """Like container.get_text(" ") but a <br> becomes a real "\\n" instead
    of just another word-separator -- get_text's separator is inserted
    between every text node regardless of whether a <br> was actually
    there, so it can't otherwise tell a deliberate line break (e.g. Il
    Post's "Ok Boomer!" stacking three unrelated titles in one <td> with
    <br> and no punctuation between them) from two halves of one sentence
    split across adjacent inline tags. Reparses a copy so the original
    container (still needed for sanitize_fragment/find_image) is untouched."""
    frag = BeautifulSoup(str(container), "html.parser")
    for br in frag.find_all("br"):
        br.replace_with("\n")
    return frag.get_text(" ")


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
    """Return the full sentence inside full_text that contains fragment,
    with the sentence right before it merged in when the match on its own
    is too short to stand as a headline, or None if fragment can't be
    found. Used to recover real context for titles that are just a link
    fragment sitting mid-sentence or mid-teaser:
    - Reuters-style briefings run several headlines as one flowing
      paragraph, so an anchor's own text is often just the tail half of its
      sentence ("parliamentary election" inside a much longer sentence
      about Russia).
    - Il Post's short digest items often split one teaser across two short
      sentences ("Uno che correva fortissimo. E che oggi compie 50 anni."),
      with the link only on the second -- that half means nothing alone.

    Sentences are also split right before "(" so a glued-on UI badge like
    TLDR's "(12 minute read)" can't accidentally splice two unrelated
    sentences together and get treated as extra context, and on real line
    breaks (see raw_text_with_line_breaks) so a <br>-separated list of
    unrelated short titles sharing one container -- e.g. Il Post's "Ok
    Boomer!" links three separate pieces in one <td> with no punctuation
    between them at all -- doesn't get glued into one garbled title."""
    if not fragment:
        return None
    sentences = [s for s in re.split(r"(?<=[.!?])\s+|\s+(?=\()|\n+", full_text) if s.strip()]
    for i, s in enumerate(sentences):
        # Compare against whitespace-normalized text: get_text(" ") inserts
        # its separator between every text node regardless of the source
        # markup, so an inline tag split (e.g. "quello di <em>Barely
        # Breathing</em>") leaves a double space in the raw segment that a
        # plain substring check against the (single-spaced) fragment would
        # otherwise miss.
        if fragment.lower() in clean_text(s).lower():
            result = clean_text(s)
            if i > 0 and len(result) < 60:
                prev = clean_text(sentences[i - 1])
                if prev and len(prev) < 120:
                    result = f"{prev} {result}"
            return result
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


def leaf_table_cells(soup):
    """<td> elements that carry their own text directly rather than
    wrapping a further layout table -- the granularity at which a
    table-based email template (no <p> tags at all, e.g. Il Post's
    Gmail-forwarded newsletters) holds one logical block: a paragraph run,
    a promo box, a secondary syndicated article."""
    return [
        td
        for td in soup.find_all("td")
        if td.find("td") is None and len(clean_text(td.get_text(" "))) >= 40
    ]


PROMO_ASIDE_RE = re.compile(
    r"biglietti si comprano|i biglietti si comprano|si comprano qui|"
    r"prenota (il tuo posto|ora)|iscriviti (qui|ora)|acquista (i biglietti|il biglietto)",
    re.I,
)


def is_ticket_or_event_promo(tag) -> bool:
    """True if this block is a ticket/event-sale call to action (Il Post's
    podcast-live-show plugs and similar). There's no reliable *structural*
    marker for this -- it's rendered the same as the newsletter's own
    emphasized prose (both just sit in a plain <em>, checked and ruled out
    against a real example: Costa italicizes ordinary asides mid-essay
    too) -- so this is deliberately a narrow, content-based check instead,
    scoped to the specific CTA phrasing these plugs actually use rather
    than "any block that's all-italic"."""
    return bool(PROMO_ASIDE_RE.search(clean_text(tag.get_text(" "))))


def starts_new_syndicated_article(tag) -> bool:
    """True if this block opens with a <strong> title immediately followed
    by a short "di <Author Name>" byline -- Il Post's convention for a
    second, differently-authored article bundled into the same issue."""
    strong = tag.find("strong")
    if strong is None:
        return False
    text = clean_text(tag.get_text(" "))
    strong_text = clean_text(strong.get_text(" "))
    idx = text.find(strong_text)
    if idx == -1:
        return False
    after = text[idx + len(strong_text) : idx + len(strong_text) + 60]
    return bool(re.match(r"^\s*di\s+[A-ZÀ-Ý]", after))


def is_section_boundary(tag) -> bool:
    """A block that starts a ticket/event promo or a new syndicated
    article -- the newsletter's own piece ends right before the first one
    of these, not at some arbitrary character count. See
    is_ticket_or_event_promo / starts_new_syndicated_article for the two
    patterns this recognizes."""
    return is_ticket_or_event_promo(tag) or starts_new_syndicated_article(tag)


VIEW_ONLINE_RE = re.compile(
    r"view .*(online|in (your )?browser)|view (this )?online|view in browser|"
    r"web version|leggi (su|online)|visualizza (online|nel browser)",
    re.I,
)


def find_view_online_link(soup) -> str | None:
    """A genuine "read this issue online" permalink, if the newsletter
    actually includes one. Every other link in a briefing/letter template
    points at a cited article or a tracking redirect, not at the issue
    itself -- showing one of those as "Read the original" would send the
    reader somewhere unrelated to what they just read, so when no real
    permalink exists this returns None rather than guessing."""
    for a in soup.find_all("a", href=True):
        if VIEW_ONLINE_RE.search(clean_text(a.get_text(" "))):
            return a["href"]
    return None


def extract_full_letter(soup, plaintext: str, subject: str) -> dict:
    """Extract a FULL_LETTER_SENDERS message as one article: these senders
    (Francesco Costa's personal letter, Reuters' daily briefing) have no
    per-item headline at all -- it's one flowing digest touching several
    topics, and every "headline" the per-link scan could find is really
    just a mid-sentence fragment or, at best, a full but paragraph-long
    sentence that reads badly as a bold headline. The message's own
    subject line is the real, human-written headline for the whole issue
    (Reuters: "'Disaster' election in Germany"; Costa, once the Fwd: prefix
    and forwarded-header block are stripped: "Sta per succedere qualcosa?"),
    so it's used as the title unconditionally -- see FULL_LETTER_SENDERS
    for why this differs from the per-link digest scan."""

    def qualifies(block) -> bool:
        text = clean_text(block.get_text(" "))
        if not text or SIGNOFF_RE.search(text) or BORING_RE.search(text):
            return False
        # A short line with no link at all is more likely a masthead/byline
        # ("Daily Briefing", "By Claire Beers") than real body prose.
        return len(text) >= 20 or block.find("a") is not None

    body_paragraphs_html = [p for p in soup.find_all("p") if qualifies(p)]
    blocks = body_paragraphs_html
    if not (blocks and sum(len(clean_text(b.get_text(" "))) for b in blocks) > 150):
        # No usable <p> structure -- fall back to this template's <td>
        # leaves (Francesco Costa's Gmail-forwarded, table-only template).
        blocks = [td for td in leaf_table_cells(soup) if qualifies(td)]

    if blocks:
        # Stop at the newsletter's own natural end -- a promo aside or a
        # different, syndicated article bundled into the same issue --
        # instead of an arbitrary character limit that can cut the actual
        # piece off mid-paragraph.
        body_blocks = []
        for block in blocks:
            if is_section_boundary(block):
                break
            body_blocks.append(block)
        frag_html = "".join(str(b) for b in body_blocks)
    else:
        # No usable HTML structure at all -- fall back to the plaintext
        # body, which Gmail renders as clean blank-line-separated
        # paragraphs. This drops inline links (plaintext can't carry them)
        # but is still readable prose.
        text = (plaintext or "").replace("\r\n", "\n")
        text = FORWARD_HEADER_RE.sub("", text, count=1)
        # Gmail's plain-text view renders *bold*/_italic_ markup literally
        # (there's no HTML here to carry real emphasis) -- strip the
        # markers so they don't end up glued onto the body text.
        text = re.sub(r"[*_]{1,2}(\S[^*_]*?\S)[*_]{1,2}", r"\1", text)
        # Gmail's plain-text view renders each link as its own visible
        # anchor text followed by the raw target URL in angle brackets
        # (which can wrap onto the next line) -- the anchor text alone is
        # what should read as prose; the bracketed URL is just clutter
        # since this fallback path doesn't carry links through anyway.
        text = re.sub(r"\s*<https?://.*?>", "", text, flags=re.S)
        paragraphs = [clean_text(p) for p in re.split(r"\n\s*\n", text) if clean_text(p)]
        body_paragraphs = []
        for para in paragraphs:
            if SIGNOFF_RE.search(para) or BORING_RE.search(para):
                break
            body_paragraphs.append(para)
        frag_html = "".join(f"<p>{html.escape(p)}</p>" for p in body_paragraphs)

    title = subject
    fragment = BeautifulSoup(f"<div>{frag_html}</div>", "html.parser")
    summary = clean_text(fragment.get_text(" "))[:400]

    # Only a genuine "view this issue online" permalink is used as the
    # "Read the original" link -- every other link in these templates
    # points at a cited article or a tracking redirect, not at the issue
    # itself (see find_view_online_link). If there isn't one, this article
    # simply has no external link rather than a misleading one.
    link = find_view_online_link(soup)

    return {
        "title": title,
        "link": link or "",
        "summary": summary,
        "image": find_image(fragment),
        "content_html": sanitize_fragment(fragment, title),
    }


def extract_articles(html: str, plaintext: str, subject: str, source_name: str = ""):
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    for hidden in soup.find_all(style=re.compile(r"display:\s*none", re.I)):
        hidden.decompose()

    # Some senders (e.g. Francesco Costa's "Da Costa a Costa") reuse the same
    # generic subject line for every issue, including the one-off
    # subscription-confirmation email, so a subject-only welcome check can't
    # tell them apart -- also check the start of the actual message body. A
    # message with no plaintext part at all needs its lead text pulled from
    # the HTML's *rendered* text, not the first 1000 characters of raw
    # markup, which for a real template is nothing but <head>/style
    # boilerplate -- that would never contain the greeting this is looking
    # for no matter how long the confirmation email actually is.
    lead_source = plaintext or (clean_text(soup.get_text(" ")) if html else "")
    lead_text = clean_text(lead_source[:1000])
    if WELCOME_RE.search(subject) or WELCOME_RE.search(lead_text):
        return _fallback_article(soup, plaintext, subject)

    if source_name in FULL_LETTER_SENDERS:
        return [extract_full_letter(soup, plaintext, subject)]

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

        # But sometimes that shared container is instead stacking several
        # unrelated one-line items separated only by <br> (Il Post's digest
        # again, and its "Ok Boomer!" spin-off) -- in that case we still
        # want each item as its own article, built only from its own lines,
        # not from the whole shared <td> (which would bleed neighboring
        # items' text into this one's body). The <br> siblings are often
        # one level deeper than the div/td/p/li container itself (e.g.
        # wrapped in a <font>/<span>), so look for the nearest ancestor
        # that actually holds them as direct children.
        content_container = container
        br_holder = a.find_parent(
            lambda t: isinstance(t, Tag)
            and any(isinstance(c, Tag) and c.name == "br" for c in t.children)
        )
        groups = br_split_groups(br_holder if br_holder is not None else container)
        # A Gmail-forwarded copy of the same newsletter can nest its quoted
        # HTML differently than the original, so this walk-up occasionally
        # lands on something far too big (e.g. the whole "gmail_quote"
        # wrapper, whose own <br>s are just the forward header's line
        # breaks) instead of the many small items the anchor's own item
        # belongs to. A legitimate multi-item digest block splits into many
        # groups even when its combined text is long; a bad match doesn't
        # split much despite being long. Bail out to the plain container
        # rather than risk splicing unrelated items' content together.
        if (
            groups is not None
            and len(groups) < 3
            and len(clean_text((br_holder or container).get_text(" "))) > 1200
        ):
            groups = None
        if groups is not None:
            idx = group_index_containing(groups, a)
            if idx is not None:
                container_key = (id(container), idx)
                group_html = "".join(str(n) for n in groups[idx])
                content_container = BeautifulSoup(f"<div>{group_html}</div>", "html.parser")

        if container_key in seen_containers:
            continue

        full_text = clean_text(content_container.get_text(" "))
        # The anchor's own text is frequently just part of its real
        # headline -- a mid-sentence fragment ("parliamentary election" in
        # Reuters' flowing briefing prose), or half of a two-sentence
        # teaser (Il Post's "Uno che correva fortissimo. E che oggi compie
        # 50 anni."). Always try to recover the full sentence(s); a title
        # that's already a complete, self-contained sentence (e.g. TLDR's
        # own "Headline (12 minute read)") comes back unchanged because
        # sentence_containing won't find a bigger match worth using.
        better = sentence_containing(raw_text_with_line_breaks(content_container), title)
        if better and better.lower() != title.lower():
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
                "image": find_image(content_container),
                "content_html": sanitize_fragment(content_container, title),
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


def stable_id(message_id: str, href: str) -> str:
    """Keyed only by message + link, not by position in the extracted list --
    a link's index can shift between runs (e.g. an earlier item in the same
    email gets filtered differently), which would otherwise change the id
    and silently drop that article's read state in localStorage."""
    raw = f"{message_id}:{href}"
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
            # The outer "Fwd: ..." subject is Gmail's own, not the
            # newsletter's -- the real subject lives in the quoted header
            # block, same place the real sender name comes from above.
            subj_match = FORWARDED_SUBJECT_RE.search(plaintext or "")
            if subj_match:
                subject = subj_match.group(1).strip()
            # Likewise the message's own received date is when it was
            # forwarded, not when the newsletter was actually sent -- use
            # the original send date from the quoted header when we can
            # parse it, so the reader shows the newsletter's real date
            # instead of the day it happened to be forwarded.
            date_match = FORWARDED_DATE_RE.search(plaintext or "")
            if date_match:
                parsed_date = parse_forwarded_date(date_match.group(1))
                if parsed_date:
                    msg_date = parsed_date

        parsed = extract_articles(html or "", plaintext or "", subject, source_name)
        for item in parsed:
            # A missing link is legitimate here: extract_full_letter only
            # sets one when it found a genuine "view online" permalink, not
            # just any link in the message (see find_view_online_link).
            if not item["title"]:
                continue
            aid = stable_id(mid, item["link"])
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
