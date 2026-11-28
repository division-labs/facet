#!/usr/bin/env node
/**
 * Shared 1:1 Copilot SDK (`copilot-runtime`) + Loopback Ollama Wire-Tap Runner.
 *
 * Drives the bundled `@github/copilot-sdk` (`copilot-runtime`) binary against an
 * isolated workspace and `COPILOT_HOME`, routing all `ollama-models/*` HTTP traffic
 * through a byte-preserving loopback proxy so both the native `events.jsonl` ledger
 * and raw `wire_capture.jsonl` socket payloads are captured 1:1 across interactive
 * audits, benchmarks, and multi-modality validation suites.
 *
 * Function Map:
 *   - Section 1: CLI & Option Parsing (`parseArgs`)
 *   - Section 2: Bundled Copilot SDK & Response Helpers (`resolveSdkBundlePaths`, `parseSseOrJsonResponse`, `applyWorkspaceModelOverrideIfCopied`)
 *   - Section 3: Host Sleep Assertion & Loopback Wire-Tap Proxy (`main`)
 *   - Section 4: Isolated Workspace & COPILOT_HOME Materialization (`main`)
 *   - Section 5: CopilotClient Session Loop, Checkpoint Auto-Approval & Harvesting (`main`)
 *
 * Copyright 2026 Facet Contributors. Licensed under the Apache License, Version 2.0.
 */

import { execFileSync, spawn } from "node:child_process";
import fs from "node:fs";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";

// ============================================================================
// Section 1: CLI & Option Parsing
// Parses workspace/governance/modality flags and validates required output paths.
// ============================================================================

function parseArgs(argv) {
  const opts = {
    probeSdk: false,
    repoRoot: process.cwd(),
    outDir: "",
    workspaceDir: "",
    copilotHome: "",
    keepWorkspace: false,
    governanceMode: "interactive",
    allowWebResearch: null,
    experimentsJson: "",
    researchFiles: [],
    seedSpecsDir: "",
    noExamples: false,
    modelOverride: "",
    reasoningEffort: "",
    prompt:
      "@Facet What can you see through your crystalline lens if you peer into the examples folder about the magical mystery image?",
    maxApproves: 8,
    maxTurns: 0,
    turnTimeoutMs: 3600000,
    ollamaBaseUrl: process.env.OLLAMA_HOST || process.env.OLLAMA_BASE_URL || "http://127.0.0.1:11434",
  };
  for (let i = 2; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--probe-sdk") opts.probeSdk = true;
    else if (a === "--repo-root" && argv[i + 1]) opts.repoRoot = path.resolve(argv[++i]);
    else if (a === "--out-dir" && argv[i + 1]) opts.outDir = path.resolve(argv[++i]);
    else if (a === "--workspace-dir" && argv[i + 1]) opts.workspaceDir = path.resolve(argv[++i]);
    else if (a === "--copilot-home" && argv[i + 1]) opts.copilotHome = path.resolve(argv[++i]);
    else if (a === "--keep-workspace") opts.keepWorkspace = true;
    else if (a === "--governance-mode" && argv[i + 1]) opts.governanceMode = argv[++i];
    else if (a === "--allow-web-research" && argv[i + 1]) opts.allowWebResearch = argv[++i] === "true";
    else if (a === "--experiments-json" && argv[i + 1]) opts.experimentsJson = argv[++i];
    else if (a === "--research-file" && argv[i + 1]) opts.researchFiles.push(path.resolve(argv[++i]));
    else if (a === "--seed-specs-dir" && argv[i + 1]) opts.seedSpecsDir = path.resolve(argv[++i]);
    else if (a === "--no-examples") opts.noExamples = true;
    else if (a === "--model-override" && argv[i + 1]) opts.modelOverride = argv[++i].trim();
    else if (a === "--reasoning-effort" && argv[i + 1]) opts.reasoningEffort = argv[++i].trim();
    else if (a === "--prompt" && argv[i + 1]) opts.prompt = argv[++i];
    else if (a === "--max-approves" && argv[i + 1]) opts.maxApproves = Number(argv[++i]);
    else if (a === "--max-turns" && argv[i + 1]) opts.maxTurns = Number(argv[++i]);
    else if (a === "--turn-timeout-ms" && argv[i + 1]) opts.turnTimeoutMs = Number(argv[++i]);
    else if (a === "--ollama-url" && argv[i + 1]) opts.ollamaBaseUrl = argv[++i];
  }
  if (!opts.probeSdk && !opts.outDir && !opts.workspaceDir) {
    throw new Error("Provide at least --out-dir <path> or --workspace-dir <path> (or --probe-sdk).");
  }
  return opts;
}

// ============================================================================
// Section 2: Bundled Copilot SDK & Response Helpers
// Locates VS Code's unpacked `@github/copilot-sdk` + `copilot-runtime` binary,
// reassembles `/v1/responses` SSE streams into completed JSON payloads for the
// wire ledger, and applies optional specialist model overrides when `.github` is copied.
// ============================================================================

function resolveSdkBundlePaths() {
  const candidates = [
    process.env.FACET_COPILOT_SDK_DIR || "",
    "/Applications/Visual Studio Code.app/Contents/Resources/app/node_modules.asar.unpacked/@github/copilot-sdk-darwin-arm64",
    "/Applications/Visual Studio Code - Insiders.app/Contents/Resources/app/node_modules.asar.unpacked/@github/copilot-sdk-darwin-arm64",
    "/usr/share/code/resources/app/node_modules.asar.unpacked/@github/copilot-sdk-linux-x64",
    "/usr/share/code/resources/app/node_modules.asar.unpacked/@github/copilot-sdk-linux-arm64",
  ].filter(Boolean);

  for (const base of candidates) {
    const sdkJs = path.join(base, "copilot-sdk", "index.js");
    const runtimeBin = path.join(base, "prebuilds", `${process.platform}-${process.arch}`, "copilot-runtime");
    if (fs.existsSync(sdkJs) && fs.existsSync(runtimeBin)) {
      return { sdkJs, runtimeBin };
    }
  }
  return null;
}

function parseSseOrJsonResponse(rawBuf, contentType) {
  const text = rawBuf.toString("utf8");
  const ct = String(contentType || "").toLowerCase();
  if (!ct.includes("text/event-stream")) {
    try {
      return JSON.parse(text);
    } catch {
      return { _raw_text: text };
    }
  }
  let completedObj = null;
  const events = [];
  for (const line of text.split(/\r?\n/)) {
    if (!line.startsWith("data:")) continue;
    const payload = line.slice(5).trim();
    if (!payload || payload === "[DONE]") continue;
    try {
      const parsed = JSON.parse(payload);
      events.push(parsed);
      if (parsed && parsed.type === "response.completed" && parsed.response) {
        completedObj = parsed.response;
      }
    } catch {
      // keep scanning
    }
  }
  return completedObj || { _sse_events: events };
}

function applyWorkspaceModelOverrideIfCopied(wsDir, modelOverride) {
  if (!modelOverride) return;
  const cleanId = modelOverride.replace(/^ollama-models\//, "");
  const agentsDir = path.join(wsDir, ".github", "agents");
  if (!fs.existsSync(agentsDir)) return;
  const st = fs.lstatSync(path.join(wsDir, ".github"));
  if (st.isSymbolicLink()) return;
  for (const roleFile of ["researcher.agent.md", "formalist.agent.md", "engineer.agent.md", "auditor.agent.md"]) {
    const fp = path.join(agentsDir, roleFile);
    if (!fs.existsSync(fp)) continue;
    const raw = fs.readFileSync(fp, "utf8");
    const updated = raw.replace(/ollama-models\/[A-Za-z0-9._:-]+/, `ollama-models/${cleanId}`);
    if (updated !== raw) {
      fs.writeFileSync(fp, updated, "utf8");
    }
  }
}

// ============================================================================
// Section 3: Host Sleep Assertion & Loopback Wire-Tap Proxy
// Acquires a macOS `caffeinate -i` idle-sleep assertion for unattended runs and
// binds an ephemeral `127.0.0.1` HTTP proxy recording every Ollama request/response.
// ============================================================================

async function main() {
  const opts = parseArgs(process.argv);
  const resolvedSdk = resolveSdkBundlePaths();
  if (opts.probeSdk) {
    if (resolvedSdk) {
      console.log(JSON.stringify({ available: true, ...resolvedSdk }));
      process.exit(0);
    }
    console.log(JSON.stringify({ available: false }));
    process.exit(1);
  }
  if (!resolvedSdk) {
    throw new Error("Could not locate @github/copilot-sdk index.js and copilot-runtime binary.");
  }
  const { sdkJs, runtimeBin } = resolvedSdk;

  // Hold a macOS idle-sleep prevention assertion (`PreventUserIdleSystemSleep`) while copilot-runtime executes.
  let caffeinateProc = null;
  if (process.platform === "darwin" && fs.existsSync("/usr/bin/caffeinate")) {
    try {
      caffeinateProc = spawn("/usr/bin/caffeinate", ["-i", "-w", String(process.pid)], {
        stdio: "ignore",
        detached: false,
      });
      caffeinateProc.unref();
    } catch {
      caffeinateProc = null;
    }
  }

  let tmpRoot = "";
  const ownsTmpRoot = !opts.workspaceDir || !opts.copilotHome;
  if (ownsTmpRoot) {
    tmpRoot = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "facet_sdk_run_")));
  }

  const rawWs = opts.workspaceDir || path.join(tmpRoot, "GitHub Projects", "FacetLiveRun");
  fs.mkdirSync(rawWs, { recursive: true });
  const wsDir = fs.realpathSync(rawWs);

  const rawChome = opts.copilotHome || path.join(tmpRoot, "copilot_home");
  fs.mkdirSync(rawChome, { recursive: true });
  const copilotHome = fs.realpathSync(rawChome);

  const rawDir = opts.outDir ? path.join(opts.outDir, "raw") : wsDir;
  fs.mkdirSync(rawDir, { recursive: true });
  const wireLogPath = path.join(rawDir, "wire_capture.jsonl");
  fs.writeFileSync(wireLogPath, "", "utf8");

  let normalizedOllama = opts.ollamaBaseUrl.trim();
  if (!normalizedOllama.startsWith("http://") && !normalizedOllama.startsWith("https://")) {
    normalizedOllama = `http://${normalizedOllama}`;
  }
  const upstreamUrl = new URL(normalizedOllama);
  const upstreamHost = upstreamUrl.hostname || "127.0.0.1";
  const upstreamPort = Number(upstreamUrl.port || (upstreamUrl.protocol === "https:" ? 443 : 11434));

  let wireSeq = 0;
  const proxy = http.createServer((req, res) => {
    const startHr = process.hrtime.bigint();
    const startedAt = new Date().toISOString();
    const reqChunks = [];
    req.on("data", (chunk) => reqChunks.push(chunk));
    req.on("end", () => {
      const reqBuf = Buffer.concat(reqChunks);
      const curSeq = ++wireSeq;
      const upReq = http.request(
        {
          hostname: upstreamHost,
          port: upstreamPort,
          path: req.url,
          method: req.method,
          headers: { ...req.headers, host: `${upstreamHost}:${upstreamPort}` },
        },
        (upRes) => {
          const resChunks = [];
          let streamedBytes = 0;
          let reqModelLabel = "llm";
          try {
            const pReq = JSON.parse(reqBuf.toString("utf8"));
            if (pReq && pReq.model) reqModelLabel = String(pReq.model);
          } catch {}
          const hbTimer = setInterval(() => {
            const elapsedS = Math.round(Number(process.hrtime.bigint() - startHr) / 1e9);
            const kbStr = (streamedBytes / 1024).toFixed(1);
            const tsHb = new Date().toISOString().slice(11, 19);
            process.stderr.write(`[copilot-sdk ${tsHb}]      ... streaming (${reqModelLabel}, ${elapsedS}s elapsed, ${kbStr} KB)\n`);
          }, 30000);
          hbTimer.unref?.();
          res.writeHead(upRes.statusCode || 200, upRes.headers);
          upRes.on("data", (chunk) => {
            resChunks.push(chunk);
            streamedBytes += chunk.length;
            res.write(chunk);
          });
          upRes.on("end", () => {
            clearInterval(hbTimer);
            const resBuf = Buffer.concat(resChunks);
            const durMs = Number(process.hrtime.bigint() - startHr) / 1e6;
            let reqJson = null;
            try {
              reqJson = JSON.parse(reqBuf.toString("utf8"));
            } catch {
              reqJson = { _raw_text: reqBuf.toString("utf8") };
            }
            const resJson = parseSseOrJsonResponse(resBuf, upRes.headers["content-type"]);
            const record = {
              wire_seq: curSeq,
              started_at: startedAt,
              completed_at: new Date().toISOString(),
              duration_ms: Math.round(durMs * 100) / 100,
              method: req.method,
              url: req.url,
              status_code: upRes.statusCode || 200,
              request_bytes: reqBuf.length,
              response_bytes: resBuf.length,
              api_call_id: resJson && typeof resJson === "object" && resJson.id ? String(resJson.id) : null,
              model: reqJson && typeof reqJson === "object" && reqJson.model ? String(reqJson.model) : null,
              request: reqJson,
              response: resJson,
            };
            fs.appendFileSync(wireLogPath, JSON.stringify(record) + "\n", "utf8");
            res.end();
          });
        }
      );
      upReq.on("error", (err) => {
        res.statusCode = 502;
        res.end(JSON.stringify({ error: String(err) }));
      });
      upReq.end(reqBuf);
    });
  });

  await new Promise((resolve) => proxy.listen(0, "127.0.0.1", resolve));
  const proxyPort = proxy.address().port;

  // ==========================================================================
  // Section 4: Isolated Workspace & COPILOT_HOME Materialization
  // Links or copies `.github/`, `.venv/`, `copilot-instructions.md`, `research/`,
  // seeded `specs/`, and `.pipeline_config.json` without mutating the source repo.
  // ==========================================================================
  const wsGithub = path.join(wsDir, ".github");
  if (!fs.existsSync(wsGithub)) {
    if (opts.modelOverride) {
      fs.cpSync(path.join(opts.repoRoot, ".github"), wsGithub, { recursive: true });
    } else {
      fs.symlinkSync(path.join(opts.repoRoot, ".github"), wsGithub);
    }
  }
  applyWorkspaceModelOverrideIfCopied(wsDir, opts.modelOverride);

  const wsVenv = path.join(wsDir, ".venv");
  if (!fs.existsSync(wsVenv) && fs.existsSync(path.join(opts.repoRoot, ".venv"))) {
    fs.symlinkSync(path.join(opts.repoRoot, ".venv"), wsVenv);
  }

  const repoCi = path.join(opts.repoRoot, ".github", "copilot-instructions.md");
  const chomeCi = path.join(copilotHome, "copilot-instructions.md");
  if (fs.existsSync(repoCi) && !fs.existsSync(chomeCi)) {
    fs.copyFileSync(repoCi, chomeCi);
  }

  if (!fs.existsSync(path.join(wsDir, ".git"))) {
    execFileSync("git", ["init", "-q"], { cwd: wsDir });
  }

  if (!opts.noExamples && !fs.existsSync(path.join(wsDir, "examples")) && fs.existsSync(path.join(opts.repoRoot, "examples"))) {
    fs.cpSync(path.join(opts.repoRoot, "examples"), path.join(wsDir, "examples"), { recursive: true });
  }

  if (opts.researchFiles.length > 0) {
    const wsResearch = path.join(wsDir, "research");
    fs.mkdirSync(wsResearch, { recursive: true });
    for (const rf of opts.researchFiles) {
      if (!fs.existsSync(rf)) {
        throw new Error(`Research file does not exist: ${rf}`);
      }
      fs.copyFileSync(rf, path.join(wsResearch, path.basename(rf)));
    }
  }

  const wsSpecs = path.join(wsDir, "specs");
  fs.mkdirSync(wsSpecs, { recursive: true });
  if (fs.existsSync(path.join(opts.repoRoot, "specs", "README.md")) && !fs.existsSync(path.join(wsSpecs, "README.md"))) {
    fs.copyFileSync(path.join(opts.repoRoot, "specs", "README.md"), path.join(wsSpecs, "README.md"));
  }
  const initialSeededSlugs = new Set();
  if (opts.seedSpecsDir && fs.existsSync(opts.seedSpecsDir)) {
    for (const ent of fs.readdirSync(opts.seedSpecsDir, { withFileTypes: true })) {
      if (ent.isDirectory()) {
        initialSeededSlugs.add(ent.name);
        const dstS = path.join(wsSpecs, ent.name);
        if (!fs.existsSync(dstS)) {
          fs.cpSync(path.join(opts.seedSpecsDir, ent.name), dstS, { recursive: true });
        }
      }
    }
  }

  const cfgPath = path.join(wsDir, ".pipeline_config.json");
  if (fs.existsSync(cfgPath)) {
    if (opts.allowWebResearch !== null || opts.experimentsJson) {
      const existingCfg = JSON.parse(fs.readFileSync(cfgPath, "utf8"));
      if (opts.allowWebResearch !== null) {
        existingCfg.allow_web_research = opts.allowWebResearch;
      }
      if (opts.experimentsJson) {
        existingCfg.experiments = { ...(existingCfg.experiments || {}), ...JSON.parse(opts.experimentsJson) };
      }
      fs.writeFileSync(cfgPath, JSON.stringify(existingCfg, null, 2) + "\n", "utf8");
    }
  } else if (opts.governanceMode === "autonomous-batch" || opts.allowWebResearch !== null || opts.experimentsJson) {
    const tplPath = path.join(opts.repoRoot, "templates", "governance-modes", "autonomous-batch.pipeline_config.json");
    const cfgObj =
      opts.governanceMode === "autonomous-batch" && fs.existsSync(tplPath)
        ? JSON.parse(fs.readFileSync(tplPath, "utf8"))
        : { governance_mode: opts.governanceMode === "interactive" ? "strict-4-gate" : opts.governanceMode };
    if (opts.allowWebResearch !== null) {
      cfgObj.allow_web_research = opts.allowWebResearch;
    }
    if (opts.experimentsJson) {
      cfgObj.experiments = { ...(cfgObj.experiments || {}), ...JSON.parse(opts.experimentsJson) };
    }
    fs.writeFileSync(cfgPath, JSON.stringify(cfgObj, null, 2) + "\n", "utf8");
  }

  const modelCatalog = [
    {
      id: "gemma4:e4b",
      provider: "ollama-models",
      capabilities: {
        supports: { vision: true, toolCalls: true, reasoningEffort: true },
        limits: { max_context_window_tokens: 131072, max_prompt_tokens: 114688, max_output_tokens: 16384 },
      },
    },
    {
      id: "gemma4:26b-a4b-it-q8_0",
      provider: "ollama-models",
      capabilities: {
        supports: { vision: true, toolCalls: true, reasoningEffort: true },
        limits: { max_context_window_tokens: 131072, max_prompt_tokens: 114688, max_output_tokens: 16384 },
      },
    },
  ];
  if (opts.modelOverride) {
    const cleanOv = opts.modelOverride.replace(/^ollama-models\//, "");
    if (!modelCatalog.some((m) => m.id === cleanOv)) {
      modelCatalog.push({
        id: cleanOv,
        provider: "ollama-models",
        capabilities: {
          supports: { vision: true, toolCalls: true, reasoningEffort: true },
          limits: { max_context_window_tokens: 131072, max_prompt_tokens: 114688, max_output_tokens: 16384 },
        },
      });
    }
  }

  // ==========================================================================
  // Section 5: CopilotClient Session Loop, Checkpoint Auto-Approval & Harvesting
  // Boots `copilot-runtime` over stdio, drives interactive `/approve` gates or
  // `autonomous-batch` runs to Stage 5 archival, and exports `run_meta.json`.
  // ==========================================================================
  const sdk = await import(pathToFileURL(sdkJs).href);
  const childEnv = {
    ...process.env,
    COPILOT_HOME: copilotHome,
    OLLAMA_HOST: `http://127.0.0.1:${proxyPort}`,
  };
  const client = new sdk.CopilotClient({
    connection: sdk.RuntimeConnection.forStdio({
      path: runtimeBin,
      env: childEnv,
    }),
  });

  let sessionId = "";
  let runtimeVersion = "unknown";
  const turnSummaries = [];
  let archivedComplete = false;
  let errorDetail = null;

  try {
    await client.start();
    const runtimeStatus = await client.getStatus();
    runtimeVersion = String(runtimeStatus?.version || "unknown");

    const sessionOpts = {
      workingDirectory: wsDir,
      trustLevel: "session",
      agentDirectories: [".github/agents"],
      skillDirectories: [".github/skills"],
      instructionDirectories: [".github"],
      enableFileHooks: true,
      enableSkills: true,
      model: "ollama-models/gemma4:e4b",
      agent: "Facet",
      providers: [
        {
          name: "ollama-models",
          type: "openai",
          wireApi: "responses",
          baseUrl: `http://127.0.0.1:${proxyPort}/v1`,
          apiKey: "ollama",
        },
      ],
      models: modelCatalog,
      onPermissionRequest: sdk.approveAll,
    };
    if (opts.reasoningEffort) {
      sessionOpts.reasoningEffort = opts.reasoningEffort;
    }

    const session = await client.createSession(sessionOpts);
    sessionId = session.sessionId;
    const liveSrcEvents = path.join(copilotHome, "session-state", sessionId, "events.jsonl");
    const liveDstEvents = path.join(rawDir, "events.jsonl");
    const pyBin = fs.existsSync(path.join(opts.repoRoot, ".venv", "bin", "python"))
      ? path.join(opts.repoRoot, ".venv", "bin", "python")
      : "python3";
    const analyzeScript = path.join(opts.repoRoot, "scripts", "analyze_trace.py");

    const syncLiveAuditSnapshot = () => {
      try {
        if (fs.existsSync(liveSrcEvents) && path.resolve(liveSrcEvents) !== path.resolve(liveDstEvents)) {
          fs.copyFileSync(liveSrcEvents, liveDstEvents);
        }
        if (opts.outDir && fs.existsSync(liveDstEvents) && fs.existsSync(analyzeScript)) {
          execFileSync(pyBin, [analyzeScript, "--session", liveDstEvents, "--audit-bundle", opts.outDir], {
            cwd: opts.repoRoot,
            stdio: "ignore",
          });
        }
      } catch {
        // non-fatal incremental snapshot
      }
    };

    if (typeof session.on === "function") {
      session.on((ev) => {
        if (!ev || typeof ev !== "object") return;
        const tsShort = new Date().toISOString().slice(11, 19);
        const d = ev.data || {};
        if (ev.type === "subagent.started") {
          const role = d.agentDisplayName || d.agentName || "Subagent";
          process.stderr.write(`[copilot-sdk ${tsShort}]   -> @${role} started (${d.model || ""})\n`);
          if (fs.existsSync(liveSrcEvents) && path.resolve(liveSrcEvents) !== path.resolve(liveDstEvents)) {
            try { fs.copyFileSync(liveSrcEvents, liveDstEvents); } catch {}
          }
        } else if (ev.type === "tool.execution_start" && d.toolName && d.toolName !== "task") {
          const argHint = d.arguments?.path ? path.basename(String(d.arguments.path)) : (d.arguments?.command ? String(d.arguments.command).slice(0, 42) : "");
          process.stderr.write(`[copilot-sdk ${tsShort}]      tool:${d.toolName}(${argHint})\n`);
        }
      });
    }

    process.stderr.write(`[copilot-sdk] Session started: ${sessionId} (runtime ${runtimeVersion}, mode=${opts.governanceMode}, logs=${rawDir})\n`);

    const readFsm = () => {
      const p = path.join(wsDir, ".pipeline_state.json");
      if (!fs.existsSync(p)) return {};
      try {
        return JSON.parse(fs.readFileSync(p, "utf8"));
      } catch {
        return {};
      }
    };

    const hasSealedSpecsOnDisk = () => {
      if (!fs.existsSync(wsSpecs)) return false;
      for (const ent of fs.readdirSync(wsSpecs, { withFileTypes: true })) {
        if (ent.isDirectory() && !initialSeededSlugs.has(ent.name) && fs.existsSync(path.join(wsSpecs, ent.name, "PROVENANCE.json"))) {
          return true;
        }
      }
      return false;
    };

    process.stderr.write(`[copilot-sdk] Turn 1 -> Initial user prompt\n`);
    const r1 = await session.sendAndWait({ prompt: opts.prompt }, opts.turnTimeoutMs);
    const fsm1 = readFsm();
    const reply1 = String(r1?.data?.content || "");
    turnSummaries.push({
      turn_index: 1,
      prompt: opts.prompt,
      fsm_stage_after: fsm1.current_stage || null,
      reply_chars: reply1.length,
      reply_content: reply1,
    });
    syncLiveAuditSnapshot();
    if ((fsm1.current_stage === "PRE_RUN_HALT" && fsm1.post_archive_halt_latch) || (opts.governanceMode === "autonomous-batch" && hasSealedSpecsOnDisk() && !String(fsm1.current_stage || "").startsWith("CHECKPOINT_"))) {
      archivedComplete = true;
    }

    for (let step = 1; !archivedComplete && step <= opts.maxApproves && (opts.maxTurns <= 0 || turnSummaries.length < opts.maxTurns); step++) {
      const curFsm = readFsm();
      const curStage = String(curFsm.current_stage || "");
      if (!curStage.startsWith("CHECKPOINT_") || curStage === "CHECKPOINT_REPAIR_ESCALATION") {
        break;
      }
      process.stderr.write(`[copilot-sdk] Turn ${step + 1} -> /approve (from ${curStage})\n`);
      const rNext = await session.sendAndWait({ prompt: "/approve" }, opts.turnTimeoutMs);
      const afterFsm = readFsm();
      const replyNext = String(rNext?.data?.content || "");
      turnSummaries.push({
        turn_index: step + 1,
        prompt: "/approve",
        fsm_stage_before: curStage,
        fsm_stage_after: afterFsm.current_stage || null,
        reply_chars: replyNext.length,
        reply_content: replyNext,
      });
      syncLiveAuditSnapshot();
      if (afterFsm.current_stage === "PRE_RUN_HALT" && afterFsm.post_archive_halt_latch) {
        archivedComplete = true;
        break;
      }
    }

    await session.disconnect();
  } catch (err) {
    errorDetail = String(err && err.stack ? err.stack : err);
    process.stderr.write(`[copilot-sdk] Error: ${errorDetail}\n`);
  } finally {
    try {
      await client.stop();
    } catch {
      // ignore shutdown error
    }
    proxy.close();
    if (caffeinateProc) {
      try {
        caffeinateProc.kill();
      } catch {
        // ignore cleanup error
      }
    }
  }

  const srcEvents = sessionId ? path.join(copilotHome, "session-state", sessionId, "events.jsonl") : "";
  const dstEvents = path.join(rawDir, "events.jsonl");
  if (srcEvents && fs.existsSync(srcEvents) && path.resolve(srcEvents) !== path.resolve(dstEvents)) {
    fs.copyFileSync(srcEvents, dstEvents);
  }

  const archivedSpecs = [];
  if (fs.existsSync(wsSpecs)) {
    for (const entry of fs.readdirSync(wsSpecs, { withFileTypes: true })) {
      if (entry.isDirectory()) {
        const subFiles = fs.readdirSync(path.join(wsSpecs, entry.name)).sort();
        archivedSpecs.push({ slug: entry.name, files: subFiles });
        if (opts.outDir) {
          const dstSpecCopy = path.join(rawDir, "archived_specs", entry.name);
          fs.mkdirSync(dstSpecCopy, { recursive: true });
          fs.cpSync(path.join(wsSpecs, entry.name), dstSpecCopy, { recursive: true });
        }
      }
    }
  }

  const meta = {
    session_id: sessionId,
    runtime_version: runtimeVersion,
    governance_mode: opts.governanceMode,
    workspace_dir: wsDir,
    copilot_home: copilotHome,
    events_path: dstEvents,
    wire_path: wireLogPath,
    archived_complete: archivedComplete,
    wire_calls_total: wireSeq,
    turns: turnSummaries,
    archived_specs: archivedSpecs,
    error: errorDetail,
  };
  fs.writeFileSync(path.join(rawDir, "run_meta.json"), JSON.stringify(meta, null, 2) + "\n", "utf8");
  if (tmpRoot && !opts.keepWorkspace && !opts.workspaceDir) {
    fs.rmSync(tmpRoot, { recursive: true, force: true });
  }

  if (errorDetail || !fs.existsSync(dstEvents)) {
    console.error(JSON.stringify(meta));
    process.exit(1);
  }
  console.log(JSON.stringify(meta));
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
