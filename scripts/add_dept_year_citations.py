#!/usr/bin/env python3
"""Add per-year / net / growth citations for the department table."""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DASH = ROOT / "dashboard" / "data"

ACTUAL_BOOK = {
    "FY2016-17": "FY 2018-19",
    "FY2020-21": "FY 2022-23",
    "FY2024-25": "FY 2026-27",
}

SPEND_RE = re.compile(r"^(total\s+)?expenditures?(?:\s+and\s+appropriations)?$", re.I)
REV_RE = re.compile(r"^(total\s+)?revenues?$", re.I)
NET_RE = re.compile(r"net\s+county\s+cost", re.I)


def load_js(name: str) -> dict:
    text = (DASH / name).read_text(encoding="utf-8")
    raw = text.split("=", 1)[1].strip().rstrip(";").strip()
    return json.loads(raw)


def save_citations(cites: dict) -> None:
    (DASH / "citations.js").write_text(
        "window.CITATIONS = " + json.dumps(cites, indent=2) + ";\n",
        encoding="utf-8",
    )


def load_lines(book: str) -> list[dict]:
    path = DASH / "lines" / (book.replace(" ", "_") + ".json")
    return json.loads(path.read_text(encoding="utf-8")).get("rows") or []


def prefer_total(line: str) -> bool:
    return bool(re.match(r"^total\s+", line or "", re.I))


def match_unit(row: dict, code: str, name: str) -> bool:
    c = re.sub(r"\D", "", str(row.get("c") or ""))
    want = re.sub(r"\D", "", code or "")
    # Code wins. Never fuzzy-match "Behavioral Health" onto "Behavioral Health Subaccount".
    if want:
        return c == want or c.endswith(want)
    u = re.sub(r"\s+", " ", (row.get("u") or "").lower()).strip()
    n = re.sub(r"\s+", " ", (name or "").lower()).strip()
    return bool(n) and u == n


def find_line(book: str, fy: str, kind: str, code: str, name: str, matcher) -> dict | None:
    rows = [
        r
        for r in load_lines(book)
        if r.get("f") == fy
        and r.get("k") == kind
        and match_unit(r, code, name)
        and matcher(str(r.get("l") or ""))
        and abs(float(r.get("v") or 0)) >= 1
    ]
    if not rows:
        return None
    rows.sort(key=lambda r: (0 if prefer_total(r.get("l")) else 1, -abs(float(r.get("v") or 0))))
    r = rows[0]
    v = float(r["v"])
    return {
        "type": "printed",
        "book": book,
        "page": r.get("p"),
        "value": v,
        "query": f"{int(round(v)):,}" if v >= 0 else f"-{int(round(abs(v))):,}",
        "unit": r.get("u") or name,
        "unitCode": r.get("c") or code,
        "line": r.get("l"),
        "fy": fy,
        "kind": kind,
        "label": f"{r.get('u') or name} · {r.get('l')}",
    }


def main() -> None:
    budget = load_js("budget-data.js")
    cites = load_js("citations.js")
    depts = budget.get("departments") or []
    added = 0
    for i, d in enumerate(depts):
        name, code = d.get("name") or "", d.get("code") or ""
        specs = [
            ("fy16", "FY2016-17", d.get("fy16"), "spend"),
            ("fy20", "FY2020-21", d.get("fy20"), "spend"),
            ("fy24", "FY2024-25", d.get("fy24"), "spend"),
        ]
        year_cites = {}
        for suffix, fy, displayed, metric in specs:
            book = ACTUAL_BOOK[fy]
            matcher = SPEND_RE.match if metric == "spend" else REV_RE.match
            found = find_line(book, fy, "actual", code, name, matcher)
            if not found:
                cites[f"dept.{i}.{suffix}"] = {
                    "type": "derived",
                    "label": f"{name} · {fy} actual spending",
                    "value": displayed,
                    "formula": (
                        f"This unit ({code or name}) does not appear in the {book} "
                        f"actuals for {fy}."
                        if not displayed else
                        f"Unit {code or name} spending for {fy} from the {book} book."
                    ),
                    "book": book,
                    "page": None,
                    "query": None,
                    "children": [],
                    "unit": name,
                    "unitCode": code,
                    "fy": fy,
                    "kind": "actual",
                    "metric": "spend",
                }
            else:
                found["label"] = f"{name} · {fy} actual spending"
                found["metric"] = "spend"
                found["children"] = []
                cites[f"dept.{i}.{suffix}"] = found
            year_cites[suffix] = cites[f"dept.{i}.{suffix}"]
            added += 1

        net_found = find_line(
            ACTUAL_BOOK["FY2024-25"], "FY2024-25", "actual", code, name, NET_RE.match
        )
        if net_found:
            net_found["label"] = f"{name} · FY 2024-25 net county cost"
            net_found["metric"] = "net"
            net_found["children"] = []
            cites[f"dept.{i}.net24"] = net_found
        else:
            spend = year_cites["fy24"]
            rev = find_line(
                ACTUAL_BOOK["FY2024-25"], "FY2024-25", "actual", code, name, REV_RE.match
            )
            cites[f"dept.{i}.net24"] = {
                "type": "derived",
                "label": f"{name} · FY 2024-25 net county cost",
                "value": d.get("net24"),
                "formula": "Spending minus that unit’s own program revenue.",
                "book": ACTUAL_BOOK["FY2024-25"],
                "page": (rev or spend).get("page") if (rev or spend) else None,
                "query": None,
                "children": [x for x in (spend, rev) if x],
                "unit": name,
                "unitCode": code,
                "fy": "FY2024-25",
                "kind": "actual",
                "metric": "net",
            }
        added += 1

        a, b = year_cites["fy16"], year_cites["fy24"]
        cites[f"dept.{i}.growth"] = {
            "type": "derived",
            "label": f"{name} · growth FY 2016-17 to FY 2024-25",
            "value": (d.get("fy24") or 0) - (d.get("fy16") or 0),
            "formula": (
                f"{int(round(d.get('fy24') or 0)):,} minus "
                f"{int(round(d.get('fy16') or 0)):,}."
            ),
            "book": b.get("book"),
            "page": None,
            "query": None,
            "children": [a, b],
            "unit": name,
            "unitCode": code,
            "metric": "growth",
        }
        added += 1

        # Keep the bar-chart id on the latest year.
        cites[f"dept.{i}"] = year_cites["fy24"]
        cites[f"unit.{i}"] = year_cites["fy24"]

    save_citations(cites)
    print(f"added/updated {added} department-year citations for {len(depts)} units")


if __name__ == "__main__":
    main()
