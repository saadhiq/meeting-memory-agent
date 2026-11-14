"""Step 6: the agent. A LangGraph loop that plans searches, retrieves evidence, answers,
checks its own citations in code, and searches again if something is missing.

  python agent.py Q14                      -> answer a question from questions.json
  python agent.py "Team IS1009 ... ?"      -> answer any question you type"""
import json
import os
import re
import sqlite3
import sys
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

import extract                              # reuses call_llm (retries, token counts) and the API client
from hybrid import get_collection, hybrid_search
from difflib import SequenceMatcher

from verify import _plain, verify_quote

MODEL = os.environ.get("AGENT_MODEL", extract.MODEL)
extract.MAX_OUTPUT_TOKENS = 12000             # answers use more room than extraction sections
MAX_ROUNDS = 3                              # hard cap so the loop can never run forever
STOPWORDS = set("a an the and or but of to in on for with at by from is are was were be been it its this that "
                "they them their what which who when where why how did do does team one two "
                "think yeah okay well just like want gonna have would could should there then".split())


class State(TypedDict, total=False):
    question: str
    team: str
    queries: list          # searches to run in the next retrieve step
    searched: list         # every search already run
    chunks: dict           # chunk_id -> passage found so far
    records: dict          # record_id -> ledger record found so far
    answer: str
    citations: list        # verified citations only
    rejected: int          # citations the model gave that code threw out
    feedback: str          # what code tells the model to fix on the next round
    round: int
    tokens: int


# ---------- helpers (plain code) ----------

def ask_json(prompt, state):
    """Call the model and read a JSON object from its reply; one retry if it is not JSON."""
    for _ in range(2):
        text, t_in, t_out = extract.call_llm(CLIENT, prompt)
        state["tokens"] = state.get("tokens", 0) + t_in + t_out
        try:
            return json.loads(text[text.find("{"):text.rfind("}") + 1])
        except ValueError:
            with open("debug_agent_reply.txt", "w", encoding="utf-8") as f:
                f.write(text)                         # inspect this file if answers come back empty
            continue
    return {}


def team_roles(team):
    """Most common role recorded for each speaker of this team."""
    rows = DB.execute(
        "SELECT speaker, role, COUNT(*) FROM roles WHERE team = ? GROUP BY speaker, role ORDER BY 3", (team,))
    return {speaker: role for speaker, role, _ in rows}       # later (more frequent) rows win


def ledger_search(team, query, k=6):
    """Ledger records for this team whose text shares the most words with the query."""
    words = [w for w in re.findall(r"[a-z0-9]+", query.lower()) if w not in STOPWORDS and len(w) > 2]
    rows = DB.execute(
        "SELECT record_id, meeting_id, meeting_number, turn_index, type, topic, status, speaker, statement, quote "
        "FROM ledger WHERE team = ?", (team,)).fetchall()
    scored = []
    for row in rows:
        text = f"{row[5]} {row[8]} {row[9]}".lower()
        score = sum(1 for w in words if w[:6] in text)        # first 6 letters: recognition ~ recogniser
        if score:
            scored.append((score, row))
    scored.sort(key=lambda pair: -pair[0])
    return [row for _, row in scored[:k]]


# ---------- graph nodes ----------

def plan(state):
    """LLM: turn the question into a few short searches."""
    team = re.search(r"\b[A-Z]{2}\d{4}\b", state["question"])           # code, not LLM
    if not team:
        return {"answer": "No team id found in the question.", "citations": [], "queries": []}
    reply = ask_json(
        "A question about a team's meetings is below. Write 3 short search queries (2 to 5 words each) "
        "that would find the transcript passages needed to answer it. Cover each part of the question. "
        "Use concrete words the speakers would say (for example 'battery', 'total cost', 'voice recogniser'). "
        "Do not include the team id. "
        'Reply with JSON only: {"queries": ["...", "...", "..."]}\n\nQUESTION: ' + state["question"], state)
    queries = [q for q in reply.get("queries", []) if isinstance(q, str) and q.strip()][:4]
    if not queries:                                   # the model gave nothing usable: split the question in code
        parts = re.split(r"[?,;]|\band\b|\bwhat\b|\bwho\b|\bwhich\b|\bwhy\b|\bhow\b", state["question"], flags=re.I)
        queries = [p.strip() for p in parts if len(p.split()) >= 3][:4] or [state["question"]]
        print("[plan] model gave no searches; split the question instead")
    print(f"[plan] model {MODEL} | team {team.group()} | searches: {queries}")
    return {"team": team.group(), "queries": queries, "round": 0,
            "chunks": {}, "records": {}, "searched": [], "tokens": state.get("tokens", 0)}


def retrieve(state):
    """Code: run each search over the ledger and the chunks, always filtered to the team.
    Every question needs more than one meeting, so each search is also run inside EACH meeting
    of the team. Later rounds skip passages already held, so a new round brings new evidence."""
    team, chunks, records = state["team"], dict(state["chunks"]), dict(state["records"])
    meetings = [m for (m,) in DB.execute(
        "SELECT DISTINCT meeting_id FROM turns WHERE team = ? ORDER BY meeting_id", (team,))]
    before = len(chunks)

    def add_new(hits, how_many):
        added = 0
        for hit in hits:
            if added == how_many:
                break
            if hit["chunk_id"] not in chunks:
                chunks[hit["chunk_id"]] = hit
                added += 1

    for query in state["queries"]:
        add_new(hybrid_search(DB, COLLECTION, query, team, k=6), 2)          # best across the team
        for meeting_id in meetings:                                          # plus the best in each meeting
            add_new(hybrid_search(DB, COLLECTION, query, team, k=3, meeting_id=meeting_id), 1)
        for row in ledger_search(team, query):
            records[row[0]] = row
    print(f"[retrieve] round {state['round'] + 1}: +{len(chunks) - before} new passages, "
          f"now {len(chunks)} passages from {len({c['meeting_id'] for c in chunks.values()})} meetings, "
          f"{len(records)} ledger records")
    return {"chunks": chunks, "records": records, "searched": state["searched"] + state["queries"],
            "round": state["round"] + 1}


ANSWER_PROMPT = """You answer questions about one design team's meetings, using ONLY the evidence below.
The team met up to four times: meeting a first, then b, c, d. A LATER meeting overrides an earlier one,
so the final state is whatever the latest meeting says.

QUESTION: {question}

SPEAKER ROLES for this team: {roles}

LEDGER NOTES (extracted earlier, oldest first). Each has an id like L3:
{ledger}

TRANSCRIPT LINES (oldest first). Lines you may cite have an id like E12:
{passages}
{feedback}
Write the answer in 1 to 3 plain sentences of your own. Answer EVERY part of the question directly,
and do not contradict yourself. Name speakers by letter, with their role if known; check the speaker
letter on the line you rely on. Never paste transcript lines, timestamps or line ids into the answer.

Then give 2 or 3 citations. Each citation is the id of ONE line above (an L id or an E id) that directly
states a key fact of your answer: the number, the decision, the change, or who said it.
For each, also copy the 6 to 25 most important words of that line into "words".
The question always involves more than one meeting, so cite lines from at least two different meetings.

If an important part of the question is NOT covered by the evidence, list up to 2 new search queries
in "more_queries"; otherwise leave it empty.

Reply with JSON only:
{{"answer": "...", "citations": [{{"id": "E12", "words": "..."}}, {{"id": "L3", "words": "..."}}], "more_queries": []}}"""

MAX_QUOTE_WORDS = 40


def build_evidence(state):
    """Code: lay out the evidence with an id on every citable line.
    Returns (ledger text, passages text, lookup of id -> (meeting_id, full turn text))."""
    lookup, ledger_lines = {}, []
    records = sorted(state["records"].values(), key=lambda r: (r[2], r[3]))
    for n, r in enumerate(records, 1):
        lookup[f"L{n}"] = (r[1], r[9])
        ledger_lines.append(f'[L{n}] {r[1]} | {r[4]} | {r[5]} | {r[6]} | {r[8]} | Speaker {r[7]} said: "{r[9]}"')

    wanted = {}                                               # meeting_id -> set of turn numbers to show
    for c in state["chunks"].values():
        wanted.setdefault(c["meeting_id"], set()).update(range(c["start_turn"], c["end_turn"] + 1))
    passage_lines, n = [], 0
    for meeting_id in sorted(wanted):
        passage_lines.append(f"\n--- MEETING {meeting_id} ---")
        previous = None
        for turn_index, speaker, text in DB.execute(
                "SELECT turn_index, speaker, text FROM turns WHERE meeting_id = ? ORDER BY turn_index", (meeting_id,)):
            if turn_index not in wanted[meeting_id] or len(text.split()) < 3:
                continue
            if previous is not None and turn_index - previous > 3:
                passage_lines.append("   (...)")              # a gap between two passages
            previous = turn_index
            if len(text.split()) >= 6:                        # long enough to be a valid citation
                n += 1
                lookup[f"E{n}"] = (meeting_id, text)
                passage_lines.append(f"[E{n}] Speaker {speaker}: {text}")
            else:
                passage_lines.append(f"      Speaker {speaker}: {text}")
    return "\n".join(ledger_lines) or "(nothing found)", "\n".join(passage_lines) or "(nothing found)", lookup


def cut_quote(turn_text, hint, focus):
    """Code: choose the exact words to quote from a turn the model pointed at.
    Short turn -> the whole turn. Long turn -> the part matching the model's 'words',
    or failing that the 40-word window sharing most words with the question and answer."""
    words = turn_text.split()
    if len(words) <= MAX_QUOTE_WORDS:
        return turn_text
    plain = [_plain(w) for w in words]
    hint_plain = [_plain(w) for w in hint.split() if _plain(w)]
    match = SequenceMatcher(None, plain, hint_plain, autojunk=False).find_longest_match(
        0, len(plain), 0, len(hint_plain))
    if match.size >= 6:
        return " ".join(words[match.a:match.a + min(match.size, MAX_QUOTE_WORDS)])
    focus_words = {_plain(w) for w in focus.split()} - STOPWORDS
    best = max(range(0, len(words) - MAX_QUOTE_WORDS + 1, 5),
               key=lambda i: sum(1 for w in plain[i:i + MAX_QUOTE_WORDS] if w in focus_words))
    return " ".join(words[best:best + MAX_QUOTE_WORDS])


def content_words(text):
    return {w[:6] for w in re.findall(r"[a-z]+", text.lower()) if len(w) > 3 and w not in STOPWORDS}


def shares_content(quote, answer_text):
    """Code: a citation must share at least one meaningful word with the question or answer.
    This throws out quotes like "Do you wanna put your cord back in?" that prove nothing."""
    return bool(content_words(quote) & content_words(answer_text))


def answer(state):
    """LLM: read the evidence, answer, and point at the lines that prove it.
    Code: copy the quotes out of the transcript, so a citation can never be misquoted."""
    ledger, passages, lookup = build_evidence(state)
    feedback = f"\nNOTE: {state['feedback']}\n" if state.get("feedback") else ""
    reply = ask_json(ANSWER_PROMPT.format(question=state["question"], roles=team_roles(state["team"]) or "unknown",
                                          ledger=ledger, passages=passages, feedback=feedback), state)

    text = re.split(r"\s*\[\d\d:\d\d:\d\d\]", str(reply.get("answer", "")))[0]   # drop pasted transcript
    text = re.sub(r"\s*\[(?:[EL]\d+|[A-D])(?:[,\s]+(?:[EL]\d+|[A-D]))*\]", "", text)      # drop leaked labels like [E14], [A]
    text = re.sub(r"\b[LE]\d+\b", "", text)                                              # and bare ones like "L8 is ..."
    text = re.split(r"\s*\b(?:words|id|quote|citations)\s*:", text)[0]              # drop leaked JSON field text
    text = re.sub(r"\s{2,}", " ", text).strip()
    good, rejected = [], 0
    for cite in reply.get("citations", [])[:3]:
        found = lookup.get(str(cite.get("id", "")).strip("[] ")) if isinstance(cite, dict) else None
        if not found:
            rejected += 1                                     # the model pointed at a line that does not exist
            continue
        meeting_id, turn_text = found
        quote = cut_quote(turn_text, str(cite.get("words", "")), state["question"] + " " + text)
        ok, _ = verify_quote(DB, meeting_id, quote)           # belt and braces: still verified
        if ok and not shares_content(quote, state["question"] + " " + text):
            ok = False                                        # real quote, but about something else
        if ok and quote not in [g["quote"] for g in good]:
            good.append({"meeting_id": meeting_id, "quote": quote})
        else:
            rejected += 1

    more = [q for q in reply.get("more_queries", []) if isinstance(q, str) and q not in state["searched"]][:2]
    feedback = ""
    if len({g["meeting_id"] for g in good}) < 2:              # every question needs two meetings
        feedback = ("Your last attempt cited only one meeting. Cite lines from at least two different "
                    "meetings: one for the earlier state and one for the later or final state.")
        if not more and state["question"] not in state["searched"]:
            more = [state["question"]]
    print(f"[answer] {len(good)} citations from {len({g['meeting_id'] for g in good})} meeting(s), "
          f"{rejected} rejected, wants more searches: {more}")
    # Code: never let a later round make things worse. Keep whichever attempt has
    # citations from more meetings (then more citations); only the searches move forward.
    def strength(cites):
        return (len({c["meeting_id"] for c in cites}), len(cites))
    if state.get("citations") and strength(good) < strength(state["citations"]):
        print("[answer] this round was weaker, keeping the previous answer")
        text, good = state["answer"], state["citations"]
    return {"answer": text, "citations": good, "rejected": rejected, "queries": more,
            "feedback": feedback, "tokens": state["tokens"]}


def decide(state):
    """Code: loop again only if evidence is missing and we still have rounds left."""
    if "team" not in state:
        return END
    if state["round"] < MAX_ROUNDS and (state["queries"] or state["feedback"] or not state["citations"]):
        return "retrieve"
    return END


def build_graph():
    graph = StateGraph(State)
    graph.add_node("plan", plan)
    graph.add_node("retrieve", retrieve)
    graph.add_node("answer", answer)
    graph.add_edge(START, "plan")
    graph.add_conditional_edges("plan", lambda s: "retrieve" if "team" in s else END)
    graph.add_edge("retrieve", "answer")
    graph.add_conditional_edges("answer", decide)
    return graph.compile()


def setup():
    global DB, COLLECTION, CLIENT
    DB = sqlite3.connect("memory.db", timeout=30)
    COLLECTION = get_collection()
    CLIENT = extract.make_client()
    extract.MODEL = MODEL


def run(question):
    """Answer one question. Returns the final state (answer, citations, rounds, tokens)."""
    return build_graph().invoke({"question": question})


if __name__ == "__main__":
    setup()
    asked = sys.argv[1] if len(sys.argv) > 1 else "Q14"
    if re.fullmatch(r"Q\d\d", asked):
        questions = json.load(open("questions.json", encoding="utf-8"))["questions"]
        asked = next(q["question"] for q in questions if q["question_id"] == asked)
    print("QUESTION:", asked, "\n")
    result = run(asked)
    print("\nANSWER:", result.get("answer"))
    for cite in result.get("citations", []):
        print(f'  [{cite["meeting_id"]}] "{cite["quote"]}"')
    print(f"\n({result.get('round', 0)} search round(s), {result.get('tokens', 0)} tokens)")
