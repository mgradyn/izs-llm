import re
from typing import Any

from langgraph.store.base import BaseStore

from core.services.graph_state import GraphState
from core.utils.logger import logger


def _get_component_reference(comp_id: str, store: BaseStore) -> list[str]:
    """Helper to extract reference code and usages for a component to reduce cyclomatic complexity."""
    code_item = store.get(("code",), comp_id)
    source_code = code_item.value.get("content") if code_item else None
    if not source_code:
        return []

    parts = [
        f"[[REFERENCE FOR STEP: {comp_id}]]",
        f"Component ID: {comp_id}",
        f"```groovy\n{source_code.strip()}\n```"
    ]

    usage_item = store.get(("usage",), comp_id)
    if usage_item:
        usages = usage_item.value.get("usages", [])
        shown = 0
        for u in usages:
            snippet = u.get("snippet", "")
            if snippet and "(not found" not in snippet and shown < 2:
                parts.append(f"  USAGE EXAMPLE (from {u['template_id']}):")
                parts.append(f"  ```groovy\n  {snippet}\n  ```")
                shown += 1

    parts.append(f"[[END REFERENCE: {comp_id}]]")
    return parts


def filter_template_logic(code: str, allowed_components: set) -> str:
    lines = code.split('\n')
    filtered_lines = []

    # Negative lookbehind (?<!\.) ensures we DO NOT match method calls like .view() or ch.join()
    # We only want to match standalone function/component calls like fastqc(ch)
    pattern = re.compile(r'(?<!\.)\b([a-zA-Z0-9_]+)\s*\(')

    ignore_list = {
        'Channel', 'path', 'tuple', 'val', 'set', 'map', 'branch', 'multiMap', 'mix',
        'if', 'else', 'while', 'for', 'switch', 'catch', 'finally', 'def', 'return',
        'print', 'println', 'error', 'exit', 'include'
    }

    for line in lines:
        match = pattern.search(line)
        if match:
            func_name = match.group(1)
            if func_name not in ignore_list and func_name not in allowed_components:
                filtered_lines.append(f"    // [REMOVED BY PLAN] {line.strip()}")
                continue

        filtered_lines.append(line)

    return "\n".join(filtered_lines)


def _build_exact_match(used_template_id: str, context_parts: list[str], store: BaseStore) -> None:
    tmpl_item = store.get(("templates",), used_template_id)
    template_def = tmpl_item.value if tmpl_item else None

    context_parts.append(f"### STRICT TEMPLATE MODE: {used_template_id}")
    if template_def:
        context_parts.append(f"Description: {template_def.get('description')}")
        code_item = store.get(("code",), used_template_id)
        tmpl_code = code_item.value.get("content") if code_item else None

        if tmpl_code:
            context_parts.append(f"[[TEMPLATE SOURCE CODE: {used_template_id}]]")
            context_parts.append("INSTRUCTION: Use the logic in this workflow block exactly.")
            context_parts.append(f"```groovy\n{tmpl_code.strip()}\n```")
            context_parts.append("[[END TEMPLATE SOURCE]]")

        for comp_id in template_def.get('components_used', []):
            context_parts.extend(_get_component_reference(comp_id, store))


def _build_adapted_match(used_template_id: str, component_ids: list[str], context_parts: list[str], store: BaseStore) -> None:
    context_parts.append(f"### ADAPTED TEMPLATE MODE: Based on {used_template_id}")
    t_item = store.get(("code",), used_template_id)
    tmpl_code = t_item.value.get("content") if t_item else None

    if tmpl_code:
        allowed_ids = {used_template_id, *component_ids}
        filtered_code = filter_template_logic(tmpl_code, allowed_ids)

        context_parts.append(f"[[TEMPLATE SOURCE CODE: {used_template_id}]]")
        context_parts.append("INFO: Some steps in this template have been commented out because they are not in your Design Plan.")
        context_parts.append("INSTRUCTION: Reuse the logic that remains, but FILL THE GAPS using your new components.")
        context_parts.append(f"```groovy\n{filtered_code.strip()}\n```")

    for comp_id in component_ids:
        if comp_id != used_template_id:
            context_parts.extend(_get_component_reference(comp_id, store))


def _build_custom_match(component_ids: list[str], context_parts: list[str], store: BaseStore) -> None:
    context_parts.append("### CUSTOM BUILD MODE")
    for comp_id in component_ids:
        context_parts.extend(_get_component_reference(comp_id, store))


def hydrator_node(state: GraphState, store: BaseStore) -> Any:
    logger.info("node_start", node="hydrator")

    if state.get("error"):
        return {"error": state["error"]}

    context_parts = []

    strategy = state.get('strategy_selector', 'CUSTOM_BUILD')
    used_template_id = state.get('used_template_id')
    component_ids = state.get('selected_component_ids', [])
    plan_text = state.get('design_plan', '')

    res_item = store.get(("resources",), "helper_functions")
    res_list = res_item.value.get("list", []) if res_item else []
    helper_names = [r['name'] for r in res_list]

    if strategy == "EXACT_MATCH" and used_template_id:
        _build_exact_match(used_template_id, context_parts, store)
    elif strategy == "ADAPTED_MATCH" and used_template_id:
        _build_adapted_match(used_template_id, component_ids, context_parts, store)
    else:
        _build_custom_match(component_ids, context_parts, store)

    # Compile the final technical context
    full_context = "\n\n".join(context_parts)

    # Global detection for helper functions across all merged source code
    detected_helpers = {h for h in helper_names if h in full_context}
    if plan_text and ("cross" in plan_text or "multiMap" in plan_text):
        detected_helpers.add("extractKey")

    if detected_helpers:
        helper_parts = ["\n### AVAILABLE HELPER FUNCTIONS"]
        for h_name in detected_helpers:
            res_def = next((r for r in res_list if r['name'] == h_name), None)
            if res_def:
                helper_parts.append(f"- {h_name}: {res_def.get('description')}")
                helper_parts.append(f"  Usage: `{res_def.get('usage')}`")
        full_context += "\n" + "\n".join(helper_parts)

    return {"technical_context": full_context}
