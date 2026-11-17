"""Spike: BM25 search -> cited answer (search_result citations) -> sentence-support check.
Usage: run.py ROOT "question" [--plant]"""
import json, re, sys, pathlib
from rank_bm25 import BM25Okapi
import anthropic

root = pathlib.Path(sys.argv[1]); question = sys.argv[2]; plant = "--plant" in sys.argv
MODEL = "claude-haiku-5-5"
PRICE_IN, PRICE_OUT = 0.10 / 1e6, 0.50 / 1e6

def load_key():
    for line in pathlib.Path.home().joinpath("projects/greencrew/.env.local").read_text().splitlines():
        if line.startswith("ANTHROPIC_API_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("no key found")

client = anthropic.Anthropic(api_key=load_key())
usage = {"in": 0, "out": 0}
def call(**kw):
    r = client.messages.create(model=MODEL, **kw)
    usage["in"] += r.usage.input_tokens; usage["out"] += r.usage.output_tokens
    return r

# 1. search
chunks = json.loads((root / "data" / "chunks.json").read_text())
tok = lambda s: re.findall(r"[a-z0-9]+", s.lower())
bm = BM25Okapi([tok(" ".join(c["passages"])) for c in chunks])
scores = bm.get_scores(tok(question))
top = sorted(range(len(chunks)), key=lambda i: -scores[i])[:8]
results = [chunks[i] for i in top]

# 2. cited answer
SYSTEM = ("You brief officers on merchant ships before a job. Use only the search results provided. "
          "Add nothing from your own knowledge: no procedures, regulations, figures or advice the results do not state. "
          "For each relevant case, say first what happened and how it ended, then how it happened, then what the report says "
          "would have prevented it. No blame. Plain English, short paragraphs, no checklist, nothing to sign. "
          "If the results do not cover the job, say there is no reliable answer in these reports. "
          "Every sentence must come from a search result. If you say what the results do not cover, keep it to one short sentence. No headings, no introduction, no summary of what the results contain; "
          "name the ship inside the first sentence about it.")
content = [{"type": "search_result", "source": f'{c["pdf"]}#page={c["page"]}', "title": f'{c["report"]} (page {c["page"]})',
            "content": [{"type": "text", "text": f'This passage is from the MAIB report: {c["report"]}.'}] + [{"type": "text", "text": p} for p in c["passages"]], "citations": {"enabled": True}} for c in results]
content.append({"type": "text", "text": question})
resp = call(max_tokens=8000, system=SYSTEM, messages=[{"role": "user", "content": content}])

# 3. split the answer into sentences; a sentence carries the citations of every block it spans
spans, pos, full = [], 0, ""
for b in resp.content:
    if b.type != "text": continue
    cites = [{"title": c.title, "source": c.source, "text": c.cited_text} for c in (b.citations or [])]
    spans.append((len(full), len(full) + len(b.text), cites)); full += b.text
sentences = []
for m in re.finditer(r"[^.!?\n]+(?:[.!?]+|$)", full):
    s_txt = m.group().strip(" *\n")
    if len(re.findall(r"\w+", s_txt)) < 4: continue
    cites = [c for a, z, cs in spans if a < m.end() and z > m.start() for c in cs]
    sentences.append({"sentence": s_txt, "cites": cites})
print("stop_reason:", resp.stop_reason)

answer_text = "".join(b.text for b in resp.content if b.type == "text")

# optional: plant false sentences to prove the check can fail
if plant:
    planted = []
    for s in [x for x in sentences if x["cites"]][:3]:
        t = s["sentence"]
        m = re.search(r"\d", t)
        bad = (t[:m.start()] + str((int(m.group()) + 3) % 10) + t[m.end():]) if m else t.rstrip(".") + ", on a chemical tanker in Singapore."
        planted.append({"sentence": bad, "cites": s["cites"], "planted": True})
    sentences += planted

# 4. check each sentence against its cited passages
JUDGE = ("You check a briefing for ship's officers against its sources. Reply with JSON only: "
         '{"kind": "fact" or "framing", "supported": "yes" or "partly" or "no", "unsupported_part": "..."}. '
         '"framing" means the sentence makes no factual claim (an introduction or a link between cases). '
         "A fact is supported only if every detail in it (numbers, places, people, equipment, outcomes, causes) is stated in the passages.")
for s in sentences:
    passages = "\n---\n".join(f'[{c["title"]}] {c["text"]}' for c in s["cites"]) or "(no passage cited)"
    r = call(max_tokens=1500, system=JUDGE, messages=[{"role": "user", "content": f"SENTENCE:\n{s['sentence']}\n\nPASSAGES:\n{passages}"}])
    txt = "".join(b.text for b in r.content if b.type == "text")
    m = re.search(r"\{.*\}", txt, re.S)
    try: s["verdict"] = json.loads(m.group()) if m else {"kind": "?", "supported": "?", "unsupported_part": txt[:200]}
    except Exception: s["verdict"] = {"kind": "?", "supported": "?", "unsupported_part": txt[:200]}

# 5. guardrail: keep only sentences judged supported
kept = [x["sentence"] for x in sentences if not x.get("planted") and x["verdict"].get("supported") == "yes"]
final_answer = " ".join(kept)

# 6. report
facts = [s for s in sentences if not s.get("planted") and s["verdict"].get("kind") == "fact"]
bad = [s for s in facts if s["verdict"].get("supported") != "yes"]
pl = [s for s in sentences if s.get("planted")]
caught = [s for s in pl if s["verdict"].get("supported") != "yes"]
cost = usage["in"] * PRICE_IN + usage["out"] * PRICE_OUT
out = {"question": question, "answer": answer_text, "final_answer": final_answer, "stop_reason": resp.stop_reason, "sentences": sentences, "searched": [f'{c["report"]} p{c["page"]}' for c in results],
       "fact_sentences": len(facts), "unsupported": len(bad), "planted": len(pl), "planted_caught": len(caught),
       "tokens": usage, "cost_usd": round(cost, 4)}
slug = re.sub(r"\W+", "-", question.lower())[:50]
(root / f"result-{slug}.json").write_text(json.dumps(out, indent=1))
print("ANSWER:\n" + answer_text + "\n")
print("FINAL (supported sentences only):\n" + final_answer + "\n")
print(f"fact sentences: {len(facts)}  unsupported: {len(bad)}  planted: {len(pl)}  planted caught: {len(caught)}  cost: ${cost:.4f}")
for s in sentences:
    v = s["verdict"]; flag = "PLANTED " if s.get("planted") else ""
    print(f'- {flag}[{v.get("kind")}/{v.get("supported")}] cites={len(s["cites"])} :: {s["sentence"][:150]}' + (f'  >> {v.get("unsupported_part","")[:160]}' if v.get("supported") != "yes" else ""))
