"""Pre-submission check: answers.json must match the format Catalist requires, exactly.

  python check_format.py

Checks (all in code, no LLM):
  1. Top level is exactly {"answers": [...], "brief": "..."}.
  2. Every answer has exactly question_id, answer, citations; 1 to 3 citations each.
  3. Every citation has exactly meeting_id and quote.
  4. All 25 ids Q01..Q25 present, in order, none missing or extra.
  5. Every quote - in the answers AND in the brief - verifies word for word
     against the transcripts, using the same verify_quote the agent uses."""
import json
import re
import sqlite3

from verify import verify_quote

problems = []
data = json.load(open("answers.json", encoding="utf-8"))
db = sqlite3.connect("memory.db")

if set(data.keys()) != {"answers", "brief"}:
    problems.append(f"top-level keys are {sorted(data.keys())}, expected ['answers', 'brief']")
if not isinstance(data.get("brief"), str) or not data["brief"].strip():
    problems.append("brief is missing, empty, or not a string")

expected_ids = [f"Q{n:02d}" for n in range(1, 26)]
seen_ids = []
for a in data.get("answers", []):
    qid = a.get("question_id", "?")
    seen_ids.append(qid)
    if set(a.keys()) != {"question_id", "answer", "citations"}:
        problems.append(f"{qid}: keys are {sorted(a.keys())}")
    if not isinstance(a.get("answer"), str) or not a["answer"].strip():
        problems.append(f"{qid}: answer is empty or not a string")
    cites = a.get("citations", [])
    if not isinstance(cites, list) or not (1 <= len(cites) <= 3):
        problems.append(f"{qid}: {len(cites)} citations (need 1-3)")
    for c in cites:
        if set(c.keys()) != {"meeting_id", "quote"}:
            problems.append(f"{qid}: citation keys are {sorted(c.keys())}")
            continue
        ok, info = verify_quote(db, c["meeting_id"], c["quote"])
        if not ok:
            problems.append(f"{qid}: quote FAILS in {c['meeting_id']}: {info} | '{c['quote'][:60]}'")

if seen_ids != expected_ids:
    missing = set(expected_ids) - set(seen_ids)
    extra = set(seen_ids) - set(expected_ids)
    problems.append(f"question ids wrong: missing={sorted(missing)} extra={sorted(extra)}")

brief_quotes = re.findall(r'\[([A-Z]{2}\d{4}[a-d])\] "([^"]+)"', data.get("brief", ""))
for meeting_id, quote in brief_quotes:
    ok, info = verify_quote(db, meeting_id, quote)
    if not ok:
        problems.append(f"brief: quote FAILS in {meeting_id}: {info} | '{quote[:60]}'")

total_cites = sum(len(a.get("citations", [])) for a in data.get("answers", []))
print(f"answers: {len(seen_ids)} | answer citations checked: {total_cites} | brief quotes checked: {len(brief_quotes)}")
if problems:
    print(f"\n{len(problems)} PROBLEM(S):")
    for p in problems:
        print(" -", p)
    raise SystemExit(1)
print("\nALL CHECKS PASSED: format matches the required schema exactly, and every quote verifies word for word.")
