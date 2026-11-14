"""Step 1: parse transcript files into speaker turns and store them in SQLite."""
import re
import sqlite3
import sys
from pathlib import Path

TURN_LINE = re.compile(r"^(\d\d:\d\d:\d\d) Speaker ([A-D])$")


def parse_file(path):
    header, turns, current = {}, [], None
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        match = TURN_LINE.match(line)
        if match:                                  # a new turn starts
            if current:
                turns.append(current)
            current = {"time": match.group(1), "speaker": match.group(2), "text": ""}
        elif current is not None:                  # text belonging to the current turn
            if line:
                current["text"] = (current["text"] + " " + line).strip()
        elif ":" in line:                          # header line, e.g. "Team: IS1009"
            key, value = line.split(":", 1)
            header[key.strip()] = value.strip()
    if current:
        turns.append(current)

    meeting_id = header["Meeting"]
    team = header["Team"]
    meeting_number = int(header["Meeting number"].split()[0])
    return [
        (meeting_id, team, meeting_number, i, t["time"], t["speaker"], t["text"])
        for i, t in enumerate(turns)
    ]


def main(folder, db_path="memory.db"):
    db = sqlite3.connect(db_path)
    db.execute("DROP TABLE IF EXISTS turns")
    db.execute(
        """CREATE TABLE turns (
               meeting_id TEXT, team TEXT, meeting_number INTEGER,
               turn_index INTEGER, time TEXT, speaker TEXT, text TEXT,
               PRIMARY KEY (meeting_id, turn_index))"""
    )
    files = sorted(Path(folder).glob("*.txt"))
    for f in files:
        db.executemany("INSERT INTO turns VALUES (?,?,?,?,?,?,?)", parse_file(f))
    db.commit()

    meetings, teams, turns = db.execute(
        "SELECT COUNT(DISTINCT meeting_id), COUNT(DISTINCT team), COUNT(*) FROM turns"
    ).fetchone()
    print(f"Parsed {len(files)} files: {meetings} meetings, {teams} teams, {turns} turns")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "transcripts")