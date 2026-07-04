import json
import re
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.messages import ToolMessage as LCToolMessage
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

    if validation_error:
        # REPAIR MODE
        system_template = """You are a Nextflow DSL2 code architect. You previously attempted to generate a pipeline AST but validation failed. You now have tools to investigate and fix the issue.

You have access to the TECHNICAL CONTEXT below which contains the source code for the components. 
**CRITICAL**: The TECHNICAL CONTEXT ONLY contains component source code. It DOES NOT contain helper functions or design patterns. You MUST use your tools to find those.

Read the VALIDATION ERROR, the PLAN, and the TECHNICAL CONTEXT, and explain what needs to be fixed.

TOOLS:
1. `check_component_channels(component_name)` - Look up a specific component's EXACT take/emit signature.
2. `verify_dataflow_plan(entrypoint_instantiations, sub_workflows)` - Test your dataflow mapping to see if you forgot to instantiate any variables.
3. `validate_body_code(code_snippet, workflow_name)` - Validate a body_code snippet for DSL2 syntax errors.
4. `find_component_usage(component_id)` - See REAL production code showing how a component is wired in existing templates.
5. `search_helper_functions(query)` - Find built-in helper functions.
6. `search_design_patterns(query)` - Find reusable data-shaping patterns (e.g. host depletion branching, cross+multiMap).

INCREMENTAL REASONING WORKFLOW (Mandatory):
Step 1: Use `find_component_usage` to see how the failing components are wired in REAL production code.
Step 2: Use `check_component_channels` to fetch the EXACT take/emit signature of the components involved in the error.
Step 3: Use `verify_dataflow_plan` to propose and test your DataFlow plan. Do NOT proceed until the tool returns "SUCCESS".
Step 4: Use `validate_body_code` to test any tricky groovy snippets you intend to write.
Step 5: Once all tests pass, output your final reasoning.

CRITICAL DSL2 RULES (common mistakes):
- body_code must NOT contain 'workflow name {{}}', 'take:', 'main:', or 'emit:' keywords — the rendering template handles these automatically
- Sub-workflows must NOT define active data channels (e.g. fetching inputs/references) — these go in the entrypoint only, data is passed via take_channels
- Void tools must NOT be assigned to variables — call them directly
- The entrypoint workflow calls sub-workflows: data = my_input(); subworkflow_name(data)
- The sub-workflow receives data via take_channels, processes it, and emits results via emit_channels
- DO NOT emit channels from a sub-workflow unless they are EXPLICITLY required by the entrypoint. Be minimal.
- .branch {{ name: predicate }} creates named output channels accessible as result.name — you MUST assign the branch result to use the names
- .multiMap {{ name: expr }} creates named output channels similarly
- Prefer using standard catalog components over custom `inline_processes`

TASK: Investigate the validation error below. Explain what needs to be fixed before retrying.

VALIDATION ERROR:
{validation_error}

PLAN:
{plan}

TECHNICAL CONTEXT:
{tech_context}"""
    else:
        system_template = """You are a Nextflow DSL2 code architect preparing to generate a pipeline AST.
You must use your tools to research how to implement the Consultant's plan before generating code.

You have access to the TECHNICAL CONTEXT below which contains the source code for the components. 
**CRITICAL**: The TECHNICAL CONTEXT ONLY contains component source code. It DOES NOT contain helper functions or design patterns. You MUST use your tools to find those.

Read the PLAN and the TECHNICAL CONTEXT, and determine what tools you need to call to find helper functions or design patterns.

TOOLS:
1. `check_component_channels(component_name)` - Look up a specific component's EXACT take/emit signature.
2. `verify_dataflow_plan(entrypoint_instantiations, sub_workflows)` - Test your dataflow mapping.
3. `validate_body_code(code_snippet, workflow_name)` - Validate a body_code snippet for DSL2 syntax errors.
4. `find_component_usage(component_id)` - See REAL production code showing how a component is wired in existing templates.
5. `search_helper_functions(query)` - Find built-in helper functions.
6. `search_design_patterns(query)` - Find reusable data-shaping patterns (e.g. host depletion branching, cross+multiMap).

MANDATORY RESEARCH WORKFLOW:
Step 1: Use `check_component_channels` on the components listed in the plan to get their EXACT take/emit signatures.
Step 2: Use `find_component_usage` to see how these components are typically wired in real pipelines.
Step 3: Use `search_helper_functions` to find the exact syntax for fetching the input data described in the plan.
Step 4: Use `search_design_patterns` to understand how to route data if the plan contains complex logic.
Step 5: Once you have all the necessary syntax and logic, output a detailed summary of your research findings.

PLAN:
{plan}

TECHNICAL CONTEXT:
{tech_context}"""

    system_content = system_template.replace("{validation_error}", str(validation_error)).replace("{plan}", str(plan)).replace("{tech_context}", str(tech_context))

    state_messages = state.get("messages", [])
    relevant_messages = []
    
    if validation_error:
        # Extract the conversation history specifically for the current repair loop
        for msg in reversed(state_messages):
            relevant_messages.insert(0, msg)
            if isinstance(msg, HumanMessage) and "**VALIDATION FAILED**" in msg.content:
                break
    else:
        # Extract the conversation history specifically for the current research loop
        for msg in reversed(state_messages):
            if getattr(msg, "tool_calls", None) or isinstance(msg, LCToolMessage):
                relevant_messages.insert(0, msg)
            else:
                break
        
        # Prepend a HumanMessage to satisfy LLM API requirements (Must have a HumanMessage before AI tool loops)
        relevant_messages.insert(0, HumanMessage(content="Please research the necessary helper functions and design patterns for the provided plan. Call tools to investigate, or output your findings if you are done."))

    messages = [SystemMessage(content=system_content), *relevant_messages]

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
        if any(kw in content_lower for kw in ("channel", "emit", "take", "connection", "validation", "helper", "design", "pattern", "syntax", "research")):
            architect_findings = msg.content
            break

    plan_text = state.get('design_plan', 'No plan provided.')
    tech_context = state.get('technical_context', 'No context provided.')

    human_msg = f"APPROVED PLAN:\n{plan_text}\n\nTECHNICAL CONTEXT (Available Tools & Code):\n{tech_context}"
    if architect_findings:
        human_msg += f"\n\nRESEARCH & ANALYSIS FINDINGS:\n{architect_findings}"

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
