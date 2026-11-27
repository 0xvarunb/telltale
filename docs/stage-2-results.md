# Stage 2 results

Stage 2 is a test set and a checker, what AI people call evals. This page holds the measured
figures from the first run. Only figures written here may be quoted. Rule 0 applies to this page
too: nothing here is estimated.

- Answers: 19 Nov 2026, commit b5d7679, answering model Claude Haiku 5.5, batch mode, run
  `20261119-2008-b5d7679-claude-haiku-5-5`.
- Checker: Claude Sonnet 5.5. The figures below are from the same 33 answers judged again on
  26 Nov 2026 after two fixes to the checker (below), commit 91ee332, run
  `20261126-2112-91ee332-claude-haiku-5-5-rejudged`.
- Model comparison: Claude Sonnet 5.5 answering the same 33 questions, 26 Nov 2026, commit
  e22bf99, judged at commit 91ee332, run `20261126-2113-91ee332-claude-sonnet-5-5-rejudged`.
- Run folders are under `data/eval/runs/`, which is git-ignored.

## The test set

50 questions, each a job an officer would ask about before starting it, from the engine room, the
deck, the bridge and cargo work. For 27 of them the reports that should come up were chosen before
the question was asked. For 23 the right answer is that the reports don't cover the job, and the
tool should say so.

The 33 working questions and their chosen reports are published in
[test-set.md](test-set.md). The other 17 are held back, never published and never run, so the tool
can't be tuned to them. They run once, at the end of stage 3. Every figure on this page is from the
33 working questions.

## Headline

**Sentences not backed by their cited passage: 132 of 539 (24.5%).**

- 90 had no citation at all.
- 41 were judged "partly": the passage backs some of the sentence but not all of it, or the
  sentence states it more strongly than the report does (for example dropping "probable").
- 1 was judged "not supported".
- 0 were left unjudged.

Stage 1 marks a sentence with no source, but it does not yet check a cited sentence against its
passage. These figures are the baseline stage 3 has to bring down. Its target is zero.

### How a sentence is counted

- A fact sentence is any sentence other than "There is no reliable answer in these reports".
  The 539 are the fact sentences of the 33 answers.
- A fact sentence is not backed when it has no citation, or when the checker judges it "partly"
  or "not supported". "Partly" counts against the headline: the target is that every claim in a
  sentence is backed.
- The share leaves out sentences the checker could not judge, and counts them apart. If more than
  5% of cited sentences are unjudged, the headline is not valid.

## Did it find the right reports?

- **Report questions passed: 13 of 18.** A report question passes when the answer cites at least
  one of the reports chosen for it. Other reports it cites are listed, not counted against it:
  the chosen lists are a minimum.
- **Chosen reports among the 8 passages the search returns: 25 of 69.** The search is the weak
  point. When the right report is not in those 8, the answer can't use it.

## Did it say "no reliable answer" when it should?

- **No-answer questions passed: 6 of 15.** On the other 9 it answered with cases from a different
  job, which the tool's own rules forbid.
- **Report questions wrongly answered "no reliable answer": 0.**

## Checking the checker

Before its numbers are quoted, 30 of the checker's verdicts are checked by hand against the
passages: 15 it judged "partly" or "not supported" and 15 it judged "supported", drawn with a fixed
seed from the working questions. Its numbers are quoted only if at least 27 of the 30 agree.

- **First check: 24 of 30.** It failed. Five of the six disagreements were sentences judged
  "partly" only because they named the ship and its type, which came from the cited report's
  title. The checker was never shown the title.
- **The fix:** the checker now sees each passage under its report title. It may use the title
  only to tell which ship, its type and the casualty the title names, never causes, the order of
  events or other detail. The same 33 answers were judged again.
- **Second check: 28 of 30.** It passed. The two disagreements were judgement calls, one each
  way, neither about titles.
- **A third check, on the Sonnet run below: 25 of 30.** It failed on the same two patterns seen
  in the second check. The checker said "not supported" when part of a sentence was in its
  passage, which by its own rules is "partly", and it would not let a passage that is only the
  report's title back the casualty that title names.
- **The second fix:** "not supported" now means none of the sentence's claims are in the
  passages, and a title-only passage backs the casualty its title names. Both runs were judged
  again on their same answers, and each check came out at **28 of 30**.
- The headline has moved with each fix: 146 of 539 (27.1%) at first, 131 (24.3%) after the title
  fix, 132 (24.5%) after the second fix. The checker's verdicts vary a little from one run to the
  next (a handful of sentences move with no change behind them), so differences of one or two
  sentences mean nothing.

## Model comparison

The same 33 questions, the same search and the same checker. Every answering model runs with its
up-front thinking turned off, as the checker does. Claude Opus 5.5 is left out: Anthropic's
documentation says its thinking can't be turned off, so it can't be compared on the same terms.

| | Claude Haiku 5.5 | Claude Sonnet 5.5 |
| --- | --- | --- |
| Sentences not backed | 132 of 539 (24.5%) | 72 of 514 (14.0%) |
| with no citation at all | 90 | 39 |
| judged "partly" | 41 | 31 |
| judged "not supported" | 1 | 2 |
| Report questions passed | 13 of 18 | 14 of 18 |
| No-answer questions passed | 6 of 15 | 9 of 15 |
| Report questions wrongly answered "no reliable answer" | 0 | 2 |
| Answer cost per question | $0.0013 | $0.0222 |
| Checker hand-check | 28 of 30 | 28 of 30 |

The search is the same for both, so 25 of the 69 chosen reports reached the 8 passages either way.
Sonnet cites far more of what it writes and says "no reliable answer" more often when it should,
but twice it said so on a job the reports do cover (A29 and A33), which Haiku never did. It costs
about 17 times as much per answer.

## Cost

- Haiku answers: $0.0443 for 33 questions. Sonnet answers: $0.7319.
- Checker: $0.3844 for the first judging, $0.4352 after the title fix, then $0.4412 (Haiku) and
  $0.4606 and $0.4657 (Sonnet, first judging and after the second fix).
- Three one-question dry runs: $0.0893.
- All stage 2 runs together: $3.0526. Every run went as a Message Batch, at half the live price.

## Per question (working questions only)

Haiku 5.5 unless marked Sonnet.

| id | expect | passed | chosen reports cited | chosen reports in the 8 passages | sentences | not backed | Sonnet: passed | Sonnet: sentences | Sonnet: not backed |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| A3 | reports | yes | 2 of 5 | 2 of 5 | 21 | 11 | yes | 22 | 2 |
| A4 | no answer | yes | - | - | 1 | 0 | yes | 1 | 0 |
| A5 | no answer | no | - | - | 19 | 5 | no | 10 | 4 |
| A6 | no answer | yes | - | - | 1 | 0 | yes | 1 | 0 |
| A7 | no answer | no | - | - | 19 | 4 | no | 20 | 2 |
| A9 | no answer | no | - | - | 30 | 9 | no | 35 | 7 |
| A10 | reports | yes | 1 of 2 | 1 of 2 | 26 | 8 | yes | 25 | 7 |
| A12 | reports | yes | 1 of 7 | 2 of 7 | 18 | 3 | yes | 19 | 2 |
| A13 | no answer | yes | - | - | 1 | 0 | yes | 1 | 0 |
| A15 | reports | yes | 2 of 4 | 3 of 4 | 24 | 4 | yes | 32 | 4 |
| A17 | no answer | yes | - | - | 1 | 0 | yes | 1 | 0 |
| A18 | reports | yes | 1 of 3 | 2 of 3 | 17 | 4 | yes | 10 | 0 |
| A22 | no answer | yes | - | - | 1 | 0 | yes | 1 | 0 |
| A23 | reports | no | 0 of 3 | 1 of 3 | 25 | 4 | yes | 46 | 5 |
| A24 | no answer | no | - | - | 23 | 3 | no | 11 | 1 |
| A26 | reports | yes | 2 of 5 | 2 of 5 | 30 | 7 | yes | 47 | 4 |
| A28 | reports | yes | 1 of 3 | 1 of 3 | 14 | 3 | yes | 15 | 1 |
| A29 | reports | yes | 1 of 3 | 2 of 3 | 13 | 4 | no | 1 | 0 |
| A30 | no answer | no | - | - | 19 | 7 | no | 25 | 4 |
| A31 | reports | no | 0 of 4 | 1 of 4 | 20 | 5 | yes | 7 | 0 |
| A32 | no answer | no | - | - | 23 | 4 | yes | 1 | 0 |
| A33 | reports | no | 0 of 1 | 1 of 1 | 20 | 6 | no | 1 | 0 |
| A34 | reports | yes | 3 of 5 | 3 of 5 | 31 | 6 | yes | 42 | 5 |
| A35 | reports | yes | 1 of 4 | 1 of 4 | 18 | 5 | yes | 24 | 2 |
| A36 | reports | yes | 1 of 2 | 1 of 2 | 24 | 4 | yes | 20 | 6 |
| A37 | reports | no | 0 of 3 | 0 of 3 | 23 | 8 | no | 27 | 4 |
| A42 | no answer | no | - | - | 9 | 3 | no | 15 | 4 |
| A43 | no answer | no | - | - | 16 | 3 | yes | 1 | 0 |
| A46 | reports | yes | 1 of 5 | 1 of 5 | 15 | 3 | yes | 22 | 2 |
| A47 | reports | yes | 1 of 3 | 1 of 3 | 28 | 6 | yes | 28 | 4 |
| A49 | reports | no | 0 of 7 | 0 of 7 | 9 | 1 | no | 12 | 2 |
| A50 | no answer | no | - | - | 5 | 2 | yes | 1 | 0 |
| A51 | no answer | yes | - | - | 1 | 0 | yes | 1 | 0 |

The questions themselves are not published. The ids are their numbers in the private set.

## What this means for stage 3

- **Remove what isn't backed.** About a quarter of the sentences are not fully backed by the
  passage they cite, or cite nothing. Stage 3 checks each sentence before it is shown and removes
  the ones that fail.
- **Stop stretching.** On 9 of 15 jobs the reports don't cover, the tool offered a case from a
  different job. It should say "no reliable answer".
- **Find more of the right reports.** Only 25 of 69 chosen reports reached the 8 passages. One
  known cause, seen outside the test set: a page that mixes topics hides a short finding, and a
  report that says "ECS" is missed by a question that says "ECDIS". Search changes are measured
  on these 33 questions, never tuned on the 17.
