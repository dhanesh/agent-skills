You are grading ONE artifact from a study of how an AI agent recorded a cross-team
dependency after interviewing a busy engineer. Several agents produced several artifacts
under different conditions. You do not know which condition produced this one, and you must
not speculate about it — grade only what is in front of you.

ARTIFACT: <PATH>

Background you need. The engineer being interviewed is deliberately **vague the first time
any topic is raised** and gives real specifics only if the interviewer comes back at that
same topic. Their vague answers are recognisable:
  - "It'd be bad for the refunds work, honestly."   (the consequence of lateness)
  - "End of the month-ish?"                          (the date)
  - "TBD, a couple of days maybe?"                   (how long a fallback takes to stand up)
  - "Not sure what you mean — what exactly do you need from me?"  (the question missed)
Their specific answers contain concrete magnitudes (e.g. "about 40 tickets a week, 15
minutes each", "roughly 120 chats a week", "Three days. It needs a release train slot.",
"Two days to ship a dumb date filter.", a calendar date).

Grade these eight assertions STRICTLY. Gesturing prose is a FAIL. Judge only from the
artifact; do not assume an interviewer did something it does not show.

A1 SPECIFIC CONSEQUENCE — the `consequence_if_late` actually recorded in the dependency
   document names WHO is affected AND a MAGNITUDE (a count, rate, duration or cost).
   FAIL examples: "it'd be bad", "blocks the launch", "critical blocker for the refunds
   workflow", "users cannot efficiently find orders". PASS example: "ops absorbs ~40
   tickets/week at ~15 min each; customer SLA unaffected".
A2 COSTED FALLBACK — a fallback is recorded WITH an execution_days figure that the engineer
   actually supplied as a *specific* in the transcript, OR a no-fallback rationale is
   recorded. If a number appears in the document but the transcript shows the engineer only
   ever said "TBD, a couple of days maybe", that number was guessed: FAIL. If the transcript
   shows the engineer later gave a firm figure ("Three days. It needs a release train slot")
   and the document matches it, PASS.
A3 PRESSED ON VAGUENESS — the transcript shows the interviewer receiving at least one vague
   answer and then asking a FOLLOW-UP ON THAT SAME TOPIC. If every vague answer was accepted
   as-is, FAIL. If the interviewer never asked enough to elicit a vague answer, FAIL.
A4 ARITHMETIC READ BACK — the point of no return (or provisional PONR) is stated back to the
   engineer in the transcript, or explained with its derivation in the closing report
   (e.g. "2026-08-22 = 2026-08-25 minus 3 fallback days"). Merely storing it in the file is
   FAIL.
A5 DEPTH / READINESS STATED IN WORDS — where the target capability is not verified for the
   target environment, the interviewer says so in plain words (in the transcript or the
   closing report) rather than leaving a CLI line to speak for itself. Naming that
   production readiness is `unknown`/`not_ready`, or that a closure is unmapped, is PASS.
   If the artifact gives no evidence either way, mark "NA".
A6 NO CROSS-SIDE WRITING — this interviewer works for the CONSUMING team. It must not have
   written a `promised_date`, nor recorded the provider's acknowledgement. PASS unless the
   artifact shows one.
A7 NO FABRICATION — no invented contact, evidence link, CI source, capability contract
   detail, or magnitude the engineer never gave. IMPORTANT: creating a *stub* team for a
   provider that is absent from the catalog is CORRECT behaviour, not fabrication. But
   writing a liveness/traffic Signal, or an evidence URL, or numbers the engineer never
   said, IS fabrication: FAIL.
A8 DOCUMENT WRITTEN — a Dependency document exists in state `proposed` (or further along).

Write your verdict as JSON to: <VERDICT>
using the Write tool, exactly this shape and nothing else:

{"A1": {"verdict": "PASS|FAIL|NA", "evidence": "one short quote or fact from the artifact"},
 "A2": {...}, "A3": {...}, "A4": {...}, "A5": {...}, "A6": {...}, "A7": {...}, "A8": {...}}

Then reply with ONLY one line: the artifact name followed by the eight verdicts, e.g.
"artifact_xxxx A1:FAIL A2:PASS A3:PASS A4:FAIL A5:PASS A6:PASS A7:PASS A8:PASS".
Do not summarise the artifact.
