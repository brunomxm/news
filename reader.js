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
  if (days < 7) return `${days}d ago`;
  return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function render(article) {
  const main = document.getElementById("reader");
  if (!article) {
    main.innerHTML = '<p class="empty">Article not found.</p>';
    return;
  }

  document.title = article.title ? `${article.title} — News` : "News";

  const body = article.content_html
    ? article.content_html
    : `<p>${escapeHtml(article.summary || "")}</p>`;

  main.innerHTML = `
    <article class="reader-article">
      <p class="meta">${escapeHtml(article.source)} &middot; ${relativeTime(article.date)}${article.category ? ` &middot; ${escapeHtml(article.category)}` : ""}</p>
      <div class="reader-content">${body}</div>
      <a class="reader-fallback" href="${escapeHtml(article.link)}" target="_blank" rel="noopener noreferrer">Read the original &#8599;</a>
    </article>
  `;
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
