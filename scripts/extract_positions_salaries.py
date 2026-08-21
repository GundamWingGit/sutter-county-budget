#!/usr/bin/env python3
"""Extract job classifications, salary ranges, and FTE counts.

Source: FY 2025-26 Position Allocation Schedule + Salary Resolution
(documents/FY 2025-26/13 - Section J - Position Allocation Schedule.pdf).

Writes data/positions.json used by build_dashboard_data.py.
"""

import json
import re
from collections import defaultdict
from pathlib import Path

import pdfplumber

ROOT = Path(__file__).resolve().parent.parent
PDF = ROOT / "documents" / "FY 2025-26" / "13 - Section J - Position Allocation Schedule.pdf"
OUT = ROOT / "data" / "positions.json"

NUM = re.compile(r"^-?\d+\.\d{2,3}$")
UNIT_HEAD = re.compile(r"^(\d{4})\s+(.+)$")
SKIP_LINE = re.compile(
    r"^(section |position allocation|county of sutter|fy fy|changes|"
    r"\d{4} total:|.+ total:|countywide)",
    re.I,
)


def money(s):
    if not s:
        return None
    t = str(s).replace(",", "").replace("$", "").strip()
    if not t:
        return None
    try:
        return float(t)
    except ValueError:
        return None


def clean_title(s):
    return re.sub(r"\s+", " ", (s or "").replace("\n", " ")).strip()


def extract_salaries(pdf):
    """One row per classification: min, max, top-of-range annual, FLSA, grade."""
    classes = {}
    for page in pdf.pages[15:100]:
        tables = page.extract_tables() or []
        for table in tables:
            for row in table:
                if not row or len(row) < 8:
                    continue
                title = clean_title(row[0])
                if not title or title.lower().startswith("class title"):
                    continue
                lo, hi = money(row[1]), money(row[2])
                if lo is None or hi is None:
                    continue
                grade = (row[3] or "").replace("\n", "").strip()
                step = (row[4] or "").strip()
                annual = money(row[7])
                flsa = (row[8] or "").strip() if len(row) > 8 else ""
                rec = classes.setdefault(
                    title,
                    {
                        "title": title,
                        "min": lo,
                        "max": hi,
                        "grade": grade,
                        "flsa": flsa,
                        "topAnnual": hi,
                    },
                )
                rec["min"] = min(rec["min"], lo)
                rec["max"] = max(rec["max"], hi)
                if annual and (not rec.get("topAnnual") or annual > rec["topAnnual"]):
                    rec["topAnnual"] = annual
                if step in ("08", "09", "10") and annual:
                    rec["topAnnual"] = max(rec.get("topAnnual") or 0, annual)
    return list(classes.values())


def extract_fte(pdf):
    """Position allocation pages 3–14: job title + current-year FTE by unit."""
    rows = []
    current_unit = None
    current_section = None
    for i, page in enumerate(pdf.pages[2:14], start=3):
        text = page.extract_text() or ""
        for raw in text.splitlines():
            line = re.sub(r"\s+", " ", raw).strip()
            if not line:
                continue
            if line.lower().startswith("section "):
                current_section = re.sub(r"\s+Position Allocation.*$", "", line, flags=re.I)
                continue
            if SKIP_LINE.match(line) or line.startswith("FY ") or line in ("2024-25 2025-26", "Changes"):
                continue
            m = UNIT_HEAD.match(line)
            if m and not NUM.search(line.split()[-1] if line.split() else ""):
                current_unit = {"code": m.group(1), "name": m.group(2).title()}
                continue
            # Job line: TITLE  prior [change] current
            parts = line.rsplit(" ", 4)
            nums = []
            words = line.split()
            while words and NUM.match(words[-1]):
                nums.insert(0, float(words.pop()))
            title = " ".join(words).strip()
            if not title or len(nums) < 2:
                continue
            if title.upper().endswith("TOTAL:"):
                continue
            current = nums[-1]
            prior = nums[0]
            change = nums[1] if len(nums) == 3 else current - prior
            rows.append(
                {
                    "title": title,
                    "unitCode": current_unit["code"] if current_unit else "",
                    "unitName": current_unit["name"] if current_unit else "",
                    "section": current_section or "",
                    "priorFte": prior,
                    "changeFte": change,
                    "fte": current,
                    "page": i,
                }
            )
    return rows


ABBREV = (
    ("PUBLIC ASST SPECIALIST", "PUBLIC ASSISTANCE SPECIALIST"),
    ("PUBLIC ASSIST SPECIALIST", "PUBLIC ASSISTANCE SPECIALIST"),
    ("PUBLIC ASSISTANCE SPECIALIST SUPV", "PUBLIC ASSISTANCE SPECIALIST SUPERVISOR"),
    ("AG STANDARDS BIOLOGST", "AGRICULTURAL AND STANDARDS BIOLOGIST"),
    ("AG COMM SEALER", "AGRICULTURAL COMMISSIONER SEALER"),
    ("AG COMM", "AGRICULTURAL COMMISSIONER"),
    ("SEALER WGTS AND MEAS", "SEALER OF WEIGHTS AND MEASURES"),
    ("WGTS AND MEAS", "WEIGHTS AND MEASURES"),
    ("MAINT WKR", "MAINTENANCE WORKER"),
    ("PSYCHIATRIC LVN TECHNICIAN", "PSYCHIATRIC LICENSED VOCATIONAL NURSE"),
    ("PSYCHIATRIC LVN", "PSYCHIATRIC LICENSED VOCATIONAL NURSE"),
    ("NURSE PRACTIONER", "NURSE PRACTITIONER"),
    ("PRACTIONER", "PRACTITIONER"),
    ("ADMIN ASST", "ADMINISTRATIVE ASSISTANT"),
    ("ADMIN ASSISTANT", "ADMINISTRATIVE ASSISTANT"),
    ("ASST ", "ASSISTANT "),
    ("SUPVG ", "SUPERVISING "),
    ("SUPV ", "SUPERVISOR "),
    ("WKR ", "WORKER "),
    ("BIOLOGST", "BIOLOGIST"),
    ("DEP AG", "DEPUTY AGRICULTURAL"),
    ("HHS ", "HEALTH AND HUMAN SERVICES "),
    ("CHILD SERVICES", "CHILDREN SERVICES"),
    ("EMPLOY SERVICES", "EMPLOYMENT SERVICES"),
    ("ADULT SERVICES", "ADULT SERVICES"),
)


def strip_grade(s):
    s = re.sub(r"\bFLEX\s+\d\s*-?\s*[A-Z0-9]+\b", "", s)
    s = re.sub(r"\b-?\s*LT\b", "", s)
    s = re.sub(r"\b(1|2|3|4|I|II|III|IV)\b$", "", s)
    return re.sub(r"\s+", " ", s).strip()


def norm_key(s):
    s = s.upper().replace("&", " AND ")
    s = re.sub(r"[^A-Z0-9 ]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"\bIII\b", "3", s)
    s = re.sub(r"\bII\b", "2", s)
    s = re.sub(r"\bIV\b", "4", s)
    s = re.sub(r"\bI\b", "1", s)
    for a, b in ABBREV:
        s = s.replace(a, b)
    return re.sub(r"\s+", " ", s).strip()


def join_fte_to_salary(fte_rows, salaries):
    by_norm = defaultdict(list)
    by_base = defaultdict(list)
    for s in salaries:
        k = norm_key(s["title"])
        by_norm[k].append(s)
        by_base[strip_grade(k)].append(s)

    def blend(recs):
        """For a FLEX series, use the bottom of I and the top of the highest grade."""
        recs = sorted(recs, key=lambda r: r["max"])
        lo, hi = recs[0], recs[-1]
        mid = recs[len(recs) // 2]
        return {
            "title": mid["title"] if len(recs) == 1 else f"{lo['title']}–{hi['title'].split()[-1]}",
            "min": min(r["min"] for r in recs),
            "max": max(r["max"] for r in recs),
            "topAnnual": max(r["topAnnual"] for r in recs),
            "grade": hi.get("grade", ""),
            "flsa": hi.get("flsa", ""),
        }

    def find_salary(title):
        k = norm_key(title)
        if k in by_norm:
            return blend(by_norm[k])
        base = strip_grade(k)
        if base in by_base:
            return blend(by_base[base])
        for sk, recs in by_base.items():
            if base and (base.startswith(sk) or sk.startswith(base)) and abs(len(base) - len(sk)) <= 12:
                return blend(recs)
        tokens = set(base.split())
        best, best_n = None, 0
        for sk, recs in by_base.items():
            st = set(sk.split())
            n = len(tokens & st)
            if n >= 2 and n > best_n and n / max(len(tokens), 1) >= 0.7:
                best, best_n = recs, n
        return blend(best) if best else None

    joined = []
    unmatched = 0
    for r in fte_rows:
        sal = find_salary(r["title"])
        rec = dict(r)
        if sal:
            rec["salaryTitle"] = sal["title"]
            rec["min"] = sal["min"]
            rec["max"] = sal["max"]
            rec["topAnnual"] = sal["topAnnual"]
            rec["mid"] = (sal["min"] + sal["max"]) / 2
            rec["estPayrollMid"] = rec["mid"] * rec["fte"]
            rec["estPayrollMax"] = sal["max"] * rec["fte"]
            rec["matched"] = True
        else:
            rec["matched"] = False
            unmatched += 1
        joined.append(rec)
    return joined, unmatched


def class_rollups(joined):
    by_title = defaultdict(lambda: {"fte": 0.0, "estMid": 0.0, "estMax": 0.0, "min": None, "max": None, "units": []})
    for r in joined:
        if not r.get("matched"):
            continue
        key = r["salaryTitle"]
        rec = by_title[key]
        rec["fte"] += r["fte"]
        rec["estMid"] += r["estPayrollMid"]
        rec["estMax"] += r["estPayrollMax"]
        rec["min"] = r["min"] if rec["min"] is None else min(rec["min"], r["min"])
        rec["max"] = r["max"] if rec["max"] is None else max(rec["max"], r["max"])
        if r["unitName"] and r["unitName"] not in rec["units"]:
            rec["units"].append(r["unitName"])
    out = []
    for title, rec in by_title.items():
        out.append(
            {
                "title": title,
                "fte": round(rec["fte"], 2),
                "min": rec["min"],
                "max": rec["max"],
                "estPayrollMid": round(rec["estMid"]),
                "estPayrollMax": round(rec["estMax"]),
                "units": rec["units"][:6],
            }
        )
    return out


def main():
    if not PDF.exists():
        raise SystemExit(f"PDF not found: {PDF}")
    pdf = pdfplumber.open(PDF)
    salaries = extract_salaries(pdf)
    fte_rows = extract_fte(pdf)
    joined, unmatched = join_fte_to_salary(fte_rows, salaries)
    rollups = class_rollups(joined)
    fte_by_salary = defaultdict(float)
    units_by_salary = defaultdict(list)
    for r in joined:
        if not r.get("matched"):
            continue
        fte_by_salary[r["salaryTitle"]] += r["fte"]
        if r["unitName"] and r["unitName"] not in units_by_salary[r["salaryTitle"]]:
            units_by_salary[r["salaryTitle"]].append(r["unitName"])
    for s in salaries:
        s["fte"] = 0.0
        s["units"] = []
        if s["title"] in fte_by_salary:
            s["fte"] = round(fte_by_salary[s["title"]], 2)
            s["units"] = units_by_salary[s["title"]][:6]
        else:
            base = strip_grade(norm_key(s["title"]))
            for key, fte in fte_by_salary.items():
                if strip_grade(norm_key(key)) == base or s["title"] in key:
                    s["fte"] = round(fte, 2)
                    s["units"] = units_by_salary[key][:6]
                    break
        s["estPayrollMid"] = round(((s["min"] + s["max"]) / 2) * s["fte"])
        s["estPayrollMax"] = round(s["max"] * s["fte"])
    total_fte = sum(r["fte"] for r in fte_rows)
    matched_fte = sum(r["fte"] for r in joined if r.get("matched"))
    payload = {
        "source": str(PDF.relative_to(ROOT)),
        "effective": "May 1, 2025",
        "year": "FY 2025-26",
        "classifications": len(salaries),
        "allocationRows": len(fte_rows),
        "totalFte": round(total_fte, 2),
        "matchedFte": round(matched_fte, 2),
        "unmatchedRows": unmatched,
        "salaries": sorted(salaries, key=lambda s: -s["max"]),
        "allocation": fte_rows,
        "byClass": sorted(rollups, key=lambda r: -r["estPayrollMid"]),
    }
    OUT.write_text(json.dumps(payload, indent=2))
    print(f"Wrote {OUT}")
    print(f"  classifications: {len(salaries)}")
    print(f"  allocation rows: {len(fte_rows)}  FTE {total_fte:.1f}")
    print(f"  matched FTE:     {matched_fte:.1f}  unmatched rows: {unmatched}")
    print("  top pay:")
    for s in payload["salaries"][:8]:
        print(f"    ${s['max']:10,.0f}  {s['title']}")
    print("  costliest classes (mid-range × FTE):")
    for r in payload["byClass"][:8]:
        print(f"    ${r['estPayrollMid']:10,.0f}  {r['fte']:6.1f} FTE  {r['title']}")


if __name__ == "__main__":
    main()
