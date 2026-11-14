# Evaluation results

12 hand-checked questions (see eval_questions.json). **9 RIGHT, 2 PARTIAL, 1 WRONG.**

Scored in code: key facts by word-boundary match, every citation re-verified word-for-word with verify_quote, and at least two meetings must be cited. Raw agent answers: eval_answers.json.

| id | team | verdict | key facts | citations valid | meetings cited | missing facts |
|---|---|---|---|---|---|---|
| E01 | IS1009 | RIGHT | 3/3 | 3/3 | 2 | - |
| E02 | IS1009 | RIGHT | 3/3 | 2/2 | 2 | - |
| E03 | IS1009 | RIGHT | 2/2 | 3/3 | 2 | - |
| E04 | IS1008 | RIGHT | 3/3 | 2/2 | 2 | - |
| E05 | IS1008 | RIGHT | 3/3 | 2/2 | 2 | - |
| E06 | TS3003 | RIGHT | 3/3 | 2/2 | 2 | - |
| E07 | TS3003 | RIGHT | 3/3 | 2/2 | 2 | - |
| E08 | TS3004 | WRONG | 1/3 | 2/2 | 2 | regular chip first, speech recognition reason |
| E09 | TS3004 | PARTIAL | 1/2 | 2/2 | 1 | twenty cents |
| E10 | TS3005 | RIGHT | 3/3 | 2/2 | 2 | - |
| E11 | TS3005 | PARTIAL | 2/3 | 2/2 | 2 | titanium floated |
| E12 | TS3005 | RIGHT | 1/1 | 3/3 | 3 | - |
