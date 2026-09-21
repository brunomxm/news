#!/usr/bin/env python3
"""
Targeted regression tests for fetch_newsletters.py's extraction/sanitization
logic, using small hand-written HTML fixtures that reproduce specific bugs
seen in the live feed. No network access, no Gmail credentials needed.

Run with:
    scripts/venv/bin/python scripts/test_fetch_newsletters.py
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bs4 import BeautifulSoup

from fetch_newsletters import (
    FORWARDED_SUBJECT_RE,
    br_split_groups,
    extract_articles,
    extract_full_letter,
    group_index_containing,
    sanitize_fragment,
    sentence_containing,
)


class SanitizeFragmentAttributes(unittest.TestCase):
    """Item 1: only href (on <a>) and src/alt (on <img>) may survive into
    the reader's HTML -- everything else (style, class, data-*, ...) must
    be stripped from every tag, not just a and img."""

    def test_strips_style_and_class_from_structural_tags(self):
        html = (
            '<div>'
            '<p style="color:red" class="foo">Hello <strong style="font-weight:900" class="bar">world</strong></p>'
            '<a href="http://example.com/a" style="color:blue" class="baz" data-track="1">link text long enough</a>'
            '<img src="http://example.com/i.png" style="width:1px" class="qux" alt="a photo">'
            '</div>'
        )
        soup = BeautifulSoup(html, "html.parser")
        out = sanitize_fragment(soup.div, title="")
        self.assertNotIn("style=", out)
        self.assertNotIn("class=", out)
        self.assertNotIn("data-track", out)
        self.assertIn('href="http://example.com/a"', out)
        self.assertIn('src="http://example.com/i.png"', out)
        self.assertIn('alt="a photo"', out)


class SentenceRecovery(unittest.TestCase):
    """Item 2: a link's own anchor text is often just a fragment of its
    real headline. sentence_containing must recover the full sentence
    without ever gluing unrelated stacked items together."""

    def test_recovers_full_sentence_for_mid_paragraph_fragment(self):
        # Reuters-style: several headlines flow as one prose paragraph.
        full_text = (
            "Russia's ruling United Russia party was on track to easily win a "
            "parliamentary election that President Vladimir Putin has cast as a test."
        )
        self.assertEqual(
            sentence_containing(full_text, "parliamentary election"),
            full_text,
        )

    def test_merges_short_lead_in_sentence(self):
        # Il Post's two-sentence teaser style: the link only covers the
        # second, contextless half.
        full_text = "Uno che correva fortissimo. E che oggi compie 50 anni ."
        result = sentence_containing(full_text, "E che oggi compie 50 anni")
        self.assertEqual(result, "Uno che correva fortissimo. E che oggi compie 50 anni .")

    def test_does_not_pull_in_glued_ui_badge(self):
        # TLDR-style: the anchor text itself already includes "(N minute
        # read)" glued on with no punctuation before the next sentence.
        full_text = (
            "Apple's Big Feature Announcement (12 minute read) Apple is set to "
            "introduce this next month."
        )
        title = "Apple's Big Feature Announcement (12 minute read)"
        result = sentence_containing(full_text, title)
        self.assertIsNone(result)

    def test_ignores_whitespace_differences_from_inline_tags(self):
        # get_text(" ") inserts its separator between EVERY text node, so a
        # fragment split by an inline tag (<em>) leaves extra internal
        # whitespace that must not break the containment check.
        full_text = "Some lead in text quello di  Barely Breathing ."
        result = sentence_containing(full_text, "quello di Barely Breathing")
        self.assertIn("Barely Breathing", result)


class BrSplitGroups(unittest.TestCase):
    """Item 2/3: a <td> stacking several unrelated one-line items separated
    only by <br> must split into one group per item, not be treated as a
    single flowing block (which was bleeding neighboring items' content
    into whichever one survived dedup)."""

    def test_splits_multiple_anchor_bearing_lines(self):
        html = (
            "<td>"
            '<a href="http://a">Title one</a><br>'
            '<a href="http://b">Title two</a><br>'
            '<a href="http://c">Title three</a>'
            "</td>"
        )
        td = BeautifulSoup(html, "html.parser").td
        groups = br_split_groups(td)
        self.assertIsNotNone(groups)
        self.assertEqual(len(groups), 3)

        anchors = td.find_all("a")
        idx0 = group_index_containing(groups, anchors[0])
        idx1 = group_index_containing(groups, anchors[1])
        idx2 = group_index_containing(groups, anchors[2])
        self.assertEqual({idx0, idx1, idx2}, {0, 1, 2})

    def test_single_item_prose_is_not_split(self):
        # One link, incidental <br> inside its own body -- must NOT be
        # treated as a stacked list (there's only one anchor-bearing line).
        html = (
            "<td>Some lead-in text.<br>"
            'More text with <a href="http://a">one link</a> in it.</td>'
        )
        td = BeautifulSoup(html, "html.parser").td
        self.assertIsNone(br_split_groups(td))


class DigestExtraction(unittest.TestCase):
    """End-to-end: extract_articles on small digest-style fixtures."""

    def test_stacked_br_items_become_separate_articles_with_own_body(self):
        html = (
            "<html><body>"
            "<td>"
            '<a href="http://x/a">Cosa mettersi per la fine del mondo lunga</a><br>'
            '<a href="http://x/b">L’amore che si fa in autunno</a><br>'
            '<a href="http://x/c">Il paese dove nessuno ha ragione mai</a>'
            "</td>"
            "</body></html>"
        )
        articles = extract_articles(html, "", "Some subject", "Il Post - Ok Boomer!")
        titles = [a["title"] for a in articles]
        self.assertEqual(len(articles), 3)
        self.assertIn("Cosa mettersi per la fine del mondo lunga", titles)
        self.assertIn("L’amore che si fa in autunno", titles)
        self.assertIn("Il paese dove nessuno ha ragione mai", titles)
        # No article's body should mention another item's title text.
        for art in articles:
            for other in titles:
                if other != art["title"] and art["content_html"]:
                    self.assertNotIn(other, art["content_html"])

    def test_boilerplate_nav_links_are_filtered(self):
        html = (
            "<html><body>"
            '<p><a href="http://x/real">A real headline worth reading today</a> some more text here.</p>'
            '<p><a href="http://x/book">Book a call</a></p>'
            '<p><a href="http://x/nav">Tutte le newsletter</a></p>'
            "</body></html>"
        )
        articles = extract_articles(html, "", "Subject", "Some Sender")
        titles = [a["title"] for a in articles]
        self.assertTrue(any("real headline" in t for t in titles))
        self.assertNotIn("Book a call", titles)
        self.assertNotIn("Tutte le newsletter", titles)


class FullLetterExtraction(unittest.TestCase):
    """Item 2/3, plus the follow-up bug where Reuters Daily Briefing's
    per-sentence "headlines" were really just paragraph-long sentences with
    no real headline behind them at all. FULL_LETTER_SENDERS treats the
    whole message as one article and uses the subject line (the one real,
    human-written headline for the issue) as the title unconditionally."""

    def test_recovers_true_subject_from_forwarded_header(self):
        # main() strips the outer "Fwd: ..." wrapper and swaps in the real
        # subject from the quoted header block before calling
        # extract_full_letter -- verify that recovery works.
        plaintext = (
            "---------- Forwarded message ---------\n"
            "From: Francesco Costa <costa@ilpost.it>\n"
            "Date: Sat, Sep 19, 2026 at 8:16 AM\n"
            "Subject: Sta per succedere qualcosa?\n"
            "To: <bruno@example.com>\n"
        )
        match = FORWARDED_SUBJECT_RE.search(plaintext)
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1).strip(), "Sta per succedere qualcosa?")

    def test_falls_back_to_plaintext_and_strips_header_and_markup(self):
        # Francesco Costa's table-only Gmail-forward template has no <p>
        # tags at all -- extraction must fall back to the plaintext body,
        # strip the forwarded-message header block, and not leave literal
        # *emphasis* markers in the body.
        plaintext = (
            "---------- Forwarded message ---------\n"
            "From: Francesco Costa <costa@ilpost.it>\n"
            "Date: Sat, Sep 19, 2026 at 8:16 AM\n"
            "Subject: Sta per succedere qualcosa?\n"
            "To: <bruno@example.com>\n"
            "\n"
            "\n"
            "*Alla fine di questa newsletter, un ringraziamento importante.*\n"
            "\n"
            "Si dice delle grandi crisi che arrivano lentamente e poi tutte in una volta.\n"
        )
        html = (
            "<html><body><table><tr><td>"
            'no paragraph tags here, just a <a href="http://x/view">view online</a> link'
            "</td></tr></table></body></html>"
        )
        soup = BeautifulSoup(html, "html.parser")
        # subject as already recovered by main() -- the "Fwd:" prefix is gone.
        result = extract_full_letter(soup, plaintext, "Sta per succedere qualcosa?")
        self.assertEqual(result["title"], "Sta per succedere qualcosa?")
        self.assertNotIn("*", result["content_html"] or "")
        self.assertNotIn("Forwarded message", result["content_html"] or "")
        self.assertIn("grandi crisi", result["content_html"] or "")

    def test_uses_html_paragraphs_and_preserves_links_when_available(self):
        # Reuters' real newsletter template has genuine <p> tags with real
        # inline citation links -- those should survive into the body
        # rather than being discarded in favor of the plaintext fallback.
        html = (
            "<html><body>"
            "<p>Daily Briefing</p>"
            "<p>By Claire Beers</p>"
            '<p>Hello. Germany\'s Merz fights for survival after a '
            '<a href="http://reuters.example/a">disaster state election</a>, '
            "and markets reacted sharply to the news this morning as investors weighed the outcome.</p>"
            "</body></html>"
        )
        soup = BeautifulSoup(html, "html.parser")
        result = extract_full_letter(soup, "", "'Disaster' election in Germany")
        self.assertEqual(result["title"], "'Disaster' election in Germany")
        self.assertIn('href="http://reuters.example/a"', result["content_html"] or "")
        self.assertNotIn("By Claire Beers", result["content_html"] or "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
