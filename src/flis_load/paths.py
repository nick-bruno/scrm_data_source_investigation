"""Filesystem layout for the FLIS loader.

Every module resolves paths through here rather than recomputing REPO_ROOT,
so moving the source under a different root is a one-file change.

When a second data source lands (SAM, USAspending) this is the obvious thing
to promote into a shared scrm_core -- RAW_ROOT and DB are repo-wide, only
RAW is source-specific. Deliberately not generalised yet: one example is not
enough to design the abstraction from.
"""

from pathlib import Path

# src/flis_load/paths.py -> src/flis_load -> src -> repository root
REPO_ROOT = Path(__file__).resolve().parents[2]

DATA = REPO_ROOT / "data"
RAW_ROOT = DATA / "raw"

# One database for every source. The whole point of the monorepo is that
# FLIS, SAM and USAspending join on CAGE; a file boundary between them would
# turn every cross-source query into ATTACH plumbing.
DB = DATA / "scrm.duckdb"

RAW = RAW_ROOT / "flis"
DOCS = REPO_ROOT / "docs"
CODE_FIELDS = DOCS / "code_fields.txt"
VIEWS_SQL = Path(__file__).resolve().parent / "views.sql"
