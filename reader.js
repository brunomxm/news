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
  const hasBody = Boolean(article.content_html);
  const d = hasBody ? "" : dek(article.summary);
  const body = hasBody
    ? article.content_html
    : `<p>${escapeHtml(article.summary || article.title)}</p>`;
  const hero = article.image
    ? `<div class="reader-hero"><img src="${escapeHtml(article.image)}" alt=""></div>`
    : "";
  const bylineParts = [dateLabel(article.date), timeOfDay(article.date)];
  if (article.reading_time_min) bylineParts.push(`${article.reading_time_min} MIN READ`);

  main.innerHTML = `
    <span class="label-source reader-source">${escapeHtml(article.source)}</span>
    <h1 class="reader-headline">${escapeHtml(article.title)}</h1>
    ${d ? `<p class="reader-dek">${escapeHtml(d)}</p>` : ""}
    <p class="label-nav reader-byline">${bylineParts.join(" &middot; ")}</p>
    ${hero}
    <div class="reader-body">${body}</div>
    <a class="reader-original" href="${escapeHtml(article.link)}" target="_blank" rel="noopener noreferrer">Read the original at ${escapeHtml(article.source)} &rarr;</a>
    <a class="reader-mark-unread label-nav" href="#" data-id="${escapeHtml(article.id)}" style="display:block;margin-top:16px;"></a>
    <a class="reader-back label-nav" href="index.html" style="display:block;margin-top:16px;">&lsaquo; Back to ${escapeHtml(SITE_NAME)}</a>
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

fetch("data/articles.json", { cache: "no-store" })
  .then((r) => r.json())
  .then((data) => {
    const article = (data.articles || []).find((a) => a.id === id);
    render(article);
  })
  .catch((err) => {
    document.getElementById("reader").innerHTML = `<p class="empty">Couldn't load the article (${escapeHtml(err.message)}).</p>`;
  });
