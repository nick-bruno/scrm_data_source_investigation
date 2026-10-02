"""Load PUB LOG zips from raw/ into a DuckDB database.

Reads only from the local raw/ directory, so it behaves identically whether a
human or fetch.py put the files there.

Everything lands as VARCHAR on purpose. NIIN, CAGE_CODE, FSC, INC and the
single-character status codes are fixed-width identifiers whose leading zeros
carry meaning; any numeric inference silently corrupts them.

    python -m flis_load.load                     # load every enabled source
    python -m flis_load.load --only REFERENCE.zip
    python -m flis_load.load --limit 50000       # fast smoke test
"""

import argparse
import codecs
import csv
import datetime as dt
import io
import json
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import NamedTuple, Optional

import duckdb

from .sources import Member, Source, enabled_sources, SOURCES

from .paths import RAW as DEFAULT_RAW, DB as DEFAULT_DB, VIEWS_SQL

CHUNK = 8 * 1024 * 1024

# PUB LOG is a mainframe-derived extract: overwhelmingly ASCII, but stray
# Windows-1252 punctuation (en-dashes, smart quotes) turns up in free-text
# description fields. Those bytes are invalid UTF-8 and make DuckDB reject
# the row. cp1252 is the real encoding; latin-1 is the floor because it
# cannot fail on any byte.
FALLBACK_ENCODINGS = ["cp1252", "latin-1"]


class Dialect(NamedTuple):
    delimiter: str
    has_header: bool
    first_line_fields: int


def extract_member(zf: zipfile.ZipFile, member: Member, dest_dir: Path) -> Path:
    """Extract one CSV to dest_dir as valid UTF-8.

    DuckDB cannot read a member out of a zip (it handles gzip, not zip), so
    extraction is required anyway -- which makes it the natural place to fix
    the encoding.

    Encoding is validated across the WHOLE file, not a sample. A 256 KB
    sniff said "utf-8" for P_HELP.CSV and the only bad byte in 2.6 MB sat at
    offset 2,649,512. Sampling cannot answer this question.
    """
    out_path = dest_dir / member.csv_name

    # Pass 1: copy verbatim while incrementally validating UTF-8.
    decoder = codecs.getincrementaldecoder("utf-8")()
    valid_utf8 = True
    with zf.open(member.csv_name) as src, open(out_path, "wb") as dst:
        while True:
            chunk = src.read(CHUNK)
            if not chunk:
                break
            dst.write(chunk)
            if valid_utf8:
                try:
                    decoder.decode(chunk)
                except UnicodeDecodeError:
                    valid_utf8 = False
    if valid_utf8:
        try:
            decoder.decode(b"", final=True)
        except UnicodeDecodeError:
            valid_utf8 = False
    if valid_utf8:
        return out_path

    # Pass 2: the file is not UTF-8. Transcode it. Both fallbacks are
    # single-byte encodings, so chunk boundaries are safe to split on.
    for encoding in FALLBACK_ENCODINGS:
        try:
            with zf.open(member.csv_name) as src, open(
                out_path, "w", encoding="utf-8", newline=""
            ) as dst:
                while True:
                    chunk = src.read(CHUNK)
                    if not chunk:
                        break
                    dst.write(chunk.decode(encoding))
            print(f"    transcoded {member.csv_name} from {encoding} to utf-8")
            return out_path
        except UnicodeDecodeError:
            continue

    raise SystemExit(f"{member.csv_name}: could not decode under any known encoding")


def probe_dialect(csv_path: Path, member: Member) -> Dialect:
    """Determine delimiter and header presence from the extracted file.

    The layout PDFs give column names and order but not the CSV dialect, and
    DLA has shipped both comma- and tab-delimited extracts of these tables.
    Sniffing beats hardcoding a guess that breaks on next month's drop.
    """
    with open(csv_path, "r", encoding="utf-8", errors="replace") as fh:
        text = fh.read(256 * 1024)

    # Drop a trailing partial line so the sniffer never sees a truncated row.
    text = text[: text.rfind("\n") + 1] or text

    try:
        delimiter = csv.Sniffer().sniff(text[:65536], delimiters=",\t|").delimiter
    except csv.Error:
        # Degenerate case (e.g. a single-column file): fall back to whichever
        # candidate actually appears on the first line.
        first = text.split("\n", 1)[0]
        delimiter = max(",\t|", key=first.count)

    rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
    first_row = rows[0] if rows else []

    expected = [c.upper() for c in member.columns]
    has_header = [c.strip().strip('"').upper() for c in first_row] == expected

    return Dialect(delimiter, has_header, len(first_row))


def load_member(
    con: duckdb.DuckDBPyConnection,
    csv_path: Path,
    member: Member,
    dialect: Dialect,
    limit: Optional[int],
) -> int:
    """Create one table from one extracted CSV. Returns the row count."""
    if dialect.first_line_fields != len(member.columns):
        raise SystemExit(
            f"\n{member.csv_name}: layout says {len(member.columns)} columns "
            f"but the file has {dialect.first_line_fields}.\n"
            f"DLA has revised the record layout. Re-read the layout PDF for "
            f"this zip and update sources.py before loading.\n"
            f"Expected: {', '.join(member.columns)}"
        )

    columns_sql = ", ".join(f"'{c}': 'VARCHAR'" for c in member.columns)
    limit_sql = "" if limit is None else f" LIMIT {int(limit)}"

    con.execute(
        f"""
        CREATE OR REPLACE TABLE {member.table} AS
        SELECT * FROM read_csv(
            ?,
            columns        = {{{columns_sql}}},
            delim          = ?,
            header         = ?,
            encoding       = 'utf-8',
            quote          = '"',
            escape         = '"',
            all_varchar    = true,
            ignore_errors  = false,
            null_padding   = false
        ){limit_sql}
        """,
        [str(csv_path), dialect.delimiter, dialect.has_header],
    )
    return con.execute(f"SELECT count(*) FROM {member.table}").fetchone()[0]


def source_provenance(zip_path: Path) -> dict:
    """Vintage of a source file.

    Risk conclusions have to be attributable to a monthly snapshot, so the
    publication date travels with the data. fetch.py writes a .meta.json
    sidecar holding the "Last Updated" date DLA states next to the link.
    Absent that -- a hand-downloaded file -- fall back to the newest timestamp
    inside the archive, which the zip records regardless of how it reached disk.
    """
    sidecar = zip_path.with_suffix(zip_path.suffix + ".meta.json")
    if sidecar.exists():
        meta = json.loads(sidecar.read_text())
        if meta.get("last_modified"):
            return {
                "published": meta["last_modified"],
                "provenance": "reading-room-page",
            }

    with zipfile.ZipFile(zip_path) as zf:
        stamps = [i.date_time for i in zf.infolist() if i.date_time]
    if stamps:
        newest = dt.datetime(*max(stamps))
        return {"published": newest.isoformat(), "provenance": "zip-entry-mtime"}
    return {"published": None, "provenance": "unknown"}


def load_source(
    con: duckdb.DuckDBPyConnection,
    source: Source,
    zip_path: Path,
    limit: Optional[int],
) -> None:
    prov = source_provenance(zip_path)
    print(f"\n{source.zip_name}  ({zip_path.stat().st_size / 1e6:.0f} MB, "
          f"published {prov['published']} via {prov['provenance']})")

    with zipfile.ZipFile(zip_path) as zf:
        available = set(zf.namelist())
        for member in source.members:
            if member.csv_name not in available:
                print(f"  ! {member.csv_name} not in archive -- skipped")
                continue

            tmpdir = Path(tempfile.mkdtemp(prefix="flis_"))
            try:
                # Each CSV is extracted, loaded, then deleted before the next
                # one -- peak disk stays at a single table, not the whole zip.
                csv_path = extract_member(zf, member, tmpdir)
                dialect = probe_dialect(csv_path, member)
                rows = load_member(con, csv_path, member, dialect, limit)
            finally:
                shutil.rmtree(tmpdir, ignore_errors=True)

            print(
                f"  {member.table:<26} {rows:>12,} rows "
                f"(layout says {member.approx_rows or '?'}; "
                f"delim={dialect.delimiter!r} header={dialect.has_header})"
            )

            con.execute(
                """
                INSERT INTO load_metadata VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    source.zip_name,
                    member.csv_name,
                    member.table,
                    rows,
                    prov["published"],
                    prov["provenance"],
                    dt.datetime.now().isoformat(timespec="seconds"),
                ],
            )


def build_indexes(con: duckdb.DuckDBPyConnection) -> None:
    """Index the join keys. v_flis_part is traversed in both directions."""
    wanted = [
        ("v_flis_part", "NIIN"),
        ("v_flis_part", "CAGE_CODE"),
        ("p_cage", "CAGE_CODE"),
        ("p_flis_nsn", "NIIN"),
    ]
    tables = {r[0] for r in con.execute("SHOW TABLES").fetchall()}
    for table, col in wanted:
        if table in tables:
            con.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{table}_{col.lower()} "
                f"ON {table}({col})"
            )
    print(f"\nIndexed {sum(1 for t, _ in wanted if t in tables)} join keys.")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--raw", type=Path, default=DEFAULT_RAW)
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--only", action="append", help="zip name; repeatable")
    ap.add_argument("--limit", type=int, help="rows per table, for smoke tests")
    ap.add_argument("--no-views", action="store_true")
    args = ap.parse_args()

    sources = enabled_sources()
    if args.only:
        requested = set(args.only)
        unknown = requested - set(SOURCES)
        if unknown:
            ap.error(f"unknown source(s): {', '.join(sorted(unknown))}")
        sources = [SOURCES[z] for z in requested]

    present = [(s, args.raw / s.zip_name) for s in sources]
    missing = [s for s, p in present if not p.exists()]
    if missing:
        print(f"Missing from {args.raw}/:", file=sys.stderr)
        for s in missing:
            print(f"  {s.zip_name:<22} {s.url}", file=sys.stderr)
        print(
            "\nDownload them with a real browser (www.dla.mil returns 403 to "
            "scripted clients), or run:  python -m flis_load.fetch",
            file=sys.stderr,
        )
    present = [(s, p) for s, p in present if p.exists()]
    if not present:
        return 1

    con = duckdb.connect(str(args.db))
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS load_metadata (
            zip_name     VARCHAR,
            csv_name     VARCHAR,
            table_name   VARCHAR,
            row_count    BIGINT,
            published    VARCHAR,
            provenance   VARCHAR,
            loaded_at    VARCHAR
        )
        """
    )
    for source, zip_path in present:
        con.execute("DELETE FROM load_metadata WHERE zip_name = ?", [source.zip_name])
        load_source(con, source, zip_path, args.limit)

    build_indexes(con)

    if not args.no_views and VIEWS_SQL.exists():
        con.execute(VIEWS_SQL.read_text())
        print("Analysis views created.")

    if args.limit:
        print(f"\n*** --limit {args.limit} was set: tables are TRUNCATED, not a full load.")
    print(f"\nDone -> {args.db}")
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
