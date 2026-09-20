const CATEGORY_ORDER = ["Tech", "AI", "Business", "Politics", "Italia", "Other"];

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

function escapeHtml(s) {
  return (s || "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function articleRow(a) {
  return `<li><a href="article.html?id=${encodeURIComponent(a.id)}">
    <span class="title">${escapeHtml(a.title)}</span>
    <span class="meta">${escapeHtml(a.source)} &middot; ${relativeTime(a.date)}</span>
  </a></li>`;
}

function leadArticle(a) {
  const img = a.image
    ? `<img src="${escapeHtml(a.image)}" alt="" loading="lazy">`
    : "";
  return `<article class="lead">
    <a class="lead-link" href="article.html?id=${encodeURIComponent(a.id)}">
      ${img}
      <h3>${escapeHtml(a.title)}</h3>
      <div class="meta">${escapeHtml(a.source)} &middot; ${relativeTime(a.date)}</div>
    </a>
  </article>`;
}

function categorySection(name, articles) {
  const [lead, ...rest] = articles;
  const slug = name.toLowerCase().replace(/\s+/g, "-");
  return `<section class="category" id="cat-${slug}">
    <h2 class="category-title">${escapeHtml(name)}</h2>
    ${leadArticle(lead)}
    <ul class="list">${rest.map(articleRow).join("")}</ul>
  </section>`;
}

function render(data) {
  const feed = document.getElementById("feed");
  const pills = document.getElementById("pills");
  const articles = data.articles || [];

  if (articles.length === 0) {
    feed.innerHTML = '<p class="empty">No articles yet. Label a newsletter issue "news" in Gmail and run the fetch script.</p>';
    return;
  }

  const byCategory = {};
  for (const a of articles) {
    const cat = a.category || "Other";
    (byCategory[cat] ||= []).push(a);
  }

  const present = CATEGORY_ORDER.filter((c) => byCategory[c]?.length);
  for (const c of Object.keys(byCategory)) {
    if (!present.includes(c)) present.push(c);
  }

  pills.innerHTML = present
    .map((c, i) => `<a class="pill${i === 0 ? " active" : ""}" href="#cat-${c.toLowerCase().replace(/\s+/g, "-")}">${escapeHtml(c)}</a>`)
    .join("");

  feed.innerHTML = present.map((c) => categorySection(c, byCategory[c])).join("");
}

fetch("data/articles.json", { cache: "no-store" })
  .then((r) => r.json())
  .then(render)
  .catch((err) => {
    document.getElementById("feed").innerHTML = `<p class="empty">Couldn't load the feed (${escapeHtml(err.message)}).</p>`;
  });
