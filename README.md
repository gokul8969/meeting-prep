# Meeting Prep and Trusted Advisor Radar (POC)

Built from `amasol_POC_Requirements_v1.docx` (20 Nov 2026). Two tools on one research engine and one shared account profile:

- **Meeting prep** (pull): start, company identification and confirmation, Radar check, describe the meeting, research, check back, summary doc and full 13-section prep pack, review and edit, debrief.
- **Trusted Advisor Radar** (push): add one account or a list, deep research, buying committee, Find new, alerts, weekly digest, and actions (one-click prep, dismiss with a reason, note, remove from Radar).

## Run

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh 4510          # binds 0.0.0.0; open http://100.99.49.120:4510
```

Log in with a user from `data/users.json` (created on first start with an `admin` user and a random password; add the named POC users there), or set `MP_USERS="name:password,name2:password2"`.

## Configuration (environment, or a `.env` file next to `run.sh`)

| Variable | Default | Purpose |
|---|---|---|
| `MP_LLM_BASE_URL`, `MP_LLM_API_KEY` | `LLM_PROXY_*`, then `ANTHROPIC_*` | Anthropic-compatible endpoint |
| `MP_MODEL` | `claude-opus-5-5` | Model for research and writing |
| `MP_RESEARCH_EFFORT` / `MP_WRITE_EFFORT` | `low` / `medium` | Effort per call type |
| `MP_SEARCHES_PER_AREA` | `5` | Web searches per research area |
| `MP_PARALLEL_CALLS` | `4` | Research areas run at once |
| `APOLLO_API_KEY` | empty | Apollo is used when set. Without it, research still completes, people findings are one confidence level lower, and briefs say so |
| `MP_SMTP_HOST`, `MP_SMTP_PORT`, `MP_SMTP_USER`, `MP_SMTP_PASSWORD`, `MP_SMTP_FROM` | empty | Digest email. Without SMTP, digests are saved to `data/outbox/` and listed in Settings |
| `MP_DIGEST_TO` | `puneet@jgsaw.ai` | Digest recipient (also editable in Settings) |
| `MP_TIMEZONE` | `Europe/Berlin` | Digest day and time are set in Settings |
| `MP_PUBLIC_URL` | `http://100.99.49.120:<port>` | Base for links in the digest and in summary PDFs |
| `MP_PRICE_*` | Opus 5.5 list prices, $10 per 1000 searches | Cost log |

## Where things are

- `app/research.py`: the research engine (areas 1 to 8, attendees, source list, Find new merge labels, synthesis, alerts).
- `app/rules.py`: source tiers, confidence rules (section 4.5), content rules (no em dashes, Detectability filtered out). Confidence is computed in code from the sources, never taken from the model.
- `app/brief.py`: summary doc and prep pack. The model writes points that cite finding ids; confidence, labels and sources come from the findings.
- `app/digest.py`: weekly digest scheduler and email.
- `app/apollo.py`: Apollo client. Every call fails soft; refused endpoints are noted on the run so the team can see what the plan includes.
- `knowledge/`: product knowledge (amasol, klaricore, tips, stages, writing rules). Edit here or on the Knowledge page; no code change needed.
- `data/`: SQLite database, outbox, users, secret key.

## How the principles are enforced

- Every finding is stored as a record (section 4.2) with sources, dates, confidence, type, status, origin and rep verdict. A source counts only if its URL came back from the web search (or from the rep or Apollo); findings without a usable source are dropped.
- Rep input (meeting inputs, check-back corrections, brief edits of facts, notes, debriefs) is stored as `Rep input`, rated above web research, and never superseded: research that disagrees is stored as `Conflicting`.
- Find new labels results New, Updated (old one kept as Superseded), Conflicting (both kept) or Already known (last-seen date updated).
- Low and Medium findings become questions; gaps become open questions; debriefs answer them back into the profile.
- Each brief version is saved with a snapshot of the profile it was built from. Costs are logged per call and per run (Costs page).
