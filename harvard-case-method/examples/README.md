# Worked examples

Three runs of the skill, one per mode, reproducible end to end:

```sh
sh examples/run-examples.sh
```

Everything is written to a temp dir and cleaned up; nothing here touches the repo.

| Mode | First result | What the gate caught |
|---|---|---|
| **Prioritise** (RICE) | `FAIL (6/7)` | The agent's own reach reported in mixed periods — a silent 3× error, and the sheet is left unranked rather than ranked wrong |
| **Pressure-test** (plan) | `FAIL (5/10)` | The plan's own two sizings disagreed by **25.9×**, in the same document, because nobody had multiplied the drivers out |
| **Decide** | `FAIL (4/7)`, then `exit 2` | The question as asked was a two-option referendum; the decision record then had no premortem, falsifier or forecast |

In all three the first artifact looked finished. Three of the four failures were the
*agent's* errors, not the user's — which is the argument for a mechanical gate rather
than a careful reader.

The published walkthrough, with the conversation around each run, is built from exactly
these files and this script.
