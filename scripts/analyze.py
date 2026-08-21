#!/usr/bin/env python3
"""Produce analysis tables (JSON) for the dashboard from the validated dataset.

Scope: governmental funds. Actuals for FY X come from the FY X+2 budget book.
FY 2026-27-book data is filtered to exclude internal service funds & districts.
"""
import csv
import json
import re
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

# actual for fy -> which book to trust (fy+2 book)
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
ADOPTED_BOOK = {  # adopted budget for fy -> book containing it
    "FY2017-18": "FY 2018-19",
    "FY2018-19": "FY 2019-20",
    "FY2019-20": "FY 2020-21",
    "FY2020-21": "FY 2021-22",
    "FY2021-22": "FY 2022-23",
    "FY2023-24": "FY 2023-24",
    "FY2025-26": "FY 2025-26",
}

NON_GOV = re.compile(r"ISF|Internal Service|County Service Area|Rio Ramaza|Street Light|"
                     r"Water Agency|Live Oak Canal|Wellness", re.I)

def norm_fn(s):
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


def is_gov(book, fund, unit_name):
    if book != "FY 2026-27":
        return True
    return not (NON_GOV.search(fund or "") or NON_GOV.search(unit_name or ""))


def main():
    out = {}

    # ---------- county totals: actuals + adopted ----------
    tot = defaultdict(lambda: defaultdict(float))
    units = []
    with open(DATA / "units_by_year.csv") as f:
        for r in csv.DictReader(f):
            r["revenues"] = float(r["revenues"]); r["expenditures"] = float(r["expenditures"])
            r["net_cost"] = float(r["net_cost"])
            units.append(r)
            if not is_gov(r["book"], r["fund"], r["unit_name"]):
                continue
            key = None
            if r["kind"] == "actual" and ACTUAL_BOOK.get(r["fy"]) == r["book"]:
                key = (r["fy"], "actual")
            elif r["kind"] == "adopted" and ADOPTED_BOOK.get(r["fy"]) == r["book"]:
                key = (r["fy"], "adopted")
            elif r["kind"] == "recommended" and r["fy"] == "FY2026-27" and r["book"] == "FY 2026-27":
                key = (r["fy"], "recommended")
            if key:
                tot[key]["rev"] += r["revenues"]
                tot[key]["exp"] += r["expenditures"]

    # categories: fund balance cancellation must be netted out of FY26-27 revenue totals
    cats = defaultdict(float)
    with open(DATA / "categories_by_year.csv") as f:
        for r in csv.DictReader(f):
            cats[(r["book"], r["fy"], r["kind"], r["side"], r["category"])] = float(r["value"])

    series = []
    for (fy, kind), d in sorted(tot.items()):
        book = ACTUAL_BOOK.get(fy) if kind == "actual" else (
            ADOPTED_BOOK.get(fy) if kind == "adopted" else "FY 2026-27")
        ofs = cats.get((book, fy, kind, "revenue", "Other Financing Sources"), 0)
        fbc = cats.get((book, fy, kind, "revenue", "Fund Balance Cancellation"), 0)
        transfers_out = cats.get((book, fy, kind, "expense", "Other Financing Uses"), 0)
        rev = d["rev"] - (fbc if book == "FY 2026-27" else 0)  # FY26-27 unit totals include FB cancellation
        series.append({
            "fy": fy, "kind": kind,
            "total_revenue": round(rev),
            "external_revenue": round(rev - ofs),
            "other_financing_sources": round(ofs),
            "expenditures": round(d["exp"]),
            "transfers_out": round(transfers_out),
            "surplus": round(rev - d["exp"]),
            "external_surplus": round((rev - ofs) - (d["exp"] - transfers_out)),
        })
    out["county_totals"] = series

    # ---------- revenue & expense category composition (actuals) ----------
    comp = defaultdict(dict)
    for (book, fy, kind, side, cat), v in cats.items():
        if kind == "actual" and ACTUAL_BOOK.get(fy) == book:
            comp[(fy, side)][cat] = round(v)
    out["categories"] = [{"fy": fy, "side": side, "values": vals}
                         for (fy, side), vals in sorted(comp.items())]

    # ---------- spending by function (actuals) ----------
    by_fn = defaultdict(float)
    for r in units:
        if r["kind"] == "actual" and ACTUAL_BOOK.get(r["fy"]) == r["book"] \
                and is_gov(r["book"], r["fund"], r["unit_name"]):
            by_fn[(r["fy"], norm_fn(r["function"]))] += r["expenditures"]
    fn_out = defaultdict(dict)
    for (fy, fn), v in by_fn.items():
        fn_out[fn][fy] = round(v)
    out["by_function"] = fn_out

    # ---------- top departments (actuals, expenditures + net county cost) ----------
    # unit codes are stable across book formats (era3 prefixes them with "CC")
    def unit_code(r):
        return re.sub(r"^CC", "", r["unit_code"]).lstrip("0") or "0"

    def clean_name(s):
        s = re.sub(r"\s+", " ", s).strip()
        s = re.sub(r"\s*(Schedule 9.*|Fiscal Year.*|County of Sutter.*)$", "", s, flags=re.I)
        return s.title().replace("Mhsa", "MHSA").replace("Igt", "IGT")

    by_unit = defaultdict(lambda: defaultdict(float))
    unit_names = {}   # code -> latest name (later books win)
    for r in units:
        if r["kind"] == "actual" and ACTUAL_BOOK.get(r["fy"]) == r["book"] \
                and is_gov(r["book"], r["fund"], r["unit_name"]):
            code = unit_code(r)
            by_unit[code][r["fy"] + "|exp"] += r["expenditures"]
            by_unit[code][r["fy"] + "|net"] += r["net_cost"]
            by_unit[code][r["fy"] + "|rev"] += r["revenues"]
            unit_names[code] = clean_name(r["unit_name"]) or unit_names.get(code, "")
    # keep units that ever exceed $2M spending
    top = {f"{unit_names[c]} ({c})": d for c, d in by_unit.items()
           if max(v for k, v in d.items() if k.endswith("|exp")) > 2e6}
    out["by_department"] = top

    (DATA / "analysis.json").write_text(json.dumps(out, indent=1))
    print("wrote data/analysis.json")
    # quick printout of headline series
    print(f"\n{'FY':10} {'kind':12} {'ext revenue':>13} {'total rev':>13} {'expenditures':>13} {'surplus':>12}")
    for s in series:
        print(f"{s['fy']:10} {s['kind']:12} {s['external_revenue']:>13,} {s['total_revenue']:>13,} "
              f"{s['expenditures']:>13,} {s['surplus']:>12,}")


if __name__ == "__main__":
    main()
