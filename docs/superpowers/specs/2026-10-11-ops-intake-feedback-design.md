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
