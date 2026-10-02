"""Generate docs/code_fields.txt -- the decode dictionary from p_help.

PUB LOG ships its own code dictionary inside IDENTIFICATION.zip as P_HELP.CSV,
keyed either as a bare field name ("RNVC") or a field:value pair ("RNVC:2").
This renders it as a readable reference and cross-references each field
against the columns actually loaded in the database.

Regenerate after every monthly load -- DLA adds and retires codes.

    python -m flis_load.codebook
"""

import argparse
import datetime as dt
from pathlib import Path

import duckdb

from .sources import SOURCES

from .paths import DB as DEFAULT_DB, CODE_FIELDS as DEFAULT_OUT

# Above this, a field's values are summarised rather than listed. These are
# the classification taxonomies (Schedule B tariff codes, supply classes),
# which are reference data in their own right, not a short code list.
INLINE_LIMIT = 60

WRAP = 110


def wrap(text, indent):
    """Wrap to WRAP columns at the given indent, preserving no newlines."""
    words, lines, cur = (text or "").split(), [], indent
    for word in words:
        if len(cur) + len(word) + 1 > WRAP and cur.strip():
            lines.append(cur.rstrip())
            cur = indent
        cur += word + " "
    if cur.strip():
        lines.append(cur.rstrip())
    return lines


def column_index():
    """column name -> (locations in loaded tables, locations in deferred ones).

    Sources registered with enabled=False are not in the database, so their
    columns must not be presented as decodable today -- but they are worth
    showing, because they are the reason to enable that source.
    """
    loaded, deferred = {}, {}
    for source in SOURCES.values():
        target = loaded if source.enabled else deferred
        for member in source.members:
            for col in member.columns:
                target.setdefault(col.upper(), []).append(f"{member.table}.{col}")
    return loaded, deferred


def describe_location(field, loaded, deferred):
    """One-line answer to 'which column does this field decode?'"""
    if field in loaded:
        locs = loaded[field]
        text = locs[0]
        return text + (f" (+{len(locs) - 1})" if len(locs) > 1 else "")
    if field in deferred:
        return f"{deferred[field][0]} [deferred]"
    return "-- no column --"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args()

    con = duckdb.connect(str(args.db), read_only=True)

    snapshot = con.execute(
        "SELECT DISTINCT published FROM load_metadata LIMIT 1"
    ).fetchone()
    snapshot = snapshot[0] if snapshot else "unknown"

    # Bare-code rows carry the field's own title and definition.
    titles = {
        r[0]: (r[1], r[2])
        for r in con.execute(
            "SELECT CODE, LITERAL, DESCRIPTION FROM p_help WHERE CODE NOT LIKE '%:%'"
        ).fetchall()
    }

    fields = con.execute(
        "SELECT field, count(*) FROM code_decode GROUP BY 1 ORDER BY 1"
    ).fetchall()

    loaded, deferred = column_index()
    out = []
    w = out.append

    w("=" * WRAP)
    w("PUB LOG CODE DICTIONARY".center(WRAP))
    w("=" * WRAP)
    w("")
    for line in wrap(
        f"The {len(fields)} coded fields defined in P_HELP.CSV (IDENTIFICATION.zip), "
        f"PUB LOG snapshot {snapshot}. Source: DLA Logistics Information Services, "
        "FLIS Data Electronic Reading Room.",
        "",
    ):
        w(line)
    w("")
    w("Query the same data directly:")
    w("    SELECT * FROM code_decode WHERE field = 'RNVC';")
    w("")
    for line in wrap(
        f"Fields with more than {INLINE_LIMIT} values are summarised rather than "
        "listed -- those are classification taxonomies (tariff codes, supply "
        "classes), not short code lists. Query code_decode for their full contents.",
        "",
    ):
        w(line)
    w("")
    w(f"Generated {dt.date.today().isoformat()} by: python -m flis_load.codebook")
    w("")

    # ---------------------------------------------------------------- index
    w("=" * WRAP)
    w("INDEX")
    w("=" * WRAP)
    w("")
    w(f"{'FIELD':<20}{'VALUES':>7}   {'DECODES COLUMN':<44}TITLE")
    w("-" * WRAP)
    for field, n in fields:
        title = titles.get(field, ("", ""))[0] or ""
        col = describe_location(field, loaded, deferred)
        w(f"{field:<20}{n:>7}   {col:<44}{title[:35]}")
    w("")
    w("  [deferred]      the column lives in a source registered but not enabled in sources.py")
    w("  -- no column -- the field is defined by DLA but no loaded table carries it")
    w("")

    # ------------------------------------------------------- uncovered cols
    w("=" * WRAP)
    w("CODED COLUMNS WITH NO DECODE ENTRY")
    w("=" * WRAP)
    w("")
    for line in wrap(
        "Columns in the loaded tables that look coded but have no p_help entry. "
        "Their meanings have to come from the FLIS manuals (DoD 4100.39-M) or, "
        "for DEMIL, from DoD 4160.28-M.",
        "",
    ):
        w(line)
    w("")
    decoded = {f for f, _ in fields}
    # Identifiers and free text, not codes -- excluded from the gap report.
    not_codes = {
        "NIIN", "PART_NUMBER", "CAGE_CODE", "COMPANY", "COMPANY_NAME", "CITY",
        "STATE", "STATE_PROVINCE", "ZIP", "ZIP_POSTAL_ZONE", "COUNTRY",
        "STREET_ADDRESS_1", "STREET_ADDRESS_2", "STREET_ADDRESS_3", "ITEM_NAME",
        "END_ITEM_NAME", "COLLOQUIAL_NAME", "CODE", "LITERAL", "DESCRIPTION",
        "REGS", "INC", "PINC", "RELATED_NSN", "CANCELLED_NIIN", "NIIN_ASGMT",
        "EFF_DATE", "DT_STDZN_DEC", "MSDS", "UNIT_PRICE", "NET_PRICE",
        "EFFECTIVE_DATE", "ROW_OBS_DT", "UOM_QTY", "UI_CONV_FAC", "MGMT_CTL",
    }
    gaps = sorted(
        (col, locs)
        for col, locs in loaded.items()
        if col not in decoded and col not in not_codes
    )
    for col, locs in gaps:
        w(f"  {col:<22}{', '.join(locs)[:74]}")
    w("")

    # --------------------------------------------------------------- detail
    w("=" * WRAP)
    w("FIELD DETAIL")
    w("=" * WRAP)

    for field, n in fields:
        title, desc = titles.get(field, ("", ""))
        w("")
        w("-" * WRAP)
        w(f"{field}" + (f"  --  {title}" if title else ""))
        w(f"  decodes: {describe_location(field, loaded, deferred)}")
        w(f"  values:  {n}")
        w("-" * WRAP)
        if desc:
            for line in wrap(desc, "  "):
                w(line)
            w("")
        if n > INLINE_LIMIT:
            w(f"  {n} values -- too many to list. Query:")
            w(f"      SELECT value, meaning FROM code_decode "
              f"WHERE field = '{field}' ORDER BY value;")
            continue
        for value, meaning in con.execute(
            "SELECT value, meaning FROM code_decode WHERE field = ? ORDER BY value",
            [field],
        ).fetchall():
            body = wrap(meaning or "", " " * 12)
            first = body[0].strip() if body else ""
            w(f"    {value:<7} {first}")
            for line in body[1:]:
                w(line)
        w("")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(out) + "\n")
    con.close()

    print(
        f"Wrote {args.out}  "
        f"({len(fields)} fields, {len(out):,} lines, "
        f"{args.out.stat().st_size / 1024:.0f} KB)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
