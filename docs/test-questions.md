# Test questions (stage 2, being collected)

Questions for the stage 2 test set, collected before it is built. Each gets the job, the ship type,
the expected reports (chosen before the question is asked), and later a correct answer written from
the reports. Some will be kept aside and never used for tuning.

| # | Question | From | Expected reports | Notes |
| --- | --- | --- | --- | --- |
| 1 | We are about to enter a ballast tank on a bulk carrier. What has gone wrong on other ships? | Stage 1 | Ville de Mars (proposed) | used to tune the stage 1 prompt, so not a held-out question |
| 2 | We are about to moor alongside on a container ship. What has gone wrong on other ships during mooring? | Stage 1 | Fremantle Express (proposed) | used to tune the stage 1 prompt |
| 3 | We are about to lower the lifeboat for a drill on a ro-ro passenger ferry. What has gone wrong on other ships? | Stage 1 | Pride of Hampshire (proposed) | used to tune the stage 1 prompt |
| 4 | We are about to do hot work in the engine room while the cargo tanks are being gas freed on a tanker. What has gone wrong on other ships? | A reader (LinkedIn comment, 17 Nov 2026) | To be chosen | Simultaneous operations. First run below |

## Question 4, first run (17 Nov 2026, Varun's Mac)

- Top 8: Border Heather p30, Stena Europe p34, Havkong p38, Jo Eik p46, Maersk Newport p44, Mobil
  Petrel p18 and p35, Multitank Ascania p7.
- Answer: 14 sentences, all 14 cited (the first answer with none marked "no source"); $0.0034
  (7,640 in, 5,316 out). The cited quotes match the report pages (checked against the Mobil Petrel,
  Stena Europe and Maersk Newport PDFs).
- But it does not answer the job. No case is hot work during gas freeing. Closest: on Mobil Petrel,
  hydrocarbon gas leaked from the cargo side into the engine room through the pump bulkhead glands.
  Maersk Newport gives hot work permit arrangements on a container ship; Stena Europe is engine room
  fires from fuel sprayed on hot surfaces, a different job. It quotes a company notice ("on our
  vessels") without saying whose. It never says the reports do not cover the combined job.
- No report title among the 619 mentions hot work or gas freeing. Tanker explosion reports exist
  (Border Heather, Esso Mersey, Stolt Groenland); whether their text covers this is not yet checked.
- Lesson: every sentence cited does not mean the question was answered. The test set has to score
  relevance as well as citations.
