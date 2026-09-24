#!/usr/bin/env python3
"""
Targeted regression tests for fetch_newsletters.py's extraction/sanitization
logic, using small hand-written HTML fixtures that reproduce specific bugs
seen in the live feed. No network access, no Gmail credentials needed.

Run with:
    scripts/venv/bin/python scripts/test_fetch_newsletters.py
"""

import base64
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bs4 import BeautifulSoup

from fetch_newsletters import (
    BORING_RE,
    FORWARDED_DATE_RE,
    FORWARDED_SUBJECT_RE,
    FULL_LETTER_SENDERS,
    REUTERS_DAILY_BRIEFING_SENDER,
    _decode_reuters_click_target,
    _is_suspicious_reuters_title,
    _reuters_title_from_slug,
    body_is_just_the_title,
    br_split_groups,
    clean_text,
    dedupe_images,
    extract_articles,
    extract_full_letter,
    extract_reuters_daily_briefing,
    fetch_og_image,
    find_image,
    find_view_online_link,
    get_text_no_glue,
    group_index_containing,
    is_admissible_image_size,
    parse_forwarded_date,
    resolve_reading_time_min,
    stable_id,
    stable_issue_id,
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
        # The " ." is what get_text(" ") produces when the closing period sits
        # outside the link; clean_text pulls it back onto the word, so the
        # recovered sentence must come back without the floating period that
        # used to dangle on its own line under the headline.
        full_text = "Uno che correva fortissimo. E che oggi compie 50 anni ."
        result = sentence_containing(full_text, "E che oggi compie 50 anni")
        self.assertEqual(result, "Uno che correva fortissimo. E che oggi compie 50 anni.")

    def test_merges_a_short_answer_to_the_question_before_it(self):
        # Il Post's other two-sentence teaser style: a question, then a short
        # answer with no connector word at all ("C'entra" has no antecedent
        # of its own -- it means nothing without the question).
        full_text = "Come fa Remco Evenepoel a essere così forte a cronometro? C'entra anche la sua pelle."
        result = sentence_containing(full_text, "C'entra anche la sua pelle")
        self.assertEqual(result, full_text)

    def test_merges_a_bare_one_word_reaction_with_the_sentence_before_it(self):
        # Real fragment (Evening Post, 21 Sept 2026): "Preparatevi." on its
        # own has no connector and no question before it, but at one word
        # it's too terse to stand as its own headline -- it only means
        # anything next to the sentence it's reacting to.
        full_text = (
            "Ha vinto tutte e cinque le partite e domenica sera giocherà gli ottavi "
            "contro la Danimarca. Preparatevi."
        )
        result = sentence_containing(full_text, "Preparatevi")
        self.assertEqual(result, full_text)

    def test_merges_a_beh_comma_hedge_with_the_sentence_before_it(self):
        # Real fragment (Evening Post, 21 Sept 2026): "Beh," (well,) is a
        # discourse-hedge continuation just like "Ma"/"E", but followed by a
        # comma rather than by whitespace before the rest of the sentence.
        full_text = "Un canale broadcast. Beh, si capisce meglio qui."
        result = sentence_containing(full_text, "si capisce meglio qui")
        self.assertEqual(result, full_text)

    def test_a_short_self_contained_sentence_does_not_borrow_the_one_before_it(self):
        # Real fragment (Evening Post, 21 Sept 2026): two *separate* stories,
        # Meclemburgo's regional election and Berlin's, told back to back
        # with no connector and no question mark -- merging them under a
        # bare length threshold alone put both stories' text under the
        # Berlino link (published article id 1bc5211d6ae5ca9e).
        full_text = (
            "Quelle tedesche in Meclemburgo invece sono state eccezionali per almeno tre motivi. "
            "A Berlino ha vinto la sinistra, per la prima volta."
        )
        result = sentence_containing(full_text, "per la prima volta")
        self.assertEqual(result, "A Berlino ha vinto la sinistra, per la prima volta.")

    def test_a_long_sentence_after_a_question_is_not_swept_in_either(self):
        # Il Post's digest often ends a short aside with a question right
        # before moving on to a new, unrelated, much longer headline -- the
        # question-answer merge must stay capped to a short answer, or it
        # would glue that unrelated headline onto the question instead.
        full_text = (
            "Per chi c'era, invece, qui ci sono un sacco di belle foto: vi trovate? "
            "Giovedì a Novara Stefano Nazzi presenta il suo nuovo libro, Profeti del "
            "buio, alla rassegna Il Castello di Miramare, con moltissimi altri dettagli."
        )
        result = sentence_containing(full_text, "Giovedì a Novara")
        self.assertTrue(result.startswith("Giovedì a Novara"), result)
        self.assertNotIn("vi trovate", result)

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
        # Deliberately not "Il Post - Ok Boomer!": that sender is now handled as
        # a whole-issue letter (see FullLetterSenders below), so it would return
        # one article by design. Any other <br>-stacking digest still splits.
        articles = extract_articles(html, "", "Some subject", "Some Digest")
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


class ForwardedMessageDateRecovery(unittest.TestCase):
    """Item 1 (real bug: brunomxm.github.io/news article 4567d13942c5c92c
    showed 21 Sept, the forward date, instead of 19 Sept, the newsletter's
    real send date). main() extracts the Date: line from the forwarded
    header block and uses it instead of the envelope's own received date."""

    FORWARDED_HEADER = (
        "---------- Forwarded message ---------\n"
        "From: Francesco Costa <costa@ilpost.it>\n"
        "Date: Sat, Sep 19, 2026 at 8:16 AM\n"
        "Subject: Sta per succedere qualcosa?\n"
        "To: <bruno@example.com>\n"
    )

    def test_extracts_and_parses_the_original_send_date(self):
        match = FORWARDED_DATE_RE.search(self.FORWARDED_HEADER)
        self.assertIsNotNone(match)
        parsed = parse_forwarded_date(match.group(1))
        self.assertIsNotNone(parsed)
        self.assertEqual((parsed.year, parsed.month, parsed.day), (2026, 9, 19))
        self.assertEqual((parsed.hour, parsed.minute), (8, 16))

    def test_uses_outer_message_timezone_when_forward_header_has_none(self):
        from datetime import timedelta, timezone

        local_tz = timezone(timedelta(hours=2))
        parsed = parse_forwarded_date("Sat, Sep 19, 2026 at 8:16 AM", local_tz)
        self.assertEqual(parsed.isoformat(), "2026-09-19T08:16:00+02:00")

    def test_unparseable_date_format_returns_none_not_a_crash(self):
        # A differently-worded mail client, or a non-English Gmail locale --
        # must fail closed to "use the received date" rather than throw.
        self.assertIsNone(parse_forwarded_date("not a real date at all"))


class GenuinePermalinkOnly(unittest.TestCase):
    """Item 2 (real bug: the Costa article's "Read the original" pointed at
    a Hemingway citation link, and Reuters' pointed at the first article
    cited in the briefing). Every link in these templates is either a
    tracking redirect or a citation to something else entirely -- only an
    explicit "view online" style anchor should ever become the article's
    external link."""

    def test_no_link_when_only_citation_and_tracking_links_exist(self):
        # Modeled on the real Costa email: every href is the same
        # x.ilpost.it/re tracking-redirect domain, none of them the
        # newsletter's own permalink.
        html = (
            "<html><body><td>"
            'Ricordavo che fosse una frase di Hemingway ma ero convinto che si trattasse di uno '
            'di quegli aforismi apocrifi, poi ho controllato '
            '<a href="https://x.ilpost.it/re?l=abc">ed è effettivamente di Hemingway</a>, anche se '
            "lui la scrisse un po' diversa."
            "</td></body></html>"
        )
        soup = BeautifulSoup(html, "html.parser")
        self.assertIsNone(find_view_online_link(soup))

    def test_no_link_when_only_read_more_citation_links_exist(self):
        # Modeled on the real Reuters email: "Read more" links go to the
        # individual articles cited in the briefing, not to the briefing
        # itself -- there is no digest-level permalink at all.
        html = (
            "<html><body>"
            '<p>Germany\'s Merz fights for survival. <a href="https://reuters.example/merz">Read more</a>.</p>'
            '<p>Oil prices slid. <a href="https://reuters.example/oil">Read more</a>.</p>'
            "</body></html>"
        )
        soup = BeautifulSoup(html, "html.parser")
        self.assertIsNone(find_view_online_link(soup))

    def test_finds_a_genuine_view_online_link_when_one_exists(self):
        html = (
            "<html><body>"
            '<a href="https://example.com/issue/42">View this email in your browser</a>'
            '<p>Some article text with a <a href="https://example.com/cited">citation</a>.</p>'
            "</body></html>"
        )
        soup = BeautifulSoup(html, "html.parser")
        self.assertEqual(find_view_online_link(soup), "https://example.com/issue/42")

    def test_extract_articles_omits_link_entirely_rather_than_a_wrong_one(self):
        html = (
            "<html><body><td>"
            'Testo con un link di citazione <a href="https://x.ilpost.it/re?l=abc">qualcosa</a> qui.'
            "</td></body></html>"
        )
        articles = extract_articles(html, "", "Sta per succedere qualcosa?", "Francesco Costa")
        self.assertEqual(len(articles), 1)
        self.assertEqual(articles[0]["link"], "")

    def test_citation_remains_id_seed_without_becoming_original_link(self):
        html = (
            "<html><body><td>Una newsletter con abbastanza testo per essere letta: "
            '<a href="https://x.ilpost.it/re?l=abc">citazione di Hemingway</a> '
            "e una conclusione utile per il lettore.</td></body></html>"
        )
        article = extract_articles(html, "", "Sta per succedere qualcosa?", "Francesco Costa")[0]
        self.assertEqual(article["link"], "")
        self.assertEqual(article["id_link"], "https://x.ilpost.it/re?l=abc")

    def test_reuters_photo_captions_are_not_reader_paragraphs(self):
        html = (
            "<html><body>"
            "<p>Germany's Merz fights for survival after the state election, "
            "as the government faces difficult coalition talks and a growing opposition.</p>"
            "<p>AfD campaign event ahead of the election. REUTERS/Lisi Niesner</p>"
            "<p>In the four months since the leaders met, trade shifted and "
            "expectations for their next summit have changed sharply.</p>"
            "<p>This email includes limited tracking for Reuters to understand engagement.</p>"
            "<p>Thomson Reuters. All rights reserved.</p>"
            "</body></html>"
        )
        article = extract_articles(html, "", "'Disaster' election in Germany", "Reuters")[0]
        self.assertNotIn("REUTERS/Lisi Niesner", article["content_html"])
        self.assertNotIn("limited tracking", article["content_html"])
        self.assertIn("trade shifted", article["content_html"])


class FullLetterBodyBoundary(unittest.TestCase):
    """Item 3, corrected: the Costa article's body was first truncated
    mid-essay at "El Niño" by a fixed 6000-character cap; the fix for that
    then over-corrected by also stopping at a ticket/event promo aside and
    at any block that looked like a new, differently-authored piece, on
    the theory that either one meant "the newsletter is over". Checked
    against the real "Sta per succedere qualcosa?" issue (which bundles:
    Costa's own opening essay, a podcast promo, a guest-authored
    cotton-industry piece, a quick-links roundup, and Costa's own
    sign-off, "A presto, Francesco", all in that order), that assumption
    was wrong -- the guest piece and the promo are both part of that same
    issue, and the real newsletter runs to about 24,000 characters, not
    the ~8,000 the syndicated-article cutoff left. Only the trailing email
    chrome (unsubscribe/copyright/"all newsletters" nav) reliably marks
    where a newsletter issue actually ends."""

    # Modeled directly on the real "Sta per succedere qualcosa?" email's
    # section order: one <td> per paragraph-with-a-link block (Il Post's
    # template splits the essay this way), a ticket-promo aside, a guest
    # piece by a different author, Costa's own sign-off, then the footer.
    HTML = (
        "<html><body>"
        "<td>Primo paragrafo del pezzo di Costa, con la sua argomentazione principale "
        'e un link di supporto <a href="https://x.ilpost.it/re?l=1">come questo</a>.</td>'
        "<td>Secondo paragrafo che conclude davvero il pezzo, fino all'ultima frase "
        "significativa che il lettore deve poter leggere per intero.</td>"
        "<td><em>Con la fine dell'estate ritorna il podcast del Post. "
        'I biglietti si comprano <a href="https://x.ilpost.it/re?l=2">qui</a>.</em></td>'
        '<td><strong>"Un titolo qualsiasi"</strong><br>di Un Altro Autore<br><br>'
        "Testo di un articolo di un altro autore, ma bundlato nella stessa "
        "newsletter come segmento ospite di questo numero.</td>"
        # Costa's real sign-off sits at the tail of the same <td> as his
        # closing paragraph, not in its own tiny cell -- matching that
        # avoids the short-line/no-link masthead filter firing on it.
        "<td>Grazie ancora per aver letto fino a qui, un applauso a voi. "
        "A presto,<br>Francesco</td>"
        '<td><a href="https://x.ilpost.it/re?l=3">Tutte le newsletter</a> - ilpost.it. '
        "Ricevi questa newsletter perché lo hai chiesto, puoi disiscriverti qui. "
        "Copyright © 2026 Il Post, All rights reserved.</td>"
        "</body></html>"
    )

    def test_body_includes_the_whole_issue_and_stops_only_at_the_footer(self):
        soup = BeautifulSoup(self.HTML, "html.parser")
        result = extract_full_letter(soup, "", "Sta per succedere qualcosa?")
        body = result["content_html"] or ""
        self.assertIn("argomentazione principale", body)
        self.assertIn("ultima frase significativa", body, "must not cut the essay off mid-way")
        self.assertIn("podcast del Post", body, "the promo is part of this same issue")
        self.assertIn("Un Altro Autore", body, "the guest segment is part of this same issue")
        self.assertIn("A presto", body, "Costa's own sign-off is part of this same issue")
        self.assertNotIn("Tutte le newsletter", body, "must stop at the generic footer nav")
        self.assertNotIn("Copyright ©", body, "must stop at the copyright boilerplate")

    def test_no_fixed_character_cap_truncates_a_long_but_legitimate_essay(self):
        # A single-block essay long enough that the old 6000-character cap
        # would have cut it off, with no footer at all -- the whole thing
        # must survive.
        long_text = "Frase numero {}. ".format
        long_essay = "".join(long_text(i) + "Contenuto legittimo del pezzo. " for i in range(400))
        html = f"<html><body><td>{long_essay}</td></body></html>"
        soup = BeautifulSoup(html, "html.parser")
        result = extract_full_letter(soup, "", "Un pezzo molto lungo")
        body = result["content_html"] or ""
        self.assertIn("Frase numero 399", body, "a long legitimate essay must not be truncated")


class SubscriptionConfirmations(unittest.TestCase):
    """Confirmation emails have no articles in them -- they used to land in the
    feed as a card with an empty reader."""

    def test_confirmation_recognised_from_the_body(self):
        html = (
            "<html><body><td>Ciao! Questo messaggio è solo per confermare la tua "
            "iscrizione a Da Costa a Costa, la newsletter del Post."
            '<a href="http://x/a">il sito del Post</a></td></body></html>'
        )
        self.assertEqual(
            extract_articles(html, "", "Ciao, da Francesco Costa", "Francesco Costa"), []
        )

    def test_confirmation_recognised_from_a_hidden_preheader(self):
        # The New Yorker's confirmations say nothing but "Welcome to ..." in the
        # visible body; the give-away sits in the display:none preheader, which
        # is stripped before the per-anchor scan -- so the check has to read the
        # lead text first.
        html = (
            "<html><body>"
            '<div style="display:none">Important information about your new '
            "newsletter subscription</div>"
            '<td>Welcome to The New Yorker. <a href="http://x/a">Start reading here</a>'
            " and explore our archive of long reads.</td>"
            "</body></html>"
        )
        self.assertEqual(extract_articles(html, "", "Welcome", "The New Yorker"), [])

    def test_an_essay_that_merely_opens_with_a_welcome_is_kept(self):
        # "welcome to" / "benvenuti" on their own are ordinary ways to open a
        # piece, and for senders whose subject line IS the headline, matching
        # them would silently delete a real issue.
        html = (
            "<html><body><td>Benvenuti nell'era dei data center: questa settimana "
            "proviamo a capire quanta energia consumano davvero e chi la paga. "
            '<a href="http://x/a">Un rapporto recente</a> prova a quantificarlo.'
            "</td></body></html>"
        )
        articles = extract_articles(html, "", "Benvenuti nell'era dei data center", "Qualcuno")
        self.assertEqual(len(articles), 1)


class SurveyRequestAndFooterFiltering(unittest.TestCase):
    """Two non-article shapes narrowly reproduced from the real mailbox: a
    reader-engagement survey request (Il Post's "Come hai scoperto il Post?",
    2026-09-22), and a self-referential "why you're getting this" footer line
    sitting inside an otherwise-real digest (The Conversation's "Trump and Xi:
    the touchy subjects on summit agenda", same date)."""

    def test_survey_request_email_yields_no_articles(self):
        # Real fragment: the message is nothing but a thank-you note and one
        # CTA button ("Vai al sondaggio") -- the per-anchor scan used to turn
        # that button into a bogus one-item "article".
        html = (
            "<html><body><td>Ciao, intanto volevamo ringraziarti ancora una volta "
            "per la tua registrazione al Post. Se ti va, puoi rispondere a questo "
            "breve questionario, che ci aiuta a conoscere meglio chi ci legge. "
            "Ci vorranno pochi minuti: "
            '<a class="dm_ctaA" href="http://x/sondaggio" target="_blank">'
            "<span>Vai al sondaggio</span></a> Grazie mille!</td></body></html>"
        )
        self.assertEqual(extract_articles(html, "", "Come hai scoperto il Post?", "Il Post"), [])

    def test_survey_cta_alone_is_filtered_even_outside_a_full_survey_email(self):
        # Defense in depth: even if "Vai al sondaggio" ever turns up as one
        # link among several genuine ones, the button text alone must never
        # become an article title.
        html = (
            '<html><body><p><a href="http://x/real">A real headline worth '
            "reading today</a> some more text describing it in full.</p>"
            '<p><a href="http://x/sondaggio">Vai al sondaggio</a></p>'
            "</body></html>"
        )
        titles = [a["title"] for a in extract_articles(html, "", "Some subject", "Il Post")]
        self.assertNotIn("Vai al sondaggio", titles)

    def test_newsletter_disclosure_footer_is_not_an_article(self):
        # Real fragment: the sender's own name ("The Conversation") is a
        # short, otherwise-unremarkable anchor text sitting inside this exact
        # disclosure sentence -- checking only the anchor's own text (before
        # sentence recovery runs) let the recovered full sentence through as
        # a bogus final "article" in an otherwise-real digest.
        html = (
            "<html><body>"
            '<p><a href="http://x/1">A real headline worth reading today</a> '
            "some more text describing it in full.</p>"
            '<p style="color: #202020; font-size: 13px;">'
            'You’re receiving this newsletter from <a href="http://x/2">The '
            "Conversation</a><br>Tenancy B, 1 Bartholomew Close, London.</p>"
            "</body></html>"
        )
        titles = [a["title"] for a in extract_articles(html, "", "Some subject", "The Conversation")]
        self.assertEqual(len(titles), 1)
        self.assertNotIn("You’re receiving this newsletter from The Conversation", titles)

    def test_english_and_italian_disclosure_variants_are_both_caught(self):
        for sentence in (
            "You’re receiving this e-mail because you signed up for the newsletter.",
            "Ricevi questa newsletter perché ti sei registrato sul nostro sito.",
        ):
            with self.subTest(sentence=sentence):
                self.assertTrue(BORING_RE.search(sentence), sentence)


class IssueIdentity(unittest.TestCase):
    """issue_id groups the articles from one Gmail message -- it must be
    derived only from the message id, never from subject or timestamp,
    since subjects repeat across genuinely different issues."""

    def test_same_message_id_always_yields_the_same_issue_id(self):
        self.assertEqual(stable_issue_id("18a2b3c4d5e6f7"), stable_issue_id("18a2b3c4d5e6f7"))

    def test_different_messages_get_different_issue_ids_even_with_the_same_subject(self):
        # TLDR reuses "TLDR AI" as a subject fragment issue after issue; two
        # different messages must never collapse into one issue just because
        # they'd have shared a subject+date-derived key.
        self.assertNotEqual(stable_issue_id("message-1"), stable_issue_id("message-2"))

    def test_issue_id_space_is_disjoint_from_article_id_space(self):
        # Both are 16-hex-char sha1 prefixes; the "issue:" prefix on the
        # hashed input keeps the two id spaces from ever coinciding.
        mid = "18a2b3c4d5e6f7"
        self.assertNotEqual(stable_issue_id(mid), stable_id(mid, "https://example.com/a"))


class TLDRPromoFiltering(unittest.TestCase):
    """TLDR labels every editorial item with its own reading-time/kind marker;
    sponsored slots and job ads are the only blocks without one."""

    def _titles(self, html):
        return [a["title"] for a in extract_articles(html, "", "TLDR", "TLDR Founders")]

    def test_sponsor_and_job_blocks_are_dropped(self):
        html = (
            "<html><body>"
            '<div class="container"><a href="http://x/1">New ICONIQ Benchmarks are '
            "insane (5 minute read)</a> A board member sent this over.</div>"
            '<div class="container"><a href="http://x/2">Financial infrastructure to '
            "grow your revenue (Sponsor)</a> Payments start as a weekend project.</div>"
            '<div class="container"><a href="http://x/3">GTM Engineer, Applied AI at '
            "TLDR ($175-205k base, Fully Remote)</a></div>"
            '<div class="container"><a href="http://x/4">AX (GitHub Repo)</a> '
            "A high-throughput declarative orchestrator.</div>"
            "</body></html>"
        )
        titles = self._titles(html)
        self.assertEqual(len(titles), 2)
        self.assertTrue(any("ICONIQ" in t for t in titles))
        self.assertTrue(any("GitHub Repo" in t for t in titles))
        self.assertFalse(any("Sponsor" in t for t in titles))
        self.assertFalse(any("GTM Engineer" in t for t in titles))


class FullLetterSenders(unittest.TestCase):
    def test_ok_boomer_is_one_whole_issue(self):
        # Ok Boomer! is one essay per issue -- the per-link scan turned its
        # "previous editions" nav list into content-less articles.
        self.assertIn("Il Post - Ok Boomer!", FULL_LETTER_SENDERS)
        html = (
            "<html><body><td>Un lungo pezzo sul vestirsi per la fine del mondo, "
            "che continua per parecchi paragrafi e non è un elenco di link.</td>"
            '<td><a href="http://x/a">Cosa mettersi per la fine del mondo</a><br>'
            '<a href="http://x/b">L’amore che si fa</a></td></body></html>'
        )
        articles = extract_articles(html, "", "Ok Boomer! di oggi", "Il Post - Ok Boomer!")
        self.assertEqual(len(articles), 1)
        self.assertEqual(articles[0]["title"], "Ok Boomer! di oggi")


class TitleOnlyBodies(unittest.TestCase):
    """Il Post's digest items are one self-contained sentence, so the item's own
    block IS the headline -- the reader used to print it twice."""

    def test_body_identical_to_title_is_dropped(self):
        self.assertTrue(
            body_is_just_the_title(
                "<p>Uno che correva fortissimo. E che oggi compie 50 anni.</p>",
                "Uno che correva fortissimo. E che oggi compie 50 anni.",
            )
        )

    def test_body_that_merely_starts_with_its_title_is_kept(self):
        self.assertFalse(
            body_is_just_the_title(
                "<p>Uno che correva fortissimo.</p><p>Aveva vinto due volte a Milano "
                "e poi si era ritirato in silenzio, a trent'anni appena compiuti.</p>",
                "Uno che correva fortissimo.",
            )
        )


class IlPostEveningDigest(unittest.TestCase):
    """The evening digest opens with the editors' own chatty note (with links of
    its own) and closes with a teaser for tomorrow; only what sits between "Le
    notizie." and those closing cells is news."""

    DIGEST = (
        "<html><body><table><tr><td class=\"dmText\">"
        "Come forse sapete, da oggi siamo a Faenza per Talk in città. Come "
        'probabilmente non sapete, <a href="http://x/rain">era praticamente certo '
        "che avrebbe piovuto</a>. L'abbiamo presa con spirito."
        "</td></tr><tr><td class=\"dmText\">"
        "<em>Le notizie.</em> Le elezioni parlamentari in Russia sono andate "
        '<a href="http://x/ru">nell’unico modo possibile</a>. Quelle tedesche '
        'invece sono state <a href="http://x/de">eccezionali per almeno tre motivi</a>.'
        "</td></tr><tr><td>"
        '<a href="http://x/easter">Indizio: gli easter egg sono 25</a>'
        "</td></tr></table></body></html>"
    )

    def setUp(self):
        self.articles = extract_articles(self.DIGEST, "", "Evening Post", "Il Post")
        self.titles = [a["title"] for a in self.articles]

    def test_editors_intro_is_not_an_article(self):
        self.assertFalse(
            any("Faenza" in t or "piovuto" in t for t in self.titles),
            f"the intro leaked into the feed: {self.titles}",
        )

    def test_closing_teaser_is_not_an_article(self):
        self.assertFalse(any("easter egg" in t for t in self.titles))

    def test_the_news_items_themselves_survive(self):
        self.assertEqual(len(self.articles), 2, self.titles)
        self.assertTrue(any("Russia" in t for t in self.titles))
        self.assertTrue(any("tedesche" in t for t in self.titles))

    def test_an_item_never_carries_another_items_text_as_its_body(self):
        for art in self.articles:
            body = art["content_html"] or ""
            for other in self.titles:
                if other != art["title"]:
                    self.assertNotIn(other, body)

    def test_other_ilpost_newsletters_are_untouched(self):
        # No "Le notizie." label -> no intro skipping, or Colonne/Ok Boomer and
        # the onboarding emails would lose their first block.
        html = self.DIGEST.replace("<em>Le notizie.</em> ", "")
        titles = [a["title"] for a in extract_articles(html, "", "Colonne", "Il Post - Colonne")]
        self.assertTrue(any("piovuto" in t or "Faenza" in t for t in titles))


class IlPostRealEmailFragments(unittest.TestCase):
    """Regression tests built from verbatim HTML pulled from the actual
    label:news mailbox (Il Post's "Evening Post", 21 Sept 2026 issue), not
    hand-simplified fixtures -- so a fix that only works on a simplified
    shape doesn't mask the real one. Reproduces the two issues found in
    published article id 1bc5211d6ae5ca9e and in the audit of the same
    message's "Roggero"/"femminicidi" teasers."""

    def test_two_short_teasers_sharing_a_sentence_boundary_stay_separate(self):
        # "Quelle tedesche in Meclemburgo..." and "A Berlino ha vinto la
        # sinistra..." are two separate stories, each with its own link, with
        # no <br> between them -- only a sentence boundary. The live site's
        # article 1bc5211d6ae5ca9e combined both sentences under the
        # "per la prima volta" (Berlino) link, because that sentence alone
        # was short enough to trip the old "merge with previous sentence"
        # heuristic even though it's a complete sentence on its own.
        html = (
            '<html><body><table><tr><td class="dmText">'
            '<font color="#000000" face="georgia, serif">'
            '<em><span style="font-size:18px">Le notizie.</span></em><br/>'
            "<span style=\"font-size:18px\">Le elezioni parlamentari in Russia sono andate "
            '<a href="http://x/ru">nell’unico modo possibile</a>.<br/>Quelle tedesche </span>'
            '<span style="font-size:18px">in Meclemburgo invece </span>'
            '<span style="font-size:18px">sono state eccezionali per '
            '<a href="http://x/de">almeno tre motivi</a>. </span>'
            '<span style="font-size:18px">A Berlino ha vinto la sinistra, '
            '<a href="http://x/berlin">per la prima volta</a>.</span>'
            "</font></td></tr></table></body></html>"
        )
        articles = extract_articles(html, "", "Evening Post", "Il Post")
        by_link = {a["link"]: a["title"] for a in articles}
        self.assertIn("http://x/de", by_link)
        self.assertIn("http://x/berlin", by_link)
        self.assertTrue(
            by_link["http://x/berlin"].startswith("A Berlino"),
            by_link["http://x/berlin"],
        )
        self.assertNotIn(
            "Meclemburgo",
            by_link["http://x/berlin"],
            f"Berlino's own title must not carry the Meclemburgo story: {by_link['http://x/berlin']!r}",
        )
        self.assertTrue(
            by_link["http://x/de"].startswith("Quelle tedesche"),
            by_link["http://x/de"],
        )
        self.assertNotIn("Berlino", by_link["http://x/de"])

    def test_words_split_across_adjacent_anchors_are_not_pulled_apart(self):
        # Il Post's per-link tracking wraps each link separately even when
        # the link boundary sits mid-word -- "un caso" is genuinely
        # <a>u</a><span><a>n caso simile...</a></span> in the source, with no
        # whitespace between the two anchors at all. get_text(" ")'s habit of
        # inserting a separator between every text node turned this into
        # "u n caso simile...", audited live as "C'è u n caso simile a quello
        # di Mario Roggero".
        html = (
            '<html><body><table><tr><td class="dmText">'
            "<span style=\"font-size:18px\">C’è "
            '<a href="http://x/u">u</a></span>'
            '<span style="font-size:18px">'
            '<a href="http://x/ncaso">n caso simile a quello di Mario Roggero</a>, '
            "e sempre in provincia di Cuneo.</span>"
            "</td></tr></table></body></html>"
        )
        articles = extract_articles(html, "", "Qualcosa", "Qualcuno")
        titles = " | ".join(a["title"] for a in articles)
        self.assertIn("un caso simile", titles, titles)
        self.assertNotIn("u n caso", titles, titles)

    def test_nove_femminicidi_word_split_is_also_preserved(self):
        # Same bug, same newsletter, a different word: "nove" (nine) split as
        # <a>n</a><span><a>ove femminicidi molto simili</a></span>, audited
        # live as "In Sudafrica ci sono stati n ove femminicidi molto simili".
        html = (
            '<html><body><table><tr><td class="dmText">'
            '<span style="font-size:18px">In Sudafrica ci sono stati '
            '<a href="http://x/n">n</a></span>'
            '<span style="font-size:18px">'
            '<a href="http://x/ove">ove femminicidi molto simili</a> in due mesi.</span>'
            "</td></tr></table></body></html>"
        )
        articles = extract_articles(html, "", "Qualcosa", "Qualcuno")
        titles = " | ".join(a["title"] for a in articles)
        self.assertIn("nove femminicidi", titles, titles)
        self.assertNotIn("n ove femminicidi", titles, titles)


class GetTextNoGlue(unittest.TestCase):
    """Direct unit coverage for the helper behind the word-split fix above."""

    def test_no_separator_between_flush_adjacent_inline_tags(self):
        html = '<td><span>C’è <a href="x">u</a></span><span><a href="y">n caso simile</a>, e sempre.</span></td>'
        soup = BeautifulSoup(html, "html.parser")
        self.assertIn("un caso simile", get_text_no_glue(soup.td))

    def test_real_whitespace_in_the_source_is_kept(self):
        html = '<p>ha vinto <a href="x">per la prima volta</a>.</p>'
        soup = BeautifulSoup(html, "html.parser")
        self.assertEqual(get_text_no_glue(soup.p), "ha vinto per la prima volta.")

    def test_br_still_becomes_a_real_newline(self):
        html = "<td>Riga uno<br>Riga due</td>"
        soup = BeautifulSoup(html, "html.parser")
        self.assertEqual(get_text_no_glue(soup.td), "Riga uno\nRiga due")

    def test_block_level_boundaries_still_get_a_separator(self):
        # Two <p>s glued with no whitespace in the markup must not run
        # together into one word salad the way flush inline tags should.
        html = "<td><p>First paragraph.</p><p>Second paragraph.</p></td>"
        soup = BeautifulSoup(html, "html.parser")
        self.assertEqual(
            clean_text(get_text_no_glue(soup.td)), "First paragraph. Second paragraph."
        )


class TLDRReadingTimeConsistency(unittest.TestCase):
    """The byline's own word-count guess over our short extracted teaser must
    never contradict the reading time TLDR itself already put in the title."""

    def test_uses_the_titles_own_minutes_when_present(self):
        # Real title from the labelled mailbox (TLDR Data). Our own teaser
        # extract is a couple of sentences -- nowhere near 11 minutes by
        # word count -- so showing our own guess instead would contradict
        # the newsletter's own stated time for the linked article.
        title = "How we knew COVID was over (and what our models had to unlearn) (11 minute read)"
        self.assertEqual(resolve_reading_time_min("TLDR Data", title, wc=40), 11)
        self.assertEqual(resolve_reading_time_min("TLDR Data", title, wc=300), 11)

    def test_non_minute_marker_suppresses_the_word_count_guess(self):
        # "(GitHub Repo)" has no comparable "minutes" figure at all.
        title = "AX (GitHub Repo)"
        self.assertIsNone(resolve_reading_time_min("TLDR AI", title, wc=90))

    def test_non_tldr_sources_are_unaffected(self):
        # Only TLDR bakes its own reading time into the title -- everyone
        # else keeps the ordinary word-count-based estimate.
        self.assertEqual(resolve_reading_time_min("Il Post", "Un pezzo qualsiasi lungo", wc=600), 3)
        self.assertIsNone(resolve_reading_time_min("Il Post", "Un pezzo breve", wc=30))


class ImageSizeAdmission(unittest.TestCase):
    """We'd rather show no image than a bad one: is_admissible_image_size
    rejects what only declared width/height give away (find_image's regex
    checks cover filenames/alt text separately)."""

    def test_tracking_pixel_rejected(self):
        self.assertFalse(is_admissible_image_size(1, 1))

    def test_logo_scale_rejected(self):
        self.assertFalse(is_admissible_image_size(140, 40))

    def test_thin_banner_rejected_unless_large(self):
        # A 700x60 divider is divider-shaped AND too small a long side to
        # plausibly be a real editorial photo.
        self.assertFalse(is_admissible_image_size(700, 60))

    def test_large_panorama_allowed(self):
        # A genuinely large image can be legitimately wide/tall -- only
        # reject the extreme-aspect-ratio shape when it's ALSO small.
        self.assertTrue(is_admissible_image_size(1600, 300))

    def test_ordinary_photo_allowed(self):
        self.assertTrue(is_admissible_image_size(640, 400))

    def test_unknown_dimensions_allowed(self):
        self.assertTrue(is_admissible_image_size(None, None))


class FindImageAdmission(unittest.TestCase):
    def test_small_declared_size_rejected(self):
        html = '<div><img src="http://example.com/logo.png" width="120" height="40"></div>'
        soup = BeautifulSoup(html, "html.parser")
        self.assertIsNone(find_image(soup.div))

    def test_masthead_alt_text_rejected_even_without_telltale_filename(self):
        html = '<div><img src="http://example.com/i/abc123.png" alt="Company masthead"></div>'
        soup = BeautifulSoup(html, "html.parser")
        self.assertIsNone(find_image(soup.div))

    def test_ordinary_photo_accepted(self):
        html = '<div><img src="http://example.com/photo.jpg" width="640" height="400" alt="A scene"></div>'
        soup = BeautifulSoup(html, "html.parser")
        self.assertEqual(find_image(soup.div), "http://example.com/photo.jpg")


class ImageDeduplication(unittest.TestCase):
    """The same image URL reused across several stories (a shared og:image,
    a logo that slipped past admission) is kept only on the first story."""

    def test_duplicate_image_dropped_from_later_stories(self):
        articles = [
            {"image": "http://example.com/shared.jpg"},
            {"image": "http://example.com/shared.jpg"},
            {"image": "http://example.com/unique.jpg"},
        ]
        dedupe_images(articles)
        self.assertEqual(articles[0]["image"], "http://example.com/shared.jpg")
        self.assertIsNone(articles[1]["image"])
        self.assertEqual(articles[2]["image"], "http://example.com/unique.jpg")

    def test_no_images_at_all_is_a_no_op(self):
        articles = [{"image": None}, {"image": None}]
        dedupe_images(articles)
        self.assertEqual([a["image"] for a in articles], [None, None])


class OgImageFetch(unittest.TestCase):
    """fetch_og_image never raises out of ingestion -- every failure path
    (bad scheme, request exception, non-HTML response) returns None."""

    def test_non_http_scheme_never_fetched(self):
        cache = {}
        with patch("fetch_newsletters.requests.get") as mock_get:
            result = fetch_og_image("mailto:someone@example.com", cache)
        mock_get.assert_not_called()
        self.assertIsNone(result)

    def test_request_exception_returns_none_and_is_cached(self):
        import requests

        cache = {}
        with patch("fetch_newsletters.requests.get", side_effect=requests.ConnectionError()):
            result = fetch_og_image("http://example.com/article", cache)
        self.assertIsNone(result)
        self.assertIn("http://example.com/article", cache)

    def test_second_call_for_same_url_does_not_refetch(self):
        cache = {"http://example.com/article": "http://example.com/og.jpg"}
        with patch("fetch_newsletters.requests.get") as mock_get:
            result = fetch_og_image("http://example.com/article", cache)
        mock_get.assert_not_called()
        self.assertEqual(result, "http://example.com/og.jpg")


def _reuters_click_link(target_url: str) -> str:
    """Build a real-shaped newslink.reuters.com click-tracking redirect
    around `target_url`, the same shape _decode_reuters_click_target
    parses in production -- so fixtures exercise the actual decode path
    instead of silently no-op'ing on a plain, undecodable test URL."""
    b64 = base64.urlsafe_b64encode(target_url.encode()).decode().rstrip("=")
    return f"https://newslink.reuters.com/click/12345.6789/{b64}/deadbeef"


# Real reuters.com article URLs (fabricated but realistically shaped, each
# with a >=3-word slug so _reuters_title_from_slug has something to work
# with), reused across the fixture below so a target can deliberately be
# linked from more than one place to test deduplication.
IRAN_URL = "https://www.reuters.com/world/middle-east/tehran-hints-hormuz-talks-with-us-leaders-gather-un-2026-09-22/"
MEDIA_BAN_URL = "https://www.reuters.com/legal/government/news-outlets-ask-judge-end-trumps-media-ban-2026-09-22/"
RAPE_CHARGE_URL = "https://www.reuters.com/business/media-telecom/looksmaxxing-influencer-charged-with-rape-2026-09-22/"
HM_URL = "https://www.reuters.com/business/retail-consumer/hm-founding-family-builds-stake-2026-09-23/"
THIRTY_K_URL = "https://www.reuters.com/legal/government/thirty-thousand-mistakes-2026-09-23/"
AIR_TRAFFIC_URL = "https://www.reuters.com/business/media-telecom/us-air-traffic-vulnerabilities-exposed-2026-09-23/"
CLEAN_STORY_URL = "https://www.reuters.com/world/asia-pacific/some-clean-headline-example-story-2026-09-23/"
STARBUCKS_URL = "https://www.reuters.com/lifestyle/culture-current/one-mans-decades-long-quest-to-visit-every-starbucks-2026-09-22/"


class ReutersDailyBriefing(unittest.TestCase):
    """Reuters Daily Briefing's real template labels every unit of content
    with its own "<type>_block" class and is structurally a multi-story
    digest, not a single essay. Two follow-on problems on top of that base
    template-awareness (see extract_reuters_daily_briefing/_reuters_teasers):

    1. Individual bullets are often written as newsletter-editor commentary
       framing a quote ("The president's words need no summary from me:
       'Will a deal be made...' he mused during his General Assembly
       speech.") rather than as a headline -- the commentary was becoming
       the title. Reuters' own click-tracking links
       (newslink.reuters.com/click/<id>/<base64 of the real target
       URL>/<suffix>) base64-encode the real reuters.com article underneath,
       and that URL's own slug reads far closer to a real headline, so a
       *suspicious* sentence-based title (quote marks, "no summary from
       me", too long) is swapped for the decoded slug title, with the
       original commentary moved to summary instead.

    2. A section-intro recap sentence can bundle several distinct stories'
       headlines into one sentence ("Emmanuel Macron dismisses Donald
       Trump's claims..., legal experts say the White House media ban
       likely won't stand up in court, and a Looksmaxxing influencer is
       charged with rape."), which used to become one fake combined
       article. A sentence linking to 2+ distinct decoded targets is now
       decomposed into one slug-titled unit per target instead.

    Both fixes share one `seen_targets` set across a whole extraction call,
    so a story a bulleted section already covers properly is never also
    emitted (worse) a second time from the terser intro recap.

    This fixture is a trimmed-down but structurally faithful reproduction
    of the real block layout (captured from a live issue), using
    real-shaped click-tracking links throughout so the decode path is
    genuinely exercised, not a fixture-only no-op."""

    FIXTURE = f"""
    <table class="paragraph_block"><tr><td>
      <p>Thanks for reading the Daily Briefing.
        <a href="{_reuters_click_link(IRAN_URL)}">Emmanuel Macron dismisses Donald Trump's claims</a>
        of securing peace in Gaza, legal experts say the White House
        <a href="{_reuters_click_link(MEDIA_BAN_URL)}">media ban</a>
        likely won't stand up in court, and a Looksmaxxing influencer
        <a href="{_reuters_click_link(RAPE_CHARGE_URL)}">is charged with rape</a>.</p>
      <p>Plus, there is a man with one name and one mission:
        <a href="{_reuters_click_link(STARBUCKS_URL)}">to visit every Starbucks on the planet</a>.</p>
    </td></tr></table>
    <table class="heading_block"><tr><td><h2>Today's Top News</h2></td></tr></table>
    <table class="image_block"><tr><td><img src="https://reuters.example/photo1.jpg"></td></tr></table>
    <table class="list_block"><tr><td><ul>
      <li>The president&#8217;s words need no summary from me: &#8220;Will a deal
        be made with Iran that lets them rebuild and create a far greater
        country than it ever was before?&#8221; he mused
        <a href="{_reuters_click_link(IRAN_URL)}">during his General Assembly speech</a>.
        <span>&#8220;Or do I</span> <span>annihilate the Islamic Republic and do it quickly?&#8221;</span></li>
      <li>A clean, ordinary Reuters headline sentence with no quotes at all
        describing <a href="{_reuters_click_link(CLEAN_STORY_URL)}">a normal linked story</a> plainly.</li>
      <li>No link in this one at all, just plain commentary text that should
        still become a reasonable article instead of being dropped outright.</li>
      <li>There is plenty more Reuters coverage from the General Assembly available here.
        <a href="https://reuters.example/signup">Sign up</a> to receive our Sustainable Switch newsletter.</li>
    </ul></td></tr></table>
    <table class="heading_block"><tr><td><h2>Business &amp; Markets</h2></td></tr></table>
    <table class="list_block"><tr><td><ul>
      <li>The billionaire family behind H&amp;M
        <a href="{_reuters_click_link(HM_URL)}">is building its stake</a>, fueling speculation.</li>
    </ul></td></tr></table>
    <table class="heading_block"><tr><td><h2>
      <a href="{_reuters_click_link(THIRTY_K_URL)}">30,000 Mistakes</a>
    </h2></td></tr></table>
    <table class="image_block"><tr><td><img src="https://reuters.example/photo2.jpg"></td></tr></table>
    <table class="paragraph_block"><tr><td>
      <p>US states may have added tens of thousands self-declared non-citizens to voter rolls.</p>
      <p>The cases expose weaknesses in voter registration systems across a dozen states.</p>
    </td></tr></table>
    <table class="heading_block"><tr><td><h2>And Finally...</h2></td></tr></table>
    <table class="image_block"><tr><td><img src="https://reuters.example/photo3.jpg"></td></tr></table>
    <table class="paragraph_block"><tr><td>
      <p>America's air-traffic control system runs on outdated telecom systems and decades-old computers.</p>
      <p>No wonder it keeps failing.</p>
    </td></tr></table>
    <table class="button_block"><tr><td><a href="{_reuters_click_link(AIR_TRAFFIC_URL)}">Read more</a></td></tr></table>
    <div>
      <p>Sponsored by: Fisher Investments</p>
      <p>7 Wealth Tips Once Your Portfolio Reaches $1 Million. <a href="https://sponsor.example/learn-more">Learn More</a></p>
    </div>
    """

    def _extract(self):
        soup = BeautifulSoup(self.FIXTURE, "html.parser")
        return extract_reuters_daily_briefing(soup)

    def test_commentary_framing_a_quote_is_moved_to_summary_and_the_real_headline_becomes_the_title(self):
        # Item 1: a linked real headline with editorial commentary/a quote
        # wrapped around it -- the decoded slug headline must become the
        # title, and the original commentary (quote included) must survive
        # somewhere in the summary rather than being discarded.
        articles = self._extract()
        match = next((a for a in articles if a["link"] == IRAN_URL), None)
        self.assertIsNotNone(match, [a["title"] for a in articles])
        self.assertNotIn("no summary from me", match["title"])
        self.assertIn("Tehran", match["title"])
        self.assertIn("no summary from me", match["summary"])
        self.assertIn("annihilate", match["summary"])

    def test_quote_split_across_inline_spans_stays_one_article_not_two(self):
        # Item 2: the closing quote is split across two adjacent <span>s in
        # the same <li> as the opening commentary -- this must never
        # produce a second, fake article out of the split-off half.
        articles = self._extract()
        matches = [a for a in articles if a["link"] == IRAN_URL]
        self.assertEqual(len(matches), 1, matches)

    def test_generic_and_finally_heading_never_becomes_the_title_when_a_clean_body_sentence_exists(self):
        # Item 3: "And Finally..." itself must never be used as a title --
        # regardless of whether a linked headline is reachable elsewhere,
        # since this heading never carries its own link at all.
        articles = self._extract()
        match = next((a for a in articles if a["link"] == AIR_TRAFFIC_URL), None)
        self.assertIsNotNone(match, [a["title"] for a in articles])
        self.assertNotEqual(match["title"], "And Finally...")
        self.assertIn("air-traffic control", match["title"])

    def test_section_intro_recap_bundling_several_stories_is_not_emitted_as_one_fake_article(self):
        # Item 4: the intro's first <p> links to three genuinely different
        # stories in one sentence -- none of those three targets may be
        # covered by ONE combined article; each must either appear as its
        # own distinct unit or (per the dedup test below) be skipped because
        # a fuller bulleted version already covers it.
        articles = self._extract()
        titles_by_link = {a["link"]: a["title"] for a in articles}
        # The recap sentence's own full run-on text must never be a title.
        self.assertFalse(
            any("Emmanuel Macron dismisses" in t and "Looksmaxxing" in t for t in titles_by_link.values())
        )
        self.assertIn(MEDIA_BAN_URL, titles_by_link)
        self.assertIn(RAPE_CHARGE_URL, titles_by_link)

    def test_intro_fragment_is_deduped_against_a_fuller_bulleted_story_for_the_same_target(self):
        # The intro's "Emmanuel Macron dismisses..." clause and the
        # "Threats and recrimination"-style bullet both link to IRAN_URL;
        # only the fuller, better-titled bulleted version may survive.
        articles = self._extract()
        matches = [a for a in articles if a["link"] == IRAN_URL]
        self.assertEqual(len(matches), 1, matches)
        self.assertIn("Tehran", matches[0]["title"])

    def test_normal_linked_headline_with_no_commentary_parses_normally(self):
        # Item 5: a clean bullet with no quotes/commentary keeps its own
        # sentence-based title unchanged -- the slug fallback must not
        # kick in when the sentence itself already reads fine.
        articles = self._extract()
        match = next((a for a in articles if a["link"] == CLEAN_STORY_URL), None)
        self.assertIsNotNone(match, [a["title"] for a in articles])
        self.assertIn("clean, ordinary Reuters headline sentence", match["title"])

    def test_fallback_when_no_link_is_present_still_emits_a_reasonable_article(self):
        # Item 6: a bullet with no anchor at all must still become an
        # article (sentence-based title, empty link) rather than being
        # dropped outright.
        articles = self._extract()
        match = next((a for a in articles if "No link in this one at all" in a["title"]), None)
        self.assertIsNotNone(match, [a["title"] for a in articles])
        self.assertEqual(match["link"], "")

    def test_no_article_duplicates_its_own_title_in_summary_or_body(self):
        # Item 7.
        articles = self._extract()
        for a in articles:
            if a["summary"]:
                self.assertNotEqual(a["title"].strip(), a["summary"].strip(), a)
            if a["content_html"]:
                self.assertNotEqual(
                    clean_text(a["title"]), clean_text(BeautifulSoup(a["content_html"], "html.parser").get_text(" ")), a
                )

    def test_self_promotional_bullet_is_dropped(self):
        articles = self._extract()
        titles = " ".join(a["title"] for a in articles)
        self.assertNotIn("plenty more Reuters coverage", titles)

    def test_feature_story_with_its_own_linked_heading_keeps_that_headline_verbatim(self):
        articles = self._extract()
        match = next((a for a in articles if a["title"] == "30,000 Mistakes"), None)
        self.assertIsNotNone(match, [a["title"] for a in articles])
        self.assertEqual(match["link"], THIRTY_K_URL)
        self.assertIn("voter registration", match["content_html"])

    def test_sponsor_ad_is_never_extracted_as_an_article(self):
        articles = self._extract()
        joined = " ".join(a["title"] for a in articles)
        self.assertNotIn("Wealth Tips", joined)
        self.assertNotIn("Fisher Investments", joined)

    def test_no_duplicate_titles_across_the_whole_issue(self):
        articles = self._extract()
        titles = [a["title"] for a in articles]
        self.assertEqual(len(titles), len(set(titles)), titles)

    def test_reuters_daily_briefing_is_no_longer_a_full_letter_sender(self):
        self.assertNotIn(REUTERS_DAILY_BRIEFING_SENDER, FULL_LETTER_SENDERS)

    def test_extract_articles_dispatches_to_the_block_aware_extractor(self):
        html = f"<html><body>{self.FIXTURE}</body></html>"
        articles = extract_articles(html, "", "Rebuild or annihilate", REUTERS_DAILY_BRIEFING_SENDER)
        # The old behavior produced exactly one garbled article titled with
        # the subject line; the fixed behavior produces several distinct,
        # real stories and never uses the subject as a title.
        self.assertGreater(len(articles), 1)
        self.assertNotIn("Rebuild or annihilate", [a["title"] for a in articles])

    def test_unrecognisable_template_falls_back_to_the_generic_scan(self):
        # If Reuters ever changes this template entirely (no "_block"
        # classes at all), extraction must not silently return nothing --
        # it should fall back to the ordinary per-anchor scan.
        html = (
            "<html><body><div><a href=\"https://reuters.example/x\">"
            "A real headline with enough words to pass the length check</a></div></body></html>"
        )
        articles = extract_articles(html, "", "Some subject", REUTERS_DAILY_BRIEFING_SENDER)
        self.assertEqual(len(articles), 1)
        self.assertIn("real headline", articles[0]["title"])


class ReutersLinkDecoding(unittest.TestCase):
    """Unit-level coverage for the click-link decoder and slug-title
    generator underneath ReutersDailyBriefing's title fallback, independent
    of the full block-walking fixture above."""

    def test_decodes_a_real_shaped_click_link_to_its_target(self):
        href = _reuters_click_link(IRAN_URL)
        self.assertEqual(_decode_reuters_click_target(href), IRAN_URL.split("?", 1)[0])

    def test_strips_tracking_query_params_from_the_decoded_target(self):
        tracked = IRAN_URL.rstrip("/") + "/?utm_source=Sailthru&utm_medium=Newsletter"
        href = _reuters_click_link(tracked)
        self.assertEqual(_decode_reuters_click_target(href), IRAN_URL.split("?", 1)[0])

    def test_non_click_link_shape_returns_none(self):
        self.assertIsNone(_decode_reuters_click_target("https://www.reuters.com/world/some-story/"))

    def test_click_link_to_a_non_reuters_target_returns_none(self):
        href = _reuters_click_link("https://example.com/not-reuters/")
        self.assertIsNone(_decode_reuters_click_target(href))

    def test_slug_title_from_a_real_looking_url(self):
        title = _reuters_title_from_slug(IRAN_URL)
        self.assertEqual(title, "Tehran Hints Hormuz Talks with US Leaders Gather UN")

    def test_slug_title_strips_the_trailing_publish_date(self):
        title = _reuters_title_from_slug(IRAN_URL)
        self.assertNotIn("2026", title)

    def test_slug_title_returns_none_for_a_bare_category_page(self):
        self.assertIsNone(_reuters_title_from_slug("https://www.reuters.com/world/united-nations/"))

    def test_suspicious_title_flags_quoted_commentary(self):
        self.assertTrue(
            _is_suspicious_reuters_title(
                'The president’s words need no summary from me: “Will a deal be made?”'
            )
        )

    def test_suspicious_title_flags_extremely_long_sentences(self):
        self.assertTrue(_is_suspicious_reuters_title("A " + "very " * 40 + "long sentence."))

    def test_suspicious_title_does_not_flag_an_ordinary_clean_sentence(self):
        self.assertFalse(
            _is_suspicious_reuters_title("The billionaire family behind H&M is building its stake.")
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
