"""Step 5: an LLM reads each meeting in sections and extracts decisions, actions and numbers
into a 'ledger' table. Code verifies (and if needed repairs) every quote before storing it."""
import json
import os
import re
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor

from dotenv import load_dotenv
from openai import OpenAI

from verify import repair_quote, verify_quote

load_dotenv()            # reads NVIDIA_API_KEY (and optional EXTRACT_MODEL) from the .env file

MODEL = os.environ.get("EXTRACT_MODEL", "z-ai/glm-5.3-flash")
WINDOW_TURNS = 120       # turns per LLM call: short sections are read far more carefully
WINDOW_OVERLAP = 10      # turns shared between neighbouring sections
WORKERS = 3              # sections of one meeting sent in parallel (keep low for the free tier)
MAX_RETRIES = 5

PROMPT = """You are building a searchable memory of a design team's meetings.
Below is ONE SECTION of a meeting of a four-person team designing a TV remote control.
Each line is one speaker turn: [turn number] Speaker letter: words.

Find the moments that someone would need to recall later. Extract ONLY these:
- "decision": the team agrees, chooses, drops, changes, or leaves something pending
- "proposal": one person suggests a specific choice or a change to an earlier choice
- "objection": someone pushes back on an idea (say what their reason was)
- "action": someone says they will do, check or find out something, or is asked to
- "number": a price, cost, budget, total, percentage or age range is stated
- "requirement": a new instruction or constraint is announced to the team

Do NOT extract: greetings, tool training, drawing animals, someone merely describing what a
button does, or vague remarks. Quality matters more than quantity: at most 12 items per section.

For each item give:
- "type": one of the six types above
- "topic": use one of these labels when it fits: energy source, case material, case shape,
  colour and look, buttons and controls, scroll wheel, lcd screen, touch screen,
  speech recognition (use this for any voice or speech recogniser), finding a lost remote,
  target group, selling price, production cost, chip, teletext, docking station.
  Otherwise write your own 1-3 word label. Never use a vague label like "functions" or "features".
- "statement": ONE sentence IN YOUR OWN WORDS saying what happened: who (speaker letter), what,
  and the reason if one is given. Never copy the quote as the statement.
- "status": agreed, proposed, rejected, dropped, pending, reported, or
  "recap" if the speaker is only reading out the minutes of an EARLIER meeting
- "owner": for actions, the speaker letter who must do it; otherwise null
- "quote": the supporting words COPIED EXACTLY from ONE turn, 6 to 40 words.
  Keep every "uh" and "um". Never join two turns. Never use "...".

Also give "roles": which speaker letter is the project manager, industrial designer,
user interface designer, marketing expert - only if this section makes it clear, otherwise null.

EXAMPLE
Section:
[41] Speaker C: I think we should uh use rubber for the case because it it feels nicer in the hand
[42] Speaker A: Yeah okay, let's go with rubber then, instead of the plastic.
[43] Speaker B: I'll check how much the rubber costs before the next meeting.
Reply:
{"roles": {"A": null, "B": null, "C": null, "D": null},
 "items": [
  {"type": "proposal", "topic": "case material", "statement": "C proposed a rubber case because it feels nicer in the hand.", "status": "proposed", "owner": null, "quote": "I think we should uh use rubber for the case because it it feels nicer in the hand"},
  {"type": "decision", "topic": "case material", "statement": "A agreed to switch the case material from plastic to rubber.", "status": "agreed", "owner": null, "quote": "Yeah okay, let's go with rubber then, instead of the plastic."},
  {"type": "action", "topic": "production cost", "statement": "B will check the cost of rubber before the next meeting.", "status": "pending", "owner": "B", "quote": "I'll check how much the rubber costs before the next meeting."}
 ]}
END OF EXAMPLE

Reply with JSON only, in the same shape as the example.

SECTION OF MEETING %s:
%s"""


def make_client():
    return OpenAI(base_url="https://integrate.api.nvidia.com/v1",
                  api_key=os.environ["NVIDIA_API_KEY"], timeout=180)


MAX_OUTPUT_TOKENS = 6000
THINKING = os.environ.get("THINKING", "default")   # "off" asks reasoning models to answer directly


def call_llm(client, prompt):
    """Send one prompt, return (reply text, input tokens, output tokens). Retries on errors."""
    extra = {"chat_template_kwargs": {"enable_thinking": False}} if THINKING == "off" else None
    for attempt in range(MAX_RETRIES):
        try:
            reply = client.chat.completions.create(
                model=MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0,
                max_tokens=MAX_OUTPUT_TOKENS,
                extra_body=extra,
            )
            usage = reply.usage
            choice = reply.choices[0]
            text = choice.message.content or ""
            if not text.strip():                       # reasoning models can spend every token "thinking"
                reasoning = getattr(choice.message, "reasoning_content", None) or ""
                print(f"    empty reply: finish_reason={choice.finish_reason}, "
                      f"{usage.completion_tokens} output tokens, {len(reasoning)} chars of hidden reasoning")
            return text, usage.prompt_tokens, usage.completion_tokens
        except Exception as error:                     # rate limit, timeout, server error
            wait = 10 * (attempt + 1)
            print(f"    API error ({type(error).__name__}), waiting {wait}s then retrying")
            time.sleep(wait)
    raise RuntimeError("API failed after several retries")


def parse_json(text):
    """Models sometimes wrap JSON in ``` fences, add a sentence, or get cut off."""
    start, end = text.find("{"), text.rfind("}")
    try:
        return json.loads(text[start:end + 1])
    except ValueError:
        pass
    # Fallback: rescue every item that is intact.
    items, roles = [], {}
    for block in re.findall(r"\{[^{}]*\}", text):
        try:
            obj = json.loads(block)
        except ValueError:
            continue
        if "quote" in obj:
            items.append(obj)
        elif set(obj) <= {"A", "B", "C", "D"}:
            roles = obj
    if not items:
        raise ValueError("no usable JSON in reply")
    return {"roles": roles, "items": items}


def setup_tables(db):
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS ledger (
            record_id INTEGER PRIMARY KEY,
            team TEXT, meeting_id TEXT, meeting_number INTEGER,
            type TEXT, topic TEXT, statement TEXT, status TEXT, owner TEXT,
            speaker TEXT, turn_index INTEGER, time TEXT, quote TEXT);
        CREATE TABLE IF NOT EXISTS roles (
            meeting_id TEXT, team TEXT, speaker TEXT, role TEXT,
            PRIMARY KEY (meeting_id, speaker));
        CREATE TABLE IF NOT EXISTS ingest_log (
            meeting_id TEXT PRIMARY KEY, team TEXT, input_tokens INTEGER, output_tokens INTEGER,
            seconds REAL, kept INTEGER, dropped INTEGER);
        """
    )


def read_section(client, meeting_id, section_text):
    """One LLM call for one section. Returns (parsed data or None, tokens in, tokens out)."""
    tokens_in = tokens_out = 0
    for attempt in range(2):                           # one retry if the reply is not usable JSON
        text, t_in, t_out = call_llm(client, PROMPT % (meeting_id, section_text))
        tokens_in, tokens_out = tokens_in + t_in, tokens_out + t_out
        try:
            return parse_json(text), tokens_in, tokens_out
        except ValueError:
            with open(f"debug_{meeting_id}.txt", "w", encoding="utf-8") as f:
                f.write(text)                          # keep the raw reply so you can inspect it
    return None, tokens_in, tokens_out


def extract_meeting(db, client, meeting_id, team, meeting_number):
    turns = db.execute(
        "SELECT turn_index, speaker, text FROM turns WHERE meeting_id = ? ORDER BY turn_index", (meeting_id,)
    ).fetchall()
    lines = [f"[{i}] Speaker {s}: {t}" for i, s, t in turns]
    step = WINDOW_TURNS - WINDOW_OVERLAP
    sections = ["\n".join(lines[i:i + WINDOW_TURNS]) for i in range(0, max(len(lines) - WINDOW_OVERLAP, 1), step)]

    start = time.time()
    with ThreadPoolExecutor(WORKERS) as pool:          # LLM calls in parallel; database writes stay below
        results = list(pool.map(lambda s: read_section(client, meeting_id, s), sections))

    kept = repaired = dropped = failed = tokens_in = tokens_out = 0
    seen = set()                                       # (turn, type) pairs already stored
    for data, t_in, t_out in results:
        tokens_in, tokens_out = tokens_in + t_in, tokens_out + t_out
        if data is None:
            failed += 1
            continue
        for item in data.get("items", []):
            quote = item.get("quote") or ""
            ok, info = verify_quote(db, meeting_id, quote)
            if not ok:                                 # the model changed the words: try to repair in code
                quote, info = repair_quote(db, meeting_id, quote)
                if quote is None:
                    dropped += 1                       # nothing in the transcript supports it
                    continue
                repaired += 1
            key = (info["turn_index"], item.get("type"))
            if key in seen:                            # same moment extracted twice
                continue
            seen.add(key)
            db.execute(
                "INSERT INTO ledger (team, meeting_id, meeting_number, type, topic, statement, status, owner,"
                " speaker, turn_index, time, quote) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (team, meeting_id, meeting_number, item.get("type"), (item.get("topic") or "").lower(),
                 item.get("statement"), item.get("status"), item.get("owner"),
                 info["speaker"], info["turn_index"], info["time"], quote),
            )
            kept += 1
        for speaker, role in (data.get("roles") or {}).items():
            if role and speaker in "ABCD":
                db.execute("INSERT OR REPLACE INTO roles VALUES (?,?,?,?)", (meeting_id, team, speaker, role))

    if failed == len(sections):
        print(f"  {meeting_id}: every section failed (see debug_{meeting_id}.txt); run again to retry")
        db.rollback()
        return
    db.execute(
        "INSERT OR REPLACE INTO ingest_log VALUES (?,?,?,?,?,?,?)",
        (meeting_id, team, tokens_in, tokens_out, time.time() - start, kept, dropped),
    )
    db.commit()
    note = f", {failed} section(s) unreadable" if failed else ""
    print(f"  {meeting_id}: {len(sections)} sections, kept {kept} ({repaired} repaired), dropped {dropped}{note}, "
          f"{tokens_in}+{tokens_out} tokens, {time.time() - start:.0f}s")


def main(target):
    db = sqlite3.connect("memory.db")
    setup_tables(db)
    client = make_client()
    print(f"Model: {MODEL}")

    done = {row[0] for row in db.execute("SELECT meeting_id FROM ingest_log")}
    meetings = db.execute(
        "SELECT DISTINCT meeting_id, team, meeting_number FROM turns ORDER BY meeting_id"
    ).fetchall()
    todo = [m for m in meetings if m[0] not in done and (target == "all" or m[1] == target)]
    print(f"{len(todo)} meetings to extract ({len(done)} already done)")

    for meeting_id, team, meeting_number in todo:
        extract_meeting(db, client, meeting_id, team, meeting_number)

    n, tin, tout, secs, kept, dropped = db.execute(
        "SELECT COUNT(*), SUM(input_tokens), SUM(output_tokens), SUM(seconds), SUM(kept), SUM(dropped) FROM ingest_log"
    ).fetchone()
    if n:
        print(f"\nTotal so far: {n} meetings, {kept} records kept, {dropped} dropped, "
              f"{tin} input + {tout} output tokens, {secs:.0f} seconds")


def redo(target):
    """Forget what was extracted (for one team, or 'all'), so it can be extracted again."""
    db = sqlite3.connect("memory.db")
    setup_tables(db)
    for table in ("ledger", "roles", "ingest_log"):
        if target == "all":
            db.execute(f"DELETE FROM {table}")
        else:
            db.execute(f"DELETE FROM {table} WHERE team = ?", (target,))
    db.commit()


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "redo":
        redo(sys.argv[2])
        main(sys.argv[2])
    else:
        main(sys.argv[1] if len(sys.argv) > 1 else "IS1009")
