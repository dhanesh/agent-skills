# The two interviews (and the third, smaller one)

Capabilities and services are **derived**; teams and dependencies are **elicited**. Code
can prove what a service exposes. It cannot prove who owns a team, what a delay would cost,
or what the fallback is. Those need a conversation — and the skill should run one rather
than leaving blank fields for someone to fill in later, which is the same as never.

## How to ask (agent-native)

Use the host's **structured question tool** when it has one — in Claude Code that is
`AskUserQuestion`; other runtimes expose an equivalent picker. It is a better instrument
than prose for this interview specifically: the respondent is mid-sprint, and a chip they
can click beats an identifier they have to recall.

Rules for using it well:

- **One question per call.** The sequencing *is* the method: whether you follow up on a
  vague consequence depends on what they just said. Batching the interview into one
  multi-question call destroys that, and it is the most common way to run this badly.
- **Never invent the choices.** Run `options` and map its output straight into the tool —
  `label` → the option label, `description` → the option description, `value` → the CLI
  argument you will pass. Offering a capability that does not exist, or an environment the
  org does not have, teaches the respondent that the catalog is fiction.

  ```bash
  python3 assets/okf_catalog.py options <bundle> --for capabilities --team <consumer> --json
  python3 assets/okf_catalog.py options <bundle> --for environments --json
  python3 assets/okf_catalog.py options <bundle> --for dependencies --team <you> --state proposed --json
  python3 assets/okf_catalog.py options <bundle> --for verification-kinds --json
  ```

- **Free text stays free text.** Dates, consequences and fallbacks are not multiple choice.
  Ask them as open questions; the tool's "Other" escape exists for a reason, and a picker
  with four guesses at what a delay would cost is worse than an empty box.
- **Show the option's consequence in its description**, not just its name. "a contract test
  — recorded as evidence, raises readiness for no environment" is the whole lesson, and the
  respondent reads it at the moment it matters.
- **Degrade gracefully.** With no such tool, ask in prose — still one question at a time,
  still with the real choices listed. Never show the respondent YAML to fill in.

| Question | Ask as | Choices from |
|---|---|---|
| which capability do you need | picker | `options --for capabilities --team <you>` |
| which environment | picker | `options --for environments` |
| which edge are you acknowledging | picker | `options --for dependencies --team <you> --state proposed` |
| how was it verified | picker | `options --for verification-kinds` |
| by when do you need it | free text (a date) | — |
| what happens if it is late | free text, then **follow up** | — |
| is there a degraded mode | free text, or "none" + why | — |
| how long to stand it up | free text (days) | — |

## Shared rules

- **Never ask for what can be derived.** Team name, repo, capability inputs and outputs come
  from the scan. Ask only what code cannot know.
- **One question at a time, in plain language.** The respondent is an engineer or a lead,
  not a schema author. Never show them YAML to fill in.
- **Refuse "TBD".** A blank field silently becomes an accepted risk. Where an answer
  genuinely does not exist, record the *inability* explicitly (`--no-fallback "<why>"`),
  which escalates rather than disappearing.
- **Never let one side answer the other's questions.** The consumer may not set
  `promised_date`; the provider may not set `consequence_if_late`. Same
  anti-self-certification principle as `consumer_verified`, applied to planning.
- **Confirm the rendered document back in prose** before writing it.

## Consumer interview → `declare`

Ask in this order:

1. **What are you building, and which team's capability does it need?** *Picker*, populated
   from `options --for capabilities --team <you>` — it excludes your own team's capabilities
   and shows each one's readiness and owner, including whether the owner is a stub. If none
   matches, say plainly that you will create a proposed capability under that team, and a
   stub team if the team is absent.
2. **Which environment does this need to work in?** *Picker*, from
   `options --for environments`. Defaults to the highest configured.
3. **By when do you need it?** This is a *request* (`requested_date`), stored separately
   from the provider's promise. Never conflate them.
4. **If it isn't there on that date, what actually happens? Who feels it, and how badly?**
   *Free text, and the one question you must be willing to ask twice.* It is the single most
   valuable answer in the document. "It'd be bad" is not an answer — follow up with a
   specific: how many people, how often, how long, what does the workaround cost them per
   week? A vague answer is a prompt to dig, not a field to close, and half the value of this
   exercise is that answering it properly forces two teams to talk.
5. **Is there a degraded mode you could ship instead** — flag, manual process, partial
   release?
6. **How long would it take to stand that fallback up, once you decided to?** This yields
   `fallback.execution_days`, which produces the point of no return.

Then run:

```bash
python3 assets/okf_catalog.py declare <bundle> \
  --consumer checkout --capability payments/initiate-refund --environment production \
  --requested-date 2026-08-25 \
  --consequence "Refund UI ships dark; ops runs ~40 manual refunds a week at ~15 min each." \
  --fallback "Ship the UI behind a flag, default off; ops uses the admin console" \
  --fallback-days 3 --by <handle>
```

Before writing, `declare` prints the target's hard-requires closure with effective readiness
and depth. **Read it back to them.** If depth is `unknown`, say it in words: they are about
to depend on something whose own dependencies nobody has mapped. It also prints the
provisional point of no return — the date being earlier than they expected is the useful
surprise — and ends with the exact command the provider must run plus a message to paste
into that team's channel.

Teams will lowball `fallback.execution_days`. Even a bad estimate beats zero buffer; an
omitted one is flagged as un-estimated (`CC-DEFAULT-FALLBACK`).

## Provider interview → `ack`

0. **Which edge are you responding to?** *Picker*, from
   `options --for dependencies --team <you> --state proposed` — skip this when the edge was
   named in the request.
1. **Here is what they say they need, in which environment, by when. Can you commit a
   date?** *Free text (a date).* Sets `promised_date`. Read the consumer's stated consequence
   back to them first; a provider who has seen the cost answers differently.
2. **Does the capability already exist for this, or is it new work?**
3. **Today, what state is it in for *that* environment specifically** — not tested anywhere,
   tested by you, or already verified by a consuming team?
4. **Is the runtime for that environment the same as where you tested it?** `ack` surfaces
   the scanned `runtimes` and refuses to proceed on a platform mismatch until the provider
   passes `--runtime-checked` and says in `--note` what they actually looked at. The
   scanner already knows the platforms differ; the interview is what makes someone look at
   it. See [failure-patterns.md](failure-patterns.md) §2 for what an unexercised target
   costs.
5. **Anything the consumer must do or provide first?** Record in `--note`.

If the provider disputes the consequence or the fallback, record it with `--dispute`; their
words go in the body and the consumer's fields stay intact.

If `promised_date` is later than `requested_date`, the edge holds at `proposed` and the
consumer must run `confirm`. Say so out loud — that gap is the negotiation, and it should
happen now rather than on delivery day.

## Verification interview → `verify`

Short, but the guards matter more than the questions.

1. **What did you exercise, and in which environment?** (`--scope`, `--environment`)
2. **How** — a deployment run, a contract test, or by hand? (`--kind`) *Picker*, from
   `options --for verification-kinds`; the descriptions carry the guard, so the respondent
   sees that a contract test raises readiness for nothing at the moment they choose it.
3. **Where did it actually run?** For `kind: deployment`, `--ran-in` must equal
   `--environment` and `--resolved-from` must be the CI run. Green against staging is
   evidence about staging.
4. **Evidence link?** Required.

Record `result: failed` as readily as `verified`. Then read the printed readiness back,
including the untouched environments — the line that says production is still `unknown`
after a staging run is the whole point.

## How this guidance gets measured

The tooling half of these rules is gate-tested (the CLI refuses a consumer-written
`promised_date`, a blank consequence, a fallback with no execution time). The conversational
half — whether an agent actually presses when an engineer answers "it'd be bad" — needs model
runs, so it lives as a documented manual protocol rather than a gate check:
`docs/okf-capability-catalog/2026-08-12-interview-elicitation-model-eval.md` in the skills
repo. If you change the questions above, re-run it, and record an unmoved measurement as
unproven rather than quietly keeping the new wording.

## Claiming a team → during `annotate`

Only when the team is absent or a stub, and only from a repo that team owns: name, id,
lead, channel. Prefer putting the answers in `.okf/team.yaml` in the repo so the next scan
never has to ask again.
