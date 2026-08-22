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

  function valuesExact(a, b) {
    if (a == null || b == null) return false;
    if (Number.isNaN(Number(a)) || Number.isNaN(Number(b))) return false;
    // Posted salary rates print cents (252,977.56) next to a rounded dashboard dollar.
    return Math.abs(Math.round(Number(a)) - Math.round(Number(b))) <= 1;
  }

  function parseMoney(s) {
    if (s == null || s === "") return null;
    if (typeof s === "number") return s;
    const t = String(s).trim().replace(/[−–—]/g, "-");
    const neg = /^\(.*\)$/.test(t) || t.startsWith("-");
    let digits = t.replace(/[^0-9.]/g, "");
    if ((digits.match(/\./g) || []).length > 1) return null;
    const n = Number(digits);
    if (Number.isNaN(n)) return null;
    return neg ? -n : n;
  }

  function looksLikeNumberToken(raw) {
    const s = String(raw || "").replace(/\s+/g, "").replace(/[−–—]/g, "-").replace(/^\$/, "");
    return /^-?\(?\d[\d,]*\)?(?:\.00)?$/.test(s);
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
    const abs = Math.round(Math.abs(n)).toLocaleString("en-US");
    // Books print negatives as -8, not (8). Keep the sign so we never
    // search for a bare "8" that sits inside 88.
    if (n < 0) return "-" + abs;
    return abs;
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

  function setPane(mode) {
    const loading = $("evidenceLoading");
    const empty = $("evidenceEmpty");
    const compose = $("evidenceCompose");
    const canvas = $("evidenceCanvas");
    if (loading) loading.classList.toggle("on", mode === "loading");
    if (empty) empty.classList.toggle("on", mode === "empty");
    if (compose) compose.classList.toggle("on", mode === "compose");
    if (canvas) canvas.style.visibility = mode === "page" ? "visible" : "hidden";
    expectPdf = mode === "loading";
    const derived = !!(currentCite && currentCite.type === "derived");
    const countyWide = !!(currentCite && (currentCite.metric === "revenue" || currentCite.metric === "spend") && currentCite.fy);
    const showBack = (derived || countyWide) && mode !== "compose" && mode !== "empty";
    const back = $("evidenceBack");
    const pageBack = $("evidencePageBack");
    if (back) back.hidden = !showBack;
    if (pageBack) {
      pageBack.hidden = !showBack;
      pageBack.classList.toggle("on", showBack);
    }
  }

  function showLoading(text, loaded, total) {
    setPane("loading");
    const label = $("evidenceLoadingText");
    const pct = $("evidenceLoadingPct");
    const bar = $("evidenceProgressBar");
    const track = $("evidenceProgress");
    if (label && text) label.textContent = text;
    if (total > 0 && loaded >= 0) {
      const p = Math.min(100, Math.round((loaded / total) * 100));
      if (track) track.classList.remove("indeterminate");
      if (bar) bar.style.width = p + "%";
      if (pct) pct.textContent = p + "% · " + fmtBytes(loaded) + " of " + fmtBytes(total);
    } else {
      if (track) track.classList.add("indeterminate");
      if (bar) bar.style.width = "40%";
      if (pct) pct.textContent = "This can take a few seconds on a large book…";
    }
  }

  function showEmpty(title, body) {
    setPane("empty");
    const t = $("evidenceEmptyTitle");
    const b = $("evidenceEmptyBody");
    if (t) t.textContent = title || "Pick a source below";
    if (b) b.textContent = body || "This total is a sum of unit lines. Click a row in the list to open that printed page here.";
  }

  function fmtShort(v) {
    const n = Number(v);
    if (Number.isNaN(n)) return "—";
    const sign = n < 0 ? "−" : "";
    const a = Math.abs(n);
    if (a >= 1e9) return sign + "$" + (a / 1e9).toFixed(1).replace(/\.0$/, "") + "B";
    if (a >= 1e6) return sign + "$" + (a / 1e6).toFixed(1).replace(/\.0$/, "") + "M";
    if (a >= 1e3) return sign + "$" + (a / 1e3).toFixed(0) + "K";
    return fmtFull(n);
  }

  function shortUnitName(s) {
    const raw = (s && (s.unit || s.group || (s.label || "").split("—")[0] || s.line)) || "Unit";
    return String(raw).replace(/\s+/g, " ").trim();
  }

  function sliceColor(i) {
    const hue = 214 - (i % 16) * 10;
    const light = 34 + (i % 5) * 4;
    return "hsl(" + hue + ", 36%, " + light + "%)";
  }

  function packSlices(rows) {
    return rows
      .filter(s => s && s.value != null && Math.abs(Number(s.value)) >= 1 && !s.countyWide)
      .slice()
      .sort((a, b) => Math.abs(b.value) - Math.abs(a.value))
      .map((s, i) => ({
        source: s,
        label: shortUnitName(s),
        value: Math.abs(Number(s.value)),
        signed: Number(s.value),
        color: sliceColor(i),
        page: s.page || null,
      }));
  }

  function mosaicHtml(slices, total, pack) {
    const denom = slices.reduce((a, s) => a + s.value, 0) || total || 1;
    const p = pack || "main";
    return slices.map((s, i) => {
      const pct = Math.max(0.4, (s.value / denom) * 100);
      const title = s.label + " · " + fmtFull(s.signed != null ? s.signed : s.value);
      return `<button type="button" class="ec-slice${s.other ? " other" : ""}" data-pack="${p}" data-i="${i}" ` +
        `style="flex:${s.value} 1 ${pct}%;background:${s.color}" title="${escapeHtml(title)}"></button>`;
    }).join("");
  }

  function rowsHtml(slices, total, pack) {
    const denom = total || slices.reduce((a, s) => a + s.value, 0) || 1;
    const p = pack || "main";
    return slices.map((s, i) => {
      const pct = (s.value / denom) * 100;
      const pctLabel = pct >= 10 ? pct.toFixed(0) + "%" : pct.toFixed(1) + "%";
      const page = s.page ? " · p." + s.page : "";
      return `<button type="button" class="ec-row${s.other ? " other" : ""}" data-pack="${p}" data-i="${i}">` +
        `<span class="ec-swatch" style="background:${s.color}"></span>` +
        `<span class="ec-row-main">` +
          `<span class="ec-row-name">${escapeHtml(s.label)}${page}</span>` +
          `<span class="ec-row-track"><i style="width:${Math.min(100, pct)}%;background:${s.color}"></i></span>` +
        `</span>` +
        `<span class="ec-row-amt">${fmtShort(s.signed != null ? s.signed : s.value)}<em>${pctLabel}</em></span>` +
      `</button>`;
    }).join("");
  }

  function bindComposeClicks(host, packs, cite) {
    host.querySelectorAll("[data-pack][data-i]").forEach((el) => {
      el.addEventListener("click", () => {
        const pack = el.getAttribute("data-pack");
        const slice = (packs[pack] || [])[Number(el.getAttribute("data-i"))];
        if (!slice) return;
        if (slice.source && slice.source.page && slice.source.book) {
          showPiece(cite, slice.source, ++openGen);
        } else if (slice.source) {
          openCitation(slice.source);
        }
      });
    });
  }

  function renderComposition(cite) {
    const host = $("evidenceCompose");
    if (!host) {
      showEmpty("Pick a source below", "Click a unit in the list to open that printed page.");
      return;
    }
    const sources = allSources || [];
    if (!sources.length) {
      showEmpty(
        "This total is not printed as one figure",
        (cite && cite.formula) || "Open a contributing source from the formula, or look the pieces up in the book."
      );
      setPane("compose");
      return;
    }

    const label = cite.label || "";
    const metric = cite.metric || "";
    const isCompare = metric === "surplus" || /draw|planned/i.test(label);
    const isYears = /cumulative/i.test(label);
    const clicked = Number(cite.value) || 0;

    setPane("compose");
    host.innerHTML = "";

    if (isYears) {
      const slices = packSlices(sources);
      const sum = slices.reduce((a, s) => a + s.signed, 0);
      host.innerHTML =
        `<div class="ec-kicker">How the years add up</div>` +
        `<div class="ec-total">${fmtFull(clicked)}</div>` +
        `<div class="ec-mosaic" role="img" aria-label="Yearly surplus mosaic">${mosaicHtml(slices, Math.abs(clicked) || Math.abs(sum), "main")}</div>` +
        `<div class="ec-hint">Every closed year, largest first. Click a year to open its sources.</div>` +
        `<div class="ec-rows">${rowsHtml(slices, Math.abs(clicked) || slices.reduce((a, s) => a + s.value, 0), "main")}</div>`;
      bindComposeClicks(host, { main: slices }, cite);
      return;
    }

    if (isCompare) {
      const rev = sources.filter(s => /rev/i.test(s.group || s.metric || s.label || ""));
      const exp = sources.filter(s => /spend|expend/i.test(s.group || s.metric || s.label || ""));
      const revN = rev.reduce((a, s) => a + Number(s.value || 0), 0);
      const expN = exp.reduce((a, s) => a + Number(s.value || 0), 0);
      const revSlices = packSlices(rev.length ? rev : sources);
      const expSlices = packSlices(exp);
      const maxBar = Math.max(revN, expN, 1);
      host.innerHTML =
        `<div class="ec-kicker">How this difference is built</div>` +
        `<div class="ec-total">${fmtFull(clicked)}</div>` +
        `<div class="ec-compare">` +
          `<div class="ec-compare-row">` +
            `<div class="ec-compare-lab">Revenue <b>${fmtShort(revN)}</b></div>` +
            `<div class="ec-mosaic tall" style="width:${Math.max(18, (Math.abs(revN) / maxBar) * 100)}%">${mosaicHtml(revSlices, Math.abs(revN), "rev")}</div>` +
          `</div>` +
          `<div class="ec-compare-row">` +
            `<div class="ec-compare-lab">Spending <b>${fmtShort(expN)}</b></div>` +
            `<div class="ec-mosaic tall" style="width:${Math.max(18, (Math.abs(expN) / maxBar) * 100)}%">${mosaicHtml(expSlices, Math.abs(expN) || 1, "exp")}</div>` +
          `</div>` +
        `</div>` +
        `<input class="ec-filter" type="search" placeholder="Filter units…" />` +
        `<div class="ec-hint">${revSlices.length + expSlices.length} unit lines. Click any row to open that page.</div>` +
        `<div class="ec-rows">` +
          `<div class="ec-section">Revenue</div>` +
          rowsHtml(revSlices, Math.abs(revN) || 1, "rev") +
          `<div class="ec-section">Spending</div>` +
          rowsHtml(expSlices.map(s => ({ ...s, label: s.label })), Math.abs(expN) || 1, "exp") +
        `</div>`;
      bindComposeClicks(host, { rev: revSlices, exp: expSlices }, cite);
      bindComposeFilter(host);
      return;
    }

    const slices = packSlices(sources);
    const stack = slices.reduce((a, s) => a + s.value, 0);
    const inflation = metric === "inflation";
    const printedTotal = sources.find(s => s && s.countyWide && s.page && s.book);
    const printedChip = printedTotal
      ? `<button type="button" class="ec-printed" data-printed="1">` +
          `<span>Box the printed county-wide total</span>` +
          `<strong>${fmtFull(printedTotal.value)} · p.${printedTotal.page}</strong>` +
        `</button>`
      : "";
    host.innerHTML =
      `<div class="ec-kicker">${inflation
        ? "The book prints the nominal figure — this chart point is CPI-adjusted"
        : slices.length + " printed lines add to"}</div>` +
      `<div class="ec-total">${fmtFull(clicked)}</div>` +
      printedChip +
      `<div class="ec-mosaic" role="img" aria-label="Composition mosaic">${mosaicHtml(slices, inflation ? stack : (Math.abs(clicked) || stack), "main")}</div>` +
      `<input class="ec-filter" type="search" placeholder="Filter units…" />` +
      `<div class="ec-hint">${inflation
        ? "Click the source row to box the printed nominal amount, " + fmtFull(stack) + "."
        : "Every unit, largest first. Click a slice or row to open that printed page."}</div>` +
      `<div class="ec-rows">${rowsHtml(slices, stack, "main")}</div>`;
    bindComposeClicks(host, { main: slices }, cite);
    bindComposeFilter(host);
    const printedBtn = host.querySelector("[data-printed]");
    if (printedBtn && printedTotal) {
      printedBtn.addEventListener("click", () => showPiece(cite, printedTotal, ++openGen));
    }
  }

  function bindComposeFilter(host) {
    const input = host.querySelector(".ec-filter");
    if (!input) return;
    input.addEventListener("input", () => {
      const q = input.value.trim().toLowerCase();
      host.querySelectorAll(".ec-row").forEach((row) => {
        const name = ((row.querySelector(".ec-row-name") || {}).textContent || "").toLowerCase();
        row.hidden = !!(q && !name.includes(q));
      });
    });
  }

  function showBreakdown() {
    if (!currentCite) return;
    viewingPiece = null;
    lastHighlight = null;
    renderComposition(currentCite);
    renderHeader(currentCite);
    renderSourceList(currentCite, ($("evidenceSourceQ") || {}).value);
    showStatus("");
  }

  function hideLoading() {
    const loading = $("evidenceLoading");
    if (loading) loading.classList.remove("on");
    expectPdf = false;
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

  async function fetchPdfBytes(url, onProgress) {
    let cache = null;
    try { cache = await caches.open("sutter-pdfs-v1"); } catch (_) {}
    if (cache) {
      const hit = await cache.match(url);
      if (hit) return await hit.arrayBuffer();
    }
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 90000);
    try {
      const res = await fetch(url, { signal: ctrl.signal });
      if (!res.ok) throw new Error("PDF HTTP " + res.status);
      const total = Number(res.headers.get("content-length") || 0);
      if (!res.body || !res.body.getReader) {
        const buf = await res.arrayBuffer();
        if (cache) cache.put(url, new Response(buf, { headers: { "Content-Type": "application/pdf" } })).catch(() => {});
        return buf;
      }
      const reader = res.body.getReader();
      const chunks = [];
      let loaded = 0;
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        chunks.push(value);
        loaded += value.byteLength;
        if (onProgress) onProgress(loaded, total);
      }
      const out = new Uint8Array(loaded);
      let offset = 0;
      chunks.forEach((c) => { out.set(c, offset); offset += c.byteLength; });
      if (cache) {
        cache.put(url, new Response(out, { headers: { "Content-Type": "application/pdf" } })).catch(() => {});
      }
      return out.buffer;
    } finally {
      clearTimeout(timer);
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
    const promise = (async () => {
      const data = await fetchPdfBytes(url, (loaded, total) => {
        if (!expectPdf) return;
        if (currentBook && currentBook !== book && currentPdf) return;
        showLoading("Opening the " + book + " book…", loaded, total);
      });
      const task = pdfjsLib.getDocument({ data, verbosity: 0 });
      const doc = await task.promise;
      const ent = pdfCache.get(book);
      if (ent) ent.doc = doc;
      return doc;
    })().catch((err) => {
      pdfCache.delete(book);
      throw err;
    });
    pdfCache.set(book, { promise, doc: null });
    return promise;
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

  function actualsLag(cite) {
    if ((cite.kind || "actual") !== "actual" || !cite.fy || !cite.book) return "";
    const fy = String(cite.fy).replace("FY", "FY ");
    return ` ${fy} actuals are printed in the ${cite.book} book — closed years appear two books later.`;
  }

  function citeFamily(cite) {
    const id = String(cite.id || "");
    const parts = id.split(".");
    const head = parts[0] || "";
    if (head === "kpi") return "kpi." + (parts[1] || "");
    if (head === "trend") return "trend." + (parts[1] || "");
    if (head === "inflation") return "inflation.real";
    if (head === "adopted") return "adopted." + (parts[1] || "");
    if (head === "pay") return "pay." + (parts[1] || "");
    if (head === "function") return "function";
    if (head === "revmix") return "revmix";
    if (head === "expmix") return "expmix";
    if (head === "revcat") return "revcat";
    if (head === "expcat") return "expcat";
    if (head === "contract") return "contract";
    if (head === "dept" || head === "unit") {
      const tail = parts[2];
      if (tail === "growth") return "dept.growth";
      if (tail === "net24") return "dept.net24";
      if (tail === "fy16" || tail === "fy20" || tail === "fy24") return "dept.year";
      return "dept.unit";
    }

    const metric = cite.metric || "";
    const label = cite.label || "";
    const formula = cite.formula || "";
    const line = String(cite.line || "");
    if (metric === "inflation" || /CPI|inflation/i.test(formula)) return "inflation.real";
    if (/cumulative/i.test(label)) return "kpi.cumulativeSurplus";
    if (/planned fund-balance draw|adopted planned|recommended planned/i.test(label)) {
      return /recommended/i.test(label) ? "kpi.recommendedDraw2627" : "kpi.adoptedDraw2526";
    }
    if (cite.kind === "adopted" && /plan|surplus|draw/i.test(label)) return "adopted.plan";
    if (cite.kind === "adopted" && metric === "spend") return "adopted.spend";
    if (metric === "surplus") return "trend.surplus";
    if (metric === "net" || /net county cost/i.test(line + label)) return "dept.net24";
    if (metric === "function" || /function/i.test(formula)) return "function";
    if (metric === "category") {
      return /revenue/i.test(label + formula) ? "revcat" : "expcat";
    }
    if (metric === "contract" || /professional|contract/i.test(line + label)) return "contract";
    if (metric === "pay") {
      if (/FTE|authorized/i.test(formula) && !/range|salary/i.test(formula)) return "pay.staff";
      if (/top of range|salary/i.test(formula)) return "pay.high";
      return "pay.high";
    }
    if (cite.unit && /net county cost/i.test(line)) return "dept.net24";
    if (cite.unit && isRevenueLine(line)) return "dept.revenue";
    if (cite.unit && isSpendLine(line)) return "dept.unit";
    if (cite.unit && line) return "printed.line";
    if (metric === "revenue") return "trend.revenue";
    if (metric === "spend") return "trend.spend";
    return "";
  }

  function plainExplainer(cite) {
    const family = citeFamily(cite);
    const lag = actualsLag(cite);
    const eq = cite.formula || null;
    const fn = cite.function || (String(cite.label || "").split("—")[0] || "").trim();
    const cat = cite.category || (String(cite.label || "").split("—")[0] || "").trim();
    const unit = cite.unit || (String(cite.label || "").split("—")[0] || "").trim();
    const line = cite.line || "this line";

    const meanings = {
      "trend.revenue": {
        title: "Actual revenue",
        body: "Money governmental funds recorded as received in that closed fiscal year. Not the adopted plan." + lag,
      },
      "trend.spend": {
        title: "Actual spending",
        body: "Money governmental funds recorded as spent in that closed fiscal year. Not the adopted appropriation." + lag,
      },
      "trend.surplus": {
        title: "Actual surplus (or draw)",
        body: "That year’s actual revenue minus actual spending. Positive means the books took in more than they spent.",
        equation: eq,
      },
      "kpi.lastActualSurplus": {
        title: "Latest closed-year leftover",
        body: "FY 2024-25 actual revenue minus actual spending. Same math as that year’s surplus bar.",
        equation: eq,
      },
      "kpi.lastActualRevenue": {
        title: "Latest closed-year revenue",
        body: "Governmental-fund money recorded as received in FY 2024-25. Not the adopted plan." + lag,
      },
      "kpi.lastActualSpend": {
        title: "Latest closed-year spending",
        body: "Governmental-fund money recorded as spent in FY 2024-25. Not the adopted appropriation." + lag,
      },
      "kpi.cumulativeSurplus": {
        title: "Nine-year net",
        body: "Sum of each closed year’s surplus from FY 2016-17 through FY 2024-25. Not cash on hand.",
        equation: eq,
      },
      "kpi.adoptedDraw2526": {
        title: "Adopted planned draw",
        body: "FY 2025-26 Board-adopted revenue minus adopted spending. A plan to use fund balance, not a closed result.",
        equation: eq,
      },
      "kpi.recommendedDraw2627": {
        title: "Recommended planned draw",
        body: "FY 2026-27 recommended revenue minus recommended spending. A staff proposal, not yet a closed result.",
        equation: eq,
      },
      "inflation.real": {
        title: "Inflation-adjusted spending",
        body: "That year’s actual spending restated in FY 2024-25 dollars using CPI-U. The book only prints the nominal (unadjusted) amount.",
        equation: eq,
      },
      "adopted.spend": {
        title: "Adopted spending",
        body: "Appropriations the Board authorized for that year — the spending plan / ceiling, not what later closed.",
      },
      "adopted.plan": {
        title: "Adopted planned surplus (or draw)",
        body: "Adopted revenue minus adopted spending for that year. What the Board planned; compare to the actual surplus bar.",
        equation: eq,
      },
      "function": {
        title: "Spending by function",
        body: "Actual spending of every unit the county classifies under " +
          (fn || "that function") +
          " in that year." + lag,
      },
      "revmix": {
        title: "Revenue by source type",
        body: "Actual governmental-fund revenue in the " +
          (cat || "that") +
          " source bucket for that year." + lag,
      },
      "expmix": {
        title: "Spending by object type",
        body: "Actual spending in the " +
          (cat || "that") +
          " object bucket for that year." + lag,
      },
      "revcat": {
        title: "Revenue category total",
        body: "Actual governmental-fund revenue in " +
          (cat || "this category") +
          " for the latest year shown." + lag,
      },
      "expcat": {
        title: "Spending category total",
        body: "Actual spending in " +
          (cat || "this category") +
          " for the latest year shown." + lag,
      },
      "dept.unit": {
        title: "Unit actual spending",
        body: (unit ? unit + "’s" : "That budget unit’s") +
          " printed Total Expenditures for the year shown. Not county-wide spending." + lag,
      },
      "dept.year": {
        title: "Unit actual spending",
        body: (unit ? unit + "’s" : "That budget unit’s") +
          " printed Total Expenditures in that closed year." + lag,
      },
      "dept.growth": {
        title: "Unit spending change",
        body: "FY 2024-25 actual spending minus FY 2016-17 actual spending for this unit, in dollars — not a percent.",
        equation: eq,
      },
      "dept.net24": {
        title: "Net county cost",
        body: "This unit’s printed Net County Cost: spending minus the unit’s own revenue — the piece the county must cover." + lag,
      },
      "dept.revenue": {
        title: "Unit actual revenue",
        body: (unit ? unit + "’s" : "That budget unit’s") +
          " printed Total Revenues for the year shown." + lag,
      },
      "pay.high": {
        title: "Top of salary range",
        body: "Highest authorized annual rate for this classification in the FY 2025-26 salary resolution. Not what any one person was paid.",
      },
      "pay.cost": {
        title: "Estimated class payroll",
        body: "Authorized FTE times a point on this class’s salary range (the chart uses mid-range). An estimate, not a printed payroll total.",
      },
      "pay.staff": {
        title: "Authorized seats",
        body: "Budgeted FTE for this classification in the position allocation schedule — not filled headcount.",
      },
      "contract": {
        title: "Contract / professional-services line",
        body: "One object in that unit’s budget, not the unit’s total spending.",
      },
      "printed.line": {
        title: "Printed budget line",
        body: "The boxed figure is " +
          (unit ? unit + " — " : "") +
          line +
          ", as printed in the source book.",
      },
    };

    if (meanings[family]) {
      return {
        title: meanings[family].title,
        body: meanings[family].body,
        equation: meanings[family].equation || null,
      };
    }

    if (cite.type === "printed") {
      return {
        title: "Printed in the source book",
        body: "The boxed figure is the same amount you clicked." + lag,
        equation: null,
      };
    }

    return {
      title: "Derived from source rows",
      body: cite.formula || "Combined from more than one printed figure.",
      equation: eq,
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
    if (cite && cite.book) bits.push(cite.book + " book");
    if (cite && cite.fy && cite.book) {
      const fy = String(cite.fy).replace("FY", "FY ");
      if (!String(cite.book).includes(fy.replace("FY ", ""))) {
        bits.push(fy + " actuals");
      }
    }
    if (viewingPiece && viewingPiece.page) bits.push("p. " + viewingPiece.page);
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

    const isPieces = cite.type === "derived" ||
      ((cite.metric === "revenue" || cite.metric === "spend") && cite.fy);
    labelEl.textContent = isPieces
      ? `Sources (${allSources.length})`
      : "Related rows";

    sumEl.textContent = "";

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
    const countyWide = (cite.metric === "revenue" || cite.metric === "spend") && cite.fy;
    if (cite.type === "printed" && !countyWide) return baked;

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
      const units = dedupeSources(found.length ? found : baked);
      if (cite.type === "printed" && cite.page && cite.book) {
        return [{
          type: "printed",
          book: cite.book,
          page: cite.page,
          value: cite.value,
          query: cite.query || formatQueryFromValue(cite.value),
          hit: cite.hit,
          label: "County-wide printed total",
          line: metric === "revenue" ? "Schedule 5 total" : "Schedule 8 total",
          fy: cite.fy,
          kind: cite.kind,
          metric: cite.metric,
          countyWide: true,
        }, ...units];
      }
      return units;
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
      showEmpty("No page for this row", "Try another source in the list.");
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
      if (highlightOn && piece.value != null) {
        currentPage = await findPageWithValue(currentPdf, currentPage, piece.value);
      }
      $("evidencePageLabel").textContent = `p. ${currentPage} of ${currentPdf.numPages}`;
      $("evidenceDocLink").href = viewerUrl(piece.book, currentPage, piece.query || formatQueryFromValue(piece.value));
      $("evidenceDocLink").textContent = "Open page";

      await renderPage(currentPage, {
        query: piece.query || formatQueryFromValue(piece.value),
        value: piece.value,
        hit: piece.hit,
        unit: piece.unit,
        line: piece.line,
      });
      if (gen != null && gen !== openGen) return;
      setPane("page");
      if (highlightOn && !lastHighlight && piece.value != null) {
        if (piece.type === "derived") {
          renderComposition(currentCite || cite);
          showStatus("This total is not printed as one figure. Click a source row to box that row.");
        } else {
          showStatus("Opened the page but could not box " + fmtFull(piece.value) + ". The figure is not on this page as a whole number.", true);
        }
      } else {
        showStatus("Highlight is " + fmtFull(piece.value) + ". Click the page for a sharper view.");
      }
    } catch (e) {
      if (gen != null && gen !== openGen) return;
      console.error(e);
      const aborted = e && (e.name === "AbortError" || /abort/i.test(String(e.message || "")));
      showEmpty(
        aborted ? "The book timed out" : "Could not open this book",
        "Click a source again, or use Open page to view it in a new tab."
      );
      showStatus(aborted
        ? "The download stalled. Click a source row again, or Open page."
        : "Could not load that PDF page. Try Open page, or refresh and click again.", true);
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
    showLoading("Finding the printed page…");
    showStatus("Finding the printed page…");

    try {
      await Promise.all([loadBooks(), ensurePdfJs()]);
    } catch (e) {
      if (gen !== openGen) return;
      showEmpty("Could not start the PDF viewer", "Refresh the page and click the number again.");
      showStatus("Could not load PDF.js. Refresh and try again.", true);
      return;
    }
    if (gen !== openGen) return;

    // Warm the book in the background so a source click is ready.
    if (cite.book) prefetchPdf(cite.book).catch(() => {});

    allSources = await expandSources(cite);
    if (gen !== openGen) return;
    renderSourceList(cite, "");

    const clickedQuery = formatQueryFromValue(cite.value);
    const parentQuery = queryMatchesValue(cite.query, cite.value) ? cite.query : clickedQuery;
    const printedHit = cite.hit && cite.hit.x0 != null && valuesExact(parseMoney(cite.hit.query), cite.value);
    const countyWide = (cite.metric === "revenue" || cite.metric === "spend") && cite.fy;
    const canHighlightClicked = !!(
      cite.type === "printed" &&
      !countyWide &&
      cite.book && cite.page && cite.value != null &&
      (printedHit || queryMatchesValue(parentQuery, cite.value))
    );

    // Only open a PDF to box a figure that is printed as that same amount.
    // County-wide revenue/spend always shows the same unit breakdown first.
    // Calculated totals stay on the breakdown — never a feeder page and a red miss.
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

    renderComposition(cite);
    showStatus(countyWide && cite.type === "printed"
      ? "Unit lines below add to this total. Click the county-wide row to box the printed figure."
      : "This total is not a single printed figure. Click a source row to box that row in the book.");
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
      if (opts.hit && opts.hit.x0 != null && valuesExact(parseMoney(opts.hit.query), opts.value)
          && (!opts.hit.page || opts.hit.page === pageNum)) {
        lastHighlight = drawHitBBox(ctx, viewport, opts.hit, base);
      }
      if (!lastHighlight && (opts.query || opts.value != null)) {
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
    const target = opts && opts.value != null ? Number(opts.value) : parseMoney(query);
    const variants = queryVariants(query || formatQueryFromValue(target), target);
    let content;
    try {
      content = await page.getTextContent();
    } catch (_) {
      return null;
    }
    const items = content.items.filter(it => it.str && it.str.trim());
    let bestRun = null;
    let bestScore = -1;
    const anchors = [];
    const needles = [];
    if (opts && opts.line) needles.push(String(opts.line).toLowerCase());
    if (opts && opts.unit) needles.push(String(opts.unit).toLowerCase().slice(0, 18));
    needles.push("total revenues", "total expenditures", "total revenues");
    items.forEach((it) => {
      const low = it.str.toLowerCase();
      if (needles.some(n => n && low.includes(n))) {
        anchors.push(transformItem(viewport, it)[5]);
      }
    });
    const hits = target != null
      ? findValueRuns(items, target)
      : variants.flatMap(q => findTextRuns(items, q));
    for (const run of hits) {
      const runText = run.map(it => it.str).join("");
      const parsed = parseMoney(runText);
      if (target != null && (parsed == null || !valuesExact(parsed, target))) continue;
      const tx = transformItem(viewport, run[0]);
      let score = 20 + runText.length;
      if (anchors.length) {
        const dist = Math.min(...anchors.map(y => Math.abs(tx[5] - y)));
        score += Math.max(0, 80 - dist / 2);
      }
      if (score > bestScore) {
        bestScore = score;
        bestRun = run;
      }
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

  function queryVariants(q, value) {
    if (q == null && value == null) return [];
    const n = value != null ? Number(value) : parseMoney(q);
    const out = new Set();
    if (q) out.add(String(q).trim());
    if (n == null || Number.isNaN(n)) return [...out].filter(Boolean);
    const abs = Math.round(Math.abs(n)).toLocaleString("en-US");
    const raw = String(Math.round(Math.abs(n)));
    if (n < 0) {
      out.add("-" + abs);
      out.add("-" + raw);
      out.add("(" + abs + ")");
      out.add("(" + raw + ")");
      out.add("-" + abs + ".00");
    } else {
      out.add(abs);
      out.add(raw);
      if (Math.abs(n) >= 1000) out.add(abs + ".00");
    }
    return [...out].filter(Boolean).sort((a, b) => b.length - a.length);
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

  function isNumChar(ch) {
    return ch >= "0" && ch <= "9";
  }

  function itemX(it) { return it.transform ? it.transform[4] : 0; }
  function itemY(it) { return it.transform ? it.transform[5] : 0; }

  function groupItemsByLine(items) {
    const rows = [];
    items.forEach(it => {
      const y = itemY(it);
      let row = rows.find(r => Math.abs(r.y - y) <= 4);
      if (!row) {
        row = { y, items: [] };
        rows.push(row);
      }
      row.items.push(it);
    });
    rows.forEach(r => r.items.sort((a, b) => itemX(a) - itemX(b)));
    return rows;
  }

  function clusterLineItems(items) {
    const groups = [];
    let g = [];
    let prevRight = -1e9;
    items.forEach(it => {
      const x = itemX(it);
      const w = it.width || 0;
      if (g.length && x - prevRight > 14) {
        groups.push(g);
        g = [];
      }
      g.push(it);
      prevRight = x + w;
    });
    if (g.length) groups.push(g);
    return groups;
  }

  function parseCluster(run) {
    const raw = run.map(it => it.str).join("").replace(/\s+/g, "").replace(/[−–—]/g, "-").replace(/^\$/, "");
    if (!looksLikeNumberToken(raw)) return null;
    return parseMoney(raw);
  }

  function commaInt(n) {
    return String(Math.abs(Math.round(n))).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  }

  function findValueRuns(items, target) {
    if (target == null || Number.isNaN(Number(target))) return [];
    const want = Math.round(Number(target));
    const hits = [];
    const seen = new Set();
    const add = (run) => {
      if (!run || !run.length) return;
      const key = run.map(it => itemX(it) + ":" + itemY(it) + ":" + it.str).join("|");
      if (seen.has(key)) return;
      seen.add(key);
      hits.push(run);
    };

    groupItemsByLine(items).forEach(row => {
      clusterLineItems(row.items).forEach(run => {
        const n = parseCluster(run);
        if (n != null && Math.abs(Math.round(n) - want) <= 1) add(run);
      });
      const line = row.items;
      for (let i = 0; i < line.length; i++) {
        for (let j = i + 1; j <= line.length && j - i <= 16; j++) {
          const n = parseCluster(line.slice(i, j));
          if (n != null && Math.abs(Math.round(n) - want) <= 1) add(line.slice(i, j));
        }
      }
    });

    if (!hits.length) {
      let hay = "";
      const map = [];
      items.forEach((it, idx) => {
        const t = String(it.str).replace(/\s+/g, "").replace(/[−–—]/g, "-");
        for (let k = 0; k < t.length; k++) map.push(idx);
        hay += t;
      });
      const abs = Math.abs(want);
      const patterns = [commaInt(abs), String(abs)];
      if (want < 0) {
        patterns.unshift("-" + commaInt(abs), "(" + commaInt(abs) + ")", "-" + String(abs), "(" + String(abs) + ")");
      }
      patterns.forEach(q => {
        let from = 0;
        while (from < hay.length) {
          const at = hay.indexOf(q, from);
          if (at < 0) break;
          const before = at > 0 ? hay[at - 1] : "";
          const after = at + q.length < hay.length ? hay[at + q.length] : "";
          if (isNumChar(before) || isNumChar(after)) { from = at + 1; continue; }
          const a = map[at];
          const b = map[Math.min(at + q.length - 1, map.length - 1)];
          if (a != null && b != null) add(items.slice(a, b + 1));
          from = at + q.length;
        }
      });
    }
    return hits;
  }

  async function findPageWithValue(doc, startPage, value) {
    const pages = [startPage];
    [1, -1, 2, -2].forEach(d => {
      const p = startPage + d;
      if (p >= 1 && p <= doc.numPages && !pages.includes(p)) pages.push(p);
    });
    for (const p of pages) {
      try {
        const page = await doc.getPage(p);
        const content = await page.getTextContent();
        const items = content.items.filter(it => it.str && it.str.trim());
        if (findValueRuns(items, value).length) return p;
      } catch (_) { /* keep looking */ }
    }
    return startPage;
  }

  function findTextRuns(items, query) {
    const q = String(query).toLowerCase().replace(/\s+/g, "");
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
      const before = at > 0 ? hay[at - 1] : "";
      const after = at + q.length < hay.length ? hay[at + q.length] : "";
      // 8 must not match the 8 inside 88 or 139.
      if (isNumChar(before) || isNumChar(after)) {
        from = at + 1;
        continue;
      }
      const startItem = map[at];
      const endItem = map[Math.min(at + q.length - 1, map.length - 1)];
      if (startItem != null && endItem != null) {
        const run = [];
        for (let i = startItem; i <= endItem; i++) run.push(items[i]);
        hits.push(run);
      }
      from = at + q.length;
      if (hits.length >= 20) break;
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
          <button type="button" id="evidenceBack" class="evidence-nav" hidden>Breakdown</button>
          <button type="button" id="evidenceHlToggle" class="evidence-nav">Hide highlight</button>
        </div>
        <p id="evidenceStatus" class="evidence-status"></p>
      </div>
      <div id="evidenceBody" class="evidence-body">
        <button type="button" id="evidencePageBack" class="evidence-page-back" hidden>← Back to breakdown</button>
        <div id="evidenceLoading" class="evidence-pane-msg">
          <div class="evidence-spinner" aria-hidden="true"></div>
          <p id="evidenceLoadingText">Opening the source book…</p>
          <div id="evidenceProgress" class="evidence-progress indeterminate">
            <i id="evidenceProgressBar"></i>
          </div>
          <p id="evidenceLoadingPct" class="evidence-loading-pct">This can take a few seconds on a large book…</p>
        </div>
        <div id="evidenceEmpty" class="evidence-pane-msg">
          <div class="evidence-empty-arrow" aria-hidden="true">↓</div>
          <p id="evidenceEmptyTitle">Pick a source below</p>
          <p id="evidenceEmptyBody" class="sub">Click a unit in the list to open that printed page.</p>
        </div>
        <div id="evidenceCompose" class="evidence-compose"></div>
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
    $("evidenceBack").addEventListener("click", showBreakdown);
    $("evidencePageBack").addEventListener("click", showBreakdown);
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
