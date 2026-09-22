const SECTION_ORDER = ["Latest", "World & Ideas", "Technology & AI", "Culture", "Music & Industry"];
const LONGREAD_WORD_THRESHOLD = 300;

function escapeHtml(s) {
  return (s || "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function relativeTime(iso) {
  const diffMs = Date.now() - new Date(iso).getTime();
  const mins = Math.round(diffMs / 60000);
  if (mins < 60) return `${Math.max(mins, 1)}m ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  return `${days}d ago`;
}

function timeOfDay(iso) {
  return new Date(iso).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
}

function dek(article) {
  const s = (article.summary || "").trim();
  if (!s) return "";
  const sentences = s.match(/[^.!?]+[.!?]+/g);
  return sentences ? sentences.slice(0, 2).join(" ").trim() : s;
}

function metaLine(a, opts = {}) {
  const parts = [escapeHtml(a.source), relativeTime(a.date)];
  if (opts.readingTime && a.reading_time_min) parts.push(`${a.reading_time_min} min`);
  return parts.join(" &middot; ");
}

function href(a) {
  return `article.html?id=${encodeURIComponent(a.id)}`;
}

function stripTags(htmlStr) {
  const div = document.createElement("div");
  div.innerHTML = htmlStr || "";
  return (div.textContent || "").trim();
}

function normalizeForCompare(s) {
  return (s || "")
    .toLowerCase()
    .replace(/[.!?,;:'’"“”\-–—…]/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

// Mirrors reader.js's own hasBody/hasSummary checks -- true only when there's
// actually something to show beyond the headline itself (a real body, or a
// summary that isn't just the title again).
function hasReadableContent(a) {
  const bodyText = a.content_html ? stripTags(a.content_html) : "";
  const isBodyJustTheTitle = bodyText && normalizeForCompare(bodyText) === normalizeForCompare(a.title);
  const hasBody = Boolean(a.content_html) && !isBodyJustTheTitle;
  const summaryText = (a.summary || "").trim();
  const hasSummary = summaryText && summaryText.toLowerCase() !== a.title.trim().toLowerCase();
  return hasBody || hasSummary;
}

// A teaser with nothing to show beyond its own headline (many of Il Post's
// digest items: one sentence, one link, no further body) used to still open
// our own article.html, which then had nothing to render but "read it on the
// original site below" -- a page whose only content is an instruction to
// leave it. Send the reader straight to the original instead; mark-as-read
// is wired up separately since this never visits reader.js to do it.
function cardLinkAttrs(a) {
  if (!hasReadableContent(a) && a.link) {
    return `href="${escapeHtml(a.link)}" target="_blank" rel="noopener noreferrer" data-mark-read="${escapeHtml(a.id)}"`;
  }
  return `href="${href(a)}"`;
}

function readGlyph(a, isRead) {
  if (!isRead) return "";
  return ` <span class="read-mark" data-read-toggle="${escapeHtml(a.id)}" role="button" tabindex="0" aria-label="Mark as unread" title="Mark as unread">&#10003;</span>`;
}

// Group the flat, already date-sorted article list into render units: a
// roundup email (several articles sharing one issue_id) becomes ONE "issue"
// unit that renders as a single collapsible row instead of N separate cards;
// a single-essay email (Understanding AI, Da Costa a Costa, ...) stays an
// ordinary "article" unit -- explicitly not wrapped in a one-item accordion.
// Grouping is driven entirely by issue_id (set during extraction from the
// Gmail message id), never by sender name, so this doesn't need updating
// when a new roundup-style sender is added.
//
// Built with one pass that assigns each issue to the position of its FIRST
// story in the (date-sorted) article list, rather than assuming stories of
// one issue sit contiguously -- correct today because extraction appends one
// message's articles together and the later sort is stable, but not an
// invariant worth depending on silently.
function groupIntoUnits(articles) {
  const byIssue = new Map();
  for (const a of articles) {
    if (!byIssue.has(a.issue_id)) byIssue.set(a.issue_id, []);
    byIssue.get(a.issue_id).push(a);
  }
  const seen = new Set();
  const units = [];
  for (const a of articles) {
    if (seen.has(a.issue_id)) continue;
    seen.add(a.issue_id);
    const stories = byIssue.get(a.issue_id);
    if (stories.length > 1) {
      units.push({
        kind: "issue",
        issueId: a.issue_id,
        stories,
        section: a.section,
        date: a.date,
        source: a.source,
        subject: a.subject,
      });
    } else {
      units.push({ kind: "article", article: a, section: a.section, date: a.date });
    }
  }
  return units;
}

// Removes and returns the first "article"-kind unit matching `predicate`
// (default: any), leaving issue units untouched in the returned `rest` --
// used everywhere a single big treatment (hero, longread band, a section's
// featured lead) needs one ordinary article and must never pick an issue
// row for it. Returns { picked: null, rest: units } if nothing matches.
function pickFirstArticleUnit(units, predicate = () => true) {
  const rest = units.slice();
  const idx = rest.findIndex((u) => u.kind === "article" && predicate(u.article));
  if (idx === -1) return { picked: null, rest: units };
  const picked = rest.splice(idx, 1)[0].article;
  return { picked, rest };
}

function renderUnit(unit, rowFn) {
  return unit.kind === "issue" ? issueRow(unit) : rowFn(unit.article);
}

// A roundup issue's own subject line ("Evening Post, le storie di oggi",
// "TLDR AI") is usually too generic on its own to tell the reader what's
// actually inside -- source and time alone aren't enough context either, so
// the collapsed row leads with the first couple of stories' own headlines,
// which is what a reader actually scans a roundup for.
function issuePreview(unit) {
  return unit.stories.slice(0, 2).map((a) => a.title).join(" · ");
}

// Plain "·" rather than the "&middot;" entity used elsewhere in this file:
// this string is later reassigned wholesale via textContent (see
// refreshIssueProgress), which never decodes HTML entities.
function issueMetaBase(unit) {
  return `${timeOfDay(unit.date)} · ${unit.stories.length} stories`;
}

function issueRow(unit) {
  const total = unit.stories.length;
  const readCount = unit.stories.filter((a) => ReadState.isRead(a.id)).length;
  const allRead = readCount === total;
  const toggleId = `issue-${unit.issueId}-toggle`;
  const panelId = `issue-${unit.issueId}-stories`;
  const metaBase = issueMetaBase(unit);
  const progress = readCount > 0 ? ` · ${readCount}/${total} read` : "";
  const label = `${unit.source}, ${timeOfDay(unit.date)}, ${total} stories${
    readCount > 0 ? `, ${readCount} of ${total} read` : ""
  }: ${issuePreview(unit)}`;
  return `<li class="issue-row${allRead ? " is-read" : ""}" data-issue="${escapeHtml(unit.issueId)}">
    <button type="button" class="issue-toggle" id="${toggleId}" aria-expanded="false" aria-controls="${panelId}" aria-label="${escapeHtml(label)}">
      <span class="label-source">${escapeHtml(unit.source)}</span>
      <span class="label-meta issue-meta" data-base="${escapeHtml(metaBase)}">${metaBase}${progress}</span>
      <span class="issue-headline" aria-hidden="true">${escapeHtml(issuePreview(unit))}<span class="issue-chevron">&#9662;</span></span>
    </button>
    <ul class="compact-list issue-stories" id="${panelId}" role="group" aria-labelledby="${toggleId}" hidden>
      ${unit.stories.map((a) => compactRow(a)).join("")}
    </ul>
  </li>`;
}

function heroBlock(a) {
  const isRead = ReadState.isRead(a.id);
  const media = a.image
    ? `<div class="hero-media"><img src="${escapeHtml(a.image)}" alt="" loading="lazy"></div>`
    : `<div class="hero-media no-image"></div>`;
  const d = dek(a);
  return `<article class="hero${isRead ? " is-read" : ""}" data-section="${escapeHtml(a.section)}">
    <a ${cardLinkAttrs(a)}>
      ${media}
      <span class="label-source">${escapeHtml(a.source)}</span>
      <h1 class="hero-headline">${escapeHtml(a.title)}</h1>
      ${d ? `<p class="hero-dek">${escapeHtml(d)}</p>` : ""}
      <p class="label-meta">${metaLine(a, { readingTime: true })}${readGlyph(a, isRead)}</p>
    </a>
  </article>`;
}

function secondaryBlock(a) {
  const isRead = ReadState.isRead(a.id);
  const thumb = a.image
    ? `<div class="secondary-thumb"><img src="${escapeHtml(a.image)}" alt="" loading="lazy"></div>`
    : "";
  return `<article class="secondary${isRead ? " is-read" : ""}">
    <a ${cardLinkAttrs(a)}>
      ${thumb}
      <span class="label-source">${escapeHtml(a.source)}</span>
      <h2 class="secondary-headline">${escapeHtml(a.title)}</h2>
      <p class="label-meta">${metaLine(a, { readingTime: true })}${readGlyph(a, isRead)}</p>
    </a>
  </article>`;
}

function featureBlock(a) {
  const isRead = ReadState.isRead(a.id);
  const media = a.image
    ? `<div class="feature-media"><img src="${escapeHtml(a.image)}" alt="" loading="lazy"></div>`
    : "";
  return `<article class="feature${isRead ? " is-read" : ""}">
    <a ${cardLinkAttrs(a)}>
      ${media}
      <span class="label-source">${escapeHtml(a.source)}</span>
      <h2 class="feature-headline">${escapeHtml(a.title)}</h2>
      <p class="label-meta">${metaLine(a, { readingTime: true })}${readGlyph(a, isRead)}</p>
    </a>
  </article>`;
}

function compactRow(a) {
  const isRead = ReadState.isRead(a.id);
  return `<li class="${isRead ? "is-read" : ""}"><a ${cardLinkAttrs(a)}>
    <span class="compact-headline">${escapeHtml(a.title)}</span>
    <span class="label-meta compact-meta">${metaLine(a)}${readGlyph(a, isRead)}</span>
  </a></li>`;
}

function radarRow(a) {
  const isRead = ReadState.isRead(a.id);
  return `<li class="${isRead ? "is-read" : ""}"><a ${cardLinkAttrs(a)}>
    <span class="label-meta radar-meta">${escapeHtml(a.source)} &middot; ${timeOfDay(a.date)}${readGlyph(a, isRead)}</span>
    <span class="radar-headline">${escapeHtml(a.title)}</span>
  </a></li>`;
}

function longreadBand(a) {
  const isRead = ReadState.isRead(a.id);
  const d = dek(a);
  return `<section class="longread-band${isRead ? " is-read" : ""}" data-section="${escapeHtml(a.section)}">
    <a ${cardLinkAttrs(a)}>
      <span class="longread-tag">Long read</span>
      <span class="label-source"> &middot; ${escapeHtml(a.source)}</span>
      <h2 class="longread-headline">${escapeHtml(a.title)}</h2>
      ${d ? `<p class="longread-dek">${escapeHtml(d)}</p>` : ""}
      <p class="label-meta">${a.reading_time_min ? `${a.reading_time_min} min read` : ""}${readGlyph(a, isRead)}</p>
    </a>
  </section>`;
}

function sectionHeader(name, count) {
  const slug = name.toLowerCase().replace(/[^a-z]+/g, "-");
  return `<div class="section-header" id="sec-${slug}">
    <h2 class="label-section section-title">${escapeHtml(name)}</h2>
    <span class="label-meta section-count">${count}</span>
  </div>`;
}

// A section header's count is a count of *stories*, not of rendered rows --
// collapsing a 14-story roundup into one row shouldn't make the section
// header (or the masthead total) look like content vanished.
function unitStoryCount(units) {
  return units.reduce((n, u) => n + (u.kind === "issue" ? u.stories.length : 1), 0);
}

function renderSection(name, units) {
  if (!units.length) return "";
  let body = "";
  if (name === "Latest") {
    body = `<ul class="radar-list">${units.map((u) => renderUnit(u, radarRow)).join("")}</ul>`;
  } else if (name === "World & Ideas") {
    const { picked: lead, rest } = pickFirstArticleUnit(units);
    const leadHtml = lead ? secondaryBlock(lead) : "";
    body = `${leadHtml}<ul class="compact-list">${rest.map((u) => renderUnit(u, compactRow)).join("")}</ul>`;
  } else if (name === "Technology & AI") {
    body = `<ul class="compact-list compact-grid-3">${units.map((u) => renderUnit(u, compactRow)).join("")}</ul>`;
  } else if (name === "Culture") {
    // Feature block for the lead item, then every remaining item in the
    // section as a compact row -- slice(0, 4) used to cap this at 4 total,
    // hiding the rest of the section even though the header's count (and
    // the masthead's total) already counted them all.
    const { picked: lead, rest } = pickFirstArticleUnit(units);
    const leadHtml = lead ? featureBlock(lead) : "";
    body = `${leadHtml}<ul class="compact-list">${rest.map((u) => renderUnit(u, compactRow)).join("")}</ul>`;
  } else {
    body = `<ul class="compact-list">${units.map((u) => renderUnit(u, compactRow)).join("")}</ul>`;
  }
  return `<section class="section" data-section="${escapeHtml(name)}">
    ${sectionHeader(name, unitStoryCount(units))}
    <div class="section-body">${body}</div>
  </section>`;
}

function renderMasthead(articles, generatedAt) {
  document.getElementById("wordmark").textContent = SITE_NAME;
  document.getElementById("sticky-wordmark").textContent = SITE_NAME;
  const now = generatedAt ? new Date(generatedAt) : new Date();
  const dateStr = now.toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long" }).toUpperCase();
  document.getElementById("masthead-date").textContent = dateStr;
  document.getElementById("masthead-count").textContent = `${articles.length} ITEMS · ${timeOfDay(now.toISOString())}`;

  const present = SECTION_ORDER.filter((s) => articles.some((a) => a.section === s));
  document.getElementById("jumpline").innerHTML = present
    .map((s) => `<a class="label-nav" data-section="${escapeHtml(s)}" href="#sec-${s.toLowerCase().replace(/[^a-z]+/g, "-")}">${escapeHtml(s)}</a>`)
    .join("");
}

function render(data) {
  const feed = document.getElementById("feed");
  const articles = data.articles || [];

  if (articles.length === 0) {
    feed.innerHTML = '<p class="empty">No articles yet. Label a newsletter issue "news" in Gmail and run the fetch script.</p>';
    document.getElementById("endmark").innerHTML = "";
    return;
  }

  renderMasthead(articles, data.generated_at);

  // A roundup issue is never the hero or the long-read band -- those are
  // single-story treatments (one image, one dek) that don't fit a
  // multi-story collapsed row, so both picks skip issue units and leave
  // them in place to be rendered as a normal row within their own section.
  const units = groupIntoUnits(articles);
  const { picked: hero, rest: afterHero } = pickFirstArticleUnit(units);

  let longread = null;
  let remaining = afterHero;
  const { picked: longreadPick, rest: afterLongread } = pickFirstArticleUnit(
    afterHero,
    (a) => a.word_count >= LONGREAD_WORD_THRESHOLD
  );
  if (longreadPick) {
    longread = longreadPick;
    remaining = afterLongread;
  }

  const bySection = {};
  for (const u of remaining) {
    (bySection[u.section] ||= []).push(u);
  }

  let html = hero ? heroBlock(hero) : "";
  for (const name of SECTION_ORDER) {
    html += renderSection(name, bySection[name] || []);
    if (name === "Technology & AI" && longread) {
      html += longreadBand(longread);
    }
  }
  feed.innerHTML = html;

  const endmark = document.getElementById("endmark");
  endmark.innerHTML = `<span>That is everything. ${articles.length} items. Next at 07:00.</span><span class="end-dot">&#9632;</span>`;

  setupScroll();
  setupReadToggle();
  setupIssueToggle();
  setupSectionFilter();
}

function setupSectionFilter() {
  const jumpline = document.getElementById("jumpline");
  let activeFilter = null;

  function applyFilter(name) {
    document.querySelectorAll("[data-section]").forEach((el) => {
      el.classList.toggle("section-hidden", Boolean(name) && el.dataset.section !== name);
    });
    jumpline.querySelectorAll("a").forEach((a) => {
      a.classList.toggle("active", a.dataset.section === name);
    });
  }

  jumpline.addEventListener("click", (e) => {
    const link = e.target.closest("a[data-section]");
    if (!link) return;
    e.preventDefault();
    activeFilter = activeFilter === link.dataset.section ? null : link.dataset.section;
    applyFilter(activeFilter);
    window.scrollTo({ top: 0, behavior: "instant" });
  });
}

// If `card` is one story inside a collapsed issue's panel, recompute that
// issue's own aggregate progress display and its dimmed/read state from the
// stories now marked read in the DOM -- this is display-only bookkeeping:
// it never calls ReadState itself and never adds a way to mark the whole
// issue read or unread in one action.
function refreshIssueProgress(card) {
  const issueLi = card.closest(".issue-row");
  if (!issueLi) return;
  const panel = issueLi.querySelector(".issue-stories");
  const metaEl = issueLi.querySelector(".issue-meta");
  if (!panel || !metaEl) return;
  const total = panel.children.length;
  const readCount = panel.querySelectorAll(":scope > .is-read").length;
  issueLi.classList.toggle("is-read", total > 0 && readCount === total);
  const base = metaEl.dataset.base || metaEl.textContent;
  metaEl.textContent = base + (readCount > 0 ? ` · ${readCount}/${total} read` : "");
}

function setupReadToggle() {
  const feed = document.getElementById("feed");

  function handle(target) {
    const mark = target.closest(".read-mark");
    if (!mark) return false;
    const id = mark.dataset.readToggle;
    ReadState.markUnread(id);
    const card = mark.closest("article, li, .longread-band");
    if (card) {
      card.classList.remove("is-read");
      refreshIssueProgress(card);
    }
    mark.remove();
    return true;
  }

  // A card whose link goes straight to the original (see cardLinkAttrs)
  // never visits reader.js, so nothing else marks it read -- do it here,
  // on the same click that opens the new tab, and update this card in
  // place so the feed doesn't need a reload to show it as read.
  function handleExternal(target) {
    const link = target.closest("a[data-mark-read]");
    if (!link) return false;
    const id = link.dataset.markRead;
    if (ReadState.isRead(id)) return false;
    ReadState.markRead(id);
    const card = link.closest("article, li, .longread-band");
    if (card && !card.classList.contains("is-read")) {
      card.classList.add("is-read");
      const meta = card.querySelector(".label-meta");
      if (meta && !meta.querySelector(".read-mark")) {
        meta.insertAdjacentHTML("beforeend", readGlyph({ id }, true));
      }
      refreshIssueProgress(card);
    }
    return true;
  }

  feed.addEventListener("click", (e) => {
    if (handle(e.target)) {
      e.preventDefault();
      return;
    }
    handleExternal(e.target);
  });
  feed.addEventListener("keydown", (e) => {
    if (e.key !== "Enter" && e.key !== " ") return;
    if (handle(e.target)) e.preventDefault();
  });
}

// Disclosure widget for a collapsed issue row: a real <button> so Enter/
// Space activation and focus come for free, toggling `hidden` on the
// stories panel and its own aria-expanded -- the WAI-ARIA APG "disclosure"
// pattern. Never touches ReadState: expanding/collapsing must not mark
// anything read.
function setupIssueToggle() {
  const feed = document.getElementById("feed");
  feed.addEventListener("click", (e) => {
    const toggle = e.target.closest(".issue-toggle");
    if (!toggle) return;
    const panel = document.getElementById(toggle.getAttribute("aria-controls"));
    const expanded = toggle.getAttribute("aria-expanded") === "true";
    toggle.setAttribute("aria-expanded", String(!expanded));
    if (panel) panel.hidden = expanded;
  });
}

function setupScroll() {
  const bar = document.getElementById("sticky-bar");
  const stickySection = document.getElementById("sticky-section");
  const headers = Array.from(document.querySelectorAll(".section-header"));

  window.addEventListener("scroll", () => {
    bar.classList.toggle("visible", window.scrollY > 420);
  }, { passive: true });

  if (headers.length) {
    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            const title = entry.target.querySelector(".section-title").textContent;
            stickySection.textContent = title;
          }
        });
      },
      { rootMargin: "-44px 0px -80% 0px" }
    );
    headers.forEach((h) => observer.observe(h));
  }
}

Promise.all([
  fetch("data/articles.json", { cache: "no-store" }).then((r) => r.json()),
  ReadState.ready,
])
  .then(([data]) => render(data))
  .catch((err) => {
    document.getElementById("feed").innerHTML = `<p class="empty">Couldn't load the feed (${escapeHtml(err.message)}).</p>`;
  });
