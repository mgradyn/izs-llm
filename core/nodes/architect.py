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

ARCHITECT_RESEARCH_SYSTEM_PROMPT = """You are a Nextflow DSL2 code architect preparing to generate a pipeline AST.
You must use your tools to research how to implement the Consultant's plan before generating code.

You have access to the TECHNICAL CONTEXT provided in the conversation which contains the component source code.
**CRITICAL**: The TECHNICAL CONTEXT ONLY contains component source code. It DOES NOT contain helper functions or design patterns. You MUST use your tools to find those.

TOOLS:
1. `check_component_channels(component_name)` - Look up a specific component's EXACT take/emit signature.
2. `verify_dataflow_plan(entrypoint_instantiations, sub_workflows)` - Test your dataflow mapping.
3. `validate_body_code(code_snippet, workflow_name)` - Validate a body_code snippet for DSL2 syntax errors.
4. `search_helper_functions(query)` - Find built-in helper functions.
5. `search_design_patterns(query)` - Find reusable data-shaping patterns (e.g. host depletion branching, cross+multiMap).
6. `auto_complete_pipeline_dag(src, tgt)` - Use the Knowledge Graph to find valid paths between components.

MANDATORY RESEARCH WORKFLOW:
Step 1: Use `check_component_channels` on the components listed in the plan to get their EXACT take/emit signatures.
Step 2: Use `search_helper_functions` to find the exact syntax for fetching the input data described in the plan.
Step 3: Use `search_design_patterns` to understand how to route data if the plan contains complex logic.
Step 4: Once you have all the necessary syntax and logic, output a detailed summary of your research findings."""

ARCHITECT_REPAIR_SYSTEM_PROMPT = """You are a Nextflow DSL2 code architect. You previously attempted to generate a pipeline AST but validation failed. You now have tools to investigate and fix the issue.

You have access to the TECHNICAL CONTEXT provided in the conversation which contains the component source code.
**CRITICAL**: The TECHNICAL CONTEXT ONLY contains component source code. It DOES NOT contain helper functions or design patterns. You MUST use your tools to find those.

TOOLS:
1. `check_component_channels(component_name)` - Look up a specific component's EXACT take/emit signature.
2. `verify_dataflow_plan(entrypoint_instantiations, sub_workflows)` - Test your dataflow mapping to see if you forgot to instantiate any variables.
3. `validate_body_code(code_snippet, workflow_name)` - Validate a body_code snippet for DSL2 syntax errors.
4. `search_helper_functions(query)` - Find built-in helper functions.
5. `search_design_patterns(query)` - Find reusable data-shaping patterns (e.g. host depletion branching, cross+multiMap).
6. `auto_complete_pipeline_dag(src, tgt)` - Use the Knowledge Graph to find valid paths between components.

INCREMENTAL REASONING WORKFLOW (Mandatory):
Step 1: Use `check_component_channels` to fetch the EXACT take/emit signature of the components involved in the error.
Step 2: Use `verify_dataflow_plan` to propose and test your DataFlow plan. Do NOT proceed until the tool returns "SUCCESS".
Step 3: Use `validate_body_code` to test any tricky groovy snippets you intend to write.
Step 4: Once all tests pass, output your final reasoning.

CRITICAL DSL2 RULES (common mistakes):
- body_code must NOT contain 'workflow name {}', 'take:', 'main:', or 'emit:' keywords — the rendering template handles these automatically
- Sub-workflows must NOT define active data channels (e.g. fetching inputs/references) — these go in the entrypoint only, data is passed via take_channels
- Void tools must NOT be assigned to variables — call them directly
- The entrypoint workflow calls sub-workflows: data = my_input(); subworkflow_name(data)
- The sub-workflow receives data via take_channels, processes it, and emits results via emit_channels
- DO NOT emit channels from a sub-workflow unless they are EXPLICITLY required by the entrypoint. Be minimal.
- .branch { name: predicate } creates named output channels accessible as result.name — you MUST assign the branch result to use the names
- .multiMap { name: expr } creates named output channels similarly
- Prefer using standard catalog components over custom `inline_processes`"""


def architect_reason_node(state: GraphState) -> Any:
    logger.info("node_start", node="architect_reason")
    if state.get("error"):
        return {"error": state['error']}

    llm = get_llm()
    validation_error = state.get("validation_error", "")
    plan = state.get('design_plan', 'No plan provided.')
    tech_context = state.get('technical_context', 'No context provided.')

    from core.tool_registry import get_architect_tools
    llm_with_tools = llm.bind_tools(get_architect_tools())

    # [Head / Fixed Prefix]: Invariant system message ensures 100% prefix cache hits in vLLM
    system_msg = SystemMessage(
        content=ARCHITECT_REPAIR_SYSTEM_PROMPT if validation_error else ARCHITECT_RESEARCH_SYSTEM_PROMPT
    )

    # [Middle / Static Reference]: Technical context and design plan anchored at front of conversation
    anchor_content = (
        f"### TECHNICAL CONTEXT (Available Tools & Code):\n{tech_context}\n\n"
        f"### APPROVED PLAN:\n{plan}\n\n"
    )
    if validation_error:
        anchor_content += (
            f"### VALIDATION ERROR TO FIX:\n{validation_error}\n\n"
            "TASK: Investigate the validation error above using your tools. Explain what needs to be fixed before retrying."
        )
    else:
        anchor_content += (
            "TASK: Please research the necessary helper functions and design patterns for the provided plan. "
            "Call tools to investigate, or output your findings if you are done."
        )

    state_messages = state.get("messages", [])
    relevant_messages = []
    
    if validation_error:
        # Extract the conversation history specifically for the current repair loop
        for msg in reversed(state_messages):
            relevant_messages.insert(0, msg)
            if isinstance(msg, HumanMessage) and "**VALIDATION FAILED**" in msg.content:
                break
    else:
        # Research mode: collect all messages from the end of the consultant turn onward.
        limit = settings.CONTEXT_WINDOW_REASON * 3
        count = 0
        in_tool_block = False

        for msg in reversed(state_messages):
            relevant_messages.insert(0, msg)
            count += 1

            if isinstance(msg, LCToolMessage):
                in_tool_block = True
            elif isinstance(msg, AIMessage) and getattr(msg, 'tool_calls', None):
                in_tool_block = False

            if isinstance(msg, HumanMessage) and "**VALIDATION FAILED**" not in msg.content:
                break  # found the real user-turn boundary
                
            if not in_tool_block and count >= limit:
                break

        # Strip any trailing AIMessages that are NOT followed by tool results.
        while relevant_messages and isinstance(relevant_messages[-1], AIMessage) and not getattr(relevant_messages[-1], 'tool_calls', None):
            relevant_messages.pop()

    # Anchor the technical context and plan as the first HumanMessage
    if not relevant_messages or not isinstance(relevant_messages[0], HumanMessage):
        relevant_messages.insert(0, HumanMessage(content=anchor_content))
    else:
        # Prepend anchor context to the first HumanMessage if not already there
        if "### TECHNICAL CONTEXT" not in str(relevant_messages[0].content):
            relevant_messages[0] = HumanMessage(content=f"{anchor_content}\n\n{relevant_messages[0].content}")

    messages = [system_msg, *relevant_messages]

    # Enforce mandatory tool calling on the first pass of the loop
    if len(relevant_messages) == 1 and isinstance(relevant_messages[0], HumanMessage):
        from core.tool_registry import get_architect_tools
        llm_with_tools = llm.bind_tools(get_architect_tools(), tool_choice="any")
        logger.info("architect_reason_mandatory_tool_enforced")
    try:
        result = llm_with_tools.invoke(messages)
        # Tag this message as internal so the API doesn't send it to the user chat
        result.additional_kwargs["internal_agent"] = "architect"
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

    for msg in reversed(messages[-settings.CONTEXT_WINDOW_REASON * 4:]):
        if not isinstance(msg, AIMessage) or not msg.content or getattr(msg, 'tool_calls', None):
            continue

        content_lower = msg.content.lower()
        if any(kw in content_lower for kw in ("channel", "emit", "take", "connection", "validation", "helper", "design", "pattern", "syntax", "research")):
            architect_findings = msg.content
            break

    plan_text = state.get('design_plan', 'No plan provided.')
    tech_context = state.get('technical_context', 'No context provided.')

    # [Middle / Static Reference]: TECHNICAL CONTEXT + APPROVED PLAN at front
    human_msg = (
        f"### TECHNICAL CONTEXT (Available Tools & Code):\n{tech_context}\n\n"
        f"### APPROVED PLAN:\n{plan_text}"
    )
    # [Tail / Dynamic Payload]: Variable research findings
    if architect_findings:
        human_msg += f"\n\n### RESEARCH & ANALYSIS FINDINGS:\n{architect_findings}"

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

        # Clean up Pydantic validation error to save context tokens
        error_str = str(e)
        error_str = re.sub(r'\[type=.*?, input_value=.*?input_type=dict\]', '', error_str, flags=re.DOTALL)
        error_str = re.sub(r'For further information visit https://errors\.pydantic\.dev/.*?$', '', error_str, flags=re.MULTILINE)

        ast_str = json.dumps(raw_ast, sort_keys=True)
        try:
            import os
            dump_dir = os.path.join(settings.BASE_DIR, "scratch")
            os.makedirs(dump_dir, exist_ok=True)
            with open(os.path.join(dump_dir, "last_ast_dump.json"), "w") as f:
                f.write(ast_str)
        except Exception:
            pass
        import hashlib
        ast_hash = hashlib.md5(ast_str.encode()).hexdigest()

        past_hashes = state.get("past_ast_hashes", [])
        if ast_hash in past_hashes:
            logger.error("AST generation loop detected. Small model is incapable of fixing this. Aborting repair loop early.")
            return {
                "ast_json": raw_ast,
                "error": f"AST Repair Loop Detected: Repeated identical failed AST structure. Model is unable to repair {error_str.strip()}"
            }
        
        return {
            "ast_json": raw_ast,
            "validation_error": error_str.strip(),
            "retries": state.get("retries", 0) + 1,
            "past_ast_hashes": past_hashes + [ast_hash]
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

    from core.services.knowledge_graph import kg
    if not kg.is_built:
        kg.build_graph(store)

    # ── Channel mismatch check ───────────────────────────────────────────────
    for i in range(len(component_ids) - 1):
        src_id = component_ids[i]
        tgt_id = component_ids[i + 1]

        # Use Knowledge Graph to deterministically check path
        path = kg.find_path(src_id, tgt_id, store)
        
        src_parsed = _get_channels_for_component(src_id, store)
        tgt_parsed = _get_channels_for_component(tgt_id, store)
        src_lower = {ch.lower() for ch in src_parsed["emits"]}
        tgt_lower = {ch.lower() for ch in tgt_parsed["takes"]}

        if not path:
            if src_lower and tgt_lower and not (src_lower & tgt_lower) and len(tgt_parsed["takes"]) != 1:
                warnings.append(
                    f"MISMATCH {src_id} → {tgt_id}: emits {list(src_lower)}, takes {list(tgt_lower)}. "
                    "No valid graph path found. Use .map or rename to adapt."
                )

    # ── Void tool detection ──────────────────────────────────────────────────
    void_tools = [mid for mid in component_ids if _is_void_tool(mid)]
    if void_tools:
        warnings.append(f"VOID TOOLS (no output): {void_tools}. Call directly, no assignment, no emit.")

    # ── Missing code check ───────────────────────────────────────────────────
    missing_code = [mid for mid in component_ids if not store.get(("code",), mid)]
    if missing_code:
        warnings.append(f"NO SOURCE CODE: {missing_code}. Rely on catalog metadata for channel names.")

    # ── Assembly without preprocessing check ────────────────────────────────
    user_query = state.get("user_query", "").lower()
    has_assembly = any("denovo" in mid or "mapping" in mid for mid in component_ids)
    has_preprocessing = any("1pp_" in mid.lower() or "trimming" in mid.lower() for mid in component_ids)
    if "raw read" in user_query and has_assembly and not has_preprocessing:
        warnings.append(
            "WARNING: The user requested assembly/mapping from 'raw reads', but no preprocessing/trimming "
            "tool (e.g. step_1PP_trimming__fastp) is in the pipeline. Assembly typically requires trimmed reads."
        )

    # ── Deterministic Helper Injection for Unmet Inputs ─────────────────────
    all_takes = set()
    all_emits = set()
    
    for mid in component_ids:
        parsed = _get_channels_for_component(mid, store)
        # Handle parsed takes correctly since they might have spaces
        for t in parsed.get("takes", []):
            if t and t.lower() not in ["none", ""]:
                all_takes.add(t.strip())
        for e in parsed.get("emits", []):
            if e and e.lower() not in ["none", ""]:
                all_emits.add(e.strip())
                
    unmet_takes = all_takes - all_emits
    
    helper_injections = []
    if unmet_takes:
        try:
            res_item = store.get(("resources",), "helper_functions")
            if res_item and res_item.value:
                helpers = res_item.value.get("list", [])
                
                for take in unmet_takes:
                    take_lower = take.lower()
                    # Score helpers: 10 if exact match in name, 5 if partial, 2 if in desc
                    scored = []
                    for h in helpers:
                        h_name = h.get("name", "")
                        h_desc = h.get("description", "")
                        h_lower = h_name.lower()
                        score = 0
                        if take_lower in h_lower:
                            score = 10 if take_lower == h_lower.replace("get", "") else 5
                        elif take_lower in h_desc.lower():
                            score = 2
                            
                        if score > 0:
                            scored.append((score, h_name, h_desc))
                    
                    if scored:
                        # Sort by score descending, get top 2
                        scored.sort(key=lambda x: x[0], reverse=True)
                        top_helpers = scored[:2]
                        for score, h_name, h_desc in top_helpers:
                            helper_injections.append(f"- For input `{take}` -> Use `{h_name}()`: {h_desc}")
        except Exception as e:
            logger.warning(f"Error extracting helper functions: {e}")

    # ── Build channel map for architect context ──────────────────────────────
    from core.loader import data_loader
    channel_map_lines = []
    for mid in component_ids:
        parsed = _get_channels_for_component(mid, store)
        e = "VOID" if _is_void_tool(mid) else ", ".join(parsed["emits"]) or "unknown"
        t = ", ".join(parsed["takes"]) or "unknown"

        # Annotate with known graph edge channels
        line = f"- {mid}: take=[{t}] emit=[{e}]"
        
        # See what downstream components we can reach directly
        downstream = []
        for tgt in component_ids:
            if mid != tgt:
                p = kg.find_path(mid, tgt, store)
                if p and len(p) == 2: # Direct edge
                    downstream.append(tgt)
                    
        if downstream:
            line += f" → {', '.join(downstream)}"

        channel_map_lines.append(line)

    # ── Deterministic Pattern Injection ──────────────────────────────────────
    pattern_injections = []
    try:
        patterns = store.search(("patterns",))
        matched_patterns = []
        for p in patterns:
            code = str(p.value.get("groovy_code", ""))
            if not code: continue
            
            # Count how many of the selected components appear in this pattern
            matched_comps = [mid for mid in component_ids if mid in code]
            if matched_comps:
                # Score by how many components match, to rank them
                matched_patterns.append((len(matched_comps), p.value.get("title", ""), code))
                
        if matched_patterns:
            # Sort by number of matched components descending
            matched_patterns.sort(key=lambda x: x[0], reverse=True)
            for score, title, code in matched_patterns[:5]: # Take top 5
                pattern_injections.append(f"### {title}\n```groovy\n{code}\n```")
    except Exception as e:
        logger.warning(f"Error extracting patterns: {e}")

    if warnings or channel_map_lines or helper_injections or pattern_injections:
        precheck_block = "\n## CHANNEL MAP (verified from code store)\n"
        precheck_block += "\n".join(channel_map_lines)
        if helper_injections:
            precheck_block += "\n\n## DEDUCED HELPER FUNCTIONS (For Unmet Inputs)\n"
            precheck_block += "You MUST use these specific helper functions to instantiate the missing input channels in your entrypoint:\n"
            precheck_block += "\n".join(helper_injections)
        if pattern_injections:
            precheck_block += "\n\n## RELEVANT DESIGN PATTERNS (Deterministically Matched)\n"
            precheck_block += "These verified patterns use the exact components in your plan. Use these idioms for complex data-shaping:\n\n"
            precheck_block += "\n\n".join(pattern_injections)
        if warnings:
            precheck_block += "\n\n## WARNINGS\n" + "\n".join(warnings)

        logger.info("architect_precheck_warnings", count=len(warnings))
        return {"technical_context": state.get("technical_context", "") + "\n\n" + precheck_block}

    logger.info("architect_precheck_clear")
    return {}


