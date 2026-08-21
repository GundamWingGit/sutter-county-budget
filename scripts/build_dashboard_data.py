#!/usr/bin/env python3
"""
LEGACY aggregator. Do not run this to refresh the public dashboard.

The site now reads validated governmental-fund actuals from data/analysis.json
(see dashboard/data/budget-data.js). This script mixed books incorrectly and
produced broken FY 2024-25 / FY 2025-26 totals (tens of millions instead of
~$400M). Kept only as a reference for pay/contract object grouping.

To regenerate the dashboard data file, do not use this script.
"""

import csv
import json
import re
from collections import defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CSV_PATH = ROOT / "data" / "unit_lines.csv"
POS_PATH = ROOT / "data" / "positions.json"
OUT_PATH = ROOT / "dashboard" / "data" / "budget-data.js"

# Kind preference when we want "what actually happened" for a fiscal year.
TREND_KIND_PRIORITY = ["actual", "actual_estimated", "adjusted", "adopted", "recommended", "requested"]
# Kind preference for the latest-year "budget plan" snapshot.
SNAPSHOT_KIND_PRIORITY = ["adopted", "recommended", "requested", "adjusted", "actual_estimated", "actual"]

EXPENSE_CATEGORIES = {
    "total salaries and employee benefit": "Salaries & Benefits",
    "total services and supplies": "Services & Supplies",
    "total other charges": "Other Charges",
    "total capital assets": "Capital Assets",
    "total intrafund transfers": "Intrafund Transfers",
    "total other financing uses": "Other Financing Uses",
    "total increases in reserves": "Reserves & Contingencies",
    "total provisions for contingencies": "Reserves & Contingencies",
}

REVENUE_CATEGORIES = {
    "total taxes": "Taxes",
    "total intergovernmental revenues": "Intergovernmental",
    "total charges for services": "Charges for Services",
    "total revenue use money property": "Use of Money & Property",
    "total licenses, permits, franchises": "Licenses & Permits",
    "total fines, forfeitures, penalties": "Fines & Penalties",
    "total miscellaneous revenues": "Miscellaneous",
    "total other financing sources": "Other Financing Sources",
    "total cancellation of obligated fb": "Fund Balance Cancellations",
    "total residual equity transfer in": "Equity Transfers",
}


def clean_function(raw):
    """Normalize function names across book eras.

    Older books: 'PUBLIC PROTECTION F' (trailing letter is a page artifact).
    Newer books: 'PUBLIC PROTECTION FUNCTION ...'.
    """
    name = raw.strip().upper()
    name = re.sub(r"\s+[A-Z]$", "", name)          # stray single letter
    name = re.sub(r"\s+FUNCTION\b.*$", "", name)   # trailing 'FUNCTION ...'
    name = re.sub(r"\s+", " ", name).strip()
    if not name or name in ("N/A", "B", "NA"):
        return "Other / Unclassified"
    aliases = {
        "GENERAL": "General Government",
        "GENERAL GOVERNMENT": "General Government",
        "RECREATION & CULTURAL SERVICES": "Recreation & Culture",
        "RECREATION AND CULTURE": "Recreation & Culture",
        "RECREATION AND CULTURAL SERVICES": "Recreation & Culture",
    }
    if name in aliases:
        return aliases[name]
    pretty = name.title().replace("And", "and").replace("&Amp;", "&")
    return pretty


def clean_fy(raw):
    """'FY2016-17' -> 'FY 2016-17'."""
    return re.sub(r"^FY\s*", "FY ", raw.strip())


def fy_sort_key(fy):
    m = re.search(r"(\d{4})", fy)
    return int(m.group(1)) if m else 0


def match_category(line_name, table):
    ln = line_name.strip().lower()
    for prefix, label in table.items():
        if ln.startswith(prefix):
            return label
    return None


def money_label(v):
    if v >= 1e6:
        return f"${v / 1e6:.1f} million"
    if v >= 1e3:
        return f"${v:,.0f}"
    return f"${v:,.0f}"


def build_pay_and_notable():
    """Job-level pay plus the line items that explain where cash actually goes."""
    pay = {
        "year": None,
        "totalFte": None,
        "classifications": None,
        "highestPaid": {"labels": [], "min": [], "max": []},
        "costliestClasses": {"labels": [], "values": [], "fte": [], "max": []},
        "mostStaff": {"labels": [], "values": []},
    }
    notable = []
    contracts = {"year": None, "groups": []}

    if POS_PATH.exists():
        pos = json.loads(POS_PATH.read_text())
        pay["year"] = pos.get("year")
        pay["totalFte"] = pos.get("totalFte")
        pay["classifications"] = pos.get("classifications")
        top_pay = pos["salaries"][:14]
        pay["highestPaid"] = {
            "labels": [s["title"] for s in top_pay],
            "min": [round(s["min"]) for s in top_pay],
            "max": [round(s["max"]) for s in top_pay],
            "fte": [s.get("fte") or 0 for s in top_pay],
            "estMid": [s.get("estPayrollMid") or 0 for s in top_pay],
            "estMax": [s.get("estPayrollMax") or 0 for s in top_pay],
            "units": [s.get("units") or [] for s in top_pay],
        }
        top_cost = pos["byClass"][:12]
        pay["costliestClasses"] = {
            "labels": [r["title"] for r in top_cost],
            "values": [r["estPayrollMid"] for r in top_cost],
            "fte": [r["fte"] for r in top_cost],
            "max": [round(r["max"]) for r in top_cost],
        }
        by_fte = sorted(pos["byClass"], key=lambda r: -r["fte"])[:12]
        pay["mostStaff"] = {
            "labels": [r["title"] for r in by_fte],
            "values": [r["fte"] for r in by_fte],
        }
        # Full pinpoint table: jobs with both a posted range and authorized seats
        jobs = sorted(
            [r for r in pos["byClass"] if r["fte"] > 0],
            key=lambda r: -r["estPayrollMid"],
        )[:24]
        pay["jobs"] = [
            {
                "title": r["title"],
                "fte": r["fte"],
                "min": round(r["min"]),
                "max": round(r["max"]),
                "estMid": r["estPayrollMid"],
                "estMax": r["estPayrollMax"],
                "units": r.get("units") or [],
            }
            for r in jobs
        ]
        if pos["salaries"]:
            top = pos["salaries"][0]
            notable.append({
                "label": "Highest posted salary range",
                "value": f"${top['max']:,.0f}",
                "detail": f"{top['title']}: {top.get('fte') or 0:.1f} authorized positions, "
                          f"range ${top['min']:,.0f}–${top['max']:,.0f}. "
                          f"Same ceiling applies to Psychiatrist. The County Administrative Officer "
                          f"is a flat ${next((s['max'] for s in pos['salaries'] if s['title']=='County Administrative Officer'), 0):,.0f}.",
            })
        if pos["byClass"]:
            c = pos["byClass"][0]
            notable.append({
                "label": "Largest payroll class",
                "value": money_label(c["estPayrollMid"]),
                "detail": f"{c['fte']:.0f} {c['title']} positions. Mid-range estimate "
                          f"${c['estPayrollMid']:,.0f}; if every seat sat at the top of the range, "
                          f"${c['estPayrollMax']:,.0f}. These are eligibility workers, not executives.",
            })

    # Detailed object lines live in the older-format books (through FY 2024-25).
    detail_csv = ROOT / "data" / "unit_lines" / "FY_2024-25.csv"
    if not detail_csv.exists():
        detail_csv = CSV_PATH
    groups_spec = [
        ("Professional / specialized services", ["professional/specialized", "professional services"]),
        ("Support and care of persons", ["support & care of persons", "support and care of persons"]),
        ("Overtime", ["overtime"]),
        ("Liability / workers' compensation ISF", ["liability insurance isf", "workers' comp insurance isf", "workers comp insurance isf"]),
    ]
    by_group_unit = defaultdict(lambda: defaultdict(float))
    by_group_total = defaultdict(float)
    fy_used = None
    kind_used = None
    rows_detail = []
    with open(detail_csv) as f:
        rows_detail = list(csv.DictReader(f))
    fys_present = sorted({r["fy"] for r in rows_detail})
    pick_fy = fys_present[-1] if fys_present else None
    kinds_for_fy = {r["kind"] for r in rows_detail if r["fy"] == pick_fy}
    pick_kind = "adopted" if "adopted" in kinds_for_fy else "recommended"
    for row in rows_detail:
        if row["kind"] != pick_kind or row["fy"] != pick_fy:
            continue
        ln = row["line_name"].strip().lower()
        try:
            v = float(row["value"])
        except (TypeError, ValueError):
            continue
        if v <= 0:
            continue
        for gname, keys in groups_spec:
            if any(k in ln for k in keys):
                by_group_unit[gname][row["unit_name"].title()] += v
                by_group_total[gname] += v
                fy_used = row["fy"]
                kind_used = pick_kind

    contracts["year"] = fy_used.replace("FY", "FY ") if fy_used else None
    contracts["kind"] = kind_used
    for gname, _ in groups_spec:
        items = sorted(by_group_unit[gname].items(), key=lambda kv: -kv[1])[:8]
        if not items:
            continue
        contracts["groups"].append({
            "name": gname,
            "total": round(by_group_total[gname]),
            "labels": [n for n, _ in items],
            "values": [round(v) for _, v in items],
        })

    if by_group_total.get("Professional / specialized services"):
        t = by_group_total["Professional / specialized services"]
        top = max(by_group_unit["Professional / specialized services"].items(), key=lambda kv: kv[1])
        notable.append({
            "label": "Outside professional services",
            "value": money_label(t),
            "detail": f"Countywide professional/specialized services in the FY 2024-25 recommended "
                      f"book. Largest user: {top[0]} at ${top[1]:,.0f} — contractors sitting next "
                      f"to a $140 million in-house payroll.",
        })
    if by_group_total.get("Support and care of persons"):
        t = by_group_total["Support and care of persons"]
        notable.append({
            "label": "Cash aid and care of persons",
            "value": money_label(t),
            "detail": "Mostly TANF family grants, adoption assistance, behavioral-health placements, "
                      "and the bi-county juvenile hall. This is pass-through aid, not county salaries — "
                      "but it is still county-budgeted spending.",
        })
    prof = by_group_unit.get("Professional / specialized services", {})
    jail = next((v for n, v in prof.items() if "jail medical" in n.lower()), 0)
    if jail:
        notable.append({
            "label": "Jail medical contract",
            "value": money_label(jail),
            "detail": "Jail medical services are largely contracted, not staffed as county employees. "
                      "That is a single program costing more than most entire departments.",
        })
    ot = by_group_unit.get("Overtime", {})
    if ot:
        sheriff = sum(v for n, v in ot.items() if "sheriff" in n.lower() or "jail" in n.lower())
        if sheriff:
            notable.append({
                "label": "Sheriff / jail overtime",
                "value": money_label(sheriff),
                "detail": "Budgeted overtime in Sheriff-Coroner and County Jail alone. "
                          "That is extra pay on top of the posted salary ranges for deputies "
                          "and correctional officers.",
            })

    return pay, notable, contracts


def main():
    if not CSV_PATH.exists():
        raise SystemExit(f"CSV not found: {CSV_PATH}")

    # (book, fy, kind) -> {unit_code: value}
    unit_exp = defaultdict(dict)
    unit_rev = defaultdict(dict)
    # (book, fy, kind) -> {category: {unit_code: value}}  (per-unit to dedupe repeated subtotals)
    exp_cat = defaultdict(lambda: defaultdict(dict))
    rev_cat = defaultdict(lambda: defaultdict(dict))
    # (book, fy, kind) -> {(dimension_value): {unit_code: value}}
    by_function = defaultdict(lambda: defaultdict(dict))
    by_fund = defaultdict(lambda: defaultdict(dict))
    unit_names = {}
    books = set()
    rows = 0

    with open(CSV_PATH, newline="") as f:
        for row in csv.DictReader(f):
            rows += 1
            ln = row["line_name"].strip().lower()
            if not ln.startswith("total"):
                continue
            try:
                value = float(row["value"])
            except (ValueError, TypeError):
                continue
            book = row["book"].strip()
            fy = clean_fy(row["fy"])
            kind = row["kind"].strip()
            unit = row["unit_code"].strip() or row["unit_name"].strip()
            key = (book, fy, kind)
            books.add(book)
            unit_names[unit] = row["unit_name"].strip().title()

            if ln.startswith("total expenditures"):
                unit_exp[key][unit] = value
                by_function[key][clean_function(row["function"])][unit] = value
                by_fund[key][row["fund"].strip()][unit] = value
            elif ln.startswith("total revenues"):
                unit_rev[key][unit] = value
            else:
                cat = match_category(row["line_name"], EXPENSE_CATEGORIES)
                if cat:
                    exp_cat[key][cat][unit] = value
                else:
                    cat = match_category(row["line_name"], REVENUE_CATEGORIES)
                    if cat:
                        rev_cat[key][cat][unit] = value

    if not unit_exp:
        raise SystemExit("No usable subtotal rows found in CSV (is extraction still early?)")

    # For each (fy, kind), pick the single best source book: most units covered, ties -> latest book.
    def best_book(fy, kind, store):
        candidates = [(len(units), book) for (book, f, k), units in store.items() if f == fy and k == kind]
        if not candidates:
            return None
        return max(candidates)[1]

    # Exclude fiscal years whose extraction looks partial (extraction may still be
    # running): require unit coverage of at least 40% of the best-covered year.
    coverage = {}
    for (_, fy, _), units in unit_exp.items():
        coverage[fy] = max(coverage.get(fy, 0), len(units))
    max_cov = max(coverage.values())
    all_fys = sorted(
        (fy for fy, cov in coverage.items() if cov >= 0.4 * max_cov),
        key=fy_sort_key,
    )
    skipped = sorted(set(coverage) - set(all_fys), key=fy_sort_key)
    if skipped:
        print(f"  (skipping low-coverage years, likely mid-extraction: {', '.join(skipped)})")

    def pick(fy, priority, store):
        """Return (kind, book) for the best available kind of a fiscal year."""
        for kind in priority:
            book = best_book(fy, kind, store)
            if book:
                return kind, book
        return None, None

    # ---- Spending / revenue trend across all fiscal years ----
    trend = {"years": [], "spend": [], "revenue": [], "kindUsed": []}
    for fy in all_fys:
        kind, book = pick(fy, TREND_KIND_PRIORITY, unit_exp)
        if not kind:
            continue
        key = (book, fy, kind)
        trend["years"].append(fy)
        trend["spend"].append(round(sum(unit_exp[key].values())))
        rev_units = unit_rev.get(key, {})
        trend["revenue"].append(round(sum(rev_units.values())) if rev_units else None)
        trend["kindUsed"].append(kind)

    # ---- Latest-year snapshot ----
    snap_fy = all_fys[-1]
    snap_kind, snap_book = pick(snap_fy, SNAPSHOT_KIND_PRIORITY, unit_exp)
    snap_key = (snap_book, snap_fy, snap_kind)

    def top_dim(store, key, n):
        agg = {dim: sum(units.values()) for dim, units in store.get(key, {}).items()}
        items = sorted(agg.items(), key=lambda kv: -kv[1])
        items = [(d, v) for d, v in items if v > 0][:n]
        return [d for d, _ in items], [round(v) for _, v in items]

    func_labels, func_values = top_dim(by_function, snap_key, 12)

    unit_agg = sorted(unit_exp[snap_key].items(), key=lambda kv: -kv[1])[:14]
    unit_labels = [unit_names.get(u, u) for u, _ in unit_agg]
    unit_values = [round(v) for _, v in unit_agg]

    def latest_key_with_data(store, min_dims=3):
        """Newer books drop some breakdowns; fall back to the latest year that has them."""
        for fy in reversed(all_fys):
            for kind in SNAPSHOT_KIND_PRIORITY:
                book = best_book(fy, kind, store)
                if not book:
                    continue
                key = (book, fy, kind)
                dims = [d for d, units in store[key].items() if d.strip() and sum(units.values()) > 0]
                if len(dims) >= min_dims:
                    return key
        return None

    def snapshot_chart(store, n):
        key = latest_key_with_data(store)
        if not key:
            return {"year": None, "labels": [], "values": []}
        labels, values = top_dim(store, key, n)
        return {"year": key[1], "labels": labels, "values": values}

    funds_chart = snapshot_chart(by_fund, 12)
    exp_cat_chart = snapshot_chart(exp_cat, 10)
    rev_cat_chart = snapshot_chart(rev_cat, 10)

    # ---- Function trend (top 7 functions + Other) ----
    func_trend_years, func_totals_by_year = [], []
    for fy in all_fys:
        kind, book = pick(fy, TREND_KIND_PRIORITY, by_function)
        if not kind:
            continue
        key = (book, fy, kind)
        func_trend_years.append(fy)
        func_totals_by_year.append({dim: sum(units.values()) for dim, units in by_function[key].items()})
    overall = defaultdict(float)
    for totals in func_totals_by_year:
        for dim, v in totals.items():
            overall[dim] += v
    top_funcs = [d for d, _ in sorted(overall.items(), key=lambda kv: -kv[1])[:7]]
    func_trend_series = [
        {"name": fn, "values": [round(t.get(fn, 0)) for t in func_totals_by_year]}
        for fn in top_funcs
    ]
    other_vals = [round(sum(v for d, v in t.items() if d not in top_funcs)) for t in func_totals_by_year]
    if any(other_vals):
        func_trend_series.append({"name": "All Other", "values": other_vals})

    # ---- Adopted vs Actual by fiscal year ----
    ava = {"years": [], "adopted": [], "actual": []}
    for fy in all_fys:
        ab = best_book(fy, "adopted", unit_exp)
        cb = best_book(fy, "actual", unit_exp)
        if ab and cb:
            ava["years"].append(fy)
            ava["adopted"].append(round(sum(unit_exp[(ab, fy, "adopted")].values())))
            ava["actual"].append(round(sum(unit_exp[(cb, fy, "actual")].values())))

    total_budget = round(sum(unit_exp[snap_key].values()))
    total_revenue = round(sum(unit_rev.get(snap_key, {}).values()))

    pay, notable, contracts = build_pay_and_notable()

    data = {
        "meta": {
            "county": "Sutter County",
            "title": "County Budget Analysis",
            "generatedAt": date.today().isoformat(),
            "sourceRows": rows,
            "books": sorted(books),
            "snapshotYear": snap_fy,
            "snapshotKind": snap_kind,
            "snapshotBook": snap_book,
            "isSampleData": False,
        },
        "kpis": {
            "totalBudget": total_budget,
            "totalRevenue": total_revenue,
            "budgetUnits": len(unit_exp[snap_key]),
            "fiscalYearsCovered": len(all_fys),
            "totalFte": pay.get("totalFte"),
            "classifications": pay.get("classifications"),
        },
        "spendTrend": trend,
        "byFunction": {"year": snap_fy, "labels": func_labels, "values": func_values},
        "functionTrend": {"years": func_trend_years, "series": func_trend_series},
        "topUnits": {"year": snap_fy, "labels": unit_labels, "values": unit_values},
        "topFunds": funds_chart,
        "expenseCategories": exp_cat_chart,
        "revenueCategories": rev_cat_chart,
        "adoptedVsActual": ava,
        "pay": pay,
        "notable": notable,
        "contracts": contracts,
    }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(
        "// Generated by scripts/build_dashboard_data.py — do not edit by hand.\n"
        "// Re-run:  python3 scripts/build_dashboard_data.py\n"
        "window.BUDGET_DATA = " + json.dumps(data, indent=2) + ";\n"
    )
    print(f"Wrote {OUT_PATH}")
    print(f"  rows scanned:   {rows:,}")
    print(f"  fiscal years:   {', '.join(all_fys)}")
    print(f"  snapshot:       {snap_fy} ({snap_kind}, from {snap_book} book)")
    print(f"  total budget:   ${total_budget:,}")
    if pay.get("totalFte"):
        print(f"  authorized FTE: {pay['totalFte']}")
    print(f"  notable items:  {len(notable)}")


if __name__ == "__main__":
    main()
