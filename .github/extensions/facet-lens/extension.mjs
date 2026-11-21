/**
 * @fileoverview Entry point for "Facet Lens" — Facet's live visual workbench inside VS Code.
 *
 * When Facet runs in VS Code Chat, the models write structured files to disk
 * (`SOURCE_OCR.md`, `RESEARCH.md`, `manifest.json`, `mapping.json`, `*.py`, `VERIFICATION.md`,
 * and `specs/<slug>/PROVENANCE.json`). Facet Lens opens a side-by-side Copilot Canvas panel
 * that renders those artifacts as interactive equations, `SPEC -> Function -> Proof` graphs,
 * and test matrices in real time with zero LLM token cost.
 *
 * Key Copilot SDK invariants enforced here:
 *   1. Standard output (`stdout`) carries the Copilot SDK JSON-RPC wire protocol, so
 *      diagnostics must only be written through `session.log()`.
 *   2. `instanceId` identifies one UI panel tab in VS Code, whereas `workspaceRoot`
 *      identifies the directory on disk that panel monitors.
 */

import path from "node:path";
import { fileURLToPath } from "node:url";
import { CanvasError, createCanvas, joinSession } from "@github/copilot-sdk/extension";

import { createLensServer } from "./server.mjs";
import { buildWorkspaceSnapshot, CANVAS_ID, loadUiPreferences, saveUiPreferences } from "./state.mjs";

// Default to the repository root three levels above `.github/extensions/facet-lens/`.
const EXTENSION_DIR = path.dirname(fileURLToPath(import.meta.url));
const DEFAULT_WORKSPACE_ROOT = path.resolve(EXTENSION_DIR, "..", "..", "..");

// Active Canvas panel instances keyed by the caller-chosen `instanceId`.
const instances = new Map();
let sessionRef = null;

/**
 * Emit a diagnostic message safely through the Copilot SDK session logger.
 * Writing to stdout would corrupt the extension host's stdio transport.
 */
function logDiagnostic(message) {
    try {
        if (sessionRef && typeof sessionRef.log === "function") {
            sessionRef.log(message);
        }
    } catch {
        // Diagnostics must never write to stdout.
    }
}

/**
 * Determine which workspace folder on disk a Canvas request should inspect.
 * Priority: explicit tool input (`workspaceRoot`/`workspacePath`) -> active Copilot
 * session working directory -> repository root containing this extension.
 */
function resolveWorkspaceForRequest(ctx, explicitPath = "") {
    if (typeof explicitPath === "string" && explicitPath.trim().length > 0) {
        return path.resolve(explicitPath.trim());
    }
    const fromSessionCtx = ctx?.session?.workingDirectory;
    if (typeof fromSessionCtx === "string" && fromSessionCtx.trim().length > 0) {
        return path.resolve(fromSessionCtx.trim());
    }
    return DEFAULT_WORKSPACE_ROOT;
}

/**
 * Convert raw filesystem or validation errors into structured `CanvasError` instances
 * so the Copilot host surfaces clean, actionable messages instead of crashing.
 */
function wrapStorageError(err) {
    if (err instanceof CanvasError) return err;
    const code = typeof err?.code === "string" ? err.code : "facet_lens_storage_error";
    return new CanvasError(code, err?.message || "Facet Lens storage operation failed.");
}

/**
 * Extract an optional workspace directory override from `open_canvas` or action inputs.
 */
function extractInputWorkspacePath(inputObj) {
    if (!inputObj || typeof inputObj !== "object" || Array.isArray(inputObj)) return "";
    const candidate = inputObj.workspaceRoot || inputObj.workspacePath || "";
    return typeof candidate === "string" ? candidate.trim() : "";
}

/**
 * Open or focus a Facet Lens panel.
 * - First call for a given `instanceId`: boots an ephemeral `127.0.0.1` HTTP + SSE server.
 * - Subsequent calls for the same `instanceId`: idempotently reuses the running server,
 *   updates its watched folder if `workspaceRoot` changed, and pushes a live SSE reload.
 */
async function handleOpen(ctx) {
    // Unpack optional `workspaceRoot` / `workspacePath` input and resolve the target folder.
    const rawInput = ctx?.input && typeof ctx.input === "object" && !Array.isArray(ctx.input) ? ctx.input : {};
    const explicitWs = extractInputWorkspacePath(rawInput);
    const existing = instances.get(ctx.instanceId);
    const wsRoot = resolveWorkspaceForRequest(
        ctx,
        explicitWs || existing?.stateBox?.workspaceRoot || ""
    );
    // Load persisted per-workspace UI preferences (tab, theme, locked node, pinned excerpts).
    let prefs;
    try {
        prefs = loadUiPreferences(wsRoot);
    } catch (err) {
        throw wrapStorageError(err);
    }

    let entry = existing;
    if (!entry) {
        // First open for this `instanceId`: start a dedicated loopback `127.0.0.1` server.
        // Wrapping `workspaceRoot` inside `stateBox` lets a later reopen switch directories
        // without tearing down the live HTTP port or browser EventSource connection.
        const stateBox = { workspaceRoot: wsRoot };
        let serverHandle;
        try {
            serverHandle = await createLensServer({
                instanceId: ctx.instanceId,
                getWorkspaceRoot: () => stateBox.workspaceRoot,
                log: logDiagnostic,
            });
        } catch (err) {
            throw new CanvasError("server_start_failed", `Unable to start Facet Lens server: ${err.message}`);
        }
        entry = {
            instanceId: ctx.instanceId,
            stateBox,
            server: serverHandle,
        };
        instances.set(ctx.instanceId, entry);
    } else {
        // Reopening an existing `instanceId`: update the monitored folder if needed,
        // refresh OS file watchers, and notify connected browser clients via SSE.
        const wsChanged = entry.stateBox.workspaceRoot !== wsRoot;
        entry.stateBox.workspaceRoot = wsRoot;
        entry.server.refreshWatchers();
        entry.server.broadcast("reload", { ts: Date.now(), workspaceChanged: wsChanged });
    }

    // Build an initial snapshot so the Canvas tab header shows live seal/stage status.
    const snap = buildWorkspaceSnapshot(wsRoot, prefs);
    return {
        title: "Facet Lens",
        status: snap.sealVerified ? `Verified · ${snap.activeSlug}` : snap.currentStage,
        url: entry.server.url,
    };
}

// Stop and unregister this panel instance's loopback HTTP server when closed by the host.
async function handleClose(ctx) {
    const entry = instances.get(ctx.instanceId);
    if (entry) {
        instances.delete(ctx.instanceId);
        await entry.server.close();
    }
}

// Action handler (`inspect_lens_state`): return current stage, seal status, and proof counts as JSON.
async function handleInspectLensState(ctx) {
    const rawInput = ctx.input && typeof ctx.input === "object" && !Array.isArray(ctx.input) ? ctx.input : {};
    const entry = instances.get(ctx.instanceId);
    const wsRoot = resolveWorkspaceForRequest(ctx, extractInputWorkspacePath(rawInput) || entry?.stateBox?.workspaceRoot || "");
    let prefs;
    try {
        prefs = loadUiPreferences(wsRoot);
    } catch (err) {
        throw wrapStorageError(err);
    }
    const snap = buildWorkspaceSnapshot(wsRoot, prefs);
    return {
        canvasId: CANVAS_ID,
        instanceId: ctx.instanceId,
        workspaceRoot: snap.workspaceRoot,
        currentStage: snap.currentStage,
        activeSlug: snap.activeSlug,
        conceptTitle: snap.conceptTitle,
        sealVerified: snap.sealVerified,
        sealSha: snap.sealSha,
        passSummary: snap.passSummary,
        activeTab: prefs.activeTab,
        theme: prefs.theme,
        sealedPackageCount: snap.sealedSpecs.length,
    };
}

// Action handler (`focus_lens_view`): switch active tab, theme, or archived `specs/<slug>/` package
// and push an immediate SSE `focus` event to all connected browser views.
async function handleFocusLensView(ctx) {
    const rawInput = ctx.input && typeof ctx.input === "object" && !Array.isArray(ctx.input) ? ctx.input : {};
    const entry = instances.get(ctx.instanceId);
    const wsRoot = resolveWorkspaceForRequest(ctx, extractInputWorkspacePath(rawInput) || entry?.stateBox?.workspaceRoot || "");
    const patch = {};
    if (rawInput.view !== undefined) patch.activeTab = rawInput.view;
    if (rawInput.theme !== undefined) patch.theme = rawInput.theme;
    if (rawInput.specSlug !== undefined) patch.selectedSpecSlug = rawInput.specSlug;

    let updated;
    try {
        updated = saveUiPreferences(wsRoot, patch);
    } catch (err) {
        throw wrapStorageError(err);
    }

    if (entry) {
        const wsChanged = entry.stateBox.workspaceRoot !== wsRoot;
        entry.stateBox.workspaceRoot = wsRoot;
        if (wsChanged) {
            entry.server.refreshWatchers();
        }
        entry.server.broadcast("focus", {
            activeTab: updated.activeTab,
            theme: updated.theme,
            reload: Boolean(rawInput.specSlug || wsChanged),
        });
    }

    return {
        ok: true,
        instanceId: ctx.instanceId,
        activeTab: updated.activeTab,
        selectedSpecSlug: updated.selectedSpecSlug,
        theme: updated.theme,
    };
}

// Register the `facet-pipeline-lens` Canvas provider and its two agent-callable actions with Copilot SDK.
sessionRef = await joinSession({
    canvases: [
        createCanvas({
            id: CANVAS_ID,
            displayName: "Facet Lens",
            description: "Inspect live Facet equations, typed Python interfaces, 4-predicate cleanroom proofs, and Merkle provenance seals.",
            icon: "./assets/facet-prism.svg",
            inputSchema: {
                type: "object",
                properties: {
                    workspaceRoot: {
                        type: "string",
                        description: "Optional absolute workspace path (or live symlink path) for Facet Lens to monitor.",
                    },
                    workspacePath: {
                        type: "string",
                        description: "Optional alias for workspaceRoot.",
                    },
                },
                additionalProperties: false,
            },
            actions: [
                {
                    name: "inspect_lens_state",
                    description: "Read the current Facet pipeline stage, active specification, proof summary, and Merkle seal verification status.",
                    inputSchema: {
                        type: "object",
                        properties: {
                            workspaceRoot: {
                                type: "string",
                                description: "Optional absolute workspace path to inspect.",
                            },
                            workspacePath: {
                                type: "string",
                                description: "Optional alias for workspaceRoot.",
                            },
                        },
                        additionalProperties: false,
                    },
                    handler: handleInspectLensState,
                },
                {
                    name: "focus_lens_view",
                    description: "Switch the active Facet Lens tab, color theme, or focused archived specification package.",
                    inputSchema: {
                        type: "object",
                        properties: {
                            view: {
                                type: "string",
                                enum: ["traceability", "source-math", "proofs", "code", "specs"],
                                description: "Target view tab to focus in Facet Lens.",
                            },
                            specSlug: {
                                type: "string",
                                description: "Optional archived specs/<slug>/ package to load.",
                            },
                            theme: {
                                type: "string",
                                enum: ["dark", "light"],
                                description: "Optional color theme ('dark' or 'light').",
                            },
                            workspaceRoot: {
                                type: "string",
                                description: "Optional absolute workspace path.",
                            },
                            workspacePath: {
                                type: "string",
                                description: "Optional alias for workspaceRoot.",
                            },
                        },
                        additionalProperties: false,
                    },
                    handler: handleFocusLensView,
                },
            ],
            open: handleOpen,
            onClose: handleClose,
        }),
    ],
});

if (sessionRef && typeof sessionRef.on === "function") {
    sessionRef.on((event) => {
        if (event?.type === "tool.execution_complete" || event?.type === "assistant.message") {
            for (const entry of instances.values()) {
                entry.server.broadcast("reload", { ts: Date.now() });
            }
        }
    });
}

