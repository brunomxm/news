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

function loadAppSandbox() {
  const sandbox = {
    ReadState: { isRead: () => false },
    console,
  };
  vm.createContext(sandbox);
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
  const html = sandbox.renderSection("Culture", items);

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
  const html = sandbox.renderSection("Culture", items);
  assert.ok(html.includes(">29<"), 'section header should show the count "29"');
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
  };
  const sandbox = {
    ReadState: { isRead: () => false, markRead: () => {} },
    SITE_NAME: "The Daily",
    location: { search: "" },
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
  return sandbox;
}

test("a body that's just the title sentence falls back to the empty-state message", () => {
  const sandbox = loadReaderSandbox();
  const article = {
    id: "a1",
    source: "Il Post",
    title: "Uno che correva fortissimo. E che oggi compie 50 anni .",
    summary: "",
    link: "https://example.com/a1",
    image: null,
    date: new Date().toISOString(),
    reading_time_min: null,
    content_html: 'Uno che correva fortissimo. <a href="https://example.com/x">E che oggi compie 50 anni</a> .',
  };
  const reader = sandbox.document.getElementById("reader");
  sandbox.render(article);
  assert.ok(
    reader.innerHTML.includes("Full text isn"),
    "expected the empty-state fallback message, got the title repeated as body"
  );
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
