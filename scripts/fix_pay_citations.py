#!/usr/bin/env python3
"""Point every pay click at the printed salary / FTE token, not the FTE roster."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from rebuild_chart_citations import build_pay_cites  # type: ignore

DASH = ROOT / "dashboard" / "data"


def load(name: str) -> dict:
    text = (DASH / name).read_text(encoding="utf-8")
    return json.loads(text.split("=", 1)[1].strip().rstrip(";").strip())


def save(cites: dict) -> None:
    (DASH / "citations.js").write_text(
        "window.CITATIONS = " + json.dumps(cites, indent=2) + ";\n",
        encoding="utf-8",
    )


def main() -> None:
    cites = load("citations.js")
    budget = load("budget-data.js")
    books = json.loads((DASH / "books.json").read_text(encoding="utf-8"))
    build_pay_cites(cites, budget, books)
    save(cites)
    print("pay citations rewritten")


if __name__ == "__main__":
    main()
