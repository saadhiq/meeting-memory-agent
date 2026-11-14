"""Step 3a: group turns into overlapping chunks and build a keyword (BM25) index."""
import sqlite3

CHUNK_TURNS = 12   # substantial turns per chunk
OVERLAP = 2        # turns shared with the previous chunk, so nothing is cut in half
MIN_WORDS = 3      # turns shorter than this ("Yeah.", "Mm-hmm.") are left out of chunk text


def build_chunks(db):
    db.executescript(
        """
        DROP TABLE IF EXISTS chunks;
        DROP TABLE IF EXISTS chunks_fts;
        CREATE TABLE chunks (
            chunk_id INTEGER PRIMARY KEY,
            meeting_id TEXT, team TEXT, meeting_number INTEGER,
            start_turn INTEGER, end_turn INTEGER, text TEXT);
        CREATE VIRTUAL TABLE chunks_fts USING fts5(text, tokenize='porter');
        """
    )
    meetings = db.execute(
        "SELECT DISTINCT meeting_id, team, meeting_number FROM turns ORDER BY meeting_id"
    ).fetchall()

    for meeting_id, team, meeting_number in meetings:
        turns = db.execute(
            "SELECT turn_index, time, speaker, text FROM turns WHERE meeting_id = ? ORDER BY turn_index",
            (meeting_id,),
        ).fetchall()
        turns = [t for t in turns if len(t[3].split()) >= MIN_WORDS]

        step = CHUNK_TURNS - OVERLAP
        for start in range(0, len(turns), step):
            group = turns[start:start + CHUNK_TURNS]
            text = "\n".join(f"[{time}] Speaker {speaker}: {words}" for _, time, speaker, words in group)
            cur = db.execute(
                "INSERT INTO chunks (meeting_id, team, meeting_number, start_turn, end_turn, text) VALUES (?,?,?,?,?,?)",
                (meeting_id, team, meeting_number, group[0][0], group[-1][0], text),
            )
            db.execute("INSERT INTO chunks_fts (rowid, text) VALUES (?, ?)", (cur.lastrowid, text))
            if start + CHUNK_TURNS >= len(turns):
                break
    db.commit()


if __name__ == "__main__":
    db = sqlite3.connect("memory.db")
    build_chunks(db)
    count, avg_words = db.execute(
        "SELECT COUNT(*), AVG(LENGTH(text) - LENGTH(REPLACE(text, ' ', '')) + 1) FROM chunks"
    ).fetchone()
    print(f"Built {count} chunks, about {avg_words:.0f} words each")
