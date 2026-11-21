#!/usr/bin/env python3
"""Canonical SciCode (scicode-bench/SciCode) 3-arm local Ollama benchmark harness.

Compares Facet's specification-and-cleanroom pipeline against direct prompting
on the exact same local Ollama model using the unmodified NeurIPS 2024 ``SciCode``
benchmark (``scicode-bench/SciCode``):
  1. ``raw-zeroshot``    — Single-turn prompt -> Python function on ``gemma4:26b-a4b-it-q8_0``.
  2. ``raw-self-refine`` — Zero-shot + local syntax/import/signature smoke check + self-fix.
  3. ``facet-pipeline``  — Full closed-book ``@Facet`` pipeline (``S1 -> S2 -> S3 -> S4 -> S5``)
                           with ``AST-1..AST-4`` gates, boundary ``pplx-decider-flash``
                           screening (``keep_alive=0``), Cleanroom 4-predicate ``pytest``
                           verification, and live ``Facet Lens`` workspace streaming via
                           ``<run_dir>/live_lens_workspace``.

Protocol Fidelity Guarantees:
  - Uses the verbatim official ``SciCode`` prompt templates (``DEFAULT_PROMPT_TEMPLATE``
    and ``BACKGROUND_PROMPT_TEMPLATE``).
  - Enforces strict causal step ordering: step ``k`` sees ONLY steps ``1..k-1`` and the
    arm's own previously generated code—never future steps, gold solutions, or ``test_data.h5``.
  - Scores generated modules against the unmodified 1.0 GB ``benchmarks/.cache/test_data.h5``
    archive using ``ScicodeEvaluator`` and ``scicode.compare.cmp``.
"""
from __future__ import annotations

import argparse
import ast
from dataclasses import asdict, dataclass
import datetime
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request

import numpy as np
import scipy.sparse
from sympy import Symbol

# Guard optional step so unexpected runtime errors degrade safely instead of crashing.
try:
    import h5py
except ImportError:  # pragma: no cover - lazy fallback if h5py not yet installed
    h5py = None  # type: ignore[assignment]

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CACHE_DIR = REPO_ROOT / "benchmarks" / ".cache"
DEFAULT_RUNS_DIR = REPO_ROOT / "benchmarks" / "runs"

SCICODE_SPLIT_URLS = {
    "validation": "https://huggingface.co/datasets/SciCode1/SciCode/resolve/main/problems_dev.jsonl",
    "test": "https://huggingface.co/datasets/SciCode1/SciCode/resolve/main/problems_test.jsonl",
}
SCICODE_H5_GDRIVE_URL = (
    "https://drive.usercontent.google.com/download?"
    "id=17G_k65N_6yFFZ2O-jQH00Lh6iaw3z-AW&export=download&confirm=t"
)
ALL_ARMS = ("raw-zeroshot", "raw-self-refine", "facet-pipeline")
QUICK_VALIDATION_IDS = ("1", "4", "19")

# Verbatim official prompt templates from scicode-bench/SciCode/eval/data/
DEFAULT_PROMPT_TEMPLATE = """PROBLEM DESCRIPTION:
You will be provided with the main description of the problem, previous steps, and the next step. Your task will be to generate the disciplinary knowledge necessary for solving the next step and then develop a Python solution focused on this step.

PREVIOUS STEPS DESCRIPTION:
{problem_steps_str}

NEXT STEP - PROBLEM DESCRIPTION AND FUNCTION HEADER:
This part will describe the next step in the problem-solving process. First, provide the necessary scientific background knowledge as a comment at the beginning of your response, starting with 'Background: '. Then, a function header will be provided, and your task is to develop the Python code for this next step based on the provided description and function header.

{next_step_str}

DEPENDENCIES:
Use only the following dependencies in your solution. Do not include these dependencies at the beginning of your code.
{dependencies}

RESPONSE GUIDELINES:
1. Start with the scientific background required for the next step, formatted as a comment.
2. Then write the complete and executable Python program for the next step in a single block.
3. Your response should focus exclusively on implementing the solution for the next step, adhering closely to the specified function header and the context provided by the initial steps.
4. DO NOT include previous function code, example usage or test code in your response.
5. Ensure your response is in the format of ```python``` and includes the necessary background as a comment at the top.

Example:
```python
# Background: [Here, insert the necessary scientific knowledge required for the next step.]

[Insert the Python code here based on the provided function header and dependencies.]
```
"""

BACKGROUND_PROMPT_TEMPLATE = """PROBLEM DESCRIPTION:
You will be provided with problem steps along with background knowledge necessary for solving the problem. Your task will be to develop a Python solution focused on the next step of the problem-solving process.

PROBLEM STEPS AND FUNCTION CODE:
Here, you'll find the Python code for the initial steps of the problem-solving process. This code is integral to building the solution.

{problem_steps_str}

NEXT STEP - PROBLEM STEP AND FUNCTION HEADER:
This part will describe the next step in the problem-solving process. A function header will be provided, and your task is to develop the Python code for this next step based on the provided description and function header.

{next_step_str}

DEPENDENCIES:
Use only the following dependencies in your solution. Do not include these dependencies at the beginning of your code.

{dependencies}

RESPONSE GUIDELINES:
Now, based on the instructions and information provided above, write the complete and executable Python program for the next step in a single block.
Your response should focus exclusively on implementing the solution for the next step, adhering closely to the specified function header and the context provided by the initial steps.
Your response should NOT include the dependencies and functions of all previous steps. If your next step function calls functions from previous steps, please make sure it uses the headers provided without modification.
DO NOT generate EXAMPLE USAGE OR TEST CODE in your response. Please make sure your response python code in format of ```python```.
"""

# Verbatim fixed step snippets from scicode-bench/SciCode/eval/data/{13.6,62.1,76.3}.txt
FIXED_TEST_STEPS: dict[tuple[str, int], str] = {
    ("13", 5): (
        "class Maxwell:\n"
        "    def __init__(self, n_grid, x_out):\n"
        "        self.n_grid = n_grid\n"
        "        self.n_vars = 7\n"
        "        self.delta = float(x_out) / (n_grid - 2.0)\n"
        "        self.x = np.linspace(-self.delta*0.5, x_out + 0.5*self.delta, self.n_grid)[:,None,None]\n"
        "        self.y = np.linspace(-self.delta*0.5, x_out + 0.5*self.delta, self.n_grid)[None,:,None]\n"
        "        self.z = np.linspace(-self.delta*0.5, x_out + 0.5*self.delta, self.n_grid)[None,None,:]\n"
        "        self.r = np.sqrt(self.x**2+self.y**2+self.z**2)\n"
        "        self.E_x = zeros((n_grid, n_grid, n_grid))\n"
        "        self.E_y = zeros((n_grid, n_grid, n_grid))\n"
        "        self.E_z = zeros((n_grid, n_grid, n_grid))\n"
        "        self.A_x = zeros((n_grid, n_grid, n_grid))\n"
        "        self.A_y = zeros((n_grid, n_grid, n_grid))\n"
        "        self.A_z = zeros((n_grid, n_grid, n_grid))\n"
        "        self.phi = zeros((n_grid, n_grid, n_grid))\n"
        "        self.constraint = zeros((n_grid, n_grid, n_grid))\n"
        "        self.t = 0.0\n"
    ),
    ("62", 0): (
        "class Block:\n"
        "    def __init__(self, length, basis_size, operator_dict):\n"
        "        self.length = length\n"
        "        self.basis_size = basis_size\n"
        "        self.operator_dict = operator_dict\n"
        "class EnlargedBlock:\n"
        "    def __init__(self, length, basis_size, operator_dict):\n"
        "        self.length = length\n"
        "        self.basis_size = basis_size\n"
        "        self.operator_dict = operator_dict\n"
    ),
    ("76", 2): (
        "def generate_dna(N: int, PWM: dict) -> tuple:\n"
        "    p = random.randint(0, N-1)\n"
        "    nucleotide = 'ACGT'\n"
        "    uni_weights = [0.25,0.25,0.25,0.25]\n"
        "    dna_string = ''.join(random.choices(nucleotide, uni_weights, k=N))\n"
        "    spike_mat = load_motif_from_df(PWM)\n"
        "    spiked_seq = ''.join(random.choices(nucleotide, weights=[PWM[nuc][i] for nuc in nucleotide], k=1)[0] for i in range(len(PWM['A'])))\n"
        "    complement = {'A':'T', 'T':'A', 'C':'G', 'G':'C'}\n"
        "    reversed_seq = dna_string[::-1]\n"
        "    reverse_complement = ''.join(complement[nuc] for nuc in reversed_seq if nuc in complement)\n"
        "    new_seq = dna_string[:p] + spiked_seq + dna_string[p:]\n"
        "    new_seq_rc = reverse_complement[:N-p] + spiked_seq + reverse_complement[N-p:]\n"
        "    return p, new_seq, new_seq_rc\n"
    ),
}

AGENT_FILE_MAP = {
    "Facet": "facet.agent.md",
    "Orchestrator": "orchestrator.agent.md",
    "Scribe": "scribe.agent.md",
    "Researcher": "researcher.agent.md",
    "Formalist": "formalist.agent.md",
    "Engineer": "engineer.agent.md",
    "Auditor": "auditor.agent.md",
}

CATEGORY_TO_TOOLS: dict[str, set[str]] = {
    "read": {"view"},
    "edit": {"create", "edit"},
    "execute": {"bash"},
    "search": {"grep", "glob"},
    "web": {"web_fetch"},
    "todo": {"sql"},
    "agent": {"task"},
}


@dataclass
class ArmProblemResult:
    r"""Single ``(problem_id, arm)`` canonical SciCode outcome row."""

    problem_id: str
    problem_name: str
    arm: str
    model: str
    main_passed: bool
    substeps_passed: int
    substeps_total: int
    wall_seconds: float
    prompt_tokens: int
    output_tokens: int
    repair_attempts: int
    auditor_passed: bool
    error: str = ""
    eval_tokens_per_sec: float = 0.0
    spec_rewinds: int = 0


# ============================================================================
# 1. Verbatim Canonical SciCode Parsing, HDF5 Reader & Prompting Assistant
# ============================================================================


def _python_interpreter() -> str:
    venv_py = REPO_ROOT / ".venv" / "bin" / "python"
    return str(venv_py) if venv_py.is_file() else sys.executable


def is_skipped_scicode_step(problem_id: str, step_idx: int) -> bool:
    r"""Official skipped substeps in scicode-bench/SciCode (13.6, 62.1, 76.3)."""
    pid = str(problem_id)
    return (pid, step_idx) in FIXED_TEST_STEPS


def extract_function_name(function_header: str) -> str:
    r"""Canonical ``scicode.parse.parse.extract_function_name``."""
    match = re.search(r"\bdef\s+(\w+)\s*\(", function_header)
    if match:
        return match.group(1)
    match = re.search(r"\bclass\s+(\w+)\s*[\(:]", function_header)
    if match:
        return match.group(1)
    raise ValueError(f"Function name or class name not found in: {function_header!r}")


def get_function_from_code(code_string: str | None, function_name: str) -> str | None:
    r"""Canonical ``scicode.parse.parse.get_function_from_code``."""
    if code_string is None:
        return None
    # Parse Python source into an AST, recovering cleanly if syntax is invalid.
    try:
        # Parse Python source code into an `ast.AST` syntax tree for structural inspection.
        tree = ast.parse(code_string)
        for node in ast.walk(tree):
            # Handle syntax-tree nodes matching `isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name...`.
            if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name == function_name:
                return ast.unparse(node)
    except Exception:
        return code_string
    return code_string


def extract_python_script(response: str) -> str:
    r"""Canonical ``scicode.gen.models.extract_python_script``."""
    if "```" in response:
        python_script = (
            response.split("```python")[1].split("```")[0]
            if "```python" in response
            else response.split("```")[1].split("```")[0]
        )
    else:
        python_script = response
    python_script = re.sub(
        r"^\s*(import .*|from .*\s+import\s+.*)", "", python_script, flags=re.MULTILINE
    )
    return python_script


def generate_dummy_response(prompt: str, **_kwargs: object) -> str:
    r"""Canonical ``scicode.gen.models.generate_dummy_response``."""
    del prompt
    return "Blah blah\n```python\nprint('Hello, World!')\n```\n"


def generate_gold_response(prob_data: dict[str, object], num_steps: int) -> str:
    r"""Canonical ``eval/inspect_ai/scicode.py::generate_gold_response``."""
    sub_steps: list[dict[str, object]] = list(prob_data["sub_steps"])  # type: ignore[arg-type]
    return f"Blah blah\n```python\n{sub_steps[num_steps - 1]['ground_truth_code']}\n```\n"


def _process_hdf5_list(group: h5py.Group) -> list[object]:
    return [group[key][()] for key in group.keys()]


def _process_hdf5_sparse_matrix(group: h5py.Group) -> object:
    data = group["data"][()]
    shape = tuple(group["shape"][()])
    if "row" in group and "col" in group:
        return scipy.sparse.coo_matrix((data, (group["row"][()], group["col"][()])), shape=shape)
    if "blocksize" in group:
        return scipy.sparse.bsr_matrix(
            (data, group["indices"][()], group["indptr"][()]),
            shape=shape,
            blocksize=tuple(group["blocksize"][()]),
        )
    return scipy.sparse.csr_matrix((data, group["indices"][()], group["indptr"][()]), shape=shape)


def _process_hdf5_dict(group: h5py.Group) -> dict[object, object]:
    out: dict[object, object] = {}
    # Step through `group.items()` (`(key, obj)`) to update or verify each item.
    for key, obj in group.items():
        # Update local state when `isinstance(obj, h5py.Group)`.
        if isinstance(obj, h5py.Group):
            out[key] = _process_hdf5_sparse_matrix(obj["sparse_matrix"])
        elif isinstance(obj[()], bytes):
            out[key] = obj[()].decode("utf-8", errors="strict")
        else:
            # Parse numeric value safely, falling back cleanly if the string is non-numeric.
            try:
                out[float(key)] = obj[()]
            except ValueError:
                out[key] = obj[()]
    return out


def _process_hdf5_datagroup(group: h5py.Group) -> object:
    # Step through `group.keys()` (`key`) to update or verify each item.
    for key in group.keys():
        if key == "list":
            return _process_hdf5_list(group[key])
        if key == "sparse_matrix":
            return _process_hdf5_sparse_matrix(group[key])
        return _process_hdf5_dict(group)
    return {}


def process_hdf5_to_tuple(step_id: str, test_num: int, h5py_file: str) -> list[object]:
    """Canonical ``scicode.parse.parse.process_hdf5_to_tuple``."""
    data_lst: list[object] = []
    with h5py.File(h5py_file, "r") as f:
        # Step through `range(test_num)` (`test_id`) to update or verify each item.
        for test_id in range(test_num):
            group_path = f"{step_id}/test{test_id + 1}"
            # Update local state when `isinstance(f[group_path], h5py.Group)`.
            if isinstance(f[group_path], h5py.Group):
                group = f[group_path]
                num_keys = list(group.keys())
                if len(num_keys) == 1:
                    subgroup = group[num_keys[0]]
                    # Update local state when `isinstance(subgroup, h5py.Dataset)`.
                    if isinstance(subgroup, h5py.Dataset):
                        val = subgroup[()]
                        data_lst.append(val.decode("utf-8", errors="strict") if isinstance(val, bytes) else val)
                    elif isinstance(subgroup, h5py.Group):
                        data_lst.append(_process_hdf5_datagroup(subgroup))
                else:
                    var_lst: list[object] = []
                    # Step through `group.keys()` (`key`) to update or verify each item.
                    for key in group.keys():
                        subgroup = group[key]
                        # Update local state when `isinstance(subgroup, h5py.Dataset)`.
                        if isinstance(subgroup, h5py.Dataset):
                            val = subgroup[()]
                            var_lst.append(val.decode("utf-8", errors="strict") if isinstance(val, bytes) else val)
                        elif isinstance(subgroup, h5py.Group):
                            var_lst.append(_process_hdf5_datagroup(subgroup))
                    data_lst.append(tuple(var_lst))
            else:
                raise FileNotFoundError(f"Path {group_path} not found in {h5py_file}.")
    return data_lst


def process_symbol_in_dict(d: dict[object, object]) -> dict[object, object]:
    """Canonical ``scicode.compare.cmp.process_symbol_in_dict``."""
    new_dict: dict[object, object] = {}
    for key, value in d.items():
        norm_val = str(value) if isinstance(value, Symbol) else value
        norm_key = str(key) if isinstance(key, Symbol) else key
        new_dict[norm_key] = norm_val
    return new_dict


def are_dicts_close(
    dict1: dict[object, object], dict2: dict[object, object], atol: float = 1e-8, rtol: float = 1e-5
) -> bool:
    """Canonical ``scicode.compare.cmp.are_dicts_close``."""
    d1 = process_symbol_in_dict(dict1)
    d2 = process_symbol_in_dict(dict2)
    # Return `False` immediately once `d1.keys() != d2.keys()`.
    if d1.keys() != d2.keys():
        return False
    for key in d1:
        v1, v2 = d1[key], d2[key]
        # Update local state when `isinstance(v1, (Symbol, str))`.
        if isinstance(v1, (Symbol, str)):
            # Return `False` immediately once `v1 != v2`.
            if v1 != v2:
                return False
        elif isinstance(
            v1,
            (
                scipy.sparse.csr_matrix,
                scipy.sparse.csc_matrix,
                scipy.sparse.bsr_matrix,
                scipy.sparse.coo_matrix,
            ),
        ):
            if not np.allclose(v1.toarray(), v2.toarray(), atol=atol, rtol=rtol):  # type: ignore[union-attr]
                return False
        else:
            # Clean up temporary file/descriptor on a best-effort basis.
            try:
                if not np.allclose(v1, v2, atol=atol, rtol=rtol):  # type: ignore[arg-type]
                    return False
            except ValueError:
                # Return `False` immediately once `v1 != v2`.
                if v1 != v2:
                    return False
    return True


def are_csc_matrix_close(matrix1: object, matrix2: object) -> bool:
    """Canonical ``scicode.compare.cmp.are_csc_matrix_close``."""
    return bool(np.allclose(matrix1.toarray(), matrix2.toarray()))  # type: ignore[attr-defined]


def cmp_tuple_or_list(var1: list[object] | tuple[object, ...], var2: list[object] | tuple[object, ...]) -> bool:
    """Canonical ``scicode.compare.cmp.cmp_tuple_or_list``."""
    if len(var1) != len(var2):
        return False
    # Step through `zip(var1, var2)` (`(v1, v2)`) to update or verify each item.
    for v1, v2 in zip(var1, var2):
        # Unpack fields once we confirm `isinstance(v1, dict) and isinstance(v2, dict)`.
        if isinstance(v1, dict) and isinstance(v2, dict):
            # Return `False` immediately once `not are_dicts_close(v1, v2)`.
            if not are_dicts_close(v1, v2):
                return False
        elif isinstance(v1, (scipy.sparse.csr_matrix, scipy.sparse.csc_matrix)):
            # Return `False` immediately once `not are_csc_matrix_close(v1, v2)`.
            if not are_csc_matrix_close(v1, v2):
                return False
        elif isinstance(v1, bool):
            # Return `False` immediately once `v1 != v2`.
            if v1 != v2:
                return False
        else:
            # Clean up temporary file/descriptor on a best-effort basis.
            try:
                if not np.allclose(v1, v2):  # type: ignore[arg-type]
                    return False
            except ValueError:
                # Return `False` immediately once `v1 != v2`.
                if v1 != v2:
                    return False
    return True


class ScicodePromptingAssistant:
    """Verbatim step-by-step prompting assistant from ``eval/inspect_ai/scicode.py``."""

    def __init__(self, output_dir: Path, prompt_dir: Path, with_background: bool) -> None:
        self.output_dir = Path(output_dir)
        self.prompt_dir = Path(prompt_dir)
        self.with_background = bool(with_background)
        self.previous_llm_code: list[str | None] = []

    def _get_background_dir(self) -> str:
        return "with_background" if self.with_background else "without_background"

    def generated_step_path(self, prob_id: str, num_steps: int) -> Path:
        return self.output_dir / self._get_background_dir() / f"{prob_id}.{num_steps}.py"

    def _raw_step_cache_path(self, prob_id: str, num_steps: int) -> Path:
        return self.output_dir / self._get_background_dir() / ".step_only" / f"{prob_id}.{num_steps}.py"

    def register_previous_response(
        self,
        prob_data: dict[str, object],
        response: str,
        previous_code: str,
        num_steps: int,
    ) -> None:
        extracted = extract_python_script(response)
        self.previous_llm_code[num_steps - 1] = extracted
        raw_p = self._raw_step_cache_path(str(prob_data["problem_id"]), num_steps)
        raw_p.parent.mkdir(parents=True, exist_ok=True)
        raw_p.write_text(extracted, encoding="utf-8")
        self.save_response_with_steps(prob_data, response, previous_code, num_steps)

    def save_response_with_steps(
        self,
        prob_data: dict[str, object],
        response: str,
        previous_code: str,
        num_steps: int,
    ) -> None:
        out_dir = self.output_dir / self._get_background_dir()
        out_dir.mkdir(parents=True, exist_ok=True)
        prob_id = str(prob_data["problem_id"])
        output_file_path = out_dir / f"{prob_id}.{num_steps}.py"
        python_code = extract_python_script(response)
        output_file_path.write_text(f"{previous_code}\n{python_code}", encoding="utf-8")

    @staticmethod
    def process_problem_code(prob_data: dict[str, object], num_steps: int) -> str:
        sub_steps: list[dict[str, object]] = list(prob_data["sub_steps"])  # type: ignore[arg-type]
        header_docstring = str(sub_steps[num_steps - 1]["function_header"])
        return_str = str(sub_steps[num_steps - 1]["return_line"])
        return f"{header_docstring}\n\n{return_str}"

    def process_problem_steps(
        self, problem_data: dict[str, object], num_steps: int
    ) -> tuple[str, str, str]:
        # Build the causal prompt context for step `num_steps` (`1..k`):
        # combine descriptions + this arm's own generated functions from steps `1..k-1`,
        # then append only the current step's problem description and function stub header.
        sub_steps: list[dict[str, object]] = list(problem_data["sub_steps"])  # type: ignore[arg-type]
        output_lines: list[str] = []
        next_step: list[str] = []
        previous_code: list[str] = []
        for i in range(num_steps - 1):
            desc_i = str(sub_steps[i]["step_description_prompt"])
            bg_i = str(sub_steps[i].get("step_background") or "")
            output_lines.append(f"{desc_i}\n{bg_i}" if self.with_background else desc_i)
            prev_c = self.previous_llm_code[i] or ""
            output_lines.append(prev_c)
            previous_code.append(prev_c)
            output_lines.append("------")

        cur_desc = str(sub_steps[num_steps - 1]["step_description_prompt"])
        cur_bg = str(sub_steps[num_steps - 1].get("step_background") or "")
        next_step.append(f"{cur_desc}\n{cur_bg}" if self.with_background else cur_desc)
        next_step.append(self.process_problem_code(problem_data, num_steps))
        output_str = "\n\n".join(output_lines[:-1])
        next_step_str = "\n\n".join(next_step)
        previous_code_str = "\n".join(previous_code)
        return output_str, next_step_str, previous_code_str

    def generate_prompt_with_steps(
        self,
        prob_data: dict[str, object],
        num_steps: int,
        prompt_template: str = DEFAULT_PROMPT_TEMPLATE,
    ) -> tuple[str, str]:
        problem_steps_str, next_step_str, previous_code_str = self.process_problem_steps(
            prob_data, num_steps
        )
        dependencies = str(prob_data["required_dependencies"])
        assert next_step_str
        return (
            prompt_template.format(
                problem_steps_str=problem_steps_str,
                next_step_str=next_step_str,
                dependencies=dependencies,
            ),
            f"{dependencies}\n{previous_code_str}\n",
        )

    def save_prompt_with_steps(
        self, prob_data: dict[str, object], prompt: str, num_steps: int
    ) -> None:
        out_dir = self.prompt_dir / self._get_background_dir()
        out_dir.mkdir(parents=True, exist_ok=True)
        out_file = out_dir / f"{prob_data['problem_id']}.{num_steps}.txt"
        out_file.write_text(prompt, encoding="utf-8")

    def prepare_final_prompt_with_steps(
        self,
        prob_data: dict[str, object],
        num_steps: int,
        tot_steps: int,
        prompt_template: str = DEFAULT_PROMPT_TEMPLATE,
        *,
        save: bool = True,
    ) -> tuple[str, str]:
        prob_id = str(prob_data["problem_id"])
        sub_steps: list[dict[str, object]] = list(prob_data["sub_steps"])  # type: ignore[arg-type]
        if num_steps == 1:
            self.previous_llm_code = [None] * tot_steps
        else:
            if len(self.previous_llm_code) != tot_steps:
                self.previous_llm_code = [None] * tot_steps
            # Step through `range(num_steps - 1)` (`prev_step`) to update or verify each item.
            for prev_step in range(num_steps - 1):
                if self.previous_llm_code[prev_step] is None:
                    if (prob_id, prev_step) in FIXED_TEST_STEPS:
                        fn_name = extract_function_name(str(sub_steps[prev_step]["function_header"]))
                        self.previous_llm_code[prev_step] = get_function_from_code(
                            FIXED_TEST_STEPS[(prob_id, prev_step)], fn_name
                        )
                    else:
                        raw_step_p = self._raw_step_cache_path(prob_id, prev_step + 1)
                        prev_file_p = self.generated_step_path(prob_id, prev_step + 1)
                        # Read and process the file on disk when `raw_step_p.is_file()`.
                        if raw_step_p.is_file():
                            self.previous_llm_code[prev_step] = raw_step_p.read_text(encoding="utf-8")
                        elif prev_file_p.is_file():
                            prev_content = prev_file_p.read_text(encoding="utf-8")
                            fn_name = extract_function_name(str(sub_steps[prev_step]["function_header"]))
                            self.previous_llm_code[prev_step] = get_function_from_code(prev_content, fn_name)
                        else:
                            raise RuntimeError(
                                f"Generating problem {prob_id} step {num_steps} ahead of step {prev_step + 1}."
                            )
        prompt, previous_code = self.generate_prompt_with_steps(prob_data, num_steps, prompt_template)
        if save:
            self.save_prompt_with_steps(prob_data, prompt, num_steps)
        return prompt, previous_code


def _write_scicode_runtime_package(pkg_root: Path) -> None:
    r"""Materialize minimal ``scicode.{parse.parse,compare.cmp}`` package for subprocess execution."""
    (pkg_root / "scicode" / "parse").mkdir(parents=True, exist_ok=True)
    (pkg_root / "scicode" / "compare").mkdir(parents=True, exist_ok=True)
    (pkg_root / "scicode" / "__init__.py").write_text("", encoding="utf-8")
    (pkg_root / "scicode" / "parse" / "__init__.py").write_text("", encoding="utf-8")
    (pkg_root / "scicode" / "compare" / "__init__.py").write_text("", encoding="utf-8")
    shim_parse = (
        "import importlib.util as _ilu, sys as _sys\n"
        f"_spec = _ilu.spec_from_file_location('_rb_mod', {str(Path(__file__).resolve())!r})\n"
        "_mod = _ilu.module_from_spec(_spec)\n"
        "_sys.modules['_rb_mod'] = _mod\n"
        "_spec.loader.exec_module(_mod)\n"
        "process_hdf5_to_tuple = _mod.process_hdf5_to_tuple\n"
    )
    shim_cmp = (
        "import sys as _sys, importlib.util as _ilu\n"
        "if '_rb_mod' not in _sys.modules:\n"
        f"    _spec = _ilu.spec_from_file_location('_rb_mod', {str(Path(__file__).resolve())!r})\n"
        "    _mod = _ilu.module_from_spec(_spec)\n"
        "    _sys.modules['_rb_mod'] = _mod\n"
        "    _spec.loader.exec_module(_mod)\n"
        "else:\n"
        "    _mod = _sys.modules['_rb_mod']\n"
        "are_dicts_close = _mod.are_dicts_close\n"
        "process_symbol_in_dict = _mod.process_symbol_in_dict\n"
        "are_csc_matrix_close = _mod.are_csc_matrix_close\n"
        "cmp_tuple_or_list = _mod.cmp_tuple_or_list\n"
    )
    (pkg_root / "scicode" / "parse" / "parse.py").write_text(shim_parse, encoding="utf-8")
    (pkg_root / "scicode" / "compare" / "cmp.py").write_text(shim_cmp, encoding="utf-8")


class ScicodeEvaluator:
    """Verbatim evaluator matching ``eval/inspect_ai/scicode.py::ScicodeEvaluator``."""

    def __init__(
        self,
        h5py_file: str,
        code_dir: Path,
        log_dir: Path,
        with_background: bool,
        step_timeout: int = 300,
    ) -> None:
        self.h5py_file = str(Path(h5py_file).resolve())
        self.code_dir = Path(code_dir)
        self.log_dir = Path(log_dir)
        self.with_background = bool(with_background)
        self.step_timeout = step_timeout

    def _get_background_dir(self) -> str:
        return "with_background" if self.with_background else "without_background"

    def test_code(
        self, prob_data: dict[str, object]
    ) -> tuple[int, int, int, dict[str, str]]:
        # Score all non-skipped substeps of a SciCode problem in an isolated temp folder:
        # concatenate generated code with SciCode's official `test_cases` assertions,
        # load target reference arrays/tuples from `test_data.h5`, and execute each step
        # inside a child Python subprocess with a strict timeout.
        code_dir = self.code_dir / "generated_code" / self._get_background_dir()
        sub_steps: list[dict[str, object]] = list(prob_data["sub_steps"])  # type: ignore[arg-type]
        problem_id = str(prob_data["problem_id"])

        with tempfile.TemporaryDirectory(prefix=f"scicode_eval_{problem_id}_") as tmp_s:
            tmp_dir = Path(tmp_s)
            shim_root = tmp_dir / "_scicode_pkg"
            _write_scicode_runtime_package(shim_root)

            scored_steps = 0
            # Step through `range(len(sub_steps))` (`idx`) to update or verify each item.
            for idx in range(len(sub_steps)):
                # Skip this iteration when `is_skipped_scicode_step(problem_id, idx)`.
                if is_skipped_scicode_step(problem_id, idx):
                    continue
                scored_steps += 1
                step_id = str(sub_steps[idx]["step_number"])
                code_file_path = code_dir / f"{prob_data['problem_id']}.{idx + 1}.py"
                # Read and process the file on disk when `not code_file_path.is_file()`.
                if not code_file_path.is_file():
                    code_file_path = code_dir / f"{step_id}.py"
                assert code_file_path.is_file(), f"Code file {code_file_path} not found."
                code_content = code_file_path.read_text(encoding="utf-8")
                test_lst: list[str] = list(sub_steps[idx]["test_cases"])  # type: ignore[arg-type]
                assert_file = tmp_dir / f"{step_id}.py"
                with assert_file.open("w", encoding="utf-8") as f:
                    f.write(code_content)
                    f.write("\n\nfrom scicode.parse.parse import process_hdf5_to_tuple\n\n")
                    f.write(
                        f"targets = process_hdf5_to_tuple({step_id!r}, {len(test_lst)}, {self.h5py_file!r})\n"
                    )
                    # Step through `range(len(test_lst))` (`i`) to update or verify each item.
                    for i in range(len(test_lst)):
                        f.write(f"target = targets[{i}]\n\n")
                        # Step through `test_lst[i].split('\n')` (`line`) to update or verify each item.
                        for line in test_lst[i].split("\n"):
                            f.write(line + "\n")

            env = dict(os.environ)
            old_pp = env.get("PYTHONPATH", "")
            env["PYTHONPATH"] = f"{shim_root}:{old_pp}" if old_pp else str(shim_root)

            total_correct = 0
            step_details: dict[str, str] = {}
            logs_dir = self.log_dir / "evaluation_logs" / self._get_background_dir()
            logs_dir.mkdir(parents=True, exist_ok=True)

            # Step through `range(len(sub_steps))` (`idx`) to update or verify each item.
            for idx in range(len(sub_steps)):
                # Skip this iteration when `is_skipped_scicode_step(problem_id, idx)`.
                if is_skipped_scicode_step(problem_id, idx):
                    continue
                step_id = str(sub_steps[idx]["step_number"])
                logs_file = logs_dir / f"{step_id}.log"
                # Read and process the file on disk when `logs_file.is_file()`.
                if logs_file.is_file():
                    lines = logs_file.read_text(encoding="utf-8").splitlines()
                    status_line = lines[0] if lines else "fail"
                    if status_line == "pass":
                        total_correct += 1
                    step_details[step_id] = "\n".join(lines)
                    continue

                script_path = tmp_dir / f"{step_id}.py"
                # Run external command with a timeout and handle non-zero exits or timeouts safely.
                try:
                    # Execute external command (`subprocess.run([_python_interpreter(), str(script_path)]...`).
                    subprocess.run(
                        [_python_interpreter(), str(script_path)],
                        cwd=str(tmp_dir),
                        env=env,
                        check=True,
                        capture_output=True,
                        text=True,
                        timeout=self.step_timeout,
                    )
                    logs_file.write_text("pass\n", encoding="utf-8")
                    total_correct += 1
                    step_details[step_id] = "pass"
                except subprocess.CalledProcessError as exc:
                    err_tail = ((exc.stderr or exc.stdout or "").strip().splitlines() or ["AssertionError"])[-1]
                    logs_file.write_text(f"fail\n{err_tail}\n", encoding="utf-8")
                    step_details[step_id] = f"fail: {err_tail}"
                except subprocess.TimeoutExpired:
                    logs_file.write_text("time out\n", encoding="utf-8")
                    step_details[step_id] = "time out"

        problem_correct = 1 if (scored_steps > 0 and total_correct == scored_steps) else 0
        return problem_correct, total_correct, scored_steps, step_details


# ============================================================================
# 2. Dataset & Official test_data.h5 Bootstrap
# ============================================================================


def load_scicode_split(
    split: str = "validation", cache_dir: Path = DEFAULT_CACHE_DIR
) -> tuple[list[dict[str, object]], str]:
    """Download (once) and parse ``validation`` (15) or ``test`` (65) SciCode JSONL."""
    if split not in SCICODE_SPLIT_URLS:
        raise ValueError(f"Unsupported split {split!r}; choose 'validation' or 'test'.")
    cache_dir.mkdir(parents=True, exist_ok=True)
    fname = "problems_dev.jsonl" if split == "validation" else "problems_test.jsonl"
    jsonl_path = cache_dir / fname
    # Read and process the file on disk when `not jsonl_path.is_file()`.
    if not jsonl_path.is_file():
        req = urllib.request.Request(
            SCICODE_SPLIT_URLS[split],
            headers={"User-Agent": "Facet-SciCode-Benchmark/1.0"},
        )
        # Open HTTP response stream and close the socket automatically when done.
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = resp.read()
        tmp_p = jsonl_path.with_suffix(".jsonl.tmp")
        tmp_p.write_bytes(data)
        tmp_p.replace(jsonl_path)

    raw_bytes = jsonl_path.read_bytes()
    # Compute deterministic SHA-256 digest for integrity verification.
    dataset_sha = hashlib.sha256(raw_bytes).hexdigest()
    problems = [
        json.loads(line)
        for line in raw_bytes.decode("utf-8").splitlines()
        if line.strip()
    ]
    return problems, dataset_sha


def ensure_scicode_h5(cache_dir: Path = DEFAULT_CACHE_DIR) -> Path:
    r"""Return path to official ``test_data.h5``, downloading from SciCode Drive if needed."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    h5_path = cache_dir / "test_data.h5"
    # Nothing to read on disk yet (`h5_path.is_file() and h5_path.stat().st_size > 100000000`), so return `h5_path` immediately.
    if h5_path.is_file() and h5_path.stat().st_size > 100_000_000:
        return h5_path
    print(f"[bootstrap] Downloading official SciCode test_data.h5 (~1.0 GB) to {h5_path} ...")
    tmp_h5 = h5_path.with_suffix(".h5.tmp")
    # Execute external command (`subprocess.run(['curl', '-sL', SCICODE_H5_GDRIVE_URL, '-o'...`).
    subprocess.run(
        ["curl", "-sL", SCICODE_H5_GDRIVE_URL, "-o", str(tmp_h5)],
        check=True,
        timeout=900,
    )
    tmp_h5.replace(h5_path)
    return h5_path


# ============================================================================
# 3. Ollama Client & 3 Canonical Step-by-Step Arms
# ============================================================================


def resolve_role_model(ws: Path, role: str, cli_override: str | None = None) -> str:
    r"""Resolve user-configured Ollama model tag from ~/.copilot/agents or .github/agents."""
    if cli_override:
        return cli_override
    user_agent = Path.home() / ".copilot" / "agents" / AGENT_FILE_MAP[role]
    ws_agent = ws / ".github" / "agents" / AGENT_FILE_MAP[role]
    src = user_agent if user_agent.is_file() else ws_agent
    raw = src.read_text(encoding="utf-8")
    _, fm, _ = raw.split("---", 2)
    m = re.search(r"^model:\s*\[([^\]]+)\]", fm, re.MULTILINE)
    tag = m.group(1).split(",")[0].strip().strip("'\"") if m else "gemma4:26b-a4b-it-q8_0"
    return tag.split("/", 1)[-1] if "/" in tag else tag


def call_ollama_chat(
    ollama_url: str,
    model: str,
    messages: list[dict[str, object]],
    tools: list[dict[str, object]] | None = None,
    *,
    think: bool = True,
    temperature: float | None = None,
) -> dict[str, object]:
    """Call local Ollama ``/api/chat`` preserving compiled Modelfile sampling defaults."""
    opts: dict[str, object] = {
        "top_p": 0.95,
        "top_k": 64,
        "num_ctx": 131072,
        "num_predict": 8192,
    }
    if temperature is not None:
        opts["temperature"] = temperature
    payload: dict[str, object] = {
        "model": model,
        "messages": messages,
        "think": think,
        "stream": False,
        "options": opts,
    }
    if tools:
        payload["tools"] = tools
    # Step through `range(2)` (`attempt`) to update or verify each item.
    for attempt in range(2):
        req = urllib.request.Request(
            f"{ollama_url.rstrip('/')}/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        # Open HTTP response stream and close the socket automatically when done.
        with urllib.request.urlopen(req, timeout=900) as resp:
            decoded = json.loads(resp.read().decode("utf-8"))
        msg_chk = decoded.get("message") or {}
        if msg_chk.get("content") or msg_chk.get("tool_calls") or attempt == 1:
            return decoded
    return decoded


def _smoke_check_step_script(full_step_code: str, expected_fn_name: str) -> tuple[bool, str]:
    """Verify AST syntax, module import, and target symbol presence (ZERO held-out tests)."""
    try:
        # Parse Python source code into an `ast.AST` syntax tree for structural inspection.
        tree = ast.parse(full_step_code)
    except SyntaxError as exc:
        return False, f"SyntaxError: {exc}"

    defined = {
        n.name
        for n in tree.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }
    if expected_fn_name not in defined:
        return False, f"Target function/class `{expected_fn_name}` is missing from top-level definitions {sorted(defined)}."

    # Create an isolated temporary directory that cleans itself up automatically.
    with tempfile.TemporaryDirectory(prefix="scicode_step_smoke_") as td:
        mod_p = Path(td) / "step_mod.py"
        mod_p.write_text(full_step_code, encoding="utf-8")
        # Execute external command (`res = subprocess.run([_python_interpreter(), '-c', 'import...`).
        res = subprocess.run(
            [_python_interpreter(), "-c", "import step_mod"],
            cwd=td,
            capture_output=True,
            text=True,
            timeout=30,
        )
        # Check whether the child process succeeded or failed (`res.returncode != 0`).
        if res.returncode != 0:
            return False, (res.stderr or res.stdout).strip()[-500:]
    return True, f"Module parsed and imported cleanly; `{expected_fn_name}` is defined."


class _StripTypeAnnotations(ast.NodeTransformer):
    r"""Strip inline type annotations so SciCode's import-stripping scorer never hits NameError."""

    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.AST:
        self.generic_visit(node)
        node.returns = None
        return node

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> ast.AST:
        self.generic_visit(node)
        node.returns = None
        return node

    def visit_arg(self, node: ast.arg) -> ast.AST:
        node.annotation = None
        return node

    def visit_AnnAssign(self, node: ast.AnnAssign) -> ast.AST:
        if node.value is None:
            return ast.Pass()
        return ast.Assign(targets=[node.target], value=node.value)


def _serialize_facet_step_for_scicode(
    module_source: str,
    previous_code: str,
    target_fn_name: str,
) -> str:
    r"""Extract newly implemented functions/constants preserving raw docstrings and inline comments."""
    # SciCode's evaluation harness prepends `required_dependencies` + `previous_code`
    # without `from __future__ import annotations` or `typing` imports. Therefore we:
    #   1. Drop functions already defined in `previous_code` so earlier steps aren't duplicated,
    #   2. Strip inline type annotations from headers while keeping raw `r"""` docstrings intact,
    #   3. Preserve safe stdlib helper imports (`math`, `functools`, `itertools`) inline via `__import__`.
    prev_names: set[str] = set()
    try:
        # Parse Python source code into an `ast.AST` syntax tree for structural inspection.
        prev_tree = ast.parse(previous_code)
        for n in prev_tree.body:
            # Handle syntax-tree nodes matching `isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))`.
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                prev_names.add(n.name)
    except SyntaxError:
        pass

    # Parse Python source into an AST, recovering cleanly if syntax is invalid.
    try:
        # Parse Python source code into an `ast.AST` syntax tree for structural inspection.
        mod_tree = ast.parse(module_source)
    except SyntaxError:
        return f"```python\n{module_source}\n```"

    src_lines = module_source.splitlines()
    kept_blocks: list[str] = []
    preserved_stdlib_bindings: list[str] = []
    safe_stdlib = {"functools", "itertools", "math", "cmath", "operator", "collections"}
    # Step through `mod_tree.body` (`node`) to update or verify each item.
    for node in mod_tree.body:
        # Handle syntax-tree nodes matching `isinstance(node, ast.Import)`.
        if isinstance(node, ast.Import):
            # Step through `node.names` (`alias`) to update or verify each item.
            for alias in node.names:
                top_m = alias.name.split(".")[0]
                as_n = alias.asname or top_m
                if top_m in safe_stdlib and f"import {top_m}" not in previous_code:
                    preserved_stdlib_bindings.append(f"{as_n} = __import__({top_m!r})")
            continue
        # Handle syntax-tree nodes matching `isinstance(node, ast.ImportFrom)`.
        if isinstance(node, ast.ImportFrom):
            if node.module and node.module.split(".")[0] in safe_stdlib:
                # Step through `node.names` (`alias`) to update or verify each item.
                for alias in node.names:
                    if alias.name != "*":
                        as_n = alias.asname or alias.name
                        preserved_stdlib_bindings.append(
                            f"{as_n} = getattr(__import__({node.module!r}, fromlist=[{alias.name!r}]), {alias.name!r})"
                        )
            continue
        # Skip this iteration when `isinstance(node, ast.Expr) and isinstance(node.value...`.
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            continue
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            # Skip this iteration when `node.name in prev_names and node.name != target_fn_name`.
            if node.name in prev_names and node.name != target_fn_name:
                continue
            stripped_fn = _StripTypeAnnotations().visit(
                ast.FunctionDef(
                    name=node.name,
                    args=json_safe_copy_args(node.args),
                    body=[ast.Pass()],
                    decorator_list=[],
                    returns=None,
                )
            )
            ast.fix_missing_locations(stripped_fn)
            header_line = ast.unparse(stripped_fn).splitlines()[0]
            # Handle syntax-tree nodes matching `isinstance(node, ast.AsyncFunctionDef)`.
            if isinstance(node, ast.AsyncFunctionDef):
                header_line = "async " + header_line
            body_start = node.body[0].lineno if node.body else node.lineno + 1
            end_ln = getattr(node, "end_lineno", body_start) or body_start
            decs = [src_lines[d.lineno - 1] for d in node.decorator_list if hasattr(d, "lineno")]
            body_slice = src_lines[body_start - 1 : end_ln]
            kept_blocks.append("\n".join([*decs, header_line, *body_slice]))
            continue
        # Handle syntax-tree nodes matching `isinstance(node, ast.ClassDef)`.
        if isinstance(node, ast.ClassDef):
            # Skip this iteration when `node.name in prev_names and node.name != target_fn_name`.
            if node.name in prev_names and node.name != target_fn_name:
                continue
            end_ln = getattr(node, "end_lineno", node.lineno) or node.lineno
            kept_blocks.append("\n".join(src_lines[node.lineno - 1 : end_ln]))
            continue
        cleaned = _StripTypeAnnotations().visit(node)
        ast.fix_missing_locations(cleaned)
        kept_blocks.append(ast.unparse(cleaned))

    rendered = "\n\n".join(kept_blocks)
    preamble = ("\n".join(dict.fromkeys(preserved_stdlib_bindings)) + "\n") if preserved_stdlib_bindings else ""
    return f"```python\n# Background: Verified by Facet Cleanroom Pipeline\n{preamble}{rendered}\n```"


def json_safe_copy_args(args_node: ast.arguments) -> ast.arguments:
    """Clone an ``ast.arguments`` node with annotations cleared for header synthesis."""
    import copy

    cloned = copy.deepcopy(args_node)
    # Verify or process each `group` in the candidate set.
    for group in (cloned.posonlyargs, cloned.args, cloned.kwonlyargs):
        # Step through `group` (`a`) to update or verify each item.
        for a in group:
            a.annotation = None
    if cloned.vararg is not None:
        cloned.vararg.annotation = None
    if cloned.kwarg is not None:
        cloned.kwarg.annotation = None
    return cloned


class _HookRunner:
    """Invoke ``pipeline_guards.py`` deterministically inside an isolated step workspace."""

    def __init__(self, ws: Path, copilot_home: Path, transcript_path: Path) -> None:
        self.ws = ws
        self.copilot_home = copilot_home
        self.transcript_path = transcript_path
        self.script = ws / ".github" / "hooks" / "scripts" / "pipeline_guards.py"
        self._seq = 0

    def call(self, action: str, payload: dict[str, object]) -> dict[str, object]:
        self._seq += 1
        full = {
            "cwd": str(self.ws),
            "transcriptPath": str(self.transcript_path),
            "timestamp": int(time.time() * 1000) + self._seq,
            **payload,
        }
        env = dict(os.environ, COPILOT_HOME=str(self.copilot_home))
        # Execute external command (`res = subprocess.run([_python_interpreter()...`).
        res = subprocess.run(
            [_python_interpreter(), str(self.script), "--scope", "workspace", action],
            input=json.dumps(full),
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
        )
        out = res.stdout.strip()
        # No hook action is required when `not out`; return `{}`.
        if not out:
            return {}
        try:
            return json.loads(out.splitlines()[-1])
        except Exception:
            return {}

    def append_transcript(
        self, event_type: str, data: dict[str, object], agent_id: str | None = None
    ) -> None:
        ev: dict[str, object] = {
            "id": f"ev-{int(time.time() * 1000)}-{self._seq}",
            "type": event_type,
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "data": data,
        }
        if agent_id:
            ev["agentId"] = agent_id
        # Open file (`self.transcript_path.open('a', encoding='utf-8')`) and guarantee descriptor closure on exit.
        with self.transcript_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")


def _parse_agent_spec(
    ws: Path,
    role: str,
    ancestor_cats: set[str] | None = None,
    model_override: str | None = None,
) -> dict[str, object]:
    raw = (ws / ".github" / "agents" / AGENT_FILE_MAP[role]).read_text(encoding="utf-8")
    _, fm, body = raw.split("---", 2)
    tools_m = re.search(r"^tools:\s*\[([^\]]*)\]", fm, re.MULTILINE)
    declared = {
        t.strip().strip("'\"")
        for t in (tools_m.group(1).split(",") if tools_m else [])
        if t.strip()
    }
    eff_cats = (declared & ancestor_cats) if ancestor_cats is not None else declared
    skills_m = re.search(r"^skills:\s*\[([^\]]*)\]", fm, re.MULTILINE)
    skills = [
        s.strip().strip("'\"")
        for s in (skills_m.group(1).split(",") if skills_m else [])
        if s.strip()
    ]
    parts = [body.strip()]
    ci = ws / ".github" / "copilot-instructions.md"
    # Read and process the file on disk when `ci.is_file()`.
    if ci.is_file():
        parts.append(f"<custom_instruction>\n{ci.read_text(encoding='utf-8').strip()}\n</custom_instruction>")
    if skills and role not in ("Facet", "Orchestrator"):
        # Step through `skills` (`sk`) to update or verify each item.
        for sk in skills:
            sk_f = ws / ".github" / "skills" / sk / "SKILL.md"
            # Read and process the file on disk when `sk_f.is_file()`.
            if sk_f.is_file():
                sk_raw = sk_f.read_text(encoding="utf-8")
                sk_body = sk_raw.split("---", 2)[2].strip() if sk_raw.startswith("---") else sk_raw.strip()
                parts.append(f"\n# Loaded Skill: {sk}\n\n{sk_body}")

    ollama_model = resolve_role_model(
        ws,
        role,
        model_override if (model_override and role not in ("Facet", "Orchestrator", "Scribe")) else None,
    )
    return {
        "model": ollama_model,
        "tool_categories": eff_cats,
        "think": True,
        "temperature": 0.1 if "e4b" in ollama_model else 1.0,
        "system_prompt": "\n\n".join(parts),
    }


def _tool_schemas(role: str, categories: set[str]) -> list[dict[str, object]]:
    enum_targets = (
        ["Orchestrator"]
        if role == "Facet"
        else ["Scribe", "Researcher", "Formalist", "Engineer", "Auditor"]
    )
    defs: dict[str, dict[str, object]] = {
        "view": {
            "name": "view",
            "description": "View file or directory contents at an absolute path.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "view_range": {"type": "array", "items": {"type": "integer"}},
                },
                "required": ["path"],
            },
        },
        "create": {
            "name": "create",
            "description": "Create or atomically overwrite a file at an absolute path.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "file_text": {"type": "string"},
                },
                "required": ["path", "file_text"],
            },
        },
        "edit": {
            "name": "edit",
            "description": "Replace exact old_str with new_str in an existing file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_str": {"type": "string"},
                    "new_str": {"type": "string"},
                },
                "required": ["path", "old_str", "new_str"],
            },
        },
        "bash": {
            "name": "bash",
            "description": "Run a non-interactive bash command in the workspace root.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "description": {"type": "string"},
                },
                "required": ["command", "description"],
            },
        },
        "grep": {
            "name": "grep",
            "description": "Search file contents with regex.",
            "parameters": {
                "type": "object",
                "properties": {"pattern": {"type": "string"}},
                "required": ["pattern"],
            },
        },
        "glob": {
            "name": "glob",
            "description": "Find files by glob pattern.",
            "parameters": {
                "type": "object",
                "properties": {"pattern": {"type": "string"}},
                "required": ["pattern"],
            },
        },
        "web_fetch": {
            "name": "web_fetch",
            "description": "Fetch URL contents.",
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
            },
        },
        "sql": {
            "name": "sql",
            "description": "Query session SQLite database.",
            "parameters": {
                "type": "object",
                "properties": {
                    "description": {"type": "string"},
                    "query": {"type": "string"},
                },
                "required": ["description", "query"],
            },
        },
        "task": {
            "name": "task",
            "description": "Delegate stage work to a specialist subagent.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "agent_type": {"type": "string", "enum": enum_targets},
                    "mode": {"type": "string", "enum": ["sync"]},
                    "prompt": {"type": "string"},
                },
                "required": ["name", "description", "agent_type", "prompt"],
            },
        },
    }
    allowed: set[str] = set()
    # Step through `categories` (`c`) to update or verify each item.
    for c in categories:
        allowed |= CATEGORY_TO_TOOLS.get(c, set())
    return [{"type": "function", "function": defs[n]} for n in sorted(allowed) if n in defs]


def _exec_ws_tool(
    ws: Path, sql_conn: sqlite3.Connection, tool_name: str, args: dict[str, object]
) -> tuple[bool, str]:
    # Guard optional step so unexpected runtime errors degrade safely instead of crashing.
    try:
        # Intercept `tool_name == 'view'` tool calls to validate paths, permissions, and arguments.
        if tool_name == "view":
            raw_p = str(args.get("path", "")).strip()
            target = Path(raw_p) if Path(raw_p).is_absolute() else (ws / raw_p)
            # Nothing to read on disk yet (`not target.exists()`), so return `(False, f'File or...` immediately.
            if not target.exists():
                return False, f"File or directory not found: {target}"
            if target.is_dir():
                return True, "\n".join(sorted(p.name for p in target.iterdir() if not p.name.startswith(".")))
            txt = target.read_text(encoding="utf-8", errors="replace")
            vr = args.get("view_range")
            # Process list elements once we confirm `isinstance(vr, list) and len(vr) == 2`.
            if isinstance(vr, list) and len(vr) == 2:
                lines = txt.splitlines()
                s_i = max(1, int(vr[0])) - 1
                e_i = len(lines) if int(vr[1]) == -1 else int(vr[1])
                txt = "\n".join(lines[s_i:e_i])
            return True, txt[:24000]
        # Intercept `tool_name == 'create'` tool calls to validate paths, permissions, and arguments.
        if tool_name == "create":
            raw_p = str(args.get("path", "")).strip()
            target = Path(raw_p) if Path(raw_p).is_absolute() else (ws / raw_p)
            target.parent.mkdir(parents=True, exist_ok=True)
            content = str(args.get("file_text", ""))
            target.write_text(content, encoding="utf-8")
            return True, f"Created file {target} with {len(content)} characters."
        # Intercept `tool_name == 'edit'` tool calls to validate paths, permissions, and arguments.
        if tool_name == "edit":
            raw_p = str(args.get("path", "")).strip()
            target = Path(raw_p) if Path(raw_p).is_absolute() else (ws / raw_p)
            # Nothing to read on disk yet (`not target.is_file()`), so return `(False, f'File does not...` immediately.
            if not target.is_file():
                return False, f"File does not exist: {target}"
            old_s, new_s = str(args.get("old_str", "")), str(args.get("new_str", ""))
            cur = target.read_text(encoding="utf-8")
            if cur.count(old_s) != 1:
                return False, f"Expected 1 match for old_str in {target}, found {cur.count(old_s)}."
            target.write_text(cur.replace(old_s, new_s, 1), encoding="utf-8")
            return True, f"Updated {target}."
        # Invoke external tool via subprocess when `tool_name == 'bash'`.
        if tool_name == "bash":
            # Execute external command (`res = subprocess.run(str(args.get('command', ''))...`).
            res = subprocess.run(
                str(args.get("command", "")),
                shell=True,
                cwd=str(ws),
                capture_output=True,
                text=True,
                timeout=45,
            )
            out = (res.stdout + ("\n" + res.stderr if res.stderr else "")).strip()
            return True, f"{out}\n<exit_code: {res.returncode}>"[:16000]
        # Intercept `tool_name == 'grep'` tool calls to validate paths, permissions, and arguments.
        if tool_name == "grep":
            pat = re.compile(str(args.get("pattern", "")))
            hits: list[str] = []
            # Scan matching filesystem entries (`fp` in `sorted(ws.rglob('*'))`).
            for fp in sorted(ws.rglob("*")):
                # Read and process the file on disk when `fp.is_file() and '.venv' not in fp.parts and ('.git' not in...`.
                if fp.is_file() and ".venv" not in fp.parts and ".git" not in fp.parts:
                    # Read file contents from disk with safe fallback on I/O errors.
                    try:
                        # Walk `enumerate(fp.read_text(encoding='utf-8').splitlin...` and collect matching entries (`(idx, ln)`).
                        for idx, ln in enumerate(fp.read_text(encoding="utf-8").splitlines(), 1):
                            if pat.search(ln):
                                hits.append(f"{fp.relative_to(ws)}:{idx}:{ln}")
                    except Exception:
                        continue
            return True, "\n".join(hits[:100]) or "No matches found."
        # Intercept `tool_name == 'glob'` tool calls to validate paths, permissions, and arguments.
        if tool_name == "glob":
            pat_s = str(args.get("pattern", "*"))
            matched = [
                str(p.relative_to(ws))
                for p in sorted(ws.rglob("*"))
                if ".venv" not in p.parts and fnmatch.fnmatch(str(p.relative_to(ws)), pat_s)
            ]
            return True, "\n".join(matched[:200]) or "No matching files."
        # Intercept `tool_name == 'web_fetch'` tool calls to validate paths, permissions, and arguments.
        if tool_name == "web_fetch":
            url = str(args.get("url", ""))
            req = urllib.request.Request(url, headers={"User-Agent": "Facet-SciCode/1.0"})
            # Open HTTP response stream and close the socket automatically when done.
            with urllib.request.urlopen(req, timeout=20) as r:
                body = r.read(1_000_000).decode("utf-8", errors="replace")
            return True, body[:12000]
        # Intercept `tool_name == 'sql'` tool calls to validate paths, permissions, and arguments.
        if tool_name == "sql":
            q = str(args.get("query", ""))
            cur = sql_conn.cursor()
            cur.executescript(q) if ";" in q.strip().rstrip(";") else cur.execute(q)
            rows = cur.fetchall() if cur.description else []
            sql_conn.commit()
            return True, json.dumps(rows)
        return False, f"Unsupported tool: {tool_name}"
    except Exception as exc:
        return False, f"Tool {tool_name} failed: {exc}"


def _run_facet_single_step(
    canonical_step_prompt: str,
    previous_code: str,
    step_id: str,
    target_fn_name: str,
    *,
    ollama_url: str,
    step_work_dir: Path,
    model_override: str | None = None,
) -> tuple[str, int, int, int, bool]:
    r"""Run one canonical SciCode substep through S0->S5 @Facet (autonomous-batch)."""
    if step_work_dir.exists():
        shutil.rmtree(step_work_dir)
    ws = step_work_dir / "workspace"
    chome = step_work_dir / "copilot_home"
    transcript = step_work_dir / "events.jsonl"
    ws.mkdir(parents=True, exist_ok=True)
    chome.mkdir(parents=True, exist_ok=True)
    transcript.write_text("", encoding="utf-8")
    # Clean up temporary file/descriptor on a best-effort basis.
    try:
        run_root = step_work_dir.parent.parent.parent
        lens_link = run_root / "live_lens_workspace"
        tmp_link = run_root / f".live_lens_workspace.{os.getpid()}.tmp"
        tmp_link.unlink(missing_ok=True)
        os.symlink(ws.resolve(), tmp_link)
        # Atomically replace target file on disk so readers never see a partially written file.
        os.replace(tmp_link, lens_link)
        real_ext_data = Path.home() / ".copilot" / "extension-data"
        if real_ext_data.is_dir() and not (chome / "extension-data").exists():
            os.symlink(real_ext_data, chome / "extension-data")
    except Exception:
        pass

    shutil.copytree(REPO_ROOT / ".github", ws / ".github", dirs_exist_ok=True)
    batch_cfg = json.loads(
        (REPO_ROOT / "templates" / "governance-modes" / "autonomous-batch.pipeline_config.json").read_text(
            encoding="utf-8"
        )
    )
    batch_cfg["allow_web_research"] = False
    batch_cfg["enable_local_spec_critic"] = "system1_s1_s2"
    batch_cfg["decision_model"] = {
        "enabled": True,
        "model": "pplx-decider-flash",
        "confidence_threshold": 0.85,
        "keep_alive": 0,
        "timeout_sec": 15,
    }
    (ws / ".pipeline_config.json").write_text(json.dumps(batch_cfg, indent=2) + "\n", encoding="utf-8")
    # Read and process the file on disk when `(REPO_ROOT / '.venv').exists() and (not (ws / '.venv').exists())`.
    if (REPO_ROOT / ".venv").exists() and not (ws / ".venv").exists():
        os.symlink(REPO_ROOT / ".venv", ws / ".venv")
    (ws / "specs").mkdir(exist_ok=True)

    mod_slug = f"scicode_step_{step_id.replace('.', '_')}"

    hooks = _HookRunner(ws, chome, transcript)
    sql_conn = sqlite3.connect(":memory:")
    sql_conn.execute(
        "CREATE TABLE todos (id TEXT PRIMARY KEY, title TEXT, description TEXT, status TEXT DEFAULT 'pending')"
    )
    sql_conn.execute("CREATE TABLE todo_deps (todo_id TEXT, depends_on TEXT)")

    tok = {"prompt": 0, "output": 0, "eval_ns": 0}

    def _run_subagent(parent_sid: str, role: str, task_prompt: str, ancestor_cats: set[str]) -> str:
        prof = _parse_agent_spec(ws, role, ancestor_cats, model_override)
        eff_cats: set[str] = set(prof["tool_categories"])  # type: ignore[arg-type]
        model = str(prof["model"])
        tools = _tool_schemas(role, eff_cats)
        agent_id = f"sub-{role.lower()}-{int(time.time() * 1000)}"
        sub_start = hooks.call(
            "subagent-start",
            {
                "sessionId": parent_sid,
                "agentId": agent_id,
                "parentAgentId": parent_sid,
                "agentName": role,
                "agentType": role,
            },
        )
        add_ctx = str(sub_start.get("additionalContext") or "").strip()
        u_content = f"{add_ctx}\n\n{task_prompt}".strip() if add_ctx else task_prompt
        if role == "Orchestrator":
            tr_o = hooks.call(
                "orchestrator-prompt",
                {
                    "sessionId": agent_id,
                    "agentId": agent_id,
                    "agentType": "Orchestrator",
                    "parentAgentId": parent_sid,
                    "prompt": task_prompt,
                    "transformedPrompt": u_content,
                },
            )
            u_content = str(tr_o.get("modifiedTransformedPrompt") or u_content)
        hooks.append_transcript(
            "user.message",
            {"content": task_prompt, "transformedContent": u_content},
            agent_id=agent_id,
        )
        msgs: list[dict[str, object]] = [
            {"role": "system", "content": prof["system_prompt"]},
            {"role": "user", "content": u_content},
        ]
        stop_active = False
        max_sub_turns = 48 if role == "Orchestrator" else 18
        # Step through `range(max_sub_turns)` (`_`) to update or verify each item.
        for _ in range(max_sub_turns):
            resp = call_ollama_chat(
                ollama_url,
                model,
                msgs,
                tools,
                think=bool(prof["think"]),
                temperature=float(prof["temperature"]),  # type: ignore[arg-type]
            )
            tok["prompt"] += int(resp.get("prompt_eval_count") or 0)
            tok["output"] += int(resp.get("eval_count") or 0)
            tok["eval_ns"] += int(resp.get("eval_duration") or 0)
            msg = dict(resp.get("message") or {})
            c_txt = str(msg.get("content") or "")
            tcalls = list(msg.get("tool_calls") or [])
            msgs.append(msg)
            hooks.append_transcript(
                "assistant.message", {"model": model, "content": c_txt}, agent_id=agent_id
            )
            if tcalls:
                # Step through `enumerate(tcalls, 1)` (`(tc_idx, tc)`) to update or verify each item.
                for tc_idx, tc in enumerate(tcalls, 1):
                    fn_o = tc.get("function") or {}
                    t_name = str(fn_o.get("name") or "")
                    r_args = fn_o.get("arguments") or {}
                    t_args = json.loads(r_args) if isinstance(r_args, str) else dict(r_args)
                    tc_id = str(tc.get("id") or f"call_{tc_idx}")
                    if role == "Orchestrator" and t_name not in ("view", "grep", "glob", "sql", "task"):
                        cg = hooks.call(
                            "coordinator-guard",
                            {
                                "sessionId": agent_id,
                                "agentId": agent_id,
                                "agentType": role,
                                "parentAgentId": parent_sid,
                                "toolName": t_name,
                                "toolArgs": t_args,
                            },
                        )
                        if cg.get("permissionDecision") == "deny":
                            msgs.append(
                                {
                                    "role": "tool",
                                    "tool_name": t_name,
                                    "content": f"Denied: {cg.get('permissionDecisionReason')}",
                                }
                            )
                            continue
                    pre = hooks.call(
                        "pre-tool",
                        {
                            "sessionId": agent_id,
                            "agentId": agent_id,
                            "agentType": role,
                            "parentAgentId": parent_sid,
                            "toolCallId": tc_id,
                            "toolName": t_name,
                            "toolArgs": t_args,
                        },
                    )
                    if pre.get("permissionDecision") == "deny":
                        reason = str(pre.get("permissionDecisionReason") or "Denied")
                        hooks.call(
                            "post-failure",
                            {
                                "sessionId": agent_id,
                                "agentId": agent_id,
                                "agentType": role,
                                "toolName": t_name,
                                "toolArgs": t_args,
                                "error": reason,
                            },
                        )
                        msgs.append({"role": "tool", "tool_name": t_name, "content": f"Denied: {reason}"})
                        continue
                    eff_args = dict(pre.get("modifiedArgs") or t_args)
                    if role == "Orchestrator" and t_name == "task":
                        sub_role = str(eff_args.get("agent_type") or eff_args.get("name") or "")
                        sub_p = str(eff_args.get("prompt") or "")
                        print(f"        -> [step {step_id}] @Orchestrator -> @{sub_role}", flush=True)
                        sub_out = _run_subagent(agent_id, sub_role, sub_p, eff_cats)
                        msgs.append({"role": "tool", "tool_name": "task", "content": sub_out})
                        continue
                    ok, t_out = _exec_ws_tool(ws, sql_conn, t_name, eff_args)
                    arg_lbl = Path(str(eff_args.get("path") or "")).name or str(eff_args.get("command") or "")[:36]
                    print(f"           [{role}] {t_name}({arg_lbl}) -> {'ok' if ok else 'ERR'}", flush=True)
                    if not ok:
                        pf = hooks.call(
                            "post-failure",
                            {
                                "sessionId": agent_id,
                                "agentType": role,
                                "toolName": t_name,
                                "toolArgs": eff_args,
                                "error": t_out,
                            },
                        )
                        rec = str(pf.get("additionalContext") or "")
                        msgs.append(
                            {"role": "tool", "tool_name": t_name, "content": f"{t_out}\n\n{rec}".strip()}
                        )
                    else:
                        ve = hooks.call(
                            "validate-edit",
                            {
                                "sessionId": agent_id,
                                "agentType": role,
                                "toolCallId": tc_id,
                                "toolName": t_name,
                                "toolArgs": eff_args,
                                "toolResult": {"resultType": "success", "textResultForLlm": t_out},
                            },
                        )
                        m_res = ve.get("modifiedResult")
                        # Unpack fields once we confirm `isinstance(m_res, dict) and m_res.get('textResultForLlm')`.
                        if isinstance(m_res, dict) and m_res.get("textResultForLlm"):
                            t_out = str(m_res["textResultForLlm"])
                        v_ctx = str(ve.get("additionalContext") or "")
                        if v_ctx:
                            t_out = f"{t_out}\n\nAdditional guidance from postToolUse hooks:\n{v_ctx}"
                        msgs.append({"role": "tool", "tool_name": t_name, "content": t_out})
                stop_active = False
                continue

            if role == "Orchestrator":
                ostop = hooks.call(
                    "orchestrator-stop",
                    {
                        "sessionId": agent_id,
                        "agentId": agent_id,
                        "agentName": role,
                        "agentType": role,
                        "response": c_txt,
                        "stopReason": "end_turn",
                        "stop_hook_active": stop_active,
                    },
                )
                if ostop.get("decision") == "block" and not stop_active:
                    stop_active = True
                    msgs.append({"role": "user", "content": str(ostop.get("reason") or "Continue pipeline stage.")})
                    continue

            sub_stop = hooks.call(
                "subagent-stop",
                {
                    "sessionId": parent_sid,
                    "agentId": agent_id,
                    "agentName": role,
                    "agentType": role,
                    "response": c_txt,
                    "stopReason": "end_turn",
                    "stop_hook_active": stop_active,
                },
            )
            if sub_stop.get("decision") == "block" and not stop_active:
                stop_active = True
                msgs.append({"role": "user", "content": str(sub_stop.get("reason") or "Persist deliverable.")})
                continue
            return str(sub_stop.get("modifiedResponse") or c_txt)
        return ""

    goal = (
        f"@Facet {canonical_step_prompt.strip()}\n\n"
        f"(Path A closed-book specification: implement `{target_fn_name}` in target module `{mod_slug}.py` "
        "preserving the exact function header above; if previous step functions appear in the prompt above, "
        f"include them in `{mod_slug}.py` so 4-predicate verification executes self-contained.)"
    )
    parent_sid = f"scicode-facet-{step_id.replace('.', '-')}"
    ss_out = hooks.call(
        "session-start", {"sessionId": parent_sid, "source": "new", "initialPrompt": goal}
    )
    ss_ctx = str(ss_out.get("additionalContext") or "").strip()
    f_prof = _parse_agent_spec(ws, "Facet")
    f_cats: set[str] = set(f_prof["tool_categories"])  # type: ignore[arg-type]
    f_model = str(f_prof["model"])
    f_tools = _tool_schemas("Facet", f_cats)
    f_msgs: list[dict[str, object]] = [
        {"role": "system", "content": f"{f_prof['system_prompt']}\n\n{ss_ctx}".strip()}
    ]

    pending_prompt: str | None = goal
    approvals = 0
    st_path = ws / ".pipeline_state.json"
    # Repeat step-by-step while `pending_prompt is not None and approvals <= 4`.
    while pending_prompt is not None and approvals <= 4:
        cur_p = pending_prompt
        pending_prompt = None
        hooks.call("user-prompt", {"sessionId": parent_sid, "prompt": cur_p})
        tr_g = hooks.call(
            "user-prompt-transformed",
            {"sessionId": parent_sid, "prompt": cur_p, "transformedPrompt": cur_p},
        )
        eff_p = str(tr_g.get("modifiedTransformedPrompt") or cur_p)
        tr_f = hooks.call(
            "facet-prompt",
            {"sessionId": parent_sid, "prompt": cur_p, "transformedPrompt": eff_p},
        )
        eff_p = str(tr_f.get("modifiedTransformedPrompt") or eff_p)
        hooks.append_transcript("user.message", {"content": cur_p, "transformedContent": eff_p})
        f_msgs.append({"role": "user", "content": eff_p})

        f_stop_active = False
        # Step through `range(8)` (`_`) to update or verify each item.
        for _ in range(8):
            resp = call_ollama_chat(
                ollama_url,
                f_model,
                f_msgs,
                f_tools,
                think=True,
                temperature=0.1,
            )
            tok["prompt"] += int(resp.get("prompt_eval_count") or 0)
            tok["output"] += int(resp.get("eval_count") or 0)
            tok["eval_ns"] += int(resp.get("eval_duration") or 0)
            msg = dict(resp.get("message") or {})
            c_txt = str(msg.get("content") or "")
            tcalls = list(msg.get("tool_calls") or [])
            f_msgs.append(msg)
            hooks.append_transcript("assistant.message", {"model": f_model, "content": c_txt})
            if tcalls:
                # Step through `enumerate(tcalls, 1)` (`(tc_idx, tc)`) to update or verify each item.
                for tc_idx, tc in enumerate(tcalls, 1):
                    fn_o = tc.get("function") or {}
                    t_name = str(fn_o.get("name") or "")
                    r_args = fn_o.get("arguments") or {}
                    t_args = json.loads(r_args) if isinstance(r_args, str) else dict(r_args)
                    hg = hooks.call(
                        "hypervisor-guard",
                        {
                            "sessionId": parent_sid,
                            "toolCallId": f"fc_{tc_idx}",
                            "toolName": t_name,
                            "toolArgs": t_args,
                        },
                    )
                    if hg.get("permissionDecision") == "deny":
                        f_msgs.append(
                            {
                                "role": "tool",
                                "tool_name": t_name,
                                "content": f"Denied: {hg.get('permissionDecisionReason')}",
                            }
                        )
                        continue
                    eff_a = dict(hg.get("modifiedArgs") or t_args)
                    if t_name == "task":
                        sub_r = str(eff_a.get("agent_type") or "Orchestrator")
                        sub_out = _run_subagent(parent_sid, sub_r, str(eff_a.get("prompt") or ""), f_cats)
                        f_msgs.append({"role": "tool", "tool_name": "task", "content": sub_out})
                    else:
                        _ok, out_s = _exec_ws_tool(ws, sql_conn, t_name, eff_a)
                        f_msgs.append({"role": "tool", "tool_name": t_name, "content": out_s})
                continue

            fstop = hooks.call(
                "facet-stop",
                {
                    "sessionId": parent_sid,
                    "agentType": "Facet",
                    "response": c_txt,
                    "stopReason": "end_turn",
                    "stop_hook_active": f_stop_active,
                },
            )
            if fstop.get("decision") == "block" and not f_stop_active:
                f_stop_active = True
                f_msgs.append({"role": "user", "content": str(fstop.get("reason") or "Continue.")})
                continue
            break

        cur_stage = (
            json.loads(st_path.read_text(encoding="utf-8")).get("current_stage", "")
            if st_path.is_file()
            else ""
        )
        # Handle pipeline behavior for stage `cur_stage.startswith('CHECKPOINT_')`.
        if cur_stage.startswith("CHECKPOINT_"):
            # Stop scanning further once `cur_stage == 'CHECKPOINT_REPAIR_ESCALATION'`.
            if cur_stage == "CHECKPOINT_REPAIR_ESCALATION":
                break
            approvals += 1
            pending_prompt = "/approve"

    spec_dirs = sorted(p for p in (ws / "specs").iterdir() if p.is_dir()) if (ws / "specs").is_dir() else []
    search_roots = spec_dirs if spec_dirs else [ws]
    py_chunks: list[str] = []
    auditor_passed = False
    repairs = 0
    # Step through `search_roots` (`root_d`) to update or verify each item.
    for root_d in search_roots:
        # Read and process the file on disk when `(root_d / 'PROVENANCE.json').is_file() or (root_d /...`.
        if (root_d / "PROVENANCE.json").is_file() or (root_d / "VERIFICATION.md").is_file():
            auditor_passed = True
        for hist_name in ("AUDIT_HISTORY.json", "AUDIT_FEEDBACK.json"):
            hp = root_d / hist_name
            # Read and process the file on disk when `hp.is_file()`.
            if hp.is_file():
                repairs = max(repairs, 1)
        # Scan matching filesystem entries (`py_f` in `sorted(root_d.glob('*.py'))`).
        for py_f in sorted(root_d.glob("*.py")):
            # Handle strings with matching prefix/suffix (`not py_f.name.startswith(('verify_', 'test_', 'conftest', 'smoke_'))`).
            if not py_f.name.startswith(("verify_", "test_", "conftest", "smoke_")):
                py_chunks.append(py_f.read_text(encoding="utf-8"))

    spec_rewinds = 0
    # Read and process the file on disk when `st_path.is_file()`.
    if st_path.is_file():
        # Parse JSON safely and handle malformed or partial payloads without crashing.
        try:
            st_obj = json.loads(st_path.read_text(encoding="utf-8"))
            repairs = max(repairs, int(st_obj.get("repair_attempts") or 0))
            # Step through `list(st_obj.get('repair_history') or [])` (`rh`) to update or verify each item.
            for rh in list(st_obj.get("repair_history") or []):
                # Unpack fields once we confirm `isinstance(rh, dict) and rh.get('rewind_target') in...`.
                if isinstance(rh, dict) and rh.get("rewind_target") in (
                    "STAGE_1_SPECIFICATION",
                    "STAGE_2_MAPPING",
                ):
                    spec_rewinds += 1
        except Exception:
            pass

    combined_src = "\n\n".join(py_chunks)
    if not combined_src.strip():
        return generate_dummy_response(""), tok["prompt"], tok["output"], tok["eval_ns"], repairs, spec_rewinds, False
    return (
        _serialize_facet_step_for_scicode(combined_src, previous_code, target_fn_name),
        tok["prompt"],
        tok["output"],
        tok["eval_ns"],
        repairs,
        spec_rewinds,
        auditor_passed,
    )


def run_arm_on_problem(
    problem: dict[str, object],
    *,
    arm: str,
    mode: str,
    with_background: bool,
    h5py_file: str,
    arm_root: Path,
    ollama_url: str,
    model_override: str | None = None,
    temperature: float | None = None,
) -> ArmProblemResult:
    """Execute one canonical step-by-step SciCode arm across all substeps of ``problem``."""
    prob_id = str(problem["problem_id"])
    prob_name = str(problem["problem_name"])
    sub_steps: list[dict[str, object]] = list(problem["sub_steps"])  # type: ignore[arg-type]
    eng_model = resolve_role_model(REPO_ROOT, "Engineer", model_override)

    prompt_template = BACKGROUND_PROMPT_TEMPLATE if with_background else DEFAULT_PROMPT_TEMPLATE
    assistant = ScicodePromptingAssistant(
        output_dir=arm_root / "generated_code",
        prompt_dir=arm_root / "prompt",
        with_background=with_background,
    )

    t0 = time.perf_counter()
    p_tok = 0
    o_tok = 0
    eval_ns = 0
    total_repairs = 0
    total_spec_rewinds = 0
    all_auditor_ok = True

    # Step through `range(len(sub_steps))` (`idx`) to update or verify each item.
    for idx in range(len(sub_steps)):
        # Skip this iteration when `is_skipped_scicode_step(prob_id, idx)`.
        if is_skipped_scicode_step(prob_id, idx):
            continue
        num_steps = idx + 1
        step_id = str(sub_steps[idx]["step_number"])
        target_fn = extract_function_name(str(sub_steps[idx]["function_header"]))

        existing_step_file = assistant.generated_step_path(prob_id, num_steps)
        # Read and process the file on disk when `existing_step_file.is_file() and mode == 'normal'`.
        if existing_step_file.is_file() and mode == "normal":
            assistant.prepare_final_prompt_with_steps(
                prob_data=problem,
                num_steps=num_steps,
                tot_steps=len(sub_steps),
                prompt_template=prompt_template,
                save=False,
            )
            if assistant.previous_llm_code[num_steps - 1] is None:
                raw_p = assistant._raw_step_cache_path(prob_id, num_steps)
                # Read and process the file on disk when `raw_p.is_file()`.
                if raw_p.is_file():
                    assistant.previous_llm_code[num_steps - 1] = raw_p.read_text(encoding="utf-8")
                else:
                    assistant.previous_llm_code[num_steps - 1] = get_function_from_code(
                        existing_step_file.read_text(encoding="utf-8"), target_fn
                    )
            print(f"    [resume-skip] {arm} :: step {step_id} already generated", flush=True)
            continue

        prompt, previous_code = assistant.prepare_final_prompt_with_steps(
            prob_data=problem,
            num_steps=num_steps,
            tot_steps=len(sub_steps),
            prompt_template=prompt_template,
        )

        if mode == "dummy":
            llm_resp = generate_dummy_response(prompt)
        elif mode == "gold":
            llm_resp = generate_gold_response(problem, num_steps)
        elif arm == "raw-zeroshot":
            resp = call_ollama_chat(
                ollama_url,
                eng_model,
                [{"role": "user", "content": prompt}],
                think=True,
                temperature=temperature,
            )
            p_tok += int(resp.get("prompt_eval_count") or 0)
            o_tok += int(resp.get("eval_count") or 0)
            eval_ns += int(resp.get("eval_duration") or 0)
            llm_resp = str((resp.get("message") or {}).get("content") or "")
        elif arm == "raw-self-refine":
            msgs: list[dict[str, object]] = [{"role": "user", "content": prompt}]
            resp = call_ollama_chat(ollama_url, eng_model, msgs, think=True, temperature=temperature)
            p_tok += int(resp.get("prompt_eval_count") or 0)
            o_tok += int(resp.get("eval_count") or 0)
            eval_ns += int(resp.get("eval_duration") or 0)
            msg = dict(resp.get("message") or {})
            msgs.append(msg)
            llm_resp = str(msg.get("content") or "")
            step_py = extract_python_script(llm_resp)
            smoke_ok, smoke_msg = _smoke_check_step_script(f"{previous_code}\n{step_py}", target_fn)
            if not smoke_ok:
                total_repairs += 1
                msgs.append(
                    {
                        "role": "user",
                        "content": (
                            f"Your step solution failed local syntax/import/signature verification:\n{smoke_msg}\n\n"
                            f"Fix the issue and return ONLY the corrected ```python``` block for `{target_fn}` "
                            "(excluding dependencies and previous functions)."
                        ),
                    }
                )
                r2 = call_ollama_chat(ollama_url, eng_model, msgs, think=True, temperature=temperature)
                p_tok += int(r2.get("prompt_eval_count") or 0)
                o_tok += int(r2.get("eval_count") or 0)
                eval_ns += int(r2.get("eval_duration") or 0)
                llm_resp = str((r2.get("message") or {}).get("content") or llm_resp)
        elif arm == "facet-pipeline":
            llm_resp, sp_tok, so_tok, se_ns, s_rep, s_rew, s_aud = _run_facet_single_step(
                prompt,
                previous_code,
                step_id,
                target_fn,
                ollama_url=ollama_url,
                step_work_dir=arm_root / "facet_workspaces" / f"step_{step_id}",
                model_override=model_override,
            )
            p_tok += sp_tok
            o_tok += so_tok
            eval_ns += se_ns
            total_repairs += s_rep
            total_spec_rewinds += s_rew
            all_auditor_ok = all_auditor_ok and s_aud
        else:
            raise ValueError(f"Unknown arm: {arm}")

        assistant.register_previous_response(problem, llm_resp, previous_code, num_steps)

    evaluator = ScicodeEvaluator(
        h5py_file=h5py_file,
        code_dir=arm_root,
        log_dir=arm_root,
        with_background=with_background,
    )
    prob_ok, sub_ok, sub_tot, step_details = evaluator.test_code(problem)
    wall = round(time.perf_counter() - t0, 3)
    first_err = next((v for v in step_details.values() if v != "pass"), "")

    tok_s = round(o_tok / (eval_ns / 1e9), 2) if eval_ns > 0 else 0.0

    return ArmProblemResult(
        problem_id=prob_id,
        problem_name=prob_name,
        arm=arm,
        model=eng_model if mode == "normal" else mode,
        main_passed=bool(prob_ok),
        substeps_passed=sub_ok,
        substeps_total=sub_tot,
        wall_seconds=wall,
        prompt_tokens=p_tok,
        output_tokens=o_tok,
        eval_tokens_per_sec=tok_s,
        repair_attempts=total_repairs,
        spec_rewinds=total_spec_rewinds,
        auditor_passed=(all_auditor_ok if arm == "facet-pipeline" else False),
        error=first_err,
    )


# ============================================================================
# 4. Resumable JSONL Checkpointing & Canonical Leaderboard Scorecard
# ============================================================================


def append_checkpoint_row(jsonl_path: Path, row: ArmProblemResult) -> None:
    jsonl_path.parent.mkdir(parents=True, exist_ok=True)
    # Open file (`jsonl_path.open('a', encoding='utf-8')`) and guarantee descriptor closure on exit.
    with jsonl_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(asdict(row), ensure_ascii=False) + "\n")


def load_completed_keys(jsonl_path: Path) -> dict[tuple[str, str], ArmProblemResult]:
    done: dict[tuple[str, str], ArmProblemResult] = {}
    # Nothing to read on disk yet (`not jsonl_path.is_file()`), so return `done` immediately.
    if not jsonl_path.is_file():
        return done
    for ln in jsonl_path.read_text(encoding="utf-8").splitlines():
        # Skip blank or comment lines.
        if not ln.strip():
            continue
        row = ArmProblemResult(**json.loads(ln))
        done[(row.problem_id, row.arm)] = row
    return done


def render_results_markdown(
    rows: list[ArmProblemResult],
    *,
    dataset_sha: str,
    split: str = "validation",
    with_background: bool = False,
    total_benchmark_problems: int = 15,
    total_benchmark_steps: int = 50,
) -> str:
    """Render ``RESULTS.md`` using canonical ``SciCode`` leaderboard metrics."""
    by_arm: dict[str, list[ArmProblemResult]] = {}
    by_prob_arm: dict[tuple[str, str], ArmProblemResult] = {}
    # Walk `rows` and collect matching entries (`r`).
    for r in rows:
        by_arm.setdefault(r.arm, []).append(r)
        by_prob_arm[(r.problem_id, r.arm)] = r

    setup_str = "w/ scientist background (`multistep_template.txt`)" if with_background else "Standard w/o background (`background_comment_template.txt`)"
    lines = [
        f"# Facet × SciCode (`{split}` Split) Canonical Benchmark Results",
        "",
        f"- **Dataset:** `SciCode1/SciCode` (`{split}` split: `{total_benchmark_problems}` main problems, `{total_benchmark_steps}` scored subproblems) + official `test_data.h5`",
        f"- **Setup:** {setup_str}",
        f"- **Dataset SHA-256:** `{dataset_sha}`",
        "",
        "## Leaderboard Scorecard",
        "",
        "| Arm | Model | Main Problem Resolve Rate | Subproblem Resolve Rate | Silent-Bug Catches | Avg Wall (s) | Avg Tok/s | Output Tokens | Repairs (Rewinds) |",
        "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |",
    ]

    arm_order = list(dict.fromkeys([*ALL_ARMS, *(r.arm for r in rows)]))
    # Step through `arm_order` (`arm`) to update or verify each item.
    for arm in arm_order:
        arm_rows = by_arm.get(arm, [])
        # Skip this iteration when `not arm_rows`.
        if not arm_rows:
            continue
        n_probs = len(arm_rows)
        main_ok = sum(1 for r in arm_rows if r.main_passed)
        sub_ok = sum(r.substeps_passed for r in arm_rows)
        sub_tot = sum(r.substeps_total for r in arm_rows) or 1
        avg_wall = sum(r.wall_seconds for r in arm_rows) / n_probs
        tot_out = sum(r.output_tokens for r in arm_rows)
        tot_rep = sum(r.repair_attempts for r in arm_rows)
        model_lbl = arm_rows[0].model

        silent_catches = 0
        if arm == "facet-pipeline":
            # Step through `arm_rows` (`r`) to update or verify each item.
            for r in arm_rows:
                raw_z = by_prob_arm.get((r.problem_id, "raw-zeroshot"))
                if (raw_z and r.substeps_passed > raw_z.substeps_passed) or (r.main_passed and r.repair_attempts > 0):
                    silent_catches += 1

        tot_rew = sum(r.spec_rewinds for r in arm_rows)
        nz_toks = [r.eval_tokens_per_sec for r in arm_rows if r.eval_tokens_per_sec > 0]
        avg_toks = (sum(nz_toks) / len(nz_toks)) if nz_toks else 0.0
        lines.append(
            f"| `{arm}` | `{model_lbl}` | **{100.0 * main_ok / n_probs:.1f}%** (`{main_ok}/{n_probs}`) | "
            f"**{100.0 * sub_ok / sub_tot:.1f}%** (`{sub_ok}/{sub_tot}`) | "
            f"`{silent_catches}` | `{avg_wall:.1f}s` | `{avg_toks:.1f}` | `{tot_out:,}` | `{tot_rep} ({tot_rew})` |"
        )

    lines.extend(
        [
            "",
            "## Per-Problem Breakdown",
            "",
            "| Problem ID | Name | Arm | Main Pass | Subproblems | Wall (s) | Repairs | First Failure |",
            "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |",
        ]
    )
    # Walk `rows` and collect matching entries (`r`).
    for r in rows:
        err_short = (r.error.replace("|", "\\|").splitlines()[0][:80]) if r.error else "—"
        lines.append(
            f"| `{r.problem_id}` | `{r.problem_name}` | `{r.arm}` | "
            f"{'PASS' if r.main_passed else 'FAIL'} | `{r.substeps_passed}/{r.substeps_total}` | "
            f"`{r.wall_seconds:.1f}` | `{r.repair_attempts}` | {err_short} |"
        )
    return "\n".join(lines) + "\n"


def render_run_comparison_markdown(
    rows_a: list[ArmProblemResult],
    rows_b: list[ArmProblemResult],
    *,
    label_a: str = "baseline",
    label_b: str = "candidate",
) -> str:
    r"""Render a side-by-side regression delta table across two ``SciCode`` runs (Track 4)."""
    by_arm_a: dict[str, list[ArmProblemResult]] = {}
    by_arm_b: dict[str, list[ArmProblemResult]] = {}
    # Walk `rows_a` and collect matching entries (`r`).
    for r in rows_a:
        by_arm_a.setdefault(r.arm, []).append(r)
    for r in rows_b:
        by_arm_b.setdefault(r.arm, []).append(r)

    all_arms = list(dict.fromkeys([*(r.arm for r in rows_a), *(r.arm for r in rows_b)]))
    out = [
        f"# SciCode Regression Comparison (`{label_a}` vs `{label_b}`)",
        "",
        f"| Arm | `{label_a}` Main % | `{label_b}` Main % | Main Delta | `{label_a}` Substep % | `{label_b}` Substep % | Substep Delta | Avg Wall Delta (s) |",
        "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |",
    ]
    # Step through `all_arms` (`arm`) to update or verify each item.
    for arm in all_arms:
        ra = by_arm_a.get(arm, [])
        rb_list = by_arm_b.get(arm, [])
        ma = (100.0 * sum(1 for x in ra if x.main_passed) / len(ra)) if ra else 0.0
        mb = (100.0 * sum(1 for x in rb_list if x.main_passed) / len(rb_list)) if rb_list else 0.0
        sa = (100.0 * sum(x.substeps_passed for x in ra) / (sum(x.substeps_total for x in ra) or 1)) if ra else 0.0
        sb = (100.0 * sum(x.substeps_passed for x in rb_list) / (sum(x.substeps_total for x in rb_list) or 1)) if rb_list else 0.0
        wa = (sum(x.wall_seconds for x in ra) / len(ra)) if ra else 0.0
        wb = (sum(x.wall_seconds for x in rb_list) / len(rb_list)) if rb_list else 0.0
        out.append(
            f"| `{arm}` | {ma:.1f}% | {mb:.1f}% | **{mb - ma:+.1f}%** | "
            f"{sa:.1f}% | {sb:.1f}% | **{sb - sa:+.1f}%** | `{wb - wa:+.1f}s` |"
        )
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the canonical step-by-step SciCode (scicode-bench/SciCode) 3-arm local Ollama benchmark."
    )
    parser.add_argument("--split", choices=["validation", "test"], default="validation")
    parser.add_argument(
        "--with-background",
        action="store_true",
        help="Include scientist-annotated background (default False = Standard SciCode leaderboard setup).",
    )
    parser.add_argument("--mode", choices=["normal", "gold", "dummy"], default="normal")
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    parser.add_argument("--arms", default=",".join(ALL_ARMS))
    parser.add_argument(
        "--preset",
        choices=["full", "quick"],
        default="full",
        help="'quick' runs a stratified 3-problem (4-subproblem) validation slice (IDs 1, 4, 19) in ~30 min.",
    )
    parser.add_argument("--problem-ids", default="", help="Optional comma-separated problem IDs.")
    parser.add_argument("--limit", type=int, default=0, help="Optional cap on problems (0 = full split).")
    parser.add_argument("--model", default="", help="Optional Engineer Ollama model override.")
    parser.add_argument("--temperature", type=float, default=None, help="Optional sampling temperature override.")
    parser.add_argument("--h5py-file", default="", help="Optional path to official test_data.h5.")
    parser.add_argument("--resume", default="", help="Existing run directory to resume.")
    parser.add_argument(
        "--compare-runs",
        nargs=2,
        metavar=("RUN_DIR_A", "RUN_DIR_B"),
        help="Compare two completed run directories and print a regression delta table.",
    )
    args = parser.parse_args(argv)

    if args.compare_runs:
        dir_a, dir_b = Path(args.compare_runs[0]).resolve(), Path(args.compare_runs[1]).resolve()
        rows_a = list(load_completed_keys(dir_a / "results.jsonl").values())
        rows_b = list(load_completed_keys(dir_b / "results.jsonl").values())
        print(render_run_comparison_markdown(rows_a, rows_b, label_a=dir_a.name, label_b=dir_b.name))
        return 0

    problems, dataset_sha = load_scicode_split(args.split)
    h5_path = Path(args.h5py_file).resolve() if args.h5py_file else ensure_scicode_h5()

    selected = list(problems)
    if args.preset == "quick" and not args.problem_ids.strip():
        selected = [p for p in selected if str(p["problem_id"]) in set(QUICK_VALIDATION_IDS)]
    if args.problem_ids.strip():
        wanted = {x.strip() for x in args.problem_ids.split(",") if x.strip()}
        selected = [p for p in selected if str(p["problem_id"]) in wanted]
    if args.limit > 0:
        selected = selected[: args.limit]

    arms = (
        [f"scicode-{args.mode}"]
        if args.mode in ("gold", "dummy")
        else [a.strip() for a in args.arms.split(",") if a.strip() in ALL_ARMS]
    )
    run_dir = (
        Path(args.resume).resolve()
        if args.resume
        else (DEFAULT_RUNS_DIR / datetime.datetime.now().strftime(f"scicode_{args.split}_%Y%m%d_%H%M%S"))
    )
    run_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = run_dir / "results.jsonl"
    completed = load_completed_keys(jsonl_path)
    model_ov = args.model.strip() or None

    # Step through `selected` (`prob`) to update or verify each item.
    for prob in selected:
        pid = str(prob["problem_id"])
        pname = str(prob["problem_name"])
        # Step through `arms` (`arm`) to update or verify each item.
        for arm in arms:
            if (pid, arm) in completed:
                print(f"[skip] SciCode {pid} ({pname}) :: arm={arm} already scored", flush=True)
                continue
            print(f"[run] SciCode {pid} ({pname}, {len(prob['sub_steps'])} steps) :: arm={arm} ...", flush=True)
            row = run_arm_on_problem(
                prob,
                arm=arm,
                mode=args.mode,
                with_background=args.with_background,
                h5py_file=str(h5_path),
                arm_root=run_dir / arm,
                ollama_url=args.ollama_url,
                model_override=model_ov,
                temperature=args.temperature,
            )
            append_checkpoint_row(jsonl_path, row)
            completed[(pid, arm)] = row
            print(
                f"      => {'PASS' if row.main_passed else 'FAIL'} "
                f"(subproblems={row.substeps_passed}/{row.substeps_total}, wall={row.wall_seconds:.1f}s, repairs={row.repair_attempts})",
                flush=True,
            )

    tot_probs = len(selected)
    tot_steps = sum(
        sum(1 for idx in range(len(p["sub_steps"])) if not is_skipped_scicode_step(str(p["problem_id"]), idx))  # type: ignore[arg-type]
        for p in selected
    )
    results_md = render_results_markdown(
        list(completed.values()),
        dataset_sha=dataset_sha,
        split=args.split,
        with_background=args.with_background,
        total_benchmark_problems=tot_probs,
        total_benchmark_steps=tot_steps,
    )
    (run_dir / "RESULTS.md").write_text(results_md, encoding="utf-8")
    print(f"\nWrote canonical SciCode scorecard to {run_dir / 'RESULTS.md'}", flush=True)
    return 0


# Raise `SystemExit` immediately if `__name__ == '__main__'`.
if __name__ == "__main__":
    raise SystemExit(main())
