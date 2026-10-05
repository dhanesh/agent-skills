# ops-intake: source output formats (research for the adapters)

Date: 2026-10-06. Companion to [`2026-10-06-ops-intake-design.md`](2026-10-06-ops-intake-design.md), section 1.2a.

This note records the exact output of each source that intake reads. An engineer can write a strict adapter from it. Every field cites its source. Where a source is silent or unclear, the note says so.

## How to read the provenance tags

Each fact carries one tag:

- **[docs]**: official vendor documentation.
- **[src]**: official source code at a named tag or commit.
- **[schema]**: an official machine-readable schema (OpenAPI, GraphQL SDL, live GraphQL introspection).
- **[observed]**: a real command run by this research, on a public repo, on 2026-10-05/06 (UTC). gh 2.98.0.
- **[vendor-issue]**: a report on the vendor's own GitHub issue tracker. It is not official. The note gives the author's association (MEMBER, COLLABORATOR, NONE).
- **[community]**: third-party code or skill files that use the tool. Weak evidence.
- **[inferred]**: our reading. No source states it.

---

## 1. GitHub CLI: `gh issue list --json` (format `gh-issues-json`)

### Command

```
gh issue list --repo OWNER/REPO --state all --limit 100 \
  --json number,title,body,labels,state,stateReason,createdAt,updatedAt,closedAt,url,author
```

- Default `--limit` is 30. Default `--state` is `open`. Values: `open|closed|all`. [docs] [gh issue list manual][gh-issue-list]; [src] `pkg/cmd/issue/list/list.go` lines 112–113 at v2.98.0.
- `--json` with no field list prints the valid field names. [docs] [gh formatting][gh-formatting]

### Top-level shape

- One JSON **array** of objects. Each object has only the fields named in `--json`. [src] `list.go` line 216 passes the slice `listResult.Issues` to the exporter; `pkg/cmdutil/json_flags.go` lines 265–286 turn a slice into an array and call `ExportData` per item.
- No result gives `[]`, not empty output. [observed] (`gh run list ... -s in_progress` on cli/cli printed `[]`; issue list uses the same exporter.) [inferred for issues]
- Piped output is compact, one line. A terminal gets pretty output. [observed]; [docs] [gh formatting][gh-formatting] (says this for `--jq`).
- The list holds issues only, not pull requests. [inferred] from the GraphQL `issues` connection; not stated in the manual.

### All valid `--json` fields (gh 2.98.0)

`assignees, author, blockedBy, blocking, body, closed, closedAt, closedByPullRequestsReferences, comments, createdAt, id, isPinned, issueType, labels, milestone, number, parent, projectCards, projectItems, reactionGroups, state, stateReason, subIssues, subIssuesSummary, title, updatedAt, url`

Source: [docs] [gh issue list manual][gh-issue-list]; [observed] `gh issue list --json` (no args) printed the same list.

### Fields intake uses

| Field | JSON type | Meaning | Source |
|---|---|---|---|
| `number` | integer | Issue number in the repo. | [src] `api/queries_issue.go` `Issue.Number int` |
| `title` | string | Issue title. | [src] `Issue.Title string` |
| `body` | string | Issue body, raw Markdown. Can be `""`. | [src] `Issue.Body string`; Markdown is [inferred] from GitHub |
| `labels` | array of `{id, name, description, color}` (all strings) | Labels on the issue. `color` is hex without `#`. | [src] `api/export_pr.go` `case "labels": issue.Labels.Nodes`; `api/queries_repo.go` `IssueLabel` struct; [observed] |
| `state` | string, `OPEN` or `CLOSED` (upper case) | Open or closed. | [schema] GraphQL `IssueState` enum, introspected 2026-10-06; [observed] `"state":"OPEN"` |
| `stateReason` | string: `COMPLETED`, `NOT_PLANNED`, `DUPLICATE`, `REOPENED`, or `""` | Why it closed. | [schema] `IssueStateReason` enum, introspected 2026-10-06. Empty string when GraphQL gives null is [inferred] from Go `string` decoding. |
| `createdAt` | string, RFC 3339 UTC (`2026-10-05T08:20:45Z`) | Creation time. | [src] `CreatedAt time.Time` (Go marshals RFC 3339); [observed] |
| `updatedAt` | string, RFC 3339 UTC | Last update time. | [src] `UpdatedAt time.Time`; [observed] |
| `closedAt` | string RFC 3339, or `null` | Close time. | [src] `ClosedAt *time.Time` (pointer, so `null` when open) |
| `url` | string | Browser URL, `https://github.com/OWNER/REPO/issues/N`. | [src] `Issue.URL`; [observed] |
| `author` | object | See below. | [src] `api/queries_issue.go` `Author.MarshalJSON` |

`author` has two shapes. [src] `Author.MarshalJSON`, lines 290–303:

- A user: `{"id": "...", "is_bot": false, "login": "...", "name": "..."}`. `name` can be `""`. [observed]
- A bot or app (empty id): `{"is_bot": true, "login": "app/<login>"}`. It has no `id` and no `name`.

### Pagination

- None to handle. gh pages internally up to `--limit`. [src] `issueList(..., limit)` in `list.go`. The output does not say if more issues exist. [src] only `listResult.Issues` is written; `TotalCount` is dropped.
- So an adapter cannot detect truncation. Set `--limit` above the expected count. [inferred]

### Known variability

- With `--search`, `--label`, `--milestone` or `--type`, gh uses the search API instead of the repo issues query. [src] `list.go` lines 235–245. The output struct is the same `api.Issue`. [src] Search results can lag. [inferred]
- New fields appear across gh versions (e.g. `blockedBy`, `subIssues`). Pin the field list in the command, so new fields do not change the output. [inferred]

### Confidence

**Documented** (manual for names, source for types, observed sample). A real sample must confirm: `stateReason` value for an open issue (`""` vs `null`), and a bot `author`.

---

## 2. GitHub CLI: `gh run list --json` (format `gh-runs-json`)

### Command

```
gh run list --repo OWNER/REPO --branch main --limit 100 \
  --json databaseId,workflowName,name,displayTitle,headBranch,headSha,event,status,conclusion,createdAt,startedAt,updatedAt,url,attempt
```

- Default `--limit` is 20. [docs] [gh run list manual][gh-run-list]; [src] `pkg/cmd/run/list/list.go` `defaultLimit = 20`.
- Filters: `--branch`, `--workflow`, `--event`, `--status`, `--user`, `--commit`, `--created`, `--all`. [docs] [gh run list manual][gh-run-list]

### Top-level shape

- One JSON **array** of run objects. [src] `list.go` line 160 writes the `runs` slice; [observed].
- No result gives `[]`. [observed] (`gh run list -R cli/cli -s in_progress` printed `[]`.)

### All valid `--json` fields (gh 2.98.0)

`attempt, conclusion, createdAt, databaseId, displayTitle, event, headBranch, headSha, name, number, startedAt, status, updatedAt, url, workflowDatabaseId, workflowName`

Source: [docs] [gh run list manual][gh-run-list]; [src] `pkg/cmd/run/shared/shared.go` `RunFields`, lines 70–87. **`jobs` is not in this list.** It is only in `SingleRunFields` (line 89), so only `gh run view <id> --json jobs` gives it.

### Fields intake uses

| Field | JSON type | Meaning | Source |
|---|---|---|---|
| `databaseId` | integer (int64) | Run id. Same as REST `id`. | [src] `ExportData` `case "databaseId": r.ID` |
| `workflowName` | string | Name of the workflow file's workflow. | [src] `ExportData` `r.WorkflowName()` |
| `name` | string | REST `name`. gh's own comment says "the semantics of this field are unclear". Observed equal to `workflowName`. | [src] `shared.go` line 92; [observed] |
| `displayTitle` | string | Run title (often the commit or PR title). | [src] `DisplayTitle` `json:"display_title"` |
| `headBranch` | string | Branch the run used. | [src] `HeadBranch` |
| `headSha` | string, 40 hex | Commit the run used. | [src] `HeadSha`; [observed] |
| `event` | string | Trigger, e.g. `push`, `pull_request`, `schedule`. | [src] `Event string`; [observed] `"schedule"` |
| `status` | string | See values below. | [src] `shared.go` lines 26–31 |
| `conclusion` | string | See values below. **`""` while the run is not completed.** | [src] `Conclusion` is a Go `string`; [observed] `"conclusion":""` with `"status":"in_progress"` and `"queued"` (microsoft/vscode, 2026-10-05) |
| `createdAt` | string, RFC 3339 UTC | Run creation time. | [src] `time.Time`; [observed] |
| `startedAt` | string, RFC 3339 UTC | REST `run_started_at`. Can be `0001-01-01T00:00:00Z` if absent. | [src] `StartedAt time.Time`; zero value is [inferred] from Go |
| `updatedAt` | string, RFC 3339 UTC | Last update. For a completed run it is close to the end time. | [src] `shared.go` `Duration()` uses it as the end time |
| `url` | string | `https://github.com/OWNER/REPO/actions/runs/<id>`. | [src] `URL` `json:"html_url"`; [observed] |
| `attempt` | integer | Re-run attempt, starts at 1. | [src] `Attempt uint64` `json:"run_attempt"`; [observed] |

### `status` and `conclusion` values

- gh lists **status**: `queued`, `completed`, `in_progress`, `requested`, `waiting`, `pending`. [src] `shared.go` lines 26–31.
- gh lists **conclusion**: `action_required`, `cancelled`, `failure`, `neutral`, `skipped`, `stale`, `startup_failure`, `success`, `timed_out`. [src] lines 34–42.
- The `--status` filter takes the union of both lists. [docs] [gh run list manual][gh-run-list].
- The REST docs say `status` and `conclusion` are "string or null" and give **no enum** for the response. [docs] [REST workflow runs][rest-runs]. So the lists above are gh's lists, not a GitHub guarantee. The adapter should keep unknown values as data and report them, not crash. [inferred]
- A failure signal is `status == "completed"` and `conclusion` in {`failure`, `timed_out`, `startup_failure`}. Whether `cancelled` counts is a policy choice. [inferred]

### Pagination

- gh pages internally up to `--limit`. The output does not say if more runs exist. [src] `list.go` writes only `runs`.

### Known variability

- `name` vs `workflowName` vs `displayTitle` differ by workflow. Use `workflowName` for identity. [inferred]
- gh fills `workflowName` with an extra lookup. Disabled workflows need `--all`. [docs] manual flag text.
- Nothing in the output says which branch is the default branch. [src] no such field.

### Confidence

**Documented** (manual for names, source for values and types, observed samples). A real sample must confirm: a `failure` run, a re-run (`attempt` > 1), and `startedAt` on a run that never started.

---

## 3. Atlassian Rovo MCP Server: Jira search (format `jira-mcp`)

### Tool and endpoint

- Server URL: `https://mcp.atlassian.com/v2/mcp` (recommended). `https://mcp.atlassian.com/v1/mcp` and `/v1/mcp/authv2` still work, and "existing v1 connections automatically start to expose and use v2 tools". [src] [atlassian-mcp-server README][atl-readme], lines 61 and 289–295; `server.json`.
- Search tool: **`searchJiraIssuesUsingJql`**, "Search Jira work items using JQL", group `search_jira`. [docs] [Supported tools][atl-tools]
- Get tool: **`getJiraIssue`**, "Get a Jira work item by ID or key", group `read_jira`. [docs] [Supported tools][atl-tools]
- v2 shows a few "Primary" tools directly. Others are reached through a discover/execute pair (`executeRead`/`execute`). `?tools=all` gives a flat list. [src] [README][atl-readme] "How the server exposes tools"; [src] `skills/triage-issue/SKILL.md` ("not a primary tool, so run it through `execute`").

### Minimal invocation

The official skills call it like this. [src] `skills/triage-issue/SKILL.md` lines 69–74; `skills/generate-status-report/SKILL.md` line 88.

```
searchJiraIssuesUsingJql(
  cloudId="...",
  jql='project = "PROJ" AND statusCategory != Done ORDER BY updated DESC',
  fields=["summary","status","priority","labels","created","updated","description"],
  maxResults=50
)
```

- Parameters seen in official skills: `cloudId`, `jql`, `fields`, `maxResults`, `nextPageToken`. [src] skills files.
- `responseContentFormat` (`"markdown"` or `"adf"`) exists on `getJiraIssue`. [vendor-issue] [#189][atl-189] (NONE) quotes its schema text.
- **The official docs do not list parameters, types, defaults or the result shape.** [docs] [Supported tools][atl-tools] is silent. The tool input schema is only visible through MCP `tools/list`.

### Top-level shape

An MCP `tools/call` result has two channels: `content[]` (text blocks) and optional `structuredContent` (JSON). [inferred from MCP spec]

**It is not the Jira REST v3 search response.** REST v3 gives `{issues: [...], isLast, nextPageToken}`. The MCP structured channel gives `issues.nodes[]`:

```json
{
  "issues": {
    "nodes": [ { "...": "issue fields" } ],
    "webUrl": "https://<site>.atlassian.net/issues?jql=<encoded-jql>",
    "pageInfo": { "hasNextPage": true, "endCursor": "<nextPageToken>" }
  }
}
```

Source: [vendor-issue] [#118][atl-118], comment by `puneet-atlassian` (association NONE on GitHub, so not verifiably staff), 2026-06-04. The issue was closed by `iosamaatlassian` (COLLABORATOR). A later commenter (NONE, 2026-08-20) says "This is definitely not fixed".

- Before that change, the envelope had `issues.totalCount` (= page size, not the true total), `nodes` and `webUrl`. [vendor-issue] [#118][atl-118] (NONE). That comment says `totalCount` was removed.
- Each node has a per-issue `webUrl`, `https://<site>.atlassian.net/browse/<KEY>`. [vendor-issue] [#213][atl-213] (NONE).
- **What `content[].text` holds is not documented.** #208 measures "bytes on the JSON text as delivered" and lists fields such as `summary`, `status`, `description`, `issuetype`, `project`, `assignee`. So the text block looks like JSON with REST-like field objects. [vendor-issue] [#208][atl-208] (NONE); shape is [inferred].
- **`structuredContent` can be missing.** #213 reports the server sends it only when the MCP `clientInfo.name` is a known client (e.g. `claude-code`, `cursor`). Other names get only the text block, which has no `webUrl`. [vendor-issue] [#213][atl-213] (NONE, open).

### Field table (per issue node)

The node keys are not documented. #118, #189, #208 and #213 refer to `key`, `fields.<name>` and `webUrl`. The `fields` values follow Jira REST v3 shapes, with the exceptions listed. Treat every row as **[vendor-issue]/[inferred]** for the MCP, and **[schema]** only for the REST shape it copies.

| Field | REST v3 type | Meaning | Source |
|---|---|---|---|
| `key` | string, e.g. `PROJ-123` | Issue key. | [schema] REST `IssueBean.key`; MCP use [vendor-issue] #213 |
| `id` | string | Numeric id as string. | [schema] `IssueBean.id` |
| `webUrl` | string | Browse URL. Only in `structuredContent`. | [vendor-issue] #213 |
| `self` | string (URI) | REST API URL, not a browser URL. | [schema] `IssueBean.self`; [vendor-issue] #213 |
| `fields.summary` | string | Title. | [schema] REST examples |
| `fields.status.name` | string | Status name (site-defined). | [schema] `StatusDetails.name` |
| `fields.status.statusCategory.key` | string | Category key, e.g. `new`, `indeterminate`, `done`. | [schema] `StatusCategory.key`; values [vendor-issue] #17 shows `"key":"new"`; full list [inferred] |
| `fields.priority.name` | string | Priority name (site-defined, e.g. Highest/High/Medium/Low/Lowest). | [schema] `Priority.name`; names [inferred] |
| `fields.labels` | array of strings | Labels. | [inferred] from REST behaviour; the OpenAPI types `fields` as a free map |
| `fields.created` / `fields.updated` | string, ISO 8601 with offset like `2023-06-24T19:24:50.000+0000` | Times. | [docs] [REST v3 intro][jira-intro] "Timestamps": ISO 8601 in the system default time zone; format from [schema] example |
| `fields.description` | **Markdown string** through MCP (REST v3 gives an ADF object) | Body. | [vendor-issue] [#189][atl-189]: markdown even with `responseContentFormat:"adf"`; [docs] [REST v3 intro][jira-intro] says v3 uses ADF for `description` |

Timestamp warning: the offset is `+0000`, with no colon. A strict RFC 3339 parser rejects it. The adapter must accept `±HHMM`. [schema] example; [inferred] parser note.

### Pagination

- Input `nextPageToken`; output `issues.pageInfo.endCursor` and `hasNextPage`, in `structuredContent` only. [vendor-issue] [#118][atl-118].
- `maxResults` is capped at 100 per call. [vendor-issue] #118 says "(max 100)". The REST endpoint default is 50. [schema] `/rest/api/3/search/jql` `maxResults` default 50.
- No true total count. [vendor-issue] #118.
- Without `structuredContent` (see #213), there may be no page cursor at all. [inferred]

### Known variability

- v1 vs v2 endpoint; v1 tools now map to v2. [src] README.
- Primary tool vs `executeRead` give different views. On v2, primary `getJiraIssue` gives a "compact view" that drops `parent`, `issuetype`, `labels` even when asked. [vendor-issue] [#256][atl-256] (NONE, open; a Jira MCP PM could not reproduce). **`labels` may be missing.**
- `fields` does not limit output. `description`, `issuetype`, `project`, `assignee` always come back. [vendor-issue] [#208][atl-208] (NONE, open).
- `description` is Markdown, not ADF. [vendor-issue] #189 (open).
- `getJiraIssue` drops `startAt`/`maxResults`/`total` from `fields.comment`. [vendor-issue] [#230][atl-230].
- Scope names changed. Search results show `read:jira-work`; the current page shows `search:jira:agent-interface` and others. [docs] [Supported tools][atl-tools].

### The REST v3 search endpoint, for reference

`GET /rest/api/3/search/jql`. Response `SearchAndReconcileResults`: `issues` (array of `IssueBean`), `isLast` (boolean), `nextPageToken` (string; absent on the last page), `names`, `schema`, `warnings` (experimental). Default `fields` is `id` only. [schema] [Jira OpenAPI v3][jira-openapi]. The official example is not a safe fixture: it has `"updated": 1` and a plain-string `description`. [schema] example.

### Confidence

**Undocumented** for the result shape. Tool names are documented. A real sample must confirm: the raw JSON-RPC `result` object (`content`, `structuredContent`, `isError`) from the owner's client, for one page with `hasNextPage: true` and the last page. Check `key`, `webUrl`, `fields.labels`, `fields.priority`, the `description` type, and the timestamp format. Note the client name that the session sent.

---

## 4. Atlassian CLI `acli`: Jira work item search (format `jira-acli`)

### Command

```
acli jira workitem search --jql "project = PROJ AND statusCategory != Done" \
  --fields "key,summary,status,priority,labels,created,updated" --limit 100 --json
```

### Flags

[docs] [acli jira workitem search][acli-search] (page "Last updated Oct 3, 2024"):

| Flag | Meaning (verbatim where quoted) |
|---|---|
| `-j, --jql string` | "JQL query to search for work items" |
| `--filter string` | "Filter ID of work items to be searched" |
| `-f, --fields string` | "Comma-separated list of fields to display in the output (default "issuetype,key,assignee,priority,status,summary")" |
| `-l, --limit int` | "Maximum number of work items to fetch" (no default given) |
| `--paginate` | "Fetch all work items by paginating through the results" |
| `--json` | "Generate a JSON output" |
| `--csv` | "Generate a CSV output" |
| `--count` | "Number of work items in the search" |
| `-w, --web` | "Search for work items in the web browser" |

`acli jira workitem view KEY --json --fields ...` exists. Its `--fields` takes `*all`, `*navigable` and `-field`, default `key,issuetype,summary,status,assignee,description`. [docs] [acli jira workitem view][acli-view].

### Top-level shape

- **The docs say nothing about the JSON shape.** [docs] [acli-search].
- Community scripts treat it as a top-level **array** of `{key, fields: {...}}`. They read `.[] | .key`, `.fields.summary` and `.fields.status.name`. [community] GitHub code search, 2026-10-06, e.g. `jq -r '.[] | "\(.key)\t\(.fields.status.name)\t\(.fields.summary)"'`.
- That matches the REST `IssueBean` shape. [inferred]

### Field table

All rows are **[community]/[inferred]**. They assume REST v3 `IssueBean` shapes (see section 3).

| Field | Likely type | Source |
|---|---|---|
| `key` | string | [community] |
| `id`, `self` | string | [inferred] from REST |
| `fields.summary` | string | [community] |
| `fields.status.name` | string | [community] |
| `fields.priority.name` | string | [inferred] |
| `fields.labels` | array of strings | [inferred] |
| `fields.created`, `fields.updated` | string, `...+0000` | [inferred] |
| `fields.description` | ADF object or text: **unknown** | [inferred] |

### Pagination

- `--limit` caps the count. `--paginate` fetches all. **How they combine is not documented.** [docs] silent.
- Whether the JSON carries `isLast` or `nextPageToken` is not documented. Community use suggests a bare array, so no. [community]

### Known variability

- `--fields` is described as fields "to display". It is not clear that JSON output honours it. [docs] wording.
- The docs page is two years old. acli releases may have changed the output. [docs] date.

### Confidence

**Undocumented** for output. Flags are documented. A real sample must confirm: array vs object at the top; `key` and `fields` placement; `description` type; `labels`; timestamp format; output with `--paginate` and with `--limit`; and the empty result (`[]`, `null` or nothing).

---

## 5. Linear MCP server: issue list (format `linear-mcp`)

### Tool and endpoint

- Server URL: `https://mcp.linear.app/mcp` (Streamable HTTP). Read-only: `https://mcp.linear.app/mcp/readonly`. `/sse` is deprecated. Auth: OAuth 2.1 with dynamic client registration, or a bearer token or API key. [docs] [Linear MCP docs][linear-mcp]; [docs] [changelog 2026-02-05][linear-cl].
- The endpoint returns `401` with `resource_metadata=".../.well-known/oauth-protected-resource/mcp"` and `scope="read write"` when there is no token. [observed] 2026-10-05.
- **Linear's docs list no tool names.** [docs] [linear-mcp].
- Tool names in use: `list_issues`, `get_issue`. [community] many Claude Code configs (`mcp__linear__list_issues`, `mcp__linear__get_issue`). A third-party directory also lists `list_my_issues`, `create_issue`, `update_issue`, `list_issue_statuses`, `list_issue_labels`, and others. [community] [remote-mcp.com][remote-mcp]. Recent configs use `save_issue` and `save_project` instead of create/update. [community]. So **tool names are unconfirmed and change over time.**

### Minimal invocation

```
list_issues(team="ENG", state="...", limit=50)
get_issue(id="ENG-123")
```

Parameter names seen in community use: `query`, `team`/`teamId`, `assignee` (`"me"`), `state`, `label`, `project`, `parentId`, `includeArchived`, `limit`, `createdAt`. `get_issue` takes `id` and accepts the identifier. [community]. **None of these is documented by Linear.**

### Top-level shape

- **Not documented.** JSON vs text, and `structuredContent` vs `content[].text`, are unknown. [docs] silent.
- No real result sample was found in public sources. One "sample" in a public repo is a hand-written UI mock, so this note does not use it.

### Field table (from the GraphQL `Issue` type)

The MCP result probably carries some of these. Which ones, and under which names, is **[inferred]**. Types are **[schema]** from [`packages/sdk/src/schema.graphql`][linear-schema] at commit `2a3ac4c` (2026-09-28).

| GraphQL field | Type | Meaning (schema text) |
|---|---|---|
| `id` | `ID!` (UUID string) | "The unique identifier of the entity." |
| `identifier` | `String!` | "Issue's human readable identifier (e.g. ENG-123)." |
| `number` | `Float!` | Team-scoped number. |
| `title` | `String!` | "The issue's title." |
| `description` | `String` (nullable) | "The issue's description in markdown format." |
| `priority` | `Float!` | "0 = No priority, 1 = Urgent, 2 = High, 3 = Medium, 4 = Low." |
| `priorityLabel` | `String!` | "Label for the priority." |
| `state` | `WorkflowState!` | Current status. `state.name` is the display name. `state.type` is one of `triage`, `backlog`, `unstarted`, `started`, `completed`, `canceled`, `duplicate`. |
| `labels` | `IssueLabelConnection!` (a connection, `nodes[].name`) | Labels. The MCP may flatten this to names. [inferred] |
| `url` | `String!` | "Issue URL." |
| `createdAt` | `DateTime!` | ISO 8601. |
| `updatedAt` | `DateTime!` | "The last time at which the entity was meaningfully updated." |
| `completedAt`, `canceledAt` | `DateTime` (nullable) | Times of moving to completed or canceled. |

Note: priority 3 is **Medium**, not "Normal". `priority` is a `Float`, so a parser should accept `3` and `3.0`. [schema]

### Pagination

- **Not documented** for the MCP tool. GraphQL uses connections (`first`/`after`, `pageInfo`). [schema]. Whether `list_issues` takes a cursor is unknown. [inferred]

### Confidence

**Undocumented** (endpoint and auth are documented; tools and result are not). A real sample must confirm: the raw JSON-RPC `result` from `tools/list` (tool names and input schemas) and from one `list_issues` call (`content`, `structuredContent`). Check: JSON or Markdown text; the priority form (number or label); the state form (name, type or both); the labels form; the URL; timestamps; and how a second page is requested.

---

## 6. Mapping to the intake signal

Target shape, from the design spec section 1.3: `{source, source_id, url, kind, title, severity 1–4, first_seen, last_seen, count, evidence[{text, source, source_id, fetched_at}]}`.

Tags here: **D** = the field exists as documented or in source. **I** = the mapping is our choice or rests on an undocumented shape.

### 6.1 Gaps between the design spec and the real formats

These change the spec, not only the adapters.

1. **CI `source_id` cannot include the job.** The spec keys CI items on `workflow/job/branch`. `gh run list` has no job field. `jobs` is only in `gh run view <id> --json jobs`. [src] `shared.go` lines 70–89. Options:
   - (a) Key on `workflowName/headBranch`. One item per failing workflow. Works with one `gh run list` call.
   - (b) Add a second format, e.g. `gh-run-view-json`, from `gh run view <id> --json jobs,...` per failing run. The agent runs one more command per failure.
2. **`gh-runs-json` needs `status` and `event`.** Without `status`, `conclusion: ""` (still running) looks like bad data. `event` separates `push` from `pull_request` and `schedule`.
3. **The default branch is not in the output.** "Failing on the default branch" needs `--branch <default>` in the command, or a config value the adapter checks against `headBranch`.
4. **`gh-issues-json` needs `createdAt` and `author`.** `createdAt` gives `first_seen`. `author` lets intake mark bot-filed issues.
5. **MCP adapters must name their channel.** For `jira-mcp`, `structuredContent` is the only channel with `webUrl` and `pageInfo`, and it may be withheld (#213). The adapter should accept `structuredContent` first, and refuse with a clear problem if only text is present, until a real text sample is in hand.

### 6.2 Per-format mapping

| Signal field | `gh-issues-json` | `gh-runs-json` | `jira-mcp` | `jira-acli` | `linear-mcp` |
|---|---|---|---|---|---|
| `source_id` | `OWNER/REPO#<number>` (D field; I format) | `<workflowName>/<headBranch>` (D fields; I key, see 6.1) | `key` (I) | `key` (I) | `identifier` (I) |
| `title` | `title` (D) | `"<workflowName> failed on <headBranch>"` built from fields (I) | `fields.summary` (I) | `fields.summary` (I) | `title` (I) |
| `url` | `url` (D) | `url` of the latest failing run (D field; I choice) | node `webUrl`; else none, because `self` is an API URL (I) | none in output; build `https://<site>/browse/<key>` from config (I) | `url` (I) |
| `kind` | `issue` | `ci` | `issue` | `issue` | `issue` |
| `severity` | label map from config, e.g. `bug`+`p0` → 4; default 2 (I) | 3 for `conclusion` in {`failure`,`timed_out`,`startup_failure`} on the default branch (spec rule; D values) | `fields.priority.name`: Highest→4, High→3, Medium→2, Low/Lowest→1 (I; names are site-defined) | same as `jira-mcp` (I) | `priority`: 1→4, 2→3, 3→2, 4→1, 0→2 "unknown" (D values; I map) |
| `first_seen` | `createdAt` (D) | `createdAt` of the oldest failing run seen (D field; I rule) | `fields.created` (I) | `fields.created` (I) | `createdAt` (I) |
| `last_seen` | `updatedAt` (D) | `updatedAt` of the newest failing run (D field; I rule) | `fields.updated` (I) | `fields.updated` (I) | `updatedAt` (I) |
| evidence `text` | `body` (D), truncated | `displayTitle` + `event` + `headSha` + `conclusion` (D fields; I text) | `fields.description` (Markdown, I) | `fields.description` (type unknown, I) | `description` (Markdown per schema; I) |
| skip rule | `state == "CLOSED"` (D values) | `status != "completed"` or `conclusion` in {`success`,`skipped`,`neutral`} (D values; I rule) | `fields.status.statusCategory.key == "done"` (I) | same (I) | `state.type` in {`completed`,`canceled`,`duplicate`} (D values; I rule) |

Notes:
- Severity maps from issue priority are all inferred. Jira priority names are per site. The design spec already says the config can override the defaults.
- Every timestamp parser must accept both `Z` (GitHub, Linear) and `+0000` (Jira). [D formats; I parser rule]
- Evidence text is untrusted. It stays quoted, per the design spec section 2 (trust boundary).

---

## Sources

- [gh-issue-list]: https://cli.github.com/manual/gh_issue_list
- [gh-run-list]: https://cli.github.com/manual/gh_run_list
- [gh-formatting]: https://cli.github.com/manual/gh_help_formatting
- cli/cli source at v2.98.0:
  - https://github.com/cli/cli/blob/v2.98.0/pkg/cmd/run/shared/shared.go
  - https://github.com/cli/cli/blob/v2.98.0/pkg/cmd/run/list/list.go
  - https://github.com/cli/cli/blob/v2.98.0/pkg/cmd/issue/list/list.go
  - https://github.com/cli/cli/blob/v2.98.0/api/queries_issue.go
  - https://github.com/cli/cli/blob/v2.98.0/api/queries_repo.go
  - https://github.com/cli/cli/blob/v2.98.0/api/export_pr.go
  - https://github.com/cli/cli/blob/v2.98.0/pkg/cmdutil/json_flags.go
- [rest-runs]: https://docs.github.com/en/rest/actions/workflow-runs?apiVersion=2022-11-28#list-workflow-runs-for-a-repository
- GitHub GraphQL enums `IssueState`, `IssueStateReason`: https://docs.github.com/en/graphql/reference/enums#issuestate (values confirmed by introspection, 2026-10-06)
- [atl-tools]: https://support.atlassian.com/atlassian-rovo-mcp-server/docs/supported-tools/
- Rovo search and fetch (no format detail): https://support.atlassian.com/atlassian-ai-gateway/docs/use-rovo-search-and-fetch-in-the-atlassian-remote-mcp-server/
- [atl-readme]: https://github.com/atlassian/atlassian-mcp-server/blob/main/README.md (and `server.json`, `skills/triage-issue/SKILL.md`, `skills/generate-status-report/SKILL.md`)
- [atl-118]: https://github.com/atlassian/atlassian-mcp-server/issues/118
- [atl-189]: https://github.com/atlassian/atlassian-mcp-server/issues/189
- [atl-208]: https://github.com/atlassian/atlassian-mcp-server/issues/208
- [atl-213]: https://github.com/atlassian/atlassian-mcp-server/issues/213
- [atl-230]: https://github.com/atlassian/atlassian-mcp-server/issues/230
- [atl-256]: https://github.com/atlassian/atlassian-mcp-server/issues/256
- Also #17 (field verbosity): https://github.com/atlassian/atlassian-mcp-server/issues/17
- [jira-intro]: https://developer.atlassian.com/cloud/jira/platform/rest/v3/intro/
- Jira search endpoint: https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-issue-search/#api-rest-api-3-search-jql-get
- [jira-openapi]: https://developer.atlassian.com/cloud/jira/platform/swagger-v3.v3.json
- [acli-search]: https://developer.atlassian.com/cloud/acli/reference/commands/jira-workitem-search/
- [acli-view]: https://developer.atlassian.com/cloud/acli/reference/commands/jira-workitem-view/
- [linear-mcp]: https://linear.app/docs/mcp
- [linear-cl]: https://linear.app/changelog/2026-02-05-linear-mcp-for-product-management
- [linear-schema]: https://github.com/linear/linear/blob/2a3ac4cfd28af84b98c439efa74b1e32b2ce985b/packages/sdk/src/schema.graphql
- [remote-mcp]: https://www.remote-mcp.com/servers/linear (third-party directory)

[gh-issue-list]: https://cli.github.com/manual/gh_issue_list
[gh-run-list]: https://cli.github.com/manual/gh_run_list
[gh-formatting]: https://cli.github.com/manual/gh_help_formatting
[rest-runs]: https://docs.github.com/en/rest/actions/workflow-runs?apiVersion=2022-11-28#list-workflow-runs-for-a-repository
[atl-tools]: https://support.atlassian.com/atlassian-rovo-mcp-server/docs/supported-tools/
[atl-readme]: https://github.com/atlassian/atlassian-mcp-server/blob/main/README.md
[atl-118]: https://github.com/atlassian/atlassian-mcp-server/issues/118
[atl-189]: https://github.com/atlassian/atlassian-mcp-server/issues/189
[atl-208]: https://github.com/atlassian/atlassian-mcp-server/issues/208
[atl-213]: https://github.com/atlassian/atlassian-mcp-server/issues/213
[atl-230]: https://github.com/atlassian/atlassian-mcp-server/issues/230
[atl-256]: https://github.com/atlassian/atlassian-mcp-server/issues/256
[jira-intro]: https://developer.atlassian.com/cloud/jira/platform/rest/v3/intro/
[jira-openapi]: https://developer.atlassian.com/cloud/jira/platform/swagger-v3.v3.json
[acli-search]: https://developer.atlassian.com/cloud/acli/reference/commands/jira-workitem-search/
[acli-view]: https://developer.atlassian.com/cloud/acli/reference/commands/jira-workitem-view/
[linear-mcp]: https://linear.app/docs/mcp
[linear-cl]: https://linear.app/changelog/2026-02-05-linear-mcp-for-product-management
[linear-schema]: https://github.com/linear/linear/blob/2a3ac4cfd28af84b98c439efa74b1e32b2ce985b/packages/sdk/src/schema.graphql
[remote-mcp]: https://www.remote-mcp.com/servers/linear
