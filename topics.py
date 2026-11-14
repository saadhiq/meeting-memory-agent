"""Look inside the ledger.
  python topics.py IS1009            -> topic labels used for that team, with counts
  python topics.py IS1009 recogni    -> every record whose topic, statement or quote contains the word"""
import sqlite3
import sys

db = sqlite3.connect("memory.db")
team = sys.argv[1] if len(sys.argv) > 1 else "IS1009"

if len(sys.argv) > 2:
    word = f"%{sys.argv[2]}%"
    rows = db.execute(
        "SELECT meeting_id, type, topic, status, speaker, statement, quote FROM ledger "
        "WHERE team = ? AND (topic LIKE ? OR statement LIKE ? OR quote LIKE ?) "
        "ORDER BY meeting_number, turn_index", (team, word, word, word))
    for meeting_id, type_, topic, status, speaker, statement, quote in rows:
        print(f"{meeting_id} | {type_} | {topic} | {status} | {speaker}")
        print(f"    says: {statement}")
        print(f"    quote: {quote}\n")
else:
    for topic, count in db.execute(
            "SELECT topic, COUNT(*) FROM ledger WHERE team = ? GROUP BY topic ORDER BY 2 DESC", (team,)):
        print(f"{count:4}  {topic}")
