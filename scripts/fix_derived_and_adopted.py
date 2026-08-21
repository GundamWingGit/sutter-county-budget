#!/usr/bin/env python3
"""Stop derived totals from borrowing a feeder page, and locate printed adopted totals."""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DASH = ROOT / "dashboard" / "data"
PDFS = ROOT / "dashboard" / "pdfs"

ADOPTED_BOOK = {
    "FY2017-18": "FY 2018-19",
    "FY2018-19": "FY 2019-20",
    "FY2019-20": "FY 2020-21",
    "FY2020-21": "FY 2021-22",
    "FY2021-22": "FY 2022-23",
    "FY2023-24": "FY 2023-24",
    "FY2025-26": "FY 2025-26",
}


def load_js(name: str) -> dict:
    text = (DASH / name).read_text(encoding="utf-8")
    return json.loads(text.split("=", 1)[1].strip().rstrip(";").strip())


def save_cites(cites: dict) -> None:
    (DASH / "citations.js").write_text(
        "window.CITATIONS = " + json.dumps(cites, indent=2) + ";\n",
        encoding="utf-8",
    )


def parse_money(s) -> float | None:
    t = str(s).strip().replace("−", "-").replace("–", "-")
    if not re.search(r"\d", t):
        return None
    neg = t.startswith("(") or t.startswith("-")
    n = re.sub(r"[^0-9.]", "", t)
    if n.count(".") > 1:
        return None
    try:
        v = float(n)
    except ValueError:
        return None
    return -v if neg else v


def same_dollars(a, b, tol: float = 1) -> bool:
    if a is None or b is None:
        return False
    return abs(float(a) - float(b)) <= tol


def word_hit(page, value: float, tol: float = 1) -> dict | None:
    want = float(value)
    best = None
    for w in page.extract_words() or []:
        n = parse_money(w.get("text"))
        if n is None or abs(n - want) > tol:
            continue
        text = str(w.get("text") or "")
        score = 3 if "," in text else 1
        cand = (score, -abs(w["x1"] - w["x0"]), w)
        if best is None or score > best[0]:
            best = (score, w)
    if not best:
        return None
    w = best[1]
    return {
        "page": page.page_number,
        "x0": round(float(w["x0"]), 2),
        "top": round(float(w["top"]), 2),
        "x1": round(float(w["x1"]), 2),
        "bottom": round(float(w["bottom"]), 2),
        "query": w["text"],
        "pageW": float(page.width),
        "pageH": float(page.height),
    }


def locate_in_book(book: str, books: dict, value: float, prefer_pages: list[int] | None = None) -> dict | None:
    rec = books.get(book)
    if not rec:
        return None
    path = PDFS / rec["file"]
    if not path.exists():
        return None
    import pdfplumber

    with pdfplumber.open(path) as pdf:
        order = []
        if prefer_pages:
            order.extend(p for p in prefer_pages if 1 <= p <= len(pdf.pages))
        # Schedule pages are usually early.
        order.extend(p for p in range(1, min(80, len(pdf.pages) + 1)) if p not in order)
        for pno in order:
            hit = word_hit(pdf.pages[pno - 1], value, tol=2)
            if hit:
                return hit
    return None


def clean_derived(cites: dict) -> int:
    n = 0
    for key, c in cites.items():
        if not isinstance(c, dict) or c.get("type") != "derived":
            continue
        q = parse_money(c.get("query"))
        if c.get("page") and (q is None or not same_dollars(q, c.get("value"), 2)):
            c["page"] = None
            c["hit"] = None
            # Keep a query only if it is the clicked total.
            if q is None or not same_dollars(q, c.get("value"), 2):
                c["query"] = None
            n += 1
    return n


def fix_adopted(cites: dict, budget: dict, books: dict) -> None:
    ava = budget.get("adoptedVsActual") or {}
    spa = budget.get("surplusPlanVsActual") or {}
    years = ava.get("years") or []
    for i, fy in enumerate(years):
        book = ADOPTED_BOOK.get(fy)
        spend = (ava.get("adopted") or [None])[i] if i < len(ava.get("adopted") or []) else None
        planned = (spa.get("planned") or [None])[i] if i < len(spa.get("planned") or []) else None
        if spend is None or not book:
            continue
        hit = locate_in_book(book, books, float(spend), prefer_pages=[8, 9, 18, 19, 20, 34, 35, 36, 41, 42, 43])
        key = f"adopted.spend.{i}"
        if hit:
            cites[key] = {
                "type": "printed",
                "label": f"{fy} adopted spending",
                "value": spend,
                "formula": (
                    f"Adopted governmental-fund appropriations printed in the {book} book. "
                    f"This is the plan, not the closed-year actual."
                ),
                "book": book,
                "page": hit["page"],
                "query": hit["query"],
                "hit": hit,
                "children": [],
                "fy": fy,
                "kind": "adopted",
                "metric": "spend",
            }
            print(f"  {key} printed p{hit['page']} {hit['query']}", flush=True)
        else:
            cites.setdefault(key, {})
            cites[key]["type"] = "derived"
            cites[key]["page"] = None
            cites[key]["query"] = None
            cites[key]["hit"] = None
            print(f"  {key} still derived — no printed total in {book}", flush=True)

        rev_val = None
        if planned is not None:
            rev_val = float(spend) + float(planned)
        spend_cite = cites.get(key) or {}
        rev_hit = locate_in_book(book, books, rev_val, prefer_pages=[8, 9, 18, 19, 20]) if rev_val else None
        children = []
        if rev_hit:
            children.append({
                "type": "printed",
                "book": book,
                "page": rev_hit["page"],
                "value": rev_val,
                "query": rev_hit["query"],
                "label": f"{fy} adopted revenue",
                "hit": rev_hit,
                "metric": "revenue",
            })
        if spend_cite.get("type") == "printed":
            children.append({
                "type": "printed",
                "book": book,
                "page": spend_cite.get("page"),
                "value": spend,
                "query": spend_cite.get("query"),
                "label": f"{fy} adopted spending",
                "hit": spend_cite.get("hit"),
                "metric": "spend",
            })
        cites[f"adopted.plan.{i}"] = {
            "type": "derived",
            "label": f"{fy} adopted planned surplus",
            "value": planned,
            "formula": (
                f"Adopted revenue {int(round(rev_val)):,} − adopted spending "
                f"{int(round(spend)):,} = {int(round(planned or 0)):,}."
                if rev_val is not None else
                "Adopted revenue minus adopted spending."
            ),
            "book": book,
            "page": None,
            "query": None,
            "hit": None,
            "children": children,
            "fy": fy,
            "kind": "adopted",
            "metric": "surplus",
        }


def add_inflation(cites: dict, budget: dict) -> None:
    inf = budget.get("inflation") or {}
    st = budget.get("spendTrend") or {}
    years = st.get("years") or []
    for i, fy in enumerate(years):
        real = (inf.get("real2025") or [None])[i] if i < len(inf.get("real2025") or []) else None
        nom = (st.get("spend") or [None])[i] if i < len(st.get("spend") or []) else None
        if real is None:
            continue
        child = cites.get(f"trend.spend.{i}")
        cites[f"inflation.real.{i}"] = {
            "type": "derived",
            "label": f"{fy} spending in FY 2024-25 dollars",
            "value": real,
            "formula": (
                f"Nominal spending {int(round(nom)):,} adjusted with BLS CPI-U "
                f"to FY 2024-25 dollars. The budget book prints the nominal figure, not this one."
                if nom is not None else
                "CPI-adjusted spending. The budget book prints the nominal figure."
            ),
            "book": (child or {}).get("book"),
            "page": None,
            "query": None,
            "hit": None,
            "children": [child] if child else [],
            "fy": fy,
            "metric": "inflation",
        }
    print(f"  inflation.real.0–{len(years)-1}", flush=True)


def main() -> None:
    cites = load_js("citations.js")
    budget = load_js("budget-data.js")
    books = json.loads((DASH / "books.json").read_text(encoding="utf-8"))
    cleared = clean_derived(cites)
    print(f"cleared misleading pages on {cleared} derived cites", flush=True)
    print("adopted totals…", flush=True)
    fix_adopted(cites, budget, books)
    print("inflation cites…", flush=True)
    add_inflation(cites, budget)
    # KPI draw already has children — just make sure it has no parent page.
    for k in ("kpi.adoptedDraw2526", "kpi.recommendedDraw2627"):
        if k in cites and cites[k].get("type") == "derived":
            cites[k]["page"] = None
            cites[k]["query"] = None
            cites[k]["hit"] = None
    save_cites(cites)
    print("done")


if __name__ == "__main__":
    main()
