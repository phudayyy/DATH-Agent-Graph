# ĐATH - Agent Graph

A minimal knowledge graph that answers [CRONQUESTIONS](https://github.com/apoorvumang/CronKGQA) questions through a
**context pack**, keeping two properties of the system it was cut from:

- **Bi-temporal.** Every fact carries when it was true (`valid_from`/`valid_to`) and when it was recorded
  (`observed_at`/`expired_at`). A question is asked *as of* a moment, and only what was true then is visible.
- **Permission-aware, fail-closed.** An entity is readable only by the principals an ACL row names, for the years
  that row covers. The permission cut runs before anything is trimmed, and the pack says how many related objects
  were withheld — never which.

## Where the code comes from

`src/brain/` is a small part of the author's private system, finch-brain, frozen at its 2026-09-28 baseline. It was
imported byte for byte and then cut down; [`PROVENANCE.md`](PROVENANCE.md) lists each file with its git blob at the
baseline and what was kept. Everything else under `src/agent_graph/` and `tests/` was written for this project.

## Run

Python 3.12+ and [uv](https://docs.astral.sh/uv/). Postgres is embedded through `pgserver` — no install, no Docker.

    uv sync --extra dev
    uv run pytest -q

The dataset is not redistributed here. Download `data_v2` as the CronKGQA README describes, then convert the question
pickle to JSON Lines once — a pickle runs code when it is read, so open only the official file, and only once:

    uv run python -m agent_graph.cronquestions convert data/wikidata_big/questions/test.pickle test.jsonl
    uv run python -m agent_graph.run --kg data/wikidata_big/kg --questions test.jsonl --limit 1000 --out runs/test.jsonl

Each output line is one question: the pack exactly as an agent reads it, and whether the gold answer is in it.

## How a question meets the graph

| Step | What happens |
|---|---|
| Time | A fact `[start, end]` in years becomes `[1 Jan start, 1 Jan end+1)` in UTC — the end year counts, as in CronKGQA. A question naming a year is asked as of 1 January of that year; one naming none is asked as of now. |
| Seeds | The question's entities, which CRONQUESTIONS provides (CronKGQA assumes perfect entity linking). |
| Spread | One hop from the seeds the asker may see, over edges true at that moment. |
| Cut | Entities the asker may not see are removed and counted; then the pack is capped at 50 entities and 100 relations. |

Asking as of a single moment is the **snapshot** semantics of the system this was cut from. It cannot answer a
question that names no year — *when*, *first/last*, *before/after* — and the runner reports that as a number rather
than hiding it.

## Permissions

Without `--acl`, every entity is granted to `anyone`: an open world, the baseline. With it, only the listed grants
exist, and an entity no grant names is visible to no one:

    # item <TAB> principal [<TAB> first year <TAB> last year]
    Q30	anyone
    Q76	alice@lab.test	2001	2004
    Q11696	domain:lab.test

A principal is an email, `group:<name>`, `domain:<name>` or `anyone`; `--identity` sets who asks.

## What this does not do

It does not choose an answer, so it reports whether the answer is **in the pack** — the ceiling on Hits@1 for any
answerer that reads the pack — rather than Hits@1 itself. Answerers belong to the experiments built on top.
