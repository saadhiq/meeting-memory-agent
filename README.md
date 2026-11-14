# Meeting Memory Agent

An agent that turns 138 real meeting transcripts (AMI Meeting Corpus, ~676,000 words) into a
searchable memory, answers questions about them with word-for-word verified citations, and writes
a pre-meeting brief for a team. Built for the Catalist Media Agentic AI intern build test.

**Core idea:** do the expensive understanding once per meeting at ingest time, store it, and at
question time retrieve only a small amount of evidence. Question cost does not grow with the
number of meetings, so the same design works at 10,000+ meetings.

**Core guarantee:** the model never writes a quote into the output. It points at an evidence line;
code copies the exact words from the database and re-verifies them. A fabricated or paraphrased
citation cannot reach `answers.json`.

**Submission documents:** [WRITEUP.md](WRITEUP.md) (one page: architecture, model-vs-code,
failures, production) · [cost_report.md](cost_report.md) (cost and time for 1,000 meetings,
computed from the real logs).

## Architecture

```
INGEST (once per meeting)                 QUESTION TIME (per question)
-------------------------                 ----------------------------
transcripts/*.txt                         question
  | parse.py    (code)                      | plan     (code finds team id, LLM writes searches)
  v                                         v
turns table (SQLite)                      retrieve    (code: hybrid search + ledger, per meeting)
  | chunk.py    (code)                      |
  v                                         v
chunks + FTS5 keyword index               answer      (LLM points at evidence ids;
  | hybrid.py   (local embeddings)          |          code copies and verifies quotes)
  v                                         v
Chroma vector index                       decide      (code: loop, max 3 rounds)
  | extract.py  (LLM + code verify)         |
  v                                         v
ledger + roles (distilled memory)         answer + verified citations -> answers.json
```

The model handles meaning (reading messy speech, choosing searches, wording answers). Code handles
everything that must be exact: parsing, the team filter (SQL), time ordering, quote verification
and repair, loop limits, and the two-meeting rule.

**How the agent knows what is current:** every record carries its meeting number; evidence is
shown oldest first; the prompt states a later meeting overrides an earlier one; retrieval covers
every meeting of the team; and "recap" labels stop a read-out of old minutes being logged as a
new decision.

## Setup

Requires Python 3.10+.

```powershell
pip install -r requirements.txt
```

Create a `.env` file in the project root (never commit it):

```
NVIDIA_API_KEY=nvapi-...
EXTRACT_MODEL=nvidia/nemotron-3.5-bolt-e4b
AGENT_MODEL=nvidia/nemotron-3.5-bolt-e4b
THINKING=off
```

The LLM is served by the NVIDIA API (OpenAI-compatible, free tier, ~40 requests/minute). Get a key
at https://build.nvidia.com. `python test_key.py` checks the key works; `python test_model.py
<model>` checks a model responds.

## Quick start (~5 minutes, skips the LLM extraction)

The repo ships `ledger_snapshot.json`: the unedited output of `extract.py` on the full corpus,
so you don't have to pay for the ~40-minute LLM extraction to try the agent. Everything
deterministic is still rebuilt from the raw transcripts on your machine:

```powershell
python parse.py transcripts    # transcripts -> speaker turns in memory.db (seconds)
python chunk.py                # turns -> chunks + keyword index (seconds)
python hybrid.py build         # embed chunks locally into Chroma (~4 min, no API, no cost)
python snapshot.py load        # load the pre-extracted ledger (instant)
python agent.py Q14            # ask something - needs only your NVIDIA_API_KEY in .env
```

## Full pipeline, in order

```powershell
python parse.py transcripts    # 1. transcripts -> speaker turns in memory.db (seconds)
python chunk.py                # 2. turns -> chunks + keyword index (seconds)
python hybrid.py build         # 3. embed chunks locally into Chroma (~4 min, no API)
python extract.py all          # 4. LLM builds the ledger (~40 min on the free tier; resumable)
                               #    OR: python snapshot.py load  (use the shipped snapshot)
python run_all.py              # 5. answer all 25 questions + write the brief -> answers.json
python check_format.py         # 6. verify answers.json schema and every quote word-for-word
```

Useful along the way:

```powershell
python agent.py Q14                      # answer one of the 25 questions, with search trace
python agent.py "Team IS1009 ... ?"      # answer any question you type (must name a team id)
python search.py IS1009 "voice cost"     # raw keyword search
python hybrid.py IS1009 "was the recogniser included?"   # hybrid search
python topics.py IS1009 recogni          # inspect ledger records about a topic
python run_all.py Q10 Q14                # re-answer specific questions
python run_all.py brief                  # rewrite only the brief
```

## Evaluation (proof it works)

12 hand-written questions, each verified against the transcripts before scoring (the evidence
quotes and timestamps are stored with each question in `eval_questions.json`):

```powershell
python evaluate.py             # runs the agent on all 12, writes eval_results.md
```

Scoring is pure code: key facts by word-boundary match, every citation re-verified with
`verify_quote`, and at least two meetings must be cited. Latest result: **9 RIGHT, 2 PARTIAL,
1 WRONG** — see `eval_results.md` for the table and `eval_answers.json` for raw answers.

## Real numbers (from ingest_log and run_log.json)

| Measure | Value |
|---|---|
| Meetings / teams / speaker turns | 138 / 35 / 48,148 |
| Chunks indexed (keyword + vector) | 3,211 |
| Local embedding time | 262 s, no cost |
| Ledger records kept / dropped as unverifiable | 4,249 / 290 |
| Extraction tokens (input + output) | 1,809,456 + 406,597 |
| Extraction time, all 138 meetings | 2,278 s (~38 min) |
| Per question | 1–3 search rounds, 7k–40k tokens, 7–19 s |

## Repository contents

| File | Role |
|---|---|
| `parse.py`, `chunk.py`, `search.py`, `hybrid.py` | ingest + search (code only; local embeddings) |
| `verify.py` | word-for-word quote verification and repair |
| `extract.py` | LLM extraction of the ledger, verified by code |
| `agent.py` | the LangGraph agent loop |
| `run_all.py` | produces `answers.json` (25 answers + TS3012 brief) |
| `snapshot.py`, `ledger_snapshot.json` | checkpoint of the LLM extraction, to skip the ~40 min step |
| `evaluate.py`, `eval_questions.json` | self-evaluation on hand-checked questions |
| `check_format.py` | pre-submission schema + quote check |
| `topics.py`, `test_key.py`, `test_model.py` | inspection and API diagnostics |
| `questions.json`, `transcripts/` | the task input data |
| `answers.json`, `run_log.json`, `eval_results.md`, `eval_answers.json` | outputs and evidence |

## Known limitations (honest)

- 7 of the 25 answers cite only one meeting despite the retry loop (flagged in `run_log.json`).
- The small model sometimes names the wrong speaker in its own prose, or gives a plausible but
  wrong reason — quotes are verified by code, the model's sentences are not (see `eval_results.md`,
  E08).
- The citation relevance check (shared content words) is loose and can pass a true but
  weakly-related quote.

## Data

Transcripts from the AMI Meeting Corpus, University of Edinburgh and partners, released under
CC BY 4.0 (https://groups.inf.ed.ac.uk/ami/corpus/). Reformatted, not edited.
