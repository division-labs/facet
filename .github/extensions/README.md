# Facet Workspace Copilot Extensions

This directory hosts repository-scoped Copilot SDK extensions (`<extension-name>/extension.mjs`) loaded natively by Visual Studio Code and `copilot-runtime` without external npm build steps or `package.json` manifests.

---

## Shipped Extension: `facet-lens/` (**Facet Lens** Canvas)

[`facet-lens/`](./facet-lens/) registers the **`facet-pipeline-lens`** Copilot Canvas panel — an interactive, zero-LLM-token side-panel workbench for inspecting live Facet equations, typed Python interfaces, 4-predicate Cleanroom verification obligations, and RFC 6962 binary Merkle provenance seals.

### Module Architecture (`facet-lens/`)

| File | Responsibility |
| :--- | :--- |
| [`extension.json`](./facet-lens/extension.json) | Dormant-activation manifest (`"version": 1`, `"initialization": "module"`) declaring `facet-pipeline-lens` and its `128x128` 8-bit RGBA icon ([`assets/facet-prism.png`](./facet-lens/assets/facet-prism.png)). |
| [`declaration.mjs`](./facet-lens/declaration.mjs) | Single-source `FACET_LENS_DECLARATION` (`id`, `title`, `description`, `inputSchema`, and agent actions `focus_stage`, `select_spec`, `refresh`), coupled 1:1 with [`extension.json`](./facet-lens/extension.json). |
| [`extension.mjs`](./facet-lens/extension.mjs) | Copilot SDK entrypoint (`joinSession` + `createCanvas` from `@github/copilot-sdk/extension`) managing idempotent per-`instanceId` panel lifecycles and diagnostic logging via `session.log()`. |
| [`server.mjs`](./facet-lens/server.mjs) | Ephemeral-port `127.0.0.1` loopback HTTP + Server-Sent Events (`/api/events`) server with fast `realpathSync` workspace digest polling, `/api/snapshot` JSON hydration, and an `/internal/notify` push endpoint triggered by [`pipeline_guards.py`](../hooks/scripts/pipeline_guards.py) in `<80 ms`. |
| [`state.mjs`](./facet-lens/state.mjs) | Read-only workspace and `specs/<concept_slug>/` artifact loader plus atomic (`tmp` + `rename`) durable UI state persistence under `$COPILOT_HOME/extension-data/facet-pipeline-lens/` keyed by workspace identity. |
| [`ui.mjs`](./facet-lens/ui.mjs) | Semantic, keyboard-accessible 5-tab client UI (`Traceability`, `Formulation`, `Proofs`, `Implementation`, `Archive`) featuring KaTeX mathematical rendering and browser-side WebCrypto SHA-256 verification of composite seals, RFC 6962 §2.1 binary Merkle roots (`0x00` leaf / `0x01` internal node separation), and `upstream_merkle_roots` DAG links. |
| [`assets/`](./facet-lens/assets/) | Vector source ([`facet-prism.svg`](./facet-lens/assets/facet-prism.svg)) and `128x128` RGBA PNG icon ([`facet-prism.png`](./facet-lens/assets/facet-prism.png)). |

---

## Scope, Security & Roadmap Notes

1. **Strictly Workspace-Scoped:** [`scripts/install_user_level.py`](../../scripts/install_user_level.py) intentionally keeps `.github/extensions/` scoped to the Facet workspace rather than copying it into `~/.copilot/extensions/`.
2. **Zero Privileged Bridge or `stdout` Pollution:** All Canvas servers bind exclusively to `127.0.0.1` on ephemeral ports, never embed credentials in URLs, escape untrusted strings before DOM insertion, and log exclusively through `session.log()` (never `console.log`).
3. **Validation:** [`scripts/validate_system.py`](../../scripts/validate_system.py) validates syntax (`node --check`), PNG header dimensions, `extension.json` <-> `declaration.mjs` coupled contracts, `Function Map:` / `Section N:` banners, and live HTTP + SSE snapshot round-trips.
4. **Future Evolution (`Mosaic` Compositional Workbench):** Planned compound port-graph clustering, ForceAtlas2 convex-hull layout, and multi-layer `Convex Face <-> Single Point Group` composition for `Facet Lens` are specified in [`ROADMAP.md`](../../ROADMAP.md).
