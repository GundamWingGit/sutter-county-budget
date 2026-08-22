#!/usr/bin/env python3
"""Add payByYear packs, position-book entries, and year-indexed pay citations."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DASH = ROOT / "dashboard" / "data"
PDFS = ROOT / "dashboard" / "pdfs"
POS_YEARS = ROOT / "data" / "positions_by_year.json"

if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))
from rebuild_chart_citations import (  # noqa: E402
    _pay_title_needles,
    _parse_pay_money,
)

PAY_YEARS = [
    "FY2017-18",
    "FY2018-19",
    "FY2019-20",
    "FY2020-21",
    "FY2021-22",
    "FY2022-23",
    "FY2023-24",
    "FY2024-25",
    "FY2025-26",
]


def short_label(fy: str) -> str:
    return "FY " + fy.replace("FY", "")[2:]


def book_label(fy: str) -> str:
    return fy.replace("FY", "FY ") + " Positions"


def load_js(path: Path) -> dict:
    raw = path.read_text(encoding="utf-8").split("=", 1)[1].strip().rstrip(";").strip()
    return json.loads(raw)


def splice_object(path: Path, new_obj: dict) -> None:
    text = path.read_text(encoding="utf-8").rstrip()
    if not text.endswith("};"):
        raise SystemExit(f"{path} does not end with }};")
    inner = json.dumps(new_obj, indent=2)[1:-1].rstrip()
    text = text[:-2].rstrip()
    path.write_text(text + ",\n" + inner.lstrip("\n") + "\n};\n", encoding="utf-8")


def locate(pdf, title, value, kind, page_hint):
    if value is None or pdf is None:
        return None
    needles = _pay_title_needles(title)
    if not needles:
        needles = [(title or "").lower()[:18]]
    want = float(value)
    n_pages = len(pdf.pages)
    if page_hint:
        lo = max(0, int(page_hint) - 3)
        hi = min(n_pages, int(page_hint) + 2)
        page_range = range(lo, hi)
        tol = 0.06 if kind == "staff" else 1.01
    elif kind == "staff":
        page_range = range(2, min(25, n_pages))
        tol = 0.06
    else:
        page_range = range(15, n_pages)
        tol = 1.01
    best = None
    for idx in page_range:
        page = pdf.pages[idx]
        text = (page.extract_text() or "").lower()
        if needles and not any(n in text for n in needles if n):
            continue
        for w in page.extract_words() or []:
            n = _parse_pay_money(w.get("text"))
            if n is None or abs(n - want) > tol:
                continue
            score = 4 if "," in str(w.get("text")) else 2
            if any(n in str(w.get("text", "")).lower() for n in needles):
                score += 1
            cand = {
                "page": idx + 1,
                "x0": round(float(w["x0"]), 2),
                "top": round(float(w["top"]), 2),
                "x1": round(float(w["x1"]), 2),
                "bottom": round(float(w["bottom"]), 2),
                "query": w["text"],
                "pageW": float(page.width),
                "pageH": float(page.height),
                "_score": score,
            }
            if best is None or cand["_score"] > best["_score"]:
                best = cand
    if not best:
        return None
    best.pop("_score", None)
    return best


def job_for(pack: dict, title: str) -> dict:
    for j in pack.get("jobs") or []:
        if j.get("title") == title:
            return j
    return {}


def slim_pack(pack: dict) -> dict:
    return {
        "year": pack["year"],
        "salaryKind": pack["salaryKind"],
        "totalFte": pack["totalFte"],
        "classifications": pack["classifications"],
        "highestPaid": pack["highestPaid"],
        "costliestClasses": pack["costliestClasses"],
        "mostStaff": pack["mostStaff"],
        "jobs": pack.get("jobs") or [],
    }


def make_cite(kind, title, value, book, year, fy, pack, hit, page_hint, extra=None):
    job = job_for(pack, title)
    formula = {
        "high": (
            f"Posted top of range in the {year} "
            f"{'salary resolution' if pack.get('salaryKind') == 'resolution' else 'step table'} "
            f"(Section J), not the FTE roster."
        ),
        "cost": (
            f"{job.get('fte') or 0:g} authorized FTE × midpoint of "
            f"{job.get('min') or 0:,.0f}–{job.get('max') or 0:,.0f} "
            f"= {value:,.0f} in the {year} position book. Not a printed payroll total."
        ),
        "staff": (
            f"Authorized FTE for this classification in the {year} "
            f"Position Allocation Schedule."
        ),
    }[kind]
    page = (hit or {}).get("page") or page_hint
    return {
        "type": "printed" if hit else "derived",
        "label": title,
        "value": value,
        "formula": formula,
        "book": book,
        "page": page if kind != "cost" or hit else page_hint,
        "query": (hit or {}).get("query"),
        "hit": ({k: v for k, v in hit.items() if k != "printed"} if hit else None),
        "children": [],
        "metric": "pay" if kind != "staff" else "fte",
        "unit": title,
        "min": job.get("min"),
        "max": job.get("max"),
        "fte": job.get("fte") if job.get("fte") is not None else (value if kind == "staff" else None),
        "estMid": job.get("estMid"),
        "estMax": job.get("estMax"),
        "units": job.get("units") or [],
        "fy": fy,
        "year": year,
        "kind": "positions",
        **(extra or {}),
    }


def main() -> None:
    payload = json.loads(POS_YEARS.read_text())
    packs = payload["packs"]
    budget = load_js(DASH / "budget-data.js")
    if "payByYear" in budget:
        # replace in-file object by rewriting just that key via full dump of the new block
        pass

    by_year = {fy: slim_pack(packs[fy]) for fy in PAY_YEARS if fy in packs}
    pay_by_year = {
        "years": [fy for fy in PAY_YEARS if fy in by_year],
        "labels": [short_label(fy) for fy in PAY_YEARS if fy in by_year],
        "byYear": by_year,
    }

    # Inject / replace payByYear without rewriting the rest of budget-data.js
    text = (DASH / "budget-data.js").read_text(encoding="utf-8")
    if '"payByYear"' in text:
        data = load_js(DASH / "budget-data.js")
        data["payByYear"] = pay_by_year
        header = text.split("window.BUDGET_DATA", 1)[0]
        (DASH / "budget-data.js").write_text(
            header + "window.BUDGET_DATA = " + json.dumps(data, indent=2) + ";\n",
            encoding="utf-8",
        )
    else:
        splice_object(DASH / "budget-data.js", {"payByYear": pay_by_year})
    print("wrote payByYear", list(by_year))

    books = json.loads((DASH / "books.json").read_text())
    sources = {m["year"].replace(" ", ""): m.get("source") for m in payload["meta"].values()}
    for fy in PAY_YEARS:
        label = book_label(fy)
        dest_name = f"{fy}-positions.pdf"
        dest = PDFS / dest_name
        src = ROOT / sources.get(fy, "")
        books[label] = {
            "file": dest_name,
            "title": f"{fy.replace('FY', 'FY ')} Position Allocation Schedule",
            "kind": "positions",
            "path": str(src.relative_to(ROOT)) if src and src.exists() else dest_name,
            "bytes": dest.stat().st_size if dest.exists() else 0,
        }
    (DASH / "books.json").write_text(json.dumps(books, indent=2) + "\n", encoding="utf-8")
    print("updated books.json")

    try:
        import pdfplumber
    except ImportError:
        pdfplumber = None

    cites = {}
    existing = load_js(DASH / "citations.js")
    for yi, fy in enumerate(pay_by_year["years"]):
        pack = by_year[fy]
        book = book_label(fy)
        year = pack["year"]
        pdf_path = PDFS / books[book]["file"]
        pdf = pdfplumber.open(pdf_path) if pdfplumber and pdf_path.exists() else None
        print(f"\n=== {year} ===", flush=True)

        hp = pack["highestPaid"]
        for i, title in enumerate(hp.get("labels") or []):
            value = (hp.get("max") or [None])[i]
            hint = (hp.get("page") or [None])[i]
            # Reuse the already-boxed FY 2025-26 resolution cites when present
            copied = existing.get(f"pay.high.{i}") if fy == "FY2025-26" else None
            hit = None
            if copied and copied.get("hit") and abs(float(copied.get("value") or 0) - float(value or 0)) <= 1:
                hit = copied["hit"]
            else:
                hit = locate(pdf, title, value, "salary", hint)
            cites[f"pay.high.{yi}.{i}"] = make_cite(
                "high", title, value, book, year, fy, pack, hit, hint
            )
            print(f"  high.{yi}.{i} {title}: p{(hit or {}).get('page') or hint}", flush=True)

        cc = pack["costliestClasses"]
        for i, title in enumerate(cc.get("labels") or []):
            value = (cc.get("values") or [None])[i]
            hint = (cc.get("page") or [None])[i]
            # Do not box a different number than the bar (mid × FTE is derived).
            cites[f"pay.cost.{yi}.{i}"] = make_cite(
                "cost", title, value, book, year, fy, pack, None, hint
            )
            cites[f"pay.cost.{yi}.{i}"]["type"] = "derived"
            print(f"  cost.{yi}.{i} {title}: derived p{hint}", flush=True)

        st = pack["mostStaff"]
        for i, title in enumerate(st.get("labels") or []):
            value = (st.get("values") or [None])[i]
            hint = (st.get("page") or [None])[i]
            copied = existing.get(f"pay.staff.{i}") if fy == "FY2025-26" else None
            hit = None
            children = []
            if copied and abs(float(copied.get("value") or 0) - float(value or 0)) <= 0.06:
                children = copied.get("children") or []
                hit = copied.get("hit")
            else:
                hit = locate(pdf, title, value, "staff", hint)
            rec = make_cite("staff", title, value, book, year, fy, pack, hit, hint)
            rec["children"] = children
            rec["metric"] = "fte"
            cites[f"pay.staff.{yi}.{i}"] = rec
            print(f"  staff.{yi}.{i} {title}: p{(hit or {}).get('page') or hint}", flush=True)

        if pdf:
            pdf.close()

    splice_object(DASH / "citations.js", cites)
    print(f"\nappended {len(cites)} pay year cites")


if __name__ == "__main__":
    main()
