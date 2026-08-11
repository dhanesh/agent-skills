# Replication with Litestream 0.5.x

Everything here is pinned to the **0.5.x** series (v0.5.16, released 2026-08-05 — the
current release when this skill was written). The 0.3.x-era tutorials that dominate search
results describe a different config grammar and a different on-disk replication format
(0.5 moved to immutable LTX files), so copying from them produces configs that either fail
to load or silently use deprecated syntax.

Each constraint below is grounded in a source fetched during the skill's research pass; the
full report, including the one claim that could **not** be grounded, is
`docs/project-atlas/2026-08-11-research-validation.md`.

## What Litestream does to your database

It runs as a separate long-lived process, watches the SQLite WAL, converts changes to
immutable LTX files, and streams them to object storage. Consequences that matter here:

- **It requires WAL, and enforces it.** Litestream runs `PRAGMA journal_mode = wal` on the
  database it is given and errors when the mode does not take. `atlas.py` therefore opens
  the index in WAL itself and refuses to run otherwise — a rollback-mode index is a
  replication failure waiting for the worst possible moment.
- **It writes into your database.** `_litestream_seq` and a lock table appear alongside your
  own tables. Expected; see `references/schema.md` for the filter.
- **One database, one replica.** 0.5.x replicates each database to exactly one destination;
  the config loader rejects more than one. Multi-destination redundancy belongs at the
  bucket level (cross-region replication), not in this config.

## Config shape

`atlas.py litestream-config` emits the singular `replica:` mapping:

```yaml
access-key-id: ${LITESTREAM_ACCESS_KEY_ID}
secret-access-key: ${LITESTREAM_SECRET_ACCESS_KEY}

dbs:
  - path: /home/dev/.local/share/project-atlas/atlas.db
    replica:
      url: s3://atlas-index/project-atlas?endpoint=https://ACCOUNT.r2.cloudflarestorage.com&region=auto
      sync-interval: 10s
```

Two things are deliberate:

- **`replica:`, not `replicas:`.** The list form is marked deprecated in Litestream's config
  structs and the loader errors on more than one entry. Most third-party guides — and
  Litestream's own provider-compatibility doc — still show the list; the loader is the
  authority, not the prose.
- **`sync-interval` is always written.** Litestream's default is one second. For an index
  that changes when someone runs a scan, that is ~86k sync cycles a day against object
  storage for a database that usually has not changed. 10s is the skill's default; for a
  once-a-day scan, `1m` or higher is entirely reasonable.

Credentials are `${…}` expansions. Litestream expands environment variables in the config at
load time (`-no-expand-env` turns that off). Export them in the same environment as the
service:

```sh
export LITESTREAM_ACCESS_KEY_ID=...
export LITESTREAM_SECRET_ACCESS_KEY=...
```

## Per-provider settings

### Cloudflare R2 — `--target r2 --bucket B --account-id ACCT`

```
url: s3://B/project-atlas?endpoint=https://ACCT.r2.cloudflarestorage.com&region=auto
```

**The `https://` scheme is load-bearing.** Litestream detects R2 from the endpoint URL and
then applies the settings R2 needs — signed payloads, `concurrency=2`, checksums disabled.
Litestream's provider doc states the scheme must be present for that detection to fire, and
its S3 client hard-codes the R2 concurrency default because R2 caps concurrent uploads at
two to three. A bare `ACCT.r2.cloudflarestorage.com` endpoint therefore runs with AWS
defaults against a backend that supports neither `aws-chunked` encoding nor request
checksums. The generator always attaches the scheme; `ensure_https()` upgrades anything you
pass in, including an `http://` URL.

Create the bucket and an API token with **Object Read & Write** for it, then use the token's
access key id and secret as the two environment variables above.

### Amazon S3 — `--target s3 --bucket B --region eu-west-1`

```
url: s3://B/project-atlas?region=eu-west-1
```

No endpoint needed. The IAM policy needs `s3:GetObject`, `s3:PutObject`,
`s3:DeleteObject`, and `s3:ListBucket` on the bucket and its contents.

### Backblaze B2 — `--target b2 --bucket B --endpoint s3.REGION.backblazeb2.com`

```
url: s3://B/project-atlas?endpoint=https://s3.REGION.backblazeb2.com&sign-payload=true&force-path-style=true
```

B2's S3 API requires signed payloads and path-style addressing; the generator emits both.

### Any other S3-compatible endpoint — `--target custom --bucket B --endpoint HOST`

Minio, Tigris, Wasabi, DigitalOcean Spaces, Hetzner. Litestream carries presets for several
of these keyed on the endpoint host, so pass the real endpoint rather than a CNAME and let
detection work. If uploads fail with signature or checksum errors, the two knobs to try are
`sign-payload=true` and `force-path-style=true` appended to the URL.

### A second disk or a mounted NAS — `--target file --replica-path /mnt/backup/atlas`

No credentials, no network, and a perfectly good answer when "survives this machine" means a
different disk rather than a different continent.

## Running it

```sh
litestream replicate -config ~/.config/litestream/atlas.yml
```

Foreground, and it stays up — put it under a supervisor. A user-level systemd unit:

```ini
[Unit]
Description=Litestream replication for the project-atlas index
After=network-online.target

[Service]
ExecStart=/usr/local/bin/litestream replicate -config %h/.config/litestream/atlas.yml
Environment=LITESTREAM_ACCESS_KEY_ID=...
Environment=LITESTREAM_SECRET_ACCESS_KEY=...
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
```

Prefer an `EnvironmentFile=` with mode `0600` over inline credentials if anything else can
read the unit. On macOS the equivalent is a launchd agent with `KeepAlive`.

Scanning is separate and scheduled — the scanner is not a daemon:

```
17 4 * * *  /usr/bin/python3 /path/to/atlas.py scan "$HOME/src" "$HOME/work"
```

## Restoring — do this once, on purpose

The generator prints these; they are the whole point of the exercise.

```sh
# latest transaction
litestream restore -o ~/.local/share/project-atlas/atlas.db "s3://atlas-index/project-atlas?endpoint=https://ACCT.r2.cloudflarestorage.com&region=auto"

# a point in time
litestream restore -timestamp 2026-08-01T00:00:00Z -o /tmp/atlas-aug.db "<same url>"

# see the plan without writing anything
litestream restore -dry-run -o /tmp/atlas-check.db "<same url>"
```

`litestream restore` refuses to overwrite an existing file, which is why the recovery drill
uses a scratch path:

```sh
litestream restore -o /tmp/atlas-check.db "<url>"
python3 assets/atlas.py stats --db /tmp/atlas-check.db
```

A project count matching the live index is the proof. Until that has been run once, the
replica is a hypothesis, not a backup. `-if-replica-exists` and `-if-db-not-exists` exist
for scripting a restore-on-boot without failing when there is nothing to restore.

## Operating notes

- **Cost.** The index is small — a few MB for hundreds of projects — so storage is
  negligible and request count dominates. `sync-interval` is the dial. R2 charges no egress,
  which makes restores free; S3 does not.
- **One writer.** Litestream assumes a single process writes the database. Do not point two
  machines' Litestream at the same bucket path; give each machine its own prefix
  (`--path atlas/$(hostname)`) if you index more than one.
- **A scan during replication is fine.** Writes go through the WAL and Litestream picks them
  up on its next sync; no pause, no lock held against the scanner.
- **Upgrading Litestream.** If a future release changes the config grammar again, the
  generator's tests are the tripwire: they assert the emitted shape, so a `make gate` failure
  is the signal to re-run the research pass rather than to patch the fixture.

## Sources

Fetched during the research pass and quoted in
`docs/project-atlas/2026-08-11-research-validation.md`:

- Litestream releases — <https://github.com/benbjohnson/litestream/releases>
- `cmd/litestream/main.go` (config structs, loader validation) — <https://raw.githubusercontent.com/benbjohnson/litestream/main/cmd/litestream/main.go>
- `docs/PROVIDER_COMPATIBILITY.md` — <https://raw.githubusercontent.com/benbjohnson/litestream/main/docs/PROVIDER_COMPATIBILITY.md>
- `s3/replica_client.go` (R2 concurrency default, endpoint parsing) — <https://raw.githubusercontent.com/benbjohnson/litestream/main/s3/replica_client.go>
- `db.go` (WAL enforcement, internal tables) — <https://raw.githubusercontent.com/benbjohnson/litestream/main/db.go>
- `replica.go` (`DefaultSyncInterval`) — <https://raw.githubusercontent.com/benbjohnson/litestream/main/replica.go>
- `cmd/litestream/restore.go` (restore flags) — <https://raw.githubusercontent.com/benbjohnson/litestream/main/cmd/litestream/restore.go>
- `etc/litestream.yml` (shipped example config) — <https://raw.githubusercontent.com/benbjohnson/litestream/main/etc/litestream.yml>
