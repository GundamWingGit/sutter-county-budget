/**
 * Click-to-source evidence panel.
 *
 * Printed numbers: jump to the page and highlight that same figure.
 * Calculated numbers: explain the math in plain English, list every
 * contributing source, and highlight only the piece the user picked —
 * never a different number pretending to be the one they clicked.
 */
(function (global) {
  "use strict";

  const PDFJS_CDN = "https://cdn.jsdelivr.net/npm/pdfjs-dist@4.4.168/build";
  let pdfjsLib = null;
  let books = null;
  let currentPdf = null;
  let currentBook = null;
  let currentPage = 1;
  let currentCite = null;
  let viewingPiece = null; // the printed row currently on screen
  let allSources = [];
  let lastHighlight = null;
  let lastRenderOpts = null;
  let highlightOn = true;
  let renderToken = 0;
  let openGen = 0;
  let expectPdf = false;
  const lineCache = {};
  const pdfCache = new Map(); // book -> { promise, doc }
  const MAX_CACHED_PDFS = 4;
  const PANEL_MIN = 360;
  const PANEL_MAX_RATIO = 0.92;

  function $(id) { return document.getElementById(id); }

  function fmtFull(v) {
    if (v == null || Number.isNaN(Number(v))) return "—";
    const n = Number(v);
    const sign = n < 0 ? "−" : "";
    return sign + "$" + Math.round(Math.abs(n)).toLocaleString("en-US");
  }

  function valuesClose(a, b) {
    if (a == null || b == null) return false;
    return Math.abs(Number(a) - Number(b)) <= 10;
  }

  function parseMoney(s) {
    if (s == null || s === "") return null;
    if (typeof s === "number") return s;
    const t = String(s).trim();
    const neg = /^\(.*\)$/.test(t) || t.startsWith("-");
    const n = Number(t.replace(/[^0-9.]/g, ""));
    if (Number.isNaN(n)) return null;
    return neg ? -n : n;
  }

  function queryMatchesValue(query, value) {
    const q = parseMoney(query);
    if (q == null || value == null) return false;
    return valuesClose(q, value);
  }

  function escapeHtml(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, c => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
    }[c]));
  }

  function formatQueryFromValue(v) {
    const n = Number(v);
    if (Number.isNaN(n)) return String(v);
    if (n < 0) return "(" + Math.round(Math.abs(n)).toLocaleString("en-US") + ")";
    return Math.round(n).toLocaleString("en-US");
  }

  async function ensurePdfJs() {
    if (pdfjsLib) return pdfjsLib;
    pdfjsLib = await import(PDFJS_CDN + "/pdf.min.mjs");
    pdfjsLib.GlobalWorkerOptions.workerSrc = PDFJS_CDN + "/pdf.worker.min.mjs";
    return pdfjsLib;
  }

  async function loadBooks() {
    if (books) return books;
    books = await (await fetch("data/books.json")).json();
    return books;
  }

  function fmtBytes(n) {
    if (!n) return "";
    if (n < 1024 * 1024) return Math.round(n / 1024) + " KB";
    return (n / (1024 * 1024)).toFixed(1) + " MB";
  }

  function showLoading(text, loaded, total) {
    expectPdf = true;
    const wrap = $("evidenceLoading");
    const label = $("evidenceLoadingText");
    const pct = $("evidenceLoadingPct");
    const bar = $("evidenceProgressBar");
    const track = $("evidenceProgress");
    if (wrap) wrap.hidden = false;
    if (label && text) label.textContent = text;
    if (total > 0 && loaded >= 0) {
      const p = Math.min(100, Math.round((loaded / total) * 100));
      if (track) track.classList.remove("indeterminate");
      if (bar) bar.style.width = p + "%";
      if (pct) pct.textContent = p + "% · " + fmtBytes(loaded) + " of " + fmtBytes(total);
    } else {
      if (track) track.classList.add("indeterminate");
      if (bar) bar.style.width = "40%";
      if (pct) pct.textContent = "Fetching the page…";
    }
  }

  function hideLoading() {
    expectPdf = false;
    const wrap = $("evidenceLoading");
    if (wrap) wrap.hidden = true;
  }

  function touchPdfCache(book) {
    const ent = pdfCache.get(book);
    if (!ent) return;
    pdfCache.delete(book);
    pdfCache.set(book, ent);
    while (pdfCache.size > MAX_CACHED_PDFS) {
      const oldest = pdfCache.keys().next().value;
      if (oldest === book || oldest === currentBook) break;
      const evict = pdfCache.get(oldest);
      pdfCache.delete(oldest);
      try { evict && evict.doc && evict.doc.destroy(); } catch (_) {}
    }
  }

  async function prefetchPdf(book) {
    if (!book) return null;
    await Promise.all([loadBooks(), ensurePdfJs()]);
    const url = pdfUrl(book);
    if (!url) return null;
    if (pdfCache.has(book)) {
      touchPdfCache(book);
      return pdfCache.get(book).promise;
    }
    const task = pdfjsLib.getDocument({
      url,
      withCredentials: false,
      disableRange: false,
      disableStream: false,
      disableAutoFetch: true,
    });
    task.onProgress = (ev) => {
      if (!expectPdf) return;
      if (currentBook && currentBook !== book && currentPdf) return;
      showLoading("Opening the " + book + " book…", ev.loaded, ev.total);
    };
    const promise = task.promise.then((doc) => {
      const ent = pdfCache.get(book);
      if (ent) ent.doc = doc;
      warmHttpCache(url);
      return doc;
    }).catch((err) => {
      pdfCache.delete(book);
      throw err;
    });
    pdfCache.set(book, { promise, doc: null });
    return promise;
  }

  function warmHttpCache(url) {
    if (!url || !("caches" in window)) return;
    caches.open("sutter-pdfs-v1").then(async (cache) => {
      if (await cache.match(url)) return;
      await cache.add(url);
    }).catch(() => {});
  }

  function prefetchById(id) {
    const cite = (global.CITATIONS || {})[id];
    if (!cite || !cite.book) return;
    prefetchPdf(cite.book).catch(() => {});
  }

  function warmLibraries() {
    const idle = global.requestIdleCallback || ((fn) => setTimeout(fn, 400));
    idle(() => {
      ensurePdfJs().catch(() => {});
      loadBooks().catch(() => {});
    });
  }

  const VIEWER_STORE = "sutterEvidenceViewer";

  function pdfUrl(bookLabel) {
    if (!books || !bookLabel) return null;
    const b = books[bookLabel];
    if (!b || !b.file) return null;
    return "pdfs/" + b.file;
  }

  function hydrateCite(partial) {
    if (!partial) return partial;
    const cites = global.CITATIONS || {};
    if (partial.id && cites[partial.id] && partial.id !== "_meta") {
      const full = cites[partial.id];
      return { ...full, ...partial, children: partial.children || full.children || [] };
    }
    if (partial.formula && Array.isArray(partial.children) && partial.children.length) {
      return partial;
    }
    const match = Object.values(cites).find(c =>
      c && typeof c === "object" && !Array.isArray(c) &&
      c !== cites._meta &&
      c.label &&
      c.label === partial.label &&
      (partial.value == null || c.value === partial.value)
    );
    if (!match) return partial;
    return { ...match, ...partial, children: partial.children || match.children || [] };
  }

  function isRevenueLine(line) {
    const l = String(line || "").trim();
    return /^(total\s+)?revenues?$/i.test(l);
  }

  function isSpendLine(line) {
    const l = String(line || "").trim();
    return /^(total\s+)?expenditures?(?:\s+and\s+appropriations)?$/i.test(l);
  }

  function preferTotalLine(line) {
    const l = String(line || "").trim();
    return /^total\s+/i.test(l);
  }

  function metricFromChild(kid) {
    if (!kid) return null;
    if (kid.metric === "revenue" || kid.metric === "spend") return kid.metric;
    const label = (kid.label || "") + " " + (kid.line || "") + " " + (kid.formula || "");
    if (/revenue/i.test(label) && !/spend|expend/i.test(label)) return "revenue";
    if (/spend|expend/i.test(label)) return "spend";
    return kid.metric || null;
  }

  function setOpen(open) {
    document.body.classList.toggle("evidence-open", open);
    const panel = $("evidencePanel");
    if (panel) panel.setAttribute("aria-hidden", open ? "false" : "true");
  }

  function showStatus(msg, isErr) {
    const el = $("evidenceStatus");
    if (!el) return;
    el.textContent = msg || "";
    el.classList.toggle("err", !!isErr);
  }

  function plainExplainer(cite) {
    const v = fmtFull(cite.value);
    const metric = cite.metric || "";
    const label = cite.label || "";
    const kids = cite.children || [];

    if (cite.type === "printed") {
      return {
        title: "Printed in the source book",
        body: "The figure below is the same amount on this page.",
        equation: null,
      };
    }

    if (metric === "surplus" || /surplus/i.test(label)) {
      const rev = kids.find(k => /revenue/i.test(k.label || k.metric || "")) || kids[0];
      const exp = kids.find(k => /spend|expend/i.test(k.label || k.metric || "")) || kids[1];
      return {
        title: "Revenue minus spending",
        body: "This surplus is not printed as one line. It is the difference of the two source figures.",
        equation: (rev && exp)
          ? `${fmtFull(rev.value)} − ${fmtFull(exp.value)} = ${v}`
          : cite.formula,
      };
    }

    if (metric === "revenue") {
      if (cite.type === "printed") {
        return {
          title: "Printed county-wide total",
          body: "This is the same governmental-fund revenue total you clicked, as printed in the source book.",
          equation: null,
        };
      }
      return {
        title: "County-wide total is a sum",
        body:
          "You clicked " + v + ". That county-wide revenue total is not printed as one figure in this book. " +
          "The sources below are each unit’s Total Revenues; they add to the number you clicked. " +
          "Pick a source to see that unit’s printed line — the highlight will then match that source, not the chart total.",
        equation: null,
      };
    }

    if (metric === "spend") {
      if (cite.type === "printed") {
        return {
          title: "Printed county-wide total",
          body: "This is the same governmental-fund spending total you clicked, as printed in the source book.",
          equation: null,
        };
      }
      return {
        title: "County-wide total is a sum",
        body:
          "You clicked " + v + ". That county-wide spending total is not printed as one figure in this book. " +
          "The sources below are each unit’s Total Expenditures; they add to the number you clicked. " +
          "Pick a source to see that unit’s printed line — the highlight will then match that source, not the chart total.",
        equation: null,
      };
    }

    if (metric === "function" || /function/i.test(cite.formula || "")) {
      const fn = cite.function || (label.split("—")[0] || "").trim();
      const book = cite.book || "";
      const fy = cite.fy || "";
      const printed = kids.filter(k => k.page && k.value != null);
      const eq = printed.length
        ? printed.slice(0, 6).map(k => fmtFull(k.value)).join(" + ")
          + (printed.length > 6 ? " + …" : "")
          + " = " + v
        : cite.formula;
      return {
        title: "Sum of units in this function",
        body:
          `${fn || "This function"} is a State Controller grouping, not one inked county-wide figure. ` +
          `${fy} actuals are printed in the ${book} book (closed-year actuals appear two years later). ` +
          `Each source below is that unit’s Total Expenditures. The highlight is one unit, not the stack total.`,
        equation: eq,
      };
    }

    if (metric === "category") {
      return {
        title: "Sum of unit category lines",
        body:
          `${cite.category || "This category"} is rolled up from unit lines in the ${cite.book || "source"} book. ` +
          `The highlight is one contributing line, not the chart total.`,
        equation: kids.length
          ? kids.slice(0, 6).map(k => fmtFull(k.value)).join(" + ")
            + (kids.length > 6 ? " + …" : "") + " = " + v
          : cite.formula,
      };
    }

    if (metric === "pay") {
      return {
        title: "Position allocation schedule",
        body: "This figure comes from the FY 2025-26 Position Allocation Schedule (Section J), not Schedule 9 unit totals.",
        equation: null,
      };
    }

    if (metric === "contract") {
      return {
        title: "Contract line in a budget unit",
        body: "This is a professional-services / contract object in that unit’s budget, not the unit’s total spending.",
        equation: null,
      };
    }

    if (/cumulative/i.test(label)) {
      return {
        title: "Sum of annual surpluses",
        body: "Each year is revenue minus spending. This figure is those nine results added together.",
        equation: kids.length
          ? kids.map(k => fmtFull(k.value)).join(" + ") + " = " + v
          : cite.formula,
      };
    }

    if (/draw|planned/i.test(label)) {
      const rev = kids.find(k => /revenue/i.test(k.label || "")) || kids[0];
      const exp = kids.find(k => /spend/i.test(k.label || "")) || kids[1];
      return {
        title: "Adopted revenue minus adopted spending",
        body: "A planned draw, not a closed-year result.",
        equation: (rev && exp)
          ? `${fmtFull(rev.value)} − ${fmtFull(exp.value)} = ${v}`
          : cite.formula,
      };
    }

    return {
      title: "Derived from source rows",
      body: cite.formula || "Combined from more than one printed figure.",
      equation: cite.formula || null,
    };
  }

  function pieceLabel(piece) {
    return piece.label || piece.line || piece.unit || "Printed source";
  }

  function renderCaption(cite, piece) {
    const el = $("evidenceCaption");
    if (!el) return;
    if (!piece || !piece.page) {
      el.hidden = true;
      el.textContent = "";
      return;
    }
    el.hidden = false;
    const pieceVal = piece.value != null ? Number(piece.value) : null;
    const clicked = cite.value != null ? Number(cite.value) : null;
    const same = valuesClose(pieceVal, clicked);

    if (cite.type === "printed" && same) {
      el.className = "evidence-caption";
      el.innerHTML = `Highlight: <strong>${fmtFull(clicked)}</strong>`;
      return;
    }
    if (cite.type === "printed" && !same && pieceVal != null) {
      el.className = "evidence-caption warn";
      el.innerHTML =
        `Clicked <strong>${fmtFull(clicked)}</strong>; page shows <strong>${fmtFull(pieceVal)}</strong> ` +
        `(${escapeHtml(pieceLabel(piece))}).`;
      return;
    }
    el.className = "evidence-caption";
    el.innerHTML =
      `Highlight is the source row <strong>${fmtFull(pieceVal)}</strong>, not the calculated ${fmtFull(clicked)}.`;
  }

  function renderHeader(cite) {
    const type = (cite && cite.type) || "printed";
    const badge = $("evidenceBadge");
    badge.textContent = type === "printed" ? "Printed" : "Calculated";
    badge.className = "evidence-badge " + (type === "printed" ? "printed" : "derived");

    $("evidenceTitle").textContent = (cite && cite.label) || "Source evidence";
    $("evidenceClicked").textContent = cite && cite.value != null ? fmtFull(cite.value) : "";

    const exp = plainExplainer(cite || {});
    $("evidenceExplainTitle").textContent = exp.title;
    $("evidenceExplainBody").textContent = exp.body;
    const eq = $("evidenceEq");
    if (exp.equation) {
      eq.hidden = false;
      eq.textContent = exp.equation;
    } else {
      eq.hidden = true;
      eq.textContent = "";
    }

    const bits = [];
    if (cite && cite.book) bits.push(cite.book);
    if (viewingPiece && viewingPiece.page) bits.push("showing page " + viewingPiece.page);
    $("evidenceMeta").textContent = bits.join(" · ");
    renderCaption(cite, viewingPiece);
  }

  function renderSourceList(cite, filter) {
    const list = $("evidenceChildren");
    const qBox = $("evidenceSourceQ");
    const sumEl = $("evidenceSourceSum");
    const labelEl = $("evidenceChildrenLabel");
    if (!list) return;

    const q = (filter || "").trim().toLowerCase();
    const sources = allSources.filter(s => {
      if (!q) return true;
      const hay = ((s.label || "") + " " + (s.unit || "") + " " + (s.line || "") + " " + (s.group || "")).toLowerCase();
      return hay.includes(q);
    });

    if (qBox) qBox.hidden = allSources.length < 8;

    if (!allSources.length) {
      labelEl.textContent = "";
      sumEl.textContent = "";
      list.innerHTML = "";
      return;
    }

    const isPieces = cite.type === "derived";
    const additive = isPieces && (
      cite.metric === "revenue" || cite.metric === "spend" ||
      cite.metric === "function" || cite.metric === "category" ||
      /cumulative/i.test(cite.label || "")
    );
    labelEl.textContent = isPieces
      ? `Sources (${allSources.length})`
      : "Related rows";

    if (additive && allSources.every(s => s.value != null) && !allSources.some(s => s.group)) {
      const sum = allSources.reduce((a, s) => a + Number(s.value || 0), 0);
      sumEl.textContent = valuesClose(sum, cite.value)
        ? `These unit lines add to ${fmtFull(sum)} — the figure you clicked.`
        : `These unit lines add to ${fmtFull(sum)} (clicked total: ${fmtFull(cite.value)}).`;
    } else {
      sumEl.textContent = "";
    }

    list.innerHTML = "";
    sources.forEach((kid) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "evidence-child" + (viewingPiece && viewingPiece.page === kid.page &&
        valuesClose(viewingPiece.value, kid.value) &&
        (viewingPiece.unit || "") === (kid.unit || "") ? " active" : "");
      const group = kid.group ? kid.group + " · " : "";
      btn.innerHTML =
        `<span class="ec-label">${escapeHtml(pieceLabel(kid))}</span>` +
        `<span class="ec-meta">${group}${kid.book || ""}${kid.page ? " · p." + kid.page : ""}` +
        `${kid.value != null ? " · " + fmtFull(kid.value) : ""}</span>`;
      btn.addEventListener("pointerenter", () => {
        if (kid.book) prefetchPdf(kid.book).catch(() => {});
      });
      btn.addEventListener("click", () => {
        if (kid.page && kid.book) showPiece(cite, kid, ++openGen);
        else openCitation(kid);
      });
      list.appendChild(btn);
    });
  }

  function dedupeSources(rows) {
    const by = {};
    rows.forEach(r => {
      const key = (r.unitCode || r.unit || r.label || "") + "|" +
        (r.group || "") + "|" + Math.round(Number(r.value) || 0);
      const prev = by[key];
      if (!prev) {
        by[key] = r;
        return;
      }
      const prevTotal = preferTotalLine(prev.line);
      const nextTotal = preferTotalLine(r.line);
      if (nextTotal && !prevTotal) by[key] = r;
      else if (nextTotal === prevTotal && (r.page || 0) >= (prev.page || 0)) by[key] = r;
      else if (!nextTotal && !prevTotal && (r.page || 0) >= (prev.page || 0)) by[key] = r;
    });
    return Object.values(by).sort((a, b) => Math.abs(b.value || 0) - Math.abs(a.value || 0));
  }

  function rowsFromBookLines(data, book, fy, kind, metric, group) {
    const found = [];
    const seenUnits = {};
    const candidates = [];
    (data.rows || []).forEach(r => {
      if (r.f !== fy || r.k !== (kind || "actual")) return;
      const line = String(r.l || "").trim();
      if (metric === "revenue" && !isRevenueLine(line)) return;
      if (metric === "spend" && !isSpendLine(line)) return;
      if (Math.abs(Number(r.v)) < 1) return;
      candidates.push(r);
    });
    // Prefer "Total …" lines; skip short Revenues/Expenditures twins per unit.
    candidates.sort((a, b) => {
      const at = preferTotalLine(a.l) ? 0 : 1;
      const bt = preferTotalLine(b.l) ? 0 : 1;
      if (at !== bt) return at - bt;
      return Math.abs(b.v) - Math.abs(a.v);
    });
    candidates.forEach(r => {
      const unitKey = r.c || r.u || "";
      if (seenUnits[unitKey]) return;
      seenUnits[unitKey] = true;
      found.push({
        type: "printed",
        book,
        page: r.p,
        value: r.v,
        query: formatQueryFromValue(r.v),
        unit: r.u,
        unitCode: r.c,
        line: r.l,
        label: (r.u || r.c || "Unit") + " — " + r.l,
        fy: r.f,
        kind: r.k,
        group: group || null,
      });
    });
    return found;
  }

  async function expandMetricRows(book, fy, kind, metric, group) {
    if (!book || !fy || !metric) return [];
    try {
      const data = await loadBookLines(book);
      return rowsFromBookLines(data, book, fy, kind, metric, group);
    } catch (_) {
      return [];
    }
  }

  async function expandSources(cite) {
    const hydratedKids = (cite.children || []).map(hydrateCite);
    const baked = dedupeSources(hydratedKids);
    if (cite.type === "printed") return baked;

    const metric = cite.metric;
    const label = cite.label || "";

    // Function / category / pay / contract: use the units baked for THAT slice.
    if (metric === "function" || metric === "category" || metric === "pay" || metric === "contract") {
      const printable = baked.filter(s => s.page && s.book);
      return printable.length ? printable : baked;
    }

    // County-wide revenue / spend: load every unit total line.
    if (metric === "revenue" || metric === "spend") {
      if (!cite.book || !cite.fy) return baked;
      const found = await expandMetricRows(
        cite.book, cite.fy, cite.kind || "actual", metric, null
      );
      const all = dedupeSources(found);
      return all.length ? all : baked;
    }

    // Surplus / planned draw: expand both revenue and spend children into unit rows.
    if (metric === "surplus" || /surplus|draw|planned/i.test(label)) {
      const kids = hydratedKids.length ? hydratedKids : baked;
      const revKid = kids.find(k => metricFromChild(k) === "revenue") || kids[0];
      const expKid = kids.find(k => metricFromChild(k) === "spend") || kids[1];
      const out = [];
      if (revKid) {
        const rows = await expandMetricRows(
          revKid.book || cite.book,
          revKid.fy || cite.fy,
          revKid.kind || cite.kind || "actual",
          "revenue",
          "Revenue"
        );
        if (rows.length) out.push(...rows);
        else if (revKid.page && revKid.book) out.push({ ...revKid, group: "Revenue" });
        else out.push({ ...revKid, group: "Revenue" });
      }
      if (expKid) {
        const rows = await expandMetricRows(
          expKid.book || cite.book,
          expKid.fy || cite.fy,
          expKid.kind || cite.kind || "actual",
          "spend",
          "Spending"
        );
        if (rows.length) out.push(...rows);
        else if (expKid.page && expKid.book) out.push({ ...expKid, group: "Spending" });
        else out.push({ ...expKid, group: "Spending" });
      }
      const all = dedupeSources(out);
      return all.length ? all : baked;
    }

    // Cumulative: keep yearly surplus rows, but attach a printable page from each year.
    if (/cumulative/i.test(label)) {
      const out = [];
      for (const kid of hydratedKids) {
        const full = hydrateCite(kid);
        let pagePiece = null;
        if (full.page && full.book) {
          pagePiece = full;
        } else {
          const subKids = (full.children || []).map(hydrateCite);
          const rev = subKids.find(k => metricFromChild(k) === "revenue") || subKids[0];
          if (rev && rev.book && rev.fy) {
            const rows = await expandMetricRows(
              rev.book, rev.fy, rev.kind || "actual", "revenue", null
            );
            pagePiece = rows[0] || null;
          }
          if (!pagePiece) {
            pagePiece = subKids.find(s => s.page && s.book) || null;
          }
        }
        out.push({
          ...full,
          type: full.type || "derived",
          label: full.label || kid.label,
          value: full.value != null ? full.value : kid.value,
          book: (pagePiece && pagePiece.book) || full.book || kid.book,
          page: (pagePiece && pagePiece.page) || full.page || null,
          query: (pagePiece && (pagePiece.query || formatQueryFromValue(pagePiece.value))) || full.query || null,
          hit: (pagePiece && pagePiece.hit) || full.hit || null,
          unit: (pagePiece && pagePiece.unit) || full.unit,
          line: (pagePiece && pagePiece.line) || full.line,
          group: "Year",
        });
      }
      return out.length ? out : baked;
    }

    // Other derived cites: if baked kids lack pages, try expanding any revenue/spend child.
    if (baked.some(s => s.page && s.book)) return baked;
    const out = [];
    for (const kid of hydratedKids) {
      const m = metricFromChild(kid);
      if ((m === "revenue" || m === "spend") && (kid.book || cite.book) && (kid.fy || cite.fy)) {
        const rows = await expandMetricRows(
          kid.book || cite.book,
          kid.fy || cite.fy,
          kid.kind || cite.kind || "actual",
          m,
          m === "revenue" ? "Revenue" : "Spending"
        );
        if (rows.length) {
          out.push(...rows);
          continue;
        }
      }
      out.push(kid);
    }
    const all = dedupeSources(out);
    if (all.length) return all;
    if (baked.length) return baked;
    // Derived with a located schedule page but no unit children (e.g. function.*.7).
    if (cite.page && cite.book) {
      return [{
        type: "printed",
        book: cite.book,
        page: cite.page,
        value: cite.value,
        query: cite.query || formatQueryFromValue(cite.value),
        hit: cite.hit,
        label: cite.label,
        unit: cite.unit,
        line: cite.line,
      }];
    }
    return baked;
  }

  async function showPiece(cite, piece, gen) {
    viewingPiece = piece;
    renderHeader(cite);
    renderSourceList(cite, ($("evidenceSourceQ") || {}).value);

    if (!piece || !piece.book || !books[piece.book] || !piece.page) {
      hideLoading();
      showStatus("This piece has no page yet. Try another row.", true);
      return;
    }

    const alreadyOpen = currentBook === piece.book && currentPdf;
    if (!alreadyOpen) {
      showLoading("Opening the " + piece.book + " book…");
    }
    try {
      const doc = alreadyOpen ? currentPdf : await prefetchPdf(piece.book);
      if (gen != null && gen !== openGen) return;
      currentPdf = doc;
      currentBook = piece.book;
      currentPage = Math.min(Math.max(1, piece.page), currentPdf.numPages);
      $("evidencePageLabel").textContent = `p. ${currentPage} of ${currentPdf.numPages}`;
      $("evidenceDocLink").href = viewerUrl(piece.book, currentPage, piece.query || formatQueryFromValue(piece.value));
      $("evidenceDocLink").textContent = "Open page";

      await renderPage(currentPage, {
        query: piece.query || formatQueryFromValue(piece.value),
        hit: piece.hit,
        unit: piece.unit,
        line: piece.line,
      });
      if (gen != null && gen !== openGen) return;
      hideLoading();
      showStatus("Click the page to open a sharper view. Use Highlight to toggle the box.");
    } catch (e) {
      if (gen != null && gen !== openGen) return;
      console.error(e);
      hideLoading();
      showStatus("Could not load that PDF page. Serve this folder over HTTP (npx --yes serve .).", true);
    }
  }

  async function openCitation(cite) {
    if (!cite) return;
    cite = hydrateCite(cite);
    const gen = ++openGen;

    currentCite = cite;
    viewingPiece = null;
    lastHighlight = null;
    setOpen(true);
    renderHeader(cite);
    showLoading(cite.book ? "Opening the " + cite.book + " book…" : "Opening the source book…");
    showStatus("Finding the printed page…");

    try {
      await Promise.all([loadBooks(), ensurePdfJs()]);
    } catch (e) {
      if (gen !== openGen) return;
      hideLoading();
      showStatus("Could not load PDF.js. Serve this site over HTTP (npx serve dashboard).", true);
      return;
    }
    if (gen !== openGen) return;

    // Start the book download immediately — don't wait on the source list.
    if (cite.book) prefetchPdf(cite.book).catch(() => {});

    allSources = await expandSources(cite);
    if (gen !== openGen) return;
    renderSourceList(cite, "");

    const clickedQuery = formatQueryFromValue(cite.value);
    const parentQuery = queryMatchesValue(cite.query, cite.value) ? cite.query : clickedQuery;
    const canHighlightClicked = !!(
      cite.book && cite.page && cite.value != null && queryMatchesValue(parentQuery, cite.value)
    );

    // Only highlight when the page figure is the same number the user clicked.
    if (canHighlightClicked) {
      viewingPiece = {
        ...cite,
        query: parentQuery,
        label: cite.label,
        value: cite.value,
      };
      await showPiece(cite, viewingPiece, gen);
      return;
    }

    // Never open a feeder row as if it were the clicked total.
    hideLoading();
    showStatus(
      "The number you clicked is not printed as one figure. Sources below add up to it — click a row to highlight that printed line.",
      false
    );
    clearCanvas();
    $("evidencePageLabel").textContent = "—";
    $("evidenceDocLink").href = "#";
    $("evidenceDocLink").textContent = "Open page";
    if (cite.book && books[cite.book]) {
      $("evidenceDocLink").href = viewerUrl(cite.book, null, clickedQuery);
      $("evidenceDocLink").textContent = books[cite.book].title || cite.book;
    }
    renderHeader(cite);
  }

  function clearCanvas() {
    const canvas = $("evidenceCanvas");
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    canvas.width = 1;
    canvas.height = 1;
    ctx.clearRect(0, 0, 1, 1);
  }

  function viewerParams(book, page, query) {
    const params = new URLSearchParams();
    if (book) params.set("book", book);
    const path = pdfUrl(book);
    if (path) params.set("pdf", path);
    if (page) params.set("page", String(page));
    if (query) params.set("q", query);
    params.set("hl", highlightOn ? "1" : "0");
    return params;
  }

  function viewerUrl(book, page, query) {
    const qs = viewerParams(book, page, query).toString();
    // Query + hash: if a host strips ?.html query on redirect, the hash still carries the book.
    return "viewer.html?" + qs + "#" + qs;
  }

  function stashViewer(book, page, query) {
    try {
      localStorage.setItem(VIEWER_STORE, JSON.stringify({
        book: book || "",
        pdf: pdfUrl(book) || "",
        page: page ? String(page) : "",
        q: query || "",
        hl: highlightOn ? "1" : "0",
        t: Date.now(),
      }));
    } catch (_) {}
  }

  function openFullView(ev) {
    if (ev) ev.preventDefault();
    const piece = (viewingPiece && viewingPiece.book && viewingPiece.page)
      ? viewingPiece
      : (allSources.find(s => s.page && s.book) || null);
    if (!piece || !piece.book || !piece.page) {
      showStatus("Pick a source row with a page before enlarging.", true);
      return;
    }
    if (!books) {
      showStatus("Books not loaded yet. Try again in a moment.", true);
      return;
    }
    const q = piece.query || formatQueryFromValue(piece.value);
    const page = currentPage || piece.page;
    stashViewer(piece.book, page, q);
    const url = viewerUrl(piece.book, page, q);
    const a = document.createElement("a");
    a.href = url;
    a.target = "_blank";
    a.rel = "noopener";
    document.body.appendChild(a);
    a.click();
    a.remove();
  }

  async function renderPage(pageNum, opts) {
    if (!currentPdf) return;
    opts = opts || {};
    lastRenderOpts = opts;
    const token = ++renderToken;
    const page = await currentPdf.getPage(pageNum);
    if (token !== renderToken) return;

    const canvas = $("evidenceCanvas");
    const ctx = canvas.getContext("2d");
    const base = page.getViewport({ scale: 1 });
    const maxW = Math.max(320, ($("evidenceBody").clientWidth || 480) - 24);
    const cssScale = maxW / base.width;
    const dpr = Math.min(window.devicePixelRatio || 1, 2.5);
    const viewport = page.getViewport({ scale: cssScale * dpr });
    canvas.width = Math.floor(viewport.width);
    canvas.height = Math.floor(viewport.height);
    canvas.style.width = Math.floor(viewport.width / dpr) + "px";
    canvas.style.height = Math.floor(viewport.height / dpr) + "px";
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    await page.render({ canvasContext: ctx, viewport }).promise;
    if (token !== renderToken) return;

    lastHighlight = null;
    if (highlightOn) {
      if (opts.hit && opts.hit.x0 != null) {
        lastHighlight = drawHitBBox(ctx, viewport, opts.hit, base);
      }
      if (!lastHighlight && opts.query) {
        lastHighlight = await highlightQuery(page, viewport, ctx, opts.query, opts);
      }
    }
    if (lastHighlight) scrollHighlightIntoView(lastHighlight, dpr);
    else if ($("evidenceBody")) $("evidenceBody").scrollTop = 0;
    syncHighlightButton();
  }

  function drawHitBBox(ctx, viewport, hit, baseViewport) {
    const pageH = hit.pageH || baseViewport.height;
    const pageW = hit.pageW || baseViewport.width;
    const pdfX0 = hit.x0;
    const pdfX1 = hit.x1;
    const pdfY0 = pageH - hit.bottom;
    const pdfY1 = pageH - hit.top;
    let x, y, w, h;
    if (viewport.convertToViewportRectangle) {
      const r = viewport.convertToViewportRectangle([pdfX0, pdfY0, pdfX1, pdfY1]);
      x = Math.min(r[0], r[2]);
      y = Math.min(r[1], r[3]);
      w = Math.abs(r[2] - r[0]);
      h = Math.abs(r[3] - r[1]);
    } else {
      x = pdfX0 * (viewport.width / pageW);
      w = (pdfX1 - pdfX0) * (viewport.width / pageW);
      y = hit.top * (viewport.height / pageH);
      h = (hit.bottom - hit.top) * (viewport.height / pageH);
    }
    const pad = 3;
    x -= pad; y -= pad; w += pad * 2; h += pad * 2;
    ctx.save();
    ctx.fillStyle = "rgba(165, 129, 75, 0.4)";
    ctx.strokeStyle = "rgba(165, 129, 75, 0.95)";
    ctx.lineWidth = 2;
    ctx.fillRect(x, y, w, h);
    ctx.strokeRect(x, y, w, h);
    ctx.restore();
    return { x, y, w, h };
  }

  function scrollHighlightIntoView(hl, dpr) {
    const body = $("evidenceBody");
    if (!body || !hl) return;
    const scale = dpr || Math.min(window.devicePixelRatio || 1, 2.5);
    body.scrollTop = Math.max(0, (hl.y / scale) - body.clientHeight * 0.35);
  }

  function syncHighlightButton() {
    const btn = $("evidenceHlToggle");
    if (!btn) return;
    btn.textContent = highlightOn ? "Hide highlight" : "Show highlight";
  }

  async function highlightQuery(page, viewport, ctx, query, opts) {
    const variants = queryVariants(query);
    let content;
    try {
      content = await page.getTextContent();
    } catch (_) {
      return null;
    }
    const items = content.items.filter(it => it.str && it.str.trim());
    let bestRun = null;
    let bestScore = -1;
    let anchorY = null;
    const near = ((opts && (opts.unit || opts.line)) || "").toLowerCase().slice(0, 16);
    if (near) {
      for (const it of items) {
        if (it.str.toLowerCase().includes(near)) {
          anchorY = transformItem(viewport, it)[5];
          break;
        }
      }
    }
    for (const q of variants) {
      const hits = findTextRuns(items, q);
      for (const run of hits) {
        const tx = transformItem(viewport, run[0]);
        let score = 10;
        if (anchorY != null) score += Math.max(0, 50 - Math.abs(tx[5] - anchorY) / 2);
        if (score > bestScore) {
          bestScore = score;
          bestRun = run;
        }
      }
      if (bestRun && !near) break;
    }
    if (!bestRun) return null;

    ctx.save();
    ctx.fillStyle = "rgba(165, 129, 75, 0.4)";
    ctx.strokeStyle = "rgba(165, 129, 75, 0.95)";
    ctx.lineWidth = 2;
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    bestRun.forEach(it => {
      const tx = transformItem(viewport, it);
      const x = tx[4];
      const y = tx[5];
      const fontH = Math.hypot(tx[2], tx[3]);
      const w = (it.width || 0) * viewport.scale;
      const top = y - fontH * 0.85;
      const h = fontH * 1.15;
      ctx.fillRect(x, top, Math.max(w, 8), h);
      ctx.strokeRect(x, top, Math.max(w, 8), h);
      minX = Math.min(minX, x);
      minY = Math.min(minY, top);
      maxX = Math.max(maxX, x + w);
      maxY = Math.max(maxY, top + h);
    });
    ctx.restore();
    return { x: minX, y: minY, w: maxX - minX, h: maxY - minY };
  }

  function transformItem(viewport, it) {
    return (pdfjsLib.Util && pdfjsLib.Util.transform)
      ? pdfjsLib.Util.transform(viewport.transform, it.transform)
      : multiplyTransform(viewport.transform, it.transform);
  }

  function queryVariants(q) {
    if (!q) return [];
    const s = String(q).trim();
    const out = new Set([s]);
    out.add(s.replace(/[$\s]/g, ""));
    out.add(s.replace(/,/g, ""));
    out.add(s.replace(/\.00$/, ""));
    out.add(s.replace(/\.00$/, "") + ".00");
    const digits = s.replace(/[^\d().-]/g, "");
    if (/^-?\d+$/.test(digits)) {
      out.add(Number(digits).toLocaleString("en-US"));
      out.add(Number(digits).toLocaleString("en-US") + ".00");
    }
    if (s.startsWith("(") && s.endsWith(")")) {
      out.add(s.slice(1, -1));
      out.add("-" + s.slice(1, -1).replace(/,/g, ""));
    }
    return [...out].filter(Boolean);
  }

  function multiplyTransform(m1, m2) {
    return [
      m1[0] * m2[0] + m1[2] * m2[1],
      m1[1] * m2[0] + m1[3] * m2[1],
      m1[0] * m2[2] + m1[2] * m2[3],
      m1[1] * m2[2] + m1[3] * m2[3],
      m1[0] * m2[4] + m1[2] * m2[5] + m1[4],
      m1[1] * m2[4] + m1[3] * m2[5] + m1[5],
    ];
  }

  function findTextRuns(items, query) {
    const q = query.toLowerCase().replace(/\s+/g, "");
    if (!q) return [];
    let hay = "";
    const map = [];
    items.forEach((it, idx) => {
      const t = it.str.replace(/\s+/g, "");
      for (let i = 0; i < t.length; i++) map.push(idx);
      hay += t.toLowerCase();
    });
    const hits = [];
    let from = 0;
    while (from < hay.length) {
      const at = hay.indexOf(q, from);
      if (at < 0) break;
      const startItem = map[at];
      const endItem = map[Math.min(at + q.length - 1, map.length - 1)];
      if (startItem != null && endItem != null) {
        const run = [];
        for (let i = startItem; i <= endItem; i++) run.push(items[i]);
        hits.push(run);
      }
      from = at + q.length;
      if (hits.length >= 12) break;
    }
    return hits;
  }

  function stepPage(delta) {
    if (!currentPdf) return;
    const next = Math.min(Math.max(1, currentPage + delta), currentPdf.numPages);
    if (next === currentPage) return;
    currentPage = next;
    $("evidencePageLabel").textContent = `p. ${currentPage} of ${currentPdf.numPages}`;
    const piece = viewingPiece || currentCite;
    renderPage(currentPage, {
      query: piece && (piece.query || formatQueryFromValue(piece.value)),
      hit: null,
      unit: piece && piece.unit,
      line: piece && piece.line,
    });
  }

  function openById(id) {
    const cite = (global.CITATIONS || {})[id];
    if (!cite) {
      console.warn("No citation for", id);
      return;
    }
    openCitation({ id, ...cite });
  }

  function openLine(book, row) {
    openCitation({
      type: "printed",
      label: `${row.u || row.c || "Unit"} — ${row.l}`,
      formula: `This number is printed in the ${book} book.`,
      book,
      page: row.p,
      value: row.v,
      query: formatQueryFromValue(row.v),
      unit: row.u,
      line: row.l,
      fy: row.f,
      kind: row.k,
      children: [],
    });
  }

  async function loadBookLines(bookLabel) {
    if (lineCache[bookLabel]) return lineCache[bookLabel];
    const slug = bookLabel.replace(/ /g, "_");
    const res = await fetch("data/lines/" + slug + ".json");
    if (!res.ok) throw new Error("Failed to load " + bookLabel);
    const data = await res.json();
    lineCache[bookLabel] = data;
    return data;
  }

  function applyPanelWidth(px) {
    const max = Math.floor(window.innerWidth * PANEL_MAX_RATIO);
    const w = Math.max(PANEL_MIN, Math.min(max, Math.round(px)));
    document.documentElement.style.setProperty("--evidence-w", w + "px");
    try { localStorage.setItem("evidencePanelW", String(w)); } catch (_) {}
    return w;
  }

  function initResize(panel) {
    const handle = document.createElement("div");
    handle.className = "evidence-resize";
    handle.title = "Drag to resize";
    panel.appendChild(handle);
    let startX = 0, startW = 0;
    const onMove = (e) => {
      const x = e.touches ? e.touches[0].clientX : e.clientX;
      applyPanelWidth(startW + (startX - x));
    };
    const onUp = () => {
      document.body.classList.remove("evidence-resizing");
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
      window.removeEventListener("touchmove", onMove);
      window.removeEventListener("touchend", onUp);
      if (currentPdf && lastRenderOpts) renderPage(currentPage, lastRenderOpts);
    };
    const onDown = (e) => {
      e.preventDefault();
      startX = e.touches ? e.touches[0].clientX : e.clientX;
      startW = panel.getBoundingClientRect().width;
      document.body.classList.add("evidence-resizing");
      window.addEventListener("mousemove", onMove);
      window.addEventListener("mouseup", onUp);
      window.addEventListener("touchmove", onMove, { passive: false });
      window.addEventListener("touchend", onUp);
    };
    handle.addEventListener("mousedown", onDown);
    handle.addEventListener("touchstart", onDown, { passive: false });
    try {
      const saved = parseInt(localStorage.getItem("evidencePanelW"), 10);
      if (saved) applyPanelWidth(saved);
    } catch (_) {}
    warmLibraries();
  }

  function initPanelDom() {
    if ($("evidencePanel")) return;
    const panel = document.createElement("aside");
    panel.id = "evidencePanel";
    panel.setAttribute("aria-hidden", "true");
    panel.innerHTML = `
      <div class="evidence-header">
        <div class="evidence-header-top">
          <span id="evidenceBadge" class="evidence-badge">Source</span>
          <button type="button" id="evidenceClose" class="evidence-close" aria-label="Close">×</button>
        </div>
        <h2 id="evidenceTitle">Source</h2>
        <p id="evidenceClicked" class="evidence-clicked"></p>
        <div class="evidence-explain">
          <h3 id="evidenceExplainTitle"></h3>
          <p id="evidenceExplainBody"></p>
          <div id="evidenceEq" class="evidence-eq" hidden></div>
        </div>
        <p id="evidenceCaption" class="evidence-caption" hidden></p>
        <p id="evidenceMeta" class="evidence-meta"></p>
        <div class="evidence-toolbar">
          <button type="button" id="evidencePrev" class="evidence-nav">← Prev</button>
          <span id="evidencePageLabel" class="evidence-page">—</span>
          <button type="button" id="evidenceNext" class="evidence-nav">Next →</button>
          <a id="evidenceDocLink" class="evidence-doc" href="#" target="_blank" rel="noopener">Open page</a>
          <button type="button" id="evidenceHlToggle" class="evidence-nav">Hide highlight</button>
        </div>
        <p id="evidenceStatus" class="evidence-status"></p>
      </div>
      <div id="evidenceBody" class="evidence-body">
        <div id="evidenceLoading" class="evidence-loading" hidden>
          <div class="evidence-spinner" aria-hidden="true"></div>
          <p id="evidenceLoadingText">Opening the source book…</p>
          <div id="evidenceProgress" class="evidence-progress indeterminate">
            <i id="evidenceProgressBar"></i>
          </div>
          <p id="evidenceLoadingPct" class="evidence-loading-pct">Fetching the page…</p>
        </div>
        <canvas id="evidenceCanvas"></canvas>
      </div>
      <div id="evidenceSourceWrap" class="evidence-children-wrap">
        <div class="evidence-children-label" id="evidenceChildrenLabel"></div>
        <p class="evidence-children-sum" id="evidenceSourceSum"></p>
        <input id="evidenceSourceQ" type="search" placeholder="Search units…" hidden />
        <div id="evidenceChildren"></div>
      </div>
    `;
    document.body.appendChild(panel);
    initResize(panel);

    $("evidenceClose").addEventListener("click", () => setOpen(false));
    $("evidencePrev").addEventListener("click", () => stepPage(-1));
    $("evidenceNext").addEventListener("click", () => stepPage(1));
    $("evidenceHlToggle").addEventListener("click", () => {
      highlightOn = !highlightOn;
      syncHighlightButton();
      if (currentPdf && lastRenderOpts) renderPage(currentPage, lastRenderOpts);
    });
    $("evidenceCanvas").addEventListener("click", openFullView);
    $("evidenceDocLink").addEventListener("click", (e) => {
      e.preventDefault();
      openFullView();
    });
    $("evidenceSourceQ").addEventListener("input", () => {
      if (currentCite) renderSourceList(currentCite, $("evidenceSourceQ").value);
    });
    document.addEventListener("keydown", (e) => {
      if (!document.body.classList.contains("evidence-open")) return;
      if (e.key === "Escape") setOpen(false);
      if (e.key === "ArrowLeft") stepPage(-1);
      if (e.key === "ArrowRight") stepPage(1);
    });
    window.addEventListener("resize", () => {
      if (currentPdf && document.body.classList.contains("evidence-open") && lastRenderOpts) {
        renderPage(currentPage, lastRenderOpts);
      }
    });
  }

  function bindCite(el, citeId) {
    if (!el || !citeId) return;
    el.classList.add("citeable");
    el.setAttribute("data-cite", citeId);
    el.title = "Click to see how this number was checked";
    el.addEventListener("pointerenter", () => prefetchById(citeId));
    el.addEventListener("click", (e) => {
      e.stopPropagation();
      openById(citeId);
    });
  }

  function chartCiteHandler(buildId) {
    return (evt, elements) => {
      if (!elements || !elements.length) return;
      const el = elements[0];
      const id = buildId(el.datasetIndex, el.index);
      if (id) openById(id);
    };
  }

  global.EvidencePanel = {
    init: initPanelDom,
    openById,
    openCitation,
    openLine,
    bindCite,
    prefetchById,
    chartCiteHandler,
    loadBookLines,
    loadBooks,
    setOpen,
  };
})(window);
