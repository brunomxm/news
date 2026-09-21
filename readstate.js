// Local-only "read" tracking, keyed by article id. No server, no accounts —
// just a set of ids in localStorage that survives refreshes/restarts.
const ReadState = (() => {
  const KEY = "bznews:read";
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

  return {
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
