#!/usr/bin/env python3
"""Attach an exact word-box to every printed citation so the panel can mark it."""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DASH = ROOT / "dashboard" / "data"
PDFS = ROOT / "dashboard" / "pdfs"


def load_js(name: str) -> dict:
    text = (DASH / name).read_text(encoding="utf-8")
    return json.loads(text.split("=", 1)[1].strip().rstrip(";").strip())


def save_citations(cites: dict) -> None:
    (DASH / "citations.js").write_text(
        "window.CITATIONS = " + json.dumps(cites, indent=2) + ";\n",
        encoding="utf-8",
    )


def parse_money(s) -> float | None:
    t = str(s).strip().replace("−", "-").replace("–", "-")
    if not re.search(r"\d", t):
        return None
    neg = t.startswith("(") or t.startswith("-")
    n = re.sub(r"[^0-9.]", "", t)
    if n.count(".") > 1:
        n = n.replace(".", "")
    if not n or n == ".":
        return None
    try:
        v = float(n)
    except ValueError:
        return None
    return -v if neg else v


def word_hit(page, value: float) -> dict | None:
    want = int(round(value))
    best = None
    for w in page.extract_words() or []:
        n = parse_money(w.get("text"))
        if n is None or int(round(n)) != want:
            continue
        text = str(w.get("text") or "")
        score = 3 if "," in text else 1
        if "-" in text or text.startswith("("):
            score += 1
        cand = (score, -abs(w["x1"] - w["x0"]), w)
        if best is None or cand[0] > best[0] or (cand[0] == best[0] and cand[1] > best[1]):
            best = cand
    if not best:
        return None
    w = best[2]
    return {
        "page": page.page_number,
        "x0": round(float(w["x0"]), 2),
        "top": round(float(w["top"]), 2),
        "x1": round(float(w["x1"]), 2),
        "bottom": round(float(w["bottom"]), 2),
        "query": w["text"],
        "pageW": float(page.width),
        "pageH": float(page.height),
    }


def walk(obj, acc: list) -> None:
    if isinstance(obj, dict):
        if obj.get("book") and obj.get("page") and obj.get("value") is not None:
            acc.append(obj)
        for v in obj.values():
            walk(v, acc)
    elif isinstance(obj, list):
        for v in obj:
            walk(v, acc)


def main() -> None:
    import pdfplumber

    cites = load_js("citations.js")
    books = json.loads((DASH / "books.json").read_text(encoding="utf-8"))
    nodes: list[dict] = []
    walk(cites, nodes)

    by_book: dict[str, list[dict]] = {}
    for node in nodes:
        by_book.setdefault(node["book"], []).append(node)

    attached = 0
    moved = 0
    missing = 0
    for book, group in by_book.items():
        rec = books.get(book)
        if not rec:
            missing += len(group)
            continue
        path = PDFS / rec["file"]
        if not path.exists():
            missing += len(group)
            continue
        pages_needed: set[int] = set()
        for node in group:
            p = int(node["page"])
            pages_needed.update([p, p - 1, p + 1, p - 2, p + 2])
        with pdfplumber.open(path) as pdf:
            cache = {}
            n_pages = len(pdf.pages)
            for pno in sorted(p for p in pages_needed if 1 <= p <= n_pages):
                cache[pno] = pdf.pages[pno - 1]
            for node in group:
                start = int(node["page"])
                value = float(node["value"])
                order = [start] + [start + d for d in (1, -1, 2, -2)]
                hit = None
                for pno in order:
                    page = cache.get(pno)
                    if page is None:
                        continue
                    hit = word_hit(page, value)
                    if hit:
                        if pno != start:
                            node["page"] = pno
                            moved += 1
                        break
                if not hit:
                    missing += 1
                    continue
                node["hit"] = hit
                node["query"] = hit["query"]
                attached += 1
        print(f"  {book}: attached so far {attached}", flush=True)

    save_citations(cites)
    print(f"attached {attached} boxes, moved {moved} to a neighbor page, still missing {missing}")


if __name__ == "__main__":
    main()
