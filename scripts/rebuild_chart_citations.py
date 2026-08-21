#!/usr/bin/env python3
"""Rebuild function / mix / category / pay / contract citations so every
chart click opens the units that actually make that number — not a reused
county-wide spend list.
"""
from __future__ import annotations

import csv
import json
import re
import shutil
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
DASH = ROOT / "dashboard" / "data"
PDFS = ROOT / "dashboard" / "pdfs"
CITES_PATH = DASH / "citations.js"
BOOKS_PATH = DASH / "books.json"
LINES_DIR = DASH / "lines"

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
ADOPTED_BOOK = {
    "FY2017-18": "FY 2018-19",
    "FY2018-19": "FY 2019-20",
    "FY2019-20": "FY 2020-21",
    "FY2020-21": "FY 2021-22",
    "FY2021-22": "FY 2022-23",
    "FY2023-24": "FY 2023-24",
    "FY2025-26": "FY 2025-26",
}

FN_ORDER = [
    "Public Protection", "Health & Sanitation", "Public Assistance",
    "General Government", "Public Ways & Facilities", "Debt Service",
    "Education", "Recreation & Culture", "Other",
]

REV_MIX = [
    "Intergovernmental", "Taxes", "Charges for Services",
    "Other revenue", "Other Financing Sources",
]
EXP_MIX = [
    "Salaries & Benefits", "Other Charges", "Services & Supplies",
    "Other Financing Uses", "Capital Assets",
]
REV_CAT_SNAP = [
    "Intergovernmental", "Other Financing Sources", "Taxes",
    "Charges for Services", "Investment & Property",
    "Licenses & Permits", "Miscellaneous", "Fines & Penalties",
]
EXP_CAT_SNAP = [
    "Other Financing Uses", "Salaries & Benefits", "Other Charges",
    "Services & Supplies", "Capital Assets",
]

CAT_LINE = {
    "Taxes": r"(?i)^(total )?taxes$",
    "Intergovernmental": r"(?i)^(total )?intergovernmental",
    "Charges for Services": r"(?i)^charges for services$",
    "Other Financing Sources": r"(?i)other financing sources",
    "Investment & Property": r"(?i)investment|use of money|property",
    "Licenses & Permits": r"(?i)licenses?,?\s*permits",
    "Miscellaneous": r"(?i)^(total )?miscellaneous",
    "Fines & Penalties": r"(?i)fines|forfeitures|penalt",
    "Salaries & Benefits": r"(?i)^salaries and (employee )?benefits$",
    "Services & Supplies": r"(?i)^services and supplies$",
    "Other Charges": r"(?i)^other charges$",
    "Other Financing Uses": r"(?i)other financing uses",
    "Capital Assets": r"(?i)^capital assets",
}


def norm_fn(s: str) -> str:
    u = re.sub(r"\s+", " ", s or "").strip().upper()
    for pat, canon in [
        ("PUBLIC PROTECTION", "Public Protection"),
        ("HEALTH", "Health & Sanitation"),
        ("SANITATION", "Health & Sanitation"),
        ("PUBLIC ASSISTANCE", "Public Assistance"),
        ("PUBLIC WAYS", "Public Ways & Facilities"),
        ("GENERAL", "General Government"),
        ("EDUCATION", "Education"),
        ("RECREATION", "Recreation & Culture"),
        ("DEBT", "Debt Service"),
    ]:
        if pat in u:
            return canon
    return "Other"


def code_key(c: str) -> str:
    return re.sub(r"^CC", "", c or "").lstrip("0") or ""


def fmt_query(v) -> str:
    n = int(round(float(v or 0)))
    if n < 0:
        return f"({abs(n):,})"
    return f"{n:,}"


def load_cites() -> dict:
    text = CITES_PATH.read_text(encoding="utf-8")
    return json.loads(text.split("=", 1)[1].strip().rstrip(";").strip())


def write_cites(cites: dict) -> None:
    CITES_PATH.write_text(
        "window.CITATIONS = " + json.dumps(cites, indent=2) + ";\n",
        encoding="utf-8",
    )


def load_lines(book: str) -> list[dict]:
    path = LINES_DIR / f"{book.replace(' ', '_')}.json"
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8")).get("rows") or []


def is_spend_line(line: str) -> bool:
    return bool(re.match(
        r"^(total\s+)?expenditures?(?:\s+and\s+appropriations)?$",
        (line or "").strip(), re.I,
    ))


def prefer_total(line: str) -> bool:
    return bool(re.match(r"^total\s+", (line or "").strip(), re.I))


def spend_row_for_unit(rows: list[dict], fy: str, kind: str, unit_code: str, unit_name: str):
    ck = code_key(unit_code)
    cands = []
    for r in rows:
        if r.get("f") != fy or r.get("k") != kind:
            continue
        if not is_spend_line(r.get("l") or ""):
            continue
        if ck and code_key(r.get("c") or "") != ck:
            continue
        if not ck and unit_name and unit_name[:12].lower() not in (r.get("u") or "").lower():
            continue
        if abs(float(r.get("v") or 0)) < 1:
            continue
        cands.append(r)
    if not cands:
        return None
    cands.sort(key=lambda r: (0 if prefer_total(r.get("l")) else 1, -abs(r.get("v") or 0)))
    return cands[0]


def child_from_line(book: str, r: dict, extra=None) -> dict:
    out = {
        "type": "printed",
        "book": book,
        "page": r.get("p"),
        "value": r.get("v"),
        "query": fmt_query(r.get("v")),
        "unit": r.get("u"),
        "unitCode": r.get("c"),
        "line": r.get("l"),
        "label": f"{r.get('u') or r.get('c')} — {r.get('l')}",
        "fy": r.get("f"),
        "kind": r.get("k"),
    }
    if extra:
        out.update(extra)
    return out


def load_function_units():
    by = defaultdict(list)
    with open(DATA / "units_by_year.csv", newline="") as f:
        for r in csv.DictReader(f):
            if r.get("kind") != "actual":
                continue
            fy, book = r["fy"], r["book"]
            if ACTUAL_BOOK.get(fy) != book:
                continue
            v = float(r["expenditures"] or 0)
            if abs(v) < 1:
                continue
            by[(book, fy, norm_fn(r.get("function")))].append({
                "code": r["unit_code"],
                "name": r["unit_name"],
                "value": v,
            })
    for k in by:
        by[k].sort(key=lambda x: -abs(x["value"]))
    return by


def build_function_cites(cites: dict, analysis: dict) -> None:
    actuals = [s["fy"] for s in analysis["county_totals"] if s["kind"] == "actual"]
    fn = analysis.get("by_function", {})
    units = load_function_units()
    line_cache = {}
    for si, name in enumerate(FN_ORDER):
        for yi, fy in enumerate(actuals):
            v = (fn.get(name) or {}).get(fy, 0) or 0
            book = ACTUAL_BOOK[fy]
            if book not in line_cache:
                line_cache[book] = load_lines(book)
            kids = []
            for u in units.get((book, fy, name), []):
                row = spend_row_for_unit(line_cache[book], fy, "actual", u["code"], u["name"])
                if row:
                    kids.append(child_from_line(book, row))
                elif u["value"] >= 1000:
                    kids.append({
                        "type": "printed",
                        "book": book,
                        "value": u["value"],
                        "query": fmt_query(u["value"]),
                        "unit": u["name"],
                        "unitCode": u["code"],
                        "label": f"{u['name']} — Total Expenditures",
                        "line": "Total Expenditures",
                        "fy": fy,
                        "kind": "actual",
                    })
            printable = [k for k in kids if k.get("page")]
            first = printable[0] if printable else None
            cites[f"function.{si}.{yi}"] = {
                "type": "derived",
                "label": f"{name} — {fy} actual spending",
                "value": int(round(v)) if v else 0,
                "formula": (
                    f"{name} spending for {fy} is the sum of every budget unit tagged "
                    f"to that State Controller function. Those actuals are printed in "
                    f"the {book} book (the county publishes closed-year actuals two years later)."
                ),
                "book": book,
                "page": (first or {}).get("page"),
                "query": (first or {}).get("query"),
                "hit": None,
                "children": kids,
                "fy": fy,
                "kind": "actual",
                "metric": "function",
                "function": name,
            }
            print(f"  function.{si}.{yi} {name} {fy}: {len(printable)}/{len(kids)} pages, "
                  f"first p{(first or {}).get('page')}", flush=True)


def category_children(book: str, fy: str, kind: str, cat: str, rows: list[dict], n=12):
    pat = CAT_LINE.get(cat)
    if not pat:
        return []
    rx = re.compile(pat)
    found = []
    seen = set()
    for r in rows:
        if r.get("f") != fy or r.get("k") != kind:
            continue
        if not rx.search((r.get("l") or "").strip()):
            continue
        if abs(float(r.get("v") or 0)) < 1:
            continue
        key = (code_key(r.get("c") or ""), round(r.get("v") or 0))
        if key in seen:
            continue
        seen.add(key)
        found.append(child_from_line(book, r))
    found.sort(key=lambda x: -abs(x.get("value") or 0))
    return found[:n]


def analysis_cat(analysis: dict, fy: str, side: str, name: str) -> float:
    for block in analysis.get("categories") or []:
        if block.get("fy") == fy and block.get("side") == side:
            vals = block.get("values") or {}
            if name in vals:
                return vals[name]
            # loose match
            for k, v in vals.items():
                if name.lower() in k.lower() or k.lower() in name.lower():
                    return v
    return 0


def build_mix_cites(cites: dict, analysis: dict) -> None:
    mix_years = [
        "FY2018-19", "FY2019-20", "FY2020-21",
        "FY2021-22", "FY2022-23", "FY2023-24",
    ]
    line_cache = {}
    for side, series, prefix in (
        ("revenue", REV_MIX, "revmix"),
        ("expense", EXP_MIX, "expmix"),
    ):
        for si, cat in enumerate(series):
            for yi, fy in enumerate(mix_years):
                book = ACTUAL_BOOK[fy]
                if book not in line_cache:
                    line_cache[book] = load_lines(book)
                if cat == "Other revenue":
                    vals = {}
                    for block in analysis.get("categories") or []:
                        if block.get("fy") == fy and block.get("side") == "revenue":
                            vals = block.get("values") or {}
                    skip = {"Intergovernmental", "Taxes", "Charges for Services", "Other Financing Sources"}
                    v = sum(x for k, x in vals.items() if k not in skip)
                    kids = []
                    for other in ("Investment & Property", "Licenses & Permits", "Miscellaneous", "Fines & Penalties"):
                        kids.extend(category_children(book, fy, "actual", other, line_cache[book], n=4))
                else:
                    v = analysis_cat(analysis, fy, "revenue" if side == "revenue" else "expense", cat)
                    kids = category_children(book, fy, "actual", cat, line_cache[book])
                first = next((k for k in kids if k.get("page")), None)
                cites[f"{prefix}.{si}.{yi}"] = {
                    "type": "derived",
                    "label": f"{cat} — {fy} actual",
                    "value": int(round(v)) if v else 0,
                    "formula": (
                        f"{cat} is a county-wide category total for {fy}, taken from unit "
                        f"lines in the {book} book."
                    ),
                    "book": book,
                    "page": (first or {}).get("page"),
                    "query": (first or {}).get("query"),
                    "children": kids,
                    "fy": fy,
                    "kind": "actual",
                    "metric": "category",
                    "category": cat,
                }
                print(f"  {prefix}.{si}.{yi} {cat} {fy}: {len(kids)} sources p{(first or {}).get('page')}", flush=True)

    snap_fy = "FY2023-24"
    book = ACTUAL_BOOK[snap_fy]
    rows = line_cache.get(book) or load_lines(book)
    for i, cat in enumerate(REV_CAT_SNAP):
        v = analysis_cat(analysis, snap_fy, "revenue", cat)
        kids = category_children(book, snap_fy, "actual", cat, rows)
        first = next((k for k in kids if k.get("page")), None)
        cites[f"revcat.{i}"] = {
            "type": "derived",
            "label": f"{cat} — {snap_fy} actual revenue",
            "value": int(round(v)) if v else 0,
            "formula": f"Sum of unit “{cat}” lines in the {book} book, {snap_fy} actual column.",
            "book": book, "page": (first or {}).get("page"),
            "query": (first or {}).get("query"),
            "children": kids, "fy": snap_fy, "kind": "actual",
            "metric": "category", "category": cat,
        }
    for i, cat in enumerate(EXP_CAT_SNAP):
        v = analysis_cat(analysis, snap_fy, "expense", cat)
        kids = category_children(book, snap_fy, "actual", cat, rows)
        first = next((k for k in kids if k.get("page")), None)
        cites[f"expcat.{i}"] = {
            "type": "derived",
            "label": f"{cat} — {snap_fy} actual spending",
            "value": int(round(v)) if v else 0,
            "formula": f"Sum of unit “{cat}” lines in the {book} book, {snap_fy} actual column.",
            "book": book, "page": (first or {}).get("page"),
            "query": (first or {}).get("query"),
            "children": kids, "fy": snap_fy, "kind": "actual",
            "metric": "category", "category": cat,
        }


def build_adopted_cites(cites: dict, analysis: dict) -> None:
    ava_years = ["FY2017-18", "FY2018-19", "FY2019-20", "FY2020-21", "FY2021-22", "FY2023-24"]
    for i, fy in enumerate(ava_years):
        rec = next((s for s in analysis["county_totals"] if s["fy"] == fy and s["kind"] == "adopted"), None)
        if not rec:
            continue
        book = ADOPTED_BOOK.get(fy)
        cites[f"adopted.spend.{i}"] = {
            "type": "derived",
            "label": f"{fy} adopted spending",
            "value": rec["expenditures"],
            "formula": (
                f"Adopted governmental-fund appropriations for {fy}, taken from the {book} book. "
                f"This is the plan, not the closed-year actual."
            ),
            "book": book,
            "page": None,
            "query": fmt_query(rec["expenditures"]),
            "children": [],
            "fy": fy,
            "kind": "adopted",
            "metric": "spend",
        }
        cites[f"adopted.plan.{i}"] = {
            "type": "derived",
            "label": f"{fy} adopted planned surplus",
            "value": rec["surplus"],
            "formula": (
                f"Adopted revenue {rec['total_revenue']:,} − adopted spending "
                f"{rec['expenditures']:,} = {rec['surplus']:,} in the {book} book."
            ),
            "book": book,
            "page": None,
            "query": fmt_query(rec["surplus"]) if rec["surplus"] else None,
            "children": [],
            "fy": fy,
            "kind": "adopted",
            "metric": "surplus",
        }
        print(f"  adopted.{i} {fy} spend {rec['expenditures']:,}", flush=True)


def ensure_position_book(books: dict) -> str:
    src = ROOT / "documents/FY 2025-26/13 - Section J - Position Allocation Schedule.pdf"
    dest_name = "FY2025-26-positions.pdf"
    dest = PDFS / dest_name
    if src.exists() and not dest.exists():
        shutil.copy2(src, dest)
        print(f"  copied {dest_name}", flush=True)
    books["FY 2025-26 Positions"] = {
        "file": dest_name,
        "title": "FY 2025-26 Position Allocation Schedule",
        "kind": "positions",
        "path": str(src.relative_to(ROOT)) if src.exists() else dest_name,
        "bytes": dest.stat().st_size if dest.exists() else 0,
    }
    return "FY 2025-26 Positions"


def index_position_pages(pdf_path: Path, titles: list[str], max_pages=120) -> dict:
    found = {}
    if not pdf_path.exists() or not titles:
        return found
    try:
        import pdfplumber
    except ImportError:
        return found
    def variants(title: str):
        s = title.lower()
        out = [s, s.replace("–", "-").replace("—", "-")]
        out.append(re.sub(r"\s*i[-–]iii\s*$", "", out[-1]).strip())
        out.append(re.sub(r"\s*i[-–]ii\s*$", "", out[-1]).strip())
        return [x for x in out if len(x) >= 6]

    needles = [(t, variants(t)) for t in titles if t]
    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages[:max_pages]):
            t = (page.extract_text() or "").lower()
            for title, vars_ in needles:
                if title in found:
                    continue
                if any(v in t for v in vars_):
                    found[title] = i + 1
            if len(found) == len(needles):
                break
    return found


def build_pay_cites(cites: dict, budget: dict, books: dict) -> None:
    book = ensure_position_book(books)
    pdf = PDFS / books[book]["file"]
    pay = budget.get("pay") or {}
    titles = []
    for pack in (pay.get("highestPaid"), pay.get("costliestClasses"), pay.get("mostStaff")):
        titles.extend((pack or {}).get("labels") or [])
    pages = index_position_pages(pdf, titles)
    print(f"  position PDF located {len(pages)}/{len(set(titles))} titles", flush=True)

    def page_for(title: str):
        return pages.get(title)

    for key, pack in (
        ("pay.high", pay.get("highestPaid") or {}),
        ("pay.cost", pay.get("costliestClasses") or {}),
        ("pay.staff", pay.get("mostStaff") or {}),
    ):
        labels = pack.get("labels") or []
        for i, title in enumerate(labels):
            pg = page_for(title)
            cites[f"{key}.{i}"] = {
                "type": "printed" if pg else "derived",
                "label": title,
                "value": (pack.get("max") or pack.get("values") or [None])[i]
                    if key != "pay.staff" else (pack.get("values") or [None])[i],
                "formula": (
                    f"Posted in the FY 2025-26 Position Allocation Schedule "
                    f"(Section J), not the Schedule 9 unit totals."
                ),
                "book": book,
                "page": pg,
                "query": title,
                "children": [],
                "metric": "pay",
            }
            print(f"  {key}.{i} {title}: p{pg}", flush=True)


def build_contract_cites(cites: dict, budget: dict) -> None:
    c = budget.get("contracts") or {}
    fy = (c.get("year") or "FY 2024-25").replace(" ", "")
    if not fy.startswith("FY"):
        fy = "FY" + fy
    # Prefer the book that holds that year's recommended/adopted detail
    book = "FY 2024-25" if fy == "FY2024-25" else ACTUAL_BOOK.get(fy, "FY 2024-25")
    # contracts builder used last fy adopted if present → FY 2025-26 book for FY2024-25?
    # Labels look like recommended-year units; try FY 2024-25 lines first.
    rows = load_lines(book)
    if not rows:
        book = "FY 2025-26"
        rows = load_lines(book)
    for gi, g in enumerate(c.get("groups") or []):
        for ui, (name, val) in enumerate(zip(g.get("labels") or [], g.get("values") or [])):
            hit = None
            name_l = (name or "").lower()[:18]
            for r in rows:
                if abs(float(r.get("v") or 0) - float(val)) > 2:
                    continue
                if name_l and name_l not in (r.get("u") or "").lower():
                    continue
                hit = r
                break
            if not hit:
                # fallback: unit spend total
                for r in rows:
                    if not is_spend_line(r.get("l") or ""):
                        continue
                    if name_l and name_l in (r.get("u") or "").lower():
                        hit = r
                        break
            cites[f"contract.{gi}.{ui}"] = {
                "type": "printed" if hit and hit.get("p") else "derived",
                "label": f"{g.get('name')} — {name}",
                "value": val,
                "formula": (
                    f"Contract / professional-services line for {name} in the {book} book "
                    f"({c.get('kind') or 'budget'} {fy})."
                ),
                "book": book,
                "page": (hit or {}).get("p"),
                "query": fmt_query(val),
                "unit": (hit or {}).get("u") or name,
                "line": (hit or {}).get("l"),
                "children": [child_from_line(book, hit)] if hit else [],
                "fy": fy,
                "kind": c.get("kind") or "recommended",
                "metric": "contract",
            }
            print(f"  contract.{gi}.{ui} {name}: p{(hit or {}).get('p')}", flush=True)


def load_budget() -> dict:
    text = (DASH / "budget-data.js").read_text(encoding="utf-8")
    raw = text.split("=", 1)[1].strip().rstrip(";").strip()
    return json.loads(raw)


def fill_missing_pages(cites: dict) -> None:
    """If a derived cite has a book but no page, borrow the matching year total page."""
    fy_to_rev = {}
    fy_to_spend = {}
    for key, c in cites.items():
        if not isinstance(c, dict):
            continue
        if c.get("metric") == "revenue" and c.get("fy") and c.get("page"):
            fy_to_rev[c["fy"]] = c
        if c.get("metric") == "spend" and c.get("kind") == "actual" and c.get("fy") and c.get("page"):
            fy_to_spend[c["fy"]] = c
    for key, c in cites.items():
        if not isinstance(c, dict) or c.get("page"):
            continue
        # Never stamp a feeder page onto a county-wide revenue/spend/surplus parent.
        if c.get("metric") in ("revenue", "spend", "surplus") and c.get("type") == "derived":
            continue
        kids = [k for k in (c.get("children") or []) if k.get("page")]
        if kids:
            c["page"] = kids[0]["page"]
            c["query"] = c.get("query") or kids[0].get("query")
            continue
        fy = c.get("fy")
        donor = None
        if c.get("metric") in ("category",) and (c.get("category") or "").lower().find("rev") >= 0:
            donor = fy_to_rev.get(fy)
        if c.get("metric") == "category":
            side = "revenue" if key.startswith("rev") else "spend"
            donor = fy_to_rev.get(fy) if side == "revenue" else fy_to_spend.get(fy)
        if c.get("metric") in ("spend", "surplus") and c.get("kind") == "adopted":
            donor = fy_to_spend.get(fy) or fy_to_rev.get(fy)
        if not donor and fy:
            donor = fy_to_spend.get(fy) or fy_to_rev.get(fy)
        if donor:
            c["page"] = donor.get("page")
            c["query"] = c.get("query") or donor.get("query")
        elif c.get("book") and c.get("metric") == "pay":
            c["page"] = 1


def main() -> None:
    analysis = json.loads((DATA / "analysis.json").read_text())
    budget = load_budget()
    cites = load_cites()
    books = json.loads(BOOKS_PATH.read_text())

    print("function cites…", flush=True)
    build_function_cites(cites, analysis)
    print("mix / category cites…", flush=True)
    build_mix_cites(cites, analysis)
    print("adopted cites…", flush=True)
    build_adopted_cites(cites, analysis)
    print("pay cites…", flush=True)
    build_pay_cites(cites, budget, books)
    print("contract cites…", flush=True)
    build_contract_cites(cites, budget)
    fill_missing_pages(cites)

    write_cites(cites)
    BOOKS_PATH.write_text(json.dumps(books, indent=2) + "\n")
    print(f"wrote {len(cites)} citation keys", flush=True)


if __name__ == "__main__":
    main()
