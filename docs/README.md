# Facet Documentation

Welcome to the documentation for **Facet**, the specification-driven engineering and cleanroom verification harness for Google Gemma 4 in Visual Studio Code.

## Documentation Structure

Facet follows the [Diataxis](https://diataxis.fr/) documentation framework, separating documentation into tutorials, how-to guides, reference material, and technical architecture:

### Guides and Explanations
- [Installation Guide](installation.md): Step-by-step setup for Ollama, Gemma 4 Modelfiles, repository workspace templates, and user-level (`~/.copilot/`) installation.
- [Usage and Workflow Guide](usage-and-workflow.md): End-to-end execution walkthrough covering Stage 0V (Vision/PDF OCR) and Stage 0 (Ideation) through Stage 5 (Archival), including checkpoint commands, `/add-source`, bounded self-repair, and troubleshooting.
- [Customization and Reparameterization Guide](customization.md): Instructions for adapting Facet to other domains (RFC protocols, ML kernels, verified APIs), hardware targets, governance modes, and programming languages.
- [System Architecture](architecture.md): Deep architectural and mathematical specification covering the 6-layer harness, cleanroom isolation, two-tier local + global 4-predicate verification, Vision/PDF Modality Firewall, artifact-preserving scoped-edit sandboxes, and cryptographic Merkle sealing.

### Technical Reference
- [Schemas and Contracts](reference/schemas-and-contracts.md): Complete specifications for `manifest.json`, `mapping.json`, `AUDIT_FEEDBACK.json`, `.pipeline_config.json`, `lint_rules.json`, and `SOURCE_OCR.md` TEI P5 provenance.
- [Hooks and Guards](reference/hooks-and-guards.md): Comprehensive reference for `pipeline_guards.py`, hook event bindings, capability confinement, write-locks, and CLI actions.

## Quick Links
- Repository Root: [README.md](../README.md)
- Changelog: [CHANGELOG.md](../CHANGELOG.md)
- Benchmark Suite (`SciCode` Canonical Harness): [benchmarks/README.md](../benchmarks/README.md)
- Workspace Canvas Extension (`Facet Lens`): [extension.mjs](../.github/extensions/facet-lens/extension.mjs)
- Reference Examples: [examples/README.md](../examples/README.md) ([magical_mystery/](../examples/magical_mystery/) and [mandelbrot_set/](../examples/mandelbrot_set/README.md))
- Specification Archive Directory: [specs/README.md](../specs/README.md)
- Ollama Blueprints: [modelfiles/README.md](../modelfiles/README.md)
- Domain and Hardware Templates: [templates/](../templates/)
