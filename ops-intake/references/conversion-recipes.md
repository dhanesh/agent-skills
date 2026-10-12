# Conversion recipes: Jira, Linear, app stores and support tools

ops-intake has no dedicated reader for Jira, Linear, App Store Connect, Google Play Console,
Zendesk or Intercom. The owner decided this on 2026-10-12. Each tool comes in through one of
the two generic JSON-lines formats in `formats.md`. This page gives a short recipe for each
tool:

1. Export the records with the vendor's own API, CLI or console.
2. Convert each record to one line with a small stdlib Python script.
3. Import the lines with `intake import`.

## Which format each tool uses

| Tool | Format | Why |
|---|---|---|
| Jira, Linear | `intake-signals-jsonl` | An issue is a signal: a title, a severity and two times. |
| App Store Connect, Play Console, Zendesk, Intercom | `feedback-jsonl` | A review or a ticket is customer text. Only `feedback-jsonl` redacts customer text, and `intake-signals-jsonl` refuses `kind: feedback`. |

Do not send review or ticket text through `intake-signals-jsonl`. That path skips the
redaction.

## Rules for every recipe

- **Use read calls only.** Each export is a search, list or query call. Do not use a GraphQL
  mutation or a POST that changes data. Intake cannot see the calls that you make.
- **Do not read tracker output.** For Jira and Linear, the script writes the export to
  files and imports them. Do not read those files (SKILL.md step 2).
- **A non-zero exit means the source was skipped.** Each script runs with
  `set -euo pipefail` and uses `curl -fsS`. A dead host, a bad token, a rate limit or an
  error body stops the script before the import. When a script exits non-zero, report that
  source as skipped (SKILL.md step 1). Do not report "nothing open". An import that runs on
  an empty export prints `ok 0` and exits 0, so the exit status of the whole script is the
  only signal.
- **Use a private temporary directory.** Each script makes one with `mktemp -d` (mode 700)
  and removes it with `trap 'rm -rf "$d"' EXIT`. Write every export page and every
  intermediate file under `"$d"`, and nothing in the repo.
- **Keep secrets off the command line.** A token in a curl argument (`-u` or `-H`) is
  visible to `ps` while curl runs. The scripts write the token into a curl config file in
  `"$d"` with the shell's `printf` builtin and pass it with `-K`. Keep tokens in environment
  variables, for example `$JIRA_TOKEN`, and never in a file in the repo, because the repo
  is shared.
- **Fetch every page.** Each script follows the next-page cursor until the last page. If you
  change a script, compare the number of imported records with the total that the tool
  shows. A single page of 100 cuts the list without an error.
- **Keep only what the signal needs.** The scripts drop names, nicknames, email addresses,
  requester and author fields, devices and territories. Do not add them back.
- **Titles are not redacted.** `intake-signals-jsonl` stores a title as it is. A Jira
  `summary` or a Linear `title` that holds an email address or a phone number lands in the
  queue as written. The Jira and Linear scripts mask both with `mask()`. Keep that step, or
  drop such titles.
- **Save the scripts outside the repo.** They are your own transforms, not part of the
  skill. A copy in the repo is a file that someone has to maintain.
- **Check your own export.** The field names below come from the vendor documents listed at
  the end of this page. A site can rename fields or add custom fields. If a field is missing,
  find the field in your export that holds the same value and change the script.
- **Exclude closed work.** `intake-signals-jsonl` has no state field, so the export query
  has to ask only for open issues.

Each script defines `intake` as `python3 "$SKILL_DIR/assets/intake.py" --root "$REPO"`, as in
SKILL.md. Set `SKILL_DIR` and `REPO` before you run it. The `--source` name needs to be a
source in `.intake/config.json`.

## Jira (Jira Cloud REST API v3)

**Export.** Use the enhanced JQL search, `GET /rest/api/3/search/jql`. Ask for the fields
`summary`, `priority`, `created` and `updated`, and use a JQL query that excludes done
issues. The response has an `issues` array, and each issue has a `key` and a `fields`
object. To get the next page, send the `nextPageToken` from the response. You can also use
the Atlassian CLI (`acli jira workitem search --json`) or the Atlassian MCP search tool,
but their output shapes are not documented. Check that the issues still carry `key` and
`fields` before you use the script below.

Run it with `bash jira-import.sh`. Set `JIRA_SITE` (for example
`https://acme.atlassian.net`), `JIRA_USER` and `JIRA_TOKEN` first.

```bash
# jira-import.sh
set -euo pipefail
intake() { python3 "$SKILL_DIR/assets/intake.py" --root "$REPO" "$@"; }
d=$(mktemp -d); chmod 700 "$d"; trap 'rm -rf "$d"' EXIT
printf 'user = "%s:%s"\n' "$JIRA_USER" "$JIRA_TOKEN" > "$d/auth"
cursor=""; n=0
while :; do
  n=$((n + 1))
  curl -fsS -K "$d/auth" -G "$JIRA_SITE/rest/api/3/search/jql" \
    --data-urlencode 'jql=project = OPS AND statusCategory != Done' \
    --data-urlencode 'fields=summary,priority,created,updated' \
    --data-urlencode 'maxResults=100' \
    ${cursor:+--data-urlencode "nextPageToken=$cursor"} -o "$d/page$n.json"
  cursor=$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1])).get("nextPageToken") or "")' "$d/page$n.json")
  [ -n "$cursor" ] || break
done
for f in "$d"/page*.json; do python3 jira2signals.py jira "$JIRA_SITE" < "$f"; done > "$d/signals.jsonl"
intake import --format intake-signals-jsonl --source jira < "$d/signals.jsonl"
```

**Convert** (`jira2signals.py`):

```python
# Jira REST search result (stdin) -> intake-signals-jsonl (stdout).
# argv[1] = the --source name, argv[2] = your site, for example https://acme.atlassian.net
import json, re, sys
SEV = {"highest": 4, "high": 3, "medium": 2, "low": 1, "lowest": 1}  # site-defined names
def mask(t):  # titles are not redacted by intake: mask emails and 10+ digit numbers
    t = re.sub(r"[^\s@<>()]+@[^\s@<>()]+\.[A-Za-z]{2,}", "[email]", t)
    return re.sub(r"\+?\d[\d ().-]{8,}\d",
                  lambda m: "[phone]" if sum(c.isdigit() for c in m.group()) >= 10 else m.group(), t)
source, site = sys.argv[1], sys.argv[2].rstrip("/")
for issue in json.load(sys.stdin)["issues"]:
    f = issue.get("fields") or {}
    prio = ((f.get("priority") or {}).get("name") or "").lower()
    print(json.dumps({
        "source": source, "source_id": issue["key"], "kind": "issue",
        "url": "%s/browse/%s" % (site, issue["key"]),
        "title": mask(f.get("summary") or issue["key"]), "severity": SEV.get(prio, 2),
        "first_seen": f["created"], "last_seen": f["updated"]}))
```

**Notes.**

- Each site sets its own priority names. The map uses the default names. A name that is not in
  the map gives severity 2. Edit the map to match your site.
- The issue's `self` field is an API link, not a page link. The script builds the
  `/browse/<key>` page link from your site address.
- Jira writes times as `+0000`. Intake accepts that form.
- The script drops `description`. It is a rich-text document (Atlassian Document Format),
  and it can hold customer data. If you need evidence text, add one short plain sentence of
  your own in `evidence`.

## Linear (GraphQL API)

**Export.** Send a GraphQL query to `https://api.linear.app/graphql`. A personal API key goes
in the `Authorization` header as it is, with no `Bearer` word. Ask for `identifier`,
`title`, `url`, `priority`, `createdAt` and `updatedAt`. Keep the open state types
(`triage`, `backlog`, `unstarted` and `started`), so that `completed` and `canceled`
issues stay out. The connection's `pageInfo` (`hasNextPage`, `endCursor`) and the `after`
argument give the next page.

Run it with `bash linear-import.sh`. Set `LINEAR_API_KEY` first.

```bash
# linear-import.sh
set -euo pipefail
intake() { python3 "$SKILL_DIR/assets/intake.py" --root "$REPO" "$@"; }
d=$(mktemp -d); chmod 700 "$d"; trap 'rm -rf "$d"' EXIT
printf 'header = "Authorization: %s"\n' "$LINEAR_API_KEY" > "$d/auth"
q='query($after: String) { issues(first: 100, after: $after, filter: { or: [
  {state: {type: {eq: "triage"}}}, {state: {type: {eq: "backlog"}}},
  {state: {type: {eq: "unstarted"}}}, {state: {type: {eq: "started"}}} ] }) {
  nodes { identifier title url priority createdAt updatedAt }
  pageInfo { hasNextPage endCursor } } }'
after=""; n=0
while :; do
  n=$((n + 1))
  python3 -c 'import json, sys; print(json.dumps({"query": sys.argv[1], "variables": {"after": sys.argv[2] or None}}))' "$q" "$after" > "$d/body.json"
  curl -fsS -K "$d/auth" -H 'Content-Type: application/json' \
    --data @"$d/body.json" https://api.linear.app/graphql -o "$d/page$n.json"
  after=$(python3 -c 'import json, sys; p = json.load(open(sys.argv[1]))["data"]["issues"]["pageInfo"]; print(p["endCursor"] if p["hasNextPage"] else "")' "$d/page$n.json")
  [ -n "$after" ] || break
done
for f in "$d"/page*.json; do python3 linear2signals.py linear < "$f"; done > "$d/signals.jsonl"
intake import --format intake-signals-jsonl --source linear < "$d/signals.jsonl"
```

**Convert** (`linear2signals.py`):

```python
# Linear GraphQL issues result (stdin) -> intake-signals-jsonl (stdout). argv[1] = --source name.
import json, re, sys
SEV = {1: 4, 2: 3, 3: 2, 4: 1}  # 1 Urgent, 2 High, 3 Medium, 4 Low; 0 (no priority) -> 2
def mask(t):  # titles are not redacted by intake: mask emails and 10+ digit numbers
    t = re.sub(r"[^\s@<>()]+@[^\s@<>()]+\.[A-Za-z]{2,}", "[email]", t)
    return re.sub(r"\+?\d[\d ().-]{8,}\d",
                  lambda m: "[phone]" if sum(c.isdigit() for c in m.group()) >= 10 else m.group(), t)
source = sys.argv[1]
for n in json.load(sys.stdin)["data"]["issues"]["nodes"]:
    print(json.dumps({
        "source": source, "source_id": n["identifier"], "kind": "issue", "url": n["url"],
        "title": mask(n["title"]), "severity": SEV.get(int(n.get("priority") or 0), 2),
        "first_seen": n["createdAt"], "last_seen": n["updatedAt"]}))
```

**Notes.**

- `priority` is a number: 0 is no priority, 1 Urgent, 2 High, 3 Medium and 4 Low. It can
  come back as `3.0`, so the script converts it to a whole number.
- The query asks for no assignee, creator or description, so no personal data comes in.
- A GraphQL error response has no `data`. The script then stops with an error and a
  non-zero exit, and intake imports nothing. Run it again after you fix the query.

## Feedback tools: the shared flow

App Store Connect, Play Console, Zendesk and Intercom go to `feedback-jsonl`. Each line
needs a `topic`, and you choose the topics in the session (SKILL.md, "Support and customer
feedback"). A script cannot choose a topic honestly, and a catch-all topic merges every
message into one item. You read the messages between the export and the import, so the flow
has three parts. The raw customer text stays in one private temporary directory until intake
has redacted it:

1. **Export.** Run the tool's export script (below). It prints the path of a private
   directory, `$d`. The raw pages are removed when the script exits. Only
   `$d/feedback.jsonl` stays: one line per message, with every field except `topic`.
2. **Choose topics.** Read `$d/feedback.jsonl`. Write `$d/topics.json`, a JSON object that
   maps each message `id` to the topic you chose, for example `{"35436": "checkout-crash"}`.
   Reuse topics that are already in the queue.
3. **Import and clean up.** Run the import below with the same `$d`. Its `trap` removes the
   directory when it ends, also on a failure. If you stop before this step, run
   `rm -rf "$d"` yourself, because the file holds customer text that intake has not
   redacted.

```bash
# feedback-import.sh <dir from step 1> <source name>
set -euo pipefail
intake() { python3 "$SKILL_DIR/assets/intake.py" --root "$REPO" "$@"; }
d=$1; trap 'rm -rf "$d"' EXIT
python3 add_topics.py "$d/topics.json" < "$d/feedback.jsonl" > "$d/ready.jsonl"
intake import --format feedback-jsonl --source "$2" < "$d/ready.jsonl"
```

`add_topics.py`:

```python
# Add the topics you chose (argv[1]: a JSON object, message id -> topic) to feedback lines.
import json, sys
topics = json.load(open(sys.argv[1]))
for line in sys.stdin:
    if line.strip():
        rec = json.loads(line)
        rec["topic"] = topics.get(str(rec["id"]))  # no topic: intake reports a PROBLEM line
        print(json.dumps(rec))
```

Every export script below starts the same way. The `raw` directory holds the API pages and
is removed on exit. `$d` stays for steps 2 and 3:

```bash
set -euo pipefail
d=$(mktemp -d); chmod 700 "$d"
raw=$(mktemp -d); chmod 700 "$raw"; trap 'rm -rf "$raw"' EXIT
```

## App Store Connect (customer reviews)

**Export.** Use the App Store Connect API endpoint
`GET /v1/apps/{id}/customerReviews`, with a signed App Store Connect API token. Each review
in `data` has an `id` and `attributes`: `rating`, `title`, `body`, `reviewerNickname`,
`createdDate` and `territory`. `links.next` holds the next page, and the last page has no
`links.next`.

```bash
# appstore-export.sh (after the shared start). Set APP_ID and ASC_TOKEN first.
printf 'header = "Authorization: Bearer %s"\n' "$ASC_TOKEN" > "$raw/auth"
url="https://api.appstoreconnect.apple.com/v1/apps/$APP_ID/customerReviews"; n=0
while [ -n "$url" ]; do
  n=$((n + 1))
  curl -fsS -K "$raw/auth" "$url" -o "$raw/page$n.json"
  url=$(python3 -c 'import json, sys; print((json.load(open(sys.argv[1])).get("links") or {}).get("next") or "")' "$raw/page$n.json")
done
for f in "$raw"/page*.json; do python3 appstore2feedback.py < "$f"; done > "$d/feedback.jsonl"
echo "$d"
```

**Convert** (`appstore2feedback.py`):

```python
# App Store Connect customerReviews result (stdin) -> feedback-jsonl without topics (stdout).
import json, sys
for r in json.load(sys.stdin)["data"]:
    a = r.get("attributes") or {}
    text = "\n".join(t for t in (a.get("title"), a.get("body")) if t)
    print(json.dumps({"id": r["id"], "channel": "review", "created_at": a["createdDate"],
                      "text": text, "rating": a.get("rating")}))
```

**Drop:** `reviewerNickname` and `territory`. The review's `links.self` is an API link, so
the script sends no `url`.

## Google Play Console (reviews)

**Export, recent reviews.** The Google Play Developer API `reviews` list method returns only
reviews with a written comment. It also returns only reviews from the last week. Each
review has `reviewId`, `authorName` and `comments`. A user comment holds `text`,
`starRating` and `lastModified` (`seconds` and `nanos`). Use the shared start, save each
response page under `"$raw"`, and follow the page token that your response gives until it
is empty. Then compare the count with the Play Console, because a single page cuts the list
without an error. Convert each page into `"$d/feedback.jsonl"`, as in the App Store script.

**Convert** (`play2feedback.py`):

```python
# Google Play Developer API reviews.list result (stdin) -> feedback-jsonl without topics.
import datetime, json, sys
for r in json.load(sys.stdin).get("reviews", []):  # an empty week can omit the list
    uc = next((c["userComment"] for c in r.get("comments", []) if "userComment" in c), None)
    if uc is None:
        continue
    secs = int(uc["lastModified"]["seconds"])  # the last change, not the first post
    when = datetime.datetime.fromtimestamp(secs, datetime.timezone.utc)
    print(json.dumps({"id": r["reviewId"], "channel": "review",
                      "created_at": when.strftime("%Y-%m-%dT%H:%M:%SZ"),
                      "text": uc.get("text", ""), "rating": uc.get("starRating")}))
```

**Export, older reviews.** Download the reviews as a CSV file from the Play Console. Save it
under `"$raw"`, not in the repo. This page does not name the CSV columns, because nobody
verified them here. Map these columns:

- the column that holds the review text to `text`;
- the star rating to `rating`;
- the submit or last-update time to `created_at`, converted to RFC 3339;
- a review id or review link to `id`.

Use Python's `csv` module. Check the column names in your own file.

**Drop:** `authorName` and every device field (`device`, `deviceMetadata`, the Android and
app versions). A developer reply is your text, not a signal, so the script skips it.

## Zendesk (tickets)

**Export.** Use the Zendesk Ticketing API, `GET /api/v2/tickets`, or a search or view that
returns tickets. Each ticket has `id`, `subject`, `description` (the first comment),
`created_at`, `priority` (`urgent`, `high`, `normal` or `low`), `requester_id` and `url`.
Use the shared start, put your credentials in `"$raw/auth"` with `printf`, and save each
page under `"$raw"`. Follow the next-page link that your response gives until it is empty.
Compare the count with the total in Zendesk.

**Convert** (`zendesk2feedback.py`):

```python
# Zendesk tickets list result (stdin) -> feedback-jsonl without topics (stdout).
import json, sys
SEV = {"urgent": 3, "high": 3, "normal": 2, "low": 1}
for t in json.load(sys.stdin)["tickets"]:
    text = "\n".join(x for x in (t.get("subject"), t.get("description")) if x)
    print(json.dumps({"id": t["id"], "channel": "email", "created_at": t["created_at"],
                      "text": text, "severity": SEV.get(t.get("priority"))}))
```

**Notes.**

- Feedback severity stops at 3, so `urgent` and `high` both give 3.
- The script sets `channel` to `email`. If your tickets come from chat or a web form, change
  it to `chat` or `form`.
- **Drop:** `requester_id`, the requester's name and email, and `tags`. The ticket's `url`
  is an API link, so the script sends no `url`.

## Intercom (conversations)

**Export.** Use the Intercom REST API, `GET https://api.intercom.io/conversations`, with your
access token in `"$raw/auth"` (`header = "Authorization: Bearer …"`). Each conversation has
`id` (a string), `created_at` (a whole number of seconds) and `source`. The `source.body`
field holds the first message as HTML. The list response has a `pages` object. Follow its
next-page cursor until it is empty, and compare the count with Intercom.

A conversation can start with a message from your team (an outbound or operator message),
not from a customer. The `source` object says how the conversation started
(`source.delivered_as`) and who wrote the first message (the type of `source.author`). The
script keeps only conversations whose first author is a customer. The author types in
`CUSTOMER` are an assumption: check the values in your own export and change the set.

**Convert** (`intercom2feedback.py`):

```python
# Intercom conversations list result (stdin) -> feedback-jsonl without topics (stdout).
import datetime, html, json, re, sys
CUSTOMER = {"user", "lead", "contact"}  # author types of a customer; check your export
for c in json.load(sys.stdin)["conversations"]:
    src = c.get("source") or {}
    if (src.get("author") or {}).get("type") not in CUSTOMER:
        continue  # started by your team or a bot: not customer feedback
    text = html.unescape(re.sub(r"<[^>]+>", " ", src.get("body") or "")).strip()  # HTML
    when = datetime.datetime.fromtimestamp(int(c["created_at"]), datetime.timezone.utc)
    print(json.dumps({"id": c["id"], "channel": "chat",
                      "created_at": when.strftime("%Y-%m-%dT%H:%M:%SZ"), "text": text}))
```

**Drop:** `source.author` (after the type check), `contacts` and every other person field.
The script sends no severity, so intake uses 2. The values of the conversation `priority`
field differ between API versions, so map it only after you check your own export.

## How these recipes were checked

On 2026-10-12 and 2026-10-13, each transform ran on a small fixture shaped from the vendor
examples below. The output went through `intake import`, and each import printed
`problems 0`. The Jira script also ran against a host that does not answer. It exited
non-zero before the import. The fixtures are not in the repo. These checks do not prove that
your own export has the same shape.

Sources for the field names:

- Jira: the Jira Cloud REST API v3 "Issue search" group (`/rest/api/3/search/jql`, `issues`,
  `key`, `fields`, `nextPageToken`) and the repo's research note
  `docs/superpowers/specs/2026-10-06-ops-intake-formats.md` (`summary`, `priority`,
  `created`, `updated` and the priority map).
- Linear: linear.app/developers (endpoint, API key header, `identifier`, `createdAt`,
  `updatedAt`, filters, pagination) and the `Issue` type in Linear's GraphQL schema (`url`,
  `priority` values, state types), as quoted in the same research note.
- App Store Connect: Apple's "List all customer reviews for an app" page (`data`,
  `attributes`, `links.next`).
- Google Play: the Android Publisher API v3 `reviews` resource and the "Reply to Reviews"
  guide (the one-week window and the CSV download).
- Zendesk: the Ticketing API "Tickets" page.
- Intercom: the REST API "Conversation" and "List all conversations" pages (`created_at`,
  `source.body`, `source.delivered_as`, `pages`). The author type values are not verified.
