# What the HBS case method actually is — and where the popular AI prompt diverges

This file exists because the skill makes a strong claim: the widely-shared "analyze
[company] using the Harvard Business School case study method" prompt is **not** the HBS
case method, and inverting it is not a stylistic quibble. Here is the evidence, and the
part that is genuinely a judgment call rather than a fact.

## What HBS says a case is

- A case is a **10–20 page document written from the viewpoint of a real person leading a
  real organization** — the protagonist. It supplies background on the situation and
  **ends in a key decision to be made**.
- It **reflects the information available to the decision-maker at the time** and builds
  to a decision point **without revealing what decision was actually made**.
- Students **place themselves in the protagonist's role**, perform the analysis, and
  **recommend a course of action without knowing the outcome**. HBS's own framing is that
  students are "not provided the answer" and must act under incomplete information — the
  same condition a manager faces.
- Learning happens in **discussion**: individual preparation, then a small discussion
  group, then a faculty-facilitated section discussion driven by classmates' comments and
  experience.
- The outcome arrives **afterwards**, as the "B-case" / "the rest of the story" / **"the
  reveal"** — material describing the solution the protagonist chose and its results,
  distributed at or after the end of class.

The method is a century old (HBS marked its centenary in 2021) and is the backbone of the
HBS curriculum, so its shape is well documented rather than a matter of interpretation.

## Where the popular prompt diverges

The prompt in circulation asks the model to walk through THE SITUATION → THE DECISIONS →
THE EXECUTION → THE OUTCOME, with instructions including "what did they bet on that
**turned out to be right**", "what broke as they scaled and **how did they fix it**", and
"what pattern **made this work**".

Every one of those is a question that can only be asked with the ending already in hand.
Structurally the prompt is a **retrospective success post-mortem**: it is the B-case
without the A-case. The one thing HBS deliberately withholds until after you have
committed is the one thing this prompt leads with.

That matters for two documented reasons:

- **Hindsight bias** — past events look more predictable than they were, which
  oversimplifies cause and effect and inflates confidence in one's own predictive
  ability. A narrative told backwards from a known outcome is the ideal vehicle for it.
- **Survivorship bias** — studying Apple, Amazon and Google without systematic exposure
  to the equally ambitious companies that pursued similar strategies and failed teaches
  *the characteristics of survivors as if they were recipes for survival*. Selecting one
  admired company per study guarantees the sample.

These two are documented as working in tandem: analysing the past in hindsight, we
cherry-pick what survived and treat its traits as causal.

## The fair reading of the original post

Three of the post's claims hold up, and should not be thrown out with the structure:

1. **The analytical loop is genuinely portable.** Situation → decisions → execution →
   transferable pattern is a reasonable frame, and an AI can carry the discussion side of
   it at near-zero cost. That part of the claim survives.
2. **What an MBA sells is mostly not the analysis.** The cohort, the credential, the
   recruiting pipeline and the classmates are not reproducible by a chatbot. The post says
   this itself, and it is the honest part of the argument.
3. **The post's own caveats notice the problem.** It warns that "case-study analysis can
   become hindsight storytelling" and asks for disconfirming evidence. The critique here
   is not that the author missed the risk — it is that the prompt as written *maximises*
   the risk it warns about, and a caveat in prose does not counteract a structure.

So this skill keeps the post's pattern-extraction questions **verbatim in spirit** and
moves them to step 9, the debrief — which is exactly where HBS puts the reveal. Nothing
of value is discarded; the ordering is repaired.

## What could not be verified

- **The source post itself.** `youtube.com` returns HTTP 403 to this environment's
  fetcher and the community post is not indexed by the available search tools, so the
  post's text, authorship and date rest on the capture supplied by the user. Tom Bilyeu
  does demonstrably publish copy-paste "paste this into Claude or ChatGPT" business
  prompts of this exact shape on other platforms, so the attribution is plausible —
  but treat it as unconfirmed rather than sourced.
- **The "10–20 page" figure and the exact reveal terminology** come from HBS-affiliated
  and encyclopaedic summaries retrieved via search snippets rather than a full page fetch,
  for the same network reason. They are consistent across independent sources; they have
  not been read in situ.

## Judgment calls, flagged as such

- **Same-year dates are allowed in the A-case.** A bare "2011" in a case dated 2011-06-30
  could be either side of the line and the lint cannot tell. It passes. Full ISO dates are
  compared exactly. This trades a small false-negative rate for not making the lint
  unusable.
- **The `## Sources` section is exempt from the hindsight rules.** Citing a 2019
  retrospective is normal scholarship; narrating its contents in the A-case is the leak.
  The exemption is by section, so anything a learner reads as the case is still scanned.
- **`lint` is a proxy, not a judge.** It catches leaked dates and stock hindsight phrases.
  A leak written in original prose with no dates and no denylisted phrase will pass. The
  rules raise the floor; reading the case is still the ceiling.

## Sources

- The Case Method | MBA, Harvard Business School — <https://www.hbs.edu/mba/academic-experience/the-case-method>
- What is the Case Study Method? | HBS Executive Education — <https://www.exed.hbs.edu/the-learning-experience/the-case-study-method>
- 5 Benefits of the Case Study Method | HBS Online — <https://online.hbs.edu/blog/post/case-study-method>
- Case method — Wikipedia (decision-forcing cases, the "reveal"/B-case) — <https://en.wikipedia.org/wiki/Case_method>
- Harvard Business School's Case Method Is Officially 100 Years Old | Poets&Quants — <https://poetsandquants.com/2021/12/26/harvard-business-schools-case-method-is-officially-100-years-old/>
- Survivorship bias — Wikipedia — <https://en.wikipedia.org/wiki/Survivorship_bias>
- Survivorship bias — The Decision Lab — <https://thedecisionlab.com/biases/survivorship-bias>
- Survivorship Bias: The Tale of Forgotten Failures — Farnam Street — <https://fs.blog/survivorship-bias/>
- Tom Bilyeu, "paste this into Claude or ChatGPT" prompt (shape corroboration) — <https://x.com/TomBilyeu/status/2062535503170159039>
- The source post as captured — <https://www.youtube.com/post/UgkxvoqgydfJe34MP7bVo2SpDmQ2sDi8waWt> (unverified; 403 to this environment)
