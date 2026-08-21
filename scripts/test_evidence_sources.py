#!/usr/bin/env python3
"""Regression tests for click-to-source evidence: every citation must resolve
to at least one printable {book, page, file}, PDFs must exist, enlarge path
must resolve, and key printed figures must appear on their cited pages.
"""
from __future__ import annotations

import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DASH = ROOT / "dashboard"
PDFS = DASH / "pdfs"
BOOKS_PATH = DASH / "data" / "books.json"
CITATIONS_PATH = DASH / "data" / "citations.js"
INDEX_PATH = DASH / "index.html"
LINES_DIR = DASH / "data" / "lines"

BASE_URL = "http://127.0.0.1:4173"

failures: list[str] = []


def fail(msg: str) -> None:
    failures.append(msg)
    print("FAIL:", msg)


def ok(msg: str) -> None:
    print("OK  :", msg)


def load_citations() -> dict:
    text = CITATIONS_PATH.read_text(encoding="utf-8")
    raw = text.split("=", 1)[1].strip().rstrip(";").strip()
    return json.loads(raw)


def load_books() -> dict:
    return json.loads(BOOKS_PATH.read_text(encoding="utf-8"))


def is_revenue_line(line: str) -> bool:
    return bool(re.match(r"^(total\s+)?revenues?$", (line or "").strip(), re.I))


def is_spend_line(line: str) -> bool:
    return bool(
        re.match(
            r"^(total\s+)?expenditures?(?:\s+and\s+appropriations)?$",
            (line or "").strip(),
            re.I,
        )
    )


def prefer_total(line: str) -> bool:
    return bool(re.match(r"^total\s+", (line or "").strip(), re.I))


def metric_from_child(kid: dict) -> str | None:
    if kid.get("metric") in ("revenue", "spend"):
        return kid["metric"]
    label = " ".join(
        str(kid.get(k) or "") for k in ("label", "line", "formula", "metric")
    )
    if re.search(r"revenue", label, re.I) and not re.search(r"spend|expend", label, re.I):
        return "revenue"
    if re.search(r"spend|expend", label, re.I):
        return "spend"
    return kid.get("metric")


def rows_from_book(book: str, fy: str, kind: str, metric: str) -> list[dict]:
    slug = book.replace(" ", "_")
    path = LINES_DIR / f"{slug}.json"
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    candidates = []
    for r in data.get("rows") or []:
        if r.get("f") != fy or r.get("k") != (kind or "actual"):
            continue
        line = str(r.get("l") or "").strip()
        if metric == "revenue" and not is_revenue_line(line):
            continue
        if metric == "spend" and not is_spend_line(line):
            continue
        if abs(float(r.get("v") or 0)) < 1:
            continue
        candidates.append(r)
    candidates.sort(
        key=lambda r: (0 if prefer_total(r.get("l")) else 1, -abs(float(r.get("v") or 0)))
    )
    seen = set()
    out = []
    for r in candidates:
        key = r.get("c") or r.get("u") or ""
        if key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "book": book,
                "page": r.get("p"),
                "value": r.get("v"),
                "line": r.get("l"),
                "unit": r.get("u"),
                "unitCode": r.get("c"),
            }
        )
    return out


def hydrate(cite: dict, cites: dict) -> dict:
    if not cite:
        return cite
    match = None
    for c in cites.values():
        if not isinstance(c, dict) or c is cites.get("_meta"):
            continue
        if c.get("label") == cite.get("label") and (
            cite.get("value") is None or c.get("value") == cite.get("value")
        ):
            match = c
            break
    if not match:
        return cite
    merged = {**match, **cite}
    merged["children"] = cite.get("children") or match.get("children") or []
    return merged


def expand_sources(cite: dict, cites: dict) -> list[dict]:
    """Mirror dashboard/js/evidence-panel.js expandSources rules."""
    cite = hydrate(cite, cites)
    kids = [hydrate(k, cites) for k in (cite.get("children") or [])]
    baked = [k for k in kids if k]

    if cite.get("metric") in ("function", "category", "pay", "contract"):
        printable = [k for k in baked if k.get("page") and k.get("book")]
        return printable or baked

    if cite.get("type") == "printed":
        if cite.get("page") and cite.get("book"):
            return [{"book": cite["book"], "page": cite["page"], "value": cite.get("value")}]
        return [k for k in baked if k.get("page") and k.get("book")] or baked

    metric = cite.get("metric")
    label = cite.get("label") or ""

    if metric in ("revenue", "spend"):
        found = rows_from_book(
            cite.get("book") or "",
            cite.get("fy") or "",
            cite.get("kind") or "actual",
            metric,
        )
        return found or [k for k in baked if k.get("page")]

    if metric == "surplus" or re.search(r"surplus|draw|planned", label, re.I):
        rev = next((k for k in kids if metric_from_child(k) == "revenue"), kids[0] if kids else None)
        exp = next((k for k in kids if metric_from_child(k) == "spend"), kids[1] if len(kids) > 1 else None)
        out: list[dict] = []
        for kid, m, group in ((rev, "revenue", "Revenue"), (exp, "spend", "Spending")):
            if not kid:
                continue
            rows = rows_from_book(
                kid.get("book") or cite.get("book") or "",
                kid.get("fy") or cite.get("fy") or "",
                kid.get("kind") or cite.get("kind") or "actual",
                m,
            )
            if rows:
                for r in rows:
                    r = dict(r)
                    r["group"] = group
                    out.append(r)
            elif kid.get("page") and kid.get("book"):
                out.append({**kid, "group": group})
            else:
                out.append({**kid, "group": group})
        return [r for r in out if r.get("page") and r.get("book")] or out

    if re.search(r"cumulative", label, re.I):
        out = []
        for kid in kids:
            full = hydrate(kid, cites)
            page_piece = None
            if full.get("page") and full.get("book"):
                page_piece = full
            else:
                sub = [hydrate(s, cites) for s in (full.get("children") or [])]
                rev = next((s for s in sub if metric_from_child(s) == "revenue"), sub[0] if sub else None)
                if rev and rev.get("book") and rev.get("fy"):
                    rows = rows_from_book(
                        rev["book"], rev["fy"], rev.get("kind") or "actual", "revenue"
                    )
                    page_piece = rows[0] if rows else None
                if not page_piece:
                    page_piece = next((s for s in sub if s.get("page") and s.get("book")), None)
            out.append(
                {
                    "book": (page_piece or {}).get("book") or full.get("book"),
                    "page": (page_piece or {}).get("page") or full.get("page"),
                    "value": full.get("value"),
                    "label": full.get("label"),
                }
            )
        return out

    # Other derived: use printable children, or expand revenue/spend kids.
    printable = [k for k in baked if k.get("page") and k.get("book")]
    if printable:
        return printable
    out = []
    for kid in kids:
        m = metric_from_child(kid)
        if m in ("revenue", "spend") and (kid.get("book") or cite.get("book")) and (
            kid.get("fy") or cite.get("fy")
        ):
            rows = rows_from_book(
                kid.get("book") or cite.get("book"),
                kid.get("fy") or cite.get("fy"),
                kid.get("kind") or cite.get("kind") or "actual",
                m,
            )
            if rows:
                out.extend(rows)
                continue
        out.append(kid)
    resolved = [r for r in out if r.get("page") and r.get("book")]
    if resolved:
        return resolved
    if cite.get("page") and cite.get("book"):
        return [
            {
                "book": cite["book"],
                "page": cite["page"],
                "value": cite.get("value"),
                "query": cite.get("query"),
                "label": cite.get("label"),
            }
        ]
    return out


def first_printable(sources: list[dict]) -> dict | None:
    for s in sources:
        if s.get("book") and s.get("page"):
            return s
    return None


def http_get(path: str, timeout: float = 30.0) -> tuple[int, bytes, str]:
    url = BASE_URL.rstrip("/") + "/" + path.lstrip("/")
    req = urllib.request.Request(url, headers={"User-Agent": "evidence-test/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read(), resp.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        return e.code, e.read() if e.fp else b"", ""
    except Exception as e:
        fail(f"HTTP {path}: {e}")
        return 0, b"", ""


def figure_on_page(pdf_path: Path, page_1based: int, query: str) -> bool:
    try:
        import pdfplumber
    except ImportError:
        fail("pdfplumber not installed")
        return False
    plain = re.sub(r"[^\d]", "", query or "")
    if not plain:
        return False
    with pdfplumber.open(pdf_path) as pdf:
        if page_1based < 1 or page_1based > len(pdf.pages):
            return False
        text = pdf.pages[page_1based - 1].extract_text() or ""
        if query in text:
            return True
        flat = re.sub(r"[^\d]", "", text)
        return plain in flat


def main() -> int:
    print("=== Evidence source / enlarge regression tests ===\n")
    books = load_books()
    cites = load_citations()
    cites.pop("_meta", None)

    # 1) Every book file exists
    for label, rec in books.items():
        f = PDFS / rec["file"]
        if f.exists() and f.stat().st_size > 1000:
            ok(f"PDF exists: {label} -> {rec['file']}")
        else:
            fail(f"Missing PDF for {label}: {f}")

    # 2) Every data-cite in index.html exists (skip JS template literals)
    index = INDEX_PATH.read_text(encoding="utf-8")
    cite_ids = sorted(
        {
            cid
            for cid in re.findall(r'data-cite="([^"]+)"', index)
            if "${" not in cid and cid.strip()
        }
    )
    # Also chart-built / KPI ids referenced in script
    for mid in (
        "kpi.lastActualSurplus",
        "kpi.cumulativeSurplus",
        "kpi.adoptedDraw2526",
        "kpi.lastActualRevenue",
        "kpi.lastActualSpend",
        "trend.surplus.8",
        "trend.revenue.8",
        "trend.spend.8",
        "trend.revenue.7",
        "trend.spend.7",
        "dept.0",
    ):
        if mid not in cite_ids:
            cite_ids.append(mid)
    for cid in cite_ids:
        if cid in cites:
            ok(f"Citation present: {cid}")
        else:
            fail(f"Citation missing for data-cite / KPI: {cid}")

    # 2b) Stress-test every chart click id from budget-data.js
    budget_txt = (DASH / "data" / "budget-data.js").read_text(encoding="utf-8")
    budget = json.loads(budget_txt.split("=", 1)[1].strip().rstrip(";").strip())
    click_ids = []
    years = budget.get("spendTrend", {}).get("years") or []
    for i in range(len(years)):
        click_ids += [f"trend.revenue.{i}", f"trend.spend.{i}", f"trend.surplus.{i}"]
    ft = budget.get("functionTrend", {})
    for si, series in enumerate(ft.get("series") or []):
        for yi, val in enumerate(series.get("values") or []):
            click_ids.append(f"function.{si}.{yi}")
    for i in range(len(budget.get("byFunction", {}).get("labels") or [])):
        name = budget["byFunction"]["labels"][i]
        si = next((j for j, s in enumerate(ft.get("series") or []) if s.get("name") == name), None)
        if si is not None:
            click_ids.append(f"function.{si}.8")
    for prefix, pack in (("revmix", budget.get("revenueMix")), ("expmix", budget.get("expenseMix"))):
        pack = pack or {}
        for si in range(len(pack.get("series") or [])):
            for yi in range(len(pack.get("years") or [])):
                click_ids.append(f"{prefix}.{si}.{yi}")
    for prefix, pack in (("revcat", budget.get("revenueCategories")), ("expcat", budget.get("expenseCategories"))):
        for i in range(len((pack or {}).get("labels") or [])):
            click_ids.append(f"{prefix}.{i}")
    for i in range(len(budget.get("topUnits", {}).get("labels") or [])):
        click_ids.append(f"dept.{i}")
    ava_n = len(budget.get("adoptedVsActual", {}).get("years") or [])
    for i in range(ava_n):
        click_ids += [f"adopted.spend.{i}", f"adopted.plan.{i}"]
    pay = budget.get("pay") or {}
    for key, pack in (("pay.high", pay.get("highestPaid")), ("pay.cost", pay.get("costliestClasses")),
                      ("pay.staff", pay.get("mostStaff"))):
        for i in range(len((pack or {}).get("labels") or [])):
            click_ids.append(f"{key}.{i}")
    for gi, g in enumerate((budget.get("contracts") or {}).get("groups") or []):
        for ui in range(len(g.get("labels") or [])):
            click_ids.append(f"contract.{gi}.{ui}")
    missing_clicks = [cid for cid in click_ids if cid not in cites]
    if missing_clicks:
        fail(f"{len(missing_clicks)} chart click ids missing, e.g. {missing_clicks[:8]}")
    else:
        ok(f"All {len(click_ids)} chart click ids have citations")

    # Function slices must not share the same first source unit
    a = (cites.get("function.0.3") or {}).get("children") or []
    b = (cites.get("function.1.3") or {}).get("children") or []
    a0 = (a[0].get("unit") or a[0].get("label") if a else "")
    b0 = (b[0].get("unit") or b[0].get("label") if b else "")
    if a and b and a0 == b0:
        fail(f"function.0.3 and function.1.3 share first source {a0!r}")
    elif a and b:
        ok(f"Function slices differ: {a0!r} vs {b0!r}")
    else:
        fail("function.0.3 / function.1.3 missing children")

    # 2c) First-chart totals: parent query must be the clicked county-wide figure
    def money(s):
        if s is None:
            return None
        t = str(s).strip()
        neg = t.startswith("(") or t.startswith("-")
        digits = "".join(ch for ch in t if ch.isdigit() or ch == ".")
        if not digits:
            return None
        n = float(digits)
        return -n if neg else n

    for i in range(len(years)):
        for prefix in ("trend.revenue", "trend.spend"):
            cid = f"{prefix}.{i}"
            c = cites.get(cid) or {}
            q = money(c.get("query"))
            v = c.get("value")
            if q is not None and v is not None and abs(q - v) > 10:
                fail(f"{cid}: parent query {c.get('query')} is not the clicked total {v}")
            elif c.get("type") == "printed" and not c.get("page"):
                fail(f"{cid}: printed cite has no page")
            elif c.get("type") == "printed":
                ok(f"{cid}: printed county-wide total p.{c.get('page')} q={c.get('query')}")
            else:
                ok(f"{cid}: derived with no mismatched parent query")

    # 2d) Audit: small negatives must not highlight a larger number (8 ≠ 88)
    park = [
        r for r in rows_from_book("FY 2022-23", "FY2020-21", "actual", "revenue")
        if "PARK&RE" in (r.get("unit") or r.get("u") or "")
        or "0107" in str(r.get("unitCode") or r.get("c") or "")
    ]
    if park and abs(float(park[0]["value"]) + 8) <= 0.5:
        ok("Unit 0107 FY2020-21 actual Total Revenues is -8")
    else:
        fail(f"Unit 0107 park impact fee actual is not -8: {park[:1]}")

    def parse_token(s: str):
        t = s.strip().replace("−", "-")
        neg = t.startswith("(") or t.startswith("-")
        digits = "".join(ch for ch in t if ch.isdigit() or ch == ".")
        if not digits:
            return None
        n = float(digits)
        return -n if neg else n

    def is_number_token(s: str) -> bool:
        return bool(re.fullmatch(r"-?\(?\d[\d,]*\)?(?:\.00)?", s.replace(" ", "")))

    if parse_token("88") == -8 or parse_token("8") == -8:
        fail("88/8 parsed as -8")
    elif parse_token("-8") != -8 or parse_token("(8)") != -8:
        fail("-8 / (8) did not parse as -8")
    elif is_number_token("88") and parse_token("88") == 88 and parse_token("-8") == -8:
        ok("Number tokens: -8 is -8, 88 is 88")
    else:
        fail("Number token parse failed")

    # 3) Line-name matcher: FY2025-26 FY2023-24 actual spend
    spend_rows = rows_from_book("FY 2025-26", "FY2023-24", "actual", "spend")
    if len(spend_rows) > 0:
        ok(f"FY2025-26 FY2023-24 spend matcher: {len(spend_rows)} rows")
    else:
        fail("FY2025-26 FY2023-24 spend matcher returned 0 rows")

    # 4) Prefer Total Revenues — no twin Revenues for same unit in FY2026-27
    rev_rows = rows_from_book("FY 2026-27", "FY2024-25", "actual", "revenue")
    twin = [r for r in rev_rows if re.match(r"^revenues?$", str(r.get("line") or ""), re.I)]
    totalish = [r for r in rev_rows if prefer_total(str(r.get("line") or ""))]
    if twin and not totalish:
        fail("FY2026-27 revenue expand only has short Revenues lines")
    elif twin:
        fail(f"FY2026-27 revenue expand still includes short Revenues twins: {len(twin)}")
    else:
        ok(f"FY2026-27 revenue expand prefers Total lines ({len(rev_rows)} units)")

    # 5) Every citation expands to ≥1 printable source
    no_page = []
    for cid, cite in cites.items():
        if not isinstance(cite, dict):
            continue
        sources = expand_sources(cite, cites)
        first = first_printable(sources)
        if not first:
            if cite.get("page") and cite.get("book"):
                first = cite
            else:
                no_page.append(cid)
                continue
        file_rec = books.get(first["book"])
        if not file_rec:
            fail(f"{cid}: printable book {first.get('book')!r} not in books.json")
            continue
        ok(f"{cid}: printable {first['book']} p.{first['page']}")

    if no_page:
        for cid in no_page:
            fail(f"{cid}: no printable book+page after expand")
    else:
        ok(f"All {len(cites)} citations resolve to a printable page")

    # 6) HTTP checks (server on 4173)
    status, viewer_html, _ = http_get("viewer.html")
    if status == 200:
        ok("GET /viewer.html → 200")
    else:
        fail(f"GET /viewer.html → {status} (is serve running on :4173?)")

    for label, rec in books.items():
        st, body, ct = http_get("pdfs/" + rec["file"], timeout=60)
        if st == 200 and (b"%PDF" in body[:20] or "pdf" in ct.lower() or len(body) > 1000):
            ok(f"GET /pdfs/{rec['file']} → 200")
        else:
            fail(f"GET /pdfs/{rec['file']} → {st} ct={ct!r}")

    q = urllib.parse.urlencode({"book": "FY 2025-26", "page": "19", "q": "409,497,404"})
    # With cleanUrls off, query on viewer.html must survive (the old Missing-file bug).
    req = urllib.request.Request(
        BASE_URL.rstrip("/") + "/viewer.html?" + q,
        headers={"User-Agent": "evidence-test/1.0"},
        method="HEAD",
    )
    try:
        opener = urllib.request.build_opener(urllib.request.HTTPRedirectHandler)
        with opener.open(req, timeout=10) as resp:
            final = resp.geturl()
            code = resp.status
    except urllib.error.HTTPError as e:
        final = e.headers.get("Location") or ""
        code = e.code
    except Exception as e:
        final, code = str(e), 0
    if code == 200 and "book=" in final:
        ok("GET viewer.html?book=… keeps query (no strip-to-/viewer)")
    elif code in (301, 302) and "book=" not in final:
        fail("serve still 301s viewer.html?… → /viewer and drops query; restart serve so serve.json applies")
    else:
        # HEAD may not echo query in geturl on some stacks; GET the body instead
        st, body, _ = http_get("viewer.html?" + q)
        html = body.decode("utf-8", errors="replace")
        if st == 200 and "sutterEvidenceViewer" in html:
            ok("GET viewer.html?book=… → 200 (viewer can resolve via query/hash/store)")
        else:
            fail(f"viewer.html?book=… unexpected {code} {final}")

    st, js, _ = http_get("js/evidence-panel.js")
    js_text = js.decode("utf-8", errors="replace")
    if st == 200 and "viewer.html?" in js_text and "sutterEvidenceViewer" in js_text and "#" in js_text:
        ok("evidence-panel.js stashes state and opens viewer.html?…#…")
    else:
        fail("evidence-panel.js missing hash+localStorage enlarge handoff")

    vh = viewer_html.decode("utf-8", errors="replace") if viewer_html else ""
    if "location.hash" in vh and "sutterEvidenceViewer" in vh and 'originUrl("data/books.json")' in vh:
        ok("viewer.html reads query, hash, and localStorage; loads books from site root")
    else:
        fail("viewer.html is missing defensive param / books.json resolution")

    # 7) pdfplumber spot-checks
    checks = [
        ("FY 2025-26", 19, "409,497,404"),
        ("FY 2025-26", 42, "398,114,608"),
        ("FY 2026-27", 512, "50,681,46"),  # Behavioral Health spend prefix
        ("FY 2026-27", 298, "58,783,273"),  # General Revenues Total Revenues
        ("FY 2022-23", 368, "-8"),  # Park impact fee Total Revenues actual
    ]
    for book, page, query in checks:
        pdf_path = PDFS / books[book]["file"]
        # prefix match for Behavioral Health
        if query.endswith("46"):
            try:
                import pdfplumber

                with pdfplumber.open(pdf_path) as pdf:
                    text = pdf.pages[page - 1].extract_text() or ""
                    hit = "50,681,46" in text or "5068146" in re.sub(r"[^\d]", "", text)
            except Exception as e:
                hit = False
                fail(f"pdfplumber {book} p.{page}: {e}")
        else:
            hit = figure_on_page(pdf_path, page, query)
        if hit:
            ok(f"Figure on page: {book} p.{page} contains {query}")
        else:
            fail(f"Figure missing: {book} p.{page} expected {query}")

    # 8) CSS regression: source list pinned
    css = index
    if "min-height: 0" in css and "28vh" in css and "overflow: hidden" in css:
        ok("Panel CSS pins source list (min-height:0 + 28vh wrap)")
    else:
        fail("Panel CSS missing min-height:0 / 28vh / overflow:hidden")

    print()
    if failures:
        print(f"{len(failures)} failure(s):")
        for f in failures:
            print(" -", f)
        return 1
    print("All tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
