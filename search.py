"""Step 3b: keyword search over chunks, always filtered to one team."""
import re
import sqlite3
import sys

STOPWORDS = set(
    "a an the and or but of to in on for with at by from is are was were be been it its this that "
    "these those they them their what which who whom when where why how did do does team".split()
)


def keyword_search(db, query, team, k=5, meeting_id=None):
    """Return the k best-matching chunks for this team, best first."""
    words = [w for w in re.findall(r"[a-z0-9]+", query.lower()) if w not in STOPWORDS]
    if not words:
        return []
    match = " OR ".join(f'"{w}"' for w in dict.fromkeys(words))   # FTS5 query: any of the words

    sql = """SELECT c.chunk_id, c.meeting_id, c.start_turn, c.end_turn, c.text, bm25(chunks_fts) AS score
             FROM chunks_fts JOIN chunks c ON c.chunk_id = chunks_fts.rowid
             WHERE chunks_fts MATCH ? AND c.team = ?"""
    params = [match, team]
    if meeting_id:
        sql += " AND c.meeting_id = ?"
        params.append(meeting_id)
    sql += " ORDER BY score LIMIT ?"       # bm25() is lower-is-better in SQLite
    params.append(k)

    cols = ["chunk_id", "meeting_id", "start_turn", "end_turn", "text", "score"]
    return [dict(zip(cols, row)) for row in db.execute(sql, params)]


if __name__ == "__main__":
    db = sqlite3.connect("memory.db")
    team = sys.argv[1] if len(sys.argv) > 1 else "IS1009"
    query = sys.argv[2] if len(sys.argv) > 2 else "voice recogniser financing cost total"
    for hit in keyword_search(db, query, team, k=3):
        print(f"--- {hit['meeting_id']} turns {hit['start_turn']}-{hit['end_turn']} (score {hit['score']:.2f})")
        print(hit["text"][:400], "...\n")
