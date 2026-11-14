# Write-up: Meeting Memory Agent

## Architecture

Two phases. **Ingest, once per meeting:** plain code parses transcripts into speaker turns
(SQLite), groups them into overlapping chunks with a BM25 keyword index (FTS5) and a local vector
index (Chroma, MiniLM on-device); then the only heavy LLM step reads each meeting in 120-turn
sections and distils decisions, proposals, objections, actions, numbers and requirements into a
**ledger**, where code verifies every quote word-for-word against the transcript (repairing
model-tidied quotes, dropping unverifiable ones — 4,249 kept, 290 dropped). **Question time:** a
LangGraph loop — plan (LLM writes short searches; code extracts the team id), retrieve (code:
hybrid keyword+vector search fused by RRF, forced into *every* meeting of the team, plus ledger
lookup), answer (LLM points at evidence line ids; code copies the exact words from the database
and re-verifies them), decide (code loops for missing evidence, max 3 rounds, and never lets a
weaker retry replace a stronger answer). Question cost is flat as the corpus grows: nothing is
ever pasted whole into a prompt.

**Memory over time:** every record carries its meeting number; evidence is shown oldest-first;
the prompt states later meetings override earlier ones; a "recap" status stops the PM reading
out old minutes from being logged as a new decision. The TS3012 brief is built only from
meetings a–c (code excludes the final meeting) with every point tied to a verified quote.

## Model vs code, and why

The model does only what needs reading or writing messy language: extracting ledger notes,
choosing search wordings, composing answers, picking which evidence lines support them. Code does
everything that must be exact or repeatable: parsing, team filtering (a SQL WHERE, so another
team's words are unreachable), time ordering, **all quotes** (copied from the database, never from
the model — a fabricated citation cannot reach the output), loop limits, and the two-meeting
rule. Citations are checked word-for-word by the grader, so quote handling was the one thing I
refused to delegate to a model.

## Tools, and where they broke

Python + SQLite/FTS5 + Chroma + LangGraph + the NVIDIA free endpoint (~40 req/min).
`z-ai/glm-5.3-flash` never responded; `nemotron-3.5-lightning-30b` burned its whole output budget
on hidden reasoning for long prompts (114k tokens for an empty answer); I shipped on
`nemotron-3.5-bolt-e4b` — fast and reliable, but weak judgment (see failures). Keyword-only search
missed paraphrased passages (hybrid fixed it); whole-meeting extraction gave vague duplicates
(short sections fixed it); the model altered 25 of 109 quotes on one team (code repair recovered
most); real-but-irrelevant quotes passed existence checks (a shared-content-word filter helps but
is still loose).

## Proof, and three failures

I wrote 12 questions of my own, verified against the transcripts (evidence timestamps stored in
`eval_questions.json`), scored in code (`evaluate.py`): **9 right, 2 partial, 1 wrong.** Failures:
**(1)** TS3004's chip reversal — the agent found regular→advanced but invented a budget reason;
the transcript says the advanced chip was needed for speech recognition. Quotes are verified,
the model's own sentences are not. **(2)** TS3004 power/docking — cited one meeting even after
three retrieval rounds; the same weakness flags 7 of the 25 main answers in `run_log.json`.
**(3)** TS3005 materials — answered rubber and wood but stopped before finding titanium:
retrieval returns the first good evidence, not all of it. Ingesting 1,000 meetings: ~13.1M input
+ ~2.9M output tokens, ~4.6 h sequential on the free tier, ~$2.50 at typical small-model prices
($0 as run — the endpoint is free); full math in `cost_report.md`.

## Production at 10,000+ meetings

Postgres + pgvector instead of SQLite + Chroma; a queue feeding parallel extraction workers on a
paid endpoint (ingest is per-meeting and incremental — a new meeting costs ~13k tokens, nothing
is rebuilt); a stronger model for answering only (~3 calls/question) with the small model kept
for bulk extraction; monitoring of dropped-quote and one-meeting-answer rates as quality alarms;
and periodic re-evaluation against the hand-checked question set.
