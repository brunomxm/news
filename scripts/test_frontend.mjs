// Targeted regression tests for app.js / readstate.js / reader.js, run with
// plain Node (no browser, no test framework, no new dependencies):
//
//   node scripts/test_frontend.mjs
//
// Each file is evaluated in a small vm sandbox with just the globals it
// touches stubbed out, so the real, unmodified site files are exercised
// directly rather than reimplementing their logic here.

import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, "..");

function readFile(name) {
  return fs.readFileSync(path.join(ROOT, name), "utf8");
}

let passed = 0;
let failed = 0;

function test(name, fn) {
  try {
    fn();
    console.log(`ok - ${name}`);
    passed++;
  } catch (err) {
    console.error(`FAIL - ${name}`);
    console.error(err);
    failed++;
  }
}

async function testAsync(name, fn) {
  try {
    await fn();
    console.log(`ok - ${name}`);
    passed++;
  } catch (err) {
    console.error(`FAIL - ${name}`);
    console.error(err);
    failed++;
  }
}

// ---------------------------------------------------------------------
// Item 1: the Culture section must render every item in it, not cap at 4.
// ---------------------------------------------------------------------

function loadAppSandbox(readState) {
  const sandbox = {
    ReadState: readState || { isRead: () => false },
    document: {
      // Used by app.js's stripTags() (hasReadableContent's body check).
      createElement: (tag) => {
        assert.equal(tag, "div");
        let html = "";
        return {
          set innerHTML(v) {
            html = v;
          },
          get textContent() {
            return html.replace(/<[^>]+>/g, "");
          },
        };
      },
    },
    console,
  };
  vm.createContext(sandbox);
  // app.js reads its editorial priority rules (SOURCE_PRIORITY, SOURCE_TYPE,
  // TYPE_HERO_BONUS, IMAGE_HERO_BONUS, LONGREAD_WORD_THRESHOLD) from
  // config.js, same as the real page does via <script src="config.js">
  // loading before <script src="app.js">.
  vm.runInContext(readFile("config.js"), sandbox, { filename: "config.js" });
  // app.js's bottom-of-file fetch(...) bootstrap would throw without a
  // real fetch/DOM -- strip it out; every function above it is pure/DOM-free
  // and is what we're actually testing.
  const src = readFile("app.js").replace(/\nPromise\.all\(\[[\s\S]*$/, "\n");
  vm.runInContext(src, sandbox, { filename: "app.js" });
  return sandbox;
}

function makeArticle(i, section) {
  return {
    id: `id-${section}-${i}`,
    // Unique by default so tests that don't care about grouping get 29
    // independent single-article units, not one giant accidental issue.
    issue_id: `issue-${section}-${i}`,
    source: "Test Source",
    title: `Item ${i}`,
    summary: `Summary for item ${i}.`,
    link: `https://example.com/${i}`,
    image: null,
    date: new Date(Date.now() - i * 3600_000).toISOString(),
    reading_time_min: 3,
    section,
  };
}

test("Culture section renders all 29 items, not just 4", () => {
  const sandbox = loadAppSandbox();
  const items = Array.from({ length: 29 }, (_, i) => makeArticle(i, "Culture"));
  const html = sandbox.renderSection("Culture", sandbox.groupIntoUnits(items));

  // 1 feature block (lead) + 28 compact rows (<li>) = every item reachable.
  const liCount = (html.match(/<li/g) || []).length;
  assert.equal(liCount, 28, `expected 28 <li> rows, got ${liCount}`);
  for (const item of items) {
    assert.ok(html.includes(item.title), `missing "${item.title}" from rendered Culture section`);
  }
});

test("Culture section header count matches the number of items passed in", () => {
  const sandbox = loadAppSandbox();
  const items = Array.from({ length: 29 }, (_, i) => makeArticle(i, "Culture"));
  const html = sandbox.renderSection("Culture", sandbox.groupIntoUnits(items));
  assert.ok(html.includes(">29<"), 'section header should show the count "29"');
});

test("a card for a teaser with nothing to show links straight to the original, not article.html", () => {
  const sandbox = loadAppSandbox();
  const teaser = {
    id: "teaser-1",
    source: "Il Post",
    title: "Uno che correva fortissimo. E che oggi compie 50 anni.",
    summary: "",
    link: "https://x.ilpost.it/re?l=abc",
    image: null,
    date: new Date().toISOString(),
    reading_time_min: null,
    content_html: null,
    section: "Culture",
  };
  const attrs = sandbox.cardLinkAttrs(teaser);
  assert.ok(attrs.includes('href="https://x.ilpost.it/re?l=abc"'), attrs);
  assert.ok(attrs.includes('target="_blank"'), "must open in a new tab, not replace the feed");
  assert.ok(attrs.includes(`data-mark-read="teaser-1"`), "must be wired up to mark itself read on click");
  assert.ok(!attrs.includes("article.html"));
});

test("a card for an article with a real body or summary still links to article.html", () => {
  const sandbox = loadAppSandbox();
  const withBody = makeArticle(1, "Culture");
  const attrsBody = sandbox.cardLinkAttrs({ ...withBody, content_html: "<p>Real, distinct body text here.</p>" });
  assert.ok(attrsBody.includes("article.html?id=id-Culture-1"));
  assert.ok(!attrsBody.includes("data-mark-read"));

  const withSummary = makeArticle(2, "Culture");
  const attrsSummary = sandbox.cardLinkAttrs({ ...withSummary, content_html: null });
  assert.ok(attrsSummary.includes("article.html?id=id-Culture-2"));
});

// ---------------------------------------------------------------------
// 2026-09-22 request: newsletter-issue grouping on the homepage.
// ---------------------------------------------------------------------

// A roundup issue's several stories, all sharing one issue_id -- same
// source/subject/date, as extraction always sets them (see
// stable_issue_id's own tests on the Python side).
function makeIssueArticles(issueId, n, opts = {}) {
  const date = opts.date || new Date().toISOString();
  const source = opts.source || "TLDR AI";
  const subject = opts.subject || "Some roundup subject";
  return Array.from({ length: n }, (_, i) => ({
    id: `${issueId}-story-${i}`,
    issue_id: issueId,
    source,
    subject,
    title: opts.titles ? opts.titles[i] : `Story ${i} (${i + 1} minute read)`,
    summary: "",
    link: `https://example.com/${issueId}/${i}`,
    image: null,
    date,
    reading_time_min: i + 1,
    word_count: 80,
    content_html: null,
    section: opts.section || "Technology & AI",
  }));
}

test("a roundup's several stories collapse into one issue unit", () => {
  const sandbox = loadAppSandbox();
  const stories = makeIssueArticles("issue-1", 5);
  const units = sandbox.groupIntoUnits(stories);
  assert.equal(units.length, 1, "5 stories sharing one issue_id must produce exactly 1 unit");
  assert.equal(units[0].kind, "issue");
  assert.equal(units[0].stories.length, 5);
});

test("a single-essay email stays an ordinary article unit, not a one-item accordion", () => {
  const sandbox = loadAppSandbox();
  const essay = makeArticle(0, "World & Ideas");
  const units = sandbox.groupIntoUnits([essay]);
  assert.equal(units.length, 1);
  assert.equal(units[0].kind, "article", "a lone issue_id must render as a normal card, not an issue row");
});

test("grouping is driven by issue_id alone, not by sender name", () => {
  const sandbox = loadAppSandbox();
  // Two different senders, same issue_id: an implementation that special-
  // cased sender names would never produce this, but a correct issue_id-
  // driven grouper doesn't care what the sender is.
  const stories = [
    { ...makeArticle(0, "Culture"), issue_id: "shared", source: "Sender A" },
    { ...makeArticle(1, "Culture"), issue_id: "shared", source: "Sender B" },
  ];
  const units = sandbox.groupIntoUnits(stories);
  assert.equal(units.length, 1);
  assert.equal(units[0].kind, "issue");
});

test("units preserve the original date-sorted order of issues and stories", () => {
  const sandbox = loadAppSandbox();
  const now = Date.now();
  const single1 = { ...makeArticle(0, "Culture"), id: "single-1", issue_id: "single-1", date: new Date(now).toISOString() };
  const issueStories = makeIssueArticles("issue-mid", 3, { date: new Date(now - 1000).toISOString() });
  const single2 = { ...makeArticle(1, "Culture"), id: "single-2", issue_id: "single-2", date: new Date(now - 2000).toISOString() };
  // Already in date-sorted order, as fetch_newsletters.py's main() sorts
  // the real feed -- groupIntoUnits must not reorder anything.
  const articles = [single1, ...issueStories, single2];
  const units = sandbox.groupIntoUnits(articles);
  assert.equal(units.length, 3);
  assert.equal(units[0].article.id, "single-1");
  assert.equal(units[1].kind, "issue");
  // JSON.stringify rather than assert.deepEqual: the "stories" array is
  // built inside the vm sandbox (a different V8 realm than this test file),
  // and Node's assert.deepStrictEqual refuses to treat a same-content array
  // from one realm as equal to one from another.
  assert.equal(
    JSON.stringify(units[1].stories.map((s) => s.id)),
    JSON.stringify(issueStories.map((s) => s.id))
  );
  assert.equal(units[2].article.id, "single-2");
});

test("hero and long-read picks skip issue units, leaving them in the section flow", () => {
  const sandbox = loadAppSandbox();
  const issueStories = makeIssueArticles("issue-first", 4, { section: "Technology & AI" });
  const essay = { ...makeArticle(0, "World & Ideas"), word_count: 900 };
  const units = sandbox.groupIntoUnits([...issueStories, essay]);

  const { picked: hero, rest: afterHero } = sandbox.pickFirstArticleUnit(units);
  assert.equal(hero.id, essay.id, "the leading issue unit must never become the hero");
  assert.equal(afterHero.length, 1, "the issue unit must remain in the list, unconsumed");
  assert.equal(afterHero[0].kind, "issue");

  // LONGREAD_WORD_THRESHOLD is a top-level `const` in config.js, so it isn't
  // exposed on the sandbox object (see READSTATE_SRC's own comment on this);
  // read it straight out of the source instead of hardcoding a copy that
  // could silently drift from the real value.
  const thresholdMatch = readFile("config.js").match(/LONGREAD_WORD_THRESHOLD = (\d+)/);
  const threshold = Number(thresholdMatch[1]);
  const { picked: longread } = sandbox.pickFirstArticleUnit(
    units,
    (a) => a.word_count >= threshold
  );
  assert.equal(longread.id, essay.id, "the leading issue unit must never become the long-read pick either");
});

test("a section's featured lead skips issue units, same as the page hero", () => {
  const sandbox = loadAppSandbox();
  const issueStories = makeIssueArticles("issue-culture", 3, { section: "Culture" });
  const essay = makeArticle(0, "Culture");
  const units = sandbox.groupIntoUnits([...issueStories, essay]);
  const html = sandbox.renderSection("Culture", units);
  // The essay (an ordinary article) is the feature lead; the issue renders
  // as a collapsed row alongside it, not as the section's big feature block.
  assert.ok(html.includes("feature-headline"), html);
  assert.ok(html.includes("issue-toggle"), html);
  assert.ok(html.includes(essay.title));
});

test("the collapsed row shows source, time, story count, and a preview -- not just source and time", () => {
  const sandbox = loadAppSandbox();
  const stories = makeIssueArticles("issue-preview", 3, {
    source: "TLDR Data",
    titles: ["First real headline here", "Second real headline here", "Third"],
  });
  const html = sandbox.issueRow(sandbox.groupIntoUnits(stories)[0]);
  assert.ok(html.includes("TLDR Data"), "source must be shown");
  assert.ok(html.includes("3 stories"), "story count must be shown");
  assert.ok(html.includes("First real headline here"), "a preview of the leading stories must be shown");
});

test("the preview is only the lead story's own headline -- two stories never get glued into one", () => {
  // Reported bug: joining two different stories' titles with just a "·"
  // between them ("...(11 minute read) · From 17ms to 0.04ms: How to
  // Design...") read as one garbled run-on title, with the second story's
  // headline mistaken for the first story's own body copy starting.
  const sandbox = loadAppSandbox();
  const stories = makeIssueArticles("issue-nomerge", 3, {
    source: "TLDR Data",
    titles: [
      "How we knew COVID was over (and what our models had to unlearn) (11 minute read)",
      "From 17ms to 0.04ms: How to Design the Right SQL Index (6 minute read)",
      "Third",
    ],
  });
  const html = sandbox.issueRow(sandbox.groupIntoUnits(stories)[0]);
  const headlineMatch = html.match(/class="issue-headline"[^>]*>([\s\S]*?)<span class="issue-chevron"/);
  assert.ok(headlineMatch, html);
  assert.equal(
    headlineMatch[1],
    "How we knew COVID was over (and what our models had to unlearn) (11 minute read)"
  );
  assert.ok(
    !headlineMatch[1].includes("From 17ms"),
    "the second story's headline must not be appended to the first"
  );
});

test("the preview stays compact even when the lead headline alone is long", () => {
  // Il Post's digest teasers, in particular, can be long, self-contained
  // sentences that would still wrap several lines on a phone screen -- the
  // opposite of the "compact row" this is meant to be.
  const sandbox = loadAppSandbox();
  const stories = makeIssueArticles("issue-longpreview", 3, {
    source: "The Conversation",
    titles: [
      "Rana Mitter has his doubts given the difficulties underlying each of the issues as well as the fact that the leaders seem to have fundamentally incompatible agendas on all three topics.",
      "But there is a more troubling reading, too – and it rings alarm bells for democracy.",
      "Third",
    ],
  });
  const html = sandbox.issueRow(sandbox.groupIntoUnits(stories)[0]);
  const headlineMatch = html.match(/class="issue-headline"[^>]*>([\s\S]*?)<span class="issue-chevron"/);
  assert.ok(headlineMatch, html);
  assert.ok(headlineMatch[1].length <= 141, `preview text is too long to be "compact": ${headlineMatch[1].length} chars`);
  assert.ok(headlineMatch[1].endsWith("…"), "an over-long preview must end with an ellipsis");
  assert.ok(
    !headlineMatch[1].includes("troubling reading"),
    "the second story's headline must not be appended to the first"
  );
});

test("the collapsed headline is never styled or colored like a bare hyperlink", () => {
  // Reported on a real device: the (aria-hidden, decorative) preview text
  // rendered blue and underlined, like an unstyled <a> -- even though it
  // sits inside a <button>, not an anchor. Assert the CSS pins both
  // properties explicitly rather than leaving them to inheritance/UA
  // defaults, on both the element that showed the bug and its container.
  const css = readFile("styles.css");
  assert.match(css, /\.issue-headline\s*\{[^}]*color:\s*var\(--text\)/s);
  assert.match(css, /\.issue-headline\s*\{[^}]*text-decoration:\s*none/s);
  assert.match(css, /\.issue-toggle\s*\{[^}]*color:\s*var\(--text\)/s);
});

test("the hover underline is gated to real pointers, so a tap can't leave it stuck on", () => {
  // Still reported after the color fix landed: on a touch device, tapping
  // the row to expand it left the headline permanently underlined --
  // touchscreens report a tap as :hover with no way to un-hover short of
  // tapping something else, so an unguarded ".issue-toggle:hover" rule
  // never turns back off. The hover-only affordance must be scoped to
  // devices that actually have a real pointer.
  const css = readFile("styles.css");
  // The media block wraps exactly one nested rule, so two closing braces
  // (inner rule, then the block itself) marks its end.
  const mediaMatch = css.match(
    /@media \(hover: hover\) and \(pointer: fine\)\s*\{\s*\.issue-toggle:hover \.issue-headline \{[^}]*\}\s*\}/
  );
  assert.ok(mediaMatch, "the :hover underline rule must be wrapped in a (hover: hover) media guard");
  assert.ok(
    !mediaMatch[0].includes(":focus-visible"),
    "the keyboard-focus affordance must not be inside the pointer-only guard"
  );
  // The keyboard-focus affordance is unaffected by this guard -- it's
  // :focus-visible, which browsers already withhold on touch/pointer
  // activation, so keyboard users still get visible feedback regardless.
  assert.ok(css.includes(".issue-toggle:focus-visible .issue-headline"));
});

test("an older issue's collapsed row shows its own date, not just today's clock time", () => {
  // Reported bug: a TLDR Data roundup from 24 Aug rendered as "TLDR Data ·
  // 12:23 · 14 stories" -- a bare clock time with no date at all, which
  // reads as if it happened today at 12:23. Every other card's meta line
  // uses relativeTime ("29d ago"), which carries its own age signal; the
  // issue row didn't, so it needs an explicit date instead.
  const sandbox = loadAppSandbox();
  const oldDate = new Date("2026-08-24T12:23:00Z").toISOString();
  const stories = makeIssueArticles("issue-old", 14, { source: "TLDR Data", date: oldDate });
  const unit = sandbox.groupIntoUnits(stories)[0];
  const expectedDateLabel = new Date(oldDate).toLocaleDateString(undefined, { day: "numeric", month: "short" });

  const html = sandbox.issueRow(unit);
  assert.ok(
    html.includes(expectedDateLabel),
    `visible meta text must include the date ("${expectedDateLabel}"): ${html}`
  );

  const ariaLabelMatch = html.match(/aria-label="([^"]*)"/);
  assert.ok(ariaLabelMatch, "issue-toggle must carry an aria-label");
  assert.ok(
    ariaLabelMatch[1].includes(expectedDateLabel),
    `screen-reader label must include the date too, not just source and time: got "${ariaLabelMatch[1]}"`
  );
});

test("the issue row is a real button wired for keyboard and screen-reader use", () => {
  const sandbox = loadAppSandbox();
  const stories = makeIssueArticles("issue-a11y", 4, { source: "TLDR AI" });
  const html = sandbox.issueRow(sandbox.groupIntoUnits(stories)[0]);
  assert.match(html, /<button[^>]*class="issue-toggle"[^>]*aria-expanded="false"/);
  assert.match(html, /aria-controls="issue-issue-a11y-stories"/);
  assert.match(html, /id="issue-issue-a11y-stories"[^>]*role="group"/);
  assert.match(html, /aria-labelledby="issue-issue-a11y-toggle"/);
  assert.match(html, /<ul[^>]*id="issue-issue-a11y-stories"[^>]* hidden>/, "the panel must start hidden (collapsed)");
  // The accessible name covers source, time, count and preview even though
  // the visible headline span is aria-hidden (decorative chevron included).
  assert.match(html, /aria-label="TLDR AI, [^"]*4 stories[^"]*:/);
});

test("collapsed by default: only one issue-toggle button, panel starts hidden", () => {
  const sandbox = loadAppSandbox();
  const stories = makeIssueArticles("issue-collapsed", 6);
  const html = sandbox.issueRow(sandbox.groupIntoUnits(stories)[0]);
  const toggleCount = (html.match(/class="issue-toggle"/g) || []).length;
  assert.equal(toggleCount, 1);
  assert.ok(/<ul[^>]*hidden>/.test(html));
});

test("each story inside an expanded issue keeps its own normal link behavior", () => {
  const sandbox = loadAppSandbox();
  // One story with a real body (stays on article.html), one bare teaser
  // (goes straight to the original) -- issueRow must not flatten this
  // per-story distinction just because they're grouped.
  const stories = makeIssueArticles("issue-links", 2);
  stories[0].content_html = "<p>A full, distinct body paragraph for this story.</p>";
  stories[1].content_html = null;
  stories[1].summary = "";
  const html = sandbox.issueRow(sandbox.groupIntoUnits(stories)[0]);
  assert.ok(html.includes(`article.html?id=${stories[0].id}`), "story with a real body stays on article.html");
  assert.ok(html.includes(`data-mark-read="${stories[1].id}"`), "bare teaser links straight to the original");
  assert.ok(html.includes(`target="_blank"`));
});

test("each story keeps its own individual read state inside the collapsed group", () => {
  const stories = makeIssueArticles("issue-read", 3);
  const sandbox = loadAppSandbox({ isRead: (id) => id === stories[1].id });
  const html = sandbox.issueRow(sandbox.groupIntoUnits(stories)[0]);
  // slice(2), not slice(1): the first "<li" split segment is the issue
  // row's own opening <li class="issue-row" ...>, not a story.
  const rows = html.split("<li").slice(2);
  assert.ok(rows[0] && !rows[0].startsWith(' class="is-read"'), "story 0 unread");
  assert.ok(rows[1] && rows[1].startsWith(' class="is-read"'), "story 1 (the read one) must be dimmed");
  assert.ok(rows[2] && !rows[2].startsWith(' class="is-read"'), "story 2 unread");
});

test("aggregate progress is subtle (shown only once something is read) and there is no mark-issue-read action", () => {
  const stories = makeIssueArticles("issue-progress", 4);

  const sandboxNone = loadAppSandbox({ isRead: () => false });
  const htmlNoneRead = sandboxNone.issueRow(sandboxNone.groupIntoUnits(stories)[0]);
  assert.ok(!htmlNoneRead.includes("read</span>"), "no progress text before anything is read");
  assert.ok(!htmlNoneRead.includes("data-mark-issue-read"), "no separate mark-issue-read action exists");
  assert.ok(!/mark.{0,20}issue.{0,20}read/i.test(htmlNoneRead));

  const sandboxTwo = loadAppSandbox({ isRead: (id) => id === stories[0].id || id === stories[1].id });
  const htmlTwoRead = sandboxTwo.issueRow(sandboxTwo.groupIntoUnits(stories)[0]);
  assert.ok(htmlTwoRead.includes("2/4 read"), htmlTwoRead);
});

// A minimal, hand-rolled fake DOM -- just enough surface for
// setupIssueToggle()/setupReadToggle() to run against (closest, classList,
// getAttribute/setAttribute, a getElementById registry, addEventListener
// with a `dispatch` test helper) -- rather than pulling in jsdom, which
// this project deliberately has no dependencies on.
class FakeElement {
  constructor(tag, attrs = {}) {
    this.tag = tag;
    this.attrs = { ...attrs };
    this.parent = null;
    this._hidden = false;
    this._classes = new Set((attrs.class || "").split(/\s+/).filter(Boolean));
    this._listeners = {};
  }
  get hidden() {
    return this._hidden;
  }
  set hidden(v) {
    this._hidden = Boolean(v);
  }
  get classList() {
    const set = this._classes;
    return {
      add: (c) => set.add(c),
      remove: (c) => set.delete(c),
      contains: (c) => set.has(c),
    };
  }
  getAttribute(name) {
    return name in this.attrs ? this.attrs[name] : null;
  }
  setAttribute(name, value) {
    this.attrs[name] = String(value);
  }
  matches(selector) {
    return selector
      .split(",")
      .map((s) => s.trim())
      .some((s) => (s.startsWith(".") ? this._classes.has(s.slice(1)) : this.tag === s));
  }
  closest(selector) {
    let el = this;
    while (el) {
      if (el.matches(selector)) return el;
      el = el.parent;
    }
    return null;
  }
  appendChild(child) {
    child.parent = this;
    return child;
  }
  addEventListener(type, cb) {
    (this._listeners[type] ||= []).push(cb);
  }
  dispatch(type, target) {
    const event = { target, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; } };
    for (const cb of this._listeners[type] || []) cb(event);
    return event;
  }
}

function loadInteractiveAppSandbox(readState) {
  const registry = new Map();
  const feed = new FakeElement("div", { id: "feed" });
  registry.set("feed", feed);
  const markReadCalls = [];
  const markUnreadCalls = [];
  const sandbox = {
    ReadState: {
      isRead: (id) => Boolean(readState && readState[id]),
      markRead: (id) => markReadCalls.push(id),
      markUnread: (id) => markUnreadCalls.push(id),
    },
    document: {
      getElementById: (id) => registry.get(id) || null,
      createElement: () => ({ set innerHTML(_v) {}, get textContent() { return ""; } }),
    },
    console,
  };
  vm.createContext(sandbox);
  const src = readFile("app.js").replace(/\nPromise\.all\(\[[\s\S]*$/, "\n");
  vm.runInContext(src, sandbox, { filename: "app.js" });
  return { sandbox, feed, registry, markReadCalls, markUnreadCalls };
}

test("clicking the collapsed row expands it inline; clicking again collapses it", () => {
  const { sandbox, feed, registry, markReadCalls } = loadInteractiveAppSandbox();
  sandbox.setupIssueToggle();

  const toggle = new FakeElement("button", {
    class: "issue-toggle",
    id: "issue-x-toggle",
    "aria-expanded": "false",
    "aria-controls": "issue-x-stories",
  });
  const panel = new FakeElement("ul", { id: "issue-x-stories" });
  panel.hidden = true;
  registry.set("issue-x-toggle", toggle);
  registry.set("issue-x-stories", panel);

  feed.dispatch("click", toggle);
  assert.equal(toggle.getAttribute("aria-expanded"), "true", "first click must expand");
  assert.equal(panel.hidden, false, "panel must become visible");

  feed.dispatch("click", toggle);
  assert.equal(toggle.getAttribute("aria-expanded"), "false", "second click must collapse");
  assert.equal(panel.hidden, true, "panel must hide again");

  assert.equal(markReadCalls.length, 0, "expanding/collapsing must never mark anything read");
});

test("clicking inside the expanded panel (not the toggle itself) does not toggle it", () => {
  const { sandbox, feed, registry } = loadInteractiveAppSandbox();
  sandbox.setupIssueToggle();

  const toggle = new FakeElement("button", {
    class: "issue-toggle",
    id: "issue-y-toggle",
    "aria-expanded": "true",
    "aria-controls": "issue-y-stories",
  });
  const panel = new FakeElement("ul", { id: "issue-y-stories" });
  panel.hidden = false;
  registry.set("issue-y-toggle", toggle);
  registry.set("issue-y-stories", panel);

  // A story link inside the panel: not the toggle, and .closest(".issue-toggle")
  // must not find one by walking up past the panel (no parent wired here,
  // matching a real click target unrelated to the toggle).
  const storyLink = new FakeElement("a", {});
  feed.dispatch("click", storyLink);
  assert.equal(toggle.getAttribute("aria-expanded"), "true", "unrelated clicks must not collapse the row");
  assert.equal(panel.hidden, false);
});

// ---------------------------------------------------------------------
// Item 2: one-time localStorage id migration (old hash scheme -> new).
// ---------------------------------------------------------------------

function makeFakeStorage() {
  const store = new Map();
  return {
    getItem(k) {
      return store.has(k) ? store.get(k) : null;
    },
    setItem(k, v) {
      store.set(k, String(v));
    },
    _dump() {
      return store;
    },
  };
}

// readstate.js declares `const ReadState = ...` -- top-level const/let
// bindings in vm.runInContext live in a separate lexical environment and
// are NOT exposed as properties of the sandbox object (unlike var/function
// declarations), so every load below appends this to export it explicitly.
const READSTATE_SRC = readFile("readstate.js") + "\nglobalThis.ReadState = ReadState;\n";

await testAsync("migration remaps old ids to new ids for links present in both feeds", async () => {
  const mapping = { "old-1": "new-1", "old-2": "new-2" };
  const fetchImpl = async () => ({ ok: true, json: async () => mapping });

  // Seed localStorage as if the old feed had marked these two read, plus
  // one id that isn't in the mapping at all (nothing should touch it) --
  // *before* the module ever loads, matching a real page load where the
  // pre-existing read set is already in localStorage when the script runs.
  const storage = makeFakeStorage();
  storage.setItem("bznews:read", JSON.stringify(["old-1", "old-2", "untouched"]));

  const sandbox = { localStorage: storage, fetch: fetchImpl, console };
  vm.createContext(sandbox);
  vm.runInContext(READSTATE_SRC, sandbox, { filename: "readstate.js" });
  await sandbox.ReadState.ready;

  assert.equal(sandbox.ReadState.isRead("new-1"), true);
  assert.equal(sandbox.ReadState.isRead("new-2"), true);
  assert.equal(sandbox.ReadState.isRead("old-1"), false);
  assert.equal(sandbox.ReadState.isRead("old-2"), false);
  // An id the migration doesn't know about is left alone, not dropped.
  assert.equal(sandbox.ReadState.isRead("untouched"), true);
});

await testAsync("migration only runs once per path (idempotent across reloads)", async () => {
  let fetchCalls = 0;
  const mapping = { "old-1": "new-1" };
  const fetchImpl = async () => {
    fetchCalls++;
    return { ok: true, json: async () => mapping };
  };

  const storage = makeFakeStorage();
  storage.setItem("bznews:read", JSON.stringify(["old-1"]));

  const sandboxA = { localStorage: storage, fetch: fetchImpl, console };
  vm.createContext(sandboxA);
  vm.runInContext(READSTATE_SRC, sandboxA, { filename: "readstate.js" });
  await sandboxA.ReadState.ready;
  assert.equal(sandboxA.ReadState.isRead("new-1"), true);

  // A second "page load" sharing the same localStorage must not re-fetch
  // or re-touch anything -- the migrated-flag should short-circuit it.
  const sandboxB = { localStorage: storage, fetch: fetchImpl, console };
  vm.createContext(sandboxB);
  vm.runInContext(READSTATE_SRC, sandboxB, { filename: "readstate.js" });
  await sandboxB.ReadState.ready;

  assert.equal(fetchCalls, 1, `expected exactly 1 fetch across two loads, got ${fetchCalls}`);
  assert.equal(sandboxB.ReadState.isRead("new-1"), true);
});

await testAsync("migration retries after a failed mapping fetch", async () => {
  let fetchCalls = 0;
  const fetchImpl = async () => {
    fetchCalls++;
    if (fetchCalls === 1) throw new Error("temporary network failure");
    return { ok: true, json: async () => ({ "old-1": "new-1" }) };
  };
  const storage = makeFakeStorage();
  storage.setItem("bznews:read", JSON.stringify(["old-1"]));

  const first = { localStorage: storage, fetch: fetchImpl, console };
  vm.createContext(first);
  vm.runInContext(READSTATE_SRC, first, { filename: "readstate.js" });
  await first.ReadState.ready;
  assert.equal(first.ReadState.isRead("old-1"), true);
  assert.equal(storage.getItem("bznews:read:migrated"), null);

  const second = { localStorage: storage, fetch: fetchImpl, console };
  vm.createContext(second);
  vm.runInContext(READSTATE_SRC, second, { filename: "readstate.js" });
  await second.ReadState.ready;
  assert.equal(fetchCalls, 2);
  assert.equal(second.ReadState.isRead("new-1"), true);
});

test("the shipped 2026-09-21 migration file covers the 73c2a55 -> f9e6349 id change", () => {
  const migrationPath = path.join(ROOT, "data", "id_migration_2026-09-21.json");
  assert.ok(fs.existsSync(migrationPath), "data/id_migration_2026-09-21.json must exist");
  const mapping = JSON.parse(fs.readFileSync(migrationPath, "utf8"));
  assert.equal(Object.keys(mapping).length, 72, "expected 72 remapped links (old<->new common links)");
  // Every value must be a plausible 16-hex-char id, same shape as stable_id().
  for (const [oldId, newId] of Object.entries(mapping)) {
    assert.match(oldId, /^[0-9a-f]{16}$/);
    assert.match(newId, /^[0-9a-f]{16}$/);
  }
});

// ---------------------------------------------------------------------
// Item 3: reader.js must not show a body that's just the title repeated.
// ---------------------------------------------------------------------

function loadReaderSandbox() {
  // render() reads back its own document.getElementById("reader") result
  // (via main.querySelector(...)) within the same call, so the mock must
  // return the SAME element object every time, not a fresh one per call.
  const readerEl = {
    _html: "",
    get innerHTML() {
      return this._html;
    },
    set innerHTML(v) {
      this._html = v;
    },
    querySelector: () => ({
      addEventListener: () => {},
      set textContent(_v) {},
    }),
    // render() sets loading/referrerPolicy on every content_html <img> --
    // no images in these fixtures, so an empty NodeList-like array is enough.
    querySelectorAll: () => [],
  };
  const replaceCalls = [];
  const markReadCalls = [];
  const sandbox = {
    ReadState: {
      isRead: () => false,
      markRead: (id) => markReadCalls.push(id),
    },
    SITE_NAME: "The Daily",
    location: {
      search: "",
      replace: (url) => replaceCalls.push(url),
    },
    document: {
      title: "",
      getElementById: (id) => (id === "reader" ? readerEl : null),
      // Used by stripTags() to get plain text from a content_html string.
      createElement: (tag) => {
        assert.equal(tag, "div");
        let html = "";
        return {
          set innerHTML(v) {
            html = v;
          },
          get textContent() {
            return html.replace(/<[^>]+>/g, "");
          },
        };
      },
    },
    console,
  };
  vm.createContext(sandbox);
  const src = readFile("reader.js").replace(
    /\nconst id = new URLSearchParams[\s\S]*$/,
    "\n"
  );
  vm.runInContext(src, sandbox, { filename: "reader.js" });
  sandbox._replaceCalls = replaceCalls;
  sandbox._markReadCalls = markReadCalls;
  return sandbox;
}

// ---------------------------------------------------------------------
// Item 2 (2026-09-22 request): a teaser with nothing to show beyond its own
// headline must never render a page whose only content is "read it on the
// original site" -- it should go straight there, marking the id read first.
// ---------------------------------------------------------------------

test("a teaser with no body and no summary redirects straight to the original link", () => {
  const sandbox = loadReaderSandbox();
  const article = {
    id: "a1",
    source: "Il Post",
    title: "Uno che correva fortissimo. E che oggi compie 50 anni.",
    summary: "",
    link: "https://x.ilpost.it/re?l=abc",
    image: null,
    date: new Date().toISOString(),
    reading_time_min: null,
    content_html: 'Uno che correva fortissimo. <a href="https://example.com/x">E che oggi compie 50 anni</a>.',
  };
  const reader = sandbox.document.getElementById("reader");
  sandbox.render(article);
  assert.deepEqual(sandbox._replaceCalls, ["https://x.ilpost.it/re?l=abc"]);
  assert.deepEqual(sandbox._markReadCalls, ["a1"], "read state must be recorded before redirecting away");
  assert.ok(
    !reader.innerHTML.includes("Full text isn"),
    "must never render the empty-state page when a redirect is possible"
  );
});

test("a teaser with no body, no summary and no link still shows the plain empty-state message", () => {
  // There's nowhere to send the reader, so this is the one case where
  // showing the page (rather than redirecting away from it) is correct.
  const sandbox = loadReaderSandbox();
  const article = {
    id: "a1",
    source: "Il Post",
    title: "Uno che correva fortissimo. E che oggi compie 50 anni.",
    summary: "",
    link: "",
    image: null,
    date: new Date().toISOString(),
    reading_time_min: null,
    content_html: 'Uno che correva fortissimo. <a href="https://example.com/x">E che oggi compie 50 anni</a>.',
  };
  const reader = sandbox.document.getElementById("reader");
  sandbox.render(article);
  assert.equal(sandbox._replaceCalls.length, 0);
  assert.ok(reader.innerHTML.includes("Full text isn"));
  assert.ok(
    !reader.innerHTML.includes('reader-body">Uno che correva fortissimo. <a'),
    "body must not just repeat the title sentence"
  );
});

test("a real, distinct body is shown normally", () => {
  const sandbox = loadReaderSandbox();
  const article = {
    id: "a2",
    source: "Reuters",
    title: "'Disaster' election in Germany",
    summary: "Hello, this is the briefing.",
    link: "https://example.com/a2",
    image: null,
    date: new Date().toISOString(),
    reading_time_min: 5,
    content_html: "<p>Hello. Germany's Merz fights for survival after a disaster election, with much more detail following in the rest of this real body paragraph.</p>",
  };
  const reader = sandbox.document.getElementById("reader");
  sandbox.render(article);
  assert.ok(reader.innerHTML.includes("Merz fights for survival"));
  assert.ok(!reader.innerHTML.includes("Full text isn"));
});

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);
