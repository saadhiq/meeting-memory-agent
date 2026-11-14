"""Step 4: vector index (Chroma) plus hybrid search that merges keyword and vector results."""
import sqlite3
import sys
import time

import chromadb

from search import keyword_search

CHROMA_DIR = "chroma_store"
COLLECTION = "chunks"
BATCH = 200


def get_collection(embedding_function=None, reset=False):
    """Open the vector collection. With no embedding_function, Chroma uses its
    built-in local model (all-MiniLM-L6-v2), which runs on your own computer."""
    client = chromadb.PersistentClient(path=CHROMA_DIR)
    kwargs = {"embedding_function": embedding_function} if embedding_function else {}
    if reset:
        try:
            client.delete_collection(COLLECTION)
        except Exception:
            pass
    return client.get_or_create_collection(COLLECTION, **kwargs)


def build_vectors(db, embedding_function=None):
    collection = get_collection(embedding_function, reset=True)
    rows = db.execute(
        "SELECT chunk_id, team, meeting_id, meeting_number, text FROM chunks ORDER BY chunk_id"
    ).fetchall()
    start = time.time()
    for i in range(0, len(rows), BATCH):
        batch = rows[i:i + BATCH]
        collection.add(
            ids=[str(r[0]) for r in batch],
            documents=[r[4] for r in batch],
            metadatas=[{"team": r[1], "meeting_id": r[2], "meeting_number": r[3]} for r in batch],
        )
        print(f"  embedded {min(i + BATCH, len(rows))}/{len(rows)} chunks")
    print(f"Done in {time.time() - start:.0f} seconds")


def vector_search(collection, query, team, k=5, meeting_id=None):
    """Return chunk ids for this team, closest in meaning first."""
    where = {"team": team}
    if meeting_id:
        where = {"$and": [{"team": team}, {"meeting_id": meeting_id}]}
    result = collection.query(query_texts=[query], n_results=k, where=where)
    return [int(i) for i in result["ids"][0]]


def hybrid_search(db, collection, query, team, k=5, meeting_id=None):
    """Merge keyword and vector rankings with reciprocal rank fusion (RRF)."""
    keyword_ids = [h["chunk_id"] for h in keyword_search(db, query, team, k=15, meeting_id=meeting_id)]
    vector_ids = vector_search(collection, query, team, k=15, meeting_id=meeting_id)

    scores = {}
    for ranking in (keyword_ids, vector_ids):
        for rank, chunk_id in enumerate(ranking):
            scores[chunk_id] = scores.get(chunk_id, 0) + 1 / (60 + rank)   # high rank in either list helps
    best = sorted(scores, key=scores.get, reverse=True)[:k]

    hits = []
    for chunk_id in best:
        meeting, start_turn, end_turn, text = db.execute(
            "SELECT meeting_id, start_turn, end_turn, text FROM chunks WHERE chunk_id = ?", (chunk_id,)
        ).fetchone()
        found_by = [name for name, ids in (("keyword", keyword_ids), ("vector", vector_ids)) if chunk_id in ids]
        hits.append({"chunk_id": chunk_id, "meeting_id": meeting, "start_turn": start_turn,
                     "end_turn": end_turn, "text": text, "found_by": "+".join(found_by)})
    return hits


if __name__ == "__main__":
    db = sqlite3.connect("memory.db")
    if len(sys.argv) > 1 and sys.argv[1] == "build":
        build_vectors(db)
    else:
        team = sys.argv[1] if len(sys.argv) > 1 else "IS1009"
        query = sys.argv[2] if len(sys.argv) > 2 else "was the voice recogniser included in the final cost?"
        for hit in hybrid_search(db, get_collection(), query, team):
            print(f"--- {hit['meeting_id']} turns {hit['start_turn']}-{hit['end_turn']} (found by {hit['found_by']})")
            print(hit["text"][:300], "...\n")
