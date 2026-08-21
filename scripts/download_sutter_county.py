#!/usr/bin/env python3
"""Download Sutter County budget archive PDFs (FY 2016-17 through FY 2026-27)
from sutter.gov into documents/, one folder per fiscal year.

The site sits behind Akamai, so requests must look like a real browser.

Usage: python3 scripts/download_sutter_county.py
"""
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "documents"
BASE = "https://www.sutter.gov"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-User": "?1",
    "Referer": "https://www.sutter.gov/government/county-departments/county-administrator/county-budgets/archives",
}

# Fiscal-year folders discovered on the archives page (newest first).
YEAR_FOLDERS = {
    "FY 2026-27": "/government/county-departments/county-administrator/county-budgets/archives/-folder-746",
    "FY 2025-26": "/government/county-departments/county-administrator/county-budgets/archives/-folder-696",
    "FY 2024-25": "/government/county-departments/county-administrator/county-budgets/archives/-folder-640",
    "FY 2023-24": "/government/county-departments/county-administrator/county-budgets/archives/-folder-583",
    "FY 2022-23": "/government/county-departments/county-administrator/county-budgets/archives/-folder-518",
    "FY 2021-22": "/government/county-departments/county-administrator/county-budgets/archives/-folder-377",
    "FY 2020-21": "/government/county-departments/county-administrator/county-budgets/archives/-folder-129",
    "FY 2019-20": "/government/county-departments/county-administrator/county-budgets/archives/-folder-110",
    "FY 2018-19": "/government/county-departments/county-administrator/county-budgets/archives/-folder-111",
    "FY 2017-18": "/government/county-departments/county-administrator/county-budgets/archives/-folder-112",
    "FY 2016-17": "/government/county-departments/county-administrator/county-budgets/archives/-folder-113",
    "FY 2015-16": "/government/county-departments/county-administrator/county-budgets/archives/-folder-114",
    "FY 2014-15": "/government/county-departments/county-administrator/county-budgets/archives/-folder-115",
    "FY 2013-14": "/government/county-departments/county-administrator/county-budgets/archives/-folder-116",
    "FY 2012-13": "/government/county-departments/county-administrator/county-budgets/archives/-folder-117",
    "FY 2011-12": "/government/county-departments/county-administrator/county-budgets/archives/-folder-118",
    "FY 2010-11": "/government/county-departments/county-administrator/county-budgets/archives/-folder-119",
    "FY 2009-10": "/government/county-departments/county-administrator/county-budgets/archives/-folder-120",
    "FY 2008-09": "/government/county-departments/county-administrator/county-budgets/archives/-folder-121",
    "Related Governing Boards & District Budgets": "/government/county-departments/county-administrator/county-budgets/archives/-folder-737",
}

# Sidebar promo links that appear on every page.
SKIP_TITLES = re.compile(r"election results|community academy", re.I)

DOC_RE = re.compile(
    r'href="(/home/showpublisheddocument/\d+/\d+)"[^>]*>(.*?)</a>', re.S
)
FOLDER_RE = re.compile(
    r'href="(/government/[^"]*archives/-folder-(\d+))"[^>]*>(.*?)</a>', re.S
)
KNOWN_FOLDER_IDS = {p.rsplit("-", 1)[1] for p in YEAR_FOLDERS.values()} | {"109"}


def clean(s: str) -> str:
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", s).strip()


COOKIE_JAR = "/tmp/sutter_cookies.txt"


def fetch(url: str, retries: int = 3) -> bytes:
    """Fetch via curl: Akamai rejects urllib's TLS fingerprint but accepts curl's."""
    cmd = ["curl", "-s", "--compressed", "--max-time", "180",
           "-c", COOKIE_JAR, "-b", COOKIE_JAR,
           "-w", "\n%{http_code}", BASE + url]
    for k, v in HEADERS.items():
        cmd += ["-H", f"{k}: {v}"]
    for attempt in range(retries):
        out = subprocess.run(cmd, capture_output=True).stdout
        body, _, code = out.rpartition(b"\n")
        if code == b"200":
            return body
        if attempt == retries - 1:
            raise RuntimeError(f"HTTP {code.decode(errors='replace')}")
        print(f"  retry (HTTP {code.decode(errors='replace')})", file=sys.stderr)
        time.sleep(3 * (attempt + 1))


def crawl_folder(url: str, outdir: Path, stats: dict, seen_folders: set):
    seen_folders.add(url)
    html = fetch(url).decode(errors="replace")
    time.sleep(0.5)

    for m in DOC_RE.finditer(html):
        href, title = m.group(1), clean(m.group(2))
        if SKIP_TITLES.search(title) or not title:
            continue
        name = re.sub(r'[\\/:*?"<>|]', "-", title)
        if not name.lower().endswith(".pdf"):
            name += ".pdf"
        dest = outdir / name
        if dest.exists() and dest.stat().st_size > 5_000:
            print(f"skip (exists): {dest.relative_to(ROOT)}")
            stats["ok"] += 1
            continue
        outdir.mkdir(parents=True, exist_ok=True)
        try:
            data = fetch(href)
            if data[:5] != b"%PDF-":
                raise RuntimeError("response is not a PDF")
            dest.write_bytes(data)
            print(f"ok  {dest.relative_to(DOCS)}  ({len(data)/1e6:.1f} MB)")
            stats["ok"] += 1
        except Exception as e:
            stats["failed"].append((str(dest.relative_to(DOCS)), href, str(e)))
            print(f"FAIL {title} :: {href} :: {e}", file=sys.stderr)
        time.sleep(0.5)

    # Recurse into unknown subfolders (e.g. Adopted vs Recommended splits).
    for m in FOLDER_RE.finditer(html):
        href, fid, title = m.group(1), m.group(2), clean(m.group(3))
        if fid in KNOWN_FOLDER_IDS or href in seen_folders or href == url:
            continue
        print(f"-> subfolder: {title}")
        crawl_folder(href, outdir / re.sub(r'[\\/:*?"<>|]', "-", title), stats, seen_folders)


def main():
    stats = {"ok": 0, "failed": []}
    seen = set()
    for year, url in YEAR_FOLDERS.items():
        print(f"\n=== {year} ===")
        crawl_folder(url, DOCS / year, stats, seen)
    print(f"\nDownloaded/present: {stats['ok']}; failed: {len(stats['failed'])}")
    for name, href, err in stats["failed"]:
        print(f"  FAILED {name} :: {href} :: {err}")


if __name__ == "__main__":
    main()
