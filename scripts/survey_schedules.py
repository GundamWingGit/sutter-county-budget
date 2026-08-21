#!/usr/bin/env python3
"""Scan every digital budget book and record which pages contain the key
State Controller schedules. Output: data/schedule_map.json"""
import glob
import json
import re
import sys
from pathlib import Path

import pdfplumber

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "schedule_map.json"

# One preferred book per fiscal year (digital-native, adopted preferred).
BOOKS = {
    "FY 2009-10": "documents/FY 2009-10/FY 2009-10 Recommended Budget - Volume II (Complete File).pdf",
    "FY 2010-11": "documents/FY 2010-11/FY 2010-11 Recommended Budget - Volume II (Complete File).pdf",
    "FY 2011-12": "documents/FY 2011-12/FY 2011-12 Recommended Budget (Complete File).pdf",
    "FY 2012-13": "documents/FY 2012-13/FY 12-13 Recommended Budget (Complete File).pdf",
    "FY 2013-14": "documents/FY 2013-14/FY 2013-14 Recommended Budget (Complete File).pdf",
    "FY 2014-15": "documents/FY 2014-15/FY 2014-15 Recommended Budget (Complete File).pdf",
    "FY 2015-16": "documents/FY 2015-16/FY 2015-16 Adopted Budget.pdf",
    "FY 2016-17": "documents/FY 2016-17/2016-2017 Adopted Budget.pdf",
    "FY 2017-18": "documents/FY 2017-18/FY 2017-18 Adopted Budget.pdf",
    "FY 2018-19": "documents/FY 2018-19/FY 2018-19 Recommended Budget (Complete File).pdf",
    "FY 2019-20": "documents/FY 2019-20/FY 2019-20 Recommended Budget (Complete File).pdf",
    "FY 2020-21": "documents/FY 2020-21/FY 2020-21 Recommended Budget (Complete File).pdf",
    "FY 2021-22": "documents/FY 2021-22/00 - FY 2021-22 Recommended Budget.pdf",
    "FY 2022-23": "documents/FY 2022-23/00 - FY 2022-23 Recommended Budget.pdf",
    "FY 2023-24": "documents/FY 2023-24/FY 2023-24 Adopted Budget.pdf",
    "FY 2024-25": "documents/FY 2024-25/00 - FY 2024-25 Recommended Budget.pdf",
    "FY 2025-26": "documents/FY 2025-26/0 FY 2025-26 Adopted Budget.pdf",
    "FY 2026-27": "documents/FY 2026-27/FY 2026-27 Recommended Budget.pdf",
}

PATTERNS = {
    "all_funds_summary": r"all funds summary|summary of county budget",
    "gov_funds_summary": r"governmental funds summary",
    "fund_balance": r"analysis of fund balance|fund balance.{0,40}governmental funds",
    "sources_by_source": r"summary of additional financing sources by source",
    "sources_detail": r"detail of additional financing sources",
    "uses_by_function": r"summary of financing uses by function|detail of financing uses by function",
    "position_alloc": r"position allocation",
}


def main():
    result = {}
    for fy, rel in BOOKS.items():
        path = ROOT / rel
        entry = {"path": rel, "pages": len_pages(path), "hits": {k: [] for k in PATTERNS}}
        with pdfplumber.open(path) as pdf:
            for i, page in enumerate(pdf.pages):
                t = (page.extract_text() or "")[:3000]
                head = " ".join(t.split("\n")[:6]).lower()
                for key, pat in PATTERNS.items():
                    if re.search(pat, head):
                        entry["hits"][key].append(i + 1)
        result[fy] = entry
        summary = {k: (v[:3], len(v)) for k, v in entry["hits"].items() if v}
        print(fy, "::", json.dumps(summary), flush=True)
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(result, indent=1))
    print("wrote", OUT)


def len_pages(path):
    with pdfplumber.open(path) as pdf:
        return len(pdf.pages)


if __name__ == "__main__":
    main()
