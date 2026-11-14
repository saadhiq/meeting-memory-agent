# Cost and time: ingesting 1,000 meetings

All measured numbers come from this repo's `ingest_log` table and `run_log.json`, produced by the
real full-corpus run (138 meetings, AMI corpus). Projections are linear in the number of meetings,
which is fair because every meeting is processed independently.

## Measured (138 meetings)

| Step | Measured |
|---|---|
| Parse + chunk + index (pure code) | seconds, negligible |
| Embed 3,211 chunks (local model, no API) | 240 s |
| LLM extraction, all 138 meetings | 1,809,456 input + 406,597 output tokens, 2,278 s |
| Per meeting (avg) | 13,112 input + 2,946 output tokens, 16.5 s |
| Per question at answer time (avg over 25) | 21,431 tokens, 12.4 s, 1–3 search rounds |

## Projected to 1,000 meetings

| Step | Projection | How |
|---|---|---|
| Extraction tokens | ~13.1M input + ~2.9M output | per-meeting average × 1,000 |
| Extraction time | ~4.6 h sequential | 16.5 s × 1,000; the free tier (~40 req/min) is the bottleneck — a paid endpoint with 10 parallel workers brings this under 30 min |
| Embedding | ~29 min on a laptop, $0 | 240 s ÷ 3,211 chunks × 23.3 chunks/meeting × 1,000 |
| Parse/chunk/index | minutes, $0 | linear, pure code |

## Cost

The NVIDIA endpoint used is **free**, so the measured cash cost of this project's ingestion was
**$0**. To state a real-world figure, assume a typical small-model price of **$0.10 per million
input tokens and $0.40 per million output tokens** (assumption, not a measured fact — swap in any
provider's price):

```
13.1M × $0.10/M  +  2.9M × $0.40/M  ≈  $1.31 + $1.18  ≈  $2.50 per 1,000 meetings
```

Answer-time cost is independent of corpus size (retrieval is indexed): ~21k tokens per question,
i.e. about **$0.005 per question** under the same price assumption.

The structural point: ingestion cost is linear in meetings and paid once per meeting; question
cost is flat. At 10,000 meetings the extraction is ~$25 once (plus ~5 h of local embedding), and
asking questions costs the same as it does today.
