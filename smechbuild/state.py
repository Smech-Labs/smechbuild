"""wanna-build equivalent: per-source state in sqlite, with history.

States: needs-build, building, built, failed, dep-wait, blocked.
Several builder processes may share one DB file; `claim` is atomic.
"""

import sqlite3
import time

STATES = ("needs-build", "building", "built", "failed", "dep-wait", "blocked")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
  name TEXT PRIMARY KEY,
  version TEXT NOT NULL,
  state TEXT NOT NULL,
  reason TEXT NOT NULL DEFAULT '',
  builder TEXT,
  digest TEXT NOT NULL DEFAULT '',
  attempts INTEGER NOT NULL DEFAULT 0,
  since REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS history (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  at REAL NOT NULL,
  old TEXT,
  new TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT ''
);
"""


class State:
    def __init__(self, path: str):
        self.db = sqlite3.connect(path, timeout=30, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(_SCHEMA)

    def close(self):
        self.db.close()

    # -- helpers ---------------------------------------------------------
    def _set(self, name, new, note="", builder=None, **cols):
        row = self.db.execute("SELECT state FROM sources WHERE name=?", (name,)).fetchone()
        old = row["state"] if row else None
        now = time.time()
        sets = {"state": new, "reason": note, "builder": builder, "since": now, **cols}
        q = ", ".join(f"{k}=?" for k in sets)
        self.db.execute(f"UPDATE sources SET {q} WHERE name=?", (*sets.values(), name))
        if old != new:
            self.db.execute(
                "INSERT INTO history(name, at, old, new, note) VALUES (?,?,?,?,?)",
                (name, now, old, new, note),
            )

    def get(self, name):
        r = self.db.execute("SELECT * FROM sources WHERE name=?", (name,)).fetchone()
        return dict(r) if r else None

    def all(self):
        return [dict(r) for r in self.db.execute("SELECT * FROM sources ORDER BY name")]

    def history(self, name):
        return [dict(r) for r in self.db.execute("SELECT * FROM history WHERE name=? ORDER BY id", (name,))]

    # -- planning --------------------------------------------------------
    def sync(self, recipes, verdicts, digests):
        """Apply a graph.plan() result. Rules:
        * a source being built is left alone;
        * a failed source stays failed until its recipe digest changes;
        * everything else follows the verdict.
        """
        self.db.execute("BEGIN IMMEDIATE")
        try:
            for name, r in recipes.items():
                v = verdicts[name]
                row = self.db.execute("SELECT * FROM sources WHERE name=?", (name,)).fetchone()
                note = "; ".join(v.reasons)
                want = {"up-to-date": "built"}.get(v.state, v.state)
                if row is None:
                    self.db.execute(
                        "INSERT INTO sources(name, version, state, since, digest) VALUES (?,?,?,?,?)",
                        (name, r.full_version, "new", time.time(), digests[name]),
                    )
                    row = self.db.execute("SELECT * FROM sources WHERE name=?", (name,)).fetchone()
                if row["state"] == "building":
                    continue
                if row["state"] == "failed" and row["digest"] == digests[name] and want in ("needs-build", "dep-wait"):
                    continue
                self._set(name, want, note, version=r.full_version, digest=digests[name])
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    # -- builder protocol ------------------------------------------------
    def claim(self, builder, only=None):
        """Atomically move one needs-build source to building; return its name."""
        self.db.execute("BEGIN IMMEDIATE")
        try:
            q = "SELECT name FROM sources WHERE state='needs-build'"
            args = []
            if only:
                q += " AND name=?"
                args.append(only)
            row = self.db.execute(q + " ORDER BY since, name LIMIT 1", args).fetchone()
            if row is None:
                self.db.execute("COMMIT")
                return None
            self._set(row["name"], "building", f"claimed by {builder}", builder=builder)
            self.db.execute("UPDATE sources SET attempts=attempts+1 WHERE name=?", (row["name"],))
            self.db.execute("COMMIT")
            return row["name"]
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def finish(self, name, ok, note=""):
        row = self.get(name)
        if row is None or row["state"] != "building":
            raise RuntimeError(f"{name} is not being built (state {row and row['state']})")
        self._set(name, "built" if ok else "failed", note)

    def retry(self, name):
        row = self.get(name)
        if row is None:
            raise KeyError(name)
        if row["state"] not in ("failed", "blocked"):
            raise RuntimeError(f"{name} is {row['state']}, only failed/blocked can be retried")
        self._set(name, "needs-build", "manual retry")

    def reap(self, older_than_s):
        """Return builds stuck in 'building' (builder died) to needs-build."""
        cutoff = time.time() - older_than_s
        names = [r["name"] for r in self.db.execute(
            "SELECT name FROM sources WHERE state='building' AND since<?", (cutoff,))]
        for n in names:
            self._set(n, "needs-build", "builder presumed dead; build was abandoned")
        return names
