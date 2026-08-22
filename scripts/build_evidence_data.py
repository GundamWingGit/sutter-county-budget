#!/usr/bin/env python3
"""Build click-to-source evidence data for the dashboard.

Outputs:
  dashboard/data/books.json
  dashboard/data/lines/<book>.json (+ .gz)
  dashboard/data/lines-index.json
  dashboard/data/citations.js   — each cite carries page + hit bbox when known

Usage:
    python3 scripts/build_evidence_data.py
    python3 scripts/build_evidence_data.py --citations-only
"""
from __future__ import annotations

import argparse
import csv
import gzip
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
DASH = ROOT / "dashboard" / "data"
LINES_DIR = DASH / "lines"
PDFS = ROOT / "dashboard" / "pdfs"

BOOKS = [
    ("FY 2018-19", "documents/FY 2018-19/FY 2018-19 Recommended Budget (Complete File).pdf",
     "FY2018-19-recommended.pdf", "Recommended Budget"),
    ("FY 2019-20", "documents/FY 2019-20/FY 2019-20 Recommended Budget (Complete File).pdf",
     "FY2019-20-recommended.pdf", "Recommended Budget"),
    ("FY 2020-21", "documents/FY 2020-21/FY 2020-21 Recommended Budget (Complete File).pdf",
     "FY2020-21-recommended.pdf", "Recommended Budget"),
    ("FY 2021-22", "documents/FY 2021-22/00 - FY 2021-22 Recommended Budget.pdf",
     "FY2021-22-recommended.pdf", "Recommended Budget"),
    ("FY 2022-23", "documents/FY 2022-23/00 - FY 2022-23 Recommended Budget.pdf",
     "FY2022-23-recommended.pdf", "Recommended Budget"),
    ("FY 2023-24", "documents/FY 2023-24/FY 2023-24 Adopted Budget.pdf",
     "FY2023-24-adopted.pdf", "Adopted Budget"),
    ("FY 2024-25", "documents/FY 2024-25/00 - FY 2024-25 Recommended Budget.pdf",
     "FY2024-25-recommended.pdf", "Recommended Budget"),
    ("FY 2025-26", "documents/FY 2025-26/0 FY 2025-26 Adopted Budget.pdf",
     "FY2025-26-adopted.pdf", "Adopted Budget"),
    ("FY 2026-27", "documents/FY 2026-27/FY 2026-27 Recommended Budget.pdf",
     "FY2026-27-recommended.pdf", "Recommended Budget"),
]
BOOK_FILE = {label: dest for label, _src, dest, _kind in BOOKS}

ACTUAL_BOOK = {
    "FY2016-17": "FY 2018-19",
    "FY2017-18": "FY 2019-20",
    "FY2018-19": "FY 2020-21",
    "FY2019-20": "FY 2021-22",
    "FY2020-21": "FY 2022-23",
    "FY2021-22": "FY 2023-24",
    "FY2022-23": "FY 2024-25",
    "FY2023-24": "FY 2025-26",
    "FY2024-25": "FY 2026-27",
}

# Analysis totals can differ by a few dollars from the inked Schedule total.
PRINTED_ALIASES = {
    # analysis value -> preferred printed forms to search
    398114614: [398114608],
}


def book_slug(label: str) -> str:
    return label.replace(" ", "_")


def fmt_query(value: float) -> str:
    v = int(round(value))
    if v < 0:
        return f"({abs(v):,})"
    return f"{v:,}"


def value_variants(value: float) -> list[str]:
    """Printed forms of a number as they typically appear in the PDFs."""
    ints = [int(round(value))]
    for alt in PRINTED_ALIASES.get(ints[0], []):
        ints.append(alt)
    out: list[str] = []
    for v in ints:
        if v < 0:
            a = abs(v)
            out.extend([f"({a:,})", f"$({a:,})", f"-{a:,}", f"-{a}", str(v), f"({a})"])
        else:
            out.extend([f"{v:,}", f"${v:,}", str(v), f"{v:,.0f}"])
    # unique, preserve order
    seen = set()
    uniq = []
    for s in out:
        if s not in seen:
            seen.add(s)
            uniq.append(s)
    return uniq


def plain_digits(s: str) -> str:
    return re.sub(r"[^\d]", "", s or "")


def plain_int_key(s: str) -> str:
    """Digit string with trailing decimal zeros stripped (58,783,273.00 → 58783273)."""
    t = (s or "").strip()
    # If it looks like a money amount with cents, drop the fractional part first
    m = re.match(r"^[^\d-]*(-?\(?[\d,]+)\.(\d{2})\)?$", t.replace("$", "").strip())
    if m:
        return plain_digits(m.group(1))
    d = plain_digits(t)
    # Also accept keys that had .00 glued on as digits
    if len(d) > 2 and d.endswith("00"):
        head = d[:-2]
        if head:
            return head
    return d


def plains_for_value(value: float) -> set[str]:
    keys = set()
    for v in value_variants(value):
        k = plain_int_key(v)
        if k:
            keys.add(k)
        # raw digits too
        d = plain_digits(v)
        if d:
            keys.add(d)
    return keys


# ---------------------------------------------------------------------------
# PDF locator — pypdf for candidate pages, pdfplumber for glyph bbox
# ---------------------------------------------------------------------------

class BookLocator:
    """Locate a printed number in a budget book and return page + bbox."""

    _cache: dict[str, "BookLocator"] = {}

    def __init__(self, book: str):
        self.book = book
        self.path = PDFS / BOOK_FILE[book]
        self._texts: list[str] | None = None
        self._plumber = None

    @classmethod
    def get(cls, book: str) -> "BookLocator":
        if book not in cls._cache:
            cls._cache[book] = cls(book)
        return cls._cache[book]

    @classmethod
    def close_all(cls):
        for loc in cls._cache.values():
            if loc._plumber is not None:
                loc._plumber.close()
                loc._plumber = None
        cls._cache.clear()

    def texts(self) -> list[str]:
        if self._texts is None:
            from pypdf import PdfReader
            reader = PdfReader(str(self.path))
            self._texts = [(p.extract_text() or "") for p in reader.pages]
            print(f"    indexed {self.book}: {len(self._texts)} pages", flush=True)
        return self._texts

    def plumber(self):
        if self._plumber is None:
            import pdfplumber
            self._plumber = pdfplumber.open(str(self.path))
        return self._plumber

    def find(
        self,
        value: float,
        *,
        page_hint: int | None = None,
        near: str | None = None,
        prefer: str | None = None,
    ) -> dict | None:
        """Return best {page,x0,top,x1,bottom,query,score} or None."""
        variants = value_variants(value)
        plains = plains_for_value(value)
        if not plains:
            return None

        texts = self.texts()
        candidate_pages: list[int] = []

        if page_hint and 1 <= page_hint <= len(texts):
            candidate_pages.append(page_hint)
            for d in (-1, 1, -2, 2):
                p = page_hint + d
                if 1 <= p <= len(texts):
                    candidate_pages.append(p)

        # Full-book text scan for the digit sequence (tolerate .00 in source text)
        for i, text in enumerate(texts):
            flat = plain_digits(text)
            # Also build a version that strips .00 patterns from the source
            flat2 = plain_digits(re.sub(r"\.00\b", "", text))
            if any(p in flat or p in flat2 for p in plains):
                pno = i + 1
                if pno not in candidate_pages:
                    candidate_pages.append(pno)

        if not candidate_pages:
            return None

        hits: list[dict] = []
        near_l = (near or "").lower()
        prefer_re = re.compile(prefer, re.I) if prefer else None

        for pno in candidate_pages:
            text = texts[pno - 1]
            page_score = 0
            if prefer_re and prefer_re.search(text):
                page_score += 100
            # Prefer summary schedules over detail (5/8 over 6/7)
            if re.search(r"Schedule\s*5\b", text, re.I):
                page_score += 40
            if re.search(r"Schedule\s*8\b", text, re.I):
                page_score += 40
            if re.search(r"Schedule\s*1\b", text, re.I):
                page_score += 30
            if re.search(r"Total Financing Uses by Function|Summarization by Source", text, re.I):
                page_score += 35
            if re.search(r"Governmental|All Funds Summary", text, re.I):
                page_score += 15
            # Soft-penalize detail schedules when we want a county rollup
            if prefer_re and re.search(r"Schedule\s*[67]\b", text, re.I):
                page_score -= 25
            if near_l and near_l[:12] in text.lower():
                page_score += 30

            bbox = self._bbox_on_page(pno, plains, near=near)
            if not bbox:
                hits.append({
                    "page": pno, "x0": None, "top": None, "x1": None, "bottom": None,
                    "query": variants[0], "score": page_score, "pageW": None, "pageH": None,
                })
                continue
            bbox["score"] = page_score + bbox.get("score", 0)
            hits.append(bbox)

        if not hits:
            return None
        hits.sort(key=lambda h: -h["score"])
        best = hits[0]
        # Drop null-bbox entries if we have a real glyph hit
        with_box = [h for h in hits if h.get("x0") is not None]
        if with_box:
            with_box.sort(key=lambda h: -h["score"])
            best = with_box[0]
        return {
            "page": best["page"],
            "x0": best.get("x0"),
            "top": best.get("top"),
            "x1": best.get("x1"),
            "bottom": best.get("bottom"),
            "query": best.get("query") or variants[0],
            "pageW": best.get("pageW"),
            "pageH": best.get("pageH"),
        }

    def _bbox_on_page(self, page_1based: int, plains: set[str], near: str | None = None) -> dict | None:
        pdf = self.plumber()
        page = pdf.pages[page_1based - 1]
        words = page.extract_words(x_tolerance=3, y_tolerance=3)
        if not words:
            return None

        def word_key(text: str) -> str:
            return plain_int_key(text)

        # Single-word matches (including 58,783,273.00)
        matches = []
        for w in words:
            key = word_key(w["text"])
            if key in plains and key:
                matches.append([w])

        # Contiguous runs (split commas / $ across glyphs)
        if not matches:
            for i in range(len(words)):
                acc = ""
                run = []
                for j in range(i, min(i + 8, len(words))):
                    if run and abs(words[j]["top"] - run[0]["top"]) > 4:
                        break
                    piece = words[j]["text"]
                    # If a token has .00, treat as end of number
                    acc += plain_digits(re.sub(r"\.00$", "", piece))
                    run.append(words[j])
                    key = acc
                    # strip trailing 00 from glued cents
                    if key.endswith("00") and key[:-2] in plains:
                        key = key[:-2]
                    if key in plains and key:
                        matches.append(list(run))
                        break
                    if len(acc) > max(len(p) for p in plains) + 4:
                        break

        if not matches:
            return None

        near_l = (near or "").lower()
        near_y = None
        if near_l:
            for w in words:
                if near_l[:10] in w["text"].lower():
                    near_y = w["top"]
                    break

        scored = []
        for run in matches:
            x0 = min(w["x0"] for w in run)
            x1 = max(w["x1"] for w in run)
            top = min(w["top"] for w in run)
            bottom = max(w["bottom"] for w in run)
            score = 10
            if near_y is not None:
                score += max(0, 40 - abs(top - near_y))
            # Prefer the query without .00 for PDF.js search fallback
            q = "".join(w["text"] for w in run)
            q_clean = re.sub(r"\.00$", "", q)
            scored.append({
                "page": page_1based,
                "x0": round(x0, 2),
                "top": round(top, 2),
                "x1": round(x1, 2),
                "bottom": round(bottom, 2),
                "query": q_clean,
                "score": score,
                "pageW": round(float(page.width), 2),
                "pageH": round(float(page.height), 2),
            })
        scored.sort(key=lambda h: -h["score"])
        return scored[0]


def hit_payload(hit: dict | None) -> dict | None:
    if not hit:
        return None
    out = {k: hit[k] for k in ("page", "x0", "top", "x1", "bottom", "query", "pageW", "pageH")
           if hit.get(k) is not None}
    return out or None


def apply_hit(cite: dict, hit: dict | None) -> dict:
    """Attach page/query/hit from a locator result."""
    if not hit:
        return cite
    cite = dict(cite)
    cite["page"] = hit["page"]
    cite["query"] = hit.get("query") or cite.get("query")
    cite["hit"] = hit_payload(hit)
    if cite.get("type") != "derived":
        cite["type"] = "printed"
    return cite


# ---------------------------------------------------------------------------
# books.json / line indexes
# ---------------------------------------------------------------------------

def write_books_json() -> dict:
    out = {}
    for label, src, dest, kind in BOOKS:
        dp = PDFS / dest
        out[label] = {
            "file": dest,
            "title": f"{label} {kind}",
            "kind": kind.lower().split()[0],
            "path": src,
            "bytes": dp.stat().st_size if dp.exists() else 0,
            "archiveUrl": (
                "https://www.sutter.gov/government/county-departments/"
                "county-administrator/county-budgets/archives"
            ),
        }
    (DASH / "books.json").write_text(json.dumps(out, indent=2))
    return out


def build_line_indexes() -> dict:
    LINES_DIR.mkdir(parents=True, exist_ok=True)
    catalog = {"books": []}

    for label, _src, dest, kind in BOOKS:
        csv_path = DATA / "unit_lines" / f"{book_slug(label)}.csv"
        if not csv_path.exists():
            print(f"skip missing {csv_path}")
            continue

        rows = []
        units = set()
        pages = set()
        seen = {}

        with open(csv_path, newline="") as f:
            for r in csv.DictReader(f):
                unit = (r.get("unit_name") or "").strip()
                code = (r.get("unit_code") or "").strip()
                line = (r.get("line_name") or "").strip()
                # Schedule 9 column indexes (1 2 3 4 5) are not dollar lines.
                if not line or re.fullmatch(r"\d{1,2}(?:\s+\d{1,2})*", line):
                    continue
                try:
                    value = float(r["value"])
                    page = int(r["page"])
                except (ValueError, KeyError):
                    continue
                fy = r.get("fy", "")
                kind_col = r.get("kind", "")
                key = (code, unit, line, fy, kind_col)
                entry = {
                    "u": unit,
                    "c": code,
                    "l": line,
                    "f": fy,
                    "k": kind_col,
                    "v": round(value, 2) if value != int(value) else int(value),
                    "p": page,
                }
                if key in seen:
                    if re.search(r"(?i)^total |^net |^unreimbursed", line):
                        rows[seen[key]] = entry
                    continue
                seen[key] = len(rows)
                rows.append(entry)
                if unit:
                    units.add(unit)
                pages.add(page)

        payload = {
            "book": label,
            "file": dest,
            "title": f"{label} {kind}",
            "n": len(rows),
            "rows": rows,
        }
        json_path = LINES_DIR / f"{book_slug(label)}.json"
        raw = json.dumps(payload, separators=(",", ":"))
        json_path.write_text(raw)
        with gzip.open(str(json_path) + ".gz", "wb") as gz:
            gz.write(raw.encode("utf-8"))

        unit_list = sorted(units)
        catalog["books"].append({
            "book": label,
            "slug": book_slug(label),
            "file": dest,
            "title": f"{label} {kind}",
            "rows": len(rows),
            "pages": max(pages) if pages else 0,
            "unitCount": len(unit_list),
        })
        print(f"  {label}: {len(rows):,} rows, {len(unit_list)} units, "
              f"{json_path.stat().st_size / 1e6:.1f} MB")

    (DASH / "lines-index.json").write_text(json.dumps(catalog, indent=2))
    return catalog


# ---------------------------------------------------------------------------
# Citations
# ---------------------------------------------------------------------------

def find_unit_pages(book: str, fy: str, kind: str, unit_code: str = "",
                    unit_name: str = "") -> list[dict]:
    csv_path = DATA / "unit_lines" / f"{book_slug(book)}.csv"
    if not csv_path.exists():
        return []
    hits = []
    code_norm = re.sub(r"^CC", "", unit_code).lstrip("0")
    name_re = re.compile(re.escape(unit_name[:20]), re.I) if unit_name else None
    with open(csv_path, newline="") as f:
        for r in csv.DictReader(f):
            if r.get("fy") != fy or r.get("kind") != kind:
                continue
            line = (r.get("line_name") or "").strip()
            if not re.search(
                r"(?i)^(total )?(revenues|expenditures)|net (county )?cost|unreimbursed",
                line,
            ):
                continue
            rc = re.sub(r"^CC", "", r.get("unit_code") or "").lstrip("0")
            rn = r.get("unit_name") or ""
            if code_norm and rc and rc != code_norm:
                continue
            if name_re and not name_re.search(rn):
                continue
            try:
                v = float(r["value"])
                p = int(r["page"])
            except ValueError:
                continue
            hits.append({
                "type": "printed",
                "book": book,
                "page": p,
                "value": v,
                "query": fmt_query(v),
                "unit": rn,
                "unitCode": r.get("unit_code", ""),
                "line": line,
                "label": f"{rn} · {line}",
                "fy": fy,
                "kind": kind,
            })
    return hits


def top_unit_total_children(
    book: str, fy: str, kind: str, side: str, n: int = 8
) -> list[dict]:
    """Largest printed unit Total Revenue / Total Expenditure rows."""
    csv_path = DATA / "unit_lines" / f"{book_slug(book)}.csv"
    if not csv_path.exists():
        return []
    pat = re.compile(r"(?i)^total revenues?$|^revenues$" if side == "revenue"
                     else r"(?i)^total expenditures?$|^expenditures$")
    best: dict[tuple, dict] = {}
    with open(csv_path, newline="") as f:
        for r in csv.DictReader(f):
            if r.get("fy") != fy or r.get("kind") != kind:
                continue
            line = (r.get("line_name") or "").strip()
            if not pat.search(line):
                continue
            try:
                v = float(r["value"])
                p = int(r["page"])
            except ValueError:
                continue
            if abs(v) < 1e5:
                continue
            key = (r.get("unit_code"), r.get("unit_name"), line)
            prev = best.get(key)
            if not prev or abs(v) >= abs(prev["value"]):
                best[key] = {
                    "type": "printed",
                    "book": book,
                    "page": p,
                    "value": v,
                    "query": fmt_query(v),
                    "unit": r.get("unit_name") or "",
                    "unitCode": r.get("unit_code") or "",
                    "line": line,
                    "label": f"{r.get('unit_name') or r.get('unit_code')} · {line}",
                    "fy": fy,
                    "kind": kind,
                }
    ranked = sorted(best.values(), key=lambda x: -abs(x["value"]))[:n]
    # Dedupe TOTAL vs Total / same unit appearing twice
    uniq = {}
    for row in ranked:
        key = (row.get("unitCode") or row.get("unit"), round(row["value"]))
        prev = uniq.get(key)
        if not prev or row["page"] >= prev["page"]:
            uniq[key] = row
    ranked = sorted(uniq.values(), key=lambda x: -abs(x["value"]))
    # Attach glyph hits on those pages
    loc = BookLocator.get(book)
    out = []
    for row in ranked:
        hit = loc.find(row["value"], page_hint=row["page"], near=row.get("unit") or row.get("line"))
        row = apply_hit(row, hit) if hit else row
        out.append(row)
    return out


def locate_county_metric(book: str, value: float, metric: str) -> dict | None:
    prefer = {
        "revenue": r"Schedule\s*5|Additional Financing Sources",
        "spend": r"Schedule\s*8|Financing Uses by Function",
        "draw": r"Schedule\s*1|All Funds|Fund Balance",
    }.get(metric)
    return BookLocator.get(book).find(value, prefer=prefer)


def thin_child(c: dict) -> dict:
    keys = (
        "type", "book", "page", "value", "query", "label", "unit", "line",
        "fy", "kind", "hit", "unitCode", "formula", "metric",
    )
    return {k: c[k] for k in keys if k in c and c[k] is not None}


def build_citations(analysis: dict) -> dict:
    cites: dict = {}
    actuals = [s for s in analysis["county_totals"] if s["kind"] == "actual"]

    print("  locating county totals…", flush=True)
    for i, s in enumerate(actuals):
        fy = s["fy"]
        book = ACTUAL_BOOK[fy]

        rev_hit = locate_county_metric(book, s["total_revenue"], "revenue")
        exp_hit = locate_county_metric(book, s["expenditures"], "spend")

        if rev_hit:
            cites[f"trend.revenue.{i}"] = {
                "type": "printed",
                "label": f"{fy} actual total revenue",
                "value": s["total_revenue"],
                "formula": (
                    f"Printed governmental-fund total revenue for {fy} in the "
                    f"{book} budget book (Schedule 5 / financing sources)."
                ),
                "book": book,
                "page": rev_hit["page"],
                "query": rev_hit["query"],
                "hit": hit_payload(rev_hit),
                "children": [],
                "fy": fy, "kind": "actual", "metric": "revenue",
            }
        else:
            children = top_unit_total_children(book, fy, "actual", "revenue", n=8)
            cites[f"trend.revenue.{i}"] = {
                "type": "derived",
                "label": f"{fy} actual total revenue",
                "value": s["total_revenue"],
                "formula": (
                    f"Add every department’s printed “Total Revenues” in the {book} book."
                ),
                "book": book,
                "page": None,
                "query": None,
                "hit": None,
                "children": children,
                "fy": fy, "kind": "actual", "metric": "revenue",
            }

        if exp_hit:
            cites[f"trend.spend.{i}"] = {
                "type": "printed",
                "label": f"{fy} actual total spending",
                "value": s["expenditures"],
                "formula": (
                    f"Printed governmental-fund total expenditures for {fy} in the "
                    f"{book} budget book (Schedule 8 / financing uses)."
                ),
                "book": book,
                "page": exp_hit["page"],
                "query": exp_hit["query"],
                "hit": hit_payload(exp_hit),
                "children": [],
                "fy": fy, "kind": "actual", "metric": "spend",
            }
        else:
            children = top_unit_total_children(book, fy, "actual", "spend", n=8)
            cites[f"trend.spend.{i}"] = {
                "type": "derived",
                "label": f"{fy} actual total spending",
                "value": s["expenditures"],
                "formula": (
                    f"Add every department’s printed “Total Expenditures” in the {book} book."
                ),
                "book": book,
                "page": None,
                "query": None,
                "hit": None,
                "children": children,
                "fy": fy, "kind": "actual", "metric": "spend",
            }

        rev_c = cites[f"trend.revenue.{i}"]
        exp_c = cites[f"trend.spend.{i}"]
        # Prefer jumping to a printed schedule page when either side has one
        jump_page = None
        jump_hit = None
        jump_query = None
        if rev_c["type"] == "printed":
            jump_page, jump_hit, jump_query = rev_c["page"], rev_c.get("hit"), rev_c.get("query")
        elif exp_c["type"] == "printed":
            jump_page, jump_hit, jump_query = exp_c["page"], exp_c.get("hit"), exp_c.get("query")
        elif rev_c.get("page"):
            jump_page, jump_hit, jump_query = rev_c["page"], rev_c.get("hit"), rev_c.get("query")
        elif exp_c.get("page"):
            jump_page, jump_hit, jump_query = exp_c["page"], exp_c.get("hit"), exp_c.get("query")

        cites[f"trend.surplus.{i}"] = {
            "type": "derived",
            "label": f"{fy} actual surplus (revenue − spending)",
            "value": s["surplus"],
            "formula": (
                f"{fmt_query(s['total_revenue'])} revenue − "
                f"{fmt_query(s['expenditures'])} spending = "
                f"{fmt_query(s['surplus'])}."
            ),
            "book": book,
            "page": None,
            "query": None,
            "hit": None,
            "children": [thin_child(rev_c), thin_child(exp_c)],
            "fy": fy, "kind": "actual", "metric": "surplus",
        }
        print(f"    {fy}: rev={'p'+str(rev_c['page']) if rev_c.get('page') else 'n/a'} "
              f"({rev_c['type']}) exp={'p'+str(exp_c['page']) if exp_c.get('page') else 'n/a'} "
              f"({exp_c['type']})", flush=True)

    last_i = len(actuals) - 1
    cum = sum(s["surplus"] for s in actuals)
    cites["kpi.lastActualSurplus"] = {
        **cites[f"trend.surplus.{last_i}"],
        "label": "FY 2024-25 actual surplus",
    }
    cites["kpi.lastActualRevenue"] = {
        **cites[f"trend.revenue.{last_i}"],
        "label": "FY 2024-25 actual revenue",
    }
    cites["kpi.lastActualSpend"] = {
        **cites[f"trend.spend.{last_i}"],
        "label": "FY 2024-25 actual spending",
    }
    # Cumulative: jump to latest surplus sources; children are yearly surpluses
    cites["kpi.cumulativeSurplus"] = {
        "type": "derived",
        "label": "Nine-year cumulative surplus",
        "value": cum,
        "formula": (
            "Sum of (revenue − spending) for each closed year "
            "FY 2016-17 through FY 2024-25."
        ),
        "book": cites[f"trend.surplus.{last_i}"].get("book"),
        "page": None,
        "query": None,
        "hit": None,
        "children": [thin_child(cites[f"trend.surplus.{i}"]) for i in range(len(actuals))],
    }

    # Forward budgets
    print("  locating forward budgets…", flush=True)
    snap = next(s for s in analysis["county_totals"]
                if s["fy"] == "FY2025-26" and s["kind"] == "adopted")
    draw_hit = locate_county_metric("FY 2025-26", snap["surplus"], "draw")
    rev_hit = locate_county_metric("FY 2025-26", snap["total_revenue"], "revenue")
    exp_hit = locate_county_metric("FY 2025-26", snap["expenditures"], "spend")
    children = []
    if draw_hit:
        children.append({
            "type": "printed", "book": "FY 2025-26", "page": draw_hit["page"],
            "value": snap["surplus"], "query": draw_hit["query"],
            "hit": hit_payload(draw_hit),
            "label": "Printed fund-balance / surplus figure",
        })
    for hit, val, lab in [
        (rev_hit, snap["total_revenue"], "Adopted total revenue"),
        (exp_hit, snap["expenditures"], "Adopted total spending"),
    ]:
        if hit:
            children.append({
                "type": "printed", "book": "FY 2025-26", "page": hit["page"],
                "value": val, "query": hit["query"], "hit": hit_payload(hit),
                "label": lab,
            })
    # Always try Schedule 1 page for context if we have nothing better
    if not children:
        s1 = BookLocator.get("FY 2025-26").find(
            542601948, prefer=r"Schedule\s*1|All Funds"
        )
        if s1:
            children.append({
                "type": "printed", "book": "FY 2025-26", "page": s1["page"],
                "value": 542601948, "query": s1["query"], "hit": hit_payload(s1),
                "label": "Schedule 1 · Total All Funds (context only; not the $32.9M draw)",
            })
    primary = children[0] if children else None
    cites["kpi.adoptedDraw2526"] = {
        "type": "derived",
        "label": "FY 2025-26 adopted planned fund-balance draw",
        "value": snap["surplus"],
        "formula": (
            f"Adopted revenue {fmt_query(snap['total_revenue'])} − "
            f"adopted spending {fmt_query(snap['expenditures'])} = "
            f"{fmt_query(snap['surplus'])}."
        ),
        "book": "FY 2025-26",
        "page": None,
        "query": None,
        "hit": None,
        "children": children,
    }

    rec = next(s for s in analysis["county_totals"]
               if s["fy"] == "FY2026-27" and s["kind"] == "recommended")
    rec_children = top_unit_total_children("FY 2026-27", "FY2026-27", "recommended", "spend", n=6)
    cites["kpi.recommendedDraw2627"] = {
        "type": "derived",
        "label": "FY 2026-27 recommended planned draw",
        "value": rec["surplus"],
        "formula": (
            f"Recommended revenue {fmt_query(rec['total_revenue'])} − "
            f"recommended spending {fmt_query(rec['expenditures'])} = "
            f"{fmt_query(rec['surplus'])}."
        ),
        "book": "FY 2026-27",
        "page": None,
        "query": None,
        "hit": None,
        "children": rec_children,
    }

    # Department rows — locate glyph on the unit's Schedule 9 page
    print("  locating department rows…", flush=True)
    depts = analysis.get("by_department", {})
    ranked = sorted(depts.items(), key=lambda kv: -kv[1].get("FY2024-25|exp", 0))
    loc2627 = BookLocator.get("FY 2026-27")
    for i, (label, vals) in enumerate(ranked[:20]):
        e24 = vals.get("FY2024-25|exp", 0)
        if e24 < 2e6:
            continue
        m = re.match(r"(.+?)\s*\(([^)]+)\)\s*$", label)
        name, code = (m.group(1), m.group(2)) if m else (label, "")
        book = ACTUAL_BOOK["FY2024-25"]
        children = find_unit_pages(book, "FY2024-25", "actual", code, name)[:6]
        # Attach hits
        located_children = []
        for ch in children:
            hit = loc2627.find(ch["value"], page_hint=ch["page"], near=ch.get("unit") or name)
            located_children.append(apply_hit(ch, hit) if hit else ch)
        primary = next(
            (c for c in located_children if re.search(r"(?i)expend", c.get("line", ""))),
            located_children[0] if located_children else None,
        )
        # Prefer the printed unit-line value (may differ from analysis by a few $)
        primary_hit = None
        if primary:
            if primary.get("hit") and primary["hit"].get("x0") is not None:
                primary_hit = {"page": primary["page"], **primary["hit"],
                               "query": primary.get("query") or primary["hit"].get("query")}
            else:
                primary_hit = loc2627.find(
                    primary["value"], page_hint=primary["page"], near=name
                )
        if not primary_hit:
            primary_hit = loc2627.find(e24, page_hint=primary["page"] if primary else None, near=name)

        cites[f"dept.{i}"] = {
            "type": "printed" if (primary or primary_hit) else "derived",
            "label": f"{name} · FY 2024-25 actual spending",
            "value": e24,
            "formula": (
                f"\"Total Expenditures\" for unit {code or name} "
                f"in the {book} book (FY 2024-25 actual column)."
            ),
            "book": book,
            "page": (primary_hit or {}).get("page") or (primary or {}).get("page"),
            "query": (primary_hit or {}).get("query") or (primary or {}).get("query") or fmt_query(e24),
            "hit": hit_payload(primary_hit) if primary_hit and primary_hit.get("x0") is not None
                   else (primary or {}).get("hit"),
            "children": [thin_child(c) for c in located_children],
            "unit": name,
            "unitCode": code,
            "fy": "FY2024-25",
            "kind": "actual",
        }
        cites[f"unit.{i}"] = cites[f"dept.{i}"]
        print(f"    dept.{i} {name}: p{cites[f'dept.{i}'].get('page')}", flush=True)

    # Function / mix / category / pay / contract cites are built by
    # scripts/rebuild_chart_citations.py so each stack segment lists the
    # units actually tagged to that function — never a reused county-wide list.

    cites["_meta"] = {
        "note": (
            "Printed citations jump to the page and highlight the inked figure. "
            "Derived citations show the formula and open the best printed source "
            "(schedule rollup or a contributing unit row)."
        ),
        "actualBookMap": ACTUAL_BOOK,
    }
    return cites


def write_citations_js(cites: dict):
    def thin(c):
        if not isinstance(c, dict):
            return c
        out = {k: v for k, v in c.items() if k != "children"}
        kids = c.get("children") or []
        out["children"] = [thin_child(kid) if isinstance(kid, dict) else kid for kid in kids]
        return out

    thin_cites = {k: thin(v) if isinstance(v, dict) else v for k, v in cites.items()}
    js = "window.CITATIONS = " + json.dumps(thin_cites, indent=2) + ";\n"
    (DASH / "citations.js").write_text(js)
    print(f"  {len(thin_cites)} citation keys → dashboard/data/citations.js "
          f"({(DASH / 'citations.js').stat().st_size / 1e3:.0f} KB)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--citations-only", action="store_true",
                    help="Skip rebuilding line indexes")
    args = ap.parse_args()

    DASH.mkdir(parents=True, exist_ok=True)
    LINES_DIR.mkdir(parents=True, exist_ok=True)

    print("books.json …")
    write_books_json()

    if not args.citations_only:
        print("line indexes …")
        build_line_indexes()
    else:
        print("line indexes … skipped")

    print("citations …")
    analysis = json.loads((DATA / "analysis.json").read_text())
    try:
        cites = build_citations(analysis)
        write_citations_js(cites)
    finally:
        BookLocator.close_all()
    print("done")


if __name__ == "__main__":
    main()
