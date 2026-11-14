"""Step 7: answer all 25 questions and write the TS3012 brief into answers.json.

  python run_all.py            -> answer every question not answered yet, then write the brief
  python run_all.py Q10 Q14    -> (re)answer only these questions
  python run_all.py brief      -> (re)write only the brief"""
import json
import os
import sys
import time

import agent

OUTPUT = "answers.json"
LOG = "run_log.json"
BRIEF_TEAM = "TS3012"

BRIEF_PROMPT = """You are preparing a team for its FINAL meeting. Below are notes from the team's EARLIER
meetings only, oldest first. Each note has an id like L7, the meeting, a type, a topic, a status,
and what was said. A later note on the same topic overrides an earlier one.

SPEAKER ROLES: {roles}

NOTES:
{notes}

Write the brief as four lists. Each point is one plain sentence, and must give the ids of 1 or 2
notes that prove it. Use speaker letters (with role if known). At most 6 points per list.
- "decided": what currently stands decided (the latest state of each topic)
- "changed": what changed along the way (say what it was before and what it became)
- "open_actions": things someone still has to do, with "owner" as the speaker letter
- "disagreements": objections or disputes that were not clearly settled

Reply with JSON only:
{{"decided": [{{"point": "...", "ids": ["L1"]}}],
  "changed": [{{"point": "...", "ids": ["L2", "L5"]}}],
  "open_actions": [{{"point": "...", "owner": "B", "ids": ["L9"]}}],
  "disagreements": [{{"point": "...", "ids": ["L4"]}}]}}"""

HEADINGS = [("decided", "WHAT HAS BEEN DECIDED"), ("changed", "WHAT CHANGED ALONG THE WAY"),
            ("open_actions", "OPEN ACTIONS AND OWNERS"), ("disagreements", "DISAGREEMENTS STILL OPEN")]


def load(path, default):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return default


def save(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def write_brief():
    """LLM chooses and words the points; code supplies every quote from the ledger."""
    db = agent.DB
    meetings = [m for (m,) in db.execute(
        "SELECT DISTINCT meeting_id FROM turns WHERE team = ? ORDER BY meeting_id", (BRIEF_TEAM,))]
    earlier = meetings[:-1]                                    # the brief is read BEFORE the final meeting
    marks = ",".join("?" * len(earlier))
    rows = db.execute(
        f"SELECT meeting_id, type, topic, status, speaker, statement, quote FROM ledger "
        f"WHERE meeting_id IN ({marks}) AND status != 'recap' ORDER BY meeting_number, turn_index", earlier).fetchall()
    if not rows:
        return f"No ledger records for {BRIEF_TEAM}; run extract.py first."
    lookup = {f"L{n}": (r[0], r[6]) for n, r in enumerate(rows, 1)}
    notes = "\n".join(f"[L{n}] {r[0]} | {r[1]} | {r[2]} | {r[3]} | Speaker {r[4]}: {r[5]}" for n, r in enumerate(rows, 1))

    state = {}
    reply = agent.ask_json(BRIEF_PROMPT.format(roles=agent.team_roles(BRIEF_TEAM) or "unknown", notes=notes), state)

    lines = [f"BRIEF FOR TEAM {BRIEF_TEAM} BEFORE THEIR FINAL MEETING",
             f"Based on meetings {', '.join(earlier)}."]
    for key, heading in HEADINGS:
        lines += ["", heading]
        points = 0
        for item in reply.get(key, []) if isinstance(reply.get(key), list) else []:
            cites = [lookup[i] for i in item.get("ids", []) if i in lookup][:2]
            if not item.get("point") or not cites:             # a point with no real evidence is left out
                continue
            owner = f" (owner: Speaker {item['owner']})" if item.get("owner") else ""
            lines.append(f"- {item['point']}{owner}")
            lines += [f'    [{meeting_id}] "{quote}"' for meeting_id, quote in cites]
            points += 1
        if not points:
            lines.append("- Nothing found in the earlier meetings.")
    print(f"[brief] {len(rows)} notes from {len(earlier)} meetings, {state.get('tokens', 0)} tokens")
    return "\n".join(lines)


def main(args):
    agent.setup()
    questions = json.load(open("questions.json", encoding="utf-8"))["questions"]
    output = load(OUTPUT, {"answers": [], "brief": ""})
    log = load(LOG, {})
    answered = {a["question_id"]: a for a in output["answers"]}

    only_brief = args == ["brief"]
    chosen = [q for q in questions if (q["question_id"] in args if args else q["question_id"] not in answered)]
    for q in [] if only_brief else chosen:
        print(f"\n===== {q['question_id']} =====")
        start = time.time()
        try:
            state = agent.run(q["question"])
        except Exception as error:                             # one bad question must not stop the run
            print(f"  FAILED: {type(error).__name__}: {error}")
            continue
        answered[q["question_id"]] = {"question_id": q["question_id"], "answer": state.get("answer", ""),
                                      "citations": state.get("citations", [])}
        log[q["question_id"]] = {"rounds": state.get("round", 0), "tokens": state.get("tokens", 0),
                                 "seconds": round(time.time() - start, 1),
                                 "citations": len(state.get("citations", [])),
                                 "meetings_cited": len({c["meeting_id"] for c in state.get("citations", [])})}
        print("ANSWER:", state.get("answer"))
        output["answers"] = [answered[k] for k in sorted(answered)]
        save(OUTPUT, output)                                   # save after every question
        save(LOG, log)

    if only_brief or not args:
        print("\n===== BRIEF =====")
        output["brief"] = write_brief()
        save(OUTPUT, output)
        print(output["brief"])

    weak = [k for k, v in sorted(log.items()) if v["citations"] == 0 or v["meetings_cited"] < 2]
    print(f"\nSaved {len(output['answers'])} answers to {OUTPUT}.")
    if weak:
        print("Check these by hand (no citation, or only one meeting cited):", ", ".join(weak))


if __name__ == "__main__":
    main(sys.argv[1:])
