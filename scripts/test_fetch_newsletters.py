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
    FORWARDED_DATE_RE,
    FORWARDED_SUBJECT_RE,
    FULL_LETTER_SENDERS,
    body_is_just_the_title,
    br_split_groups,
    clean_text,
    extract_articles,
    extract_full_letter,
    find_view_online_link,
    get_text_no_glue,
    group_index_containing,
    parse_forwarded_date,
    resolve_reading_time_min,
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
        article = extract_articles(html, "", "'Disaster' election in Germany", "Reuters Daily Briefing")[0]
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
