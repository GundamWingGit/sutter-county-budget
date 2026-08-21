#!/usr/bin/env python3
"""Aggregate extracted unit lines into analysis-ready tables.

Outputs:
  data/units_by_year.csv      per-unit totals (revenues, expenditures, net cost)
  data/categories_by_year.csv county-wide totals by revenue/expense category
  data/county_totals.csv      county-wide (governmental funds) totals
"""
import csv
import glob
import re
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

# --- category normalization -------------------------------------------------
REV_CATS = {
    "TOTAL TAXES": "Taxes",
    "TAXES": "Taxes",
    "TOTAL LICENSES, PERMITS, FRANCHISES": "Licenses, Permits & Franchises",
    "LICENSES, PERMITS, AND FRANCHISES": "Licenses, Permits & Franchises",
    "TOTAL FINES, FORFEITURES, PENALTIES": "Fines, Forfeitures & Penalties",
    "FINES, FORFEITURES, AND PENALTIES": "Fines, Forfeitures & Penalties",
    "TOTAL REVENUE USE MONEY PROPERTY": "Investment & Property Revenue",
    "REVENUE FROM INVESTMENT AND PROPERTY": "Investment & Property Revenue",
    "TOTAL INTERGOVERNMENTAL REVENUES": "Intergovernmental",
    "INTERGOVERNMENTAL REVENUES": "Intergovernmental",
    "TOTAL CHARGES FOR SERVICES": "Charges for Services",
    "CHARGES FOR SERVICES": "Charges for Services",
    "TOTAL MISCELLANEOUS REVENUES": "Miscellaneous",
    "MISCELLANEOUS REVENUES": "Miscellaneous",
    "SPECIAL BENEFIT ASSESSMENTS": "Special Assessments",
    "TOTAL OTHER FINANCING SOURCES": "Other Financing Sources",
    "TRANSFERS IN (NONRECIPROCAL)": "Other Financing Sources",
    "SALE OF CAPITAL ASSETS": "Other Financing Sources",
    "LONG-TERM DEBT PROCEEDS": "Other Financing Sources",
    "TOTAL RESIDUAL EQUITY TRANSFER IN": "Other Financing Sources",
    "TOTAL CANCELLATION OF OBLIGATED FB": "Fund Balance Cancellation",
    "TOTAL UNDESIGNATED FUND BALANCE": "Fund Balance Cancellation",
    "BUDGETARY REVENUE": "Fund Balance Cancellation",
}
EXP_CATS = {
    "TOTAL SALARIES AND EMPLOYEE BENEFIT": "Salaries & Benefits",
    "TOTAL SALARIES AND EMPLOYEE BENEFITS": "Salaries & Benefits",
    "SALARIES AND EMPLOYEE BENEFITS": "Salaries & Benefits",
    "TOTAL SERVICES AND SUPPLIES": "Services & Supplies",
    "SERVICES AND SUPPLIES": "Services & Supplies",
    "TOTAL OTHER CHARGES": "Other Charges",
    "OTHER CHARGES": "Other Charges",
    "OTHER": "Other Charges",
    "TOTAL CAPITAL ASSETS": "Capital Assets",
    "CAPITAL ASSETS - EXPENDITURES": "Capital Assets",
    "CAPITAL ASSETS EXPENDITURES": "Capital Assets",
    "BUDGETARY EXPENDITURE": "Increases in Reserves",
    "INCREASE IN OBLIGATED F": "Increases in Reserves",
    "TOTAL OTHER FINANCING USES": "Other Financing Uses",
    "TRANSFERS OUT (NONRECIPROCAL)": "Other Financing Uses",
    "TOTAL INTRAFUND TRANSFERS": "Intrafund Transfers",
    "INTRAFUND TRANSFERS": "Intrafund Transfers",
    "TOTAL PROVISIONS FOR CONTINGENCIES": "Contingencies",
    "TOTAL INCREASES IN RESERVES": "Increases in Reserves",
}
# exact-case: title-case rows are the per-unit summary block; uppercase are per-fund-page
# category totals (a different partition of the same money).
UNIT_TOTAL_REV = {"Total Revenues"}
UNIT_TOTAL_EXP = {"Total Expenditures", "Total Expenditures and Appropriations"}
UNIT_NET = {"Unreimbursed Costs", "Net Costs"}


def norm(s):
    return re.sub(r"\s+", " ", s).strip().upper()


def main():
    units = defaultdict(lambda: defaultdict(float))   # (book,fy,kind,unit,uname,fund,function) -> {rev,exp,net}
    cats = defaultdict(float)                          # (book,fy,kind,side,cat) -> value
    era3_books = {"FY 2025-26", "FY 2026-27"}

    for path in sorted(glob.glob(str(DATA / "unit_lines" / "*.csv"))):
        with open(path) as f:
            for row in csv.DictReader(f):
                book, fy, kind = row["book"], row["fy"], row["kind"]
                name = norm(row["line_name"])
                v = float(row["value"])
                unit_key = (book, fy, kind, row["unit_code"], row["unit_name"],
                            row["fund"], row["function"])
                if row["account"]:
                    continue  # line items: kept in unit_lines for drill-down, not needed here
                raw = re.sub(r"\s+", " ", row["line_name"]).strip()
                if book == "FY 2026-27":
                    # only the closing summary block; the section-leading bare
                    # "Revenues"/"Expenditures" totals would double-count
                    if raw == "Net County Cost":
                        raw = "Net Costs"
                if raw in UNIT_TOTAL_REV:
                    units[unit_key]["rev"] += v
                elif raw in UNIT_TOTAL_EXP:
                    units[unit_key]["exp"] += v
                elif raw in UNIT_NET:
                    units[unit_key]["net"] += v
                # category totals: era2 uses "TOTAL X" (uppercase only, exact); era3 uses
                # title-case category rows. Deduped per unit+fund because FY26-27 repeats
                # category subtotals in each unit's closing summary block.
                elif name in REV_CATS:
                    cats[(book, fy, kind, row["unit_code"], row["fund"],
                          "revenue", REV_CATS[name])] = v
                elif name in EXP_CATS:
                    cats[(book, fy, kind, row["unit_code"], row["fund"],
                          "expense", EXP_CATS[name])] = v

    with open(DATA / "units_by_year.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["book", "fy", "kind", "unit_code", "unit_name", "fund", "function",
                    "revenues", "expenditures", "net_cost"])
        for (book, fy, kind, uc, un, fund, fn), d in sorted(units.items()):
            w.writerow([book, fy, kind, uc, un, fund, fn,
                        d.get("rev", 0), d.get("exp", 0), d.get("net", 0)])

    # re-aggregate deduped per-unit categories to county level
    cat_totals = defaultdict(float)
    for (book, fy, kind, unit, fund, side, cat), v in cats.items():
        cat_totals[(book, fy, kind, side, cat)] += v

    with open(DATA / "categories_by_year.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["book", "fy", "kind", "side", "category", "value"])
        for (book, fy, kind, side, cat), v in sorted(cat_totals.items()):
            w.writerow([book, fy, kind, side, cat, round(v, 2)])

    # county totals from categories (external revenue vs expenditures)
    totals = defaultdict(lambda: defaultdict(float))
    for (book, fy, kind, side, cat), v in cat_totals.items():
        t = totals[(book, fy, kind)]
        if side == "revenue" and cat not in ("Other Financing Sources", "Fund Balance Cancellation"):
            t["external_revenue"] += v
        if side == "revenue" and cat == "Other Financing Sources":
            t["other_financing_sources"] += v
        if side == "expense" and cat not in ("Increases in Reserves",):
            t["expenditures"] += v
        if side == "expense" and cat == "Other Financing Uses":
            t["transfers_out"] += v
    with open(DATA / "county_totals.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["book", "fy", "kind", "external_revenue", "other_financing_sources",
                    "expenditures", "transfers_out"])
        for (book, fy, kind), t in sorted(totals.items()):
            w.writerow([book, fy, kind, round(t["external_revenue"], 2),
                        round(t["other_financing_sources"], 2),
                        round(t["expenditures"], 2), round(t["transfers_out"], 2)])
    print("wrote units_by_year.csv, categories_by_year.csv, county_totals.csv")


if __name__ == "__main__":
    main()
