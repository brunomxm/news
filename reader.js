function escapeHtml(s) {
  return (s || "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function timeOfDay(iso) {
  return new Date(iso).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
}

function dateLabel(iso) {
  return new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short" }).toUpperCase();
}

function dek(summary) {
  const s = (summary || "").trim();
  if (!s) return "";
  const sentences = s.match(/[^.!?]+[.!?]+/g);
  return sentences ? sentences.slice(0, 2).join(" ").trim() : s;
}

function stripTags(html) {
  const div = document.createElement("div");
  div.innerHTML = html || "";
  return (div.textContent || "").trim();
}

function normalizeForCompare(s) {
  return (s || "")
    .toLowerCase()
    .replace(/[.!?,;:'’"“”\-–—…]/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

function render(article) {
  const main = document.getElementById("reader");
  if (!article) {
    main.innerHTML = '<p class="empty">Article not found.</p>';
    return;
  }

  document.title = article.title ? `${article.title} — ${SITE_NAME}` : SITE_NAME;

  ReadState.markRead(article.id);

  // content_html already carries the newsletter's own title + summary text,
  // so a separately-extracted dek would just repeat it. Only show a dek when
  // there is no body content to fall back on.
  //
  // Some short teasers (e.g. Il Post's "Uno che correva fortissimo. E che
  // oggi compie 50 anni.") extract a content_html whose text is the title
  // sentence itself, with nothing else -- showing that back as the
  // "article" just repeats the headline. Treat that the same as no body.
  const bodyText = article.content_html ? stripTags(article.content_html) : "";
  const isBodyJustTheTitle =
    bodyText && normalizeForCompare(bodyText) === normalizeForCompare(article.title);
  const hasBody = Boolean(article.content_html) && !isBodyJustTheTitle;
  const summaryText = (article.summary || "").trim();
  // A summary that's empty, or identical to the title (which happens when
  // extraction couldn't find any body text beyond the headline sentence
  // itself), isn't real body content -- showing it back as the "article"
  // just duplicates the headline and reads as broken. Say plainly that
  // there's nothing more here instead, and point at the original link.
  const hasSummary = summaryText && summaryText.toLowerCase() !== article.title.trim().toLowerCase();
  // Some newsletters (e.g. Il Post's forwarded table templates, Reuters'
  // briefing) never include a genuine "read online" permalink -- every
  // other link in them points at a cited article or a tracking redirect,
  // not at the issue itself, so extraction leaves article.link empty
  // rather than guessing. Word the empty-body message accordingly, and
  // don't render a "Read the original" link that would send the reader
  // somewhere unrelated to what they just read.
  const hasLink = Boolean(article.link);
  // The feed itself links these straight to the original (see app.js's
  // cardLinkAttrs) so this page is normally skipped entirely, but a
  // teaser with nothing to show can still be reached directly -- a stale
  // bookmark, a shared link, a search hit. Don't show a page whose only
  // content is an instruction to leave it; just leave. markRead already
  // ran above, so the read state is recorded before the redirect fires.
  if (!hasBody && !hasSummary && hasLink) {
    location.replace(article.link);
    return;
  }
  const d = hasBody ? "" : (hasSummary ? dek(article.summary) : "");
  const body = hasBody
    ? article.content_html
    : hasSummary
      ? `<p>${escapeHtml(article.summary)}</p>`
      : hasLink
        ? `<p class="reader-empty">Full text isn’t available here — read it on the original site below.</p>`
        : `<p class="reader-empty">Full text isn’t available here.</p>`;
  const hero = article.image
    ? `<div class="reader-hero"><img src="${escapeHtml(article.image)}" alt=""></div>`
    : "";
  const bylineParts = [dateLabel(article.date), timeOfDay(article.date)];
  if (article.reading_time_min) bylineParts.push(`${article.reading_time_min} MIN READ`);
  const original = hasLink
    ? `<a class="reader-original" href="${escapeHtml(article.link)}" target="_blank" rel="noopener noreferrer">Read the original at ${escapeHtml(article.source)} &rarr;</a>`
    : "";

  main.innerHTML = `
    <a class="reader-back-top label-nav" href="index.html">&lsaquo; Back to ${escapeHtml(SITE_NAME)}</a>
    <span class="label-source reader-source">${escapeHtml(article.source)}</span>
    <h1 class="reader-headline">${escapeHtml(article.title)}</h1>
    ${d ? `<p class="reader-dek">${escapeHtml(d)}</p>` : ""}
    <p class="label-nav reader-byline">${bylineParts.join(" &middot; ")}</p>
    ${hero}
    <div class="reader-body">${body}</div>
    ${original}
    <a class="reader-mark-unread label-nav" href="#" data-id="${escapeHtml(article.id)}" style="display:block;margin-top:16px;"></a>
  `;

  const markLink = main.querySelector(".reader-mark-unread");
  const updateMarkLink = () => {
    markLink.textContent = ReadState.isRead(article.id) ? "Mark as unread" : "Mark as read";
  };
  updateMarkLink();
  markLink.addEventListener("click", (e) => {
    e.preventDefault();
    ReadState.toggle(article.id);
    updateMarkLink();
  });
}

const id = new URLSearchParams(location.search).get("id");

Promise.all([
  fetch("data/articles.json", { cache: "no-store" }).then((r) => r.json()),
  ReadState.ready,
])
  .then(([data]) => {
    const article = (data.articles || []).find((a) => a.id === id);
    render(article);
  })
  .catch((err) => {
    document.getElementById("reader").innerHTML = `<p class="empty">Couldn't load the article (${escapeHtml(err.message)}).</p>`;
  });
