/**
 * @fileoverview Durable UI preference storage and read-only workspace snapshot builder for Facet Lens.
 *
 * Stores user view preferences (active tab, dark/light theme, locked graph node, pinned
 * corpus excerpts, and selected `specs/<slug>/` package) outside the git repository under
 * `$COPILOT_HOME/extension-data/facet-pipeline-lens/`, while inspecting workspace artifacts
 * and recomputing SHA-256 digests on every read.
 */
import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

export const CANVAS_ID = "facet-pipeline-lens";
const STATE_FORMAT = "facet-pipeline-lens-state";
const STATE_VERSION = 1;
const VALID_TABS = new Set(["traceability", "source-math", "proofs", "code", "specs"]);
const VALID_THEMES = new Set(["dark", "light"]);
const VALID_SRC_MODES = new Set(["image", "multipdf", "literature"]);

/**
 * Create an Error tagged with a machine-readable `code` property for `CanvasError` wrapping.
 */
export function createStorageError(code, message) {
    const err = new Error(message);
    err.code = code;
    return err;
}

// Resolve `$COPILOT_HOME` when non-empty, defaulting to `~/.copilot`.
export function resolveCopilotHome() {
    const envHome = process.env.COPILOT_HOME;
    if (typeof envHome === "string" && envHome.trim().length > 0) {
        return envHome;
    }
    return path.join(os.homedir(), ".copilot");
}

export function getExtensionDataDir() {
    return path.join(resolveCopilotHome(), "extension-data", CANVAS_ID);
}

// Derive a per-workspace state filename (`state_<sha256_prefix>.json`) so multiple
// repositories never overwrite one another's Facet Lens tab or theme preferences.
function getWorkspaceStatePath(workspaceRoot) {
    const canonical = path.resolve(String(workspaceRoot || process.cwd()));
    const digest = crypto.createHash("sha256").update(canonical).digest("hex").slice(0, 24);
    return path.join(getExtensionDataDir(), `state_${digest}.json`);
}

function defaultPreferences(workspaceRoot) {
    return {
        format: STATE_FORMAT,
        version: STATE_VERSION,
        workspaceRoot: path.resolve(String(workspaceRoot || process.cwd())),
        activeTab: "traceability",
        activeSrcMode: "",
        lockedNode: "",
        lockedNodeSlug: "",
        selectedSpecSlug: "",
        pinnedExcerpts: [],
        theme: "dark",
        updatedAt: new Date().toISOString(),
    };
}

// Validate format marker, schema version, and allowed enum values before trusting saved JSON.
function validatePreferencePayload(data) {
    if (!data || typeof data !== "object" || Array.isArray(data)) {
        throw createStorageError("corrupt_extension_state", "Extension state file is not a JSON object.");
    }
    if (data.format !== STATE_FORMAT || data.version !== STATE_VERSION) {
        throw createStorageError(
            "corrupt_extension_state",
            `Unrecognized extension state format (${String(data.format)}) or version (${String(data.version)}).`
        );
    }
    if (typeof data.workspaceRoot !== "string" || typeof data.activeTab !== "string" || !VALID_TABS.has(data.activeTab)) {
        throw createStorageError("corrupt_extension_state", "Extension state fields have an invalid shape.");
    }
    if (typeof data.theme !== "string" || !VALID_THEMES.has(data.theme)) {
        throw createStorageError("corrupt_extension_state", "Extension state theme field is invalid.");
    }
}

// Load saved UI preferences from `$COPILOT_HOME/extension-data/`, or return clean defaults.
export function loadUiPreferences(workspaceRoot) {
    const statePath = getWorkspaceStatePath(workspaceRoot);
    if (!fs.existsSync(statePath)) {
        return defaultPreferences(workspaceRoot);
    }
    let rawText = "";
    try {
        rawText = fs.readFileSync(statePath, "utf8");
    } catch (err) {
        throw createStorageError("storage_read_failed", `Failed to read Facet Lens state: ${err.message}`);
    }
    let parsed;
    try {
        parsed = JSON.parse(rawText);
    } catch {
        throw createStorageError("corrupt_extension_state", `Corrupt JSON in Facet Lens state file: ${statePath}`);
    }
    validatePreferencePayload(parsed);
    return {
        ...defaultPreferences(workspaceRoot),
        ...parsed,
    };
}

// Apply a validated partial preference update and persist atomically via temp file + rename.
export function saveUiPreferences(workspaceRoot, patch = {}) {
    const current = loadUiPreferences(workspaceRoot);
    const next = {
        ...current,
        workspaceRoot: path.resolve(String(workspaceRoot || current.workspaceRoot)),
        updatedAt: new Date().toISOString(),
    };
    if (patch.activeTab !== undefined) {
        if (!VALID_TABS.has(patch.activeTab)) {
            throw createStorageError("invalid_view", `Unsupported view '${String(patch.activeTab)}'.`);
        }
        next.activeTab = patch.activeTab;
    }
    if (patch.theme !== undefined) {
        if (!VALID_THEMES.has(patch.theme)) {
            throw createStorageError("invalid_theme", `Unsupported theme '${String(patch.theme)}'.`);
        }
        next.theme = patch.theme;
    }
    if (patch.activeSrcMode !== undefined) {
        next.activeSrcMode = VALID_SRC_MODES.has(patch.activeSrcMode) ? patch.activeSrcMode : "";
    }
    if (patch.lockedNode !== undefined) {
        next.lockedNode = typeof patch.lockedNode === "string" ? patch.lockedNode.slice(0, 64) : "";
    }
    if (patch.lockedNodeSlug !== undefined) {
        next.lockedNodeSlug = typeof patch.lockedNodeSlug === "string" ? patch.lockedNodeSlug.slice(0, 128) : "";
    }
    if (patch.selectedSpecSlug !== undefined) {
        next.selectedSpecSlug = typeof patch.selectedSpecSlug === "string" ? patch.selectedSpecSlug.slice(0, 128) : "";
    }
    if (Array.isArray(patch.pinnedExcerpts)) {
        next.pinnedExcerpts = patch.pinnedExcerpts
            .filter((p) => p && typeof p === "object" && typeof p.ref === "string" && typeof p.text === "string")
            .slice(0, 12)
            .map((p) => ({ ref: p.ref.slice(0, 120), text: p.text.slice(0, 300) }));
    }

    const dir = getExtensionDataDir();
    const targetPath = getWorkspaceStatePath(workspaceRoot);
    const tmpPath = path.join(dir, `.tmp_${process.pid}_${process.hrtime.bigint()}.json`);
    try {
        fs.mkdirSync(dir, { recursive: true });
        fs.writeFileSync(tmpPath, JSON.stringify(next, null, 2) + "\n", "utf8");
        fs.renameSync(tmpPath, targetPath);
    } catch (err) {
        try {
            if (fs.existsSync(tmpPath)) fs.unlinkSync(tmpPath);
        } catch {
            // ignore cleanup error
        }
        throw createStorageError("storage_write_failed", `Failed to persist Facet Lens preferences: ${err.message}`);
    }
    return next;
}

// Recompute a file's live SHA-256 hex digest on disk (`""` when missing).
function sha256File(filePath) {
    try {
        if (!fs.existsSync(filePath) || !fs.statSync(filePath).isFile()) return "";
        return crypto.createHash("sha256").update(fs.readFileSync(filePath)).digest("hex");
    } catch {
        return "";
    }
}

function safeReadJson(filePath) {
    try {
        if (!fs.existsSync(filePath)) return null;
        return JSON.parse(fs.readFileSync(filePath, "utf8"));
    } catch {
        return null;
    }
}

function safeReadText(filePath) {
    try {
        if (!fs.existsSync(filePath)) return "";
        return fs.readFileSync(filePath, "utf8");
    } catch {
        return "";
    }
}

// Enumerate `<workspace>/specs/*/` packages and verify every file hash against `PROVENANCE.json`.
function listSealedSpecs(wsRoot) {
    const specsDir = path.join(wsRoot, "specs");
    if (!fs.existsSync(specsDir) || !fs.statSync(specsDir).isDirectory()) return [];
    const entries = [];
    for (const dirent of fs.readdirSync(specsDir, { withFileTypes: true })) {
        if (!dirent.isDirectory()) continue;
        const slug = dirent.name;
        const dirPath = path.join(specsDir, slug);
        const prov = safeReadJson(path.join(dirPath, "PROVENANCE.json"));
        if (!prov) continue;
        const man = safeReadJson(path.join(dirPath, "manifest.json")) || {};
        const map = safeReadJson(path.join(dirPath, "mapping.json")) || {};
        const obls = Array.isArray(map.verification_obligations) ? map.verification_obligations : [];
        const sr = man.source_reference && typeof man.source_reference === "object" ? man.source_reference : {};
        const hashes = prov.artifact_hashes && typeof prov.artifact_hashes === "object" ? prov.artifact_hashes : {};
        let allMatch = true;
        for (const [fn, expectedSha] of Object.entries(hashes)) {
            const actual = sha256File(path.join(dirPath, fn));
            if (!actual || actual !== expectedSha) {
                allMatch = false;
                break;
            }
        }
        entries.push({
            slug,
            dirPath,
            title: String(man.concept_name || map.concept_name || slug),
            domain: String(man.domain_category || "Engineering Specification"),
            sourceLabel: String(sr.title || sr.doi_or_url || ""),
            year: sr.year ? String(sr.year) : "",
            obligationCount: obls.length,
            sealSha: String(prov.seal_sha256 || ""),
            sealVerified: prov.status === "VERIFIED_COMPLETE" && allMatch,
            artifactHashes: hashes,
        });
    }
    return entries.sort((a, b) => a.slug.localeCompare(b.slug));
}

function stripMarkdownInline(raw) {
    return String(raw || "")
        .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
        .replace(/\*\*([^*]+)\*\*/g, "$1")
        .replace(/\*([^*\n]+)\*/g, "$1")
        .replace(/`([^`]+)`/g, "$1")
        .replace(/\s+/g, " ")
        .trim();
}

function truncateClean(text, maxLen = 140) {
    const clean = stripMarkdownInline(text);
    if (clean.length <= maxLen) return clean;
    const cut = clean.slice(0, maxLen);
    const sp = cut.lastIndexOf(" ");
    return (sp > maxLen * 0.6 ? cut.slice(0, sp) : cut).replace(/[.,;:\-\s]+$/, "") + "...";
}

// Extract candidate headings, citations, and `$$ ... $$` display equations from `RESEARCH.md`.
function extractResearchCandidates(resText) {
    const text = String(resText || "");
    if (!text.trim()) return [];
    const candidates = [];
    const secRegex = /^###\s+(?:\d+\.\s*)?([^\r\n]+)\r?\n([\s\S]*?)(?=^###\s+|^##\s+|\Z)/gm;
    let match;
    while ((match = secRegex.exec(text)) !== null) {
        const rawHeading = stripMarkdownInline(match[1]);
        if (/^stage\s+briefing$/i.test(rawHeading)) continue;
        const body = match[2] || "";
        const citeMatch = body.match(/[-*]\s*\*\*(?:Canonical\s+)?(?:Literature\s+)?Reference:\*\*\s*([^\r\n]+)/i);
        const mechMatch = body.match(/[-*]\s*\*\*(?:Technical\s+Justification|Trade-offs|Preconditions[^*]*):\*\*\s*([^\r\n]+)/i);
        const eqMatch = body.match(/\$\$([\s\S]*?)\$\$/);
        const citation = citeMatch ? stripMarkdownInline(citeMatch[1]) : "";
        const latex = eqMatch ? eqMatch[1].replace(/\\displaystyle\b/g, "").trim() : "";
        const summary = mechMatch ? stripMarkdownInline(mechMatch[1]) : citation;
        if (rawHeading && (citation || latex)) {
            candidates.push({
                title: rawHeading,
                citation: citation || "Documented in RESEARCH.md",
                latex,
                summary: summary || rawHeading,
            });
        }
    }
    if (candidates.length === 0) {
        const displayEqs = [...text.matchAll(/\$\$([\s\S]*?)\$\$/g)].slice(0, 4);
        displayEqs.forEach((m, idx) => {
            const latex = m[1].replace(/\\displaystyle\b/g, "").trim();
            if (latex) {
                candidates.push({
                    title: `Candidate Formulation ${idx + 1}`,
                    citation: "Extracted from RESEARCH.md",
                    latex,
                    summary: "Stage 0 literature formulation awaiting Stage 1 manifest selection.",
                });
            }
        });
    }
    return candidates.slice(0, 6);
}

// Collect optical source plates, `research/` corpus excerpts, and literature citations for Tab 2.
function parseSourceAssets(wsRoot, baseDir, manData, ocrText, resText) {
    const sr = manData.source_reference && typeof manData.source_reference === "object" ? manData.source_reference : {};
    let imageRel = "";
    const mImg = ocrText.match(/((?:examples|research)\/[A-Za-z0-9_\-./]+\.(?:png|jpg|jpeg|webp|gif|bmp))/i);
    if (mImg && fs.existsSync(path.join(wsRoot, mImg[1]))) {
        imageRel = mImg[1];
    } else if (typeof sr.doi_or_url === "string" && /\.(?:png|jpg|jpeg|webp|gif|bmp)$/i.test(sr.doi_or_url)) {
        if (fs.existsSync(path.join(wsRoot, sr.doi_or_url))) {
            imageRel = sr.doi_or_url;
        }
    }

    const researchDir = path.join(wsRoot, "research");
    const corpusDocs = [];
    if (fs.existsSync(researchDir) && fs.statSync(researchDir).isDirectory()) {
        for (const d of fs.readdirSync(researchDir, { withFileTypes: true })) {
            if (!d.isFile() || d.name.startsWith(".")) continue;
            const ext = path.extname(d.name).toLowerCase().replace(".", "").toUpperCase() || "DOC";
            const hasUncertain = ocrText.includes("<uncertain") || ocrText.includes("<illegible");
            const paragraphs = (ocrText || safeReadText(path.join(researchDir, d.name)))
                .split(/\n{2,}/)
                .map((s) => s.trim())
                .filter((s) => s.length > 20)
                .slice(0, 6);
            corpusDocs.push({
                name: d.name,
                kind: ext.slice(0, 4),
                role: "research/",
                hasUncertain,
                pages: Math.max(1, paragraphs.length),
                excerpts: paragraphs.length > 0 ? paragraphs : [`Indexed research document: research/${d.name}`],
            });
        }
    }

    const references = [];
    if (sr.title || sr.doi_or_url) {
        const authors = Array.isArray(sr.authors) ? sr.authors.join(", ") : String(sr.authors || "");
        references.push({
            kicker: "PRIMARY CANONICAL SOURCE",
            meta: sr.year ? String(sr.year) : "manifest.json",
            title: stripMarkdownInline([authors, sr.title].filter(Boolean).join(" · ") || String(sr.doi_or_url)),
            detail: stripMarkdownInline(String(sr.doi_or_url || "Verified primary specification reference.")),
        });
    }

    const candidates = extractResearchCandidates(resText);
    for (const cand of candidates) {
        references.push({
            kicker: references.length === 0 ? "PRIMARY CANDIDATE SOURCE" : `CANDIDATE 0${references.length + 1}`,
            meta: "RESEARCH.md",
            title: truncateClean(cand.title, 88),
            detail: truncateClean(cand.citation, 180),
        });
    }

    if (references.length === 0 && resText.trim()) {
        const cleanBullets = resText
            .split(/\r?\n/)
            .map((l) => l.trim())
            .filter((l) => (l.startsWith("- ") || l.startsWith("* ")) && l.length > 18)
            .map((l) => stripMarkdownInline(l.replace(/^[-*]\s+/, "")))
            .filter((l) => l && !l.endsWith(":"))
            .slice(0, 3);
        for (const b of cleanBullets) {
            const colonIdx = b.indexOf(":");
            const hasPrefix = colonIdx > 2 && colonIdx < 42;
            references.push({
                kicker: references.length === 0 ? "PRIMARY CANONICAL SOURCE" : "LITERATURE NOTE",
                meta: "RESEARCH.md",
                title: hasPrefix ? b.slice(0, colonIdx).trim() : truncateClean(b, 72),
                detail: hasPrefix ? truncateClean(b.slice(colonIdx + 1).trim(), 180) : truncateClean(b, 180),
            });
        }
    }
    void baseDir;
    return { imageRel, corpusDocs, references, candidates };
}

export function buildWorkspaceSnapshot(workspaceRoot, prefsInput = null) {
    const wsRoot = path.resolve(String(workspaceRoot || process.cwd()));
    let prefs;
    try {
        prefs = prefsInput || loadUiPreferences(wsRoot);
    } catch {
        prefs = defaultPreferences(wsRoot);
    }
    const fsm = safeReadJson(path.join(wsRoot, ".pipeline_state.json")) || { current_stage: "PRE_RUN_HALT" };
    const currentStage = String(fsm.current_stage || "PRE_RUN_HALT");
    const sealedSpecs = listSealedSpecs(wsRoot);

    // Check whether an active run currently has working files in the workspace root.
    // When the workspace root is clean after Stage 5 archival, automatically display
    // either the user-selected `specs/<slug>/` archive or the most recently sealed run.
    const hasRootDrafts = [
        "SOURCE_OCR.md",
        "RESEARCH.md",
        "manifest.json",
        "mapping.json",
        "LOCAL_CRITIC.json",
        "AUDIT_FEEDBACK.json",
        "VERIFICATION.md",
    ].some((f) => fs.existsSync(path.join(wsRoot, f)));

    let activeBaseDir = wsRoot;
    let activeSlug = String(fsm.concept_slug || "");
    let isArchivedView = false;
    if (!hasRootDrafts && sealedSpecs.length > 0) {
        const chosen =
            sealedSpecs.find((s) => s.slug === prefs.selectedSpecSlug) ||
            sealedSpecs.find((s) => s.slug === (fsm.last_archived_cycle && fsm.last_archived_cycle.concept_slug)) ||
            sealedSpecs[0];
        activeBaseDir = chosen.dirPath;
        activeSlug = chosen.slug;
        isArchivedView = true;
    }

    // Load all structured JSON and Markdown artifacts from `activeBaseDir` (`null`/`""` when not yet created).
    const manData = safeReadJson(path.join(activeBaseDir, "manifest.json")) || {};
    const mapData = safeReadJson(path.join(activeBaseDir, "mapping.json")) || {};
    const provData = safeReadJson(path.join(activeBaseDir, "PROVENANCE.json")) || {};
    const ocrText = safeReadText(path.join(activeBaseDir, "SOURCE_OCR.md"));
    const resText = safeReadText(path.join(activeBaseDir, "RESEARCH.md"));
    const verText = safeReadText(path.join(activeBaseDir, "VERIFICATION.md"));

    // Normalize `SPEC-k` entries across supported schema field aliases (`formal_specifications` / `specifications`).
    const rawSpecs = Array.isArray(manData.formal_specifications)
        ? manData.formal_specifications
        : Array.isArray(manData.specifications)
          ? manData.specifications
          : [];
    const specs = rawSpecs.slice(0, 8).map((sp, i) => ({
        id: String(sp.spec_id || sp.id || `SPEC-${i + 1}`),
        title: String(sp.name || sp.title || sp.description || `Specification ${i + 1}`),
        latex: String(sp.latex || sp.expression || sp.mathematical_form || sp.equation || sp.code_or_pseudocode || ""),
        status: String(sp.epistemic_status || "canonical"),
        detail: String(sp.grounding_evidence || sp.description || ""),
    }));

    // Resolve the production Python module filename from `mapping.json` or `manifest.json`,
    // falling back to scanning `activeBaseDir` for non-test `.py` modules if needed.
    const pub = mapData.public_interface && typeof mapData.public_interface === "object" ? mapData.public_interface : {};
    const manTargetIf = manData.target_interface && typeof manData.target_interface === "object" ? manData.target_interface : {};
    const rawTargetMod = String(
        pub.target_module || pub.module_name || manTargetIf.target_module || (activeSlug ? `${activeSlug}.py` : "module.py")
    ).trim();
    let targetModule = rawTargetMod && !rawTargetMod.endsWith(".py") ? `${rawTargetMod}.py` : rawTargetMod || "module.py";
    if (!fs.existsSync(path.join(activeBaseDir, targetModule)) && fs.existsSync(activeBaseDir)) {
        const pyFallbacks = fs
            .readdirSync(activeBaseDir)
            .filter(
                (fn) =>
                    fn.endsWith(".py") &&
                    !fn.startsWith("verify_") &&
                    !fn.startsWith("test_") &&
                    !fn.startsWith("smoke_") &&
                    !fn.startsWith("__")
            )
            .sort();
        if (pyFallbacks.length > 0) {
            targetModule = pyFallbacks[0];
        }
    }
    // Extract typed public function signatures (`FN-1..FN-6`) for the center column of the Traceability Graph.
    const rawEps = Array.isArray(pub.entrypoints) && pub.entrypoints.length > 0
        ? pub.entrypoints
        : Array.isArray(manTargetIf.entrypoints)
          ? manTargetIf.entrypoints
          : [];
    const entrypoints = rawEps.slice(0, 6).map((ep, i) => ({
        nodeId: `FN-${i + 1}`,
        name: String(ep.name || ep.function_name || `entrypoint_${i + 1}`),
        params: (Array.isArray(ep.parameters) ? ep.parameters : []).map((p) => ({
            name: String(p.name || "arg"),
            type: String(p.type || p.computational_type || "object"),
        })),
        returnType: String(ep.return_type || "object"),
        detail: String(ep.description || `Public entrypoint in ${targetModule}`),
    }));

    // Extract 4-predicate verification obligations (`OBL-1..OBL-8`) and infer live `PASS`/`FAIL`/`PENDING`
    // status from `VERIFICATION.md` and archive completion state.
    const basisTitleFallback = {
        anchor: "Pinned Anchor Vector",
        reference_model: "Reference Model Equivalence",
        relational_law: "Metamorphic Relational Law",
        postcondition: "Postcondition & Invariant Bound",
    };
    const rawObls = Array.isArray(mapData.verification_obligations) ? mapData.verification_obligations : [];
    const obligations = rawObls.slice(0, 8).map((ob, i) => {
        const oid = String(ob.obligation_id || ob.id || `OBL-${i + 1}`);
        const basis = String(ob.predicate_type || ob.category || "obligation");
        const rawCov = ob.covers || ob.spec_refs || ob.spec_id || ob.source_spec_id || "";
        const covers = Array.isArray(rawCov) ? rawCov.map(String) : rawCov ? [String(rawCov)] : [];
        const failed = new RegExp(`\\b${oid}\\b[^\\n]*\\bFAIL\\b`, "i").test(verText);
        const rawDesc = String(ob.description || "").trim();
        const descTitle =
            rawDesc && rawDesc.toUpperCase() !== oid.toUpperCase()
                ? rawDesc
                : basisTitleFallback[basis] || `Contract Obligation (${covers.join(", ") || basis})`;
        return {
            id: oid,
            basis,
            covers,
            description: descTitle,
            predicate: String(ob.formal_predicate || ob.description || ""),
            verdict: failed ? "FAIL" : verText || isArchivedView ? "PASS" : "PENDING",
        };
    });

    // Build bidirectional lookup maps (`SPEC-k <-> OBL-j`) and directed `SPEC -> FN -> OBL` edges
    // so clicking any card in the UI highlights its connected equations, functions, code lines, and proofs.
    const specToObls = {};
    const oblToSpecs = {};
    for (const ob of obligations) {
        oblToSpecs[ob.id] = ob.covers.slice();
        for (const sc of ob.covers) {
            if (!specToObls[sc]) specToObls[sc] = [];
            if (!specToObls[sc].includes(ob.id)) specToObls[sc].push(ob.id);
        }
    }

    const edges = [];
    for (const sp of specs) {
        for (const fn of entrypoints) {
            edges.push([sp.id, fn.nodeId]);
        }
    }
    for (const ob of obligations) {
        const targetFns = entrypoints.filter((fn) => ob.predicate.includes(fn.name));
        const chosenFns = targetFns.length > 0 ? targetFns : entrypoints.slice(0, 1);
        for (const fn of chosenFns) {
            edges.push([fn.nodeId, ob.id]);
        }
    }

    // Scan the implementation module line-by-line for `# Implements SPEC-k` annotations,
    // propagating active `SPEC-k` tags through indented function bodies for cross-tab code highlighting.
    const implCode = safeReadText(path.join(activeBaseDir, targetModule));
    const specLineMap = {};
    const lineSpecTags = [];
    if (implCode) {
        const linesArr = implCode.replace(/\n$/, "").split("\n");
        let activeDefSpecs = [];
        linesArr.forEach((ln, idx) => {
            if (/^(?:def|class)\s+/.test(ln)) {
                activeDefSpecs = [];
            }
            const directHits = [...ln.matchAll(/\b(SPEC-\d+)\b/gi)].map((m) => m[1].toUpperCase());
            if (directHits.length > 0) {
                activeDefSpecs = [...new Set([...activeDefSpecs, ...directHits])];
            }
            const tags = directHits.length > 0 ? directHits : (/^\s+/.test(ln) ? activeDefSpecs : []);
            lineSpecTags.push(tags);
            for (const sid of tags) {
                if (!specLineMap[sid]) specLineMap[sid] = [];
                specLineMap[sid].push(idx + 1);
            }
        });
    }

    // Merge Stage 4 `AUDIT_HISTORY.json` / `AUDIT_FEEDBACK.json` and Stage 1/2 `LOCAL_CRITIC.json`
    // records into `repairHistory` for the Counterexample Inspector.
    const fbData =
        safeReadJson(path.join(activeBaseDir, "AUDIT_HISTORY.json")) ||
        safeReadJson(path.join(activeBaseDir, "AUDIT_FEEDBACK.json")) ||
        safeReadJson(path.join(wsRoot, "AUDIT_FEEDBACK.json"));
    const repairHistory = [];
    if (fbData && typeof fbData === "object") {
        const histPrev = Array.isArray(fbData.historical_attempts) ? fbData.historical_attempts : [];
        histPrev.forEach((h, idx) => {
            if (h && typeof h === "object") {
                repairHistory.push({
                    attempt: Number(h.attempt_number || idx + 1),
                    obligationId: "Prior Attempt",
                    specId: "",
                    classification: "PRIOR_ATTEMPT",
                    expected: "",
                    actual: "",
                    diagnosis: String(h.summary || `Attempt ${idx + 1} (${h.failure_count || 1} failure(s))`),
                    resolved: Boolean(verText || isArchivedView),
                });
            }
        });
        const curFails = Array.isArray(fbData.failures) ? fbData.failures : [];
        curFails.forEach((f, idx) => {
            if (f && typeof f === "object") {
                const expVal = f.expected_oracle ?? f.expected;
                const actVal = f.actual_observed ?? f.actual ?? f.minimal_counterexample;
                repairHistory.push({
                    attempt: histPrev.length + idx + 1,
                    obligationId: String(f.obligation_id || f.id || "OBL-1"),
                    specId: String(f.spec_id || ""),
                    classification: String(
                        f.failure_classification || f.defect_type || f.defect_category || f.classification || f.category || f.type || "IMPLEMENTATION_DEFECT"
                    ).toUpperCase(),
                    expected: expVal !== undefined ? (typeof expVal === "string" ? expVal : JSON.stringify(expVal)) : "",
                    actual: actVal !== undefined ? (typeof actVal === "string" ? actVal : JSON.stringify(actVal)) : "",
                    diagnosis: String(f.diagnosis || f.description || "Verification counterexample recorded."),
                    resolved: Boolean(verText || isArchivedView),
                });
            }
        });
    }
    const lcData = safeReadJson(path.join(activeBaseDir, "LOCAL_CRITIC.json")) || safeReadJson(path.join(wsRoot, "LOCAL_CRITIC.json"));
    if (lcData && typeof lcData === "object" && Array.isArray(lcData.issues)) {
        lcData.issues.forEach((iss, idx) => {
            if (iss && typeof iss === "object") {
                repairHistory.push({
                    attempt: repairHistory.length + idx + 1,
                    obligationId: String(iss.obligation_id || iss.target || iss.id || lcData.stage || "LOCAL_CRITIC"),
                    specId: String(iss.spec_id || ""),
                    classification: String(iss.issue_type || lcData.stage || "LOCAL_SPEC_CRITIC").toUpperCase(),
                    expected: "",
                    actual: "",
                    diagnosis: String(iss.diagnosis || iss.description || iss.message || "Local semantic specification review note."),
                    resolved: String(lcData.status || "").toUpperCase() === "PASS",
                });
            }
        });
    }

    const verifyFiles = fs.existsSync(activeBaseDir)
        ? fs.readdirSync(activeBaseDir).filter((f) => f.startsWith("verify_") && f.endsWith(".py"))
        : [];
    const verifyFileName = verifyFiles[0] || (activeSlug ? `verify_${activeSlug}.py` : "verify_suite.py");
    const suiteCode = safeReadText(path.join(activeBaseDir, verifyFileName));

    const { imageRel, corpusDocs, references, candidates } = parseSourceAssets(wsRoot, activeBaseDir, manData, ocrText, resText);
    if (specs.length === 0 && candidates.length > 0) {
        candidates.forEach((cand, idx) => {
            if (cand.latex) {
                specs.push({
                    id: `CAND-${idx + 1}`,
                    title: cand.title,
                    latex: cand.latex,
                    status: "candidate",
                    detail: cand.citation,
                });
            }
        });
    }
    const researchHeadingMatch = resText.match(/^#\s+(?:Research\s+Brief:\s*)?([^\r\n]+)/m);
    const inferredConceptTitle = researchHeadingMatch ? stripMarkdownInline(researchHeadingMatch[1]) : "";
    const vc = manData.verification_contract && typeof manData.verification_contract === "object" ? manData.verification_contract : {};
    const vars = Array.isArray(manData.variables) ? manData.variables.slice(0, 8) : [];
    const constraints = Array.isArray(manData.constraints) ? manData.constraints.map((c) => String(c)).slice(0, 6) : [];
    const ambiguities = Array.isArray(manData.ambiguities) ? manData.ambiguities : [];

    // Assemble live or archived SHA-256 artifact digests for the topbar cryptographic seal popover.
    const activeSpecEntry = sealedSpecs.find((s) => s.slug === activeSlug);
    const sealSha = String(provData.seal_sha256 || fsm.state_seal_sha256 || "");
    const baseHashes =
        (activeSpecEntry && activeSpecEntry.artifactHashes) ||
        (provData.artifact_hashes && typeof provData.artifact_hashes === "object" ? provData.artifact_hashes : {});
    const artifactHashes = { ...baseHashes };
    if (Object.keys(artifactHashes).length === 0) {
        const liveCandidates = [
            "SOURCE_OCR.md",
            "RESEARCH.md",
            "manifest.json",
            "mapping.json",
            "requirements.txt",
            targetModule,
            verifyFileName,
            "LOCAL_CRITIC.json",
            "AUDIT_FEEDBACK.json",
            "VERIFICATION.md",
        ];
        for (const fn of liveCandidates) {
            if (!fn) continue;
            const digest = sha256File(path.join(activeBaseDir, fn));
            if (digest) artifactHashes[fn] = digest;
        }
    }

    const groundingLabel = imageRel
        ? path.basename(imageRel)
        : corpusDocs.length > 0
          ? `research/${corpusDocs[0].name}${corpusDocs.length > 1 ? ` +${corpusDocs.length - 1}` : ""}`
          : references.length > 0
            ? stripMarkdownInline(references[0].title).slice(0, 42)
            : "Awaiting source";

    const isIdle = !hasRootDrafts && sealedSpecs.length === 0;

    return {
        workspaceRoot: wsRoot,
        isIdle,
        isArchivedView,
        currentStage,
        activeSlug: activeSlug || "workspace",
        conceptTitle: String(manData.concept_name || mapData.concept_name || inferredConceptTitle || (activeSlug || "Facet Specification Studio")),
        domainCategory: String(manData.domain_category || "Specification-Driven Engineering"),
        groundingLabel,
        precisionLabel: `${vc.precision || "float64"} · ${vc.rtol ?? "1e-07"}`,
        atolLabel: String(vc.atol ?? "1e-09"),
        rtolLabel: String(vc.rtol ?? "1e-07"),
        passSummary: obligations.length > 0 ? `${obligations.filter((o) => o.verdict === "PASS").length} / ${obligations.length} PASS` : "Pending",
        repairSummary: `${Number(provData.repair_attempts ?? fsm.repair_attempts ?? 0)} / 3 Cycles`,
        sealSha,
        sealVerified: activeSpecEntry ? activeSpecEntry.sealVerified : Boolean(sealSha),
        artifactHashes,
        ambiguityCount: ambiguities.length,
        ocrSha: sha256File(path.join(activeBaseDir, "SOURCE_OCR.md")),
        resSha: sha256File(path.join(activeBaseDir, "RESEARCH.md")),
        mapSha: sha256File(path.join(activeBaseDir, "mapping.json")),
        specs,
        entrypoints,
        obligations,
        specToObls,
        oblToSpecs,
        edges,
        variables: vars,
        constraints,
        targetModule,
        verifyFileName,
        implCode,
        suiteCode,
        specLineMap,
        lineSpecTags,
        repairHistory,
        imageRel,
        corpusDocs,
        references,
        sealedSpecs,
        pytestCmd: isArchivedView && activeSlug
            ? `./.venv/bin/python -m pytest specs/${activeSlug}/${verifyFileName}`
            : `./.venv/bin/python -m pytest ${verifyFileName}`,
        prefs,
    };
}
