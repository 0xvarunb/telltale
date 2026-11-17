# Spike results, 30 Oct 2026 (THROWAWAY test, not the product)

Set-up: 21 MAIB reports (OGL), 1,628 passages. Keyword search (BM25), top 8 chunks sent to
Claude Haiku 5.5 as search results with citations on. A second Haiku 5.5 call checks every
sentence against the passages it cites. Planted test: 3 cited sentences altered on purpose
(a changed number or tank number, or an invented ship type and port).

## What we saw

| Run | Question | Fact sentences | Without support | Planted caught | Cost |
| --- | --- | --- | --- | --- | --- |
| 1 | ballast tank entry, bulk carrier | 29 | 14 | 3 of 3 | $0.0071 |
| 2 | same, sentence splitting fixed, stricter prompt | 23 | 8 | 3 of 3 | $0.0073 |
| 3 | same, report titles citable | 24 | 7 | - | $0.0060 |
| 4 | crankcase relief valve (not covered by the 21 reports) | 13 | 6 | - | $0.0033 |

Run 1 counts are inflated by a sentence-splitting bug in the test (fixed for run 2). Runs 2 to 4:
21 of 60 fact sentences (about a third) came back without a supporting passage.

## What it means

1. The model alone does NOT meet rule 0. About a third of its sentences, mostly its own summary
   lines ("On the Saga Rose, a petty officer died..."), carried no citation. One sentence added
   outside knowledge (it named the IMO where the passage said only "the revised Resolution").
2. The sentence check works. It caught every unsupported sentence we looked at, and 6 of 6
   planted errors, including small ones (50 cm changed to 80 cm, tank No 3 changed to No 6).
   Spot check of five sentences it passed: all genuinely matched their cited passage.
3. The check is strict: a true detail elsewhere in the report still fails if it is not in the
   cited passage. That is the right behaviour for cite-or-stay-silent.
4. Deleting unsupported sentences leaves awkward text (dangling "It", lost ship names). Stage 3
   needs a rewrite-from-supported-sentences step, then a re-check.
5. On a job the reports do not cover, the model said so ("no reliable answer in these reports for
   that job"), then offered the closest case.
6. Cost: under 1 US cent per question including the checks.
7. Keyword search missed some obvious enclosed-space cases. Stage 1 uses proper semantic search.

Verdict: the promise "every sentence is backed by a line in a report, or it is dropped" is
achievable with the check. It is NOT achievable by prompting the model alone.
