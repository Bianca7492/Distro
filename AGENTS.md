# AGENTS.md

Repository-specific knowledge for agents working on this codebase.

## What this is

`Bianca7492/Distro` — a DDEX-compliant music distribution and tokenised-royalty
platform. Assembled from donor repositories rather than written from scratch.
Local branch is `release`; **commits are local only, do not push unless asked.**

## Standing rules

1. **Do not write from scratch.** Clone or use existing repos and assemble.
2. **Do not push.** Commit locally. Pushing requires an explicit instruction.
3. Money is exact. Never use floats for currency.

## Layout

| Path | What |
| :--- | :--- |
| `revenue/` | Stage 1 revenue truth (statement → reconciled facts) |
| `tests/` | pytest suite (47 tests) |
| `PLAN/` | Stage/pass/gate plan, task board, schedule |
| `scripts/` | `fetch_stack.sh`, `prepare_dsrf.py` |
| `boe-stack/` | **Third-party donor repos. Git-ignored. Never commit.** |
| `BOE-STACK.md` | Record of what was fetched and the donor quirks |

## Commands

```bash
python3 -m pytest                       # full suite (47 tests)
python3 -m pytest tests/test_dsrf_adapter.py -v   # donor integration tests

bash scripts/fetch_stack.sh             # fetch donor stack (once)
python3 scripts/prepare_dsrf.py         # make the dsrf donor runnable

python3 -m revenue.cli --db /tmp/r.db ingest <statement> --catalogue <cat.json>
python3 -m revenue.cli --db /tmp/r.db reconcile <statement-id>
python3 -m revenue.cli --db /tmp/r.db inspect <statement-id>
```

## Environment notes

* Python 3.13. `lxml`, `rapidfuzz`, `pytest`, `six`, `protobuf`,
  `grpcio-tools` (for protoc), `setuptools<81` (for `pkg_resources`).
* `dsrf` donor needs `setuptools<81`; `pkg_resources` is removed in newer
  setuptools and the donor imports it unconditionally.

## Gotchas that cost time

* **The donor `dsrf` library does not run as shipped on Python 3.12+.** Seven
  separate problems; all are fixed by `scripts/prepare_dsrf.py` and documented
  in `BOE-STACK.md`. Re-run that script after any fresh fetch.
* **The most dangerous donor quirk:** decimal cells are stored in
  `decimal_value`, not `string_value`. Reading only string/integer cells
  parses fine and reports **every revenue figure as zero**. If revenue looks
  like zero, check `_cells()` in `revenue/dsrf_adapter.py` first.
* **The donor's validator is wrong.** It rejects row types (`SY0201`) that its
  own XSDs define (`SY02`/`SY0201` depending on profile version). Its *parser*
  is sound. Never trust its validation output; validate ourselves.
* **Row types vary by profile version:** `SU03` (1.0), `SU03.01`/`SU0301`
  (1.1.1), `SU03.02` (1.2). Always normalise to the 4-character base.
* **Profile versions in files may have no shipped XSD** (e.g. `UGCProfile 1.1`;
  only 1.1.1 ships). Alias rather than fail.
* **`King-Fisher000` GitHub mirrors are suspended** (2026-10-23). Resolve
  manifest repo names back to upstream projects.
* `Money.scale` is a **property**, not a method. `StatementLine` has no
  `rights_controller` field — allocations are Stage 4, via
  `dsrf_adapter.parse_allocations()`.

## Provenance: `daddykev` references are correct and must stay

`daddykev` is **not** a former identity of ours to be scrubbed. It is a real,
unrelated third-party GitHub user — Kevin Moo (id `51279136`, `daddykev`) —
whose public repositories are the genuine upstreams for `ddex-suite`,
`ddex-workbench`, and `stardust-dsp` (all verified present and reachable).

So every `daddykev/...` string in this repository is a **donor coordinate**:
the upstream repo to fetch and the attribution the MIT licence requires. Do not
rewrite these to `Bianca7492/...`. Doing so would point `fetch_stack.sh` and the
SDK manifest at repositories that do not exist (`Bianca7492/ddex-suite` → 404)
and would strip required attribution from MIT-licensed code.

Our own authorship is already clean and needs no rewrite:

* All commits in this repository are authored and committed by `Bianca7492`
  `<86526814+Bianca7492@users.noreply.github.com>`. Zero commits, in any ref or
  tag, carry a `daddykev` author, committer, or email.
* No commit message mentions `daddykev`.
* The `boe-research` corpus is authored by `claudeone7492` and has no
  `daddykev` authorship either.

The only tracked files that name `daddykev` are `BOE-STACK.md` and
`scripts/fetch_stack.sh`, and in both it is the upstream column of the donor
register — the correct value.

Two unrelated local repos, `/workspace/4track-dev` and `/workspace/electricshe`,
carry `Kevin Moo <kevinmoo@Kevins-MacBook-Pro.local>` authorship. Those are
Kevin's own projects, not this platform's history, and are out of scope.

## Stage 1 invariants (do not weaken)

1. `facts + suspense == statement total`, to the cent. The gate fails otherwise.
2. **Fuzzy matches are never auto-distributed** — they go to suspense.
3. Money is integer minor units plus a currency code.

## Next

Stage 2 (identity & catalog): Supabase schema, upload, fingerprint, ERN
delivery. Donors available in `boe-stack/`: `ddex-suite`, `ddex-workbench`,
`stardust-dsp`, `dedex`.
