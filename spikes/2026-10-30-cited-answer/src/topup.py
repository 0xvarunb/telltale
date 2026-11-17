"""Top up to ~20 merchant-ship reports with PDF attachments from GOV.UK search."""
import json, sys, urllib.request, urllib.parse, pathlib

root = pathlib.Path(sys.argv[1]); out = root / "data" / "pdf"
meta = json.loads((root / "data" / "reports.json").read_text())
have = {m["page"] for m in meta}
skip = ("fishing", "trawler", "yacht", "cruiser", "potter", "dory", "rib ", "kayak", "canoe", "safety warning", "creel", "scallop", "angling", "leisure")
for q in ["enclosed space", "cargo hold", "engine room fire", "mooring", "tank entry", "bunkering"]:
    if len(meta) >= 21: break
    url = "https://www.gov.uk/api/search.json?" + urllib.parse.urlencode({"filter_format": "maib_report", "q": q, "count": 30, "fields": "title,link"})
    for r in json.load(urllib.request.urlopen(url, timeout=30))["results"]:
        if len(meta) >= 21: break
        page = "https://www.gov.uk" + r["link"]
        if page in have or any(s in r["title"].lower() for s in skip): continue
        c = json.load(urllib.request.urlopen("https://www.gov.uk/api/content" + r["link"], timeout=30))
        atts = [a for a in c.get("details", {}).get("attachments", []) if str(a.get("url", "")).lower().endswith(".pdf")]
        if not atts: continue
        a = max(atts, key=lambda x: x.get("file_size") or 0)
        name = r["link"].rsplit("/", 1)[1][:80] + ".pdf"
        urllib.request.urlretrieve(a["url"], out / name)
        meta.append({"title": c["title"], "page": page, "pdf": a["url"], "file": name, "published": c.get("first_published_at", "")[:10]})
        have.add(page); print("OK", c["title"][:90])
(root / "data" / "reports.json").write_text(json.dumps(meta, indent=1))
print(len(meta), "reports")
