#!/usr/bin/env python3
"""
NEXTFLOW PIPELINE VALIDATOR

Validates generated Nextflow code by:
1. Syntax check (nextflow -preview)
2. Stub run (nextflow run -stub) - tests workflow logic without execution
3. Full run (nextflow run) - requires test data

Usage:
    # From API response
    python validate_pipeline.py --code "nextflow code here"

    # From file
    python validate_pipeline.py --file generated_pipeline.nf

    # Interactive test with API
    python validate_pipeline.py --prompt "I want to do MLST on bacterial samples"

    # Stub run (test workflow logic)
    python validate_pipeline.py --file pipeline.nf --stub
"""

import argparse
import subprocess
import tempfile
import os
import sys
import json
import requests
from pathlib import Path
import re
from typing import TypedDict

class ErrorPattern(TypedDict):
    pattern: re.Pattern
    category: str
    label: str
    is_fatal: bool
    groups: list[str]
    source: str 

# Framework directory where includes are resolved
FRAMEWORK_DIR = Path(os.getenv("NGSMANAGER_DIR", "../cohesive-ngsmanager-cli/cohesive-ngsmanager")).resolve()
API_URL = "http://localhost:8080"
E2E_PARAMS_CONFIG = Path(__file__).parent / "test_e2e_params.config"

# Some of the  error patterns are referenced from:
# https://training.nextflow.io/2.7.0/side_quests/debugging
# ============================================================
# CATEGORY 1 - SYNTAX / COMPILATION ERRORS
# ============================================================

SYNTAX_PATTERNS: list[ErrorPattern] = [

    # 1a. Script compilation error header
    # Example:
    # ERROR ~ Script compilation error
    # ERROR ~ Script compilation failed
    {
        "pattern": re.compile(r"ERROR\s*~\s*Script compilation (?:error|failed)"),
        "category": "script_compilation_error",
        "label": "Script compilation error",
        "is_fatal": True,
        "groups": [],
        "source": "ERROR ~ Script compilation error",
    },

    # 1b. Cause line inside compilation block
    # Example:
    # - cause: Unexpected input: '{' @ line 3, column 23.
    {
        "pattern": re.compile(r"-\s*cause:\s*(.+)"),
        "category": "compilation_cause",
        "label": "Compilation error cause",
        "is_fatal": True,
        "groups": ["cause_text"],
        "source": "- cause: Unexpected input: '{' @ line 3, column 23.",
    },

    # 1c. Unexpected parser input
    # Example:
    # Unexpected input: '{' @ line 3, column 23
    # Unexpected input: '<EOF>'
    {
        "pattern": re.compile(
            r"Unexpected input:\s*['\"]?(.+?)['\"]?(?:\s*@\s*line\s*(\d+),?\s*column\s*(\d+))?"
        ),
        "category": "syntax_unexpected_input",
        "label": "Unexpected input token",
        "is_fatal": True,
        "groups": ["token", "line_number", "column_number"],
        "source": "Unexpected input: '{' @ line 3, column 23",
    },

    # 1d. Invalid process keyword
    # Example:
    # Invalid process definition -- Unknown keyword `inputs`
    {
        "pattern": re.compile(
            r"Invalid process definition\s*--\s*Unknown keyword\s*[`'\"]?(\w+)[`'\"]?"
        ),
        "category": "syntax_invalid_process_keyword",
        "label": "Invalid process keyword",
        "is_fatal": True,
        "groups": ["keyword"],
        "source": "Invalid process definition -- Unknown keyword `inputs`",
    },

    # 1e. Undefined variable
    # Example:
    # No such variable: prefix -- Check script 'bad_bash_var.nf' at line: 11
    {
        "pattern": re.compile(r"No such variable:\s*(\w+)"),
        "category": "undefined_variable",
        "label": "Undefined variable",
        "is_fatal": True,
        "groups": ["variable_name"],
        "source": "No such variable: prefix",
    },

    # 1f. Compilation error count
    # Example:
    # 1 error
    # 2 errors
    {
        "pattern": re.compile(r"\b(\d+)\s+errors?\b"),
        "category": "compilation_error_count",
        "label": "Compilation error count",
        "is_fatal": True,
        "groups": ["count"],
        "source": "1 error",
    },

    # 1g. Syntax NOTE hint
    # Example:
    # NOTE: If this is the beginning of a process or workflow...
    {
        "pattern": re.compile(
            r"NOTE:\s*If this is the beginning of a process or workflow"
        ),
        "category": "syntax_note_body_error",
        "label": "Possible process/workflow body syntax issue",
        "is_fatal": False,
        "groups": [],
        "source": "NOTE: If this is the beginning of a process or workflow...",
    },

    # 1h. Missing component in module
    # Example:
    # Cannot find a component with name 'extractKe' in module: /path/module.nf
    {
        "pattern": re.compile(
            r"Cannot find a component with name\s+[`'\"]?([^`'\"]+)[`'\"]?\s+in module:\s+([^\n]+)"
        ),
        "category": "module_missing_component",
        "label": "Component not found in module",
        "is_fatal": True,
        "groups": ["component_name", "module_path"],
        "source": "Cannot find a component with name 'extractKe' in module: /path/module.nf",
    },

    # 1i. Module include file not found (invalid path or typo)
    # Example:
    # "ERROR ~ No such file or directory: Can't find a matching module file for include: ../functions/commn.nf"
    {
        "pattern": re.compile(
            r"Can't find a matching module file for include:\s+([^\n]+)"
        ),
        "category": "module_include_not_found",
        "label": "Module include failed: file not found or path incorrect",
        "is_fatal": True,
        "groups": ["include_path"],
        "source": "Can't find a matching module file for include: ../functions/commn.nf",
    },
]


# ============================================================
# CATEGORY 2 - CHANNEL STRUCTURE ERRORS
# ============================================================

CHANNEL_PATTERNS: list[ErrorPattern] = [

    # 2a. Wrong number of input channels
    # Example:
    # Process `PROCESS_FILES` declares 1 input channel but 2 were specified
    {
        "pattern": re.compile(
            r"Process\s+[`'\"]?(\w+)[`'\"]?\s+declares\s+(\d+)\s+input channels?\s+but\s+(\d+)\s+were specified"
        ),
        "category": "channel_count_mismatch",
        "label": "Incorrect number of input channels",
        "is_fatal": True,
        "groups": ["process_name", "expected_count", "provided_count"],
        "source": "Process `PROCESS_FILES` declares 1 input channel but 2 were specified",
    },

    # 2b. Missing output file due to tuple mis-shape
    # Example:
    # Missing output file(s) `[sample1, file1.txt]_output.txt` expected by process `PROCESS_FILES (1)`
    {
        "pattern": re.compile(
            r"Missing output file\(s\)\s+`(\[[^\]]+\][^`]*)`\s+expected by process\s+[`'\"]?(.+?)[`'\"]?"
        ),
        "category": "channel_shape_mismatch",
        "label": "Channel structure mismatch",
        "is_fatal": True,
        "groups": ["expected_file", "process_name"],
        "source": "Missing output file(s) `[sample1, file1.txt]_output.txt` expected by process `PROCESS_FILES (1)`",
    },

    # 2c. Null path emitted by channel
    # Example:
    # Path value cannot be null
    {
        "pattern": re.compile(r"Path value cannot be null"),
        "category": "channel_null_value",
        "label": "Channel emitted null path value",
        "is_fatal": True,
        "groups": [],
        "source": "Path value cannot be null",
    },
]


# ============================================================
# CATEGORY 3 - PROCESS EXECUTION ERRORS
# ============================================================

PROCESS_PATTERNS: list[ErrorPattern] = [

    # 3a. Process execution failure header
    # Example:
    # ERROR ~ Error executing process > 'PROCESS_FILES (3)'
    {
        "pattern": re.compile(
            r"ERROR\s*~\s*Error executing process\s*>\s*[`'\"]?(.+?)[`'\"]?"
        ),
        "category": "process_execution_error",
        "label": "Process execution error",
        "is_fatal": True,
        "groups": ["process_name"],
        "source": "ERROR ~ Error executing process > 'PROCESS_FILES (3)'",
    },

    # 3b. Missing expected output file
    # Example:
    # Missing output file(s) `sample3.txt` expected by process `PROCESS_FILES (3)`
    {
        "pattern": re.compile(
            r"Missing output file\(s\)\s+`([^`]+)`\s+expected by process\s+[`'\"]?(.+?)[`'\"]?"
        ),
        "category": "process_missing_output",
        "label": "Missing expected output file",
        "is_fatal": True,
        "groups": ["expected_file", "process_name"],
        "source": "Missing output file(s) `sample3.txt` expected by process `PROCESS_FILES (3)`",
    },

    # 3c. Process exited with non-zero exit code
    # Example:
    # Process `PROCESS_FILES (3)` terminated with an error exit status (127)
    {
        "pattern": re.compile(
            r"Process\s+[`'\"]?(.+?)[`'\"]?\s+terminated with an error exit status\s+\((\d+)\)"
        ),
        "category": "process_nonzero_exit",
        "label": "Process exited with non-zero status",
        "is_fatal": True,
        "groups": ["process_name", "exit_code"],
        "source": "Process `PROCESS_FILES (3)` terminated with an error exit status (127)",
    },

    # 3d. Command not found inside process script
    # Example:
    # .command.sh: line 2: cowpy: command not found
    {
        "pattern": re.compile(
            r"\.command\.sh:\s*line\s*(\d+):\s*(\w+):\s*command not found"
        ),
        "category": "process_command_not_found",
        "label": "Command not found in process script",
        "is_fatal": True,
        "groups": ["line_number", "command"],
        "source": ".command.sh: line 2: cowpy: command not found",
    },

    # 3e. Generic command not found
    # Example:
    # cowpy: command not found
    {
        "pattern": re.compile(r"(\w+):\s*command not found"),
        "category": "process_command_not_found",
        "label": "Command not found",
        "is_fatal": True,
        "groups": ["command"],
        "source": "cowpy: command not found",
    },

    # 3f. Process exceeded runtime limit
    # Example:
    # Process exceeded running time limit (1ms)
    {
        "pattern": re.compile(r"Process exceeded running time limit\s*\(([^)]+)\)"),
        "category": "process_time_limit_exceeded",
        "label": "Process exceeded time limit",
        "is_fatal": True,
        "groups": ["limit"],
        "source": "Process exceeded running time limit (1ms)",
    },

    # 3g. Out-of-memory termination (common cluster message)
    {
        "pattern": re.compile(
            r"(?:Process|Task)\s+(?:killed|terminated).*?(?:out of memory|OOM|exit.*?137)"
        ),
        "category": "process_oom",
        "label": "Process killed due to out-of-memory",
        "is_fatal": True,
        "groups": [],
        "source": "Process killed: out of memory (exit 137)",
    },

    # 3h. Caused by block
    # Example:
    # Caused by:
    #   Missing output file(s) `sample3.txt`
    {
        "pattern": re.compile(r"Caused by:\s*(.+)"),
        "category": "process_caused_by",
        "label": "Process error cause",
        "is_fatal": True,
        "groups": ["cause_text"],
        "source": "Caused by: Missing output file(s)...",
    },
]
 
# ============================================================
# CATEGORY 4 - NON-FATAL / EXPECTED NOISE  (is_fatal=False)
# ============================================================
 
NOISE_PATTERNS: list[ErrorPattern] = [
 
    # 4a. WARN: file not found (very common in -preview)
    # "WARN: file not found: '/home/zeynull/.../result/*.fastq*'"
    {
        "pattern": re.compile(
            r"(?:WARN|WARNING)[:\s]+file not found[:\s]+['\"]?(.+?)['\"]?\s*$"
        ),
        "category": "warn_file_not_found",
        "label": "File not found warning (expected in -preview with no real data)",
        "is_fatal": False,
        "groups": ["path_glob"],
        "source": "WARN: file not found: '/home/zeynull/.../result/*.fastq*'",
    },
 
    # 4b. Channel defined outside workflow block (non-fatal warning)
    # Docs Section 1.5: runs fine but is bad practice; will be enforced in future NF versions.
    {
        "pattern": re.compile(
            r"(?:WARN|WARNING)[:\s]+.*?channel.*?(?:outside|defined outside).*?workflow(.*)$",
            re.IGNORECASE,
        ),
        "category": "warn_channel_outside_workflow",
        "label": "Channel defined outside workflow block (bad practice, non-fatal today)",
        "is_fatal": False,
        "groups": ["detail"],
        "source": "WARN: channel defined outside workflow block",
    },
 
    # 4c. Missing required parameter
    {
        "pattern": re.compile(
            r"[Mm]issing\s+(?:required\s+)?(?:param(?:eter)?)[:\s]+[-\s]*['\"]?(\w+)['\"]?"
        ),
        "category": "missing_param",
        "label": "Missing pipeline parameter (expected in -preview)",
        "is_fatal": False,
        "groups": ["param_name"],
        "source": "Missing required parameter: --genome",
    },
 
    # 4d. Param should be provided
    {
        "pattern": re.compile(
            r"param(?:eter)?\s+should be provided[:\s]+['\"]?(\w+)['\"]?"
        ),
        "category": "missing_param",
        "label": "Parameter should be provided (expected in -preview)",
        "is_fatal": False,
        "groups": ["param_name"],
        "source": "param should be provided: genome",
    },
 
    # 4e. Could not find reference
    {
        "pattern": re.compile(
            r"[Cc]ould not find\s+(?:reference|genome)[:\s]+['\"]?(.+?)['\"]?\s*$"
        ),
        "category": "missing_reference",
        "label": "Reference genome not found (expected in -preview)",
        "is_fatal": False,
        "groups": ["reference_name"],
        "source": "Could not find reference genome: hg38",
    },
 
    # 4f. No reference provided
    {
        "pattern": re.compile(r"No reference provided(.*)"),
        "category": "missing_reference",
        "label": "No reference genome provided (expected in -preview)",
        "is_fatal": False,
        "groups": ["detail"],
        "source": "No reference provided",
    },
 
    # 4g. Generic input file not found (pipeline data, not code)
    {
        "pattern": re.compile(
            r"[Ii]nput file not found[:\s]+['\"]?(.+?)['\"]?\s*$"
        ),
        "category": "missing_input_file",
        "label": "Input data file not found (expected in -preview)",
        "is_fatal": False,
        "groups": ["path"],
        "source": "Input file not found: /data/sample.fastq",
    },
 
    # 4h. nf-core style "not found: <item>"
    {
        "pattern": re.compile(r"\bnot found:\s*['\"]?(\S+)['\"]?\s*$"),
        "category": "missing_input_file",
        "label": "Generic 'not found' (likely input data, expected in -preview)",
        "is_fatal": False,
        "groups": ["item"],
        "source": "not found: samplesheet.csv",
    },
]


# ============================================================
# MULTI-LINE / TRAILER PATTERNS
# Applied to the FULL combined output, not line-by-line.
# ============================================================
 
LOCATION_TRAILER = re.compile(
    r"--\s+Check script\s+['\"]([^'\"]+)['\"]\s+at line[:\s]+(\d+)",
    re.MULTILINE,
)
 
FILE_LOCATION = re.compile(
    r"-\s*file\s*:\s*(.+?)\s*$",
    re.MULTILINE,
)
 
SUGGESTION_TRAILER = re.compile(
    r"Did you mean(?: any of these)?\?\s*(?:\n((?:\s{2,}[A-Za-z0-9_]+\s*\n?)+)|['\"]([^'\"]+)['\"]\s*instead\?)",
    re.MULTILINE,
)
 
NF_LOG_HINT = re.compile(
    r"see\s+['\"]?\.nextflow\.log['\"]?\s+file for (?:more )?details",
    re.MULTILINE | re.IGNORECASE,
)
 
LINE_PATTERNS: list[ErrorPattern] = (
    CHANNEL_PATTERNS
    + SYNTAX_PATTERNS
    + PROCESS_PATTERNS
    + NOISE_PATTERNS
)

def extract_text_blocks(lines):
    """
    Extract all text blocks, each block is separated by at least one empty line
    (or line with only spaces). Returns a list of strings (blocks).
    """
    blocks = []
    current_block = []

    for line in lines:
        if line.strip() == "":
            if current_block:
                blocks.append("\n".join(current_block))
                current_block = []
        else:
            current_block.append(line)

    if current_block:
        blocks.append("\n".join(current_block))

    return blocks

def parse_nextflow_output(stdout: str, stderr: str) -> dict:
    """
    Parse Nextflow stdout + stderr and return structured error info.
 
    Returns
    -------
    {
      "fatal_errors"    : list of matched fatal error dicts,
      "noise_errors"    : list of matched non-fatal / expected dicts,
      "unmatched_errors": list of raw lines that looked like errors but were not matched,
      "script_location" : {"file": str, "line": int} | None,
      "file_location"   : str | None,
      "suggestion"      : str | None,
      "nf_log_hint"     : bool,
    }
    """
    combined = stderr + "\n" + stdout
    fatal_errors:     list[dict] = []
    noise_errors:     list[dict] = []
    unmatched_errors: list[str]  = []
 
    for raw_line in combined.splitlines():
        line = raw_line.strip()
        if not line:
            continue
 
        matched = False
        for ep in LINE_PATTERNS:
            m = ep["pattern"].search(line)
            if m:
                entry = {
                    "category":  ep["category"],
                    "label":     ep["label"],
                    "is_fatal":  ep["is_fatal"],
                    "raw":       line[:400],
                    "captures":  dict(zip(ep["groups"], m.groups())),
                }
                if ep["is_fatal"]:
                    fatal_errors.append(entry)
                else:
                    noise_errors.append(entry)
                matched = True
                break
 
        if not matched:
            # Skip bare section headers that carry no actionable info on their own
            # e.g. "Caused by:" or "Command error:" with no trailing text
            if line in ("Caused by:", "Command error:", "Command output:", "Command executed:"):
                continue
            if any(kw in line for kw in (
                "ERROR", "Error:", "error:", "Exception",
                "FAILED", "Caused by", "terminated",
            )):
                unmatched_errors.append(line[:400])
 
    # Extract script/file locations as before
    loc_m  = LOCATION_TRAILER.search(combined)
    file_m = FILE_LOCATION.search(combined)

    script_location = (
        {"file": loc_m.group(1), "line": int(loc_m.group(2))}
        if loc_m else None
    )
    file_location = file_m.group(1).strip() if file_m else None

    # Use block extraction for suggestions
    lines = combined.splitlines()
    output_blocks = extract_text_blocks(lines)
    suggestions = [block for block in output_blocks if "Did you mean" in block]
    suggestions = suggestions if suggestions else None

    # Flag for .nextflow.log hint
    nf_log_hint = bool(NF_LOG_HINT.search(combined))

    return {
        "fatal_errors":     fatal_errors,
        "noise_errors":     noise_errors,
        "unmatched_errors": unmatched_errors,
        "script_location":  script_location,
        "file_location":    file_location,
        "output_blocks":    output_blocks,
        "suggestions":      suggestions,
        "nf_log_hint":      nf_log_hint,
    }

def check_syntax(code: str) -> dict:
    """Level 1: Syntax validation only"""

    # Create temp file directly in pipelines/ so ../functions/ works
    test_dir = FRAMEWORK_DIR / "pipelines"
    test_file = test_dir / "_llm_test_pipeline.nf"

    try:
        test_file.write_text(code)

        # Run nextflow syntax check
        result = subprocess.run(
            ["nextflow", "run", str(test_file), "-preview", "-c", str(E2E_PARAMS_CONFIG)],
            capture_output=True,
            text=True,
            cwd=str(FRAMEWORK_DIR),
            timeout=30
        )

        success = result.returncode == 0

        return {
            "level": "syntax",
            "success": success,
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }

    except subprocess.TimeoutExpired:
        return {"level": "syntax", "success": False, "errors": ["Timeout after 30s"]}
    except Exception as e:
        return {"level": "syntax", "success": False, "errors": [str(e)]}
    finally:
        if test_file.exists():
            test_file.unlink()


def check_stub(code: str) -> dict:
    """Level 2: Stub run - tests workflow logic without executing processes"""

    test_dir = FRAMEWORK_DIR / "pipelines"
    test_file = test_dir / "_llm_test_pipeline.nf"
    work_dir = FRAMEWORK_DIR / "_llm_test_work"

    try:
        test_file.write_text(code)

        # Run with -stub flag
        result = subprocess.run(
            [
                "nextflow", "run", str(test_file),
                "-stub",
                "-work-dir", str(work_dir),
                "--outdir", str(test_dir / "output")
            ],
            capture_output=True,
            text=True,
            cwd=str(FRAMEWORK_DIR),
            timeout=60
        )

        success = result.returncode == 0

        return {
            "level": "stub",
            "success": success,
            "returncode": result.returncode,
            "stdout": result.stdout[-2000:] if len(result.stdout) > 2000 else result.stdout,
            "stderr": result.stderr[-2000:] if len(result.stderr) > 2000 else result.stderr,
        }

    except subprocess.TimeoutExpired:
        return {"level": "stub", "success": False, "errors": ["Timeout after 60s"]}
    except Exception as e:
        return {"level": "stub", "success": False, "errors": [str(e)]}
    finally:
        # Cleanup
        if test_file.exists():
            test_file.unlink()
        if work_dir.exists():
            subprocess.run(["rm", "-rf", str(work_dir)], capture_output=True)

def build_llm_feedback(parsed: dict) -> str:
    """
    Convert parse_nextflow_output() result into a compact, actionable string
    the repair LLM can act on directly.
    Noise errors and log hints are intentionally excluded.
    """
    parts: list[str] = []
 
    if parsed["file_location"]:
        parts.append(f"File: {parsed['file_location']}")
 
    if parsed["script_location"]:
        loc = parsed["script_location"]
        parts.append(f"Error location: {loc['file']} at line {loc['line']}")
 
    if parsed["suggestions"]:
        parts.append(f"Nextflow suggestions: '{', '.join(parsed['suggestions'])}'")
 
    if parsed["fatal_errors"]:
        parts.append("\nCODE ERRORS TO FIX:")
        for e in parsed["fatal_errors"]:
            caps = ", ".join(
                f"{k}={v!r}" for k, v in e["captures"].items() if v
            )
            header = f"  [{e['category']}] {e['label']}"
            if caps:
                header += f" ({caps})"
            parts.append(header)
            parts.append(f"    -> {e['raw']}")
 
    if parsed["unmatched_errors"]:
        parts.append("\nUNCATEGORISED ERRORS (review manually):")
        for raw in parsed["unmatched_errors"]:
            parts.append(f"  {raw}")
 
    return "\n".join(parts)
 

def get_code_from_api(prompt: str, verbose: bool = False) -> str:
    """Get generated code from API"""
    import time

    session_id = f"validate_{os.getpid()}_{int(time.time())}"

    # Send initial prompt
    response = requests.post(
        f"{API_URL}/chat",
        json={"session_id": session_id, "message": prompt}
    )

    if response.status_code != 200:
        raise Exception(f"API error: {response.status_code} - {response.text}")

    data = response.json()
    if verbose:
        print(f"  [Turn 1] Status: {data.get('status')}, Code: {'Yes' if data.get('nextflow_code') else 'No'}")

    # Keep chatting until we get code or give up
    max_turns = 5
    for i in range(2, max_turns + 1):
        if data.get("nextflow_code"):
            return data["nextflow_code"]

        status = data.get("status", "")

        if status == "APPROVED":
            # Plan approved, code should be generated
            # Sometimes needs a follow-up to trigger generation
            response = requests.post(
                f"{API_URL}/chat",
                json={"session_id": session_id, "message": "generate the pipeline"}
            )
        elif status == "CHATTING":
            # Approve the plan
            response = requests.post(
                f"{API_URL}/chat",
                json={"session_id": session_id, "message": "yes, that looks good, please proceed"}
            )
        else:
            break

        if response.status_code != 200:
            raise Exception(f"API error on turn {i}: {response.status_code}")

        data = response.json()
        if verbose:
            print(f"  [Turn {i}] Status: {data.get('status')}, Code: {'Yes' if data.get('nextflow_code') else 'No'}")

    if data.get("nextflow_code"):
        return data["nextflow_code"]

    raise Exception(f"No code generated after {max_turns} turns. Last status: {data.get('status')}, Reply: {data.get('reply', '')[:200]}")

def print_result(result: dict, verbose: bool = False):
    """Pretty print validation result"""

    level = result.get("level", "unknown")
    success = result.get("success", False)

    status = "✅ PASS" if success else "❌ FAIL"
    print(f"\n{'='*60}")
    print(f"  {level.upper()} VALIDATION: {status}")
    print(f"{'='*60}")

    if result.get("errors"):
        print("\nErrors:")
        for err in result["errors"]:
            print(f"  - {err}")

    if verbose and result.get("stderr"):
        print("\nStderr:")
        print(result["stderr"])

    if verbose and result.get("stdout"):
        print("\nStdout:")
        print(result["stdout"])

def main():
    parser = argparse.ArgumentParser(description="Validate Nextflow pipelines")

    input_group = parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument("--code", help="Nextflow code string")
    input_group.add_argument("--file", help="Path to .nf file")
    input_group.add_argument("--prompt", help="Generate code from API prompt")

    parser.add_argument("--stub", action="store_true", help="Run stub test (level 2)")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show full output")
    parser.add_argument("--json", action="store_true", help="Output as JSON")

    args = parser.parse_args()

    # Get code
    if args.code:
        code = args.code
    elif args.file:
        code = Path(args.file).read_text()
    elif args.prompt:
        print(f"Generating code for: {args.prompt}")
        try:
            code = get_code_from_api(args.prompt, verbose=args.verbose)
            print(f"Generated {len(code)} chars of code")
        except Exception as e:
            print(f"Failed to get code from API: {e}")
            sys.exit(1)

    # Validate
    results = []

    # Level 1: Syntax
    print("\n[1/2] Checking syntax...")
    syntax_result = check_syntax(code)
    results.append(syntax_result)

    if not args.json:
        print_result(syntax_result, args.verbose)

    # Level 2: Stub (optional)
    if args.stub and syntax_result["success"]:
        print("\n[2/2] Running stub test...")
        stub_result = check_stub(code)
        results.append(stub_result)

        if not args.json:
            print_result(stub_result, args.verbose)

    # Output
    if args.json:
        print(json.dumps(results, indent=2))
    else:
        print("\n" + "="*60)
        all_passed = all(r["success"] for r in results)
        print(f"  OVERALL: {'✅ ALL TESTS PASSED' if all_passed else '❌ SOME TESTS FAILED'}")
        print("="*60)

    # print(parse_nextflow_output(syntax_result['stdout'], syntax_result['stderr']))

    print(syntax_result['stdout'])
    print("\nLLM FEEDBACK DRAFT:")
    print(build_llm_feedback(
        parse_nextflow_output(syntax_result['stdout'], syntax_result['stderr'])
    ))

    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    main()
