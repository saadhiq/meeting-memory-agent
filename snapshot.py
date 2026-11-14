"""Checkpoint for the expensive LLM step: export/load the extracted ledger.

  python snapshot.py export   -> write ledger, roles and ingest_log to ledger_snapshot.json
  python snapshot.py load     -> load ledger_snapshot.json into memory.db (skips the ~40 min
                                 LLM extraction; run parse.py and chunk.py first)

ledger_snapshot.json is the UNEDITED output of extract.py on the full corpus, exported with
this script. Only the LLM-produced tables are checkpointed; everything deterministic (turns,
chunks, indexes, embeddings) is rebuilt from the transcripts by parse.py / chunk.py / hybrid.py."""
import json
import sqlite3
import sys

import extract  # only for setup_tables (no API call happens on import)

SNAPSHOT = "ledger_snapshot.json"
TABLES = {
    "ledger": ["record_id", "team", "meeting_id", "meeting_number", "type", "topic",
               "statement", "status", "owner", "speaker", "turn_index", "time", "quote"],
    "roles": ["meeting_id", "team", "speaker", "role"],
    "ingest_log": ["meeting_id", "team", "input_tokens", "output_tokens", "seconds", "kept", "dropped"],
}


def export(db_path="memory.db"):
    db = sqlite3.connect(db_path)
    data = {}
    for table, cols in TABLES.items():
        rows = db.execute(f"SELECT {', '.join(cols)} FROM {table}").fetchall()
        data[table] = [list(r) for r in rows]
        print(f"  {table}: {len(rows)} rows")
    with open(SNAPSHOT, "w", encoding="utf-8") as f:
        json.dump({"columns": TABLES, "rows": data}, f, ensure_ascii=False)
    print(f"Wrote {SNAPSHOT}")


def load(db_path="memory.db"):
    with open(SNAPSHOT, encoding="utf-8") as f:
        snap = json.load(f)
    db = sqlite3.connect(db_path)
    extract.setup_tables(db)
    for table, cols in TABLES.items():
        db.execute(f"DELETE FROM {table}")
        rows = snap["rows"][table]
        marks = ",".join("?" * len(cols))
        db.executemany(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({marks})", rows)
        print(f"  {table}: {len(rows)} rows loaded")
    db.commit()
    n, kept = db.execute("SELECT COUNT(*), SUM(kept) FROM ingest_log").fetchone()
    print(f"Done: ledger for {n} meetings ({kept} records) ready in {db_path}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "load"
    path = sys.argv[2] if len(sys.argv) > 2 else "memory.db"
    if cmd == "export":
        export(path)
    elif cmd == "load":
        load(path)
    else:
        print(__doc__)
