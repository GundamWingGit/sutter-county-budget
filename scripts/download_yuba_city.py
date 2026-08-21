#!/usr/bin/env python3
"""Download all budget/financial PDFs linked from Yuba City's
"Budget, Financials & Reports" page into documents/, organized by
fiscal-year folder with readable names.

Usage: python3 scripts/download_documents.py
"""
import html
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "documents"
INDEX = DOCS / "_index.html"
BASE = "https://yubacity.net/"
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}

SKIP_PATTERNS = re.compile(r"food truck", re.I)


def parse_links(text: str):
    """Yield (year_label, title, href). Year headings look like
    <h3>2023-24</h3> (rendered as '2023-244 documents' when scraped);
    anchors to PDFs follow each heading."""
    entries = []
    current_year = "unknown"
    # Walk through the document in order, tracking year headings and pdf anchors.
    token_re = re.compile(
        r'<h3[^>]*>\s*([0-9]{4}-[0-9]{2})|<a\s+[^>]*href="([^"]+\.pdf[^"]*)"[^>]*>(.*?)</a>',
        re.I | re.S,
    )
    for m in token_re.finditer(text):
        if m.group(1):
            current_year = m.group(1)
        else:
            href = html.unescape(m.group(2))
            title = re.sub(r"<[^>]+>", " ", m.group(3))
            title = re.sub(r"\s+", " ", html.unescape(title)).strip()
            entries.append((current_year, title, href))
    return entries


def resolve(href: str) -> str:
    # Resolve against <base href="https://yubacity.net/">, then percent-encode.
    joined = urllib.parse.urljoin(BASE, href)
    parts = urllib.parse.urlsplit(joined)
    path = urllib.parse.quote(parts.path)
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, path, parts.query, ""))


def safe_name(title: str, href: str) -> str:
    base = title if title and not title.lower().endswith(".pdf") else Path(
        urllib.parse.urlsplit(href).path
    ).name
    base = re.sub(r'[\\/:*?"<>|]', "-", base).strip()
    if not base.lower().endswith(".pdf"):
        base += ".pdf"
    return base


def main():
    text = INDEX.read_text(errors="replace")
    entries = parse_links(text)
    seen = set()
    ok, failed = 0, []
    for year, title, href in entries:
        if SKIP_PATTERNS.search(href) or SKIP_PATTERNS.search(title):
            continue
        url = resolve(href)
        if url in seen:
            continue
        seen.add(url)
        outdir = DOCS / year
        outdir.mkdir(parents=True, exist_ok=True)
        dest = outdir / safe_name(title, href)
        if dest.exists() and dest.stat().st_size > 10_000:
            print(f"skip (exists): {dest.relative_to(ROOT)}")
            ok += 1
            continue
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=120) as resp, open(dest, "wb") as f:
                ctype = resp.headers.get("Content-Type", "")
                if "pdf" not in ctype.lower():
                    raise RuntimeError(f"not a pdf ({ctype})")
                while chunk := resp.read(1 << 20):
                    f.write(chunk)
            size_mb = dest.stat().st_size / 1e6
            print(f"ok  {year}  {dest.name}  ({size_mb:.1f} MB)")
            ok += 1
        except Exception as e:
            failed.append((year, title, url, str(e)))
            print(f"FAIL {year}  {title}  {url}  -> {e}", file=sys.stderr)
            if dest.exists():
                dest.unlink()
        time.sleep(0.3)

    print(f"\nDownloaded/present: {ok}; failed: {len(failed)}")
    for year, title, url, err in failed:
        print(f"  FAILED [{year}] {title} :: {url} :: {err}")


if __name__ == "__main__":
    main()
