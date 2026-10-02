# FLIS / PUB LOG

Loads DLA's public **PUB LOG** logistics data into `data/scrm.duckdb`, to map
suppliers (CAGE codes) to parts (NIIN and manufacturer part number).

One source loader within
[scrm_data_source_investigation](../README.md); it contributes the
CAGE -> NIIN -> part number half of the CAGE join.

## What this data is, and what it is not

**WebFLIS is not publicly available.** It requires AMPS registration plus a
CAC/ECA/FBCA/PIV credential. The publicly releasable counterpart is **PUB
LOG**, published by DLA Logistics Information Services as a FOIA (a)(2)
posting in the
[FLIS Data Electronic Reading Room](https://www.dla.mil/Information-Operations/FLIS-Data-Electronic-Reading-Room/):
free, no subscription, no CAC, refreshed on the first business day of each
month. It carries the same NIIN / part number / CAGE relationships WebFLIS
exposes interactively.

Each zip has a record-layout PDF next to it in the Reading Room. Those
layouts are transcribed into `sources.py` and are the contract for the
loader — see *Trusting the layouts* below for where they are already wrong.

## Quick start

```bash
export PYTHONPATH=src   # see the root README for first-time setup
./.venv/bin/python -m flis_load.fetch     # opens a browser window
./.venv/bin/python -m flis_load.load
./.venv/bin/python -m flis_load.verify
```

794 MB of zips, about 65 seconds for a full load on a laptop, and a 3.1 GB
`data/scrm.duckdb`. Peak extra disk is one extracted CSV at a time (largest ~1 GB),
not the whole archive set.

## Why the fetcher opens a browser window

`www.dla.mil` is behind Akamai bot protection. Measured against the live
site, not assumed:

| Client | Result |
|---|---|
| curl / requests, full Chrome headers, `Referer`, HTTP/1.1 and HTTP/2 | **403** |
| Playwright **headless** Chromium | **403** |
| Playwright **headless** real Chrome (`channel="chrome"`) | **403** |
| Playwright **headed** Chromium | 200 |
| Playwright **headed** real Chrome | 200 |

So `fetch.py` runs headed and a window opens. That is the only mode that
works. The pipeline is split so this matters as little as possible: `load.py`
reads whatever zips are sitting in `data/raw/flis/` and does not care how
they got there. If the fetcher ever breaks, it prints the direct URLs and you download
them by hand into `data/raw/flis/` — everything downstream is unchanged.

### Dead ends, so nobody re-walks them

- **`publogstorage.blob.core.windows.net/publog/PublogDVD.zip`** (1.6 GB) is
  public and *not* bot-blocked, and its central directory lists the same
  tables (`V_FLIS_PART.TAB`, `P_CAGE.TAB`, …). But the `.TAB` files are
  encrypted or otherwise proprietary — unrelated tables share an identical
  60-byte header (`a3 50 5c 88 6a 68 02 9c …`) and do not decompress to text.
  They are readable only by the bundled Windows IMD viewer. Not usable.
- **DIBBS** (`dibbs.bsm.dla.mil`) resets the connection for non-browser
  clients. It is the natural next source for award and price history by NSN,
  but it is out of scope here.

## The one thing to get right analytically

A NIIN↔CAGE row in `v_flis_part` is **not** a claim that the company makes
the part. `RNCC` (reference number category) and `RNVC` (reference number
variation) qualify the relationship. `RNCC='3'` with `RNVC='2'` is the
*design control reference* — the number controlled by the organisation that
controls the item's design and production. Everything else is a vendor item,
a secondary reference, or a superseded number.

This is 37.8% of reference rows in the 10/01/2026 snapshot (6,286,924 of
16,623,521). The `is_design_control` flag on `supplier_part` encodes it, and
every risk view filters on it. Use the unfiltered table for cross-reference
lookup ("what else is this part called"); use the filter for supplier
attribution. Confirm the semantics yourself against the data's own decode
table:

```sql
SELECT * FROM code_decode WHERE field IN ('RNCC','RNVC');
-- RNVC 2 -> AN ITEM-IDENTIFYING NUMBER
-- RNVC 3 -> A VENDOR'S REFERENCE NUMBER
```

## Layout

```
data/raw/flis/           downloaded zips + .meta.json provenance sidecars (gitignored)
data/scrm.duckdb         output, shared with every other source (gitignored)
docs/code_fields.txt     generated code dictionary -- 96 coded fields
src/flis_load/
  paths.py               filesystem layout, single point of truth
  sources.py             registry: zips, member CSVs, column layouts
  fetch.py               headed-Playwright downloader -> data/raw/flis/
  load.py                zips -> DuckDB
  views.sql              analysis views
  verify.py              post-load checks
  codebook.py            regenerates docs/code_fields.txt
```

### Code dictionary

PUB LOG ships its own code definitions in `P_HELP.CSV`, exposed as the
`code_decode` view and rendered as a flat reference in
[`docs/code_fields.txt`](docs/code_fields.txt) — 96 fields with their
definitions and values, plus which loaded column each one decodes.
Regenerate it after every monthly load:

```bash
./.venv/bin/python -m flis_load.codebook
```

Of the 96 fields, 21 decode a column that is loaded today, 8 belong to the
deferred `MANAGEMENT.zip`, and 67 describe FLIS fields no enabled source
carries. The file also lists the reverse gap — coded columns in the database
that `p_help` does **not** define, `DMIL`/`DEMIL` among them.

### Tables

| Table | Rows (10/01/2026) | From |
|---|---:|---|
| `v_flis_part` | 16,623,521 | `REFERENCE.zip` — **the supplier↔part link** |
| `p_cage` | 4,158,375 | `CAGE.zip` — company, city, country |
| `v_cage_address` | 4,158,375 | `CAGE.zip` — full street address |
| `p_flis_nsn` | 17,025,717 | `IDENTIFICATION.zip` — FSC, item name |
| `v_flis_identification` | 17,025,717 | `IDENTIFICATION.zip` — DEMIL, criticality, IUID |
| `v_flis_standardization` | 17,400,519 | `IDENTIFICATION.zip` |
| `v_flis_cancelled_niin` | 907,604 | `IDENTIFICATION.zip` |
| `v_colloquial_name` | 33,949 | `IDENTIFICATION.zip` |
| `p_help` | 36,515 | `IDENTIFICATION.zip` — code decode table |
| `load_metadata` | — | snapshot vintage per source |

### Views

| View | Purpose |
|---|---|
| `supplier_part` | parts ⋈ CAGE ⋈ NSN, with `is_design_control`, `is_foreign`, `is_non_manufacturer` |
| **`commercial_supplier_part`** | design-control references pointing at a real company — the risk working set |
| `non_manufacturer_cage` | CAGEs classified `government` / `standards_body` / `placeholder` |
| `niin_supplier_count` | commercial suppliers per item; `= 1` is sole-source |
| `unattributed_parts` | items whose only design authority is a non-manufacturer |
| `cage_part_count` | items per supplier — concentration chokepoints |
| `foreign_sourced_parts` | commercially-sourced items with a non-US supplier |
| `sole_source_foreign` | one commercial supplier, and that supplier is foreign |
| `code_decode` | `(field, value) -> meaning` from `p_help` |

```sql
SELECT COMPANY, COUNTRY, niin_count
FROM cage_part_count ORDER BY niin_count DESC LIMIT 20;

SELECT * FROM niin_supplier_count WHERE supplier_count = 1 AND foreign_supplier_count = 1;
```

### Not every CAGE is a manufacturer

FLIS has no field distinguishing a company from a government command —
`CAGE_STATUS` records lifecycle only. But **3,438 CAGEs holding design
control are not manufacturers**, and they carry **18.3% of all
design-control references**: 2,661 government design activities that own the
drawing and buy the part (NGA alone holds 353,264 NIINs; then TACOM, NAVSEA,
AMCOM), 767 standards bodies whose specification the part conforms to (SAE,
ASTM), and 10 outright placeholders.

DLA states the placeholders in the name field itself, which is what makes
them detectable at all — CAGE `10001` is literally `NAVAIR AND NAVSEA
MANAGED ORIGINAL DESIGN ACTIVITY DWG NOT MATERIAL/PARTS SUPPLIER`, and
`99999` is `DEPARTMENT OF DEFENSE AMMUNITION CODE ASSIGNED AND PROMULGATED
BY...` (13,362 references), meaning the part number is a DoD-assigned
ammunition code rather than any manufacturer's. The `%CODE ASSIGNED%`
pattern also picks up the Canadian DND and UK RNCA equivalents.

Counting them inflates sole-source exposure by 18.1%: **5,688,453 items drop
to 4,657,263** once excluded. The 1,069,961 difference lands in
`unattributed_parts` — items whose real manufacturer FLIS does not record,
which is a sourcing-research backlog, not a supply risk.

`non_manufacturer_cage` holds the classification in one auditable place.
It is keyword matching on company names, so treat it as a strong default
rather than ground truth: it misses government entities with non-obvious
names, and `%ARSENAL%` can catch a commercial one. `verify.py` asserts each
category still matches and that the excluded share stays in a 5–40% band, so
a rename in the CAGE master cannot silently re-admit these.

## Design notes

**Everything loads as `VARCHAR`.** NIIN, CAGE_CODE, FSC, INC and the
single-character status codes are fixed-width identifiers whose leading zeros
carry meaning — CAGE `00000` is the Ordnance Corps, NIIN `000000042` is a
real item. Any numeric inference silently corrupts them, so `verify.py`
asserts exact column widths on every load.

**Encoding is validated across the whole file, not a sample.** `P_HELP.CSV`
is clean ASCII for its first 2.6 MB and then contains a single cp1252
en-dash (`0x96`). A 256 KB sniff concludes "UTF-8" and DuckDB then rejects
the row. `load.py` streams each member while incrementally validating UTF-8
and transcodes from cp1252 when that fails. Extraction has to happen anyway
(DuckDB reads gzip, not zip members), so this costs nothing extra.

**Provenance travels with the data.** `fetch.py` records the "Last Updated"
date DLA states next to each link into a `.meta.json` sidecar; hand-downloaded
files fall back to the newest timestamp inside the archive. `load_metadata`
holds it, and `verify.py` fails if the loaded zips come from different monthly
snapshots — mixing vintages produces orphan joins that look like data quality
problems.

### Trusting the layouts

The record-layout PDFs lag the shipped files. `load.py` compares the layout's
column count against the file's first line and refuses to load on a mismatch
rather than silently shifting every column. This has already fired once:

- **`P_HELP.CSV`** — the layout PDF (dated 9/23/2026) lists 2 columns; the
  shipped file has 4 (`CODE, LITERAL, DESCRIPTION, REGS`). `sources.py`
  follows the file. The extra columns turned out to be useful: `CODE` is
  either a bare field name (`AAC`) or a `field:value` pair (`AAC:A`), which
  is what makes the `code_decode` view possible.

Row-count estimates in the PDFs also drift; they are printed as "layout says"
during a load for comparison only. The largest current gap is
`V_FLIS_CANCELLED_NIIN`, where the layout claims 8M+ and the file holds
907,604. Nothing is wrong with the load — the estimate is simply stale.

### Known data characteristics

- **930 of 16.6M** reference rows (0.006%) carry a CAGE code absent from the
  CAGE master. Expected; `verify.py` allows up to 0.5% and flags a jump,
  which usually means two zips from different months.
- Every NIIN in `v_flis_part` resolves in `p_flis_nsn` (0 orphans).
- Government activities and standards bodies hold design control over large
  numbers of items without manufacturing any of them. The risk views exclude
  them — see *Not every CAGE is a manufacturer* above.

## Adding more datasets

`MANAGEMENT.zip` (unit price, acquisition advice code, source of supply),
`HISTORY.zip` (inactive NSNs — obsolescence) and `MOE_RULE.zip` (which
service uses the item) are registered in `sources.py` with `enabled=False`.
Transcribe the column list from the matching layout PDF, flip `enabled`, and
rerun. No other file changes. Also available but not registered:
`CHARACTERISTICS.zip`, `FREIGHT_PACKAGING.zip`, `H-SERIES.zip`, `FLISV*.zip`,
`MRD*`.

## Monthly refresh

```bash
export PYTHONPATH=src
./.venv/bin/python -m flis_load.fetch && \
./.venv/bin/python -m flis_load.load && \
./.venv/bin/python -m flis_load.verify
```

`load.py` uses `CREATE OR REPLACE TABLE`, so a rerun is a clean replacement.
Views are rebuilt each time and never materialised, so no stale derived
copies survive a reload.
