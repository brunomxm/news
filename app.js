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

function readGlyph(a, isRead) {
  if (!isRead) return "";
  return ` <span class="read-mark" data-read-toggle="${escapeHtml(a.id)}" role="button" tabindex="0" aria-label="Mark as unread" title="Mark as unread">&#10003;</span>`;
}

function heroBlock(a) {
  const isRead = ReadState.isRead(a.id);
  const media = a.image
    ? `<div class="hero-media"><img src="${escapeHtml(a.image)}" alt="" loading="lazy"></div>`
    : `<div class="hero-media no-image"></div>`;
  const d = dek(a);
  return `<article class="hero${isRead ? " is-read" : ""}" data-section="${escapeHtml(a.section)}">
    <a href="${href(a)}">
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
    <a href="${href(a)}">
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
    <a href="${href(a)}">
      ${media}
      <span class="label-source">${escapeHtml(a.source)}</span>
      <h2 class="feature-headline">${escapeHtml(a.title)}</h2>
      <p class="label-meta">${metaLine(a, { readingTime: true })}${readGlyph(a, isRead)}</p>
    </a>
  </article>`;
}

function compactRow(a) {
  const isRead = ReadState.isRead(a.id);
  return `<li class="${isRead ? "is-read" : ""}"><a href="${href(a)}">
    <span class="compact-headline">${escapeHtml(a.title)}</span>
    <span class="label-meta compact-meta">${metaLine(a)}${readGlyph(a, isRead)}</span>
  </a></li>`;
}

function radarRow(a) {
  const isRead = ReadState.isRead(a.id);
  return `<li class="${isRead ? "is-read" : ""}"><a href="${href(a)}">
    <span class="label-meta radar-meta">${escapeHtml(a.source)} &middot; ${timeOfDay(a.date)}${readGlyph(a, isRead)}</span>
    <span class="radar-headline">${escapeHtml(a.title)}</span>
  </a></li>`;
}

function longreadBand(a) {
  const isRead = ReadState.isRead(a.id);
  const d = dek(a);
  return `<section class="longread-band${isRead ? " is-read" : ""}" data-section="${escapeHtml(a.section)}">
    <a href="${href(a)}">
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

function renderSection(name, items) {
  if (!items.length) return "";
  let body = "";
  if (name === "Latest") {
    body = `<ul class="radar-list">${items.map(radarRow).join("")}</ul>`;
  } else if (name === "World & Ideas") {
    const [lead, ...rest] = items;
    body = `${secondaryBlock(lead)}<ul class="compact-list">${rest.map(compactRow).join("")}</ul>`;
  } else if (name === "Technology & AI") {
    body = `<ul class="compact-list compact-grid-3">${items.map(compactRow).join("")}</ul>`;
  } else if (name === "Culture") {
    const [lead, ...rest] = items.slice(0, 4);
    body = `${featureBlock(lead)}<ul class="compact-list">${rest.map(compactRow).join("")}</ul>`;
  } else {
    body = `<ul class="compact-list">${items.map(compactRow).join("")}</ul>`;
  }
  return `<section class="section" data-section="${escapeHtml(name)}">
    ${sectionHeader(name, items.length)}
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

  const remaining = articles.slice();
  const hero = remaining.shift();

  let longread = null;
  const longreadIdx = remaining.findIndex((a) => a.word_count >= LONGREAD_WORD_THRESHOLD);
  if (longreadIdx !== -1) {
    longread = remaining.splice(longreadIdx, 1)[0];
  }

  const bySection = {};
  for (const a of remaining) {
    (bySection[a.section] ||= []).push(a);
  }

  let html = heroBlock(hero);
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

function setupReadToggle() {
  const feed = document.getElementById("feed");

  function handle(target) {
    const mark = target.closest(".read-mark");
    if (!mark) return false;
    const id = mark.dataset.readToggle;
    ReadState.markUnread(id);
    const card = mark.closest("article, li, .longread-band");
    if (card) card.classList.remove("is-read");
    mark.remove();
    return true;
  }

  feed.addEventListener("click", (e) => {
    if (handle(e.target)) e.preventDefault();
  });
  feed.addEventListener("keydown", (e) => {
    if (e.key !== "Enter" && e.key !== " ") return;
    if (handle(e.target)) e.preventDefault();
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

fetch("data/articles.json", { cache: "no-store" })
  .then((r) => r.json())
  .then(render)
  .catch((err) => {
    document.getElementById("feed").innerHTML = `<p class="empty">Couldn't load the feed (${escapeHtml(err.message)}).</p>`;
  });
