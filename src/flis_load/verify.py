"""Post-load sanity checks.

Run after every monthly load. These are the checks that would have caught the
two real defects found while building this pipeline: a stale record layout
(P_HELP gained two columns) and a non-UTF-8 byte 2.6 MB into a file whose
first 256 KB looked like clean ASCII.

    python -m flis_load.verify

Exit code is 0 only if every check passes.
"""

import argparse
import sys
from pathlib import Path

import duckdb

from .paths import DB as DEFAULT_DB

# Fixed-width identifiers whose leading zeros carry meaning. If any of these
# lost their width, something typed a column as a number.
WIDTHS = [
    ("v_flis_part", "NIIN", 9),
    ("v_flis_part", "CAGE_CODE", 5),
    ("p_cage", "CAGE_CODE", 5),
    ("p_flis_nsn", "NIIN", 9),
    ("p_flis_nsn", "FSC", 4),
]

# Floors, not targets: catches a truncated or half-extracted load without
# breaking every time DLA's monthly counts drift upward.
MIN_ROWS = [
    ("v_flis_part", 15_000_000),
    ("p_cage", 3_000_000),
    ("v_cage_address", 3_000_000),
    ("p_flis_nsn", 15_000_000),
    ("p_help", 10_000),
]

# A CAGE in the reference file with no entry in the CAGE master. Expected to
# be tiny; a jump means the two zips came from different monthly snapshots.
MAX_ORPHAN_CAGE_PCT = 0.5


class Checks:
    def __init__(self, con):
        self.con = con
        self.failed = 0

    def report(self, ok, label, detail=""):
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}{'  ' + detail if detail else ''}")
        if not ok:
            self.failed += 1

    def scalar(self, sql):
        return self.con.execute(sql).fetchone()[0]

    def tables(self):
        return {r[0] for r in self.con.execute("SHOW TABLES").fetchall()}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = ap.parse_args()

    if not args.db.exists():
        print(f"No database at {args.db}. Run: python -m flis_load.load", file=sys.stderr)
        return 2

    con = duckdb.connect(str(args.db), read_only=True)
    c = Checks(con)
    present = c.tables()

    print("\nProvenance")
    rows = con.execute(
        "SELECT DISTINCT zip_name, published, provenance FROM load_metadata "
        "ORDER BY zip_name"
    ).fetchall()
    for zip_name, published, prov in rows:
        print(f"  {zip_name:<22} published {published}  ({prov})")
    vintages = {r[1] for r in rows}
    c.report(
        len(vintages) <= 1,
        "all sources from one monthly snapshot",
        f"vintages: {', '.join(sorted(str(v) for v in vintages))}",
    )

    print("\nRow counts (floors)")
    for table, floor in MIN_ROWS:
        if table not in present:
            c.report(False, f"{table} missing")
            continue
        n = c.scalar(f"SELECT count(*) FROM {table}")
        c.report(n >= floor, f"{table}", f"{n:,} (floor {floor:,})")

    print("\nFixed-width identifiers (leading zeros intact)")
    for table, col, width in WIDTHS:
        if table not in present:
            continue
        bad = c.scalar(
            f"SELECT count(*) FROM {table} WHERE length({col}) <> {width}"
        )
        c.report(bad == 0, f"{table}.{col} == {width} chars", f"{bad:,} violations")

    print("\nReferential integrity")
    if {"v_flis_part", "p_cage"} <= present:
        total = c.scalar("SELECT count(*) FROM v_flis_part")
        orphan = c.scalar(
            "SELECT count(*) FROM v_flis_part p "
            "LEFT JOIN p_cage c USING(CAGE_CODE) WHERE c.CAGE_CODE IS NULL"
        )
        pct = 100 * orphan / total if total else 0
        c.report(
            pct <= MAX_ORPHAN_CAGE_PCT,
            "CAGE codes resolve to the CAGE master",
            f"{orphan:,} orphans ({pct:.3f}%, limit {MAX_ORPHAN_CAGE_PCT}%)",
        )
    if {"v_flis_part", "p_flis_nsn"} <= present:
        orphan = c.scalar(
            "SELECT count(*) FROM v_flis_part p "
            "LEFT JOIN p_flis_nsn n USING(NIIN) WHERE n.NIIN IS NULL"
        )
        c.report(orphan == 0, "NIINs resolve to the NSN master", f"{orphan:,} orphans")

    print("\nDesign-control filter is meaningful")
    if "v_flis_part" in present:
        total = c.scalar("SELECT count(*) FROM v_flis_part")
        dc = c.scalar("SELECT count(*) FROM v_flis_part WHERE RNCC='3' AND RNVC='2'")
        pct = 100 * dc / total if total else 0
        # Neither degenerate (filters everything) nor a no-op (filters nothing).
        c.report(
            5 < pct < 95,
            "RNCC=3/RNVC=2 narrows without emptying",
            f"{dc:,} rows ({pct:.1f}%)",
        )

    print("\nEnd-to-end: a NIIN with more suppliers than design-controllers")
    try:
        row = con.execute(
            """
            SELECT NIIN,
                   count(*)                                  AS refs,
                   count(*) FILTER (WHERE is_design_control)  AS dc
            FROM supplier_part
            GROUP BY NIIN
            HAVING dc > 0 AND refs > dc
            LIMIT 1
            """
        ).fetchone()
        if row:
            niin, refs, dc = row
            c.report(True, f"NIIN {niin}", f"{refs} references -> {dc} design-control")
            for r in con.execute(
                "SELECT NSN, ITEM_NAME, PART_NUMBER, COMPANY, COUNTRY, "
                "RNCC, RNVC, is_design_control FROM supplier_part "
                f"WHERE NIIN = '{niin}' ORDER BY is_design_control DESC LIMIT 4"
            ).fetchall():
                print(f"         {r}")
        else:
            c.report(False, "no NIIN found where the filter narrows")
    except duckdb.Error as exc:
        c.report(False, "supplier_part view", str(exc)[:90])

    print("\nRisk views return plausible, non-degenerate results")
    for view, sql in [
        ("niin_supplier_count", "SELECT count(*) FROM niin_supplier_count"),
        ("sole source", "SELECT count(*) FROM niin_supplier_count WHERE supplier_count=1"),
        ("commercial_supplier_part", "SELECT count(*) FROM commercial_supplier_part"),
        ("unattributed_parts", "SELECT count(*) FROM unattributed_parts"),
        ("foreign_sourced_parts", "SELECT count(DISTINCT NIIN) FROM foreign_sourced_parts"),
        ("sole_source_foreign", "SELECT count(DISTINCT NIIN) FROM sole_source_foreign"),
        ("code_decode", "SELECT count(*) FROM code_decode"),
    ]:
        try:
            n = c.scalar(sql)
            c.report(n > 0, view, f"{n:,}")
        except duckdb.Error as exc:
            c.report(False, view, str(exc)[:90])

    print("\nNon-manufacturer exclusion is applied consistently")
    try:
        # Every category must still match something. A rename in the CAGE
        # master would silently empty one of these and quietly re-admit
        # government activities to the sole-source count.
        for category in ("government", "standards_body", "placeholder"):
            n = c.scalar(
                "SELECT count(*) FROM non_manufacturer_cage "
                f"WHERE category = '{category}'"
            )
            c.report(n > 0, f"{category} CAGEs classified", f"{n:,}")

        leaked = c.scalar(
            "SELECT count(*) FROM cage_part_count WHERE CAGE_CODE IN "
            "(SELECT CAGE_CODE FROM non_manufacturer_cage)"
        )
        c.report(leaked == 0, "no non-manufacturer in cage_part_count", f"{leaked:,}")

        dc = c.scalar("SELECT count(*) FROM supplier_part WHERE is_design_control")
        com = c.scalar("SELECT count(*) FROM commercial_supplier_part")
        pct = 100 * (dc - com) / dc if dc else 0
        # Seen at 18.3% on the 10/01/2026 snapshot. A large move means the
        # patterns stopped matching, not that DLA re-catalogued the fleet.
        c.report(
            5 < pct < 40,
            "excluded share is in the expected band",
            f"{pct:.1f}% excluded ({dc - com:,} rows)",
        )
    except duckdb.Error as exc:
        c.report(False, "non_manufacturer_cage", str(exc)[:90])

    con.close()
    print(f"\n{'All checks passed.' if not c.failed else f'{c.failed} check(s) FAILED.'}\n")
    return 1 if c.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
