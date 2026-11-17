# Global constraints for every Stage 1 task (verbatim from the plan, plus controller rulings)

- Rule 0: the tool never invents a fact. Answers use only the retrieved passages; no procedures, figures or regulation text from the model's own knowledge.
- Lead each case with what happened and how it ended, then how, then what the report says would have prevented it. No blame. No checklist, form or anything to sign. Never "safe to proceed".
- Every case links to its report (PDF and page) and carries "Crown copyright" credit. Text only; no report photos or figures.
- Model: `claude-haiku-5-5`. Prices for cost display: $0.10 per million input tokens, $0.50 per million output tokens. Do not send `temperature`, `top_p` or `top_k`.
- Embedding model runs locally on CPU (offline target is a CPU-only ship's office laptop).
- Public repo rules: never commit `.env`, API keys, report PDFs, extracted report text, the SQLite store or vectors (`data/` and `spikes/**/data`, `spikes/**/.venv`, `spikes/**/result-*.json` are git-ignored). Test fixtures are synthetic and name no real ship, person or company.
- Never push to GitHub. Never merge into `main`. Work only on branch `stage-1-report-search`.
- UI copy: British spelling, no em dashes, plain words. Fonts EB Garamond (headings), Geist (text), JetBrains Mono (figures); colours paper `#f4f1ea`, ink `#13283b`, orange `#e8501a`, green `#2f6b4a`.
- Commits end with: `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`
- SECRETS: never read, cat, print, grep or echo any `.env*` file or the API key. Code may load the key from `.env` via `load_settings()`; you never look at it.
- Data paths (controller ruling): SQLite store `data/telltale.sqlite`, PDFs `data/pdf/`, search index `data/index/`.
- Run Python only through `uv run ...` in the repo root (Python 3.12 managed by uv). Do not use the system python3 (3.9).
