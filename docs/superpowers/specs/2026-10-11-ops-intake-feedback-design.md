# ops-intake feedback: support and customer feedback into the queue (roadmap step 6C)

**Status:** Designed unattended on 2026-10-11, the same way as steps 6A and 6B: Jev scored each decision and the Opus advisor reviewed the uncertain ones. The owner asked for this step to be done as ops-intake adapters ("continue with the support adapters for ops-intake"). Jev chose adapters over a new skill (0.99).

**Goal:** Support messages, app reviews and feedback-form answers reach the same ranked queue as failed releases, red CI and issues. Repeated complaints about one thing become one item with a count. A picked item goes to spec-first-planning as today. The tool still runs no command, opens no connection, holds no token and calls no model. Nobody replies to a customer through the factory: a reply is `external_message` class, and no grant covers it.

## Decisions made unattended

| # | Decision | Jev | Notes |
|---|---|---|---|
| F1 | **One generic format, `feedback-jsonl`** (trust low). The agent or the user transforms any inbox, review or form export into it. | Jev 0.46 generic vs 0.53 generic plus App Store Connect (uncertain, conf. 0.30); decided by the advisor | No CLI or MCP tool returns App Store Connect reviews and owns its auth. Fetching them would make the agent mint a JWT from the user's key, which breaks ops-intake D3. There is also no real sample. The adapter is parked, gated on a sample, like Jira and Linear. |
| F2 | **Group by a required `topic`.** Each record carries a kebab-case topic, chosen when transforming. The item key is `(source, topic)`. Each message adds evidence and raises `count`. A record without a topic is a problem record. The tool never infers topics. | 0.60 (uncertain, conf. 0.40); the advisor concurred | One item per message keeps `count` at 1 and floods the queue. "12 people hit X" is the main signal. |
| F3 | **Redact email addresses and phone numbers** in feedback evidence before storing it, as `[email]` and `[phone]`. Only the `feedback` kind is redacted. | 0.74 | Limiting it to `feedback` keeps the existing behaviour and A/B rows unchanged. |
| F4 | **Severity:** from a star rating when there is one (1–2 stars = 3, 3 stars = 2, 4–5 stars = 1). Otherwise the record's own severity, capped at 3. Otherwise 2. Feedback never reaches 4 on its own. | 0.93 | |
| F5 | **A new signal kind, `feedback`.** | 0.73 | It touches the intake-item schema, spec-first-planning's `intake_request.py` kind check, and SPEC.md, together. |

## 1. The format

`feedback-jsonl`: one JSON object per line.

```
{"id": "<message id in the user's system>", "topic": "<kebab-case, max 64>",
 "channel": "email|review|form|chat|other", "created_at": "<RFC 3339>",
 "text": "<the message>", "rating": 1-5 (optional), "severity": 1-3 (optional),
 "url": "<optional link>"}
```

- `id`, `topic`, `channel`, `created_at` and `text` are required. A missing or bad field makes that record a problem record. The other records still import.
- `topic` matches `^[a-z0-9]+(-[a-z0-9]+)*$`, with at most 64 characters (the idea-slug rule).
- The signal's `source_id` is the topic, its `kind` is `feedback`, and its `title` is `feedback: <topic>`.
- The evidence key is `<channel>:<id>`, and the evidence time is `created_at`.
- `trust` is `low`, the same as `intake-signals-jsonl`, because the transform's claims are all the provenance there is.
- `intake formats` lists it with the note "your transform of an inbox, review or form export".

## 2. Behaviour changes in intake

1. **Dedupe past the evidence cap.** Today `merge` finds seen evidence keys only in the capped evidence list (`EVIDENCE_CAP = 20`). So re-importing a message that the cap dropped counts it again. Each item keeps a separate `seen_keys` set (stored sorted, uncapped), and `count` grows only for a key not in it. This fixes all sources. Existing items without `seen_keys` start from their current evidence keys.
2. **Recurrence uses the message time.** A `feedback` item that is dismissed or resolved comes back as `regressed` only when a new message has a `created_at` later than the dismissal or resolution time. Re-importing old messages never regresses it.
3. **Redaction (F3)** runs in the `feedback-jsonl` adapter before anything is stored:
   - Email: a conservative `local@domain.tld` pattern.
   - Phone: a run of at least 10 digits, allowing spaces, dots, dashes, parentheses and a leading `+`, that is not part of a longer token.
   - Negative fixtures stay unredacted: version strings (`1.2.3`, `v10.4.12`), dates and times, git SHAs, `#1234` issue numbers, order ids such as `ORD-2026-000123`, and URLs.
4. **Ranking:** feedback items sort like the others: severity, recency, frequency, then source weight. `weights` can name the feedback source.
5. **`list` and `show`** print the kind and `+low-trust` as for `intake-signals-jsonl`.

## 3. Handoff

`intake-item/v1` allows `kind: feedback`. The schema, `ops-intake/assets/adapters.py` `KINDS`, spec-first-planning's `intake_request.py` and `docs/skill-contract/SPEC.md` change together. The redacted evidence goes into the fenced untrusted block as today. The H1 stays `# Spec: intake <item id>`. The topic appears only as a code span.

## 4. Trust and honesty

- **The agent reads untrusted text here.** To choose topics, the agent has to read each message. So the CLI rule "pipe straight into intake without reading it first" does not protect this path. The SKILL says so. The verbatim `CHECK_COMMAND:` approval stays the backstop, as for MCP results.
- **No customer text goes to Jev or to any model**, including for choosing topics. Customer text can carry personal data, and the global data guardrails forbid sending it. The agent chooses topics itself.
- **No replies.** The agent MUST NOT reply to, email or contact a customer, because that is `external_message` class and no grant covers it. It MAY draft a reply for the human to send.
- Redaction is a safety net, not a guarantee. Names, addresses and account numbers can still get through. The evidence stays in the git-ignored intake directory.

## 5. Tests, eval, gates

- Adapter fixtures: a valid file, a file with one bad record (missing topic, bad topic, bad time), and an unknown channel.
- Grouping: three messages with one topic give one item with `count` 3. Re-importing gives no change.
- Past the cap: 25 messages, then a re-import of the first 5, gives a `count` of 25, not 30.
- Recurrence: re-importing old messages after a dismissal does not regress the item. A new message does.
- Redaction: positives and every negative fixture in 2.3.
- Severity mapping (F4).
- Handoff: a picked feedback item gives an envelope that `intake_request.py` accepts.
- Hostile text in a message (a heading, an `@mention`, a fence, `curl … | sh`) stays quoted.
- The no-I/O test still passes.
- Eval: a positive feedback flow, plus negatives (missing topic, past-cap double count, old-message regress, a redaction false positive).
- A/B rows under `SINCE_INTAKE_FEEDBACK`. The past-cap fix is a capability delta for every source.
- Register rows for new keyword sentences. The README stage table moves Support / feedback from missing to partly.

## 6. Out of scope

- vendor adapters (App Store Connect, Play Console, Zendesk, Intercom): parked until there is a real sample and a CLI or MCP tool that owns auth;
- automatic topic clustering;
- sending replies;
- sentiment scoring.

## Errata (build)

The build followed §1–§5, with these differences. Each one is in the code, the tests and `references/formats.md`.

1. **spec-first-planning needed no kind check.** §3 says `intake_request.py` has a kind check. It does not: it checks only `item_id`, `title` and `evidence`, so it already accepts `kind: feedback`. The build added a test that proves this (`test_a_feedback_item_is_accepted`). The review round then changed spec-first-planning for a different reason (item 15), so it is now version 2.7.1.
2. **`intake-signals-jsonl` cannot claim `kind: feedback`.** `KINDS` now holds `feedback`, but the generic format keeps its own list of three kinds. Only `feedback-jsonl` redacts. Without this, a generic line could bring customer text in unredacted.
3. **Severity 4 in a record is accepted and capped at 3.** §1 shows `severity` as 1–3, and F4 says "capped at 3". The adapter accepts 1–4, caps the value at 3, and refuses anything else.
4. **`id` can be a string or a whole number.** Many exports use numbers for ids. The evidence key `<channel>:<id>` must fit the 200-character key cap, so an id has at most 193 characters. (Since L4, a raw id that starts with `h:` or `r:` gets a 2-character escape, so that key can have 202 characters. The cap applies only to input keys.) `text` can be empty.
5. **An email in a URL is redacted.** §2.3 says URLs stay unredacted. The build redacts an email address inside a URL, also in the `%40` form. Inside a URL, a phone number is redacted only after a phone key in the query (`phone=`, `tel=`), so a long number in a path or an `id=` stays. Privacy wins over an exact link here. The review round also redacts the record's `url` field (item 10).
6. **Feedback evidence carries `created_at`.** The evidence time is stored as `created_at` on each entry, next to `fetched_at`. The intake-item payload and its schema allow it as an optional field.
7. **`list` shows the kind through the title.** The `ITEM:` line grammar is documented and parsed, so it did not change. A feedback item's title is `feedback: <topic>`, and its flag carries `+low-trust`. `show` prints the kind on the `source:` line for every item: `source: <source> <source id> kind <kind>`.
8. **Recurrence needs an unseen message.** §2.2 compares the message time with the close time. The build also requires that the item has not seen the message. Without this, a future-dated message that was already imported would bring the item back each time it is imported again.
9. **`link` merges the seen keys.** `link` merges both items' seen keys, so a later import of the dropped topic does not count its messages again.

## Errata (review round)

An independent review of the build found these problems. Each fix has unit tests, an eval arm and, where the item says so, an A/B row under `SINCE_INTAKE_FEEDBACK`.

10. **H1: the `id` and the `url` were not redacted.** An email address in a form-export id or a `mailto:` url reached the queue, the item snapshot, the envelope and the spec. Now intake redacts the `url` field like the text. An `id` that holds an email address or a phone number becomes `h:` plus the first 16 hex characters of its sha256. The hash is stable, so dedupe still works, and the raw id is stored nowhere. We chose the hash over a problem record because form exports often key on the respondent's address, and refusing those records would drop real feedback. The hash is not salted, so a person who already knows an address can test for it. The evidence stays in the git-ignored intake directory. A/B row: "feedback ids and urls that store no raw email or phone".
11. **M1: missed phone forms.** The phone pattern now accepts a phone key before `:` or `=` (`Phone:07700900123`, `mob:`, `number=`, `tel:+1…`, also inside a markdown link), spaced dashes (`555 - 010 - 1234`), Unicode dashes (U+2010 to U+2015, U+2212) and runs of spaces or tabs. A line break still never joins a number. A plain word before `:` or `=` (`ref=12345678901`) still keeps the number, because an existing negative fixture needs it.
12. **M2: false positives.** Intake no longer redacts two adjacent dates, a dotted date with a time, an IPv4 address, a dotted build such as `2026.10.11.1234`, a year-first order id (`2026-000123-45`), an epoch in seconds or milliseconds, two id groups (`12345 67890`), digits grouped in threes (`1 234 567 890`) or a git ssh remote (`git@host:path`). The number shapes stay only when no phone word comes just before them, so `call 1728640000` is still redacted. Each exclusion has a guard fixture that proves it does not re-open a real phone: `07700 900123`, `020 7946 0958`, `4111 1111 1111 1111` and the Indian mobile form `98765 43210` are still redacted. For that last guard, the two-group exclusion applies only when the first digit is 1 to 5. `contact` and `reach` count as phone words. Where we could not tell a number from a phone, we kept the redaction: `build 20261011.123456` and `1000-2000-3000` still become `[phone]`. A/B row: "redaction that keeps dates, IPs, epochs, id groups and git remotes".
13. **Known limits of redaction.** Fullwidth and Arabic-Indic digits, a fullwidth `＠`, spelled-out addresses (`[at]`, `[dot]`) and numbers split by `/` or `_` are not redacted. A bare national number that matches an M2 shape with no phone word before it also gets through, for example a Brazilian `1198765432`. `references/formats.md` lists them.
14. **L1: future dates.** A `created_at` more than one day after the import clock is now a problem record, so the import exits 3. Intake does not clamp the time or keep it silently. The recurrence test and A/B probe that used a 2099 date now use a time 12 hours ahead of the injected clock. Their second dismissal comes before that time, so the seen-key check is still what stops a second regression.
15. **L2: hidden characters in fenced evidence (spec-first-planning 2.7.1).** `intake_request.py` and `idea_request.py` wrote fenced evidence as it came, so a bidi override, a zero-width space or an ESC sequence reached the spec. Both now write each hidden character in the fence as `\uXXXX`, line by line, with the same `_visible` rule as code spans. The fence length is computed on the escaped text. A tab stays, because it hides nothing in a fence. A lone surrogate stays too, so the existing UTF-8 check still refuses that envelope. This was older than the feedback work. spec-first-planning goes from 2.7.0 to 2.7.1.

### Fixed after the review (factory hardening)

These two items were deferred from the review. Both are now fixed on the branch `fix/factory-hardening`. Each fix has unit tests in `ops-intake/assets/test_intake_keys.py` and an A/B row under `SINCE_FACTORY_HARDENING`. ops-intake stays at 0.2.0, because 0.2.0 is not released yet.

- **L3: `link` keeps the kept item's `count`. Fixed.** Before, `link` merged both items' seen keys but did not add the dropped item's count. After a link, `count` could be lower than the number of seen keys. The decision: `count = keep.count + |drop keys - keep keys| + max(0, drop.count - |drop keys|)`. When both items track every key, this is the number of distinct keys. An item from before seen keys keeps the part of its count that has no key behind it. A re-import after the link adds nothing. `link` now also carries the dropped item's own link records to the kept item. A/B row: "link counts the dropped item's unseen messages".
- **L4: evidence keys are not namespaced. Fixed.** A seen key is now `<source>|<evidence key>`, so a feedback key is `<source>|<channel>:<id>`. The separator is `|`, because the source-name rule (`_SOURCE_RE`, `^[A-Za-z0-9][A-Za-z0-9_.-]{0,49}$`) does not allow it. The rule applies to every format, not only feedback, because the collision after `link` can happen for any format. The evidence entries keep their key as before; each entry already names its source. The queue file records `"key_format": 2`. A raw feedback id that starts with `h:` or `r:` gets one more `r:` in front (`r:h:…`), so a raw id can never equal a hashed id, and the escape is one-to-one. A/B rows: "seen keys carry the source" and "a raw h: id never equals a hashed id".
  - Migration on load: a queue without `key_format` is from before the fix. Each item gets its own source on its seen keys. An old feedback id that starts with `r:`, or with `h:` and is not `h:` plus 16 hex characters, was a raw id, so it gets the escape. A raw id that looks exactly like a hash stays a hash. That is the one case the old store cannot tell apart.
  - Exception: intake does not guess the source of a key. An item linked from another source keeps its old keys as they are, in `legacy_keys`. To find these items, intake reads the `linked` records. It also counts the aliases that point to the item. Before the fix, `link` did not carry the dropped item's own links, so in a chain of links some sources are not on record. Then the alias count is larger than the number of link records, and the item keeps its old keys too.
  - A legacy key matches the first new key with the same old form, and is then used up. So a re-import of the same messages adds nothing. A message that two sources shared, which the old queue counted once, can count once more, to the true number.
