# Domain playbook: learning a codebase or technical system

The default workflow, adapted for onboarding onto an unfamiliar codebase, architecture,
or complex system. Works for a human engineer and for coaching an agent's own
exploration. The diagnostic test still governs: reading code end-to-end is the systems
version of rereading a chapter — comfortable, fluent, and mostly non-generative unless
each pass is followed by retrieval.

## Adapted workflow

1. **Pretest.** Before opening the repo, write down guesses: what are the major
   components? Where does a request enter? What owns persistence? Wrong guesses are fine
   — they prime encoding and, once checked, reveal where the mental model was mis-shaped.
2. **First exposure = trace one real path end-to-end.** Pick a single concrete behavior
   (one request, one CLI command, one job) and follow it through every layer once. Don't
   annotate everything; just walk it.
3. **Retrieve = reconstruct from memory.** Close the editor. Draw the architecture on a
   whiteboard or blank file from memory: components, boundaries, and the path just
   traced. Then diff the drawing against reality. The mismatches are the study agenda.
4. **Question & explain = 5 Whys on the surprises.** For each part that surprised you,
   chase the mechanism: why is this boundary here? why does this call go through a queue?
   Self-explain how each answer connects to what you already knew about the system.
5. **Space it.** Schedule expanding-interval reconstruction sessions with
   `assets/spaced_schedule.py` — re-draw the architecture cold on day 1, 3, 7… Each
   session's diff against reality is the retention check; a large diff means shorten the
   next interval.
6. **Interleave.** Once one path holds, trace a second, contrasting path (a write instead
   of a read, a failure instead of a success) and alternate between them. Choosing which
   pattern applies is the judgment interleaving trains.

## Retrieval prompts that work for systems

- "From memory: what happens, step by step, between X arriving and Y being persisted?"
- "Which component would you change to add feature Z, and what would break first?"
- "Draw the dependency direction between A and B. Now check. Why is it that way?"
- "What invariant does this module protect, and what test would catch its violation?"

## Anti-patterns to redirect

| Habit | Why it fails | Replace with |
|---|---|---|
| Reading the whole repo top-to-bottom | Pure exposure, no generation | Trace one real path, then reconstruct |
| Copying architecture docs into notes | Transcription isn't retrieval | Blank-page re-draw, then diff against the docs |
| Highlighting/bookmarking "important" files | Marks without generating | A "where would I change X?" self-quiz over the same files |
| One marathon onboarding week | Massed practice decays | Shorter sessions on an expanding schedule |
