import json
import re
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.store.base import BaseStore

from core.config import settings
from core.models.ast_structure import NextflowPipelineAST
from core.services.graph_state import GraphState
from core.services.llm import get_llm
from core.services.prompt_loader import load_architect_prompt
from core.utils.logger import logger

ARCHITECT_SYSTEM_PROMPT = load_architect_prompt()


def architect_reason_node(state: GraphState) -> Any:
    logger.info("node_start", node="architect_reason")
    if state.get("error"):
        return {"error": state['error']}

    llm = get_llm()
    validation_error = state.get("validation_error", "")
    plan = state.get('design_plan', 'No plan provided.')
    tech_context = state.get('technical_context', 'No context provided.')

    from core.services.architect_tools import ARCHITECT_TOOLS
    llm_with_tools = llm.bind_tools(ARCHITECT_TOOLS)

    system_template = """You are a Nextflow DSL2 code architect. You previously attempted to generate a pipeline AST but validation failed. You now have tools to investigate and fix the issue.

You have access to the TECHNICAL CONTEXT below which contains all the source code of the components. You DO NOT need to look them up.
Read the VALIDATION ERROR, the PLAN, and the TECHNICAL CONTEXT, and explain what needs to be fixed.

TOOLS:
1. `check_component_channels(component_name)` - Look up a specific component's EXACT take/emit signature.
2. `verify_dataflow_plan(entrypoint_instantiations, sub_workflows)` - Test your dataflow mapping to see if you forgot to instantiate any variables.
3. `validate_body_code(code_snippet, workflow_name)` - Validate a body_code snippet for DSL2 syntax errors.

INCREMENTAL REASONING WORKFLOW (Mandatory):
Step 1: Use `check_component_channels` to fetch the EXACT take/emit signature AND the usage example of the components.
Step 2: Use `verify_dataflow_plan` to propose and test your DataFlow plan. Do NOT proceed until the tool returns "SUCCESS".
Step 3: Use `validate_body_code` to test any tricky groovy snippets you intend to write.
Step 4: Once all tests pass, output your final reasoning.

CRITICAL DSL2 RULES (common mistakes):
- body_code must NOT contain 'workflow name {{}}', 'take:', 'main:', or 'emit:' keywords — the rendering template handles these automatically
- Sub-workflows must NOT define active data channels (e.g. fetching inputs/references) — these go in the entrypoint only, data is passed via take_channels
- Void tools must NOT be assigned to variables — call them directly
- The entrypoint workflow calls sub-workflows: data = my_input(); subworkflow_name(data)
- The sub-workflow receives data via take_channels, processes it, and emits results via emit_channels
- DO NOT emit channels from a sub-workflow unless they are EXPLICITLY required by the entrypoint. Be minimal. Avoid blindly emitting everything.
- Prefer using standard catalog components over custom `inline_processes`. Only write an `InlineProcess` if explicitly instructed or if absolutely necessary.

TASK: Investigate the validation error below. Explain what needs to be fixed before retrying.

VALIDATION ERROR:
{validation_error}

PLAN:
{plan}

TECHNICAL CONTEXT:
{tech_context}"""

    system_content = system_template.replace("{validation_error}", str(validation_error)).replace("{plan}", str(plan)).replace("{tech_context}", str(tech_context))

    state_messages = state.get("messages", [])

    # Extract the conversation history specifically for the current repair loop
    repair_messages = []
    for msg in reversed(state_messages):
        repair_messages.insert(0, msg)
        # Stop traversing once we hit the start of this specific repair attempt
        if isinstance(msg, HumanMessage) and "**VALIDATION FAILED**" in msg.content:
            break

    messages = [SystemMessage(content=system_content), *repair_messages]

    try:
        result = llm_with_tools.invoke(messages)
        logger.info("architect_reason_tool_calls", count=len(result.tool_calls) if result.tool_calls else 0)
        return {"messages": [result]}
    except Exception as e:
        logger.error("architect_reason_error", error=str(e))
        return {"error": f"Failed to reason: {e}"}


def architect_generate_node(state: GraphState) -> Any:
    logger.info("node_start", node="architect_generate")
    if state.get("error"):
        return {"error": state['error']}

    llm = get_llm()
    architect_agent = llm.with_structured_output(NextflowPipelineAST, method="json_schema", include_raw=False)

    architect_findings = ""
    messages = state.get("messages", [])

    # Extract the last substantive reasoning response from the Architect
    for msg in reversed(messages[-settings.CONTEXT_WINDOW_REASON:]):
        if not isinstance(msg, AIMessage) or not msg.content or getattr(msg, 'tool_calls', None):
            continue

        content_lower = msg.content.lower()
        if any(kw in content_lower for kw in ("channel", "emit", "take", "connection", "validation")):
            architect_findings = msg.content
            break

    plan_text = state.get('design_plan', 'No plan provided.')
    tech_context = state.get('technical_context', 'No context provided.')

    human_msg = f"APPROVED PLAN:\n{plan_text}\n\nTECHNICAL CONTEXT (Available Tools & Code):\n{tech_context}"
    if architect_findings:
        human_msg += f"\n\nPREVIOUS ATTEMPT ANALYSIS (fix these issues):\n{architect_findings}"

    gen_messages = [
        SystemMessage(content=ARCHITECT_SYSTEM_PROMPT),
        HumanMessage(content=human_msg)
    ]

    try:
        result = architect_agent.invoke(gen_messages)
        logger.info("architect_generate_success")
        return {
            "ast_json": result.model_dump(),
            "validation_error": None
        }
    except Exception as e:
        logger.error("architect_validation_failed", error=str(e))
        raw_ast = {}
        llm_output = getattr(e, "llm_output", None)

        # Robust fallback extraction for malformed JSON
        if llm_output and isinstance(llm_output, str):
            try:
                content = llm_output
                match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', content, re.DOTALL)
                if match:
                    content = match.group(1)
                raw_ast = json.loads(content)
            except Exception:
                pass

        return {
            "ast_json": raw_ast,
            "validation_error": str(e),
            "retries": state.get("retries", 0) + 1
        }


def _get_channels_for_component(mid: str, store: BaseStore) -> dict[str, list[str]]:
    """Helper to extract takes/emits dynamically, resolving the None-fallback bugs."""
    from core.services.consultant_tools import _parse_nextflow_channels

    code_item = store.get(("code",), mid)
    code = code_item.value.get("content", "") if code_item else ""
    parsed = _parse_nextflow_channels(code)

    if not parsed["takes"]:
        meta = store.get(("components",), mid) or store.get(("templates",), mid)
        if meta:
            parsed["takes"] = meta.value.get("input_channels", meta.value.get("input_types", [])) or []

    if not parsed["emits"]:
        meta = store.get(("components",), mid) or store.get(("templates",), mid)
        if meta:
            parsed["emits"] = meta.value.get("output_channels", meta.value.get("out", [])) or []

    return parsed


def architect_precheck_node(state: GraphState, store: BaseStore) -> Any:
    logger.info("node_start", node="architect_precheck")
    if state.get("error"):
        return {"error": state["error"]}

    component_ids = state.get("selected_component_ids", [])
    strategy = state.get("strategy_selector", "CUSTOM_BUILD")

    if strategy == "EXACT_MATCH" or len(component_ids) < 2:
        logger.info("architect_precheck_skipped")
        return {}

    from core.services.ast_compiler import _is_void_tool

    warnings = []

    # Check for direct sequence mismatches
    for i in range(len(component_ids) - 1):
        src_id = component_ids[i]
        tgt_id = component_ids[i + 1]

        src_parsed = _get_channels_for_component(src_id, store)
        tgt_parsed = _get_channels_for_component(tgt_id, store)

        src_lower = {ch.lower() for ch in src_parsed["emits"]}
        tgt_lower = {ch.lower() for ch in tgt_parsed["takes"]}

        if src_lower and tgt_lower and not (src_lower & tgt_lower) and len(tgt_parsed["takes"]) != 1:
            warnings.append(
                f"MISMATCH {src_id} → {tgt_id}: emits {list(src_lower)}, takes {list(tgt_lower)}. Use .map or rename to adapt."
            )

    void_tools = [mid for mid in component_ids if _is_void_tool(mid)]
    if void_tools:
        warnings.append(f"VOID TOOLS (no output): {void_tools}. Call directly, no assignment, no emit.")

    missing_code = [mid for mid in component_ids if not store.get(("code",), mid)]
    if missing_code:
        warnings.append(f"NO SOURCE CODE: {missing_code}. Rely on catalog metadata for channel names.")

    channel_map_lines = []
    for mid in component_ids:
        parsed = _get_channels_for_component(mid, store)
        e = "VOID" if _is_void_tool(mid) else ", ".join(parsed["emits"]) or "unknown"
        t = ", ".join(parsed["takes"]) or "unknown"
        channel_map_lines.append(f"- {mid}: take=[{t}] emit=[{e}]")

    if warnings or channel_map_lines:
        precheck_block = "\n## CHANNEL MAP (verified from code store)\n"
        precheck_block += "\n".join(channel_map_lines)
        if warnings:
            precheck_block += "\n## WARNINGS\n" + "\n".join(warnings)

        logger.info("architect_precheck_warnings", count=len(warnings))
        return {"technical_context": state.get("technical_context", "") + "\n\n" + precheck_block}

    logger.info("architect_precheck_clear")
    return {}
