# Decision record

## Frame
Whether to renew KYC vendor terms before the 2026-08-29 auto-extension.

## Alternatives Considered
- Build in-house end to end
- Renew on quoted terms
- Renew one year, run the document-requirement experiment
- Split: vendor identity, own document capture

## Values
Funnel recovery over unit cost. A point of drop-off is worth more than the price rise.

## Reasoning
We do not know whether the vendor causes the drop-off. Building to fix a cause we
have not isolated is the expensive version of a guess.

## Decision
Renew for one year and run the document-requirement experiment in Q3.

## Premortem
It is 2027-08-29 and this failed. The experiment ran late, finished inconclusive
because we changed two things at once, and we renewed again at a worse price with
the same drop-off. We bought a year and spent it.

## Falsifier
If the experiment has not produced a clean read by 2026-12-31, the renewal was a
delay rather than a decision, and we escalate to a build call without it.

## Predictions
- 2026-12-31 | 0.75 | The document-requirement experiment produces a clean read
- 2027-03-31 | 0.55 | Drop-off at the KYC step falls below 18%
- 2027-08-29 | 0.30 | We commit to an in-house build at the next renewal
