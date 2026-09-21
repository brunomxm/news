// Local-only "read" tracking, keyed by article id. No server, no accounts —
// just a set of ids in localStorage that survives refreshes/restarts.
//
// Article ids are a hash of message+link data, so changing how they're
// computed (as happened on 2026-09-21, dropping the extraction-order index
// from the hash) orphans every id already stored in a reader's
// localStorage — none of it matches the newly-generated feed, so all read
// state silently vanishes. MIGRATIONS below lists one-time id remaps for
// past changes like that: each entry points at a small static JSON file
// (old id -> new id, for every link present in both the pre- and
// post-change feed) generated once from the two feed snapshots and
// applied at most once per migration, tracked by its own localStorage flag
// so re-running it is a no-op.
const MIGRATIONS = ["data/id_migration_2026-09-21.json"];

const ReadState = (() => {
  const KEY = "bznews:read";
  const MIGRATED_KEY = "bznews:read:migrated";
  let cache = null;

  function load() {
    if (cache) return cache;
    try {
      cache = new Set(JSON.parse(localStorage.getItem(KEY) || "[]"));
    } catch {
      cache = new Set();
    }
    return cache;
  }

  function save() {
    try {
      localStorage.setItem(KEY, JSON.stringify([...load()]));
    } catch {
      // localStorage unavailable (private mode, quota, etc.) — fail silently.
    }
  }

  function alreadyMigrated(path) {
    try {
      return new Set(JSON.parse(localStorage.getItem(MIGRATED_KEY) || "[]")).has(path);
    } catch {
      return false;
    }
  }

  function markMigrated(path) {
    try {
      const done = new Set(JSON.parse(localStorage.getItem(MIGRATED_KEY) || "[]"));
      done.add(path);
      localStorage.setItem(MIGRATED_KEY, JSON.stringify([...done]));
    } catch {
      // Best-effort — if this fails, the migration just runs again next
      // load, which is harmless (mapping an id that's already been
      // remapped is a no-op).
    }
  }

  async function runMigration(path) {
    if (alreadyMigrated(path)) return;
    let mapping;
    try {
      mapping = await fetch(path).then((r) => (r.ok ? r.json() : null));
    } catch {
      mapping = null;
    }
    if (mapping && typeof mapping === "object" && !Array.isArray(mapping)) {
      const s = load();
      let changed = false;
      for (const [oldId, newId] of Object.entries(mapping)) {
        if (s.has(oldId)) {
          s.delete(oldId);
          s.add(newId);
          changed = true;
        }
      }
      if (changed) save();
      markMigrated(path);
    }
    // A failed fetch must be retried on the next page load, or the reader's
    // existing read history would remain orphaned permanently.
  }

  const ready = MIGRATIONS.reduce(
    (p, path) => p.then(() => runMigration(path)),
    Promise.resolve()
  );

  return {
    ready,
    isRead(id) {
      return load().has(id);
    },
    markRead(id) {
      const s = load();
      if (!s.has(id)) {
        s.add(id);
        save();
      }
    },
    markUnread(id) {
      const s = load();
      if (s.has(id)) {
        s.delete(id);
        save();
      }
    },
    toggle(id) {
      this.isRead(id) ? this.markUnread(id) : this.markRead(id);
    },
  };
})();
