#!/usr/bin/env python3
"""Extract budget-unit line-item detail (Schedule 9) from the digital-native
Sutter County budget books (FY 2018-19 through FY 2026-27 editions).

Output: data/unit_lines.csv with one row per (book, unit, account, column).
Column labels are normalized to e.g. "FY2016-17|actual", "FY2018-19|recommended".
"""
import csv
import re
import sys
from pathlib import Path

import pdfplumber

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "unit_lines.csv"

# era2: per-account rows, "Unit Title:" header.  era3: by-object rows, "Cost Center:" header.
BOOKS = [
    ("FY 2018-19", "documents/FY 2018-19/FY 2018-19 Recommended Budget (Complete File).pdf", "era2"),
    ("FY 2019-20", "documents/FY 2019-20/FY 2019-20 Recommended Budget (Complete File).pdf", "era2"),
    ("FY 2020-21", "documents/FY 2020-21/FY 2020-21 Recommended Budget (Complete File).pdf", "era2"),
    ("FY 2021-22", "documents/FY 2021-22/00 - FY 2021-22 Recommended Budget.pdf", "era2"),
    ("FY 2022-23", "documents/FY 2022-23/00 - FY 2022-23 Recommended Budget.pdf", "era2"),
    ("FY 2023-24", "documents/FY 2023-24/FY 2023-24 Adopted Budget.pdf", "era2"),
    ("FY 2024-25", "documents/FY 2024-25/00 - FY 2024-25 Recommended Budget.pdf", "era2"),
    ("FY 2025-26", "documents/FY 2025-26/0 FY 2025-26 Adopted Budget.pdf", "era3"),
    ("FY 2026-27", "documents/FY 2026-27/FY 2026-27 Recommended Budget.pdf", "era3_6col"),
]

NUM_RE = re.compile(r"^\(?\$?-?[\d,]*\d(\.\d+)?\)?$|^-$")


def merge_words(page):
    """extract_words, merging tokens with ~zero horizontal gap (font splits)."""
    words = page.extract_words(x_tolerance=1)
    rows = {}
    for w in words:
        rows.setdefault(round(w["top"] / 2.5), []).append(w)
    lines = []
    for key in sorted(rows):
        ws = sorted(rows[key], key=lambda w: w["x0"])
        merged = []
        for w in ws:
            if merged and w["x0"] - merged[-1]["x1"] < 1.0:
                merged[-1] = {
                    "text": merged[-1]["text"] + w["text"],
                    "x0": merged[-1]["x0"],
                    "x1": w["x1"],
                    "top": merged[-1]["top"],
                }
            else:
                merged.append(dict(text=w["text"], x0=w["x0"], x1=w["x1"], top=w["top"]))
        lines.append(merged)
    return lines


def parse_number(tok):
    t = tok.replace("$", "").replace(",", "").strip()
    if t in ("-", ""):
        return 0.0
    neg = t.startswith("(") and t.endswith(")")
    t = t.strip("()")
    try:
        v = float(t)
    except ValueError:
        return None
    return -v if neg else v


def fy_shift(fy_label, delta):
    """fy_label like 'FY 2018-19'; delta in years."""
    start = int(fy_label.split()[1].split("-")[0]) + delta
    return f"FY{start}-{str(start + 1)[-2:]}"


def era2_columns(book_fy, label_line_text, ncols=6):
    if ncols == 4:  # FY 2023-24 adopted: Actual, Actual, Recommend, BOS Adopted
        kinds = ["actual", "actual", "recommended", "adopted"]
        years = [-2, -1, 0, 0]
        return [(fy_shift(book_fy, y), k) for y, k in zip(years, kinds)]
    if "Actual as of" in label_line_text:
        kinds = ["actual", "actual_estimated", "adopted", "adjusted", "requested", "recommended"]
        years = [-2, -1, -1, -1, 0, 0]
    else:  # Actual Adopted Adjusted Estimated Department CAO
        kinds = ["actual", "adopted", "adjusted", "actual_estimated", "requested", "recommended"]
        years = [-2, -1, -1, -1, 0, 0]
    return [(fy_shift(book_fy, y), k) for y, k in zip(years, kinds)]


def era3_columns(book_fy, ncols):
    if ncols == 4:
        # 23-24: Actual, Actual, Recommended, Adopted; 25-26: Actual, ActualXEstimated, Rec, Adopted
        return [
            (fy_shift(book_fy, -2), "actual"),
            (fy_shift(book_fy, -1), "actual_estimated"),
            (fy_shift(book_fy, 0), "recommended"),
            (fy_shift(book_fy, 0), "adopted"),
        ]
    return [
        (fy_shift(book_fy, -2), "actual"),
        (fy_shift(book_fy, -1), "actual_estimated"),
        (fy_shift(book_fy, 0), "recommended"),
    ]


def find_year_header(lines):
    """Return (line_index, [word boxes]) for the line of 3+ year tokens like 2016-2017 / 2023-24."""
    yr = re.compile(r"^20\d{2}-(20)?\d{2}$")
    for i, ws in enumerate(lines[:14]):
        toks = [w for w in ws if yr.match(w["text"])]
        if len(toks) >= 3:
            return i, toks
    return None, None


def assign_columns(ws, col_x):
    """Split words into (name_words, {col_idx: value}) using column x-centers."""
    name_parts, vals = [], {}
    leftmost = min(col_x) - 40
    for w in ws:
        center = (w["x0"] + w["x1"]) / 2
        if NUM_RE.match(w["text"]) and w["x1"] > leftmost:
            idx = min(range(len(col_x)), key=lambda i: abs(col_x[i] - center))
            v = parse_number(w["text"])
            if v is not None:
                if idx in vals:
                    vals[idx] = vals[idx]  # keep first; overlap is header noise
                else:
                    vals[idx] = v
                continue
        if w["text"] != "$":
            name_parts.append(w["text"])
    return " ".join(name_parts).strip(), vals


HEADER_NOISE = re.compile(
    r"^(state controller|county budget act|schedule 9|governmental funds|fiscal year|"
    r"financing uses classification|expenditures$|1 2 3 4|county of sutter|"
    r"function, activity|estimated board|actual actual|adopted by|"
    r"\d{1,2}(?:\s+\d{1,2})*)$",
    re.I,
)


def is_column_number_row(name, vals):
    """Schedule 9 prints 1 2 3 4 5 under the year headers. Those are column
    indexes, not dollars. After assign_columns the name is often just '1'
    and the values are 2,3,4,5 sitting in the money columns."""
    n = (name or "").strip()
    if not n:
        return True
    if re.fullmatch(r"\d{1,2}(?:\s+\d{1,2})*", n):
        return True
    nums = [v for v in (vals or {}).values() if v == int(v) and 1 <= v <= 8]
    if len(nums) >= 3:
        seq = sorted(int(v) for v in nums)
        if seq == list(range(seq[0], seq[-1] + 1)):
            return True
    return False


def parse_era2_page(page_lines, book_fy):
    head = " ".join(w["text"] for ws in page_lines[:8] for w in ws)
    m = re.search(r"Unit Title:\s*(\S+)\s*-\s*(.*?)\s*County Budget Act", head)
    if not m:
        m = re.search(r"Unit Title:\s*(\S+)\s*-?\s*([A-Z0-9 &/'\.\-]*)", head)
    unit_code, unit_name = (m.group(1), m.group(2).strip()) if m else ("", "")
    fund = fn = act = ""
    fm = re.search(r"Fund:\s*(\S+\s*-\s*[A-Z0-9 &/'\.\-]+?)(?:SCHEDULE|Function|$)", head)
    if fm:
        fund = fm.group(1).strip()
    fnm = re.search(r"Function:\s*([A-Z &/\-]+)", head)
    if fnm:
        fn = fnm.group(1).strip()
    am = re.search(r"Activity:\s*([A-Z &/\-]+)", head)
    if am:
        act = am.group(1).strip()

    hi, ytoks = find_year_header(page_lines)
    if hi is None:
        return None
    col_x = [(w["x0"] + w["x1"]) / 2 for w in ytoks]
    label_text = " ".join(w["text"] for ws in page_lines[hi + 1 : hi + 3] for w in ws)
    cols = era2_columns(book_fy, label_text, ncols=len(col_x))
    if len(col_x) != len(cols):
        cols = cols[: len(col_x)]

    rows = []
    for ws in page_lines[hi + 1 :]:
        name, vals = assign_columns(ws, col_x)
        if not vals or HEADER_NOISE.match(name) or is_column_number_row(name, vals):
            continue
        acct = ""
        am2 = re.match(r"^(\d{5})\s+(.*)$", name)
        if am2:
            acct, name = am2.group(1), am2.group(2)
        rows.append((unit_code, unit_name, fund, fn, act, acct, name, vals, cols))
    return rows


def cluster_columns(page_lines, skip_lines=12, gap=22.0):
    """Derive column x-centers by clustering numeric-token positions on data rows."""
    centers = []
    for ws in page_lines[skip_lines:]:
        for w in ws:
            # bare "-" is often label punctuation, not a zero cell; exclude from clustering
            if NUM_RE.match(w["text"]) and any(c.isdigit() for c in w["text"]):
                centers.append((w["x0"] + w["x1"]) / 2)
    if not centers:
        return []
    centers.sort()
    clusters = [[centers[0]]]
    for c in centers[1:]:
        if c - clusters[-1][-1] > gap:
            clusters.append([c])
        else:
            clusters[-1].append(c)
    return [sum(cl) / len(cl) for cl in clusters]


def parse_era3_page(page_lines, book_fy):
    head = " ".join(w["text"] for ws in page_lines[:10] for w in ws)
    m = re.search(r"Cost Center:\s*(\S+)\s*-\s*(.+?)\s*(Function:|$)", head)
    unit_code, unit_name = (m.group(1), m.group(2).strip()) if m else ("", "")
    fnm = re.search(r"Function:\s*(.+?)\s*(Activity:|$)", head)
    fn = fnm.group(1).strip() if fnm else ""
    am = re.search(r"Activity:\s*(.+?)\s*(20\d\d|$)", head)
    act = am.group(1).strip() if am else ""

    hi, ytoks = find_year_header(page_lines)
    if hi is None:
        return None
    col_x = [(w["x0"] + w["x1"]) / 2 for w in ytoks]
    cols = era3_columns(book_fy, len(col_x))

    rows = []
    for ws in page_lines[hi + 1 :]:
        name, vals = assign_columns(ws, col_x)
        if not vals or HEADER_NOISE.match(name) or is_column_number_row(name, vals):
            continue
        rows.append((unit_code, unit_name, "", fn, act, "", name, vals, cols))
    return rows


ERA36_COLS = lambda book_fy: [
    (fy_shift(book_fy, -2), "actual"),
    (fy_shift(book_fy, -1), "adopted"),
    (fy_shift(book_fy, -1), "adjusted"),
    (fy_shift(book_fy, -1), "actual_estimated"),
    (fy_shift(book_fy, 0), "requested"),
    (fy_shift(book_fy, 0), "recommended"),
]


def parse_era3_6col_page(page_lines, book_fy, col_x, context):
    """FY 2026-27: 6 numeric columns with decimals; labels wrap onto their own lines.
    col_x: global column grid.  context: dict carried across continuation pages."""
    head = " ".join(w["text"] for ws in page_lines[:10] for w in ws)
    m = re.search(r"Cost Center:\s*(\S+)\s*-\s*(.+?)\s*(Fund:|Function:|$)", head)
    if m:
        context["unit_code"], context["unit_name"] = m.group(1), m.group(2).strip()
        fdm = re.search(r"Fund:\s*(\S+)\s*-\s*(.+?)\s*(County of Sutter|Function:|Schedule|$)", head)
        context["fund"] = (fdm.group(1) + " - " + fdm.group(2).strip()) if fdm else ""
        fnm = re.search(r"Function:\s*(.+?)\s*(Schedule|Activity:|$)", head)
        context["function"] = fnm.group(1).strip() if fnm else ""
        am = re.search(r"Activity:\s*(.+?)\s*(Fiscal|$)", head)
        context["activity"] = am.group(1).strip() if am else ""
    unit_code = context.get("unit_code", "")
    unit_name = context.get("unit_name", "")
    fn = context.get("function", "")
    act = context.get("activity", "")
    cols = ERA36_COLS(book_fy)
    skip = 12 if m else 5  # continuation pages have a short repeated header

    # First pass: classify lines into value-rows and label-fragment rows.
    parsed_lines = []
    for ws in page_lines[skip:]:
        nums = [w for w in ws if NUM_RE.match(w["text"]) and any(c.isdigit() for c in w["text"])]
        text = " ".join(
            w["text"] for w in ws
            if not (NUM_RE.match(w["text"]) and any(c.isdigit() for c in w["text"]))
            and w["text"] not in ("$", "-")
        )
        top = ws[0]["top"] if ws else 0
        parsed_lines.append({"nums": nums, "text": text.strip(), "top": top})

    LOCAL_NOISE = re.compile(
        r"^(state controller|county budget act|schedule 9|governmental funds|fiscal year|"
        r"cost center|function|activity|adjusted estimated|request$|actuals)", re.I,
    )
    rows, used_frag = [], set()
    for i, ln in enumerate(parsed_lines):
        if len(ln["nums"]) < 3:
            continue
        name = ln["text"]
        for j in (i - 1, i + 1):
            if 0 <= j < len(parsed_lines) and j not in used_frag:
                frag = parsed_lines[j]
                if not frag["nums"] and frag["text"] and abs(frag["top"] - ln["top"]) < 14 \
                        and not LOCAL_NOISE.match(frag["text"]):
                    if j < i:
                        name = (frag["text"] + " " + name).strip()
                    else:
                        name = (name + " " + frag["text"]).strip()
                    used_frag.add(j)
        if LOCAL_NOISE.match(name) or is_column_number_row(name, {}):
            continue
        vals = {}
        for w in ln["nums"]:
            center = (w["x0"] + w["x1"]) / 2
            idx = min(range(len(col_x)), key=lambda k: abs(col_x[k] - center))
            if abs(col_x[idx] - center) > 30:  # stray token far from any column
                continue
            v = parse_number(w["text"])
            if v is not None and idx not in vals:
                vals[idx] = v
        if vals:
            rows.append((unit_code, unit_name, context.get("fund", ""), fn, act, "", name, vals, cols))
    return rows


def extract_era3_6col_book(pdf, book_fy):
    """Two-pass extraction for the FY 2026-27 book: build a global column grid
    from full-width pages, then parse every unit page including continuations."""
    is_unit = []
    for page in pdf.pages:
        t = (page.extract_text() or "")[:500]
        header = "Cost Center:" in t and re.search(r"Schedule 9", t)
        cont = ("2024-25 Actuals" in t or "CAO" in t) and re.search(r"SC\d{5}|Recommended", t) \
            and not header and "Table of Contents" not in t
        is_unit.append("H" if header else ("C" if cont else "-"))

    # global grid from header pages with exactly 6 clusters
    grids = []
    for i, page in enumerate(pdf.pages):
        if is_unit[i] == "H":
            cx = cluster_columns(merge_words(page), skip_lines=12)
            if len(cx) == 6:
                grids.append(cx)
    if not grids:
        return []
    col_x = [sum(g[k] for g in grids) / len(grids) for k in range(6)]

    all_rows, context, in_section = [], {}, False
    for i, page in enumerate(pdf.pages):
        if is_unit[i] == "H":
            in_section = True
        elif is_unit[i] == "-":
            in_section = False
            continue
        elif not in_section:  # continuation page with no preceding header
            continue
        rows = parse_era3_6col_page(merge_words(page), book_fy, col_x, context)
        if rows:
            all_rows.append((i + 1, rows))
    return all_rows


def main():
    only = set(sys.argv[1:])
    outdir = ROOT / "data" / "unit_lines"
    outdir.mkdir(parents=True, exist_ok=True)
    for book_fy, rel, era in BOOKS:
        if only and book_fy not in only:
            continue
        path = ROOT / rel
        f = open(outdir / (book_fy.replace(" ", "_") + ".csv"), "w", newline="")
        w = csv.writer(f)
        w.writerow(["book", "page", "unit_code", "unit_name", "fund", "function",
                    "activity", "account", "line_name", "fy", "kind", "value"])
        n_rows = n_pages = 0

        def emit(pageno, parsed):
            nonlocal n_rows
            for unit_code, unit_name, fund, fn, act, acct, name, vals, cols in parsed:
                if is_column_number_row(name, vals):
                    continue
                for idx, v in vals.items():
                    if idx < len(cols):
                        fy, kind = cols[idx]
                        w.writerow([book_fy, pageno, unit_code, unit_name, fund,
                                    fn, act, acct, name, fy, kind, v])
                        n_rows += 1

        with pdfplumber.open(path) as pdf:
            if era == "era3_6col":
                for pageno, parsed in extract_era3_6col_book(pdf, book_fy):
                    n_pages += 1
                    emit(pageno, parsed)
            else:
                for pi, page in enumerate(pdf.pages):
                    t = (page.extract_text() or "")[:400]
                    if era == "era2":
                        if "Detail of Financing Sources and Financing Uses" not in t:
                            continue
                    else:
                        if not re.search(r"Schedule 9", t) or "Cost Center:" not in t:
                            continue
                    lines = merge_words(page)
                    parsed = (parse_era2_page if era == "era2" else parse_era3_page)(lines, book_fy)
                    if not parsed:
                        continue
                    n_pages += 1
                    emit(pi + 1, parsed)
        f.close()
        print(f"{book_fy}: {n_pages} pages, {n_rows} values", flush=True)


if __name__ == "__main__":
    main()
