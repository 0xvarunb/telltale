# Stage 3 results

Stage 3 makes the answers more trustworthy. This page holds its measured figures. Only figures
written here may be quoted. Rule 0 applies to this page too: nothing here is estimated.

## Starting point

Before any guardrail work, the library grew from one publisher to three. This section measures
what that alone did, so later changes can be shown apart from it.

### The library

| Publisher | Reports | Page groups |
| --- | --- | --- |
| MAIB (UK) | 619 | 23,519 |
| NTSB (US) | 353 | 8,419 |
| TSIB (Singapore) | 72 | 2,874 |
| Total | 1,044 | 34,812 |

On 26 Nov the library was the 619 MAIB reports only.

### The run

- Answers and checking: 27 Nov 2026, commit a4df394, answering model Claude Haiku 5.5 with its
  thinking off, checker Claude Sonnet 5.5, batch mode, run
  `20261127-2138-a4df394-claude-haiku-5-5`. The run notes "uncommitted changes": the only one
  was an untracked tool settings folder, not code or data.
- Cost: $0.5590 ($0.0475 answers, $0.5115 checking).
- The 26 Nov figures beside it are from run `20261126-2112-91ee332-claude-haiku-5-5-rejudged`
  (answers from 19 Nov, commit b5d7679, the same answering model and the same checker).
- Besides the bigger library, two things changed in the answer request since 19 Nov: Haiku's
  thinking is now turned off explicitly, and each passage names its own publisher instead of
  always saying MAIB. The search method is the same; only the index grew.

### The expected reports changed too

Before this run, all 50 questions were checked again against the 425 new reports, by the same
picking rule as on 19 Nov. Four working questions that had no matching report now have one, and
13 working report questions gained reports. Nothing was removed. So the working set is now 22
report questions (was 18) and 11 "no reliable answer" questions (was 15), and the expected lists
are longer. Totals across the two runs are not like for like, so each figure below is also
given on the questions that are the same in both.

### Figures

| | 26 Nov, MAIB only | 27 Nov, three publishers |
| --- | --- | --- |
| Sentences not backed by their cited passage | 132 of 539 (24.5%) | 133 of 617 (21.6%) |
| of which: no citation | 90 | 88 |
| of which: judged "partly" | 41 | 45 |
| of which: judged "not supported" | 1 | 0 |
| Report questions passed (all) | 13 of 18 | 13 of 22 |
| Report questions passed (the same 18) | 13 of 18 | 11 of 18 |
| Expected reports in the top 8 (each run's own lists) | 25 of 69 | 24 of 112 |
| Expected reports in the top 8 (the 26 Nov lists) | 25 of 69 | 16 of 69 |
| "No reliable answer" questions passed (all) | 6 of 15 | 4 of 11 |
| "No reliable answer" questions passed (the same 11) | 4 of 11 | 4 of 11 |
| Report questions wrongly answered "no reliable answer" | 0 | 0 |

### What it shows

- **The bigger library pushed relevant reports out of the top 8.** On the 26 Nov expected
  lists, 16 of 69 expected reports were found, down from 25. Three report questions that passed
  on 26 Nov failed because none of their expected reports was found any more (A18, A29, A47),
  and one failed question now passes (A31). The new reports did bring in 8 of the 43 reports
  added to the expected lists.
- **Two of the four questions that gained a report now pass on it** (A4 and A51). The other two
  (A5 and A9) did not find theirs.
- **The share of unbacked sentences fell from 24.5% to 21.6%**, mostly because the answers were
  longer (617 sentences against 539) while the unbacked count stayed level. The checker's
  verdicts vary by a handful of sentences from one run to the next, so this is not yet a
  measured improvement.
- **Reports from TSIB name no ship in their titles.** A sentence that names the ship and cites a
  page where the name does not appear is judged "partly", even when the name is right.

### The crankcase question

The question a reader sent on 19 Nov ("Opening up an auxiliary engine crankcase or scavenge
space right after shutdown. What has gone wrong on other ships doing this job?") was asked again
on 27 Nov.

- On 19 Nov the answer was "There is no reliable answer in these reports for that job."
- On 27 Nov it gave 15 sentences from three reports, 11 of them cited and 4 with no source:
  Damgracht (NTSB), where the crew opened the crankcase doors after a repair and water vapour
  later set off a false oil mist shutdown; Finnreel (MAIB), where the crankcase doors were removed after a shutdown and
  nothing went wrong; and Dennis Hendrix (NTSB), where a crankcase door blew out with the engine
  running.
- A keyword search of every stored page found no report of something going wrong while opening
  up a crankcase or scavenge space right after shutdown. The new reports do not answer this job.
  Damgracht is the closest related case, and the answer does not say that none of its cases is
  the job itself.

### Checking the checker

30 of the checker's verdicts from this run (15 "supported", 15 "partly") were checked by hand
against their passages. **28 of 30 agree**, confirmed by a second reader, so the figures above may
be quoted (the bar is 27 of 30). The two disagreements are faithful rewordings judged "partly":
"the load required to render the winch" given as "the load the winch was designed to render at",
and "a locally developed procedure" given as the crew's own procedure.
