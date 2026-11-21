/**
 * @fileoverview Zero-dependency HTML, CSS, SVG, and LaTeX math renderer for Facet Lens.
 *
 * Renders the 5-tab workbench (`Traceability Graph`, `Sources & Formulation`,
 * `4-Predicate Proofs`, `Implementation`, and `Sealed Archive`). Uses an offline-first
 * LaTeX-to-HTML tokenizer (`renderLatexTokensToHtml`) plus `String.raw` client JS
 * isolation (`CLIENT_RUNTIME_JS`) paired with `<script id="boot-data" type="application/json">`
 * so regular expressions and math backslashes are never corrupted by template-literal escaping.
 */

/**
 * Escape untrusted text before inserting into HTML elements or attributes.
 */
export function escapeHtml(value) {
    return String(value ?? "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#39;");
}

// Built-in Unicode/HTML-entity map for LaTeX Greek letters, operators, and math functions
// so formulas render cleanly offline even before KaTeX loads.
const LATEX_SYMBOLS = {
    alpha: "&alpha;", beta: "&beta;", gamma: "&gamma;", delta: "&delta;",
    epsilon: "&epsilon;", varepsilon: "&epsilon;", zeta: "&zeta;", eta: "&eta;",
    theta: "&theta;", vartheta: "&thetasym;", iota: "&iota;", kappa: "&kappa;",
    lambda: "&lambda;", mu: "&mu;", nu: "&nu;", xi: "&xi;", pi: "&pi;", varpi: "&piv;",
    rho: "&rho;", varrho: "&rho;", sigma: "&sigma;", varsigma: "&sigmaf;", tau: "&tau;",
    upsilon: "&upsilon;", phi: "&phi;", varphi: "&phi;", chi: "&chi;", psi: "&psi;", omega: "&omega;",
    Gamma: "&Gamma;", Delta: "&Delta;", Theta: "&Theta;", Lambda: "&Lambda;", Xi: "&Xi;",
    Pi: "&Pi;", Sigma: "&Sigma;", Upsilon: "&Upsilon;", Phi: "&Phi;", Psi: "&Psi;", Omega: "&Omega;",
    nabla: "&nabla;", partial: "&part;", infty: "&infin;", cdot: "&middot;", cdots: "&#8943;",
    ldots: "&hellip;", dots: "&hellip;", times: "&times;", otimes: "&otimes;", bigotimes: "&#10754;",
    oplus: "&oplus;", bigoplus: "&#10753;", top: "&#8868;", perp: "&perp;", dagger: "&dagger;",
    langle: "&lang;", rangle: "&rang;", ell: "&#8467;", hbar: "&#8463;",
    Vert: "&#8214;", lVert: "&#8214;", rVert: "&#8214;", lvert: "|", rvert: "|",
    pm: "&plusmn;", mp: "&#8723;",
    le: "&le;", leq: "&le;", ge: "&ge;", geq: "&ge;", ne: "&ne;", neq: "&ne;",
    approx: "&approx;", equiv: "&equiv;", sim: "&sim;", propto: "&prop;",
    in: "&isin;", notin: "&notin;", subset: "&sub;", subseteq: "&sube;",
    to: "&rarr;", rightarrow: "&rarr;", leftarrow: "&larr;", mapsto: "&#8614;",
    Rightarrow: "&rArr;", Leftarrow: "&lArr;", Leftrightarrow: "&hArr;",
    forall: "&forall;", exists: "&exist;", sum: "&Sigma;", prod: "&Pi;", int: "&int;",
    det: '<span class="m-rm">det</span>', tr: '<span class="m-rm">tr</span>',
    rank: '<span class="m-rm">rank</span>', sin: '<span class="m-rm">sin</span>',
    cos: '<span class="m-rm">cos</span>', tan: '<span class="m-rm">tan</span>',
    exp: '<span class="m-rm">exp</span>', ln: '<span class="m-rm">ln</span>',
    log: '<span class="m-rm">log</span>', min: '<span class="m-rm">min</span>',
    max: '<span class="m-rm">max</span>', argmin: '<span class="m-rm">arg&thinsp;min</span>',
    argmax: '<span class="m-rm">arg&thinsp;max</span>', lim: '<span class="m-rm">lim</span>',
};

const BLACKBOARD_MAP = { R: "&#8477;", C: "&#8450;", Z: "&#8484;", N: "&#8469;", Q: "&#8474;", E: "&#120124;" };
const ACCENT_COMBINING = { hat: "&#770;", tilde: "&#771;", bar: "&#772;", overline: "&#773;", vec: "&#8407;", dot: "&#775;", ddot: "&#776;" };

// Parse a balanced `{...}` group (or single macro/token) starting at character index `idx`.
function readLatexGroup(src, idx) {
    let i = idx;
    while (i < src.length && /\s/.test(src[i])) i++;
    if (i >= src.length) return { content: "", next: i };
    if (src[i] === "{") {
        let depth = 1;
        const start = i + 1;
        i++;
        while (i < src.length && depth > 0) {
            if (src[i] === "\\" && i + 1 < src.length) {
                i += 2;
                continue;
            }
            if (src[i] === "{") depth++;
            else if (src[i] === "}") depth--;
            i++;
        }
        return { content: src.slice(start, Math.max(start, i - 1)), next: i };
    }
    if (src[i] === "\\") {
        let j = i + 1;
        if (j < src.length && /[A-Za-z]/.test(src[j])) {
            while (j < src.length && /[A-Za-z]/.test(src[j])) j++;
            const cmd = src.slice(i + 1, j);
            if (["mathbf", "boldsymbol", "mathrm", "text", "hat", "bar", "tilde", "vec", "mathbb", "mathcal"].includes(cmd)) {
                const sub = readLatexGroup(src, j);
                return { content: `\\${cmd}{${sub.content}}`, next: sub.next };
            }
            return { content: src.slice(i, j), next: j };
        }
        return { content: src.slice(i, Math.min(src.length, i + 2)), next: Math.min(src.length, i + 2) };
    }
    return { content: src[i], next: i + 1 };
}

// Recursively convert LaTeX tokens (`\begin{bmatrix}`, `\frac`, `\sqrt`, `^`, `_`, macros) into HTML.
export function renderLatexTokensToHtml(input) {
    const src = String(input || "");
    let out = "";
    let i = 0;
    while (i < src.length) {
        // Convert `\begin{bmatrix}...\end{bmatrix}` blocks into CSS grid matrix elements.
        if (src.startsWith("\\begin{", i)) {
            const envEnd = src.indexOf("}", i + 7);
            if (envEnd !== -1) {
                const envName = src.slice(i + 7, envEnd);
                const closeTag = `\\end{${envName}}`;
                const closeIdx = src.indexOf(closeTag, envEnd + 1);
                if (closeIdx !== -1) {
                    const body = src.slice(envEnd + 1, closeIdx);
                    const rows = body.split(/\\\\/).map((r) => r.trim()).filter(Boolean);
                    const parsedRows = rows.map((r) => r.split("&").map((c) => renderLatexTokensToHtml(c.trim())));
                    const cols = Math.max(1, ...parsedRows.map((r) => r.length));
                    const cellsHtml = parsedRows.flatMap((r) => r.map((c) => `<span>${c}</span>`)).join("");
                    out += `<span class="bmatrix" style="grid-template-columns:repeat(${cols},auto)">${cellsHtml}</span>`;
                    i = closeIdx + closeTag.length;
                    continue;
                }
            }
        }
        const ch = src[i];
        if (ch === "\\") {
            if (i + 1 < src.length && !/[A-Za-z]/.test(src[i + 1])) {
                const esc = src[i + 1];
                if (esc === "|") out += "&#8214;";
                else if (esc === "," || esc === ";" || esc === " ") out += "&thinsp;";
                else if (esc === "!") out += "";
                else if ("{}%&_#$".includes(esc)) out += escapeHtml(esc);
                else out += escapeHtml(esc);
                i += 2;
                continue;
            }
            let j = i + 1;
            while (j < src.length && /[A-Za-z]/.test(src[j])) j++;
            const cmd = src.slice(i + 1, j);
            if (cmd === "displaystyle" || cmd === "textstyle" || cmd === "left" || cmd === "right") {
                i = j;
                continue;
            }
            if (cmd === "quad" || cmd === "qquad") {
                out += "&emsp;";
                i = j;
                continue;
            }
            // Render stacked fractions (`\frac{num}{den}`) using `.m-num` over `.m-den`.
            if (cmd === "frac" || cmd === "dfrac" || cmd === "tfrac") {
                const num = readLatexGroup(src, j);
                const den = readLatexGroup(src, num.next);
                out += `<span class="m-frac"><span class="m-num">${renderLatexTokensToHtml(num.content)}</span><span class="m-den">${renderLatexTokensToHtml(den.content)}</span></span>`;
                i = den.next;
                continue;
            }
            // Render square roots (`\sqrt{rad}`) with a radical sign and overbar span.
            if (cmd === "sqrt") {
                const rad = readLatexGroup(src, j);
                out += `<span class="m-sqrt">&radic;<span class="m-rad">${renderLatexTokensToHtml(rad.content)}</span></span>`;
                i = rad.next;
                continue;
            }
            if (cmd === "mathbf" || cmd === "boldsymbol" || cmd === "bm") {
                const grp = readLatexGroup(src, j);
                out += `<strong class="m-bf">${renderLatexTokensToHtml(grp.content)}</strong>`;
                i = grp.next;
                continue;
            }
            if (cmd === "text" || cmd === "mathrm" || cmd === "operatorname" || cmd === "textit") {
                const grp = readLatexGroup(src, j);
                out += `<span class="m-rm">${escapeHtml(grp.content)}</span>`;
                i = grp.next;
                continue;
            }
            if (cmd === "mathbb") {
                const grp = readLatexGroup(src, j);
                const k = grp.content.trim();
                out += `<span class="m-bb">${BLACKBOARD_MAP[k] || escapeHtml(k)}</span>`;
                i = grp.next;
                continue;
            }
            if (cmd === "mathcal") {
                const grp = readLatexGroup(src, j);
                out += `<span class="m-cal">${renderLatexTokensToHtml(grp.content)}</span>`;
                i = grp.next;
                continue;
            }
            // Attach Unicode combining marks for `\hat`, `\tilde`, `\bar`, `\vec`, and `\dot`.
            if (ACCENT_COMBINING[cmd]) {
                const grp = readLatexGroup(src, j);
                out += `<span class="m-accent">${renderLatexTokensToHtml(grp.content)}${ACCENT_COMBINING[cmd]}</span>`;
                i = grp.next;
                continue;
            }
            if (LATEX_SYMBOLS[cmd]) {
                out += LATEX_SYMBOLS[cmd];
                i = j;
                continue;
            }
            out += `<span class="m-rm">${escapeHtml(cmd)}</span>`;
            i = j;
            continue;
        }
        // Render superscripts (`^`) and subscripts (`_`) recursively inside `<sup>` / `<sub>`.
        if (ch === "^" || ch === "_") {
            const tag = ch === "^" ? "sup" : "sub";
            const grp = readLatexGroup(src, i + 1);
            out += `<${tag}>${renderLatexTokensToHtml(grp.content)}</${tag}>`;
            i = grp.next;
            continue;
        }
        if (ch === "{") {
            const grp = readLatexGroup(src, i);
            out += renderLatexTokensToHtml(grp.content);
            i = grp.next;
            continue;
        }
        if (ch === "}") {
            i++;
            continue;
        }
        if (ch === "-") {
            out += "&minus;";
            i++;
            continue;
        }
        out += escapeHtml(ch);
        i++;
    }
    return out;
}

function formatMatrixOrEquationHtml(latexRaw) {
    const raw = String(latexRaw || "").replace(/^\$+|\$+$/g, "").trim();
    if (!raw) return `<span class="empty-inline">Pending formal extraction</span>`;
    return `<span class="math-expr" data-latex="${escapeHtml(raw)}">${renderLatexTokensToHtml(raw)}</span>`;
}

function formatMathSymbolHtml(symbolRaw) {
    const raw = String(symbolRaw || "").replace(/^\$+|\$+$/g, "").trim();
    if (!raw) return "";
    return `<span class="math-sym" data-latex="${escapeHtml(raw)}">${renderLatexTokensToHtml(raw)}</span>`;
}

// Render inline `$...$` / `$$...$$` / `\(...\)` math segments inside prose while escaping plain text.
function formatMixedProseWithMathHtml(proseRaw) {
    const text = String(proseRaw || "");
    if (!text) return "";
    const parts = text.split(/(\$\$[\s\S]+?\$\$|\\\[[\s\S]+?\\\]|\\\([\s\S]+?\\\)|\$[^$\n]+\$)/g);
    return parts
        .map((seg) => {
            if (
                (seg.startsWith("$$") && seg.endsWith("$$")) ||
                (seg.startsWith("$") && seg.endsWith("$") && seg.length > 1)
            ) {
                return formatMathSymbolHtml(seg);
            }
            if ((seg.startsWith("\\[") && seg.endsWith("\\]")) || (seg.startsWith("\\(") && seg.endsWith("\\)"))) {
                return formatMathSymbolHtml(seg.slice(2, -2));
            }
            return escapeHtml(seg);
        })
        .join("");
}

// Translate `.pipeline_state.json` `current_stage` into `done` / `active` topbar timeline states.
function stageFlags(stage, isArchivedView) {
    if (isArchivedView) {
        return { research: "done", spec: "done", iface: "done", proof: "done", sealed: "active" };
    }
    const map = {
        PRE_RUN_HALT: { research: "", spec: "", iface: "", proof: "", sealed: "" },
        STAGE_0_IDEATION: { research: "active", spec: "", iface: "", proof: "", sealed: "" },
        CHECKPOINT_0_AWAITING_APPROVAL: { research: "active", spec: "", iface: "", proof: "", sealed: "" },
        STAGE_1_SPECIFICATION: { research: "done", spec: "active", iface: "", proof: "", sealed: "" },
        STAGE_1_CRITIC: { research: "done", spec: "active", iface: "", proof: "", sealed: "" },
        CHECKPOINT_1_AWAITING_APPROVAL: { research: "done", spec: "active", iface: "", proof: "", sealed: "" },
        STAGE_2_MAPPING: { research: "done", spec: "done", iface: "active", proof: "", sealed: "" },
        STAGE_2_CRITIC: { research: "done", spec: "done", iface: "active", proof: "", sealed: "" },
        CHECKPOINT_2_AWAITING_APPROVAL: { research: "done", spec: "done", iface: "active", proof: "", sealed: "" },
        STAGE_3_IMPLEMENTATION: { research: "done", spec: "done", iface: "done", proof: "active", sealed: "" },
        STAGE_4_VERIFICATION: { research: "done", spec: "done", iface: "done", proof: "active", sealed: "" },
        CHECKPOINT_3_AWAITING_APPROVAL: { research: "done", spec: "done", iface: "done", proof: "active", sealed: "" },
        CHECKPOINT_REPAIR_ESCALATION: { research: "done", spec: "done", iface: "done", proof: "active", sealed: "" },
    };
    return map[stage] || { research: "done", spec: "done", iface: "done", proof: "active", sealed: "" };
}

// Build 2-column line-number gutter + `data-specs="SPEC-k"` annotated source lines for Tab 4.
function addLineNumbers(codeText, lineSpecTags = []) {
    const lines = String(codeText || "# No artifact written yet.").replace(/\n$/, "").split("\n");
    const gutter = lines.map((_, idx) => `<div class="gutter-ln">${String(idx + 1).padStart(2, "0")}</div>`).join("");
    const code = lines
        .map((ln, idx) => {
            const tags = Array.isArray(lineSpecTags[idx]) ? lineSpecTags[idx] : [];
            const specAttr = tags.length > 0 ? ` data-specs="${escapeHtml(tags.join(","))}"` : "";
            return `<div class="code-line" data-line="${idx + 1}"${specAttr}>${escapeHtml(ln) || " "}</div>`;
        })
        .join("");
    return { gutter, code };
}

export const CLIENT_RUNTIME_JS = String.raw`
    // Hydrate initial server state from the JSON data tag ('<script id="boot-data">').
    let BOOT = JSON.parse((document.getElementById("boot-data") || {}).textContent || "{}");
    let lockedNode = BOOT.lockedNode || "";
    let pinnedExcerpts = Array.isArray(BOOT.pinnedExcerpts) ? BOOT.pinnedExcerpts.slice() : [];
    let curPdf = 0;
    let curPage = 1;
    let lastDigest = "";
    let refreshInFlight = false;
    let pendingRefresh = false;

    // Asynchronously save user UI preferences ('activeTab', 'theme', 'lockedNode', 'pinnedExcerpts')
    // back to the loopback server without blocking click responsiveness.
    function persistPref(patch) {
      fetch("/api/prefs", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(patch) }).catch(() => {});
    }

    // Highlight matching 'SPEC-k' equation cards, 'OBL-j' proof cells, and '# Implements SPEC-k'
    // Python code lines simultaneously across all tabs whenever a node is hovered or clicked.
    function applyCrossTabHighlight(activeNode) {
      const reach = activeNode ? connectedSet(activeNode) : null;
      const litSpecs = new Set();
      const litObls = new Set();
      if (reach) {
        reach.nodes.forEach((n) => {
          if (n.startsWith("SPEC-")) litSpecs.add(n);
          if (n.startsWith("OBL-")) litObls.add(n);
        });
      }
      document.querySelectorAll(".form-card[data-spec]").forEach((el) => {
        el.classList.toggle("spec-lit", litSpecs.has(el.getAttribute("data-spec")));
      });
      document.querySelectorAll(".proof-cell[data-obl]").forEach((el) => {
        const oid = el.getAttribute("data-obl");
        el.classList.toggle("spec-lit", litObls.has(oid));
      });
      document.querySelectorAll("#code-pane-impl .code-line").forEach((el) => {
        const lspecs = (el.getAttribute("data-specs") || "").split(",").filter(Boolean);
        el.classList.toggle("spec-lit", lspecs.some((s) => litSpecs.has(s)));
      });
    }

    function applyGroundingFade() {
      const el = document.getElementById("kv-grounding");
      if (el) el.classList.toggle("is-faded", el.scrollWidth > el.clientWidth);
    }

    // Compute all 'SPEC-k', 'FN-m', and 'OBL-j' nodes and edges reachable from 'nodeId'.
    function connectedSet(nodeId) {
      const nodes = new Set([nodeId]);
      const edgeKeys = new Set();
      const edges = BOOT.edges || [];
      const specToObls = BOOT.specToObls || {};
      const oblToSpecs = BOOT.oblToSpecs || {};
      if (nodeId.startsWith("SPEC-")) {
        const directObls = new Set(specToObls[nodeId] || []);
        const fns = edges.filter(([a]) => a === nodeId).map(([, b]) => b);
        for (const fn of fns) {
          nodes.add(fn); edgeKeys.add(nodeId + "->" + fn);
          for (const [a, b] of edges) {
            if (a === fn && (directObls.size === 0 || directObls.has(b))) {
              nodes.add(b); edgeKeys.add(a + "->" + b);
            }
          }
        }
        directObls.forEach((oid) => nodes.add(oid));
      } else if (nodeId.startsWith("FN-")) {
        for (const [a, b] of edges) if (b === nodeId || a === nodeId) { nodes.add(a); nodes.add(b); edgeKeys.add(a + "->" + b); }
      } else {
        const directSpecs = new Set(oblToSpecs[nodeId] || []);
        const fns = edges.filter(([, b]) => b === nodeId).map(([a]) => a);
        for (const fn of fns) {
          nodes.add(fn); edgeKeys.add(fn + "->" + nodeId);
          for (const [a, b] of edges) {
            if (b === fn && (directSpecs.size === 0 || directSpecs.has(a))) {
              nodes.add(a); edgeKeys.add(a + "->" + b);
            }
          }
        }
        directSpecs.forEach((sid) => nodes.add(sid));
      }
      return { nodes, edgeKeys };
    }

    // Draw smooth cubic Bezier curves connecting '[SPEC-k] -> [FN-m] -> [OBL-j]' cards
    // inside the SVG overlay, illuminating edges that belong to the active/locked contract.
    function drawCurves(activeNode) {
      const stage = document.getElementById("trace-stage");
      const svg = document.getElementById("trace-svg");
      if (!stage || !svg || stage.offsetParent === null) return;
      const stageRect = stage.getBoundingClientRect();
      svg.setAttribute("viewBox", "0 0 " + stageRect.width + " " + stageRect.height);
      const reach = activeNode ? connectedSet(activeNode) : null;
      let paths = "";
      for (const [fromId, toId] of (BOOT.edges || [])) {
        const fromEl = stage.querySelector('[data-node="' + fromId + '"]');
        const toEl = stage.querySelector('[data-node="' + toId + '"]');
        if (!fromEl || !toEl) continue;
        const r1 = fromEl.getBoundingClientRect();
        const r2 = toEl.getBoundingClientRect();
        const x1 = r1.right - stageRect.left;
        const y1 = r1.top + r1.height / 2 - stageRect.top;
        const x2 = r2.left - stageRect.left;
        const y2 = r2.top + r2.height / 2 - stageRect.top;
        const dx = (x2 - x1) * 0.52;
        const d = "M " + x1 + " " + y1 + " C " + (x1 + dx) + " " + y1 + ", " + (x2 - dx) + " " + y2 + ", " + x2 + " " + y2;
        const isLit = reach ? reach.edgeKeys.has(fromId + "->" + toId) : false;
        if (isLit) {
          paths += '<path d="' + d + '" fill="none" stroke="url(#curve-lit)" stroke-width="4.2" opacity="0.2" filter="url(#curve-glow)"/>';
          paths += '<path d="' + d + '" fill="none" stroke="url(#curve-lit)" stroke-width="1.8" opacity="0.95"/>';
          paths += '<path class="curve-flow" d="' + d + '" fill="none" stroke="#ffffff" stroke-width="1.3" opacity="0.52"/>';
        } else {
          paths += '<path d="' + d + '" fill="none" stroke="var(--curve-rest)" stroke-width="1.25" opacity="' + (reach ? 0.2 : 0.85) + '"/>';
        }
      }
      svg.innerHTML = paths;
      stage.querySelectorAll(".node-card").forEach((card) => {
        const nid = card.getAttribute("data-node");
        card.classList.toggle("focused", Boolean(activeNode && nid === activeNode));
        card.classList.toggle("connected", Boolean(reach && reach.nodes.has(nid) && nid !== activeNode));
        card.classList.toggle("dimmed", Boolean(reach && !reach.nodes.has(nid)));
      });
      const idEl = document.getElementById("insp-id");
      const txtEl = document.getElementById("insp-text");
      if (activeNode) {
        const card = stage.querySelector('[data-node="' + activeNode + '"]');
        idEl.textContent = activeNode + " FOCUS";
        txtEl.innerHTML = (card && card.getAttribute("data-detail-html")) || "";
        upgradeKatexIfLoaded(txtEl);
      } else {
        idEl.textContent = "ALL CONTRACTS";
        txtEl.textContent = "Click or hover any specification, entrypoint, or cleanroom obligation to inspect contract provenance.";
      }
    }

    // Switch active workbench panel, redraw SVG Bezier curves on Traceability, and persist choice.
    function switchTab(tabId, save = true) {
      document.querySelectorAll(".tab-btn").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.tab === tabId)));
      document.querySelectorAll(".view-panel").forEach((p) => p.classList.toggle("active", p.id === "panel-" + tabId));
      if (tabId === "traceability") requestAnimationFrame(() => drawCurves(lockedNode));
      if (save) persistPref({ activeTab: tabId });
    }

    // Upgrade '[data-latex]' spans to full KaTeX typesetting whenever 'window.katex' is present.
    function upgradeKatexIfLoaded(root = document) {
      if (!window.katex) return;
      root.querySelectorAll("[data-latex]").forEach((el) => {
        const tex = el.getAttribute("data-latex");
        if (!tex) return;
        try {
          window.katex.render(tex, el, { throwOnError: false, displayMode: false });
        } catch {}
      });
      if (root === document) requestAnimationFrame(() => drawCurves(lockedNode));
    }

    function escHtmlClient(s) {
      return String(s || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
    }

    function renderPinnedWell() {
      const well = document.getElementById("pinned-corpus-well");
      if (!well) return;
      if (pinnedExcerpts.length === 0) {
        well.classList.remove("visible");
        well.textContent = "";
        return;
      }
      well.classList.add("visible");
      well.innerHTML = pinnedExcerpts.map((p) => "<div><strong>Pinned (" + escHtmlClient(p.ref) + "):</strong> " + escHtmlClient(p.text) + "</div>").join("");
    }

    function renderInlineMathClient(text) {
      const parts = String(text || "").split(/(\$\$[\s\S]+?\$\$|\$[^$\r\n]+\$)/g);
      return parts.map((seg) => {
        if ((seg.startsWith("$$") && seg.endsWith("$$")) || (seg.startsWith("$") && seg.endsWith("$") && seg.length > 1)) {
          const raw = seg.replace(/^\$+|\$+$/g, "").trim();
          return '<span class="math-sym" data-latex="' + escHtmlClient(raw) + '">' + escHtmlClient(raw) + '</span>';
        }
        return escHtmlClient(seg);
      }).join("");
    }

    // Refresh the Multi-Document Corpus Reader page counter, excerpt HTML, and pin button state.
    function renderPdfState() {
      const docs = BOOT.corpusDocs || [];
      const d = docs[curPdf] || { name: "research/", excerpts: [""] };
      const total = Math.max(1, (d.excerpts || []).length);
      curPage = Math.min(total, Math.max(1, curPage));
      const refKey = d.name + " p." + curPage;
      const excerptTxt = (d.excerpts && d.excerpts[curPage - 1]) || "";
      const secLbl = document.getElementById("pdf-section-label");
      const pgCnt = document.getElementById("pdf-page-counter");
      const exEl = document.getElementById("pdf-excerpt");
      const prevBtn = document.getElementById("pdf-prev");
      const nextBtn = document.getElementById("pdf-next");
      if (secLbl) secLbl.textContent = d.name;
      if (pgCnt) pgCnt.textContent = String(curPage).padStart(2, "0") + " / " + String(total).padStart(2, "0");
      if (exEl) { exEl.innerHTML = renderInlineMathClient(excerptTxt); upgradeKatexIfLoaded(exEl); }
      if (prevBtn) prevBtn.disabled = curPage <= 1;
      if (nextBtn) nextBtn.disabled = curPage >= total;
      const pinBtn = document.getElementById("corpus-pin-btn");
      if (pinBtn) {
        const isPinned = pinnedExcerpts.some((p) => p.ref === refKey);
        pinBtn.classList.toggle("pinned", isPinned);
        pinBtn.textContent = isPinned ? "Pinned" : "Pin Excerpt";
      }
      renderPinnedWell();
    }

    // Attach hover/click locking, corpus search, pinning, file switching, theme, and copy handlers.
    function bindInteractiveHandlers() {
      document.querySelectorAll(".tab-btn").forEach((btn) => {
        btn.onclick = () => switchTab(btn.dataset.tab, true);
      });

      document.querySelectorAll("#trace-stage .node-card").forEach((card) => {
        const nid = card.getAttribute("data-node");
        card.onmouseenter = () => { if (!lockedNode) { drawCurves(nid); applyCrossTabHighlight(nid); } };
        card.onmouseleave = () => { if (!lockedNode) { drawCurves(""); applyCrossTabHighlight(""); } };
        card.onclick = () => {
          lockedNode = lockedNode === nid ? "" : nid;
          drawCurves(lockedNode);
          applyCrossTabHighlight(lockedNode);
          persistPref({ lockedNode, lockedNodeSlug: BOOT.activeSlug || "" });
        };
      });

      document.querySelectorAll(".form-card[data-spec]").forEach((card) => {
        card.onclick = () => {
          const sid = card.getAttribute("data-spec");
          lockedNode = lockedNode === sid ? "" : sid;
          drawCurves(lockedNode);
          applyCrossTabHighlight(lockedNode);
          persistPref({ lockedNode, lockedNodeSlug: BOOT.activeSlug || "" });
        };
      });

      document.querySelectorAll(".proof-cell[data-obl]").forEach((card) => {
        card.onclick = () => {
          const oid = card.getAttribute("data-obl");
          lockedNode = lockedNode === oid ? "" : oid;
          drawCurves(lockedNode);
          applyCrossTabHighlight(lockedNode);
          persistPref({ lockedNode, lockedNodeSlug: BOOT.activeSlug || "" });
        };
      });

      document.querySelectorAll("[data-srcmode]").forEach((chip) => {
        chip.onclick = () => {
          if (chip.disabled) return;
          const mode = chip.dataset.srcmode;
          document.querySelectorAll("[data-srcmode]").forEach((c) => c.classList.toggle("active", c === chip));
          ["image", "multipdf", "literature"].forEach((m) => {
            const pane = document.getElementById("src-mode-" + m);
            if (pane) pane.style.display = m === mode ? (m === "multipdf" ? "flex" : "block") : "none";
          });
          const ttl = document.getElementById("left-pane-title");
          const meta = document.getElementById("left-pane-meta");
          if (ttl && BOOT.leftPaneTitleByMode && BOOT.leftPaneTitleByMode[mode]) ttl.textContent = BOOT.leftPaneTitleByMode[mode];
          if (meta && BOOT.leftPaneMetaByMode && BOOT.leftPaneMetaByMode[mode]) meta.textContent = BOOT.leftPaneMetaByMode[mode];
          persistPref({ activeSrcMode: mode });
        };
      });

      // Live-filter 'research/' corpus documents and jump to the first matching excerpt page.
      const searchInp = document.getElementById("corpus-search-input");
      if (searchInp) {
        searchInp.oninput = () => {
          const q = searchInp.value.trim().toLowerCase();
          const docs = BOOT.corpusDocs || [];
          document.querySelectorAll(".corpus-row").forEach((row) => {
            const idx = Number(row.dataset.pdf);
            const d = docs[idx];
            if (!q || !d) { row.style.display = "flex"; return; }
            const hay = (d.name + " " + (d.excerpts || []).join(" ")).toLowerCase();
            row.style.display = hay.includes(q) ? "flex" : "none";
            if (hay.includes(q)) {
              const matchPage = (d.excerpts || []).findIndex((ex) => ex.toLowerCase().includes(q));
              if (matchPage >= 0 && curPdf === idx) { curPage = matchPage + 1; renderPdfState(); }
            }
          });
        };
      }

      // Toggle pinned reference excerpts and persist them per workspace via '/api/prefs'.
      const pinBtnEl = document.getElementById("corpus-pin-btn");
      if (pinBtnEl) {
        pinBtnEl.onclick = () => {
          const docs = BOOT.corpusDocs || [];
          const d = docs[curPdf] || { name: "research/", excerpts: [""] };
          const refKey = d.name + " p." + curPage;
          const idx = pinnedExcerpts.findIndex((p) => p.ref === refKey);
          if (idx >= 0) pinnedExcerpts.splice(idx, 1);
          else pinnedExcerpts.push({ ref: refKey, text: String((d.excerpts && d.excerpts[curPage - 1]) || "").slice(0, 220) });
          persistPref({ pinnedExcerpts });
          renderPdfState();
        };
      }

      document.querySelectorAll(".corpus-row").forEach((b) => {
        b.onclick = () => {
          curPdf = Number(b.dataset.pdf); curPage = 1;
          document.querySelectorAll(".corpus-row").forEach((x) => x.classList.toggle("active", x === b));
          renderPdfState();
        };
      });
      const prevEl = document.getElementById("pdf-prev");
      const nextEl = document.getElementById("pdf-next");
      if (prevEl) prevEl.onclick = () => { curPage--; renderPdfState(); };
      if (nextEl) nextEl.onclick = () => { curPage++; renderPdfState(); };

      document.querySelectorAll(".code-file-btn").forEach((btn) => {
        btn.onclick = () => {
          const t = btn.dataset.codefile;
          document.querySelectorAll(".code-file-btn").forEach((b) => b.classList.toggle("active", b === btn));
          document.getElementById("code-pane-impl").style.display = t === "impl" ? "grid" : "none";
          document.getElementById("code-pane-suite").style.display = t === "suite" ? "grid" : "none";
        };
      });

      // Switch the entire workbench to inspect any clicked 'specs/<slug>/' package from Tab 5.
      document.querySelectorAll(".spec-shelf-card").forEach((card) => {
        card.onclick = async () => {
          await fetch("/api/prefs", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ selectedSpecSlug: card.dataset.specslug, activeTab: "traceability" }) });
          await refreshLensDomInPlace();
        };
      });

      document.querySelectorAll(".tl-node").forEach((step) => {
        step.onclick = () => {
          const stg = step.dataset.stage;
          if (stg === "research" || stg === "spec") switchTab("source-math");
          else if (stg === "interface") switchTab("traceability");
          else if (stg === "proof") switchTab("proofs");
          else if (stg === "sealed") switchTab("specs");
        };
      });

      const themeBtn = document.getElementById("theme-toggle");
      if (themeBtn) {
        themeBtn.onclick = () => {
          const html = document.documentElement;
          const next = html.dataset.lensTheme === "dark" ? "light" : "dark";
          html.dataset.lensTheme = next;
          persistPref({ theme: next });
          requestAnimationFrame(() => drawCurves(lockedNode));
        };
      }

      const sealBtn = document.getElementById("seal-toggle");
      if (sealBtn) {
        sealBtn.onclick = (e) => {
          e.stopPropagation();
          document.getElementById("seal-popover").classList.toggle("open");
        };
      }

      const copyBtn = document.getElementById("copy-pytest");
      if (copyBtn) {
        copyBtn.onclick = function () {
          if (navigator.clipboard) navigator.clipboard.writeText(BOOT.pytestCmd).catch(() => {});
          const orig = this.textContent;
          this.textContent = "Copied pytest command";
          setTimeout(() => { this.textContent = orig; }, 1300);
        };
      }
    }

    // Hot-swap '.topbar', '.viewport', and '.footer' in place when SSE 'reload' or digest
    // polling detects new workspace files, preserving the user's active tab and scroll offsets.
    async function refreshLensDomInPlace() {
      if (refreshInFlight) {
        pendingRefresh = true;
        return;
      }
      refreshInFlight = true;
      try {
        const curTabEl = document.querySelector('.tab-btn[aria-selected="true"]');
        const activeTabId = curTabEl ? curTabEl.dataset.tab : (BOOT.activeTab || "traceability");
        const scrolls = {};
        document.querySelectorAll(".view-panel .frame-scroll").forEach((el, idx) => { scrolls[idx] = el.scrollTop; });
        const prevSlug = BOOT.activeSlug || "";

        const res = await fetch("/index.html", { cache: "no-store" });
        if (!res.ok) return;
        const htmlTxt = await res.text();
        const parsed = new DOMParser().parseFromString(htmlTxt, "text/html");
        if (parsed.title) document.title = parsed.title;
        const bootDataEl = parsed.getElementById("boot-data");
        if (bootDataEl) {
          try {
            BOOT = JSON.parse(bootDataEl.textContent || "{}");
            const curBootData = document.getElementById("boot-data");
            if (curBootData) curBootData.textContent = bootDataEl.textContent;
          } catch {}
        }
        if ((BOOT.activeSlug || "") !== prevSlug) {
          lockedNode = "";
          curPdf = 0;
          curPage = 1;
        }
        for (const sel of [".topbar", ".viewport", ".footer"]) {
          const curEl = document.querySelector(sel);
          const nextEl = parsed.querySelector(sel);
          if (curEl && nextEl) curEl.replaceWith(nextEl);
        }
        bindInteractiveHandlers();
        switchTab(activeTabId, false);
        document.querySelectorAll(".view-panel .frame-scroll").forEach((el, idx) => {
          if (scrolls[idx] !== undefined) el.scrollTop = scrolls[idx];
        });
        upgradeKatexIfLoaded();
        applyGroundingFade();
        renderPdfState();
        requestAnimationFrame(() => { drawCurves(lockedNode); applyCrossTabHighlight(lockedNode); });
      } catch {} finally {
        refreshInFlight = false;
        if (pendingRefresh) {
          pendingRefresh = false;
          refreshLensDomInPlace();
        }
      }
    }

    // Poll '/api/digest' every 1.2 s so updates still appear if an embedded webview drops SSE.
    async function pollDigestOnce() {
      try {
        const r = await fetch("/api/digest", { cache: "no-store" });
        if (!r.ok) return;
        const j = await r.json();
        if (j && j.digest) {
          if (lastDigest && j.digest !== lastDigest) {
            lastDigest = j.digest;
            await refreshLensDomInPlace();
          } else {
            lastDigest = j.digest;
          }
        }
      } catch {}
    }

    document.addEventListener("click", () => {
      const pop = document.getElementById("seal-popover");
      if (pop) pop.classList.remove("open");
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && lockedNode) {
        lockedNode = "";
        drawCurves("");
        applyCrossTabHighlight("");
        persistPref({ lockedNode: "", lockedNodeSlug: "" });
      }
    });
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible") pollDigestOnce();
    });

    bindInteractiveHandlers();

    // Open live EventSource ('/api/events') connection for instant push reloads and focus events.
    const es = new EventSource("/api/events");
    es.addEventListener("open", () => { pollDigestOnce(); });
    es.addEventListener("reload", () => { refreshLensDomInPlace(); });
    es.addEventListener("focus", (ev) => {
      try {
        const p = JSON.parse(ev.data);
        if (p.theme) document.documentElement.dataset.lensTheme = p.theme;
        if (p.reload) {
          refreshLensDomInPlace().then(() => { if (p.activeTab) switchTab(p.activeTab, false); });
        } else if (p.activeTab) {
          switchTab(p.activeTab, false);
        }
      } catch {}
    });

    pollDigestOnce();
    setInterval(pollDigestOnce, 1200);

    window.addEventListener("resize", () => drawCurves(lockedNode));
    window.addEventListener("load", () => { upgradeKatexIfLoaded(); applyGroundingFade(); renderPdfState(); drawCurves(lockedNode); applyCrossTabHighlight(lockedNode); });
`;

// Render the complete Facet Lens HTML document from the workspace `snapshot`.
export function renderLensHtml(snapshot) {
    const prefs = snapshot.prefs || {};
    const theme = prefs.theme === "light" ? "light" : "dark";
    const activeTab = prefs.activeTab || "traceability";
    const defaultSrcMode =
        prefs.activeSrcMode ||
        (snapshot.imageRel ? "image" : snapshot.corpusDocs.length > 0 ? "multipdf" : "literature");

    const st = stageFlags(snapshot.currentStage, snapshot.isArchivedView);
    const sealShort = snapshot.sealSha ? snapshot.sealSha.slice(0, 8) : "pending";
    const implFormatted = addLineNumbers(snapshot.implCode, snapshot.lineSpecTags || []);
    const suiteFormatted = addLineNumbers(snapshot.suiteCode, []);

    const hashRows = Object.entries(snapshot.artifactHashes || {})
        .slice(0, 8)
        .map(
            ([fn, sha]) =>
                `<div class="pop-row"><span>${escapeHtml(fn)}</span><span class="mono">${escapeHtml(String(sha).slice(0, 8))}...${escapeHtml(String(sha).slice(-8))}</span></div>`
        )
        .join("");

    // Traceability Column 1: `SPEC-k` governing equation cards from `manifest.json`.
    const specCardsHtml = (snapshot.specs.length > 0
        ? snapshot.specs
        : [{ id: "SPEC-1", title: "Awaiting manifest.json", latex: "", status: "pending", detail: "Stage 1 populates formal specifications." }]
    )
        .map(
            (sp) => `
            <article class="node-card" data-node="${escapeHtml(sp.id)}" data-detail-html="${escapeHtml(formatMixedProseWithMathHtml(sp.detail || sp.title))}">
              <span class="port port-out"></span>
              <div class="node-top">
                <span class="node-kicker">${escapeHtml(sp.id)}</span>
                <span class="quiet-meta">${escapeHtml(sp.status)}</span>
              </div>
              <div class="node-title">${formatMixedProseWithMathHtml(sp.title)}</div>
              <div class="math-well">${formatMatrixOrEquationHtml(sp.latex)}</div>
            </article>`
        )
        .join("");

    // Traceability Column 2: typed Python public entrypoint cards from `mapping.json`.
    const fnCardsHtml = snapshot.entrypoints.length > 0
        ? snapshot.entrypoints
              .map((fn, idx) => {
                  const pNames = fn.params.map((p) => escapeHtml(p.name)).join(", ");
                  const pRows = fn.params
                      .map((p) => `<div class="sig-row"><span class="sig-param">${escapeHtml(p.name)}</span><span class="sig-type">${escapeHtml(p.type)}</span></div>`)
                      .join("");
                  return `
            <article class="node-card" data-node="${escapeHtml(fn.nodeId)}" data-detail-html="${escapeHtml(formatMixedProseWithMathHtml(fn.detail))}">
              <span class="port port-in"></span><span class="port port-out"></span>
              <div class="node-top"><span class="node-kicker">ENTRYPOINT ${String(idx + 1).padStart(2, "0")}</span><span class="quiet-meta">pure fn</span></div>
              <div class="fn-headline mono">${escapeHtml(fn.name)}<span>(${pNames})</span></div>
              <div class="sig-well mono">
                ${pRows}
                <div class="sig-ret"><span>returns</span><span>${escapeHtml(fn.returnType)}</span></div>
              </div>
            </article>`;
              })
              .join("")
        : `<div class="pending-slot">
              <span class="node-kicker">STAGE 2 &middot; FORMALIST</span>
              <div class="pending-slot-title">Typed Python API Pending</div>
              <p class="pending-slot-sub">Discretizes specification equations into typed function signatures and domain preconditions in <span class="mono">mapping.json</span>.</p>
           </div>`;

    // Traceability Column 3: 4-predicate cleanroom obligation (`OBL-j`) verdict cards.
    const oblCardsHtml = snapshot.obligations.length > 0
        ? snapshot.obligations
              .map(
                  (ob) => `
            <article class="node-card obl-card" data-node="${escapeHtml(ob.id)}" data-detail-html="${escapeHtml(formatMixedProseWithMathHtml(ob.predicate || ob.description))}">
              <span class="port port-in"></span>
              <div class="node-top">
                <span class="node-kicker">${escapeHtml(ob.id)} &middot; ${escapeHtml(ob.basis)}</span>
                <span class="pass-label"><span class="status-pip${ob.verdict === "PASS" ? "" : " amber"}"></span>${escapeHtml(ob.verdict)}</span>
              </div>
              <div class="obl-desc">${formatMixedProseWithMathHtml(ob.description)}</div>
            </article>`
              )
              .join("")
        : `<div class="pending-slot">
              <span class="node-kicker">STAGE 2 &rarr; 4 &middot; CLEANROOM</span>
              <div class="pending-slot-title">4-Predicate Proofs Pending</div>
              <p class="pending-slot-sub">Anchor vectors, reference models, metamorphic laws, and Hypothesis postconditions.</p>
           </div>`;

    // Tab 3 (`4-Predicate Proofs`): detailed obligation predicates and tolerance bounds.
    const proofCellsHtml = (snapshot.obligations.length > 0 ? snapshot.obligations : [])
        .map(
            (ob) => `
          <article class="proof-cell" data-obl="${escapeHtml(ob.id)}" data-covers="${escapeHtml(ob.covers.join(","))}">
            <div class="node-top">
              <span class="node-kicker">${escapeHtml(ob.id)} &middot; ${escapeHtml(ob.basis)}</span>
              <span class="pass-label"><span class="status-pip${ob.verdict === "PASS" ? "" : " amber"}"></span>${escapeHtml(ob.verdict)}</span>
            </div>
            <div class="node-title">${formatMixedProseWithMathHtml(ob.description)}</div>
            <p class="proof-rationale">Covers ${escapeHtml(ob.covers.join(", ") || "specification contract")} within rtol=${escapeHtml(snapshot.rtolLabel)}, atol=${escapeHtml(snapshot.atolLabel)}.</p>
            <div class="proof-expr mono">${escapeHtml(ob.predicate)}</div>
          </article>`
        )
        .join("") || `<div class="proof-cell"><div class="node-title">No verification obligations recorded yet</div><p class="proof-rationale">Formalist populates obligations in Stage 2 and Auditor verifies them in Stage 4.</p></div>`;

    // Tab 3 bottom section: Reflexion & Spec-Rewind Counterexample Inspector diff cards.
    const repairInspectorHtml = Array.isArray(snapshot.repairHistory) && snapshot.repairHistory.length > 0
        ? `<div class="repair-inspector">
            <div class="pane-header" style="border-top:1px solid var(--border-grid)">
              <span class="pane-title">Reflexion &amp; Spec-Rewind Counterexample Inspector</span>
              <span class="mono" style="font-size:11px;color:var(--ink-secondary)">${snapshot.repairHistory.length} recorded audit finding${snapshot.repairHistory.length === 1 ? "" : "s"}</span>
            </div>
            <div class="repair-list">
              ${snapshot.repairHistory
                  .map(
                      (item) => `
                <article class="repair-card">
                  <div class="node-top" style="margin-bottom:8px">
                    <span class="node-kicker">ATTEMPT ${item.attempt} &middot; ${escapeHtml(item.obligationId)}${item.specId ? ` &rarr; ${escapeHtml(item.specId)}` : ""}</span>
                    <span class="pass-label"><span class="status-pip${item.resolved ? "" : " amber"}"></span>${escapeHtml(item.classification)}${item.resolved ? " &middot; RESOLVED" : " &middot; OPEN"}</span>
                  </div>
                  <p class="proof-rationale" style="margin-bottom:10px">${formatMixedProseWithMathHtml(item.diagnosis)}</p>
                  ${
                      item.expected || item.actual
                          ? `<div class="repair-diff-grid mono">
                               <div class="repair-diff-col"><div class="kv-label">Expected / Oracle</div><div>${escapeHtml(item.expected || "—")}</div></div>
                               <div class="repair-diff-col"><div class="kv-label">Observed / Counterexample</div><div>${escapeHtml(item.actual || "—")}</div></div>
                             </div>`
                          : ""
                  }
                </article>`
                  )
                  .join("")}
            </div>
          </div>`
        : "";

    const varRowsHtml = (snapshot.variables || [])
        .map(
            (v) =>
                `<tr><td class="sym-cell">${formatMathSymbolHtml(v.symbol || "")}</td><td class="mono">${escapeHtml(v.domain || "")}${v.dimensions ? ` <span style="color:var(--ink-muted)">(${formatMixedProseWithMathHtml(v.dimensions)})</span>` : ""}</td><td>${formatMixedProseWithMathHtml(v.description || "")}</td></tr>`
        )
        .join("");

    const constraintsListHtml = (snapshot.constraints || [])
        .map((c) => `<li class="constraint-item">${formatMixedProseWithMathHtml(c)}</li>`)
        .join("");

    const corpusRowsHtml = (snapshot.corpusDocs.length > 0
        ? snapshot.corpusDocs
        : [{ name: "No local research/ PDFs in this run", kind: "DIR", role: "research/", hasUncertain: false, pages: 1, excerpts: ["Place PDFs, diagrams, or notes inside research/ to inspect multi-document grounding here."] }]
    )
        .map(
            (doc, idx) => `
                <button class="corpus-row${idx === 0 ? " active" : ""}" data-pdf="${idx}" type="button">
                  <span class="corpus-row-left">
                    <span class="corpus-idx mono">${String(idx + 1).padStart(2, "0")}</span>
                    <span class="kind-tag">${escapeHtml(doc.kind)}</span>
                    <span class="corpus-name mono">${escapeHtml(doc.name)}</span>
                  </span>
                  <span class="corpus-row-right mono">
                    <span>${escapeHtml(doc.role)}</span>
                    <span>&middot;</span>
                    <span class="status-pip${doc.hasUncertain ? " amber" : ""}"></span>
                    <span>${doc.pages} pp</span>
                  </span>
                </button>`
        )
        .join("");

    const refCardsHtml = (snapshot.references.length > 0
        ? snapshot.references
        : [{ kicker: "CITED REFERENCES", meta: "Pending", title: "No literature citations recorded yet", detail: "Stage 0/1 populates canonical references in RESEARCH.md and manifest.json." }]
    )
        .map(
            (ref) => `
              <div class="cite-card">
                <div class="node-top">
                  <span class="node-kicker">${escapeHtml(ref.kicker)}</span>
                  <span class="quiet-meta">${escapeHtml(ref.meta)}</span>
                </div>
                <div class="node-title" style="margin-bottom:4px">${formatMixedProseWithMathHtml(ref.title)}</div>
                <p style="font-size:12px;color:var(--ink-secondary)">${formatMixedProseWithMathHtml(ref.detail)}</p>
              </div>`
        )
        .join("");

    const archiveShelfHtml = (snapshot.sealedSpecs.length > 0 ? snapshot.sealedSpecs : [])
        .map(
            (sp) => `
          <div class="node-card spec-shelf-card${sp.slug === snapshot.activeSlug ? " focused" : ""}" data-specslug="${escapeHtml(sp.slug)}">
            <div class="node-top">
              <span class="node-kicker" title="${escapeHtml(sp.slug)}">${escapeHtml(sp.slug)}</span>
              <span class="pass-label"><span class="status-pip${sp.sealVerified ? "" : " amber"}"></span>${sp.sealVerified ? "SEALED" : "CHECK"}</span>
            </div>
            <div class="node-title">${escapeHtml(sp.title)}</div>
            <div class="mono" style="font-size:11.5px;color:var(--ink-secondary)">${sp.obligationCount}/${sp.obligationCount} PASS &middot; seal ${escapeHtml(sp.sealSha.slice(0, 8))}</div>
          </div>`
        )
        .join("") || `<div class="node-card" style="cursor:default"><div class="node-title">No sealed packages in specs/ yet</div><div class="mono" style="font-size:11.5px;color:var(--ink-secondary)">Completed runs seal automatically at Stage 5.</div></div>`;

    const formulationsStackHtml = snapshot.specs.length > 0
        ? snapshot.specs
              .map(
                  (sp) => `
                <div class="form-card" data-spec="${escapeHtml(sp.id)}">
                  <div class="node-top" style="margin-bottom:6px">
                    <span class="node-kicker">${escapeHtml(sp.id)} &middot; ${formatMixedProseWithMathHtml(sp.title)}</span>
                    <span class="quiet-meta">${escapeHtml(sp.status)}</span>
                  </div>
                  <div class="math-well" style="padding:14px 16px;font-size:16px">${formatMatrixOrEquationHtml(sp.latex)}</div>
                </div>`
              )
              .join("")
        : `<div class="empty-well">No specification equations recorded yet. Run Stage 0 or Stage 1 in Chat to populate formulations.</div>`;

    const leftPaneTitleByMode = {
        image: "Optical Source Plate",
        multipdf: "Research Corpus Index",
        literature: "Cited Literature & Sources",
    };
    const leftPaneMetaByMode = {
        image: `${snapshot.ambiguityCount} ambiguities`,
        multipdf: `${snapshot.corpusDocs.length} document${snapshot.corpusDocs.length === 1 ? "" : "s"}`,
        literature: `${snapshot.references.length} source${snapshot.references.length === 1 ? "" : "s"}`,
    };
    const initialLeftTitle = leftPaneTitleByMode[defaultSrcMode] || "Cited Literature & Sources";
    const initialLeftMeta = leftPaneMetaByMode[defaultSrcMode] || `${snapshot.references.length} sources`;
    const isCheckpoint = snapshot.currentStage.startsWith("CHECKPOINT_");
    const footerStatus = snapshot.isArchivedView
        ? `Live sync · Viewing sealed package specs/${escapeHtml(snapshot.activeSlug)}`
        : `Live sync · ${escapeHtml(snapshot.currentStage.replace(/_/g, " ").toLowerCase())}`;
    const footerCue = isCheckpoint ? "Ask a question or reply /approve in Chat" : "Steer and approve stages in Chat";
    const rawVerifyBase = String(snapshot.verifyFileName || "verify_suite.py");
    const compactVerifyBase = rawVerifyBase.length > 24 ? `${rawVerifyBase.slice(0, 21)}...py` : rawVerifyBase;
    const copyPytestLabel = `Copy pytest · ${compactVerifyBase}`;

    const effectiveLockedNode =
        prefs.lockedNodeSlug && prefs.lockedNodeSlug !== snapshot.activeSlug ? "" : (prefs.lockedNode || "");

    // Embed serialized graph edges, lookup tables, and corpus docs in `<script id="boot-data">`
    // (escaping `<` as `\u003c` so user/equation strings cannot break out of the JSON tag).
    const clientBootJson = JSON.stringify({
        edges: snapshot.edges,
        specToObls: snapshot.specToObls || {},
        oblToSpecs: snapshot.oblToSpecs || {},
        specLineMap: snapshot.specLineMap || {},
        activeSlug: snapshot.activeSlug || "",
        lockedNode: effectiveLockedNode,
        pinnedExcerpts: Array.isArray(prefs.pinnedExcerpts) ? prefs.pinnedExcerpts : [],
        activeTab,
        defaultSrcMode,
        leftPaneTitleByMode,
        leftPaneMetaByMode,
        groundingLabel: snapshot.groundingLabel,
        corpusDocs: snapshot.corpusDocs.length > 0
            ? snapshot.corpusDocs
            : [{ name: "research/", pages: 1, hasUncertain: false, excerpts: ["Place PDFs or figures in research/ for multi-document optical extraction."] }],
        pytestCmd: snapshot.pytestCmd,
    }).replace(/</g, "\\u003c");

    return `<!DOCTYPE html>
<html lang="en" data-lens-theme="${theme}">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Facet Lens — ${escapeHtml(snapshot.conceptTitle)}</title>
  <link rel="preconnect" href="https://fonts.googleapis.com" />
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=JetBrains+Mono:wght@400;500;600&family=Space+Grotesk:wght@500;600&display=swap" rel="stylesheet" />
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/katex.min.css" crossorigin="anonymous" />
  <script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/katex.min.js" crossorigin="anonymous"></script>
  <style>
    /* Dark & Light Design-System Color, Border, Radius, and Shadow Tokens */
    :root, [data-lens-theme="dark"] {
      --bg-canvas: #0b0d10;
      --bg-chrome: rgba(11, 13, 16, 0.86);
      --bg-frame: #111419;
      --bg-pane-head: rgba(255, 255, 255, 0.022);
      --bg-card: linear-gradient(180deg, #171b22 0%, #13171d 100%);
      --bg-card-solid: #151920;
      --bg-card-hover: linear-gradient(180deg, #1d222b 0%, #171b23 100%);
      --bg-well: #0d1014;
      --border-grid: rgba(255, 255, 255, 0.075);
      --border-bevel: rgba(255, 255, 255, 0.12);
      --border-strong: rgba(255, 255, 255, 0.17);
      --border-focus: rgba(74, 212, 255, 0.62);
      --ink-display: #f8fafc;
      --ink-primary: #e2e6ee;
      --ink-secondary: #929aa8;
      --ink-muted: #616977;
      --brand-cyan: #33d6ff;
      --brand-sky: #7cb3ff;
      --brand-cobalt: #4c8df6;
      --emerald-ink: #6ee7a8;
      --emerald-bg: rgba(46, 190, 126, 0.1);
      --emerald-border: rgba(110, 231, 168, 0.22);
      --amber-ink: #fbcc5c;
      --amber-bg: rgba(251, 188, 4, 0.1);
      --amber-border: rgba(251, 204, 92, 0.24);
      --dot-grid: rgba(255, 255, 255, 0.05);
      --aura-a: rgba(51, 214, 255, 0.06);
      --aura-b: rgba(76, 141, 246, 0.07);
      --curve-rest: rgba(124, 179, 255, 0.26);
      --port-bg: #0e1116;
      --r-outer: 9px;
      --r-card: 8px;
      --r-inner: 5px;
      --shadow-card: 0 1px 0 rgba(255, 255, 255, 0.05) inset, 0 8px 22px -12px rgba(0, 0, 0, 0.6);
      --shadow-focus: 0 1px 0 rgba(168, 228, 255, 0.24) inset, 0 0 0 1px rgba(76, 141, 246, 0.36), 0 14px 32px -12px rgba(32, 92, 214, 0.34);
      --shadow-pop: 0 22px 48px rgba(0, 0, 0, 0.65);
    }
    [data-lens-theme="light"] {
      --bg-canvas: #f6f8fb;
      --bg-chrome: rgba(246, 248, 251, 0.9);
      --bg-frame: #ffffff;
      --bg-pane-head: #f8fafd;
      --bg-card: linear-gradient(180deg, #ffffff 0%, #fafbfd 100%);
      --bg-card-solid: #ffffff;
      --bg-card-hover: linear-gradient(180deg, #ffffff 0%, #f3f7fe 100%);
      --bg-well: #f2f5fa;
      --border-grid: rgba(15, 23, 42, 0.085);
      --border-bevel: rgba(15, 23, 42, 0.11);
      --border-strong: rgba(15, 23, 42, 0.18);
      --border-focus: rgba(26, 115, 232, 0.55);
      --ink-display: #0b1220;
      --ink-primary: #1e293b;
      --ink-secondary: #525e70;
      --ink-muted: #7b8596;
      --brand-cyan: #0284c7;
      --brand-sky: #1a73e8;
      --brand-cobalt: #1a73e8;
      --emerald-ink: #0f7638;
      --emerald-bg: rgba(15, 118, 56, 0.07);
      --emerald-border: rgba(15, 118, 56, 0.2);
      --amber-ink: #a15c00;
      --amber-bg: rgba(217, 119, 6, 0.08);
      --amber-border: rgba(180, 83, 9, 0.22);
      --dot-grid: rgba(15, 23, 42, 0.055);
      --aura-a: rgba(14, 165, 233, 0.05);
      --aura-b: rgba(59, 91, 219, 0.05);
      --curve-rest: rgba(26, 115, 232, 0.26);
      --port-bg: #ffffff;
      --shadow-card: 0 1px 2px rgba(15, 23, 42, 0.04), 0 6px 18px -10px rgba(15, 23, 42, 0.1);
      --shadow-focus: 0 0 0 1.5px rgba(26, 115, 232, 0.45), 0 10px 24px -10px rgba(26, 115, 232, 0.16);
      --shadow-pop: 0 18px 40px rgba(15, 23, 42, 0.14);
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    html, body { height: 100%; }
    body {
      background-color: var(--bg-canvas);
      background-image:
        radial-gradient(680px 300px at 18% -4%, var(--aura-a), transparent 72%),
        radial-gradient(600px 320px at 82% 2%, var(--aura-b), transparent 74%);
      color: var(--ink-primary);
      font-family: "Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      font-size: 13px;
      line-height: 1.52;
      height: 100vh;
      display: flex;
      flex-direction: column;
      overflow: hidden;
      -webkit-font-smoothing: antialiased;
    }
    .mono { font-family: "JetBrains Mono", "SF Mono", ui-monospace, monospace; font-variant-numeric: tabular-nums; }
    .nowrap { white-space: nowrap; }
    .topbar {
      flex-shrink: 0;
      position: sticky; top: 0; z-index: 40; height: 52px; padding: 0 28px;
      background: var(--bg-chrome); backdrop-filter: blur(18px); border-bottom: 1px solid var(--border-grid);
      display: grid; grid-template-columns: minmax(180px, 1fr) auto minmax(180px, 1fr); align-items: center; gap: 16px;
    }
    .brand { display: flex; align-items: center; gap: 10px; }
    .brand-mark { width: 23px; height: 23px; filter: drop-shadow(0 0 9px rgba(51, 214, 255, 0.22)); }
    .brand-wordmark { font-family: "Space Grotesk", sans-serif; font-size: 15.5px; font-weight: 600; letter-spacing: -0.03em; color: var(--ink-display); }
    .brand-divider { width: 1px; height: 14px; background: var(--border-strong); }
    .brand-sub { font-size: 11px; font-weight: 500; letter-spacing: 0.1em; text-transform: uppercase; color: var(--ink-secondary); }
    .timeline { display: flex; align-items: center; gap: 2px; }
    .tl-node {
      display: inline-flex; align-items: center; gap: 7px; height: 28px; padding: 0 9px;
      border: none; border-radius: var(--r-inner); background: transparent; color: var(--ink-muted);
      font-family: inherit; font-size: 12px; font-weight: 500; cursor: pointer;
    }
    .tl-node:hover { color: var(--ink-primary); background: rgba(255, 255, 255, 0.035); }
    .tl-pip { width: 6.5px; height: 6.5px; border-radius: 50%; border: 1.5px solid var(--ink-muted); }
    .tl-node.done { color: var(--ink-secondary); }
    .tl-node.done .tl-pip { background: var(--emerald-ink); border-color: var(--emerald-ink); }
    .tl-node.active { color: var(--ink-display); }
    .tl-node.active .tl-pip { background: var(--brand-cyan); border-color: var(--brand-cyan); box-shadow: 0 0 0 3px rgba(51, 214, 255, 0.17); }
    .tl-rule { width: 15px; height: 1px; background: var(--border-grid); }
    .tl-rule.done { background: rgba(110, 231, 168, 0.34); }
    .top-actions { display: flex; align-items: center; justify-content: flex-end; gap: 8px; }
    .seal-btn {
      display: inline-flex; align-items: center; gap: 7px; height: 28px; padding: 0 10px;
      border-radius: var(--r-inner); border: 1px solid var(--emerald-border); background: var(--emerald-bg);
      color: var(--emerald-ink); font-family: inherit; font-size: 11.5px; font-weight: 500; cursor: pointer;
    }
    .seal-dot { width: 6px; height: 6px; border-radius: 50%; background: var(--emerald-ink); }
    .theme-btn {
      height: 28px; padding: 0 10px; border-radius: var(--r-inner); border: 1px solid var(--border-grid);
      background: transparent; color: var(--ink-secondary); font-family: inherit; font-size: 11.5px; cursor: pointer;
    }
    .theme-btn:hover { color: var(--ink-display); border-color: var(--border-strong); }
    .seal-popover {
      display: none; position: absolute; right: 28px; top: 58px; width: 430px;
      background: var(--bg-card-solid); border: 1px solid var(--border-strong); border-radius: var(--r-outer);
      padding: 15px 17px; box-shadow: var(--shadow-pop); z-index: 60;
    }
    .seal-popover.open { display: block; }
    .pop-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 9px; }
    .pop-title { font-size: 12px; font-weight: 600; color: var(--ink-display); }
    .pop-row { display: flex; justify-content: space-between; padding: 6px 0; border-top: 1px solid var(--border-grid); font-size: 11.5px; }
    .pop-row span:first-child { color: var(--ink-secondary); }
    /* Main Workbench Viewport, Hero Masthead, and 4-Column Contract Summary Ledger */
    .viewport {
      flex: 1; min-height: 0; max-width: 1260px; width: 100%; margin: 0 auto;
      padding: 20px 32px 18px; display: flex; flex-direction: column; overflow: hidden;
    }
    .masthead { flex-shrink: 0; display: grid; grid-template-columns: 1.15fr auto; align-items: end; gap: 28px; padding-bottom: 16px; }
    .eyebrow { display: inline-flex; align-items: center; gap: 8px; font-size: 10.5px; font-weight: 600; letter-spacing: 0.11em; text-transform: uppercase; color: var(--brand-sky); margin-bottom: 6px; }
    .eyebrow-sep { width: 3px; height: 3px; border-radius: 50%; background: var(--ink-muted); }
    .hero-title { font-family: "Space Grotesk", "Inter", sans-serif; font-size: 30px; font-weight: 600; letter-spacing: -0.035em; line-height: 1.12; color: var(--ink-display); }
    .kv-ledger { display: grid; grid-template-columns: 164px 154px 132px 124px; border-left: 1px solid var(--border-grid); }
    .kv-cell { padding: 2px 16px; border-right: 1px solid var(--border-grid); min-width: 0; overflow: hidden; }
    .kv-label { font-size: 10px; font-weight: 600; letter-spacing: 0.09em; text-transform: uppercase; color: var(--ink-muted); margin-bottom: 4px; white-space: nowrap; }
    .kv-value { display: block; width: 100%; font-size: 12.5px; font-weight: 500; color: var(--ink-display); white-space: nowrap; overflow: hidden; }
    .kv-value.is-faded { -webkit-mask-image: linear-gradient(90deg, #000 70%, transparent 100%); mask-image: linear-gradient(90deg, #000 70%, transparent 100%); }
    .kv-value.emerald { color: var(--emerald-ink); }
    .subnav { flex-shrink: 0; display: flex; align-items: center; border-bottom: 1px solid var(--border-grid); margin-bottom: 16px; overflow-x: auto; scrollbar-width: none; }
    .subnav::-webkit-scrollbar { display: none; }
    .tab-strip { display: flex; gap: 24px; }
    .tab-btn {
      position: relative; padding: 8px 2px 10px; border: none; background: transparent;
      color: var(--ink-secondary); font-family: inherit; font-size: 12.5px; font-weight: 500;
      white-space: nowrap; flex-shrink: 0; cursor: pointer;
    }
    .tab-btn:hover, .tab-btn[aria-selected="true"] { color: var(--ink-display); }
    .tab-btn[aria-selected="true"]::after {
      content: ""; position: absolute; left: 0; right: 0; bottom: -1px; height: 2px;
      background: linear-gradient(90deg, var(--brand-cyan), var(--brand-cobalt)); box-shadow: 0 0 10px rgba(51, 214, 255, 0.4);
    }
    .view-panel { display: none; }
    .view-panel.active { flex: 1; min-height: 0; display: flex; flex-direction: column; }
    .inline-status { display: inline-flex; align-items: center; gap: 6px; font-size: 11.5px; color: var(--ink-secondary); white-space: nowrap; }
    .status-pip { width: 6px; height: 6px; border-radius: 50%; background: var(--emerald-ink); flex-shrink: 0; }
    .status-pip.amber { background: var(--amber-ink); }
    .swiss-frame {
      flex: 1; min-height: 0; display: flex; flex-direction: column;
      border: 1px solid var(--border-grid); border-radius: var(--r-outer); background: var(--bg-frame);
      overflow: hidden; box-shadow: var(--shadow-card);
    }
    .frame-scroll {
      flex: 1; min-height: 0; overflow-y: auto; overflow-x: auto;
      scrollbar-gutter: stable; scrollbar-width: thin; scrollbar-color: rgba(148, 163, 184, 0.24) transparent;
    }
    .frame-scroll.dot-surface { background-image: radial-gradient(var(--dot-grid) 1px, transparent 1px); background-size: 20px 20px; padding: 20px 22px 22px; }
    .pane-body { padding: 18px 20px 20px; flex: 1; }
    .pane-body.dot-stage { background-color: var(--bg-frame); background-image: radial-gradient(var(--dot-grid) 1px, transparent 1px); background-size: 20px 20px; }
    .swiss-split { display: grid; grid-template-columns: 1.02fr 1.18fr; min-height: 100%; }
    .swiss-pane { display: flex; flex-direction: column; min-width: 0; }
    .swiss-pane + .swiss-pane { border-left: 1px solid var(--border-grid); }
    .pane-header {
      flex-shrink: 0; height: 40px; padding: 0 18px; background: var(--bg-pane-head);
      border-bottom: 1px solid var(--border-grid); display: flex; align-items: center; justify-content: space-between; gap: 12px;
    }
    .pane-title { font-size: 10.5px; font-weight: 600; letter-spacing: 0.09em; text-transform: uppercase; color: var(--ink-muted); }
    .modality-bar { flex-shrink: 0; display: flex; align-items: center; justify-content: space-between; gap: 10px; padding: 9px 18px; border-bottom: 1px solid var(--border-grid); background: var(--bg-well); }
    .source-tabs { display: flex; flex-wrap: wrap; gap: 6px; }
    .src-chip {
      display: inline-flex; align-items: center; gap: 6px; height: 25px; padding: 0 9px;
      border-radius: var(--r-inner); border: 1px solid var(--border-grid); background: transparent;
      color: var(--ink-secondary); font-family: inherit; font-size: 11.5px; cursor: pointer;
    }
    .src-chip:hover:not(:disabled) { color: var(--ink-display); border-color: var(--border-strong); }
    .src-chip:disabled { opacity: 0.38; cursor: default; }
    .src-chip.active { background: rgba(76, 141, 246, 0.13); border-color: rgba(124, 179, 255, 0.42); color: var(--ink-display); }
    .kind-tag { font-family: "JetBrains Mono", monospace; font-size: 9.5px; font-weight: 600; color: var(--brand-sky); }
    .corpus-pane { padding: 0; display: flex; flex-direction: column; flex: 1; }
    .corpus-row {
      position: relative; width: 100%; height: 38px; padding: 0 18px; border: none; border-bottom: 1px solid var(--border-grid);
      background: transparent; color: var(--ink-secondary); font-family: inherit; font-size: 12px;
      display: flex; align-items: center; justify-content: space-between; gap: 12px; cursor: pointer; text-align: left;
    }
    .corpus-row:hover { background: var(--bg-pane-head); color: var(--ink-display); }
    .corpus-row.active { background: rgba(76, 141, 246, 0.08); color: var(--ink-display); }
    .corpus-row.active::before { content: ""; position: absolute; left: 0; top: 0; bottom: 0; width: 2px; background: linear-gradient(180deg, var(--brand-cyan), var(--brand-cobalt)); }
    .corpus-row-left { display: flex; align-items: center; gap: 9px; min-width: 0; }
    .corpus-idx { font-size: 10.5px; color: var(--ink-muted); }
    .corpus-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-weight: 500; }
    .corpus-row-right { display: inline-flex; align-items: center; gap: 8px; flex-shrink: 0; font-size: 11px; color: var(--ink-muted); }
    .corpus-Reader-bar { height: 36px; padding: 0 18px; background: var(--bg-pane-head); border-bottom: 1px solid var(--border-grid); display: flex; align-items: center; justify-content: space-between; gap: 12px; font-size: 11.5px; color: var(--ink-secondary); }
    .stepper-group { display: inline-flex; align-items: center; border: 1px solid var(--border-grid); border-radius: 4px; background: var(--bg-well); overflow: hidden; }
    .step-arrow { width: 24px; height: 22px; border: none; background: transparent; color: var(--ink-primary); cursor: pointer; font-size: 13px; }
    .step-arrow:disabled { opacity: 0.32; cursor: default; }
    .step-counter { padding: 0 8px; border-left: 1px solid var(--border-grid); border-right: 1px solid var(--border-grid); font-size: 11px; color: var(--ink-display); line-height: 22px; }
    .corpus-stage { flex: 1; padding: 18px 20px; background-color: var(--bg-frame); background-image: radial-gradient(var(--dot-grid) 1px, transparent 1px); background-size: 20px 20px; display: flex; flex-direction: column; gap: 12px; }
    .corpus-quote { background: var(--bg-well); border: 1px solid var(--border-grid); border-radius: var(--r-inner); padding: 14px 16px; font-size: 12.6px; line-height: 1.65; color: var(--ink-primary); }
    .plate-well { background: var(--bg-card); border: 1px solid var(--border-grid); border-top-color: var(--border-bevel); border-radius: var(--r-card); box-shadow: var(--shadow-card); padding: 16px; display: flex; flex-direction: column; align-items: center; gap: 10px; }
    .plate-well img { max-width: 100%; max-height: 278px; border-radius: 4px; object-fit: contain; }
    .cite-card { background: var(--bg-card); border: 1px solid var(--border-grid); border-top-color: var(--border-bevel); border-radius: var(--r-card); box-shadow: var(--shadow-card); padding: 13px 15px; }
    .cite-card + .cite-card { margin-top: 10px; }
    /* 3-Column Bezier Traceability Stage, SVG Curve Overlay, and Interactive Node Cards */
    .trace-stage { position: relative; display: grid; grid-template-columns: 1.04fr 1.16fr 1.06fr; gap: 60px; align-items: stretch; min-width: 780px; }
    .trace-svg { position: absolute; inset: 0; width: 100%; height: 100%; pointer-events: none; z-index: 1; overflow: visible; }
    @keyframes pulseFlow { from { stroke-dashoffset: 36; } to { stroke-dashoffset: 0; } }
    .curve-flow { stroke-dasharray: 5 13; animation: pulseFlow 2.6s linear infinite; }
    .trace-col { position: relative; z-index: 2; display: flex; flex-direction: column; gap: 10px; }
    .trace-col-cards { flex: 1; display: grid; grid-auto-rows: 1fr; gap: 12px; }
    .col-header { flex: 0 0 18px; height: 18px; display: flex; align-items: center; justify-content: space-between; gap: 10px; font-size: 10px; font-weight: 600; letter-spacing: 0.1em; text-transform: uppercase; color: var(--ink-muted); padding: 0 2px; }
    .col-file { font-family: "JetBrains Mono", monospace; font-size: 10.5px; letter-spacing: 0; text-transform: none; color: var(--ink-secondary); max-width: 170px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .pending-slot { border: 1px dashed var(--border-strong); border-radius: var(--r-card); background: rgba(255, 255, 255, 0.012); padding: 24px 20px; display: flex; flex-direction: column; align-items: center; justify-content: center; text-align: center; gap: 8px; }
    .pending-slot-title { font-family: "Space Grotesk", "Inter", sans-serif; font-size: 14px; font-weight: 500; color: var(--ink-display); }
    .pending-slot-sub { font-size: 12px; line-height: 1.55; color: var(--ink-secondary); max-width: 240px; }
    .trace-col .node-card { min-height: 0; display: flex; flex-direction: column; justify-content: space-between; }
    .trace-col .node-card .math-well, .trace-col .node-card .sig-well { margin-top: auto; }
    .trace-col .obl-card { justify-content: flex-start; padding: 12px 16px; }
    .trace-col .obl-card .node-top { padding-right: 62px; margin-bottom: 5px; }
    .trace-col .obl-card .pass-label { position: absolute; top: 12px; right: 16px; }
    .node-card { position: relative; background: var(--bg-card); border: 1px solid var(--border-grid); border-top-color: var(--border-bevel); border-radius: var(--r-card); padding: 14px 16px; box-shadow: var(--shadow-card); cursor: pointer; }
    .node-card:hover { background: var(--bg-card-hover); border-color: var(--border-strong); }
    .node-card.focused { border-color: var(--border-focus); box-shadow: var(--shadow-focus); }
    .node-card.connected { border-color: rgba(124, 179, 255, 0.34); }
    .node-card.dimmed { opacity: 0.28; }
    .port { position: absolute; top: 50%; width: 8px; height: 8px; margin-top: -4px; border-radius: 50%; background: var(--port-bg); border: 1.6px solid rgba(124, 179, 255, 0.48); z-index: 3; }
    .port-out { right: -4.5px; }
    .port-in { left: -4.5px; }
    .node-card.focused .port, .node-card.connected .port { background: var(--brand-cyan); border-color: #d9f7ff; box-shadow: 0 0 8px var(--brand-cyan); }
    .node-top { display: flex; align-items: center; justify-content: space-between; gap: 18px; margin-bottom: 6px; min-width: 0; }
    .node-kicker { font-family: "JetBrains Mono", monospace; font-size: 10.5px; font-weight: 600; color: var(--brand-sky); min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .quiet-meta { font-family: "JetBrains Mono", monospace; font-size: 10.5px; color: var(--ink-muted); flex-shrink: 0; white-space: nowrap; }
    .pass-label { display: inline-flex; align-items: center; gap: 5px; font-family: "JetBrains Mono", monospace; font-size: 10.5px; font-weight: 600; color: var(--emerald-ink); flex-shrink: 0; white-space: nowrap; }
    .node-title { font-family: "Space Grotesk", "Inter", sans-serif; font-size: 13.5px; font-weight: 500; letter-spacing: -0.015em; color: var(--ink-display); margin-bottom: 8px; }
    .fn-headline { font-size: 12.5px; font-weight: 600; color: var(--ink-display); margin-bottom: 8px; }
    .fn-headline span { color: var(--brand-cyan); font-weight: 400; }
    /* Offline Math Typography: Fractions, Radicals, Matrices, and Cross-Tab Highlighting */
    .math-well { background: var(--bg-well); border: 1px solid var(--border-grid); border-radius: var(--r-inner); padding: 10px 13px; display: flex; align-items: center; justify-content: center; font-family: "STIX Two Math", "Cambria Math", Georgia, serif; font-size: 15.5px; color: var(--ink-display); gap: 7px; overflow-x: auto; }
    .math-expr, .math-sym { font-family: "STIX Two Math", "Cambria Math", "Times New Roman", serif; letter-spacing: 0.01em; }
    .sym-cell { font-size: 14px; color: var(--ink-display); white-space: nowrap; }
    .m-bf { font-weight: 700; font-style: normal; }
    .m-rm { font-family: "Inter", sans-serif; font-style: normal; font-size: 0.88em; }
    .m-bb { font-weight: 600; font-style: normal; }
    .m-cal { font-style: italic; }
    .m-accent { display: inline-block; }
    .m-frac { display: inline-flex; flex-direction: column; align-items: center; vertical-align: middle; padding: 0 3px; font-size: 0.86em; line-height: 1.1; }
    .m-num { border-bottom: 1px solid var(--ink-primary); padding: 0 3px 1px; }
    .m-den { padding: 1px 3px 0; }
    .m-sqrt { display: inline-flex; align-items: baseline; }
    .m-rad { border-top: 1px solid var(--ink-primary); padding: 0 2px; }
    .form-card { border: 1px solid transparent; border-radius: var(--r-card); padding: 10px 12px; margin: 0 -12px; cursor: pointer; transition: border-color 0.15s ease, background 0.15s ease; }
    .form-card:hover { background: var(--bg-pane-head); }
    .form-card.spec-lit { border-color: var(--border-focus); background: rgba(76, 141, 246, 0.08); box-shadow: var(--shadow-focus); }
    .form-card + .form-card { margin-top: 10px; }
    .proof-cell { cursor: pointer; transition: background 0.15s ease; }
    .proof-cell.spec-lit { background: rgba(76, 141, 246, 0.08); box-shadow: inset 0 0 0 1.5px var(--border-focus); }
    .gutter-ln { height: 23px; }
    .code-line { min-height: 23px; padding: 0 24px 0 20px; border-left: 2px solid transparent; }
    .code-line.spec-lit { background: rgba(51, 214, 255, 0.09); border-left-color: var(--brand-cyan); }
    .repair-list { display: flex; flex-direction: column; gap: 12px; padding: 16px 20px 20px; }
    .repair-card { background: var(--bg-card); border: 1px solid var(--border-grid); border-top-color: var(--border-bevel); border-radius: var(--r-card); padding: 14px 16px; box-shadow: var(--shadow-card); }
    .repair-diff-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
    .repair-diff-col { background: var(--bg-well); border: 1px solid var(--border-grid); border-radius: var(--r-inner); padding: 10px 12px; font-size: 11.5px; overflow-x: auto; white-space: pre-wrap; overflow-wrap: anywhere; }
    .corpus-search-bar { padding: 8px 18px; border-bottom: 1px solid var(--border-grid); background: var(--bg-well); display: flex; align-items: center; gap: 8px; }
    .corpus-search-input { flex: 1; height: 26px; padding: 0 9px; border: 1px solid var(--border-grid); border-radius: 4px; background: var(--bg-frame); color: var(--ink-primary); font-family: inherit; font-size: 11.8px; outline: none; }
    .corpus-search-input:focus { border-color: var(--border-focus); }
    .pin-btn { height: 22px; padding: 0 8px; border: 1px solid var(--border-grid); border-radius: 4px; background: var(--bg-well); color: var(--ink-secondary); font-family: inherit; font-size: 11px; cursor: pointer; }
    .pin-btn.pinned { border-color: var(--border-focus); color: var(--brand-cyan); background: rgba(51, 214, 255, 0.09); }
    .pinned-corpus-well { display: none; background: rgba(76, 141, 246, 0.08); border: 1px solid rgba(124, 179, 255, 0.34); border-radius: var(--r-inner); padding: 10px 13px; font-size: 11.8px; color: var(--ink-primary); }
    .pinned-corpus-well.visible { display: block; }
    .empty-well { background: var(--bg-well); border: 1px dashed var(--border-strong); border-radius: var(--r-inner); padding: 18px; text-align: center; font-family: "Inter", sans-serif; font-size: 12.5px; color: var(--ink-secondary); }
    .empty-inline { font-family: "Inter", sans-serif; font-size: 12px; color: var(--ink-muted); }
    .constraint-list { margin-top: 14px; padding-left: 18px; color: var(--ink-secondary); font-size: 12.2px; display: flex; flex-direction: column; gap: 6px; }
    .katex { font-size: 1.03em !important; color: var(--ink-display); }
    .math-var { font-style: italic; }
    .math-op { opacity: 0.85; padding: 0 2px; }
    .bmatrix { display: inline-grid; grid-template-columns: repeat(2, auto); column-gap: 13px; row-gap: 1px; padding: 2px 7px; border-left: 1.5px solid var(--ink-primary); border-right: 1.5px solid var(--ink-primary); position: relative; font-size: 14px; line-height: 1.28; }
    .bmatrix::before, .bmatrix::after { content: ""; position: absolute; top: -1px; bottom: -1px; width: 4px; border-top: 1.5px solid var(--ink-primary); border-bottom: 1.5px solid var(--ink-primary); }
    .bmatrix::before { left: -1px; }
    .bmatrix::after { right: -1px; }
    .sig-well { background: var(--bg-well); border: 1px solid var(--border-grid); border-radius: var(--r-inner); padding: 8px 10px; font-size: 11.5px; line-height: 1.58; color: var(--ink-secondary); }
    .sig-row { display: flex; justify-content: space-between; gap: 10px; }
    .sig-param { color: var(--ink-primary); }
    .sig-type { color: var(--brand-sky); }
    .sig-ret { margin-top: 5px; padding-top: 5px; border-top: 1px dashed var(--border-grid); display: flex; justify-content: space-between; color: var(--brand-cyan); }
    .obl-desc { font-size: 12.5px; color: var(--ink-secondary); }
    .inspector-strip { flex-shrink: 0; padding: 10px 18px; background: var(--bg-well); border-top: 1px solid var(--border-grid); display: flex; align-items: center; justify-content: space-between; gap: 16px; font-size: 12px; }
    .insp-left { display: flex; align-items: center; gap: 11px; min-width: 0; }
    .insp-tag { padding: 2px 7px; border-radius: 4px; background: rgba(76, 141, 246, 0.14); color: var(--brand-sky); font-size: 10.5px; font-weight: 600; white-space: nowrap; }
    .insp-formula { color: var(--ink-primary); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .var-table { width: 100%; border-collapse: collapse; margin-top: 14px; font-size: 12.5px; }
    .var-table th, .var-table td { text-align: left; padding: 8px 6px; border-bottom: 1px solid var(--border-grid); }
    .var-table th { color: var(--ink-muted); font-weight: 600; font-size: 10px; text-transform: uppercase; letter-spacing: 0.08em; }
    .proof-swiss-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); }
    .proof-cell { position: relative; padding: 22px 24px 24px; min-width: 0; display: flex; flex-direction: column; }
    .proof-cell:nth-child(even) { border-left: 1px solid var(--border-grid); }
    .proof-cell:nth-child(n + 3) { border-top: 1px solid var(--border-grid); }
    .proof-cell .node-top { padding-right: 72px; margin-bottom: 8px; }
    .proof-cell .pass-label { position: absolute; top: 22px; right: 24px; }
    .proof-cell .node-title { font-size: 15px; margin-bottom: 6px; }
    .proof-rationale { font-size: 12.8px; line-height: 1.6; color: var(--ink-secondary); margin-bottom: 14px; }
    .proof-expr { margin-top: auto; background: var(--bg-well); border: 1px solid var(--border-grid); border-radius: var(--r-inner); padding: 11px 14px; font-size: 11.8px; line-height: 1.65; color: var(--ink-primary); overflow-x: auto; white-space: pre-wrap; overflow-wrap: anywhere; }
    .code-viewer { display: grid; grid-template-columns: auto 1fr; padding: 18px 0; font-size: 12.5px; line-height: 1.84; overflow-x: auto; background: var(--bg-frame); }
    .code-gutter { user-select: none; text-align: right; padding: 0 16px 0 22px; border-right: 1px solid var(--border-grid); color: var(--ink-muted); white-space: pre; }
    .code-lines { padding: 0; color: var(--ink-primary); white-space: pre; }
    .specs-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(315px, 1fr)); gap: 18px; }
    .specs-grid .node-card { padding: 18px 20px; }
    .specs-grid .node-top { padding-right: 78px; margin-bottom: 8px; }
    .specs-grid .pass-label { position: absolute; top: 18px; right: 20px; }
    .footer { flex-shrink: 0; height: 36px; padding: 0 28px; gap: 16px; border-top: 1px solid var(--border-grid); background: var(--bg-chrome); display: flex; align-items: center; justify-content: space-between; font-size: 11.5px; color: var(--ink-secondary); overflow: hidden; white-space: nowrap; }
    .status-left { display: flex; align-items: center; gap: 9px; min-width: 0; flex: 1; overflow: hidden; white-space: nowrap; }
    #footer-status { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .pulse-dot { flex-shrink: 0; width: 6px; height: 6px; border-radius: 50%; background: var(--brand-cyan); box-shadow: 0 0 8px var(--brand-cyan); }
    .gate-cue { flex-shrink: 0; color: var(--amber-ink); font-weight: 500; white-space: nowrap; }
    .copy-chip { flex-shrink: 0; height: 23px; max-width: 260px; padding: 0 10px; border-radius: 4px; border: 1px solid var(--border-grid); background: var(--bg-frame); color: var(--ink-secondary); font-size: 11px; cursor: pointer; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    @media (max-width: 980px) { .masthead { grid-template-columns: 1fr; gap: 14px; } .kv-ledger { border-top: 1px solid var(--border-grid); padding-top: 10px; width: fit-content; } }
    @media (max-width: 860px) { .swiss-split, .proof-swiss-grid { grid-template-columns: 1fr; } .swiss-pane + .swiss-pane { border-left: none; border-top: 1px solid var(--border-grid); } .proof-cell:nth-child(even) { border-left: none; } .proof-cell + .proof-cell { border-top: 1px solid var(--border-grid); } }
  </style>
</head>
<body>
  <svg width="0" height="0" style="position:absolute">
    <defs>
      <linearGradient id="curve-lit" x1="0%" y1="0%" x2="100%" y2="0%">
        <stop offset="0%" stop-color="#33d6ff"/><stop offset="52%" stop-color="#7cb3ff"/><stop offset="100%" stop-color="#6ee7a8"/>
      </linearGradient>
      <filter id="curve-glow" x="-20%" y="-20%" width="140%" height="140%">
        <feGaussianBlur stdDeviation="3.2" result="blur"/><feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge>
      </filter>
    </defs>
  </svg>

  <header class="topbar">
    <div class="brand">
      <img class="brand-mark" src="/assets/facet-prism.svg" alt="" />
      <span class="brand-wordmark">Facet</span>
      <span class="brand-divider"></span>
      <span class="brand-sub">Lens</span>
    </div>
    <nav class="timeline" aria-label="Pipeline Lifecycle">
      <button class="tl-node ${st.research}" data-stage="research" type="button"><span class="tl-pip"></span>Research</button>
      <span class="tl-rule ${st.research === "done" ? "done" : ""}"></span>
      <button class="tl-node ${st.spec}" data-stage="spec" type="button"><span class="tl-pip"></span>Specification</button>
      <span class="tl-rule ${st.spec === "done" ? "done" : ""}"></span>
      <button class="tl-node ${st.iface}" data-stage="interface" type="button"><span class="tl-pip"></span>Interface</button>
      <span class="tl-rule ${st.iface === "done" ? "done" : ""}"></span>
      <button class="tl-node ${st.proof}" data-stage="proof" type="button"><span class="tl-pip"></span>Verification</button>
      <span class="tl-rule ${st.proof === "done" ? "done" : ""}"></span>
      <button class="tl-node ${st.sealed}" data-stage="sealed" type="button"><span class="tl-pip"></span>Sealed</button>
    </nav>
    <div class="top-actions">
      <button class="seal-btn" id="seal-toggle" type="button" aria-label="Inspect cryptographic seal ledger">
        <span class="seal-dot"></span><span>Merkle Seal</span><span class="mono">${escapeHtml(sealShort)}</span>
      </button>
      <button class="theme-btn" id="theme-toggle" type="button" aria-label="Toggle color theme">Theme</button>
    </div>
    <div class="seal-popover" id="seal-popover" role="dialog" aria-label="Cryptographic Seal Ledger">
      <div class="pop-head"><span class="pop-title">Merkle Provenance &amp; Shadow Seal Ledger</span><span class="pass-label"><span class="status-pip"></span>${snapshot.sealVerified ? "Verified" : "Pending"}</span></div>
      <div class="pop-row"><span>Composite Seal</span><span class="mono">${escapeHtml(snapshot.sealSha ? snapshot.sealSha.slice(0, 16) + "..." : "unsealed")}</span></div>
      ${hashRows}
    </div>
  </header>

  <main class="viewport">
    <section class="masthead">
      <div>
        <div class="eyebrow">
          <span>${escapeHtml(snapshot.domainCategory)}</span>
          <span class="eyebrow-sep"></span>
          <span class="mono" style="text-transform:none;letter-spacing:0">${escapeHtml(snapshot.isArchivedView ? `specs/${snapshot.activeSlug}` : snapshot.activeSlug)}</span>
        </div>
        <h1 class="hero-title">${escapeHtml(snapshot.conceptTitle)}</h1>
      </div>
      <div class="kv-ledger" aria-label="Contract Telemetry">
        <div class="kv-cell"><div class="kv-label">Grounding</div><div class="kv-value mono" id="kv-grounding" title="${escapeHtml(snapshot.groundingLabel)}">${escapeHtml(snapshot.groundingLabel)}</div></div>
        <div class="kv-cell"><div class="kv-label">Tolerance</div><div class="kv-value mono">${escapeHtml(snapshot.precisionLabel)}</div></div>
        <div class="kv-cell"><div class="kv-label">Cleanroom</div><div class="kv-value emerald mono">${escapeHtml(snapshot.passSummary)}</div></div>
        <div class="kv-cell"><div class="kv-label">Self-Repair</div><div class="kv-value mono">${escapeHtml(snapshot.repairSummary)}</div></div>
      </div>
    </section>

    <div class="subnav">
      <div class="tab-strip" role="tablist" aria-label="Lens Views">
        <button class="tab-btn" role="tab" aria-selected="${activeTab === "traceability"}" data-tab="traceability" type="button">Traceability Graph</button>
        <button class="tab-btn" role="tab" aria-selected="${activeTab === "source-math"}" data-tab="source-math" type="button">Sources &amp; Formulation</button>
        <button class="tab-btn" role="tab" aria-selected="${activeTab === "proofs"}" data-tab="proofs" type="button">4-Predicate Proofs</button>
        <button class="tab-btn" role="tab" aria-selected="${activeTab === "code"}" data-tab="code" type="button">Implementation</button>
        <button class="tab-btn" role="tab" aria-selected="${activeTab === "specs"}" data-tab="specs" type="button">Sealed Archive (${snapshot.sealedSpecs.length})</button>
      </div>
    </div>

    <section class="view-panel${activeTab === "traceability" ? " active" : ""}" id="panel-traceability">
      <div class="swiss-frame">
        <div class="pane-header">
          <span class="pane-title">Contract Traceability DAG</span>
          <span class="inline-status"><span class="status-pip"></span><span class="mono" id="trace-header-meta">${snapshot.edges.length} active filaments &middot; click any node to isolate</span></span>
        </div>
        <div class="frame-scroll dot-surface">
          <div class="trace-stage" id="trace-stage">
            <svg class="trace-svg" id="trace-svg"></svg>
            <div class="trace-col"><div class="col-header"><span>Formal Specifications</span><span class="col-file">manifest.json</span></div><div class="trace-col-cards">${specCardsHtml}</div></div>
            <div class="trace-col"><div class="col-header"><span>Typed Python API</span><span class="col-file">${escapeHtml(snapshot.targetModule)}</span></div><div class="trace-col-cards">${fnCardsHtml}</div></div>
            <div class="trace-col"><div class="col-header"><span>Cleanroom Proofs</span><span class="col-file">4-Predicate Basis</span></div><div class="trace-col-cards">${oblCardsHtml}</div></div>
          </div>
        </div>
        <div class="inspector-strip">
          <div class="insp-left"><span class="insp-tag mono" id="insp-id">ALL CONTRACTS</span><span class="insp-formula" id="insp-text">Click or hover any specification, entrypoint, or cleanroom obligation to inspect contract provenance.</span></div>
          <span class="mono" style="color:var(--ink-muted);white-space:nowrap">sha256 &middot; ${escapeHtml((snapshot.mapSha || snapshot.sealSha || "pending").slice(0, 8))}</span>
        </div>
      </div>
    </section>

    <section class="view-panel${activeTab === "source-math" ? " active" : ""}" id="panel-source-math">
      <div class="swiss-frame">
        <div class="modality-bar">
          <div class="source-tabs" role="group" aria-label="Detected Grounding Sources">
            <button class="src-chip${defaultSrcMode === "image" ? " active" : ""}" data-srcmode="image" type="button" ${snapshot.imageRel ? "" : "disabled title=\"No optical image in this run\""}><span class="kind-tag">IMG</span><span>${escapeHtml(snapshot.imageRel ? snapshot.imageRel.split("/").pop() : "Optical Plate")}</span></button>
            <button class="src-chip${defaultSrcMode === "multipdf" ? " active" : ""}" data-srcmode="multipdf" type="button" ${snapshot.corpusDocs.length > 0 ? "" : "disabled title=\"No files in research/\""}><span class="kind-tag">DIR</span><span>research/ (${snapshot.corpusDocs.length} files)</span></button>
            <button class="src-chip${defaultSrcMode === "literature" ? " active" : ""}" data-srcmode="literature" type="button"><span class="kind-tag">REF</span><span>Cited References (${snapshot.references.length})</span></button>
          </div>
          <span class="mono" style="font-size:11px;color:var(--ink-muted)">sha256 ${escapeHtml((snapshot.ocrSha || snapshot.resSha || "pending").slice(0, 8))}</span>
        </div>
        <div class="frame-scroll">
          <div class="swiss-split">
            <div class="swiss-pane">
              <div class="pane-header">
                <span class="pane-title" id="left-pane-title">${escapeHtml(initialLeftTitle)}</span>
                <span class="inline-status"><span class="status-pip${defaultSrcMode === "image" && snapshot.ambiguityCount > 0 ? " amber" : ""}" id="left-pane-pip"></span><span class="mono" id="left-pane-meta">${escapeHtml(initialLeftMeta)}</span></span>
              </div>
              <div class="pane-body dot-stage" id="src-mode-image" style="display:${defaultSrcMode === "image" ? "block" : "none"}">
                <div class="plate-well">
                  ${snapshot.imageRel ? `<img src="/api/asset?rel=${encodeURIComponent(snapshot.imageRel)}" alt="${escapeHtml(snapshot.imageRel)}" />` : `<span class="mono" style="color:var(--ink-secondary)">No optical image referenced in this run.</span>`}
                  <span class="mono" style="font-size:11px;color:var(--ink-muted)">${escapeHtml(snapshot.imageRel || "Switch to Cited References or research/")}</span>
                </div>
              </div>
              <div class="corpus-pane" id="src-mode-multipdf" style="display:${defaultSrcMode === "multipdf" ? "flex" : "none"}">
                <div class="corpus-search-bar">
                  <input class="corpus-search-input" id="corpus-search-input" type="search" placeholder="Search corpus documents &amp; excerpts..." aria-label="Search corpus documents and excerpts" />
                </div>
                <div class="corpus-index" role="listbox" aria-label="Corpus Documents">${corpusRowsHtml}</div>
                <div class="corpus-Reader-bar">
                  <span class="mono" id="pdf-section-label">Document Excerpt</span>
                  <div style="display:inline-flex;align-items:center;gap:8px">
                    <button class="pin-btn mono" id="corpus-pin-btn" type="button">Pin Excerpt</button>
                    <div class="stepper-group">
                      <button class="step-arrow mono" id="pdf-prev" type="button" aria-label="Previous page">&lsaquo;</button>
                      <span class="step-counter mono" id="pdf-page-counter">01 / 01</span>
                      <button class="step-arrow mono" id="pdf-next" type="button" aria-label="Next page">&rsaquo;</button>
                    </div>
                  </div>
                </div>
                <div class="corpus-stage">
                  <div class="pinned-corpus-well" id="pinned-corpus-well"></div>
                  <div class="corpus-quote" id="pdf-excerpt"></div>
                </div>
              </div>
              <div class="pane-body dot-stage" id="src-mode-literature" style="display:${defaultSrcMode === "literature" ? "block" : "none"}">${refCardsHtml}</div>
            </div>
            <div class="swiss-pane">
              <div class="pane-header"><span class="pane-title">Governing Formulation</span><span class="mono" style="font-size:11px;color:var(--ink-secondary)">rtol ${escapeHtml(snapshot.rtolLabel)} &middot; atol ${escapeHtml(snapshot.atolLabel)}</span></div>
              <div class="pane-body">
                ${formulationsStackHtml}
                ${varRowsHtml ? `<table class="var-table"><thead><tr><th>Symbol</th><th>Domain</th><th>Interpretation</th></tr></thead><tbody>${varRowsHtml}</tbody></table>` : ""}
                ${constraintsListHtml ? `<ul class="constraint-list">${constraintsListHtml}</ul>` : ""}
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>

    <section class="view-panel${activeTab === "proofs" ? " active" : ""}" id="panel-proofs">
      <div class="swiss-frame">
        <div class="pane-header">
          <span class="pane-title">Hughes 4-Predicate Cleanroom Matrix</span>
          <span class="inline-status"><span class="status-pip"></span><span class="mono">${escapeHtml(snapshot.passSummary)} &middot; ${escapeHtml(snapshot.verifyFileName)}</span></span>
        </div>
        <div class="frame-scroll"><div class="proof-swiss-grid">${proofCellsHtml}</div>${repairInspectorHtml}</div>
      </div>
    </section>

    <section class="view-panel${activeTab === "code" ? " active" : ""}" id="panel-code">
      <div class="swiss-frame">
        <div class="modality-bar" style="height:40px;padding:0 18px">
          <div class="source-tabs" role="group" aria-label="Verified Source Artifacts">
            <button class="src-chip code-file-btn active" data-codefile="impl" type="button"><span class="kind-tag">PY</span><span class="mono">${escapeHtml(snapshot.targetModule)}</span></button>
            <button class="src-chip code-file-btn" data-codefile="suite" type="button"><span class="kind-tag">TEST</span><span class="mono">${escapeHtml(snapshot.verifyFileName)}</span></button>
          </div>
          <span class="inline-status"><span class="status-pip"></span><span class="mono">Cleanroom isolated</span></span>
        </div>
        <div class="frame-scroll">
          <div class="code-viewer mono" id="code-pane-impl"><div class="code-gutter">${implFormatted.gutter}</div><div class="code-lines">${implFormatted.code}</div></div>
          <div class="code-viewer mono" id="code-pane-suite" style="display:none"><div class="code-gutter">${suiteFormatted.gutter}</div><div class="code-lines">${suiteFormatted.code}</div></div>
        </div>
      </div>
    </section>

    <section class="view-panel${activeTab === "specs" ? " active" : ""}" id="panel-specs">
      <div class="swiss-frame">
        <div class="pane-header">
          <span class="pane-title">Workspace Specification Dossiers</span>
          <span class="inline-status"><span class="status-pip"></span><span class="mono">${snapshot.sealedSpecs.length} sealed packages</span></span>
        </div>
        <div class="frame-scroll dot-surface"><div class="specs-grid">${archiveShelfHtml}</div></div>
      </div>
    </section>
  </main>

  <footer class="footer" aria-live="polite">
    <div class="status-left">
      <span class="pulse-dot"></span>
      <span id="footer-status" title="${footerStatus}">${footerStatus}</span>
      <span>&middot;</span>
      <span class="gate-cue">${escapeHtml(footerCue)}</span>
    </div>
    <button class="copy-chip mono" id="copy-pytest" type="button" title="${escapeHtml(snapshot.pytestCmd)}">${escapeHtml(copyPytestLabel)}</button>
  </footer>

  <script id="boot-data" type="application/json">${clientBootJson}</script>
  <script id="boot-script">${CLIENT_RUNTIME_JS}</script>
</body>
</html>`;
}
