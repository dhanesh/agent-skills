# Conversion recipes: Jira, Linear, app stores and support tools

ops-intake has no dedicated reader for Jira, Linear, App Store Connect, Google Play Console,
Zendesk or Intercom. The owner decided this on 2026-10-12. Each tool comes in through one of
the two generic JSON-lines formats in `formats.md`. This page gives a short recipe for each
tool:

1. Export the records with the vendor's own API, CLI or console.
2. Convert each record to one line with a small stdlib Python script.
3. Pipe the lines into `intake import`.

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
- **Pipe tracker output.** For Jira and Linear, pipe the export through the script straight
  into `intake import`. Do not read the output first (SKILL.md step 2).
- **Keep only what the signal needs.** The scripts below drop names, nicknames, email
  addresses, requester and author fields, devices and territories. Do not add them back.
- **Keep credentials out of the repo.** Put tokens in environment variables, for example
  `$JIRA_TOKEN`. Do not write a token into a file in the repo.
- **Save the scripts outside the repo.** They are your own transforms, not part of the
  skill. A copy in the repo is a file that someone has to maintain.
- **Check your own export.** The field names below come from the vendor documents listed at
  the end of this page. A site can rename fields or add custom fields. If a field is missing,
  find the field in your export that holds the same value and change the script.
- **A failed export is a skipped source.** An error response (a bad token or a rate limit)
  has no records. The scripts read the records key directly, so an error response stops the
  script with a traceback. If curl or the script prints an error, report that source as
  skipped (SKILL.md step 1), even when the import printed `ok 0`. Use `curl -fsS`, so that
  curl fails on an HTTP error.
- **Exclude closed work.** `intake-signals-jsonl` has no state field, so the export query
  must ask only for open issues.

In the commands, `intake` means `python3 "$SKILL_DIR/assets/intake.py" --root <repo>`, as in
SKILL.md. The `--source` name must be a source in `.intake/config.json`.

## Jira (Jira Cloud REST API v3)

**Export.** Use the enhanced JQL search, `GET /rest/api/3/search/jql`. Ask for the fields
`summary`, `priority`, `created` and `updated`, and use a JQL query that excludes done
issues. The response has an `issues` array, and each issue has a `key` and a `fields`
object. To get the next page, send the `nextPageToken` from the response. You can also use
the Atlassian CLI (`acli jira workitem search --json`) or the Atlassian MCP search tool,
but their output shapes are not documented. Check that the issues still carry `key` and
`fields` before you use the script below.

```bash
curl -fsS -G -u "$JIRA_USER:$JIRA_TOKEN" "https://<site>/rest/api/3/search/jql" \
  --data-urlencode 'jql=project = OPS AND statusCategory != Done' \
  --data-urlencode 'fields=summary,priority,created,updated' \
  --data-urlencode 'maxResults=100' \
  | python3 jira2signals.py jira "https://<site>" \
  | intake import --format intake-signals-jsonl --source jira
```

**Convert** (`jira2signals.py`):

```python
# Jira REST search result (stdin) -> intake-signals-jsonl (stdout).
# argv[1] = the --source name, argv[2] = your site, for example https://acme.atlassian.net
import json, sys
SEV = {"highest": 4, "high": 3, "medium": 2, "low": 1, "lowest": 1}  # site-defined names
source, site = sys.argv[1], sys.argv[2].rstrip("/")
for issue in json.load(sys.stdin)["issues"]:
    f = issue.get("fields") or {}
    prio = ((f.get("priority") or {}).get("name") or "").lower()
    print(json.dumps({
        "source": source, "source_id": issue["key"], "kind": "issue",
        "url": "%s/browse/%s" % (site, issue["key"]),
        "title": f.get("summary") or issue["key"], "severity": SEV.get(prio, 2),
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
`title`, `url`, `priority`, `createdAt` and `updatedAt`. Filter out the `completed`,
`canceled` and `duplicate` state types. For more than one page, use `first` and `after`
with the connection's page info.

```bash
curl -fsS https://api.linear.app/graphql \
  -H "Authorization: $LINEAR_API_KEY" -H 'Content-Type: application/json' \
  -d '{"query":"{ issues(first: 100, filter: { or: [ {state: {type: {eq: \"triage\"}}}, {state: {type: {eq: \"backlog\"}}}, {state: {type: {eq: \"unstarted\"}}}, {state: {type: {eq: \"started\"}}} ] }) { nodes { identifier title url priority createdAt updatedAt } } }"}' \
  | python3 linear2signals.py linear \
  | intake import --format intake-signals-jsonl --source linear
```

**Convert** (`linear2signals.py`):

```python
# Linear GraphQL issues result (stdin) -> intake-signals-jsonl (stdout). argv[1] = --source name.
import json, sys
SEV = {1: 4, 2: 3, 3: 2, 4: 1}  # 1 Urgent, 2 High, 3 Medium, 4 Low; 0 (no priority) -> 2
source = sys.argv[1]
for n in json.load(sys.stdin)["data"]["issues"]["nodes"]:
    print(json.dumps({
        "source": source, "source_id": n["identifier"], "kind": "issue", "url": n["url"],
        "title": n["title"], "severity": SEV.get(int(n.get("priority") or 0), 2),
        "first_seen": n["createdAt"], "last_seen": n["updatedAt"]}))
```

**Notes.**

- `priority` is a number: 0 is no priority, 1 Urgent, 2 High, 3 Medium and 4 Low. It can
  come back as `3.0`, so the script converts it to a whole number.
- The query asks for no assignee, creator or description, so no personal data comes in.
- A GraphQL error response has no `data`, so the script stops with an error and intake
  imports nothing. Run the query again after you fix it.

## Feedback tools: the shared last step

App Store Connect, Play Console, Zendesk and Intercom go to `feedback-jsonl`. Each line
needs a `topic`, and you choose the topics in the session (SKILL.md, "Support and customer
feedback"). A script must not guess a topic, and a catch-all topic merges every message into
one item. So each feedback recipe has three steps:

1. Run the tool's export through its script. Write the lines to a file in a temporary
   directory outside the repo. The lines have every field except `topic`.
2. Read the lines. Write a JSON object that maps each message `id` to the topic you chose,
   for example `{"35436": "checkout-crash"}`. Reuse topics that are already in the queue.
3. Add the topics and import:

```bash
python3 add_topics.py topics.json < feedback.jsonl \
  | intake import --format feedback-jsonl --source support
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

Delete the temporary directory after the import. The lines hold customer text.

## App Store Connect (customer reviews)

**Export.** Use the App Store Connect API endpoint
`GET /v1/apps/{id}/customerReviews`, with a signed App Store Connect API token. Each review
in `data` has an `id` and `attributes`: `rating`, `title`, `body`, `reviewerNickname`,
`createdDate` and `territory`. Follow `links.next` for more pages.

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
`starRating` and `lastModified` (`seconds` and `nanos`).

**Convert** (`play2feedback.py`):

```python
# Google Play Developer API reviews.list result (stdin) -> feedback-jsonl without topics.
import datetime, json, sys
for r in json.load(sys.stdin).get("reviews", []):
    uc = next((c["userComment"] for c in r.get("comments", []) if "userComment" in c), None)
    if uc is None:
        continue
    secs = int(uc["lastModified"]["seconds"])  # the last change, not the first post
    when = datetime.datetime.fromtimestamp(secs, datetime.timezone.utc)
    print(json.dumps({"id": r["reviewId"], "channel": "review",
                      "created_at": when.strftime("%Y-%m-%dT%H:%M:%SZ"),
                      "text": uc.get("text", ""), "rating": uc.get("starRating")}))
```

**Export, older reviews.** Download the reviews as a CSV file from the Play Console. This
page does not name the CSV columns, because nobody verified them here. Map these columns:

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
access token. Each conversation has `id` (a string), `created_at` (a whole number of
seconds) and `source`. The `source.body` field holds the first message as HTML.

**Convert** (`intercom2feedback.py`):

```python
# Intercom conversations list result (stdin) -> feedback-jsonl without topics (stdout).
import datetime, html, json, re, sys
for c in json.load(sys.stdin)["conversations"]:
    body = (c.get("source") or {}).get("body") or ""
    text = html.unescape(re.sub(r"<[^>]+>", " ", body)).strip()  # body is HTML
    when = datetime.datetime.fromtimestamp(int(c["created_at"]), datetime.timezone.utc)
    print(json.dumps({"id": c["id"], "channel": "chat",
                      "created_at": when.strftime("%Y-%m-%dT%H:%M:%SZ"), "text": text}))
```

**Drop:** `source.author`, `contacts` and every other person field. The script sends no
severity, so intake uses 2. The values of the conversation `priority` field differ between
API versions, so map it only after you check your own export.

## How these recipes were checked

On 2026-10-12, each script ran on a small fixture shaped from the vendor examples below. The
output went through `intake import`, and each import printed `problems 0`. The fixtures are
not in the repo. These checks do not prove that your own export has the same shape.

Sources for the field names:

- Jira: the Jira Cloud REST API v3 "Issue search" group (`/rest/api/3/search/jql`, `issues`,
  `key`, `fields`, `nextPageToken`) and the repo's research note
  `docs/superpowers/specs/2026-10-06-ops-intake-formats.md` (`summary`, `priority`,
  `created`, `updated` and the priority map).
- Linear: linear.app/developers (endpoint, API key header, `identifier`, `createdAt`,
  `updatedAt`, filters) and the `Issue` type in Linear's GraphQL schema (`url`, `priority`
  values, state types), as quoted in the same research note.
- App Store Connect: Apple's "List all customer reviews for an app" page.
- Google Play: the Android Publisher API v3 `reviews` resource and the "Reply to Reviews"
  guide (the one-week window and the CSV download).
- Zendesk: the Ticketing API "Tickets" page.
- Intercom: the REST API "Conversation" and "List all conversations" pages.
