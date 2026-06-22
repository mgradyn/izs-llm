"""
Nextflow .nf Parser — Extracts process/workflow definitions from Nextflow DSL2 files.

Parses:
  - Process definitions (name, container, input/output channels, script)
  - Workflow definitions (name, take/emit channels, body)
  - Include statements

Usage:
    from ingestion.parser import parse_nf_file
    components = parse_nf_file(Path("steps/step_1PP_trimming__fastp.nf"))
"""

import re
from pathlib import Path


def parse_nf_file(filepath: Path) -> dict:
    """Parse a single .nf file and extract process/workflow definitions.

    Returns:
        {
            "file": str,
            "processes": [{name, container, inputs, outputs, script}],
            "workflows": [{name, takes, emits, body, includes}],
            "includes": [str],
            "raw_code": str,
        }
    """
    code = filepath.read_text(encoding="utf-8", errors="replace")

    return {
        "file": str(filepath),
        "file_stem": filepath.stem,
        "processes": _extract_processes(code),
        "workflows": _extract_workflows(code),
        "includes": _extract_includes(code),
        "raw_code": code,
    }


def _extract_processes(code: str) -> list[dict]:
    """Extract process definitions from Nextflow DSL2 code."""
    processes = []

    # Match: process PROCESS_NAME { ... }
    # Use a brace-counting approach for nested blocks
    proc_starts = list(re.finditer(r'\bprocess\s+(\w+)\s*\{', code))

    for match in proc_starts:
        name = match.group(1)
        start_pos = match.end()
        body = _extract_balanced_block(code, start_pos - 1)

        if not body:
            continue

        proc = {
            "name": name,
            "container": _extract_container(body),
            "inputs": _extract_input_channels(body),
            "outputs": _extract_output_channels(body),
            "script": _extract_script_block(body),
        }
        processes.append(proc)

    return processes


def _extract_workflows(code: str) -> list[dict]:
    """Extract workflow definitions from Nextflow DSL2 code."""
    workflows = []

    # Named workflows: workflow WORKFLOW_NAME { ... }
    wf_starts = list(re.finditer(r'\bworkflow\s+(\w+)\s*\{', code))

    for match in wf_starts:
        name = match.group(1)
        start_pos = match.end()
        body = _extract_balanced_block(code, start_pos - 1)

        if not body:
            continue

        wf = {
            "name": name,
            "takes": _extract_take_channels(body),
            "emits": _extract_emit_channels(body),
            "body": _extract_main_block(body),
            "includes": _extract_workflow_calls(body),
        }
        workflows.append(wf)

    # Unnamed (entry) workflow
    entry_match = re.search(r'(?<!\w)workflow\s*\{', code)
    if entry_match:
        body = _extract_balanced_block(code, entry_match.end() - 1)
        if body:
            workflows.append({
                "name": "__entrypoint__",
                "takes": [],
                "emits": [],
                "body": body,
                "includes": _extract_workflow_calls(body),
            })

    return workflows


def _extract_includes(code: str) -> list[str]:
    """Extract include statements: include { step_xxx } from '...'"""
    includes = []
    for match in re.finditer(r"include\s*\{([^}]+)\}\s*from", code):
        block = match.group(1)
        for item in block.split(';'):
            item = item.strip()
            name = item.split(' as ')[0].strip() if ' as ' in item else item.strip()
            if name and re.match(r'^[a-zA-Z_]\w*$', name):
                includes.append(name)
    return includes


# ── Block extraction helpers ─────────────────────────────────────────────────

def _extract_balanced_block(code: str, open_brace_pos: int) -> str | None:  # noqa: C901
    """Extract content between balanced braces starting at open_brace_pos.
    Ignores braces inside single/double/triple quotes and comments.
    """
    if open_brace_pos >= len(code) or code[open_brace_pos] != '{':
        return None

    depth = 0
    in_single = False
    in_double = False
    in_triple_single = False
    in_triple_double = False
    in_line_comment = False
    in_block_comment = False

    i = open_brace_pos
    while i < len(code):
        char = code[i]

        # Handle state exits
        if in_line_comment:
            if char == '\n':
                in_line_comment = False
            i += 1
            continue

        if in_block_comment:
            if char == '*' and i + 1 < len(code) and code[i+1] == '/':
                in_block_comment = False
                i += 2
                continue
            i += 1
            continue

        if in_triple_single:
            if char == '\\':
                i += 2
                continue
            if code[i:i+3] == "'''":
                in_triple_single = False
                i += 3
                continue
            i += 1
            continue

        if in_triple_double:
            if char == '\\':
                i += 2
                continue
            if code[i:i+3] == '"""':
                in_triple_double = False
                i += 3
                continue
            i += 1
            continue

        if in_single:
            if char == '\\':
                i += 2
                continue
            if char == "'":
                in_single = False
            i += 1
            continue

        if in_double:
            if char == '\\':
                i += 2
                continue
            if char == '"':
                in_double = False
            i += 1
            continue

        # Handle state entrances
        if code[i:i+2] == '//':
            in_line_comment = True
            i += 2
            continue
        if code[i:i+2] == '/*':
            in_block_comment = True
            i += 2
            continue
        if code[i:i+3] == "'''":
            in_triple_single = True
            i += 3
            continue
        if code[i:i+3] == '"""':
            in_triple_double = True
            i += 3
            continue
        if char == "'":
            in_single = True
            i += 1
            continue
        if char == '"':
            in_double = True
            i += 1
            continue

        # Count braces only in code space
        if char == '{':
            depth += 1
        elif char == '}':
            depth -= 1
            if depth == 0:
                return code[open_brace_pos + 1:i]

        i += 1

    return None


def _extract_container(process_body: str) -> str | None:
    """Extract container directive from a process body."""
    match = re.search(r'container\s+["\']([^"\']+)["\']', process_body)
    return match.group(1) if match else None


def _extract_input_channels(process_body: str) -> list[str]:
    """Extract input channel declarations from a process body."""
    channels = []
    input_match = re.search(r'\binput\s*:(.*?)(?=\boutput\s*:|\bscript\s*:|\bshell\s*:|\bexec\s*:|\Z)',
                            process_body, re.DOTALL)
    if input_match:
        for line in input_match.group(1).strip().split('\n'):
            line = line.strip()
            if line and not line.startswith('//'):
                # Extract channel type and name: val(x), path(x), tuple val(x), path(y)
                names = re.findall(r'(?:val|path|file|env|stdin)\s*\(?\s*(\w+)', line)
                if names:
                    channels.extend(names)
                elif line and not line.startswith('*'):
                    channels.append(line.split('//')[0].strip())
    return channels


def _extract_output_channels(process_body: str) -> list[str]:
    """Extract output channel declarations from a process body."""
    channels = []
    output_match = re.search(r'\boutput\s*:(.*?)(?=\bscript\s*:|\bshell\s*:|\bexec\s*:|\bwhen\s*:|\Z)',
                             process_body, re.DOTALL)
    if output_match:
        for line in output_match.group(1).strip().split('\n'):
            line = line.strip()
            if line and not line.startswith('//'):
                # Emit labels: emit: channel_name
                emit_match = re.search(r'emit\s*:\s*(\w+)', line)
                if emit_match:
                    channels.append(emit_match.group(1))
                else:
                    names = re.findall(r'(?:val|path|file|env|stdout)\s*\(?\s*(\w+)', line)
                    if names:
                        channels.extend(names)
    return channels


def _extract_script_block(process_body: str) -> str:
    """Extract the script/shell block from a process body."""
    match = re.search(r'(?:script|shell)\s*:\s*\n?\s*(?:"""|\'\'\')\s*\n?(.*?)(?:"""|\'\'\')',
                      process_body, re.DOTALL)
    if match:
        return match.group(1).strip()

    # Single-line script
    match = re.search(r'(?:script|shell)\s*:\s*\n?\s*["\'](.+?)["\']', process_body)
    return match.group(1).strip() if match else ""


def _extract_take_channels(workflow_body: str) -> list[str]:
    """Extract take: channels from a workflow body."""
    channels = []
    take_match = re.search(r'\btake\s*:(.*?)(?=\bmain\s*:|\bemit\s*:|\Z)',
                           workflow_body, re.DOTALL)
    if take_match:
        for line in take_match.group(1).strip().split('\n'):
            line = line.strip()
            if line and not line.startswith('//'):
                channels.append(line.split('//')[0].strip())
    return channels


def _extract_emit_channels(workflow_body: str) -> list[str]:
    """Extract emit: channels from a workflow body."""
    channels = []
    emit_match = re.search(r'\bemit\s*:(.*?)(?=\}|$|\Z)', workflow_body, re.DOTALL)
    if emit_match:
        for line in emit_match.group(1).strip().split('\n'):
            line = line.strip()
            if line and not line.startswith('//') and not line.startswith('}'):
                lhs = line.split('=')[0].strip() if '=' in line else line.split('//')[0].strip()
                if lhs:
                    channels.append(lhs)
    return channels


def _extract_main_block(workflow_body: str) -> str:
    """Extract the main: block from a workflow body."""
    match = re.search(r'\bmain\s*:(.*?)(?=\bemit\s*:|\Z)', workflow_body, re.DOTALL)
    if match:
        return match.group(1).strip()
    # No main: label — everything before emit:
    match = re.search(r'^(.*?)(?=\bemit\s*:|\Z)', workflow_body, re.DOTALL)
    return match.group(1).strip() if match else workflow_body.strip()


def _extract_workflow_calls(body: str) -> list[str]:
    """Extract process/workflow calls from workflow body code."""
    calls = re.findall(r'(?<!\.)\b([a-zA-Z0-9_]+)\s*\(', body)

    built_ins = {
        'file', 'path', 'val', 'env', 'tuple', 'set', 'Channel', 'println',
        'print', 'error', 'exit', 'log', 'workflow', 'process', 'def',
        'if', 'else', 'for', 'while', 'switch', 'case', 'return', 'String', 'Integer', 'Boolean'
    }

    return list(set(c for c in calls if c not in built_ins))
