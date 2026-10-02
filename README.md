# scrm_data_source_investigation

Loaders for the public datasets that, joined together, describe the defense
industrial base supply chain. Each source is ingested into one shared DuckDB
database so they can be queried across.

## The join

Supply chain risk analysis in the defense industrial base is bottlenecked by
a missing join: three public datasets each hold part of the picture and none
of them connect. **CAGE code is the spine.**

```
     FLIS / PUB LOG            SAM.gov              USAspending / FPDS
   CAGE -> NIIN -> part    CAGE -> UEI, owner,    CAGE -> obligations, PSC,
      number, FSC          exclusions, size        NAICS, place of mfr
            \                     |                        /
             \                    |                       /
              +-------------- CAGE code -----------------+
```

What each contributes:

| Source | Answers | Status |
|---|---|---|
| **FLIS / PUB LOG** | Which suppliers hold design control over which parts | **Loaded** — see [docs/flis.md](docs/flis.md) |
| **SAM.gov** | Who the supplier actually is: UEI, parent, exclusions, business size | Planned |
| **USAspending / FPDS** | What was actually bought, from whom, for how much | Planned |

This repository is the **ingestion layer** only. Analysis projects depend on
the database it produces rather than re-downloading sources —
[critical_materials_and_commodity_exposure_mapper](https://github.com/nick-bruno/critical_materials_and_commodity_exposure_mapper)
is the first consumer.

## Quick start

```bash
python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt
./.venv/bin/python -m playwright install chromium    # FLIS fetcher only

export PYTHONPATH=src
./.venv/bin/python -m flis_load.fetch     # opens a browser window; see docs/flis.md
./.venv/bin/python -m flis_load.load
./.venv/bin/python -m flis_load.verify
```

FLIS alone is ~800 MB of source zips and a ~3 GB database, loading in about
80 seconds.

## Layout

```
data/
  raw/<source>/        downloaded source files (gitignored)
  scrm.duckdb          every source, one database (gitignored)
docs/
  flis.md              FLIS loader documentation
  code_fields.txt      generated PUB LOG code dictionary
src/
  flis_load/           FLIS / PUB LOG loader
  sam_load/            planned
  usaspending_load/    planned
```

Nothing under `data/` is committed. Both the raw files and the database are
reproducible from the loaders, and the sizes are prohibitive.

## Conventions every loader follows

These are not stylistic preferences; each one exists because violating it
produced a real defect while building the FLIS loader.

**Load identifiers as `VARCHAR`.** CAGE codes, NIINs and FSCs are
fixed-width strings whose leading zeros carry meaning — CAGE `00000` is a
real entity, NIIN `000000042` is a real item. Numeric inference silently
destroys them, so `verify.py` asserts exact column widths after every load.

**Validate encoding across the whole file, not a sample.** `P_HELP.CSV` is
clean ASCII for 2.6 MB and then contains one cp1252 en-dash. A 256 KB sniff
concludes UTF-8 and the load then fails or drops rows.

**Record the snapshot vintage.** Every load writes to `load_metadata`, and
`verify.py` fails if sources from different vintages are mixed. This matters
much more across sources than within one: a February USAspending extract
joined to an October FLIS snapshot produces orphan rows that look like data
quality problems but are calendar artifacts.

**Trust the file over the documentation.** Published record layouts lag the
data. Loaders compare the documented column count against the file and
refuse to load on a mismatch rather than silently shifting every column —
this has already caught one stale DLA layout.

**Normalize CAGE identically everywhere.** 5 characters, uppercase,
zero-padded. This is the join key for the entire repository; a loader that
coerces it to an integer drops rows silently rather than erroring. When the
second source lands this becomes the first thing promoted to shared code.

## On shared code

There is deliberately no `scrm_core` package yet. The FLIS loader's
extract-validate-load core is roughly 80% reusable, but SAM is an
API-with-a-key and USAspending is a bulk download, so the fetch layers will
barely overlap. One example is not enough to design the abstraction from.
Extract the common core when the second source actually lands;
`src/flis_load/paths.py` is the intended first thing to promote.
