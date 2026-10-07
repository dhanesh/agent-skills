# Writing style: 80% of the way to ASD-STE100

Owner decision, 2026-10-06. Text that people read in this repo follows "80% of the way
to ASD-STE100 Simplified Technical English". Andrej Karpathy gave this tip in October
2026. This page tells you what the rule means here, where it applies, and where it
does not apply.

This page is separate from [The Prompting Playbook](prompting-playbook.md). The playbook
is about SKILL.md prompts, and `prompting-playbook.sh` lints them. This style is for text
that people read. It does not apply to SKILL.md prompt bodies.

## What "80% STE" means here

ASD-STE100 is a controlled language for technical documents. It has a strict rule set
and a dictionary of about 900 approved words. We take the useful 80% and keep a natural
tone. We do not apply the full standard.

Use these rules:

1. **Short sentences.** Try to keep an instruction to 20 words or fewer. Try to keep a
   description to 25 words or fewer.
2. **Common words.** Use the simple word. Write "use", not "utilise". Write "start",
   not "commence".
3. **Active voice.** Say who does the action. Write "the gate rejects the file", not
   "the file is rejected".
4. **One action per instruction.** Put each action in its own step or sentence.
5. **Consistent terms.** Use one name for one thing. Do not change between "check",
   "gate" and "lint" for the same script.
6. **No Latin abbreviations.** Do not write `e.g.`, `i.e.`, `etc.`, `viz.` or `via`.
   Write "for example", "that is", "and more", "namely", or "through" / "with" / "by".
7. **No long noun clusters.** Do not put more than three nouns in a row. Write "the
   check that validates the skill frontmatter", not "skill frontmatter validation check".

You do not need the STE dictionary. You do not need to count every word. The aim is
text that a reader understands on the first read, also when English is not their
first language.

## Scope

The rule applies to:

1. **Text for people in this repo, from now on.** This includes specs, plans, PR
   bodies, reports, READMEs and `references/` files that you write or change. You do
   not need to rewrite old text that you do not touch.
2. **Output that each skill makes for its users.** Each SKILL.md has one added
   "Output style" line. The line tells the agent to write reports and explanations
   for the user in this style. Some skills make code, prompts or files with a fixed
   format. In those skills, the line applies only to the prose for the user.

The rule does not apply to:

- **SKILL.md prompt bodies.** We did not rewrite them into STE. No evidence shows
  that STE changes how a model follows a prompt. A rewrite can also change the
  meaning of a tested instruction. The playbook gate and the BCP 14 register check
  these bodies.
- **Code, commands, config and generated files.** Their own formats apply.

## BCP 14 keywords are defined terms

STE has its own rules for words like "must". This repo has a different rule.
The capitalised BCP 14 keywords (`MUST`, `MUST NOT`, `SHOULD`, `SHOULD NOT`, `MAY`)
have a defined technical meaning. RFC 2119 and RFC 8174 define them. Keep them as they are.
The playbook (PP-7) and the BCP 14 register control their use in SKILL.md files.
This is a decision of this repo, not a rule from STE.

## Cautions

- **Models drift.** A model that starts in STE often goes back to long sentences.
  Read the output again before you publish it.
- **Looking compliant is not the same as being compliant.** The 2026 ASD white paper
  warns that AI text can look like STE when it is not STE. Check the text. Do not
  trust its look.
- **Do not copy the Karpathy reference sheet that people share.** It has errors. Its
  APPROXIMATELY and TEST entries are two examples. Use the sources below.

## Check your text

Run `make ste`. It runs `scripts/gates/ste-advisory.py` on the Markdown files that
your branch changes. It does not check `*/SKILL.md`. It finds sentences over 25 words,
Latin abbreviations and possible passive verbs. It gives hints only. It always exits
0, and it is not part of `make gate`.

The script does not check `via` or noun clusters. Without a grammar parser, these
checks give too many false hints.

## Sources

- [Simplified Technical English (Wikipedia)](https://en.wikipedia.org/wiki/Simplified_Technical_English)
- [Karpathy on understanding LLM outputs (max.nardit.com)](https://max.nardit.com/articles/karpathy-understanding-llm-outputs)
- [Hacker News discussion](https://news.ycombinator.com/item?id=49114639)
- [ASD-STE100 material on GitHub (JAICHANGPARK)](https://github.com/JAICHANGPARK/ASD-STE100)
