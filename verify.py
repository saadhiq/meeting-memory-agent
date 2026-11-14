"""Step 2: check that a quote is copied exactly from one speaker turn of a meeting."""
import re
from difflib import SequenceMatcher
import sqlite3
import sys

MIN_WORDS = 6


def normalize(text):
    """Lowercase and collapse all whitespace, because the checker ignores case and spacing."""
    return re.sub(r"\s+", " ", text).strip().lower()


def verify_quote(db, meeting_id, quote):
    """Return (True, turn info) if the quote is valid, otherwise (False, reason)."""
    wanted = normalize(quote)
    if len(wanted.split()) < MIN_WORDS:
        return False, f"quote has fewer than {MIN_WORDS} words"

    rows = db.execute(
        "SELECT turn_index, time, speaker, text FROM turns WHERE meeting_id = ? ORDER BY turn_index",
        (meeting_id,),
    ).fetchall()
    if not rows:
        return False, f"no meeting called {meeting_id}"

    for turn_index, time, speaker, text in rows:
        if wanted in normalize(text):
            return True, {"turn_index": turn_index, "time": time, "speaker": speaker}
    return False, "quote not found inside any single speaker turn"


def _plain(word):
    """A word with case and punctuation removed, used only for comparing."""
    return re.sub(r"[^a-z0-9']", "", word.lower())


def repair_quote(db, meeting_id, quote):
    """The model often 'tidies' a quote (drops an "uh", fixes a comma). Find the longest run of
    words that the quote and one turn really share, and return the turn's own words for that run.
    Returns (exact quote, turn info) or (None, None)."""
    wanted = [_plain(w) for w in quote.split()]
    wanted = [w for w in wanted if w]
    best = (0, None, None)
    rows = db.execute(
        "SELECT turn_index, time, speaker, text FROM turns WHERE meeting_id = ? ORDER BY turn_index",
        (meeting_id,),
    ).fetchall()
    for turn_index, time, speaker, text in rows:
        original = text.split()
        plain = [_plain(w) for w in original]
        match = SequenceMatcher(None, plain, wanted, autojunk=False).find_longest_match(
            0, len(plain), 0, len(wanted))
        if match.size > best[0]:
            exact = " ".join(original[match.a:match.a + match.size])
            best = (match.size, exact, {"turn_index": turn_index, "time": time, "speaker": speaker})
    size, exact, info = best
    if size >= MIN_WORDS and size >= 0.5 * len(wanted):      # long enough, and most of the quote
        ok, _ = verify_quote(db, meeting_id, exact)          # final check with the strict verifier
        if ok:
            return exact, info
    return None, None


if __name__ == "__main__":
    db = sqlite3.connect("memory.db")
    tests = [
        ("IS1009d", "In the financing there was no room for our voice recogniser."),   # exact
        ("IS1009d", "in the  financing there was NO room for our voice recogniser."),  # case/spacing differ
        ("IS1009d", "There was no space in the budget for the voice recogniser."),     # paraphrase
        ("IS1009d", "no room for our"),                                                # too short
        ("IS1009c", "In the financing there was no room for our voice recogniser."),   # wrong meeting
    ]
    if len(sys.argv) == 3:
        tests = [(sys.argv[1], sys.argv[2])]
    for meeting_id, quote in tests:
        ok, info = verify_quote(db, meeting_id, quote)
        print("PASS" if ok else "FAIL", "|", meeting_id, "|", quote[:50], "|", info)
