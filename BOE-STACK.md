# BOE Build Stack — fetched state

The donor repositories this platform is assembled from. Third-party code:
**git-ignored, never committed.** This file records what was fetched and which
commit each is pinned to, so the stack is reproducible.

Fetched with `scripts/fetch_stack.sh`; donor fixes applied by
`scripts/prepare_dsrf.py`. Upstream is the original project, not the
`King-Fisher000` mirrors — that account was suspended on 2026-10-23 and is
unreachable, so the manifest's mirror names were resolved back to upstream.

## Fetched (19 repositories, ~540 MB)

| Repo | Upstream | Commit | Pinned? | Role |
| :--- | :--- | :--- | :--- | :--- |
| `dsrf` | `ddexnet/dsrf` | `1dc231e` | exact | **DDEX flat-file parser + XSDs** |
| `ddex-suite` | `daddykev/ddex-suite` | `3529ae5` | exact | DDEX builder/parser |
| `ddex-workbench` | `daddykev/ddex-workbench` | `2788924` | exact | DDEX validation |
| `dedex` | `miqwit/dedex` | `8f48a6f` | head | DDEX ERN parse (independent check) |
| `ddex-processor` | `AudiusProject/ddex-processor` | `5292f91` | exact | Sales-report generation |
| `rd-portal-backend` | `adytonentertainment/rd-portal-backend` | `590caca` | head | Statement ingest (PDF/XLSX) |
| `stardust-dsp` | `daddykev/stardust-dsp` | `7bee6bc` | exact | Streaming app + DSR generation |
| `ERC-3643` | `ERC-3643/ERC-3643` | `2f0704d` | exact | Restricted security token (I1) |
| `zora-protocol` | `ourzora/zora-protocol` | `32c2931` | exact | Fan token (I2) |
| `splits-contracts` | `0xSplits/splits-contracts` | `6f474b8` | exact | Royalty splits |
| `openzeppelin-contracts` | `OpenZeppelin/openzeppelin-contracts` | `11565e5` | exact | Base contracts |
| `v4-core` | `Uniswap/v4-core` | `bea7f6d` | head | AMM for the venue |
| `bnbchain-mcp` | `bnb-chain/bnbchain-mcp` | `c4557e2` | exact | ERC-8004 agents + confirm |
| `eunomia` | `eunomia-finance/eunomia` | `630a0f8` | head | Contract-enforced agent spend |
| `nirium` | `nirium-protocol/nirium` | `1d3b1a4` | head | Payment + audit-CID rails |
| `labelgrid-mcp` | `labelgrid/labelgrid-mcp` | `42e6428` | exact | Royalty MCP reference |
| `x402` | `coinbase/x402` | `0e6e8c5` | exact | Agent payment protocol |
| `btcpayserver` | `btcpayserver/btcpayserver` | `2a60b14` | head | Payout rail (self-hosted) |
| `openzeppelin-contracts` | — | — | — | (see above) |

**"Pinned?"** — `exact` means the manifest commit was reachable in the shallow
clone. `head` means it was not (history rewritten or the commit is older than
the clone depth); the current default-branch head is used instead. Five repos
are at `head`: `rd-portal-backend`, `v4-core`, `eunomia`, `nirium`,
`btcpayserver`.

## Not fetched

| Repo | Why |
| :--- | :--- |
| `stardust-distro` | **This is our repo** (`Bianca7492/Distro`). Fetching it into itself would be circular. |
| `hyperswitch` | 349 MB; present on the account as `B-Pay-backend`. |
| `navidrome`, `feishin` | Streaming/player references only; GPL-3.0. Read upstream if needed. |
| `ddex-php` | 299 MB; PHP, and the Python path covers DSR/ERN. |
| `opbnb` | Node software (86 MB). Run as a service, do not vendor. |
| `abikeola817/music-royalty-distribution` | Hollow: 12 entries, 1 commit, no `.clar` files. |
| `music-metadata/music-metadata` | A static website, not a library. |

## Donor quirks found (all covered by tests)

The manifest warns "verify before depending on it". Verified, and it does not
run as shipped on Python 3.12+:

| # | Quirk | Fix |
| ---: | :--- | :--- |
| 1 | protobuf bindings are not committed | generate from `proto/*.proto` |
| 2 | generated bindings use Python-2 absolute imports | rewrite to package-relative |
| 3 | `csv.Dialect.quotechar = ''` is invalid since 3.11 | set to `None` |
| 4 | decimal cells live in `decimal_value`, not `string_value` | read all four value fields |
| 5 | row types appear as `SU03`, `SU03.01`, `SU0301` | normalise to the 4-char base |
| 6 | files declare profile versions with no shipped XSD | alias to nearest schema |
| 7 | the donor's validator rejects row types its own XSDs accept | parse with it, validate ourselves |

**Quirk 4 is the dangerous one.** Reading only string and integer cells parses
the file successfully and reports *every revenue figure as zero*. Silent, and
exactly the class of error the reconciliation gate exists to catch.

**Quirk 7 means the donor's validation output must not be trusted.** Its parser
is sound; its validator is not.

## Licences

| Licence | Repos | Obligation |
| :--- | :--- | :--- |
| MIT / Apache-2.0 | most | attribution only |
| GPL-3.0 | `splits-contracts` | copyleft — run as a deployed service, do not link |
| BUSL | `v4-core` | check terms before production |
| none | `rd-portal-backend` | all rights reserved by default; verify with authors |

`splits-contracts` is load-bearing for Stage 4. It is GPL-3.0, so it must run as
a separate deployed service rather than being vendored into our source. Flagged
for legal review before production.
