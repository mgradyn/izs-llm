"""
Architect Tools — tools for the architect to verify code logic during generation.

These tools let the architect look up component source code, validate
channel connections, and check AST structure against DSL2 rules.
Token-efficient: only used on retries, not on first attempt.
"""

import re

from langchain_core.tools import tool

from core.services.consultant_tools import lookup_catalog_item


@tool
def validate_body_code(code_snippet: str, workflow_name: str) -> dict:  # noqa: C901
    """Validate a body_code snippet for common Nextflow DSL2 errors.
    Use this to check a piece of body_code BEFORE including it in the AST.

    Checks for:
    - Forbidden keywords (take:, main:, emit:, workflow wrapper)
    - Active channels in sub-workflows (get*() functions)
    - Void tool assignment errors
    - Inline channel join errors (.cross/.combine inside process args)
    - Framework component existence

    Args:
        code_snippet: The body_code string to validate
        workflow_name: Name of the workflow this code belongs to (use 'entrypoint' for the main workflow)
    """
    issues = []
    warnings = []
    _is_entrypoint = (workflow_name == "entrypoint")

    # Check for forbidden keywords that the template handles
    forbidden_kw_list = [
        ("take:", "Remove it from body_code and put channels in take_channels list."),
        ("main:", "Remove it from body_code. The rendering template adds it automatically."),
        ("emit:", "Remove it from body_code and put channels in emit_channels list."),
    ]
    for kw, fix in forbidden_kw_list:
        if re.search(rf'^\s*{re.escape(kw)}\s*$', code_snippet, re.MULTILINE):
            issues.append("FORBIDDEN KEYWORD '" + kw + "' found in body_code. " + fix)

    # Check for workflow wrapper
    if re.search(r'^\s*workflow\s+\w+\s*\{', code_snippet, re.MULTILINE):
        issues.append(
            "body_code contains a 'workflow name' wrapper. "
            "The template handles this. body_code should only contain the logic INSIDE the workflow block."
        )

    # Check for active channels in sub-workflows
    if not _is_entrypoint:
        active_calls = re.findall(r'\b(get_[a-zA-Z0-9_]+|my_active_channel)\s*\(', code_snippet)
        if active_calls:
            issues.append(
                f"Active channel instantiation {active_calls} found inside sub-workflow '{workflow_name}'. "
                "Active channels must ONLY be instantiated in the entrypoint."
            )

    # Check for void tool assignments
    from core.models.ast_structure import _is_void_tool
    void_assignments = re.finditer(
        r'\b[a-zA-Z0-9_]+\s*=\s*([a-zA-Z0-9_]+)\s*\(',
        code_snippet
    )
    for m in void_assignments:
        proc_name = m.group(1)
        if _is_void_tool(proc_name):
            issues.append(
                "Void tool '" + proc_name + "' is assigned to a variable. "
                "Void tools have no output. Call it directly without assignment."
            )

    # Check for inline channel joins in process arguments
    proc_calls = re.finditer(
        r'\b[a-zA-Z0-9_]+\s*\(([^)]+)\)',
        code_snippet
    )
    for m in proc_calls:
        args = m.group(1)
        if '.cross' in args or '.combine' in args:
            issues.append(
                "Inline channel join in process arguments: '" + m.group(0) + "'. "
                "Perform .cross()/.combine() on a separate line, shape with .map/.multiMap, "
                "assign to a variable, then pass the variable."
            )

    # Check for .set on process calls
    if re.search(r'\b[a-zA-Z0-9_]+\s*\([^)]*\)\s*\.set\s*\{', code_snippet):
        issues.append(
            "'.set' appended to a process call. "
            "Use direct assignment instead: 'var = process(...)'"
        )

    # Check for framework components existence (via CatalogRegistry)
    from core.catalog_registry import get_registry
    registry = get_registry()
    referenced = set(match.group(1) for match in re.finditer(r'(?<!\.)\b([a-zA-Z0-9_]+)\s*\(', code_snippet))

    if registry.is_initialized and registry.valid_components:
        builtins = {
            'file', 'tuple', 'val', 'path', 'Channel', 'fromPath', 'of', 'println',
            'log', 'error', 'exit', 'get', 'set', 'map', 'cross', 'combine', 'mix',
            'join', 'branch', 'multiMap', 'groupTuple', 'flatten', 'collect',
            'splitCsv', 'splitText', 'splitFasta', 'splitFastq', 'env', 'print',
            'workflow', 'process', 'def', 'if', 'else', 'for', 'while', 'switch',
            'case', 'return', 'String', 'Integer', 'Boolean'
        }

        plugin_helpers = set()
        try:
            from core.plugin_loader import get_active_plugin
            plugin_helpers = set(get_active_plugin().helper_imports.keys())
        except Exception:
            pass

        filtered_referenced = {r for r in referenced if not (r in builtins or r in plugin_helpers or r.startswith('get_') or r == 'my_active_channel')}

        invalid = filtered_referenced - registry.valid_components
        if invalid:
            issues.append("Unknown components or functions not in catalog: " + str(sorted(invalid)))

    return {
        "valid": len(issues) == 0,
        "issues": issues,
        "warnings": warnings,
        "checked_workflow": workflow_name,
    }


ARCHITECT_TOOLS = [
    lookup_catalog_item,
    validate_body_code,
]
