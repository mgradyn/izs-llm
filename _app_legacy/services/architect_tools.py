"""
Architect Tools — tools for the architect to verify code logic during generation.

These tools let the architect look up component source code, validate
channel connections, and check AST structure against DSL2 rules.
Token-efficient: only used on retries, not on first attempt.
"""

import re
import json
from langchain_core.tools import tool
from langchain.tools import ToolRuntime
from app.services.consultant_tools import _parse_nextflow_channels


@tool
def lookup_component_code(component_id: str, runtime: ToolRuntime) -> dict:
    """Look up a component's Nextflow source code from the knowledge base.
    Use this to verify HOW a component works — its take/emit channels,
    internal logic, and connection patterns — before writing code that uses it.
    
    Args:
        component_id: The exact component or template ID (e.g. 'step_1PP_trimming__fastp')
    """
    store = runtime.store
    
    # Get metadata
    comp_item = store.get(("components",), component_id)
    tmpl_item = store.get(("templates",), component_id) if not comp_item else None
    
    if not comp_item and not tmpl_item:
        return {"error": f"'{component_id}' not found in catalog"}
    
    meta = (comp_item or tmpl_item).value
    code_item = store.get(("code",), component_id)
    code = code_item.value.get("content", "") if code_item else ""
    
    # Parse channels from actual code
    parsed = _parse_nextflow_channels(code) if code else {"takes": [], "emits": [], "partial": True}
    
    result = {
        "id": component_id,
        "type": "template" if tmpl_item else "component",
        "takes": parsed["takes"] or meta.get("input_channels", meta.get("input_types", [])),
        "emits": parsed["emits"] or meta.get("output_channels", meta.get("out", [])),
    }
    
    if code:
        # Truncate to keep context small
        result["code"] = code[:2000] + ("\n// ... (truncated)" if len(code) > 2000 else "")
    else:
        result["warning"] = "Source code not available in code store"
    
    return result


@tool
def verify_channel_connection(source_id: str, target_id: str, runtime: ToolRuntime) -> dict:
    """Quick check if two components can connect based on their emit/take channels.
    Use this to verify a specific connection before writing it in the workflow code.
    
    Args:
        source_id: The upstream component ID
        target_id: The downstream component ID
    """
    store = runtime.store
    
    for cid in (source_id, target_id):
        if not store.get(("components",), cid) and not store.get(("templates",), cid):
            return {"error": f"'{cid}' not found in catalog"}
    
    src_code_item = store.get(("code",), source_id)
    tgt_code_item = store.get(("code",), target_id)
    
    src_code = src_code_item.value.get("content", "") if src_code_item else ""
    tgt_code = tgt_code_item.value.get("content", "") if tgt_code_item else ""
    
    src_parsed = _parse_nextflow_channels(src_code)
    tgt_parsed = _parse_nextflow_channels(tgt_code)
    
    # Fallback to catalog metadata
    if not src_parsed["emits"]:
        src_meta = (store.get(("components",), source_id) or store.get(("templates",), source_id)).value
        src_parsed["emits"] = src_meta.get("output_channels", src_meta.get("out", []))
    if not tgt_parsed["takes"]:
        tgt_meta = (store.get(("components",), target_id) or store.get(("templates",), target_id)).value
        tgt_parsed["takes"] = tgt_meta.get("input_channels", tgt_meta.get("input_types", []))
    
    src_lower = {ch.lower() for ch in src_parsed["emits"]}
    tgt_lower = {ch.lower() for ch in tgt_parsed["takes"]}
    exact = src_lower & tgt_lower
    
    return {
        "source": source_id,
        "target": target_id,
        "source_emits": src_parsed["emits"],
        "target_takes": tgt_parsed["takes"],
        "exact_matches": list(exact),
        "compatible": bool(exact) or len(tgt_parsed["takes"]) == 1,
    }


@tool
def validate_body_code(code_snippet: str, workflow_name: str) -> dict:
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
    is_entrypoint = (workflow_name == "entrypoint")
    
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
        
    # Check for void tool assignments
    from app.models.ast_structure import _is_void_tool
    void_assignments = re.finditer(
        r'\b[a-zA-Z0-9_]+\s*=\s*((?:step_|multi_|module_)[a-zA-Z0-9_]+)\s*\(',
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
        r'\b(?:step_|multi_|module_)[a-zA-Z0-9_]+\s*\(([^)]+)\)',
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
    if re.search(r'\b(?:step_|multi_|module_)[a-zA-Z0-9_]+\s*\([^)]*\)\s*\.set\s*\{', code_snippet):
        issues.append(
            "'.set' appended to a process call. "
            "Use direct assignment instead: 'var = process(...)'"
        )
    
    # Check for framework components existence
    from app.models.ast_structure import FRAMEWORK_COMPONENTS
    referenced = set(re.findall(r'\b((?:step_|multi_|module_)[a-zA-Z0-9_]+)\s*\(', code_snippet))
    invalid = referenced - FRAMEWORK_COMPONENTS
    if invalid:
        issues.append("Unknown components not in framework: " + str(sorted(invalid)))
    
    return {
        "valid": len(issues) == 0,
        "issues": issues,
        "warnings": warnings,
        "checked_workflow": workflow_name,
    }


ARCHITECT_TOOLS = [
    lookup_component_code,
    verify_channel_connection,
    validate_body_code,
]

