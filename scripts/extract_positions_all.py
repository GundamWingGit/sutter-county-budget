#!/usr/bin/env python3
"""Extract salary ranges + FTE from every year that has a Position Allocation PDF."""
from __future__ import annotations

import json
import re
import shutil
import sys
from collections import defaultdict
from pathlib import Path

import pdfplumber

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
from extract_positions_salaries import (
    class_rollups,
    extract_salaries,
    join_fte_to_salary,
    money,
)

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "positions_by_year.json"
DASH_PDF = ROOT / "dashboard" / "pdfs"

BOOKS = [
    ("FY2016-17", "FY 2016-17", ROOT / "documents" / "FY 2016-17" / "Position Allocation Schedule.pdf"),
    ("FY2017-18", "FY 2017-18", ROOT / "documents" / "FY 2017-18" / "Position Allocation Schedule.pdf"),
    ("FY2018-19", "FY 2018-19", ROOT / "documents" / "FY 2018-19" / "Position Allocation Schedule.pdf"),
    ("FY2019-20", "FY 2019-20", ROOT / "documents" / "FY 2019-20" / "Position Allocation Schedule.pdf"),
    ("FY2020-21", "FY 2020-21", ROOT / "documents" / "FY 2020-21" / "Position Allocation Schedule.pdf"),
    ("FY2021-22", "FY 2021-22", ROOT / "documents" / "FY 2021-22" / "13 - Section J - Possition Allocation Schedule.pdf"),
    ("FY2022-23", "FY 2022-23", ROOT / "documents" / "FY 2022-23" / "13 - Section J - Position Allocation Schedule.pdf"),
    ("FY2023-24", "FY 2023-24", ROOT / "documents" / "FY 2023-24" / "13 - Section J - Position Allocation Schedule.pdf"),
    ("FY2024-25", "FY 2024-25", ROOT / "documents" / "FY 2024-25" / "13 - Section J - Position Allocation Schedule.pdf"),
    ("FY2025-26", "FY 2025-26", ROOT / "documents" / "FY 2025-26" / "13 - Section J - Position Allocation Schedule.pdf"),
]

NUM = re.compile(r"^-?\d+\.\d{2,3}$")
UNIT_HEAD = re.compile(r"^(\d{4})\s+(.+)$")
SKIP_LINE = re.compile(
    r"^(section |position allocation|county of sutter|fy fy|changes|"
    r"\d{4} total:|.+ total:|countywide|this page left|alpha class|salary resolution)",
    re.I,
)
# Grade is 2–4 letters + 2 digits (GCL29, MGT51) or a 5-letter flat code (ESHSH, PNPEA).
# Title and grade are sometimes concatenated: PSYCHIATRIC NURSE PRACTITIONERPNPEA
GRADE = r"(?:[A-Z]{2,4}\d{2}|[A-Z]{5})"
STEP_NEW = re.compile(
    rf"^(?P<title>.+?)(?P<grade>{GRADE})\s+(?P<step>\d{{1,2}})\s+"
    rf"(?P<hourly>\d+\.\d+)\s+[\d,]+\.\d+\s+[\d,]+\.\d+\s+(?P<annual>[\d,]+\.\d+)",
)
STEP_CONT = re.compile(
    r"^(?P<step>\d{1,2})\s+(?P<hourly>\d+\.\d+)\s+[\d,]+\.\d+\s+[\d,]+\.\d+\s+(?P<annual>[\d,]+\.\d+)",
)
LOOKS_STEP_ROW = re.compile(r"[A-Za-z].+\d+\.\d+\s+[\d,]+\.\d+\s+[\d,]+\.\d+\s+[\d,]+\.\d+")
SALARY_START = re.compile(r"ALPHA CLASS STEP|Salary Resolution|Class Title Minimum", re.I)


def pretty_title(s):
    t = re.sub(r"\s+", " ", (s or "")).strip().title()
    t = re.sub(r"\bAnd\b", "and", t)
    t = re.sub(r"\bOf\b", "of", t)
    for a, b in (
        (r"\bLvn\b", "LVN"),
        (r"\bHhs\b", "HHS"),
        (r"\bCao\b", "CAO"),
        (r"\bHr\b", "HR"),
        (r"\bIii\b", "III"),
        (r"\bIi\b", "II"),
    ):
        t = re.sub(a, b, t)
    return t.replace("&Amp;", "&")


def extract_fte(pdf):
    rows = []
    current_unit = None
    current_section = None
    for i, page in enumerate(pdf.pages, start=1):
        text = page.extract_text() or ""
        if i > 2 and SALARY_START.search(text) and "Position Allocation" not in text[:80]:
            break
        if i < 3:
            continue
        for raw in text.splitlines():
            line = re.sub(r"\s+", " ", raw).strip()
            if not line:
                continue
            if line.lower().startswith("section "):
                current_section = re.sub(r"\s+Position Allocation.*$", "", line, flags=re.I)
                continue
            if SKIP_LINE.match(line) or line.startswith("FY ") or re.match(r"^\d{4}-\d{2} \d{4}-\d{2}$", line):
                continue
            m = UNIT_HEAD.match(line)
            if m and not NUM.search(line.split()[-1] if line.split() else ""):
                current_unit = {"code": m.group(1), "name": m.group(2).title()}
                continue
            words = line.split()
            nums = []
            while words and NUM.match(words[-1]):
                nums.insert(0, float(words.pop()))
            title = " ".join(words).strip()
            if not title or len(nums) < 2 or title.upper().endswith("TOTAL:"):
                continue
            current = nums[-1]
            prior = nums[0]
            change = nums[1] if len(nums) == 3 else current - prior
            rows.append({
                "title": title,
                "unitCode": current_unit["code"] if current_unit else "",
                "unitName": current_unit["name"] if current_unit else "",
                "section": current_section or "",
                "priorFte": prior,
                "changeFte": change,
                "fte": current,
                "page": i,
            })
    return rows


def extract_step_table(pdf):
    classes = {}
    current = None
    started = False
    for i, page in enumerate(pdf.pages, start=1):
        text = page.extract_text() or ""
        if not started:
            if not SALARY_START.search(text):
                continue
            started = True
        if "Position Allocation Schedule" in text and i < 20:
            continue
        for raw in text.splitlines():
            line = re.sub(r"\s+", " ", raw).strip()
            if not line or line.startswith("COUNTY OF SUTTER") or line.startswith("Page:"):
                continue
            if line.startswith("ALPHA CLASS") or line.startswith("EFFECTIVE") or line.startswith("Pay Exempt"):
                continue
            if line.startswith("Position Title"):
                continue
            m = STEP_NEW.match(line)
            if m:
                title = pretty_title(m.group("title"))
                annual = money(m.group("annual"))
                if not title or annual is None:
                    current = None
                    continue
                current = title
                rec = classes.setdefault(title, {
                    "title": title,
                    "min": annual,
                    "max": annual,
                    "grade": m.group("grade"),
                    "flsa": "",
                    "topAnnual": annual,
                    "page": i,
                })
                rec["min"] = min(rec["min"], annual)
                rec["max"] = max(rec["max"], annual)
                rec["topAnnual"] = rec["max"]
                continue
            m = STEP_CONT.match(line)
            if m and current:
                annual = money(m.group("annual"))
                if annual is None:
                    continue
                rec = classes[current]
                rec["min"] = min(rec["min"], annual)
                rec["max"] = max(rec["max"], annual)
                rec["topAnnual"] = rec["max"]
                continue
            # A new title row we failed to parse must not keep feeding steps
            # into the previous class (that is how LVN inherited NP rates).
            if LOOKS_STEP_ROW.search(line) and not re.match(r"^\d{1,2}\s", line):
                current = None
    return list(classes.values())


def finish_salaries(salaries, joined):
    fte_by_salary = defaultdict(float)
    units_by_salary = defaultdict(list)
    for r in joined:
        if not r.get("matched"):
            continue
        fte_by_salary[r["salaryTitle"]] += r["fte"]
        if r["unitName"] and r["unitName"] not in units_by_salary[r["salaryTitle"]]:
            units_by_salary[r["salaryTitle"]].append(r["unitName"])
    from extract_positions_salaries import norm_key, strip_grade
    for s in salaries:
        s["fte"] = 0.0
        s["units"] = []
        if s["title"] in fte_by_salary:
            s["fte"] = round(fte_by_salary[s["title"]], 2)
            s["units"] = units_by_salary[s["title"]][:6]
        else:
            base = strip_grade(norm_key(s["title"]))
            for key, fte in fte_by_salary.items():
                if strip_grade(norm_key(key)) == base:
                    s["fte"] = round(fte, 2)
                    s["units"] = units_by_salary[key][:6]
                    break
        s["estPayrollMid"] = round(((s["min"] + s["max"]) / 2) * s["fte"])
        s["estPayrollMax"] = round(s["max"] * s["fte"])
    return salaries


def extract_year(fy, label, pdf_path):
    pdf = pdfplumber.open(pdf_path)
    fte_rows = extract_fte(pdf)
    text0 = (pdf.pages[min(14, len(pdf.pages) - 1)].extract_text() or "") + (
        pdf.pages[min(15, len(pdf.pages) - 1)].extract_text() or ""
    )
    if "Salary Resolution" in text0 or "Class Title Minimum" in text0:
        salaries = extract_salaries(pdf)
        sal_kind = "resolution"
    else:
        salaries = extract_step_table(pdf)
        sal_kind = "step-table" if salaries else "none"
    pdf.close()
    joined, unmatched = join_fte_to_salary(fte_rows, salaries) if salaries else ([{**r, "matched": False} for r in fte_rows], len(fte_rows))
    if salaries:
        salaries = finish_salaries(salaries, joined)
    rollups = class_rollups(joined) if salaries else []
    total_fte = round(sum(r["fte"] for r in fte_rows), 2)
    return {
        "year": label,
        "fy": fy,
        "source": str(pdf_path.relative_to(ROOT)),
        "salaryKind": sal_kind,
        "classifications": len(salaries),
        "allocationRows": len(fte_rows),
        "totalFte": total_fte,
        "matchedFte": round(sum(r["fte"] for r in joined if r.get("matched")), 2),
        "unmatchedRows": unmatched,
        "salaries": sorted(salaries, key=lambda s: -s["max"]) if salaries else [],
        "byClass": sorted(rollups, key=lambda r: -r["estPayrollMid"]),
        "allocation": fte_rows,
    }


def chart_pack(rec):
    top_pay = rec["salaries"][:14]
    top_cost = rec["byClass"][:12]
    by_fte = sorted(rec["byClass"] or [], key=lambda r: -r["fte"])[:12]
    if not by_fte and rec.get("allocation"):
        # FTE-only year: roll up allocation titles
        fte = defaultdict(float)
        page_of = {}
        for r in rec["allocation"]:
            fte[r["title"]] += r["fte"]
            page_of.setdefault(r["title"], r.get("page"))
        by_fte = [{
            "title": pretty_title(t),
            "fte": v,
            "estPayrollMid": 0,
            "max": 0,
            "min": 0,
            "page": page_of.get(t),
        } for t, v in sorted(fte.items(), key=lambda kv: -kv[1])[:12]]
    jobs = sorted([r for r in rec["byClass"] if r["fte"] > 0], key=lambda r: -r["estPayrollMid"])[:24]
    sal_page = {s["title"]: s.get("page") for s in rec["salaries"]}
    return {
        "year": rec["year"],
        "salaryKind": rec["salaryKind"],
        "totalFte": rec["totalFte"],
        "classifications": rec["classifications"],
        "highestPaid": {
            "labels": [s["title"] for s in top_pay],
            "min": [round(s["min"]) for s in top_pay],
            "max": [round(s["max"]) for s in top_pay],
            "fte": [s.get("fte") or 0 for s in top_pay],
            "page": [s.get("page") for s in top_pay],
        },
        "costliestClasses": {
            "labels": [r["title"] for r in top_cost],
            "values": [r["estPayrollMid"] for r in top_cost],
            "fte": [r["fte"] for r in top_cost],
            "min": [round(r["min"]) for r in top_cost] if top_cost else [],
            "max": [round(r["max"]) for r in top_cost] if top_cost else [],
            "page": [sal_page.get(r["title"]) or r.get("page") for r in top_cost],
        },
        "mostStaff": {
            "labels": [r["title"] for r in by_fte],
            "values": [r["fte"] for r in by_fte],
            "page": [sal_page.get(r["title"]) or r.get("page") for r in by_fte],
        },
        "jobs": [
            {
                "title": r["title"],
                "fte": r["fte"],
                "min": round(r["min"]),
                "max": round(r["max"]),
                "estMid": r["estPayrollMid"],
                "estMax": r["estPayrollMax"],
                "units": r.get("units") or [],
                "page": sal_page.get(r["title"]) or r.get("page"),
            }
            for r in jobs
        ],
    }


def main():
    years = {}
    for fy, label, path in BOOKS:
        if not path.exists():
            print("SKIP missing", path)
            continue
        print(f"\n=== {label} ===")
        rec = extract_year(fy, label, path)
        years[fy] = rec
        dest = DASH_PDF / f"{fy}-positions.pdf"
        if not dest.exists():
            shutil.copy2(path, dest)
            print("  copied", dest.name)
        print(f"  FTE {rec['totalFte']:.1f}  classes {rec['classifications']}  kind {rec['salaryKind']}  unmatched {rec['unmatchedRows']}")
        for s in rec["salaries"][:5]:
            print(f"    ${s['max']:10,.0f}  {s['title']}  FTE {s.get('fte') or 0}")
    slim = {fy: {k: v for k, v in rec.items() if k != "allocation"} for fy, rec in years.items()}
    OUT.write_text(json.dumps({
        "years": list(years),
        "packs": {fy: chart_pack(rec) for fy, rec in years.items()},
        "meta": {fy: {
            "year": rec["year"],
            "salaryKind": rec["salaryKind"],
            "totalFte": rec["totalFte"],
            "classifications": rec["classifications"],
            "source": rec["source"],
        } for fy, rec in years.items()},
    }, indent=2))
    print("\nWrote", OUT)


if __name__ == "__main__":
    main()
