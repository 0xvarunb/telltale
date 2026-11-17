"""Extract report text into small citable passages (one or two sentences each), grouped in chunks."""
import json, re, sys, pathlib
from pypdf import PdfReader

root = pathlib.Path(sys.argv[1])
meta = json.loads((root / "data" / "reports.json").read_text())
chunks = []
for m in meta:
    reader = PdfReader(root / "data" / "pdf" / m["file"])
    for pno, page in enumerate(reader.pages, 1):
        text = re.sub(r"[ \t]+", " ", page.extract_text() or "")
        text = re.sub(r"-\n(\w)", r"\1", text)
        text = re.sub(r"\s*\n\s*", " ", text).strip()
        sents = [s.strip() for s in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9(])", text) if len(s.strip()) > 2]
        # passages of up to ~2 sentences; chunks of ~12 passages
        passages, buf = [], ""
        for s in sents:
            buf = (buf + " " + s).strip()
            if len(buf) > 220:
                passages.append(buf); buf = ""
        if buf: passages.append(buf)
        for i in range(0, len(passages), 12):
            chunks.append({"report": m["title"], "pdf": m["pdf"], "page": pno, "passages": passages[i:i + 12]})
(root / "data" / "chunks.json").write_text(json.dumps(chunks))
print(len(meta), "reports,", len(chunks), "chunks,", sum(len(c["passages"]) for c in chunks), "passages")
