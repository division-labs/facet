# Facet Reference: Hooks and Guards

This document specifies the deterministic hook architecture implemented in `pipeline_guards.py` and registered via `pipeline-guards.json` across GitHub Copilot in Visual Studio Code.

---

## 1. Supported Hook Events and Lifecycle Handlers

| Event (camelCase) | Registered Action | Official Output Consumed | Facet Purpose |
| :--- | :--- | :--- | :--- |
| **`sessionStart`** | `session-start` | `additionalContext` | Probes Ollama connectivity and lists archived specifications in `specs/`. |
| **`preToolUse`** | `pre-tool` / `coordinator-guard` / `hypervisor-guard` | `permissionDecision`, `permissionDecisionReason`, `modifiedArgs` | Enforces Coordinator worker-tool & `skill` denial, `STAGE_SPECIALIST_ALLOWLIST` `task` routing, canonical `build_canonical_specialist_task_prompt()` + `<caller_interface_contract>` + advisory `<edit_scope_hint>` `modifiedArgs["prompt"]` rewriting, Vision/PDF Modality Firewall, `@Scribe` plate-view gate, Create-Once / Scoped-Edit (`always_scoped_once_created`) sandboxes + 4-stage `old_str` auto-snap, hard `AST-3` rational-pole sign-clip denial, missing-`path` inference, and cryptographic write-locks. |
| **`postToolUse`** | `validate-edit` | `modifiedResult`, `additionalContext` | Sanitizes `web_fetch` UTF-8 bytes, scrubs Markdown emojis, promotes backslashed Python docstrings to raw `r"""`, formats Gemma `<\|"\|>` JSON to strict RFC 8259, enforces TEI P5 provenance, `AST-1` (`formal_predicate` Python `eval` AST + `O(N^3)` precondition ban), `AST-2` (lexical-scope unbound symbols), `AST-4` (`mandated_target_module`, exact positional parameter names, & `verify_*.py` entrypoint coverage), anchor-scoped anti-leakage rules, `LOCAL_CRITIC.json`, and `pytest` execution proof before `VERIFICATION.md`, notifies `Facet Lens` over loopback SSE, and preserves drafts on disk with line-numbered remediation hints. |
| **`postToolUseFailure`** | `post-failure` | `additionalContext` | Tracks consecutive tool failure counters, restores staged `.rollback_<name>` backups on failed `create`, and injects diagnostic recovery advice. |
| **`agentStop`** *(agent-scoped on `@Facet`)* | `facet-stop` | `decision`, `reason` | Blocks `@Facet` from ending a turn after Stage 5 until it has viewed `.pipeline_state.json` and `PROVENANCE.json`, the archive seals verify (`[HYPERVISOR HALT - ARCHIVAL VERIFICATION FAILED]` otherwise), and a fenced `@Facet ...` next-cycle prompt is present; enforces checkpoint gate fidelity and blocks emojis in final responses. |
| **`agentStop`** *(agent-scoped on `@Orchestrator`)* | `orchestrator-stop` | `decision`, `reason` | Reconciles mid-turn `"delivery": "steering"` `/approve`, `/reset`, and `/add-source` commands from `transcriptPath` (root-origin events only), and blocks premature archival/completion claims when `.pipeline_state.json` still awaits human `/approve` sign-off. |
| **`preCompact`** | `pre-compact` | None (notification-only) | Observational notification. Returns `{}`. Persistent state survives via disk files and instructions. |
| **`userPromptSubmitted`** | `user-prompt` | None (stdout dropped for command hooks) | Enforces root-origin provenance (ignoring subagent-origin payloads), clears stale `active_agent` / per-turn viewed paths, purges `pending_state_cleanup` descendants from prior archived cycles, executes `/approve`, `/reset`, and `/add-source` updates, and records one-shot archival / reset receipts. Returns `{}`. |
| **`userPromptTransformed`** *(agent-scoped on `@Facet`)* | `facet-prompt` | `modifiedTransformedPrompt` | Creates a signed genesis `PRE_RUN_HALT` state on the first cycle, replaces non-`/approve` checkpoint turns with a pre-rendered `<checkpoint_reprompt>` block, injects `<recovery_notice>` when `last_incident` is present, clears `post_archive_halt_latch` on a fresh user goal when seals verify, and injects `<hypervisor_context>`. |
| **`userPromptTransformed`** | `user-prompt-transformed` / `orchestrator-prompt` | `modifiedTransformedPrompt` | Yields `{}` for non-`Orchestrator` subagent payloads so checkpoint text never leaks to workers; routes `@Facet`-prefixed prompts to `facet-prompt` when picker selection was dropped; replaces non-`/approve` root checkpoint prompts with `<checkpoint_reprompt>`; and injects `<archival_complete>`, `<reset_complete>`, `<source_registry_update>`, and `<stage_anchor>`. |
| **`subagentStart`** | `subagent-start` | `additionalContext` | Keys active subagent state by child `agentId` (`parent_session_id`), computes incremental AST dependency-closure hashes (`.pipeline_scratch/ast_closure_cache.json`) during Stage 4 repair cycles, and injects `<subagent_context>` (`SOURCE_OCR.md` hashes, approved research domains or `allow_web_research=false` closed-book rules, `AUDIT_FEEDBACK.json` spec-rewind context, parent hashes, Cleanroom rules). |
| **`subagentStop`** | `subagent-stop` | `decision`, `reason`, `modifiedResponse` | Verifies designated stage deliverables exist on disk (`decision: "block"` when missing), cleans `ocr_staging/`, scrubs channel tokens & uncreated test references, seals `CHECKPOINT_*` states, renders `<checkpoint_briefing>`, snapshots `hook_state_snapshot.json` on autonomous-batch archival, and injects `<directive>` / `<hypervisor_directive>`. |
| **`sessionEnd`** | `session-end` | None (notification-only) | Observational notification on clean session shutdown (absent on hung/abandoned chats). Returns `{}`. |
| **`errorOccurred`** | `error-occurred` | None (notification-only) | Clears stale `active_agent` on crashed child and parent entries, logs stack-free error messages to `.pipeline_errors.log`, classifies incidents (`quota_exceeded`, `empty_response_422`, `context_overflow`, `other`), and seals `last_incident` into `.pipeline_state.json`. Returns `{}`. |
| **`permissionRequest`** | `permission-request` | `behavior: "allow" \| "deny"` | Auto-approves safe read-only tools, `web_fetch` calls matching the 3-layer approved research domain registry, and Cleanroom-validated workspace `.venv` / skill shell commands (`not requestSandboxBypass`); returns `{}` on unlisted URLs or arbitrary shell commands so VS Code prompts interactively. |

> **Official Copilot Exit-Code, Timeout & Measured Latency Semantics:** Command `preToolUse` hooks are **fail-closed** on non-zero exit codes (including exit `2` and crashes), denying the tool call—whereas **command-hook timeouts (`timeoutSec`) are always fail-open** across all events (`preToolUse`: `15 s`, `postToolUse`: `10 s`, inline and other events: `5 s`). On an Apple Silicon M5 Pro (64 GB), `pipeline_guards.py` measures a median **`58 ms`** standalone `python3` startup (`~0.2 ms` handler logic, invariant to `hooks_state.json` size) and **`151 ms`** median (`166 ms` max) end-to-end `preToolUse` duration in the Copilot session log (`133 ms` `userPromptSubmitted`, `130 ms` `userPromptTransformed`, `225 ms` `sessionStart`), staying two orders of magnitude below the `5–15 s` fail-open timeout thresholds.

---

## 2. Scope-Aware Precedence and Dual-Hook Idempotency

When Facet is installed at both the workspace level (`.github/hooks/`) and user level (`~/.copilot/hooks/`), Copilot dispatches both hooks for every event. Facet resolves this in two layers:

### Layer A: Scope-Aware Precedence Shadowing
- Workspace hooks pass `--scope workspace`.
- User hooks pass `--scope user`.
- When `pipeline_guards.py` executes with `--scope user`, it inspects `<cwd>/.github/hooks/pipeline-guards.json`. If an active workspace hook exists (and `disableAllHooks` is not true), the user hook immediately yields (`{}`) in less than 1 millisecond.

### Layer B: Secondary Atomic POSIX Event Lock
For concurrent calls or single-source dispatches:
- A SHA-256 event fingerprint is derived from:
  $$\text{hash} = \text{SHA-256}(\text{sessionId} + \text{action} + \text{timestamp} + \text{toolName} + \text{agentName})$$
- The hook attempts an atomic file creation:
  `os.open("~/.copilot/session-state/hook_locks/<hash>.lock", os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)`
- If the file exists (`FileExistsError`), another instance has already claimed the event, and the process exits immediately with `{}`. Stale locks (>15s) are purged automatically.

### Layer C: Two-Layer State Concurrency Locking & Atomic Unique Temp Files
Parallel tool calls (e.g., batched `view` reads) run concurrent hook processes. `pipeline_guards.py` prevents torn reads and lost updates across both same-session and cross-session races using two `fcntl.flock` layers (degrading gracefully to unlocked on non-POSIX hosts, with a `1.5 s` per-lock timeout that logs to `stderr` and never writes to JSON `stdout`):
1. **Striped Handler Serialization:** `invocation_state_locks()` acquires an exclusive `flock` on a 64-slot session stripe (`$COPILOT_HOME/session-state/state_locks/sess_00..63.flock`) strictly before a 64-slot workspace stripe (`ws_00..63.flock`) for the ~`0.2 ms` handler duration (`state_locks/` is separate from `hook_locks/` so 15-second event-lock cleanup never unlinks active `flock` inodes).
2. **Global Merge-Write Lock:** `save_session_state()`, `snapshot_session_tree_state()`, and `purge_session_state_entries()` acquire `$COPILOT_HOME/session-state/state_locks/hooks_state_global.flock`, re-read `hooks_state.json`, update only the target session keys, and commit via `atomic_write_text()` using unique `<pid>.<time_ns>.tmp` files + `os.replace()` (also used for `.pipeline_state.json`, Shadow Seal Ledgers, `PROVENANCE.json`, and config writes).

---

## 3. Capability Confinement and Security Guards

`preToolUse` enforces strict least-privilege boundaries across both file-editing tools (`edit`, `create`, `str_replace_editor`, `apply_patch`) and shell execution (`execute`, `bash`, `powershell`, `Bash`):

### 00. Hypervisor Read-Only Confinement, Handoff Briefs & Checkpoint Front Gates (`hypervisor-guard`, `facet-prompt`, & `facet-stop`)
`@Facet` declares the 7-category ceiling `tools: ['read', 'edit', 'execute', 'search', 'agent', 'web', 'todo']` solely so Depth-1 `@Orchestrator` and Depth-2 specialists (`@Scribe`, `@Researcher`, `@Formalist`, `@Engineer`, `@Auditor`) inherit `bash`, `create`, `edit`, `grep`, `glob`, and `web_fetch` under Copilot SDK `1.0.108+` transitive parent-to-child tool intersection (setting `['read', 'agent']` at Depth 0 intersects `@Scribe` down to a single `view` tool). Depth-0 routing ignores stale `active_agent` state (`is_depth0_session`), denies direct depth-0 worker launches (`[HYPERVISOR INGRESS REQUIRED]`), and enforces root-only provenance on `/approve`, `/reset`, and `/add-source`. Agent-scoped hooks in `facet.agent.md` enforce:
- `hypervisor-guard` (`preToolUse`): allows only seal/provenance `view` calls (`.pipeline_state.json`, `specs/*/PROVENANCE.json`, and configured `upstream_seals`) and synchronous `task(agent_type="Orchestrator", mode="sync", ...)` (denying `glob`, `grep`, or workspace-folder `view` calls with the exact corrected `@Orchestrator` handoff call so `@Orchestrator` and `@Scribe` perform all folder/image discovery). At any `CHECKPOINT_*` stage (`checkpoint_handoff_gate`), `task`, `agent`, `write_agent`, and `send_message` are denied (`read_agent` remains allowed) and workers are restricted to read-only tools. Once pre-flight seals verify (`state_seal_sha256`, approved-checkpoint hashes, `upstream_seals`, and tamper-evident Shadow Seal Ledger parity), `hypervisor-guard` enforces `agent_type="Orchestrator"` and `mode="sync"` strictly and denies a first non-canonical prompt once with the exact 4-line (`Goal:`, `Context:`, `Allowed:`, `Expected:`) + `<user_request>` call so `@Facet` copies it cleanly (allowing a same-turn retry with `Orchestrator` + `sync` to prevent deny loops, and clearing `last_incident` upon dispatch).
- `facet-prompt` & `user-prompt-transformed`: handle `/status` (and fuzzy typos like `/stauts`) with an instant zero-tool `<status_pulse>` readout, replace non-`/approve` checkpoint inputs up front with a split-plane `<checkpoint_reprompt>` block (instant zero-tool hint on `/appove` or `approve/` `difflib` typos, or safe read-only Checkpoint Q&A on substantive questions about the pending deliverable), pre-render the exact `@Orchestrator` handoff call with a `STAGE_SPINNER_LABELS` `description` (plus Cycle-2 `specs/<prev_slug>/` context when referenced) inside `<hypervisor_context>` Step 2, inject `<recovery_notice>` after recorded `errorOccurred` incidents, and render split-plane `<checkpoint_briefing>` blocks (`26B-A4B` specialist `latest_stage_briefing`, stage-tailored visual previews, and a 3-line `Stage X of 4` + `Next:` Gate Card carrying the full 64-character `compute_file_sha256()` digest and `Ask a question about this stage, reply /approve to continue, or /reset to start over.`) on checkpoint stops. Prompts and hook-injected blocks are **never capped or truncated**; to monitor context utilization against `num_ctx` (`131072`), inspect `inputTokens` in `session.usage_record` entries inside `~/.copilot/session-state/<session-id>/events.jsonl`.
- `facet-stop` (`agentStop`): allows zero-tool `<status_pulse>` replies, blocks premature conversational exits when an active user request has not yet dispatched `@Orchestrator`, blocks internal agent/plumbing narration stubs at checkpoints, verifies post-archive `PROVENANCE.json` reads/seals, Topic-and-Result Stage 5 synthesis (blocking pipeline process tables), `### Where to Take This Next` narrative lead-in before the fenced `@Facet ...` prompt, and checkpoint `/approve` or `/reset` call-to-action fidelity, and blocks emojis in final `@Facet` messages. Read-only Canvas tools (`list_canvas_capabilities`, `open_canvas`, `invoke_canvas_action`) are permitted via `BENIGN_TOOLS`.

### Opt-In Redacted Hook Trace (`FACET_HOOK_TRACE=1`)
Setting `FACET_HOOK_TRACE=1` appends one redacted JSON line per hook invocation (including yielded user-scope calls) to `$COPILOT_HOME/session-state/hook_trace.jsonl` containing only `{ts, action, scope, sessionId, agentId, agentType, parentAgentId, toolName, mode, targetAgent, decision, outputKeys}`—never prompt text, file contents, or `stdout`.

### 0. Coordinator Privilege Separation (`coordinator-guard`, `orchestrator-prompt`, & `orchestrator-stop`)
`@Orchestrator` declares all 7 VS Code frontmatter tool categories (`read`, `edit`, `execute`, `search`, `agent`, `web`, `todo`) so spawned child subagents inherit `create`, `edit`, `bash`, and `web_fetch` in Copilot SDK `1.0.108+`. Agent-scoped inline hooks in `orchestrator.agent.md` invoke:
- `coordinator-guard` (`matcher: "create|edit|str_replace_editor|apply_patch|bash|read_bash|stop_bash|list_bash|powershell|web_fetch|skill|rename_chat"`), deterministically blocking `@Orchestrator` from directly mutating files, running shell commands, fetching URLs, loading worker skills, or invoking unavailable client tools (`rename_chat`);
- `orchestrator-prompt` (`userPromptTransformed`) to strip unreachable `rename_chat` `<system_reminder>` blocks and inject Turn-0 `<stage_anchor>` routing strictly on `@Orchestrator` without affecting general or `[[PLAN]]` chats; and
- `orchestrator-stop` (`agentStop`) to reconcile mid-turn `"delivery": "steering"` `/approve`, `/reset`, and `/add-source` commands from `transcriptPath` and block premature archival claims when `.pipeline_state.json` still awaits human sign-off.

### 1. Vision & PDF Modality Firewall and `@Scribe` OCR-Only Confinement
- `@Researcher`, `@Formalist`, `@Engineer`, and `@Auditor` are deterministically blocked from calling `view`/`read_file` on `.png`, `.jpg`, `.jpeg`, `.webp`, `.gif`, `.bmp`, `.tiff`, and `.pdf` assets (`[MODALITY FIREWALL]`).
- `@Scribe` is confined strictly to Stage 0V: `bash` permits only single invocations of `prepare_visual_sources.py` (no `;`, `&&`, `||`, `|`, `>`), file mutations permit only atomic `create` on `SOURCE_OCR.md`, and delegating to `Scribe` in `STAGE_2_MAPPING`+ is blocked.

### 2. Control-Plane Immutability, Create-Once / Scoped-Edit Sandboxes, `AST-1..4` Gates & 4-Stage `edit` Auto-Snap
- Subagents (`@Researcher`, `@Scribe`, `@Formalist`, `@Engineer`, `@Auditor`) are strictly barred from modifying `.pipeline_config.json`, `.pipeline_state.json`, `approved_sources.json`, `pipeline-guards.json`, `pipeline_guards.py`, or `.pipeline_scratch/*`.
- Deliverables follow **Create-Once, Scoped-Edit Thereafter** (`[SCOPED EDIT REQUIRED — PRESERVE EXISTING ARTIFACT]`): new specification and implementation files are written once via `create` (with `.rollback_<filename>` backups staged automatically), and all local validation or Stage 4 Reflexion updates preserve existing files on disk and apply minimal targeted changes via `edit` (with 4-stage `resolve_edit_pair()` whitespace/indent/JSON/AST auto-snapping) or `code_patch.py` (`replace-symbol`, `replace-lines`, `replace-json`).
- Four deterministic AST verification gates guard artifact quality: **`AST-1`** (`formal_predicate` Python `eval` AST compilation + $O(N^3)$ decomposition ban in `precondition_assertions`), **`AST-2`** (`find_lexical_unbound_names` lexical scope check across functions, closures, comprehensions, and `except` handlers), **`AST-3`** (`detect_rational_pole_sign_clip` blocking silent one-sided rational denominator bailouts in `preToolUse`), and **`AST-4`** (caller `mandated_target_module` parity, exact positional parameter names, and `verify_*.py` entrypoint call coverage).
- At stage-transition boundaries (`subagentStop` / Stage 0V pre-check), `pplx-decider-flash` (`/v1/systemone`, `"keep_alive": 0` under `OLLAMA_MAX_LOADED_MODELS=1`) performs `<1 s` semantic spec screening (`"system1_s1_s2"`, $\tau=0.85$), Stage-4 diagnosis-vs-label override, and `Method L3` (`Line-ID Choice` + AST/JSON hunk mass pooling + Companion `Noul`) localization to populate advisory `<edit_scope_hint>` windows, degrading gracefully to `"off"` if offline or disabled.

### 3. Cryptographic Write-Locks on Approved Specifications
- Once Checkpoint 1 is approved (`/approve`), `manifest.json` and `SOURCE_OCR.md` are write-locked against modification.
- Once Checkpoint 2 is approved (`/approve`), `mapping.json` is write-locked against modification.

### 4. Role-Scoped Capability Confinement
- `@Researcher` writes only `RESEARCH.md` (Stage 0) and `manifest.json` (Stage 1).
- `@Formalist` writes only `mapping.json` (Stage 2).
- `@Engineer` modifies only `requirements.txt` (`create`/`edit`, PEP-508 validated) and production source code (`*.py`), barred from specification/report files and verification oracle scripts (`verify_*.py`, `test_*.py`).
- `@Auditor` writes/edits only verification test files (`verify_*.py`, `test_*.py`) and writes `VERIFICATION.md` or `AUDIT_FEEDBACK.json` via `create`.

### 5. Shell & `code_patch.py` Cleanroom Confinement
Shell commands executed by subagents are inspected (`@Researcher` and `@Formalist` are denied shell access entirely; `@Scribe` is restricted to single `prepare_visual_sources.py` calls). Symmetrical Cleanroom guards block `@Engineer` from mutating verification suites (`verify_*`, `test_*`) or specifications via shell operators (`>`, `tee`, `sed -i`, `rm`, `mv`, `cp`) or `code_patch.py`, and block `@Auditor` from mutating production modules or `requirements.txt` via shell or `code_patch.py`.

---

## 4. Reflexion Bounded Self-Repair & Target-Aware Spec Rewind Loop

`postToolUse` on `AUDIT_FEEDBACK.json` and `subagentStop` on `@Auditor` manage target-aware repair and upstream specification rewinds:
1. If `AUDIT_FEEDBACK.json` reports `"overall_status": "FAILED"` (or `"status": "FAILED"` / `"FAIL"`), the hook increments `fsm["repair_attempts"]` and appends a structured entry to `fsm["repair_history"]` while **preserving `AUDIT_FEEDBACK.json` on disk** (archived to `specs/<slug>/AUDIT_HISTORY.json` at Stage 5).
2. If `repair_attempts < max_repair_attempts` (default: 3), `apply_audit_rewind_transition()` routes by `failure_classification`:
   - **`IMPLEMENTATION_DEFECT` / `DOMAIN_BOUND_LIMITATION`:** Transitions FSM to `STAGE_3_IMPLEMENTATION` (`@Engineer` surgical code repair with `repair_history` credit ledger).
   - **`MAPPING_DEFECT` / `SPECIFICATION_AMBIGUITY`:** Unseals `checkpoint_2` while **preserving all workspace artifacts on disk** (`mapping.json`, `requirements.txt`, `*.py`, `verify_*.py`, and `AUDIT_FEEDBACK.json`), transitions FSM to `STAGE_2_MAPPING` (`@Formalist` applies a scoped `edit` to `mapping.json` with `<original_user_request>` + `<audit_rewind_feedback>` injected via `modifiedArgs["prompt"]`), and resumes the deterministic lifecycle forward.
   - **`MANIFEST_DEFECT`:** Unseals `checkpoint_1` and `checkpoint_2` while **preserving all workspace artifacts on disk**, transitions FSM to `STAGE_1_SPECIFICATION` (`@Researcher` applies a scoped `edit` to `manifest.json`), and resumes the deterministic lifecycle forward.
   - `VERIFICATION.md` requires a recorded passing `pytest` run matching current suite/module SHA-256 digests, blocks any `OBL-j: FAIL/FAILED` or `CONDITIONAL_PASS`, and records `## Specification & Repair History` whenever prior repairs or spec rewinds occurred.
3. If `repair_attempts >= max_repair_attempts`:
   - The **Reflexion Circuit Breaker** trips (`CHECKPOINT_REPAIR_ESCALATION`) and halts for human oversight.

---

## 5. CLI Dispatcher Actions

`pipeline_guards.py` exposes 22 CLI dispatcher actions (`--scope workspace|user`):
- **Lifecycle Hooks:** `session-start`, `session-end`, `pre-compact`, `error-occurred`, `user-prompt`, `user-prompt-transformed`, `orchestrator-prompt`, `orchestrator-stop`, `hypervisor-guard`, `facet-prompt`, `facet-stop`, `pre-tool`, `coordinator-guard`, `validate-edit`, `post-failure`, `subagent-start`, `subagent-stop`, `permission-request`.
- **Archive Integrity & Maintenance (`garden-specs`):** Audits all `specs/*/PROVENANCE.json` composite seals, per-artifact SHA-256 hashes, PEP 508 `requirements.txt` files, and `upstream_modules` DAG links (`echo '{}' | python3 .github/hooks/scripts/pipeline_guards.py garden-specs`).
- **Manual Admin Reset (`reset` / `clean` / `archive`):**

```bash
# Snapshot working files to specs/<slug>_interrupted_<timestamp>/ and reset workspace root
python3 .github/hooks/scripts/pipeline_guards.py reset

# Validate syntax and contract of an edited file
python3 .github/hooks/scripts/pipeline_guards.py validate-edit < payload.json
```
