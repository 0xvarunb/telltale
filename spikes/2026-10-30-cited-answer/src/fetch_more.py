"""Second pass: reports whose PDF is linked in the page body, plus CSL Thames by direct URL."""
import json, re, sys, urllib.request, pathlib

root = pathlib.Path(sys.argv[1]); out = root / "data" / "pdf"
meta = json.loads((root / "data" / "reports.json").read_text())
have = {m["page"] for m in meta}
for slug in (root / "reports.txt").read_text().split():
    page = "https://www.gov.uk" + slug
    if page in have: continue
    try:
        with urllib.request.urlopen("https://www.gov.uk/api/content" + slug, timeout=30) as r:
            c = json.load(r)
    except Exception as e:
        print("MISS", slug, e); continue
    body = json.dumps(c.get("details", {}))
    pdfs = re.findall(r'https://assets\.publishing\.service\.gov\.uk/[^"\\ ]+?\.pdf', body)
    if not pdfs:
        print("STILL NO PDF", slug); continue
    name = slug.rsplit("/", 1)[1][:80] + ".pdf"
    urllib.request.urlretrieve(pdfs[0], out / name)
    meta.append({"title": c["title"], "page": page, "pdf": pdfs[0], "file": name, "published": c.get("first_published_at", "")[:10]})
    print("OK", c["title"][:70], "|", len(set(pdfs)), "pdf links, took first")
csl = "https://assets.publishing.service.gov.uk/media/547c6f8240f0b60244000021/CSLThames.pdf"
if not any(m["pdf"] == csl for m in meta):
    urllib.request.urlretrieve(csl, out / "csl-thames.pdf")
    meta.append({"title": "Grounding of CSL Thames in the Sound of Mull", "page": csl, "pdf": csl, "file": "csl-thames.pdf", "published": "2012-03"})
    print("OK CSL Thames")
(root / "data" / "reports.json").write_text(json.dumps(meta, indent=1))
print(len(meta), "reports")
