# Telltale

Telltale tells officers on merchant ships what went wrong on other ships doing the same job,
from published accident investigation reports (now in stage 3: merchant-ship reports from UK
MAIB, US NTSB and Singapore TSIB). Each sentence of its answer links to the report passage it cites, or is marked "no source".

## Rule 0: the tool never invents a fact

Answers use only the report passages the search finds. The model is told to add nothing from its
own knowledge: no procedures, regulations or figures it was not given. If the reports do not cover
the job, it says there is no reliable answer in these reports. Today a sentence without a source
is marked "no source", not removed; removing it is planned within stage 3 and is not built yet.
It never says a job is safe and never replaces the permit to work, the risk assessment, the SMS
or the office's permission.

What it can't do: [docs/limits.md](docs/limits.md) (also at `/limits` on the local page).

## How to run it

You need [uv](https://docs.astral.sh/uv/); it installs Python 3.12 for you.

Run every command from the repository root: `.env` and the `data/` folder are looked up relative
to the current folder.

1. Install:

   ```
   uv sync
   ```

2. Add your Anthropic API key: copy `.env.example` to `.env` and put the key after
   `ANTHROPIC_API_KEY=`. `.env` is git-ignored; never commit it. `ingest` does not need the key;
   `ask` and `serve` do.

3. Download the reports and build the search index:

   ```
   uv run telltale ingest
   ```

   This needs internet access to the hosts of each publisher it reads: www.gov.uk and
   assets.publishing.service.gov.uk (MAIB), www.ntsb.gov (NTSB investigation pages and PDFs), and
   www.mot.gov.sg and isomer-user-content.by.gov.sg (TSIB listing and PDFs). The first time, it
   also needs huggingface.co for the embedding model, which is then kept in `data/models` for
   offline use. It makes one request per report, so the full run takes a while; `--limit N`
   stops after N reports. It is safe to re-run: PDFs already downloaded are kept. A re-run also
   reuses the saved embedding of any page group whose text has not changed, so only new or changed
   reports are embedded. At the end it prints the counts and lists every report it could not make
   searchable, with its page link where it has one.

   By itself, `ingest` downloads UK MAIB reports. `--source` picks another publisher:

   ```
   uv run telltale ingest --source tsib
   uv run telltale ingest --source ntsb --ntsb-export path/to/carol.json
   uv run telltale ingest --source all --ntsb-export path/to/carol.json
   ```

   - `maib` (the default): UK Marine Accident Investigation Branch.
   - `tsib`: Singapore Transport Safety Investigation Bureau, listed from www.mot.gov.sg, with
     each PDF from isomer-user-content.by.gov.sg.
   - `ntsb`: US National Transportation Safety Board. NTSB's search (CAROL) cannot be read by a
     script without a key, so you save its results once in a browser: open
     https://my.ntsb.gov/basic, search in Marine mode with no other filter, press "Download Data
     (JSON)", and pass the saved file with `--ntsb-export`. Each report's page and PDF then come
     from www.ntsb.gov.
   - `all`: MAIB, then NTSB, then TSIB. Without `--ntsb-export` it says so, skips NTSB and carries
     on with the others.
   - `atsb` (Australia) is not available yet: its website refuses scripted requests, so `ingest`
     refuses it before making any request.

   With `all`, `--limit N` stops after N reports from each publisher. Whatever the source, the
   search index is rebuilt from every report in the store, and the counts at the end are given
   for each publisher and in total.
   Everything it stores goes in `data/` (`data/telltale.sqlite`, `data/pdf/`, `data/index/`, `data/ocr/`),
   which is git-ignored.

4. Ask from the command line:

   ```
   uv run telltale ask "We are about to enter a ballast tank on a bulk carrier. What has gone wrong on other ships?"
   ```

   It prints numbered sentences, `[no source]` on any sentence without a source, the numbered
   sources (report, page and PDF link) and the cost of the answer.

5. Or use the local web page:

   ```
   uv run telltale serve
   ```

   Then open http://127.0.0.1:8000. The page has no login and every question spends API credit,
   so keep it on this computer (the default, 127.0.0.1).

6. Count the reports by department (no API key needed):

   ```
   uv run telltale departments
   ```

   It sorts the stored reports into Bridge and navigation, Deck, Engine, and Catering and
   accommodation from the words in their titles, prints the counts and writes
   `data/departments.csv` with the words that placed each report, for checking. A report can be in
   more than one department; one whose title is too general stays unsorted.

7. Make an answer card, a phone-sized image (1080 x 1350) for posts and comments:

   ```
   uv run telltale ask "QUESTION" --save data/results/answer.json --card data/results/answer.png
   ```

   The card shows the question, the sentences word for word with their source numbers or "no
   source" mark, the sources they cite, and the cost. Choose sentences with `--sentences 1-4,7`.
   To redraw from a saved answer without asking (or paying) again:

   ```
   uv run telltale card data/results/answer.json --out data/results/card.png --sentences 1-4
   ```

   The card fonts (EB Garamond and Geist) are bundled under the SIL Open Font License 1.1; the
   licence texts are in `src/telltale/card_assets/`.

8. Score it against a set of test questions (stage 2):

   ```
   uv run telltale eval --batch
   ```

   It asks each working question, then a second model (Claude Sonnet 5.5 unless you pass
   `--judge-model`) reads every cited sentence against its passage and the title of its report and marks it supported, partly
   or not supported. A sentence with no citation counts as not backed. No more questions are asked
   once the run's cost reaches `--max-cost` (default $1.00). `--batch` sends the answers, then the
   checks, as Message Batches at half the live price; each batch can take up to 24 hours.
   `--only A3,A7` runs only those questions. Each run is saved under `data/eval/runs/`, with a
   `report.md` and a review sheet of 30 checked sentences for a person to mark, so the checker is
   itself checked before its numbers are quoted. To rebuild a run's report without asking again:

   ```
   uv run telltale eval-report data/eval/runs/RUN_FOLDER
   ```

   To judge a run's saved answers again (after a change to the checker) without asking again:

   ```
   uv run telltale eval-rejudge data/eval/runs/RUN_FOLDER
   ```

   It writes a new run folder named after the source run, as one batch at half the price.

   The question file (`docs/private/eval/questions.json`) is not in this repository. The 33
   working questions and their chosen reports are published in [docs/test-set.md](docs/test-set.md);
   the other 17 are held back for one final run (`--final`) and never published, so the tool can't
   be tuned to them. Without the file, `telltale eval` stops before asking anything.

Tests: `uv run pytest`. They make no network calls and need no API key.

## Data credit

Report text comes from three publishers:

- UK Marine Accident Investigation Branch (MAIB): investigation reports, safety bulletins and
  preliminary examinations, published on GOV.UK. Crown copyright, used under the
  [Open Government Licence v3.0](https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/).
- US National Transportation Safety Board (NTSB): marine accident reports and briefs, published
  on www.ntsb.gov. US Government work, not subject to copyright in the US.
- Transport Safety Investigation Bureau, Singapore (TSIB): final reports listed on the Ministry
  of Transport's website, www.mot.gov.sg. Public reports, each cited and linked to its PDF.

No report PDFs or report text are kept in this repository; `telltale ingest` downloads them from
each publisher's own site. Report photographs and figures are not used.

## Build log

The public build log (every change, why it was made, and what it measured) is the static page in
`site/`, served by Vercel at https://telltale-log.vercel.app/.

## Licence

The code is under the MIT licence (`LICENSE`). The MAIB, NTSB and TSIB reports are not part of
this repository and keep their own terms, above.

## Skills map

Telltale is built in six stages, one AI skill per stage, each posted on LinkedIn.

| Stage | AI concept | In plain words | Where in the code | Post |
| --- | --- | --- | --- | --- |
| 1 | RAG (retrieval-augmented generation) | Find the matching report passages, then answer only from them | `src/telltale/search`, `src/telltale/answer` | Tue 24 Nov |
| 2 | Evals | Score the tool against a set of questions with answers taken from the reports | `src/telltale/evals` | coming |
| 3 | Guardrails | Enforce the rules, starting with rule 0: remove any sentence its passage does not support | coming | coming |
| 4 | Agents | Add the port state control side and put together the job briefing and the safety meeting pack | coming | coming |
| 5 | LLM ops | Trace every model call, with its cost and time, and re-run the evals on every change | coming | coming |
| 6 | Fine-tuning | A small model that can run on board without a connection, compared on the evals | coming | coming |

The concepts used so far are explained in plain English in [docs/concepts.md](docs/concepts.md).
Stage 1 results: [docs/stage-1-results.md](docs/stage-1-results.md). Stage 2 results:
[docs/stage-2-results.md](docs/stage-2-results.md). Test set: [docs/test-set.md](docs/test-set.md).
Stage 2 plan:
[docs/plans/2026-11-19-stage-2-evals.md](docs/plans/2026-11-19-stage-2-evals.md). Build log:
https://telltale-log.vercel.app/ (early entries also in [docs/build-log.md](docs/build-log.md)).
