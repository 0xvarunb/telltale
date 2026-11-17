# Concepts used so far

Plain-English notes on the AI ideas in Telltale, each tied to the code that uses it. Only what the
code actually does is described here. Stage 1.

## RAG (retrieval-augmented generation)

A language model on its own answers from whatever it picked up in training, and can sound sure
when it is wrong. RAG changes the order of work: first find the passages in your own documents
that match the question (retrieval), then give only those passages to the model and ask it to
write its answer from them (generation).

In Telltale:

- The search finds the 8 best-matching groups of report passages for the officer's question:
  `HybridIndex.search` in `src/telltale/search/index.py`, called with `SEARCH_K = 8` by `answer`
  in `src/telltale/answer/brief.py`.
- Those 8 groups go to Claude Haiku 5.5 as search results, with an instruction to use only them:
  `SYSTEM_PROMPT` and `build_request` in `src/telltale/answer/prompt.py`.
- If the search finds nothing, the model is not called at all and the answer says there is no
  reliable answer in these reports (`answer` in `src/telltale/answer/brief.py`).

**Passages and groups.** Before any of this, each report PDF is cut into short passages of a
sentence or two (up to about 220 characters, never splitting a sentence), and the passages on one
page are grouped, up to 12 to a group: `to_passages` and `group_by_page` in
`src/telltale/ingest/extract.py`. A group is the unit the search finds and the model cites; it
keeps its report title, PDF link and page number (`Chunk` in `src/telltale/models.py`). The groups
are stored in SQLite by `src/telltale/ingest/store.py`.

## Embeddings

An embedding turns a piece of text into a list of numbers (a vector) so that texts with a similar
meaning get similar numbers, even when they use different words. "Entering a ballast tank" and
"went down into the double bottom space" share few words but can sit close together.

In Telltale:

- The model is `BAAI/bge-small-en-v1.5`, a small embedding model run on the laptop's CPU through
  fastembed (no graphics card, no internet once downloaded): `FastEmbedder` in
  `src/telltale/search/embed.py`. It is downloaded once into `data/models`.
- Each group's text (its report title plus its passages, `index_text` in
  `src/telltale/search/index.py`) becomes one vector. The vectors are scaled to length 1, so the
  similarity of a question and a group is a single multiplication and sum (`_vector_ranking` in
  `src/telltale/search/index.py`).
- Every `telltale ingest` rebuilds the vectors for all stored groups (never reusing old ones, so a
  changed report title cannot leave a stale vector) and saves them as `data/index/vectors.npy`.
  The model's name is saved beside them in `data/index/index_meta.json`, and a saved index built
  with a different model is refused, so a search never runs against the wrong vectors
  (`HybridIndex.save` and `HybridIndex.load` in `src/telltale/search/index.py`).
- The model reads at most 512 tokens of each group, so the end of a very long group does not count
  towards its vector. Keyword search (below) still sees all of it.

## Keyword search (BM25)

Keyword search finds the groups that contain the question's words. BM25 is the standard way to
score this: a word counts for more when it is rare across all the reports ("ballast" more than
"ship"), and repeating a word in one group helps less and less.

In Telltale:

- Text is cut into lower-case words (`tokenize` in `src/telltale/search/index.py`) and scored with
  `BM25Okapi` from the rank-bm25 library (`_keyword_ranking` in the same file). Groups that score
  nothing are left out of its list.
- Keyword search catches exact terms an embedding can blur, such as a tank number or a piece of
  equipment by name.

## Rank fusion (RRF)

Telltale runs both searches and merges them. Each search keeps its top 40 groups (`TOP_N`). The
two lists are combined by reciprocal rank fusion: a group scores 1 / (60 + its place) in each list,
the two scores are added, and the highest totals win (`rrf` in `src/telltale/search/index.py`).
A group near the top of both lists beats one that only one search liked. The top 8 go to the
model.

## OCR (reading text from scanned pages)

Some older MAIB reports are scans: each page is a picture, with no text a computer can read. OCR
(optical character recognition) reads the words off the picture. Telltale does this only for pages
with no text layer, on the laptop's CPU and without internet: it renders the page as an image
(pypdfium2) and reads it with a small OCR model (RapidOCR). Lines the model is unsure of are left
out, and every passage read this way is marked "scanned page, text read by OCR" wherever it is
cited, because OCR can misread a word or a figure. Results are kept in `data/ocr/` so a page is
read only once.

Code: `src/telltale/ingest/ocr.py`, `read_pages` in `src/telltale/ingest/extract.py`, the mark in
`Store.chunks` (`src/telltale/ingest/store.py`).

## Reports published as a web page

MAIB publishes a completed preliminary examination as the text of its GOV.UK page, with no PDF.
For a report with no PDF, Telltale reads the page body from the GOV.UK content API: each
paragraph, list item and heading becomes its own block, each table row one line ("Flag: ..."),
and pictures are left out. The passages are cut as for a PDF page, and cited with the page's
link, which has no page numbers.

Code: `src/telltale/ingest/webpage.py`, `page_html` in `_to_report`
(`src/telltale/sources/maib.py`), the no-PDF branch of `_store_text` (`src/telltale/cli.py`).

## Citations

A citation ties a sentence of the answer to the exact passage it came from, so the officer can
check it.

In Telltale:

- Each group is sent to the model as a `search_result` block with citations switched on, its
  source set to the report PDF link with its page (`...pdf#page=N`), or the GOV.UK page for a
  report with no PDF, and its first line naming the report: `build_request` in `src/telltale/answer/prompt.py`.
- The model's reply comes back in pieces, each carrying the passages it cites. `parse_sentences`
  in `src/telltale/answer/brief.py` joins the pieces, splits them into sentences
  (`src/telltale/text.py`, which does not split on "15.87m", "No. 3" or "e.g."), and gives each
  sentence every citation of the pieces it overlaps.
- A sentence with no citation is shown with "no source": `[no source]` from `print_answer` in
  `src/telltale/cli.py`, and the orange mark in `src/telltale/web/templates/index.html`. Each
  cited passage is listed with its report, page, a link to that page of the PDF and "Crown
  copyright".
- In stage 1 the tool marks these sentences but does not remove them, and it does not yet check
  that a cited passage really supports its sentence. Both are stage 3 work. See
  [limits.md](limits.md).
