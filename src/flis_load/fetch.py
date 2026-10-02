"""Optional Playwright fetcher: download PUB LOG zips into raw/.

www.dla.mil sits behind Akamai bot protection and returns 403 to every
scripted HTTP client -- curl and requests fail with a full Chrome header set,
a Referer, HTTP/1.1 and HTTP/2 alike. A real browser passes, so this drives
Chromium and lets it download the files through its own network stack.

It runs HEADED, and a window will open. That is not a preference: Akamai
serves 403 to headless Chromium and to headless real Chrome, while both pass
headed. Measured, not assumed -- see the table in the README.

This is the fragile half of the pipeline by design. If it breaks, it prints
the URLs and exits; load.py reads raw/ and neither knows nor cares how the
files got there.

    python -m flis_load.fetch
    python -m flis_load.fetch --only CAGE.zip
    python -m flis_load.fetch --headless        # expected to 403; for retesting
"""

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Optional

from .sources import READING_ROOM, SOURCES, enabled_sources

from .paths import RAW as DEFAULT_RAW

DATE_RE = re.compile(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b")


def published_date(row_text: str) -> Optional[str]:
    """Pull the 'Last Updated' date out of the file's table row.

    The Reading Room page states each file's publication date next to the
    link. That is better provenance than an HTTP header, because it is the
    date DLA claims for the monthly snapshot.
    """
    match = DATE_RE.search(row_text)
    return match.group(0) if match else None


def fetch(raw_dir: Path, zip_names, headless: bool, timeout_ms: int) -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print(
            "playwright is not installed.\n"
            "  pip install playwright && python -m playwright install chromium",
            file=sys.stderr,
        )
        return 2

    raw_dir.mkdir(parents=True, exist_ok=True)
    failures = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=headless,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = browser.new_context(
            accept_downloads=True,
            locale="en-US",
            viewport={"width": 1440, "height": 900},
        )
        page = context.new_page()

        print(f"Opening {READING_ROOM}")
        response = page.goto(
            READING_ROOM, wait_until="domcontentloaded", timeout=timeout_ms
        )
        if response and response.status == 403:
            print(
                f"  ! Akamai returned 403 ({page.title()}).\n"
                f"    Headless mode is blocked; drop --headless and retry.",
                file=sys.stderr,
            )
            browser.close()
            return _manual_instructions(raw_dir, list(zip_names))

        for zip_name in zip_names:
            # Match on the href rather than the link text: the page labels
            # files inconsistently ("Freight/Packaging Data" -> FREIGHT_PACKAGING.zip).
            link = page.locator(f'a[href$="{zip_name}" i]').first
            if link.count() == 0:
                print(f"  ! no link for {zip_name} on the page")
                failures.append(zip_name)
                continue

            stamp = None
            try:
                stamp = published_date(link.locator("xpath=ancestor::tr").inner_text())
            except Exception:
                pass  # provenance is nice to have; the download is not optional

            target = raw_dir / zip_name
            print(f"  downloading {zip_name} (published {stamp or 'unknown'}) ...", flush=True)
            try:
                with page.expect_download(timeout=timeout_ms) as dl:
                    link.click()
                download = dl.value
                download.save_as(target)
            except Exception as exc:
                print(f"  ! {zip_name} failed: {type(exc).__name__}: {exc}")
                failures.append(zip_name)
                continue

            target.with_suffix(target.suffix + ".meta.json").write_text(
                json.dumps(
                    {
                        "zip_name": zip_name,
                        "source_url": SOURCES[zip_name].url,
                        "last_modified": stamp,
                        "retrieved_from": READING_ROOM,
                    },
                    indent=2,
                )
            )
            print(f"    -> {target} ({target.stat().st_size / 1e6:.0f} MB)")

        context.close()
        browser.close()

    if failures:
        return _manual_instructions(raw_dir, failures)
    return 0


def _manual_instructions(raw_dir: Path, zip_names) -> int:
    """The fallback that keeps the pipeline usable when the fetcher breaks."""
    print(
        "\nCould not download automatically. Open the Reading Room in your "
        f"browser and save these into {raw_dir}/ by hand:\n  {READING_ROOM}",
        file=sys.stderr,
    )
    for zip_name in zip_names:
        print(f"  {SOURCES[zip_name].url}", file=sys.stderr)
    print("\nThen run:  python -m flis_load.load", file=sys.stderr)
    return 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--raw", type=Path, default=DEFAULT_RAW)
    ap.add_argument("--only", action="append", help="zip name; repeatable")
    ap.add_argument(
        "--headless",
        action="store_true",
        help="run without a window (Akamai blocks this; for retesting only)",
    )
    ap.add_argument("--timeout", type=int, default=600_000, help="ms per download")
    args = ap.parse_args()

    if args.only:
        unknown = set(args.only) - set(SOURCES)
        if unknown:
            ap.error(f"unknown source(s): {', '.join(sorted(unknown))}")
        names = list(args.only)
    else:
        names = [s.zip_name for s in enabled_sources()]

    return fetch(args.raw, names, args.headless, args.timeout)


if __name__ == "__main__":
    raise SystemExit(main())
