/**
 * @fileoverview Embedded local HTTP + Server-Sent Events (SSE) server for Facet Lens.
 *
 * Serves the Facet Lens web UI over `127.0.0.1` on an ephemeral OS-chosen port and keeps
 * the browser view synchronized with disk state through three mechanisms:
 *   1. Direct Hook Push (`POST /api/notify`): `pipeline_guards.py` posts an 80ms loopback
 *      notification whenever an artifact passes validation or a stage transition occurs.
 *   2. Directory Watchers (`fs.watch`): Monitors workspace root and `specs/` directories.
 *   3. Fast Metadata & Symlink Digest (`computeFastWorkspaceDigest` every 600ms): Detects
 *      atomic file renames and `live_lens_workspace` symlink target swaps during benchmarks.
 */
import crypto from "node:crypto";
import fs from "node:fs";
import { createServer } from "node:http";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { buildWorkspaceSnapshot, getExtensionDataDir, loadUiPreferences, saveUiPreferences } from "./state.mjs";
import { renderLensHtml } from "./ui.mjs";

const MODULE_DIR = path.dirname(fileURLToPath(import.meta.url));
const PRISM_SVG_PATH = path.join(MODULE_DIR, "assets", "facet-prism.svg");

// Whitelisted raster/vector MIME types allowed through `/api/asset`.
const IMAGE_MIMES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
    ".svg": "image/svg+xml",
};

/**
 * Read and parse a bounded JSON request body (default 16 KiB limit to prevent memory abuse).
 */
function readJsonBody(req, maxBytes = 16384) {
    return new Promise((resolve, reject) => {
        const chunks = [];
        let total = 0;
        req.on("data", (chunk) => {
            total += chunk.length;
            if (total > maxBytes) {
                reject(new Error("Payload too large"));
                req.destroy();
                return;
            }
            chunks.push(chunk);
        });
        req.on("end", () => {
            try {
                const raw = Buffer.concat(chunks).toString("utf8").trim();
                resolve(raw ? JSON.parse(raw) : {});
            } catch (err) {
                reject(err);
            }
        });
        req.on("error", reject);
    });
}

/**
 * Safely resolve an image asset path requested by `?rel=...`, verifying both lexical
 * containment and `fs.realpathSync` containment so `../` or symlink escapes outside
 * the workspace directory are always rejected with HTTP 403.
 */
function resolveContainedAssetPath(workspaceRoot, relParam) {
    if (typeof relParam !== "string" || !relParam.trim()) return null;
    if (relParam.includes("\0")) return null;
    const wsReal = fs.realpathSync(workspaceRoot);
    const candidate = path.resolve(wsReal, relParam);
    if (candidate !== wsReal && !candidate.startsWith(wsReal + path.sep)) {
        return null;
    }
    if (!fs.existsSync(candidate)) return null;
    const realTarget = fs.realpathSync(candidate);
    if (realTarget !== wsReal && !realTarget.startsWith(wsReal + path.sep)) {
        return null;
    }
    if (!fs.statSync(realTarget).isFile()) return null;
    return realTarget;
}

// Start a loopback `127.0.0.1:0` HTTP + SSE server and file-watcher loop for one Canvas panel.
export async function createLensServer({ instanceId, getWorkspaceRoot, log }) {
    const sseClients = new Set();
    let debounceTimer = null;
    let watchPollTimer = null;
    const watchers = [];
    const endpointFilePaths = new Set();
    let lastObservedRealWs = "";
    let lastObservedFastDigest = "";

    function computeFastWorkspaceDigest(wsRootInput) {
        // Resolve both the requested path and its underlying real directory (`realpathSync`)
        // so symlink swaps (such as `live_lens_workspace -> step_19.2/workspace`) produce a
        // new digest immediately even before new files are written inside the new folder.
        const wsResolved = path.resolve(String(wsRootInput || process.cwd()));
        let realWs = wsResolved;
        try {
            if (fs.existsSync(wsResolved)) {
                realWs = fs.realpathSync(wsResolved);
            }
        } catch {
            // Keep `wsResolved` if the symlink target is mid-creation.
        }
        const parts = [`ws:${wsResolved}`, `real:${realWs}`];
        try {
            const prefs = loadUiPreferences(wsResolved);
            parts.push(`sel:${prefs.selectedSpecSlug || ""}`);
        } catch {
            // Ignore unreadable preference file during fast digest polling.
        }
        try {
            // Hash file names, sizes, and millisecond modification timestamps across the
            // workspace root and `specs/*/` subdirectories without reading full file bodies.
            if (fs.existsSync(realWs) && fs.statSync(realWs).isDirectory()) {
                for (const d of fs.readdirSync(realWs, { withFileTypes: true })) {
                    if (d.name === ".git" || d.name === ".venv" || d.name === "__pycache__" || d.name === ".pytest_cache" || d.name === ".hypothesis") {
                        continue;
                    }
                    const full = path.join(realWs, d.name);
                    if (d.isFile()) {
                        const st = fs.statSync(full);
                        parts.push(`f:${d.name}:${st.mtimeMs}:${st.size}`);
                    } else if (d.isDirectory() && d.name === "specs") {
                        for (const sd of fs.readdirSync(full, { withFileTypes: true })) {
                            if (!sd.isDirectory()) continue;
                            const sdir = path.join(full, sd.name);
                            for (const sf of fs.readdirSync(sdir, { withFileTypes: true })) {
                                if (!sf.isFile()) continue;
                                const sst = fs.statSync(path.join(sdir, sf.name));
                                parts.push(`s:${sd.name}/${sf.name}:${sst.mtimeMs}:${sst.size}`);
                            }
                        }
                    }
                }
            }
        } catch {
            // Ignore transient races when `os.replace` swaps a temporary file on disk.
        }
        parts.sort();
        return {
            realWs,
            digest: crypto.createHash("sha256").update(parts.join("|")).digest("hex").slice(0, 32),
        };
    }

    function writeLiveEndpointRegistry(portNum) {
        // Register our local `http://127.0.0.1:<port>/api/notify` URL under BOTH the
        // unresolved workspace path hash and the `realpathSync` path hash so Python's
        // `Path(cwd).resolve()` in `pipeline_guards.py` always finds the live endpoint.
        try {
            const wsCanonical = path.resolve(String(getWorkspaceRoot() || process.cwd()));
            const candidates = new Set([wsCanonical]);
            try {
                if (fs.existsSync(wsCanonical)) {
                    candidates.add(fs.realpathSync(wsCanonical));
                }
            } catch {
                // ignore broken symlink until target exists
            }
            const dir = getExtensionDataDir();
            fs.mkdirSync(dir, { recursive: true });
            for (const candPath of candidates) {
                const wsDigest = crypto.createHash("sha256").update(candPath).digest("hex").slice(0, 24);
                const target = path.join(dir, `live_endpoint_${wsDigest}.json`);
                const tmp = path.join(dir, `.ep_${process.pid}_${process.hrtime.bigint()}.json`);
                const payload = {
                    url: `http://127.0.0.1:${portNum}/api/notify`,
                    pid: process.pid,
                    instanceId,
                    workspaceRoot: candPath,
                    updatedAt: new Date().toISOString(),
                };
                fs.writeFileSync(tmp, JSON.stringify(payload, null, 2) + "\n", "utf8");
                fs.renameSync(tmp, target);
                endpointFilePaths.add(target);
            }
        } catch (err) {
            log?.(`Facet Lens endpoint registry warning: ${err.message}`);
        }
    }

    // Write a Server-Sent Event frame to every connected browser tab, pruning closed sockets.
    function broadcast(eventName, payload = {}) {
        const frame = `event: ${eventName}\ndata: ${JSON.stringify(payload)}\n\n`;
        for (const res of sseClients) {
            try {
                res.write(frame);
            } catch {
                sseClients.delete(res);
            }
        }
    }

    // Coalesce bursty multi-file edits into a single `reload` push after 60 ms.
    function scheduleReloadBroadcast() {
        if (debounceTimer) clearTimeout(debounceTimer);
        debounceTimer = setTimeout(() => {
            broadcast("reload", { ts: Date.now() });
        }, 60);
    }

    // Attach non-persistent `fs.watch` listeners on the workspace root and `specs/` folder.
    function attachWatchers() {
        for (const w of watchers.splice(0)) {
            try {
                w.close();
            } catch {
                // ignore
            }
        }
        try {
            const wsRoot = getWorkspaceRoot();
            const targetDir = fs.existsSync(wsRoot) ? fs.realpathSync(wsRoot) : wsRoot;
            lastObservedRealWs = targetDir;
            if (fs.existsSync(targetDir) && fs.statSync(targetDir).isDirectory()) {
                watchers.push(
                    fs.watch(targetDir, { persistent: false }, (_event, filename) => {
                        if (!filename || filename.startsWith(".git")) return;
                        scheduleReloadBroadcast();
                    })
                );
                const specsDir = path.join(targetDir, "specs");
                if (fs.existsSync(specsDir) && fs.statSync(specsDir).isDirectory()) {
                    watchers.push(
                        fs.watch(specsDir, { persistent: false }, () => {
                            scheduleReloadBroadcast();
                        })
                    );
                }
            }
        } catch (err) {
            log?.(`Facet Lens watcher warning (${instanceId}): ${err.message}`);
        }
    }

    const server = createServer(async (req, res) => {
        try {
            const reqUrl = new URL(req.url || "/", "http://127.0.0.1");
            const wsRoot = getWorkspaceRoot();

            // Serve the bundled Facet prism brand icon.
            if (req.method === "GET" && reqUrl.pathname === "/assets/facet-prism.svg") {
                const svg = fs.readFileSync(PRISM_SVG_PATH, "utf8");
                res.writeHead(200, { "Content-Type": "image/svg+xml; charset=utf-8", "Cache-Control": "no-cache" });
                res.end(svg);
                return;
            }

            // Open a persistent `text/event-stream` SSE channel for sub-60ms live reloads.
            if (req.method === "GET" && reqUrl.pathname === "/api/events") {
                res.writeHead(200, {
                    "Content-Type": "text/event-stream; charset=utf-8",
                    "Cache-Control": "no-cache",
                    Connection: "keep-alive",
                });
                res.write(": connected\n\n");
                sseClients.add(res);
                req.on("close", () => sseClients.delete(res));
                return;
            }

            // Return the full computed workspace snapshot as JSON.
            if (req.method === "GET" && reqUrl.pathname === "/api/state") {
                const prefs = loadUiPreferences(wsRoot);
                const snap = buildWorkspaceSnapshot(wsRoot, prefs);
                res.writeHead(200, { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-cache" });
                res.end(JSON.stringify(snap));
                return;
            }

            if (req.method === "GET" && reqUrl.pathname === "/api/digest") {
                const { digest } = computeFastWorkspaceDigest(wsRoot);
                const snap = buildWorkspaceSnapshot(wsRoot);
                res.writeHead(200, { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-cache" });
                res.end(JSON.stringify({ digest, stage: snap.currentStage, activeSlug: snap.activeSlug, ts: Date.now() }));
                return;
            }

            if (req.method === "POST" && reqUrl.pathname === "/api/notify") {
                const body = await readJsonBody(req);
                broadcast("reload", { ts: Date.now(), stage: String(body?.stage || "") });
                res.writeHead(200, { "Content-Type": "application/json; charset=utf-8" });
                res.end(JSON.stringify({ ok: true }));
                return;
            }

            if (req.method === "POST" && reqUrl.pathname === "/api/prefs") {
                const body = await readJsonBody(req);
                const updated = saveUiPreferences(wsRoot, body);
                res.writeHead(200, { "Content-Type": "application/json; charset=utf-8" });
                res.end(JSON.stringify({ ok: true, prefs: updated }));
                return;
            }

            if (req.method === "GET" && reqUrl.pathname === "/api/asset") {
                const rel = reqUrl.searchParams.get("rel") || "";
                const safePath = resolveContainedAssetPath(wsRoot, rel);
                if (!safePath) {
                    res.writeHead(403, { "Content-Type": "text/plain; charset=utf-8" });
                    res.end("Forbidden asset path");
                    return;
                }
                const ext = path.extname(safePath).toLowerCase();
                const mime = IMAGE_MIMES[ext];
                if (!mime) {
                    res.writeHead(415, { "Content-Type": "text/plain; charset=utf-8" });
                    res.end("Unsupported asset type");
                    return;
                }
                res.writeHead(200, { "Content-Type": mime, "Cache-Control": "no-cache" });
                res.end(fs.readFileSync(safePath));
                return;
            }

            if (req.method === "GET" && (reqUrl.pathname === "/" || reqUrl.pathname === "/index.html")) {
                const prefs = loadUiPreferences(wsRoot);
                const snapshot = buildWorkspaceSnapshot(wsRoot, prefs);
                const html = renderLensHtml(snapshot);
                res.writeHead(200, {
                    "Content-Type": "text/html; charset=utf-8",
                    "Cache-Control": "no-cache",
                    "X-Content-Type-Options": "nosniff",
                });
                res.end(html);
                return;
            }

            res.writeHead(404, { "Content-Type": "text/plain; charset=utf-8" });
            res.end("Not found");
        } catch (err) {
            res.writeHead(500, { "Content-Type": "application/json; charset=utf-8" });
            res.end(JSON.stringify({ error: err.code || "internal_error", message: err.message }));
        }
    });

    await new Promise((resolve, reject) => {
        server.once("error", reject);
        server.listen(0, "127.0.0.1", () => resolve());
    });
    attachWatchers();

    const addr = server.address();
    const port = typeof addr === "object" && addr ? addr.port : 0;
    writeLiveEndpointRegistry(port);
    lastObservedFastDigest = computeFastWorkspaceDigest(getWorkspaceRoot()).digest;

    watchPollTimer = setInterval(() => {
        try {
            const wsCur = getWorkspaceRoot();
            const { realWs, digest } = computeFastWorkspaceDigest(wsCur);
            if (realWs !== lastObservedRealWs) {
                attachWatchers();
                writeLiveEndpointRegistry(port);
            }
            if (digest !== lastObservedFastDigest) {
                lastObservedFastDigest = digest;
                scheduleReloadBroadcast();
            }
        } catch {
            // ignore transient fs errors
        }
    }, 600);
    watchPollTimer.unref?.();

    return {
        url: `http://127.0.0.1:${port}/`,
        broadcast,
        refreshWatchers() {
            attachWatchers();
            writeLiveEndpointRegistry(port);
            lastObservedFastDigest = computeFastWorkspaceDigest(getWorkspaceRoot()).digest;
        },
        async close() {
            if (debounceTimer) clearTimeout(debounceTimer);
            if (watchPollTimer) clearInterval(watchPollTimer);
            for (const epFile of endpointFilePaths) {
                try {
                    if (fs.existsSync(epFile)) fs.unlinkSync(epFile);
                } catch {
                    // ignore
                }
            }
            endpointFilePaths.clear();
            for (const w of watchers.splice(0)) {
                try {
                    w.close();
                } catch {
                    // ignore
                }
            }
            for (const client of sseClients) {
                try {
                    client.end();
                } catch {
                    // ignore
                }
            }
            sseClients.clear();
            await new Promise((resolve) => server.close(() => resolve()));
        },
    };
}
