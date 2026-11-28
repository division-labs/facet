#!/bin/bash
#
# Compile Ollama Modelfiles for Facet via HTTP POST /api/create with explicit stop=[].
#
# Gemma 4 uses built-in special tokens (`<|channel>thought`, `<channel|>`,
# `<|tool_call>`) to separate internal reasoning from tool calls, parsed by
# Ollama's native `PARSER gemma4`. Calling `/api/create` directly with
# `"stop": []` clears any inherited custom stop strings so generation is never
# cut off mid-thought before a tool call is emitted.

set -euo pipefail

# Locate this script's directory and normalize the Ollama HTTP endpoint URL.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OLLAMA_ENDPOINT="${OLLAMA_HOST:-http://localhost:11434}"
if [[ "${OLLAMA_ENDPOINT}" != http://* && "${OLLAMA_ENDPOINT}" != https://* ]]; then
  OLLAMA_ENDPOINT="http://${OLLAMA_ENDPOINT}"
fi

#######################################
# Compile one Ollama Modelfile via HTTP /api/create with stop=[].
# Globals:
#   OLLAMA_ENDPOINT
# Arguments:
#   $1: Target Ollama model tag (e.g. gemma4:26b-a4b-it-q8_0).
#   $2: Source Modelfile path on disk.
# Outputs:
#   Writes errors to stderr if a forbidden stop parameter remains.
# Returns:
#   0 on success; exits non-zero on validation or API failure.
#######################################
compile_clean_stop_model() {
  local tag="$1"
  local mfile="$2"
  python3 - "${OLLAMA_ENDPOINT}" "${tag}" "${mfile}" <<'PY'
import json
import re
import sys
import urllib.request
from pathlib import Path

endpoint, tag, mfile_str = sys.argv[1].rstrip("/"), sys.argv[2], sys.argv[3]
text = Path(mfile_str).read_text(encoding="utf-8")

# Extract the base model (`FROM ...`), `SYSTEM """..."""` prompt, and typed
# `PARAMETER` lines from the Modelfile, then POST directly to `/api/create`
# with `"stop": []` so no inherited stop sequences survive on the compiled tag.
from_match = re.search(r"^FROM\s+(\S+)", text, flags=re.MULTILINE)
if not from_match:
    raise SystemExit(f"Missing FROM directive in {mfile_str}")
from_model = from_match.group(1)

sys_match = re.search(r'SYSTEM\s+"""(.*?)"""', text, flags=re.DOTALL)
system_prompt = sys_match.group(1).strip() if sys_match else ""

params = {"stop": []}
for m in re.finditer(r"^PARAMETER\s+(\S+)\s+(.+)$", text, flags=re.MULTILINE):
    key = m.group(1).strip()
    raw_val = m.group(2).strip().strip('"')
    if key == "stop":
        raise SystemExit(f"Forbidden PARAMETER stop found in {mfile_str}")
    # Convert integer and float parameter strings into native JSON numbers for `/api/create`.
    if re.fullmatch(r"-?\d+", raw_val):
        params[key] = int(raw_val)
    elif re.fullmatch(r"-?\d+\.\d+", raw_val):
        params[key] = float(raw_val)
    else:
        params[key] = raw_val

payload = {
    "model": tag,
    "from": from_model,
    "system": system_prompt,
    "parameters": params,
    "stream": False,
}
req = urllib.request.Request(
    f"{endpoint}/api/create",
    data=json.dumps(payload).encode("utf-8"),
    headers={"Content-Type": "application/json"},
    method="POST",
)
with urllib.request.urlopen(req, timeout=300) as resp:
    body = json.loads(resp.read().decode("utf-8"))
    if body.get("status") != "success":
        raise SystemExit(f"Unexpected /api/create response for {tag}: {body}")
PY

  if ollama show "${tag}" --modelfile | grep -q "^PARAMETER stop "; then
    echo "[ERROR] Compiled model ${tag} still contains PARAMETER stop lines!" >&2
    exit 1
  fi
}

echo "=== Building Facet Clean-Stop Models in Ollama (${OLLAMA_ENDPOINT}) ==="

for mf_cand in "${SCRIPT_DIR}"/Modelfile.*; do
  if [[ -f "${mf_cand}" ]] && grep -qE "^PARAMETER[[:space:]]+stop([[:space:]]|$)" "${mf_cand}"; then
    echo "Forbidden PARAMETER stop found in ${mf_cand}" >&2
    exit 1
  fi
done

if [[ "${1:-}" == "--role-tags" ]]; then
  echo "1/6: Compiling gemma4:26b-formalist (@Formalist)..."
  compile_clean_stop_model "gemma4:26b-formalist" \
    "${SCRIPT_DIR}/Modelfile.formalist"

  echo "2/6: Compiling gemma4:26b-engineer (@Engineer & @Auditor)..."
  compile_clean_stop_model "gemma4:26b-engineer" \
    "${SCRIPT_DIR}/Modelfile.engineer-auditor"

  echo "3/6: Compiling gemma4:26b-researcher (@Researcher)..."
  compile_clean_stop_model "gemma4:26b-researcher" \
    "${SCRIPT_DIR}/Modelfile.researcher"

  echo "4/6: Compiling gemma4:e4b-facet (@Facet Hypervisor)..."
  compile_clean_stop_model "gemma4:e4b-facet" \
    "${SCRIPT_DIR}/Modelfile.facet"

  echo "5/6: Compiling gemma4:e4b-orchestrator (@Orchestrator)..."
  compile_clean_stop_model "gemma4:e4b-orchestrator" \
    "${SCRIPT_DIR}/Modelfile.orchestrator"

  echo "6/6: Compiling gemma4:e4b-scribe (@Scribe Vision OCR)..."
  compile_clean_stop_model "gemma4:e4b-scribe" \
    "${SCRIPT_DIR}/Modelfile.scribe"

  echo "[OK] Compiled 6 clean-stop role tags (128K ctx, stop=[])."
else
  echo "1/2: Compiling gemma4:26b-a4b-it-q8_0 (@Researcher, @Formalist, @Engineer & @Auditor)..."
  compile_clean_stop_model "gemma4:26b-a4b-it-q8_0" \
    "${SCRIPT_DIR}/Modelfile.engineer-auditor"

  echo "2/2: Compiling gemma4:e4b (@Facet, @Orchestrator & @Scribe via Modelfile.e4b)..."
  compile_clean_stop_model "gemma4:e4b" \
    "${SCRIPT_DIR}/Modelfile.e4b"

  if ollama list 2>/dev/null | awk 'NR>1 {print $1}' | grep -q "^pplx-decider-flash"; then
    echo "[OK] Verified System-1 boundary decision model: pplx-decider-flash (keep_alive=0 under OLLAMA_MAX_LOADED_MODELS=1)."
  else
    echo "[INFO] Optional System-1 boundary decision model 'pplx-decider-flash' not yet pulled (run: ollama pull pplx-decider-flash)."
  fi

  echo "[OK] Compiled 2 clean-stop Gemma 4 base tags (128K ctx, stop=[])."
fi
