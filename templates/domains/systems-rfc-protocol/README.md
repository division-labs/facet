# Domain Preset: Systems & RFC Protocol Engineering

## Pipeline Focus
Translating IETF RFCs, wire formats, distributed consensus protocols, and state machines into verified protocol parsers, serializers, and state engines.

## Key Configurations
* **Manifest (`manifest.json`):**
  - `domain_category`: `"Cryptographic & Network Protocol Specification"`
  - `verification_contract.equivalence_mode`: `"state_trace_equivalence"` or `"exact_equality"`
  - `formal_specifications[*].representation_format`: `"state_transition"` or `"bnf_grammar"`
* **Mapping (`mapping.json`):**
  - Type mappings: `bytes`, `int64`, dataclasses, state enums
  - Predicate types:
    - `anchor`: Standard RFC test vectors (e.g. RFC-8446 Appendix A test transcripts)
    - `relational_law`: Round-trip serialization invariant `decode(encode(msg)) == msg`
    - `postcondition`: Rejection of malformed/truncated packets without panics
* **Toolchain & Libraries:**
  - Isolated Python 3.11+ environment (`./.venv/bin/python`) with customizable `verification_globs` in `.pipeline_config.json`
