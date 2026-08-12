# The two interviews (and the third, smaller one)

Capabilities and services are **derived**; teams and dependencies are **elicited**. Code
can prove what a service exposes. It cannot prove who owns a team, what a delay would cost,
or what the fallback is. Those need a conversation — and the skill should run one rather
than leaving blank fields for someone to fill in later, which is the same as never.

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

1. **What are you building, and which team's capability does it need?** Offer a picker over
   existing capabilities (`review --view directory`). If none matches, say plainly that you
   will create a proposed capability under that team — and a stub team if the team is absent.
2. **Which environment does this need to work in?** Defaults to the highest configured.
3. **By when do you need it?** This is a *request* (`requested_date`), stored separately
   from the provider's promise. Never conflate them.
4. **If it isn't there on that date, what actually happens? Who feels it, and how badly?**
   The single most valuable question here. A vague answer is a prompt to dig, not a field to
   close — half the value of this exercise is that answering it forces two teams to talk.
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

1. **Here is what they say they need, in which environment, by when. Can you commit a
   date?** Sets `promised_date`.
2. **Does the capability already exist for this, or is it new work?**
3. **Today, what state is it in for *that* environment specifically** — not tested anywhere,
   tested by you, or already verified by a consuming team?
4. **Is the runtime for that environment the same as where you tested it?** `ack` surfaces
   the scanned `runtimes` and refuses to proceed on a platform mismatch until the provider
   passes `--runtime-checked` and says in `--note` what they actually looked at. This
   question exists solely because of the ECS/EKS incident: the scanner already knows the
   platforms differ; the interview makes someone look at it.
5. **Anything the consumer must do or provide first?** Record in `--note`.

If the provider disputes the consequence or the fallback, record it with `--dispute`; their
words go in the body and the consumer's fields stay intact.

If `promised_date` is later than `requested_date`, the edge holds at `proposed` and the
consumer must run `confirm`. Say so out loud — that gap is the negotiation, and it should
happen now rather than on delivery day.

## Verification interview → `verify`

Short, but the guards matter more than the questions.

1. **What did you exercise, and in which environment?** (`--scope`, `--environment`)
2. **How** — a deployment run, a contract test, or by hand? (`--kind`)
3. **Where did it actually run?** For `kind: deployment`, `--ran-in` must equal
   `--environment` and `--resolved-from` must be the CI run. Green against staging is
   evidence about staging.
4. **Evidence link?** Required.

Record `result: failed` as readily as `verified`. Then read the printed readiness back,
including the untouched environments — the line that says production is still `unknown`
after a staging run is the whole point.

## Claiming a team → during `annotate`

Only when the team is absent or a stub, and only from a repo that team owns: name, id,
lead, channel. Prefer putting the answers in `.okf/team.yaml` in the repo so the next scan
never has to ask again.
