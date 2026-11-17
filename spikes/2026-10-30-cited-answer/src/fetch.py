"""Fetch MAIB report PDFs listed in reports.txt via the GOV.UK content API."""
import json, sys, urllib.request, pathlib

root = pathlib.Path(sys.argv[1])
out = root / "data" / "pdf"
meta = []
for slug in (root / "reports.txt").read_text().split():
    try:
        with urllib.request.urlopen("https://www.gov.uk/api/content" + slug, timeout=30) as r:
            c = json.load(r)
    except Exception as e:
        print("MISS", slug, e); continue
    atts = [a for a in c.get("details", {}).get("attachments", []) if a.get("content_type") == "application/pdf" or str(a.get("url", "")).lower().endswith(".pdf")]
    if not atts:
        print("NO PDF", slug); continue
    a = max(atts, key=lambda x: x.get("file_size") or 0)
    name = slug.rsplit("/", 1)[1][:80] + ".pdf"
    urllib.request.urlretrieve(a["url"], out / name)
    meta.append({"title": c["title"], "page": "https://www.gov.uk" + slug, "pdf": a["url"], "file": name,
                 "published": c.get("first_published_at", "")[:10]})
    print("OK", c["title"][:70])
(root / "data" / "reports.json").write_text(json.dumps(meta, indent=1))
print(len(meta), "reports")
