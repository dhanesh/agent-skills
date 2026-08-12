# The failure patterns these rules come from

Every non-obvious rule in this skill exists because a specific class of failure keeps
happening. The four cases below are public, documented by the organisations involved, and
each one maps onto a rule the tooling enforces. Use them when a team pushes back on a rule
as bureaucracy: the argument is not "process", it is "this is how this specific mistake
gets made, repeatedly, by competent people".

None of them are cross-team-catalog case studies as such. They are the *shapes* — a
provider's own testing standing in for acceptance, deployment reality differing per target,
a hidden upstream, a capability nobody ever exercised — and those shapes are what the
schema is built to make impossible to state ambiguously.

## 1. A provider's own validation is not the consumer's acceptance

**CrowdStrike, Falcon Channel File 291 — 19 July 2024.** A Rapid Response Content update
passed CrowdStrike's own Content Validator, which evaluated the new Template Instances on
the assumption that the IPC Template Type would be supplied with 21 input fields; the
sensor code supplied 20. The 21st field had only ever been exercised with wildcard matching
criteria, so no test covered the case that shipped. The content passed every provider-side
check and then crashed sensors on customers' machines.

**Rules this motivates**

- `provider_tested` is a **ceiling** on what a provider-owned document may say, not a step
  on the way to "ready". Internal validation is evidence about the provider's own harness.
- `kind: contract` raises readiness for **no** environment. A validator that checks the
  shape of an exchange can pass while the thing it describes is unrunnable in situ.
- `consumer_verified` may only be written by a consuming team, from a run that actually
  executed in the environment being claimed.

Sources:
[CrowdStrike RCA announcement](https://www.crowdstrike.com/en-us/blog/channel-file-291-rca-available/),
[External Technical Root Cause Analysis (PDF)](https://www.crowdstrike.com/wp-content/uploads/2024/08/Channel-File-291-Incident-Root-Cause-Analysis-08.06.2024.pdf).

## 2. "Deployed" is per target, and a fallback you have not rehearsed is not a fallback

**Knight Capital Americas — 1 August 2012.** New Retail Liquidity Program code was
deployed across eight SMARS servers, but one server did not receive it; that server still
carried years-old "Power Peg" code behind a flag the new release had repurposed. At market
open the eighth server acted on the repurposed flag. In 45 minutes the firm executed over 4
million unintended trades and lost more than $460 million. The SEC's order found the firm
lacked adequate controls around market access; response during the event included actions
that made matters worse rather than a rehearsed, time-boxed fallback.

**Rules this motivates**

- The code axis (`feature`/`integration`/`release`) says nothing about the deployment axis.
  "It is on the release branch" is not "it is running everywhere it needs to run".
- `runtimes` is recorded **per environment**, and a platform or fleet that differs across
  environments raises `CC-PLATFORM-DRIFT` while any dependency is open. What was never
  exercised is exactly where parity failures land.
- `fallback.execution_days` is mandatory, and the **point of no return** is computed
  backwards from the deadline. A fallback nobody has costed cannot be invoked in time, and
  improvising under pressure is its own failure mode.

Sources:
[SEC press release 2013-222](https://www.sec.gov/newsroom/press-releases/2013-222),
[SEC order, Release No. 34-70694 (PDF)](https://www.sec.gov/files/litigation/admin/2013/34-70694.pdf).

## 3. Your readiness is the minimum over your hidden upstreams

**Amazon Kinesis, US-EAST-1 — 25 November 2020.** A small capacity addition to the Kinesis
front-end fleet pushed the fleet past the maximum thread count allowed by the operating
system configuration. Kinesis degraded — and so did Cognito, CloudWatch, Lambda and
AutoScaling, because they depended on it. Many teams discovered the shape of their own
dependency graph during the event rather than before it.

**Rules this motivates**

- `effective_readiness(C, env) = min(own_readiness(C, env), effective over hard requires)`.
  A capability is only as consumable as the weakest link in its hard closure.
- `requires` lives on the **capability**, declared once, so a consumer never has to know
  their provider's providers to reason about risk.
- A trip on any edge propagates to every downstream edge automatically, naming the
  originating edge — because relaying bad news by hand is what fails under pressure.
- `CC-UNMANAGED-UPSTREAM` fires when an edge rests on a capability that is not verified in
  the target environment and has no edge managing it.

Source:
[Summary of the Amazon Kinesis Event in the Northern Virginia (US-EAST-1) Region](https://aws.amazon.com/message/11201/).

## 4. A capability nobody has exercised is `unknown`, not ready

**GitLab.com — 31 January 2017.** After an accidental deletion of production database data,
the team worked through their recovery options and found that of five backup and replication
techniques, none were working reliably or had been set up: `pg_dump` was silently failing
against a newer PostgreSQL and the failure notifications were not being delivered, disk
snapshots were not enabled on the database servers, and the staging copy was hours stale.
About six hours of data was lost. The backups were believed to exist; no one had exercised a
restore.

**Rules this motivates**

- `unknown` is the default, and it is not `fine`. A capability whose closure nobody has
  mapped renders as `unknown` in every view — distinct from both green and red.
- Readiness is **derived** from Verification and Signal documents, never stored in the
  capability. Delete the verification and the state drops back, so a green tick cannot
  outlive the thing it described.
- `result: failed` is first-class and lowers readiness. The format must never make a team
  choose between recording bad news and recording nothing.
- `expires_after_days` exists because an old verification is a claim about a system that has
  since changed.

Source:
[Postmortem of database outage of January 31](https://about.gitlab.com/blog/postmortem-of-database-outage-of-january-31/).

## Using these in conversation

When someone says a capability is "done", the useful question is not "is it done?" but
**"done where, exercised by whom, and how would we know if it stopped being true?"** Each
case above is a way of getting that question wrong, by people who were paying attention.
The catalog's job is to make the ambiguous answer unwritable: a state that names its
environment, an owner who is entitled to assert it, and a depth qualifier saying how much
of the picture was actually checked.
