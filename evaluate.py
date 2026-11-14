"""Evaluation: run the agent on our own hand-checked questions and score it in code.

  python evaluate.py          -> runs every question in eval_questions.json
  python evaluate.py E05 E08  -> runs only those

Scoring (all plain code, no LLM):
  - key facts:   a fact counts as found if ANY of its any_of phrases appears in the
                 answer (case-insensitive, word-boundary match)
  - citations:   every quote must pass verify_quote (word-for-word in ONE turn)
  - meetings:    at least 2 different meetings must be cited
  Verdict: RIGHT   = all facts found, 1+ citations, all valid, 2+ meetings
           PARTIAL = at least half the facts and at least one valid citation
           WRONG   = anything less

Writes eval_results.md (the report) and eval_answers.json (raw agent output).
Known limitation: substring facts cannot see negation ("was NOT dropped" still
matches "dropped"), so read the saved answers before trusting a verdict."""
import json
import re
import sys
import time

import agent
from verify import verify_quote

RESULTS_MD = "eval_results.md"
RAW_JSON = "eval_answers.json"


def fact_found(answer, any_of):
    low = answer.lower()
    return any(re.search(r"\b" + re.escape(alt.lower()) + r"\b", low) for alt in any_of)


def score(q, state):
    answer = state.get("answer") or ""
    cites = state.get("citations") or []
    facts = q["key_facts"]
    found = [f["fact"] for f in facts if fact_found(answer, f["any_of"])]
    missing = [f["fact"] for f in facts if f["fact"] not in found]
    valid = sum(1 for c in cites if verify_quote(agent.DB, c["meeting_id"], c["quote"])[0])
    meetings = len({c["meeting_id"] for c in cites})
    if len(found) == len(facts) and cites and valid == len(cites) and meetings >= 2:
        verdict = "RIGHT"
    elif len(found) * 2 >= len(facts) and valid >= 1:
        verdict = "PARTIAL"
    else:
        verdict = "WRONG"
    return {"found": found, "missing": missing, "facts_total": len(facts),
            "citations": len(cites), "citations_valid": valid, "meetings_cited": meetings,
            "verdict": verdict}


def main(only):
    agent.setup()
    data = json.load(open("eval_questions.json", encoding="utf-8"))
    questions = [q for q in data["questions"] if not only or q["eval_id"] in only]
    raw, rows = {}, []

    for q in questions:
        print(f"\n===== {q['eval_id']} ({q['team']}) =====\n{q['question']}")
        start = time.time()
        try:
            state = agent.run(q["question"])
        except Exception as error:
            print(f"  AGENT FAILED: {type(error).__name__}: {error}")
            state = {"answer": f"AGENT ERROR: {error}", "citations": []}
        seconds = round(time.time() - start, 1)
        s = score(q, state)
        raw[q["eval_id"]] = {"question": q["question"], "answer": state.get("answer", ""),
                             "citations": state.get("citations", []), "score": s,
                             "rounds": state.get("round", 0), "tokens": state.get("tokens", 0),
                             "seconds": seconds}
        rows.append((q, s))
        print(f"  ANSWER: {state.get('answer', '')}")
        print(f"  -> {s['verdict']}: facts {len(s['found'])}/{s['facts_total']}"
              + (f" (missing: {', '.join(s['missing'])})" if s["missing"] else "")
              + f", citations {s['citations_valid']}/{s['citations']} valid, {s['meetings_cited']} meeting(s), {seconds}s")
        with open(RAW_JSON, "w", encoding="utf-8") as f:
            json.dump(raw, f, indent=2, ensure_ascii=False)

    right = sum(1 for _, s in rows if s["verdict"] == "RIGHT")
    partial = sum(1 for _, s in rows if s["verdict"] == "PARTIAL")
    wrong = sum(1 for _, s in rows if s["verdict"] == "WRONG")

    lines = ["# Evaluation results", "",
             f"{len(rows)} hand-checked questions (see eval_questions.json). "
             f"**{right} RIGHT, {partial} PARTIAL, {wrong} WRONG.**", "",
             "Scored in code: key facts by word-boundary match, every citation re-verified "
             "word-for-word with verify_quote, and at least two meetings must be cited. "
             "Raw agent answers: eval_answers.json.", "",
             "| id | team | verdict | key facts | citations valid | meetings cited | missing facts |",
             "|---|---|---|---|---|---|---|"]
    for q, s in rows:
        lines.append(f"| {q['eval_id']} | {q['team']} | {s['verdict']} | {len(s['found'])}/{s['facts_total']} "
                     f"| {s['citations_valid']}/{s['citations']} | {s['meetings_cited']} "
                     f"| {', '.join(s['missing']) or '-'} |")
    with open(RESULTS_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"\n{right} RIGHT, {partial} PARTIAL, {wrong} WRONG -> {RESULTS_MD}")


if __name__ == "__main__":
    main(sys.argv[1:])
