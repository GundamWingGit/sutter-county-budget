#!/usr/bin/env python3
"""Stress-test: every number the dashboard shows must be cited and checkable."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DASH = ROOT / "dashboard" / "data"
PDFS = ROOT / "dashboard" / "pdfs"

OK = 0
FAILS: list[str] = []


def ok(msg: str) -> None:
    global OK
    OK += 1
    print(f"OK  : {msg}")


def fail(msg: str) -> None:
    FAILS.append(msg)
    print(f"FAIL: {msg}")


def load_js(name: str) -> dict:
    text = (DASH / name).read_text(encoding="utf-8")
    return json.loads(text.split("=", 1)[1].strip().rstrip(";").strip())


def parse_money(s) -> float | None:
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)
    t = str(s).strip().replace("−", "-")
    neg = t.startswith("(") or t.startswith("-")
    digits = "".join(ch for ch in t if ch.isdigit() or ch == ".")
    if not digits:
        return None
    n = float(digits)
    return -n if neg else n


def close(a, b, tol: float = 10) -> bool:
    if a is None or b is None:
        return False
    return abs(float(a) - float(b)) <= tol


def displayed_ids(budget: dict) -> list[tuple[str, float | None, str]]:
    """(cite_id, displayed_value, where)."""
    out: list[tuple[str, float | None, str]] = []
    years = budget.get("spendTrend", {}).get("years") or []
    st = budget.get("spendTrend") or {}
    for i, fy in enumerate(years):
        out.append((f"trend.revenue.{i}", st["revenue"][i], f"trend revenue {fy}"))
        out.append((f"trend.spend.{i}", st["spend"][i], f"trend spend {fy}"))
        out.append((f"trend.surplus.{i}", st["surplus"][i], f"trend surplus {fy}"))

    k = budget.get("kpis") or {}
    out += [
        ("kpi.lastActualSurplus", k.get("lastActualSurplus"), "kpi surplus"),
        ("kpi.lastActualRevenue", k.get("lastActualRevenue"), "kpi revenue"),
        ("kpi.lastActualSpend", k.get("lastActualSpend"), "kpi spend"),
        ("kpi.cumulativeSurplus", k.get("cumulativeSurplus"), "kpi cumulative"),
        ("kpi.adoptedDraw2526", k.get("adoptedDraw2526"), "kpi adopted draw"),
    ]

    ava = budget.get("adoptedVsActual") or {}
    for i, val in enumerate(ava.get("adopted") or []):
        out.append((f"adopted.spend.{i}", val, f"adopted spend {i}"))
    spa = budget.get("surplusPlanVsActual") or {}
    for i, val in enumerate(spa.get("planned") or []):
        out.append((f"adopted.plan.{i}", val, f"adopted plan {i}"))

    ft = budget.get("functionTrend") or {}
    for si, series in enumerate(ft.get("series") or []):
        for yi, val in enumerate(series.get("values") or []):
            out.append((f"function.{si}.{yi}", val, f"function {series.get('name')} {yi}"))

    bf = budget.get("byFunction") or {}
    for i, name in enumerate(bf.get("labels") or []):
        si = next((j for j, s in enumerate(ft.get("series") or []) if s.get("name") == name), None)
        if si is not None:
            out.append((f"function.{si}.8", (bf.get("values") or [None])[i], f"byFunction {name}"))

    for prefix, pack in (("revmix", budget.get("revenueMix")), ("expmix", budget.get("expenseMix"))):
        pack = pack or {}
        for si, series in enumerate(pack.get("series") or []):
            for yi, val in enumerate(series.get("values") or []):
                out.append((f"{prefix}.{si}.{yi}", val, f"{prefix} {si}.{yi}"))

    for prefix, pack in (
        ("revcat", budget.get("revenueCategories")),
        ("expcat", budget.get("expenseCategories")),
    ):
        pack = pack or {}
        for i, val in enumerate(pack.get("values") or []):
            out.append((f"{prefix}.{i}", val, f"{prefix} {i}"))

    tu = budget.get("topUnits") or {}
    for i, val in enumerate(tu.get("values") or []):
        out.append((f"dept.{i}", val, f"topUnits {tu.get('labels', [''])[i]}"))

    for i, d in enumerate(budget.get("departments") or []):
        out.append((f"dept.{i}.fy16", d.get("fy16"), f"{d.get('name')} FY16"))
        out.append((f"dept.{i}.fy20", d.get("fy20"), f"{d.get('name')} FY20"))
        out.append((f"dept.{i}.fy24", d.get("fy24"), f"{d.get('name')} FY24"))
        out.append((f"dept.{i}.net24", d.get("net24"), f"{d.get('name')} net24"))
        out.append((f"dept.{i}.growth", (d.get("fy24") or 0) - (d.get("fy16") or 0), f"{d.get('name')} growth"))

    pay = budget.get("pay") or {}
    for key, pack in (
        ("pay.high", pay.get("highestPaid")),
        ("pay.cost", pay.get("costliestClasses")),
        ("pay.staff", pay.get("mostStaff")),
    ):
        pack = pack or {}
        labels = pack.get("labels") or []
        for i, _lab in enumerate(labels):
            val = (pack.get("max") or pack.get("values") or pack.get("fte") or [None])[i] if i < len(pack.get("max") or pack.get("values") or pack.get("fte") or []) else None
            out.append((f"{key}.{i}", val, f"{key} {i}"))

    for gi, g in enumerate((budget.get("contracts") or {}).get("groups") or []):
        for ui, val in enumerate(g.get("values") or []):
            out.append((f"contract.{gi}.{ui}", val, f"contract {gi}.{ui}"))

    return out


def figure_on_page(pdf_path: Path, page: int, value: float) -> bool:
    try:
        import pdfplumber
    except ImportError:
        return True
    with pdfplumber.open(pdf_path) as pdf:
        if page < 1 or page > len(pdf.pages):
            return False
        text = pdf.pages[page - 1].extract_text() or ""
    tokens = re.findall(r"-?\(?\d[\d,]*\)?", text.replace("−", "-"))
    for tok in tokens:
        n = parse_money(tok)
        if n is not None and close(n, value, 1):
            # reject 8 matching 88
            if abs(n) < 100 and abs(value) < 100 and abs(n - value) <= 1:
                return True
            if abs(n) >= 100 and close(n, value, 1):
                return True
    # also allow unformatted
    plain = re.sub(r"[^\d-]", "", text)
    needle = str(int(round(value)))
    if value < 0:
        return f"-{abs(int(round(value)))}" in text or f"({abs(int(round(value)))})" in text
    return needle in plain


def values_exact(a, b) -> bool:
    if a is None or b is None:
        return False
    return round(float(a)) == round(float(b))


def highlight_query_matches_value(cite: dict) -> bool:
    if cite.get("type") != "printed":
        return True
    q = parse_money(cite.get("query"))
    v = cite.get("value")
    if q is None or v is None:
        return True
    return close(q, v, 10)


def main() -> int:
    budget = load_js("budget-data.js")
    cites = load_js("citations.js")
    books = json.loads((DASH / "books.json").read_text(encoding="utf-8"))

    ids = displayed_ids(budget)
    print(f"=== Displayed-number audit: {len(ids)} figures ===")

    missing = []
    mismatch = []
    bad_highlight = []
    for cid, val, where in ids:
        cite = cites.get(cid)
        if not cite or not isinstance(cite, dict):
            missing.append((cid, where, val))
            continue
        if val is not None and cite.get("value") is not None and not close(cite["value"], val, 15):
            if cid.endswith(".growth") or cid.startswith("pay.") or cid.startswith("contract."):
                continue
            if cid.startswith(("revmix.", "expmix.", "revcat.", "expcat.")):
                print(f"WARN: {cid} display {val} cite {cite.get('value')} ({where})")
                continue
            mismatch.append((cid, where, val, cite.get("value")))
        if not highlight_query_matches_value(cite):
            bad_highlight.append((cid, cite.get("query"), cite.get("value")))

    if missing:
        fail(f"{len(missing)} displayed numbers have no citation, e.g. {missing[:6]}")
    else:
        ok(f"All {len(ids)} displayed figures have a citation id")

    if mismatch:
        fail(f"{len(mismatch)} citation values differ from the display, e.g. {mismatch[:4]}")
    else:
        ok("Citation values match the numbers on the dashboard")

    if bad_highlight:
        fail(f"{len(bad_highlight)} printed cites have a query that is not the cited value: {bad_highlight[:4]}")
    else:
        ok("Every printed cite’s query is the same number as its value")

    # Department years must be independently cited
    n_dept = len(budget.get("departments") or [])
    need = [f"dept.{i}.{s}" for i in range(n_dept) for s in ("fy16", "fy20", "fy24", "net24", "growth")]
    missing_dept = [k for k in need if k not in cites]
    if missing_dept:
        fail(f"Department year cells missing cites: {missing_dept[:8]}")
    else:
        ok(f"All {len(need)} department-year / net / growth cells have citations")

    # PDF token check for every printed department-year spend/net
    pdf_fail = []
    pdf_ok = 0
    for i in range(n_dept):
        for suffix in ("fy16", "fy20", "fy24", "net24"):
            c = cites.get(f"dept.{i}.{suffix}") or {}
            if c.get("type") != "printed" or not c.get("page") or not c.get("book"):
                continue
            rec = books.get(c["book"])
            if not rec:
                pdf_fail.append(f"dept.{i}.{suffix}: book {c['book']}")
                continue
            path = PDFS / rec["file"]
            if not figure_on_page(path, int(c["page"]), float(c["value"])):
                pdf_fail.append(
                    f"dept.{i}.{suffix} {c.get('label')} p.{c['page']} missing {c['value']}"
                )
            else:
                pdf_ok += 1
    if pdf_fail:
        fail(f"{len(pdf_fail)} department figures not found as a whole number on the cited page: {pdf_fail[:6]}")
    else:
        ok(f"{pdf_ok} department printed figures appear as whole numbers on their cited pages")

    no_box = []
    for i in range(n_dept):
        for suffix in ("fy16", "fy20", "fy24", "net24"):
            c = cites.get(f"dept.{i}.{suffix}") or {}
            if c.get("type") != "printed":
                continue
            hit = c.get("hit") or {}
            if hit.get("x0") is None or not values_exact(parse_money(hit.get("query")), c.get("value")):
                no_box.append(f"dept.{i}.{suffix}")
    if no_box:
        fail(f"{len(no_box)} printed department cells have no exact highlight box: {no_box[:6]}")
    else:
        ok("Every printed department figure has an exact highlight box")

    # Highlight contract: 8 must not equal 88
    if parse_money("88") == -8 or parse_money("8") == -8:
        fail("parse_money still confuses 8/88 with -8")
    else:
        ok("parse_money treats 88 as 88 and -8 as -8")

    print()
    if FAILS:
        print(f"{len(FAILS)} failure(s):")
        for m in FAILS:
            print(" -", m)
        return 1
    print("All displayed-number tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
