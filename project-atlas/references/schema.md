# Index schema and query recipes

The index is an ordinary SQLite database — the CLI is a convenience, not a gate. Anything
below runs in `sqlite3 ~/.local/share/project-atlas/atlas.db`.

## Tables

```sql
CREATE TABLE meta (                     -- schema_version, fts5 ("1"/"0")
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE projects (
    id            INTEGER PRIMARY KEY,
    path          TEXT NOT NULL UNIQUE, -- absolute; the identity of a project
    name          TEXT NOT NULL,        -- basename
    vcs           TEXT,                 -- 'git' or NULL
    git_remote    TEXT,                 -- origin url, or the first remote, or ''
    git_branch    TEXT,                 -- may be 'HEAD' when detached
    git_head      TEXT,                 -- 40-char sha, or '' in a repo with no commits
    git_head_at   TEXT,                 -- ISO 8601 committer date of HEAD
    languages     TEXT,                 -- comma-separated, manifest hits then extension census
    toolchains    TEXT,                 -- comma-separated marker labels
    file_count    INTEGER,              -- files walked (capped at 20000 per project)
    size_bytes    INTEGER,
    first_seen    TEXT NOT NULL,
    last_scanned  TEXT NOT NULL,
    missing_since TEXT                  -- non-NULL ⇒ the path was gone at the last scan
);

CREATE TABLE docs (
    project_id INTEGER PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
    body       TEXT NOT NULL            -- path, name, README, top-level docs; ≤ 65536 bytes
);

CREATE VIRTUAL TABLE docs_fts USING fts5(
    body, content='docs', content_rowid='project_id'
);                                      -- created only when the SQLite build has FTS5

CREATE TABLE agent_sessions (
    id                INTEGER PRIMARY KEY,
    session_id        TEXT NOT NULL,    -- the agent's own session uuid
    agent             TEXT NOT NULL,    -- 'claude-code'
    host              TEXT,             -- machine the capture came from
    cwd               TEXT,             -- working directory, read from the records
    slug              TEXT,             -- projects/<slug> directory it was found under
    project_id        INTEGER REFERENCES projects(id) ON DELETE SET NULL,
    git_branch        TEXT,
    cli_version       TEXT,             -- which CLI wrote this format
    title             TEXT,             -- first real user prompt, truncated
    started_at        TEXT,
    ended_at          TEXT,
    message_count     INTEGER,
    user_turns        INTEGER,
    assistant_turns   INTEGER,
    redacted          INTEGER NOT NULL DEFAULT 1,
    redaction_summary TEXT,             -- JSON {kind: count}
    transcript_sha256 TEXT,             -- of the ORIGINAL bytes, before redaction
    transcript_bytes  INTEGER,
    captured_at       TEXT NOT NULL,
    captured_from     TEXT,
    UNIQUE (agent, session_id, host)    -- two machines may each hold the same session
);

CREATE TABLE session_events (           -- one row per transcript record, verbatim
    session_row INTEGER NOT NULL REFERENCES agent_sessions(id) ON DELETE CASCADE,
    seq         INTEGER NOT NULL,       -- document order
    uuid        TEXT,
    parent_uuid TEXT,                   -- the chain a resume walks
    kind        TEXT,
    ts          TEXT,
    body        TEXT NOT NULL,          -- the whole JSON record as text
    PRIMARY KEY (session_row, seq)
);

CREATE TABLE session_artifacts (        -- tasks, live descriptor, optional shell snapshots
    session_row INTEGER NOT NULL REFERENCES agent_sessions(id) ON DELETE CASCADE,
    kind        TEXT NOT NULL,
    name        TEXT NOT NULL,
    body        TEXT NOT NULL,
    PRIMARY KEY (session_row, kind, name)
);

CREATE TABLE session_docs (
    session_row INTEGER PRIMARY KEY REFERENCES agent_sessions(id) ON DELETE CASCADE,
    body        TEXT NOT NULL           -- searchable chat text, <= 131072 bytes
);

CREATE VIRTUAL TABLE session_fts USING fts5(
    body, content='session_docs', content_rowid='session_row'
);
```

## Why sessions are stored this way

`session_events.body` holds each record **as text, unparsed**. The format is undocumented
and moves with the CLI version, so a normalized schema would silently drop whatever the next
version adds. Only the envelope columns (`uuid`, `parent_uuid`, `kind`, `ts`) are lifted
out, and only because ordering and chain verification need them.

`transcript_sha256` is the hash of the *original* bytes. For an unredacted capture, a
restore rebuilds the file and compares — a real equality check, verified byte-identical on a
456-record, 1.8 MB transcript. For a redacted one the bytes differ by design, so restore
falls back to checking that every non-root `parent_uuid` resolves to a stored `uuid`.

The uniqueness key is `(agent, session_id, host)` rather than `session_id` alone: two
machines can each hold a capture of the same session, and collapsing them would silently
discard one history.

## Why the document is built this way

`docs.body` leads with the project's path and name, then its README, then top-level
`*.md`/`*.rst`/`*.txt`/`*.adoc`, then `docs/` and `doc/`. Because the 64 KiB cap truncates
the tail, the ordering decides what survives: identity first, then the file most likely to
say what the project *is*. A 400 KB README keeps its first 64 KiB, which in practice is the
title, the summary, and the getting-started section.

`docs_fts` is an **external-content** table: the text lives once, in `docs`, and the index
references it by rowid. Scans rebuild it in one statement
(`INSERT INTO docs_fts(docs_fts) VALUES('rebuild')`) rather than maintaining triggers —
simpler, and the rebuild cost is trivial next to walking the filesystem.

If the SQLite build has no FTS5, `docs_fts` is never created and `meta.fts5` is `0`; search
falls back to `docs.body LIKE ?`. The same rows come back, ranked by path instead of
relevance.

## Litestream leaves its own tables here

Once replication is running, the database also contains `_litestream_seq` and a lock table
that Litestream creates to force WAL writes. They are expected. Any code that enumerates
tables should skip them:

```sql
SELECT name FROM sqlite_master
WHERE type = 'table' AND name NOT LIKE 'sqlite_%' AND name NOT LIKE '_litestream%';
```

## Recipes

**Checkouts you have not touched in a year**

```sql
SELECT path, git_head_at FROM projects
WHERE missing_since IS NULL AND git_head_at < date('now', '-1 year')
ORDER BY git_head_at;
```

**Two checkouts of the same repository**

```sql
SELECT git_remote, count(*) AS copies, group_concat(path, '  |  ') AS paths
FROM projects
WHERE git_remote <> '' AND missing_since IS NULL
GROUP BY git_remote HAVING copies > 1
ORDER BY copies DESC;
```

**Anything still pointing at an old forge**

```sql
SELECT path, git_remote FROM projects WHERE git_remote LIKE '%old-host.example%';
```

**Projects that vanished, and when they were last seen**

```sql
SELECT path, last_scanned, missing_since FROM projects
WHERE missing_since IS NOT NULL ORDER BY missing_since DESC;
```

**Disk by language**

```sql
SELECT languages, count(*) AS projects, sum(size_bytes)/1024/1024 AS mb
FROM projects WHERE missing_since IS NULL
GROUP BY languages ORDER BY mb DESC LIMIT 20;
```

**Ranked full-text search by hand**

```sql
SELECT p.path, bm25(docs_fts) AS score
FROM docs_fts JOIN projects p ON p.id = docs_fts.rowid
WHERE docs_fts MATCH 'kafka AND consumer'
ORDER BY score LIMIT 10;
```

**Which sessions touched a given project**

```sql
SELECT s.session_id, s.ended_at, s.title
FROM agent_sessions s JOIN projects p ON p.id = s.project_id
WHERE p.path = '/home/dev/payments' ORDER BY s.ended_at DESC;
```

**Sessions stored without redaction (an audit you want to be able to run)**

```sql
SELECT session_id, host, cwd, captured_at FROM agent_sessions WHERE redacted = 0;
```

**What redaction actually removed, per session**

```sql
SELECT session_id, redaction_summary FROM agent_sessions
WHERE redaction_summary <> '{}' ORDER BY ended_at DESC;
```

**Reconstruct a transcript by hand**

```sql
SELECT body FROM session_events e JOIN agent_sessions s ON s.id = e.session_row
WHERE s.session_id = '<id>' ORDER BY e.seq;
```

**Untracked work — a project with no VCS at all**

```sql
SELECT path, size_bytes FROM projects
WHERE vcs IS NULL AND missing_since IS NULL ORDER BY size_bytes DESC;
```

## Schema changes

`meta.schema_version` is `2` (version 1 had no session tables; the session commands create
them on first use, and `doctor` degrades cleanly against a v1 index). The scanner uses `CREATE TABLE IF NOT EXISTS` and an upsert,
so re-running a newer `atlas.py` over an older index adds nothing destructive — but it also
does not migrate. If a future version changes a column, bump `schema_version` and write the
migration explicitly; a replicated database makes silent schema drift a restore-time
surprise rather than a local one.
