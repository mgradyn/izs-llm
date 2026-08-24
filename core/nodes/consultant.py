from typing import Any

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.messages import ToolMessage as LCToolMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langgraph.store.base import BaseStore

from core.config import settings
from core.models.consultant_structure import ConsultantOutput
from core.services.graph_state import GraphState
from core.services.llm import get_llm
from core.services.prompt_loader import load_consultant_prompt, load_extractor_prompt
from core.utils.logger import logger
from core.utils.retry import with_exponential_backoff

CONSULTANT_SYSTEM_PROMPT = load_consultant_prompt()
EXTRACTOR_SYSTEM_PROMPT = load_extractor_prompt()

# ──────────────────────────────────────────────────────────────────────────────
# Approval detection utility
# ──────────────────────────────────────────────────────────────────────────────

_APPROVAL_PHRASES = (
    "approved", "i approve", "yes, build", "yes build",
    "please build", "go ahead", "looks good", "lgtm",
    "build the pipeline", "build it", "proceed", "confirm", "execute",
)


def _detect_approval(messages: list) -> bool:
    """Return True if the last human message is an approval of the proposed plan."""
    for m in reversed(messages):
        if isinstance(m, HumanMessage):
            content = m.content
            if isinstance(content, list):
                text = " ".join(
                    c.get("text", "") for c in content
                    if isinstance(c, dict) and c.get("type") == "text"
                )
            elif isinstance(content, str):
                text = content
            else:
                text = ""
            text = text.strip().lower().rstrip("!.,;:?")
            return any(phrase in text for phrase in _APPROVAL_PHRASES)
    return False


# ──────────────────────────────────────────────────────────────────────────────
# Message sanitisation
# ──────────────────────────────────────────────────────────────────────────────

def _sanitize_messages_for_api(messages: list) -> list:
    """Ensures every tool call has a corresponding tool response for strict API compliance."""
    answered_ids = {m.tool_call_id for m in messages if isinstance(m, LCToolMessage)}

    patched = []
    for msg in messages:
        patched.append(msg)
        if isinstance(msg, AIMessage) and getattr(msg, "tool_calls", None):
            for tc in msg.tool_calls:
                tc_id = tc.get("id") or tc.get("tool_call_id")
                if tc_id and tc_id not in answered_ids:
                    patched.append(LCToolMessage(
                        content="[Tool call skipped — iteration limit reached]",
                        tool_call_id=tc_id,
                        name=tc.get("name", "unknown"),
                    ))
                    answered_ids.add(tc_id)

    return patched


# ──────────────────────────────────────────────────────────────────────────────
# Consultant node
# ──────────────────────────────────────────────────────────────────────────────

def consultant_node(state: GraphState) -> Any:
    logger.info("node_start", node="consultant")
    llm = get_llm()
    current_messages = state.get("messages", [])
    current_plan = state.get("design_plan", "No plan generated yet.")
    current_components = state.get("selected_component_ids", [])
    current_template = state.get("used_template_id", "None")
    tool_memory = state.get("tool_memory", []) or []

    formatted_facts = ""
    if tool_memory:
        fact_lines = []
        for fact in tool_memory:
            if isinstance(fact, dict):
                tool_name = fact.get('tool', '?')
                args = fact.get('args', '')
                result = fact.get('result', '(no result)')
                fact_lines.append(f"  - {tool_name}({args}) → {str(result)[:300]}")
            else:
                fact_lines.append(f"  - {fact}")
        formatted_facts = "\n".join(fact_lines)

    is_approval_turn = _detect_approval(current_messages)

    revision_context = f"""### CURRENT PIPELINE STATE & REVISION CONTEXT
- Current Modules: {current_components}
- Current Template: {current_template}
- Current Plan: {current_plan}

## Previously Gathered Tool Facts:
{formatted_facts if formatted_facts else '(none yet)'}"""

    from langchain_core.messages import SystemMessage
    # [Head / Fixed Prefix]: Invariant system prompt ensures 100% vLLM prefix cache hits across turns
    system_msg = SystemMessage(content=CONSULTANT_SYSTEM_PROMPT)
    prompt = ChatPromptTemplate.from_messages([
        system_msg,
        MessagesPlaceholder(variable_name="messages")
    ])

    from core.tool_registry import get_consultant_tools
    if is_approval_turn:
        # Skip LLM entirely — the extract node shortcircuits this turn anyway.
        # Calling the model without tools causes Devstral/Mistral to emit
        # "I don't have the tools to help" which pollutes message history.
        logger.info("consultant_approval_turn_no_tools")
        approval_msg = AIMessage(content="Understood — proceeding to build the pipeline as planned.")
        logger.info("consultant_tool_calls", count=0)
        return {"messages": [approval_msg]}

    llm_with_tools = llm.bind_tools(get_consultant_tools())
    chain = prompt | llm_with_tools
    safe_messages = _sanitize_messages_for_api(current_messages)

    # [Middle-Tail / Dynamic Context]: Prepend revision state to dynamic messages if state/facts exist
    has_active_state = (current_plan != "No plan generated yet.") or bool(current_components) or bool(formatted_facts)
    if has_active_state and safe_messages:
        # If the first message is a HumanMessage, prepend the revision context into the prompt stream
        if isinstance(safe_messages[0], HumanMessage) and "### CURRENT PIPELINE STATE" not in str(safe_messages[0].content):
            safe_messages = [HumanMessage(content=f"{revision_context.strip()}\n\n{safe_messages[0].content}")] + safe_messages[1:]
        elif not isinstance(safe_messages[0], HumanMessage):
            safe_messages = [HumanMessage(content=revision_context.strip())] + safe_messages

    try:
        result = with_exponential_backoff(chain.invoke)({"messages": safe_messages})

        tool_call_count = len(result.tool_calls) if getattr(result, "tool_calls", None) else 0
        logger.info("consultant_tool_calls", count=tool_call_count)
        return {"messages": [result]}

    except Exception as e:
        logger.error("consultant_error", error=str(e))
        return {"messages": [AIMessage(content="I encountered an error while processing. Please try again.")], "error": str(e)}


# ──────────────────────────────────────────────────────────────────────────────
# Consultant extract helpers
# ──────────────────────────────────────────────────────────────────────────────

def _get_approval_shortcircuit(messages: list, state: GraphState) -> dict:
    approval_reply = ""
    for m in reversed(messages):
        if isinstance(m, AIMessage) and m.content and not getattr(m, "tool_calls", None):
            approval_reply = m.content
            break

    prior_plan = state.get("design_plan")
    if not prior_plan:
        for m in reversed(messages):
            if isinstance(m, AIMessage) and m.content and not getattr(m, "tool_calls", None) and m.content != approval_reply:
                prior_plan = m.content
                break
        prior_plan = prior_plan or approval_reply or "Pipeline approved by user."

    selected_ids = state.get("selected_component_ids") or []
    logger.info(
        "consultant_extract_approval_shortcircuit",
        plan_chars=len(prior_plan),
        extracted_ids=len(selected_ids),
        final_ids=selected_ids,
    )

    return {
        "messages": [AIMessage(content=approval_reply or "Understood — proceeding to build the pipeline.")],
        "consultant_status": "APPROVED",
        "design_plan": prior_plan,
        "strategy_selector": state.get("strategy_selector") or "CUSTOM_BUILD",
        "used_template_id": state.get("used_template_id"),
        "selected_component_ids": selected_ids,
        "tool_memory": state.get("tool_memory") or [],
        "error": None,
    }


def _get_ai_content(messages: list) -> str:
    """Extract real assistant response text without injecting artificial filler."""
    for msg in reversed(messages):
        if isinstance(msg, AIMessage) and msg.content and not getattr(msg, 'tool_calls', None):
            return str(msg.content)

    for msg in reversed(messages):
        if isinstance(msg, AIMessage) and msg.content:
            return str(msg.content)

    return ""


def _validate_approved_components(result: ConsultantOutput, store: BaseStore) -> None:
    from core.services.knowledge_graph import kg
    if store and not kg.is_built:
        kg.build_nx_graph(store)

    if result.used_template_id:
        proj_tmpl = kg.project_vertex(result.used_template_id) if kg.is_built else None
        if proj_tmpl:
            result.used_template_id = proj_tmpl
        elif store and not store.get(("templates",), result.used_template_id):
            logger.warning("hallucinated_template", id=result.used_template_id)
            result.used_template_id = None

    if kg.is_built and result.selected_component_ids:
        # Check and bridge topological path reachability
        bridged = kg.bridge_pipeline_path(result.selected_component_ids)
        result.selected_component_ids = bridged

    for mod_id in result.selected_component_ids:
        if store and not store.get(("components",), mod_id) and not store.get(("templates",), mod_id):
            logger.warning("hallucinated_module_passed_to_hydrator", id=mod_id)


def _build_extraction_context(messages: list) -> tuple[str, list[dict]]:
    """Builds the string context and captures any new tool facts for memory."""
    conversation_summary = []
    tool_memory_new = []

    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
    
    for msg in messages[-settings.CONTEXT_WINDOW_EXTRACT:]:
        if isinstance(msg, AIMessage):
            if getattr(msg, "tool_calls", None):
                for tc in msg.tool_calls:
                    conversation_summary.append(f"[TOOL CALL] {tc.get('name', '?')}({tc.get('args', '?')})")
            if msg.content:
                conversation_summary.append(f"[CONSULTANT] {msg.content}")
        elif hasattr(msg, 'type') and msg.type == 'tool':
            result_str = str(msg.content)[:settings.MAX_TOOL_RESULT_PREVIEW] if msg.content else "(empty)"
            conversation_summary.append(f"[TOOL RESULT] {result_str}")
            if msg.content:
                tool_memory_new.append({
                    "tool": getattr(msg, 'name', 'unknown'),
                    "args": "(from conversation)",
                    "result": result_str
                })
        elif isinstance(msg, HumanMessage):
            conversation_summary.append(f"[USER] {msg.content}")
        elif isinstance(msg, SystemMessage):
            conversation_summary.append(f"[SYSTEM] {msg.content}")

    return "\n".join(conversation_summary), tool_memory_new


# ──────────────────────────────────────────────────────────────────────────────
# Consultant extract node
# ──────────────────────────────────────────────────────────────────────────────

def consultant_extract_node(state: GraphState, store: BaseStore) -> Any:  # noqa: C901
    logger.info("node_start", node="consultant_extract")
    llm = get_llm()
    messages = state.get("messages", [])

    if _detect_approval(messages) and state.get("selected_component_ids"):
        # If we already have the components, shortcircuit.
        # If not, let the extractor LLM run so it can pull them from the conversation history.
        return _get_approval_shortcircuit(messages, state)

    last_ai_content = _get_ai_content(messages)
    context_text, tool_memory_new = _build_extraction_context(messages)
    if not last_ai_content and not context_text:
        return {
            "messages": [AIMessage(content="I couldn't generate a response. Please try rephrasing your request.")],
            "error": "No consultant response to extract from"
        }

    reasoning_payload = last_ai_content if last_ai_content else "(Direct tool actions — see conversation context above)"
    extraction_prompt = ChatPromptTemplate.from_messages([
        ("system", EXTRACTOR_SYSTEM_PROMPT),
        ("human", "CONVERSATION CONTEXT:\n{context}\n\nFINAL CONSULTANT MESSAGE:\n{reasoning}")
    ])

    extractor = llm.with_structured_output(ConsultantOutput, method="json_schema", include_raw=False)
    chain = extraction_prompt | extractor

    try:
        result = chain.invoke({
            "context": context_text,
            "reasoning": reasoning_payload
        })

        if _detect_approval(messages):
            result.status = "APPROVED"
            
        logger.info("consultant_extract_status", status=result.status)

        # 1. Active Bipartite Vertex Partitioning & Canonical Projection on Knowledge Graph
        from core.services.knowledge_graph import kg
        if store and not kg.is_built:
            kg.build_nx_graph(store)

        if kg.is_built and result.selected_component_ids:
            valid_comps, helper_funcs = kg.partition_raw_ids(result.selected_component_ids)
            result.selected_component_ids = kg.expand_composite_components(valid_comps, store=store)

        if result.status == "APPROVED":
            if not result.selected_component_ids:
                import json
                extracted = []
                # Fallback: extract from verified tool memory
                for fact in ((state.get("tool_memory", []) or []) + tool_memory_new):
                    if fact.get("tool") == "lookup_catalog_item":
                        try:
                            data = json.loads(fact.get("result", "{}"))
                            if data.get("valid") and data.get("id") and data.get("id") not in extracted:
                                extracted.append(data.get("id"))
                        except Exception:
                            pass
                result.selected_component_ids = kg.expand_composite_components(extracted, store=store) if kg.is_built else extracted

            _validate_approved_components(result, store)

        is_hard_reset = (result.status == "CHATTING" and not result.draft_plan and len(result.selected_component_ids) == 0)

        # In CHATTING mode without a concrete draft plan (e.g. diagnostic probing / conflict refusal), ensure components list stays empty
        if result.status == "CHATTING" and (not result.draft_plan or len(str(result.draft_plan).strip()) < 15):
            result.selected_component_ids = []
            final_ids = []
        else:
            final_ids = result.selected_component_ids if result.selected_component_ids else ([] if is_hard_reset else state.get("selected_component_ids", []))
            if final_ids and kg.is_built:
                final_ids = kg.expand_composite_components(final_ids, store=store)

        # FIX: Ensure we slice the *combined* array, not just the new additions, to prevent infinite growth.
        combined_memory = (state.get("tool_memory", []) or []) + tool_memory_new
        pruned_memory = combined_memory[-settings.MEMORY_MAX_TOOL_FACTS:]

        if result.draft_plan:
            edges_str = "\n".join([f"- {e.upstream_component} -> [{e.channel}] -> {e.downstream_component}" for e in result.semantic_edges]) if result.semantic_edges else "None"
            inputs_str = "\n".join([f"- `{i.variable}` = `{i.source_helper}`" for i in result.input_assignments]) if result.input_assignments else "None"
            enriched_plan = f"{result.draft_plan}\n\n### INPUT ASSIGNMENTS:\n{inputs_str}\n\n### SEMANTIC BRIDGES:\n{edges_str}"
        else:
            enriched_plan = None

        state_updates = {
            "messages": [AIMessage(content=result.response_to_user)],
            "consultant_status": result.status,
            "design_plan": enriched_plan if enriched_plan else (None if is_hard_reset else state.get("design_plan")),
            "strategy_selector": result.strategy_selector if result.strategy_selector else (None if is_hard_reset else state.get("strategy_selector", "CUSTOM_BUILD")),
            "used_template_id": result.used_template_id if result.used_template_id else (None if is_hard_reset else state.get("used_template_id")),
            "selected_component_ids": final_ids,
            "tool_memory": pruned_memory,
            "error": None
        }

        if result.status == "CHATTING" or (result.status == "APPROVED" and state.get("nextflow_code")):
            state_updates["nextflow_code"] = None
            state_updates["mermaid_agent"] = None
            state_updates["mermaid_deterministic"] = None
            state_updates["ast_json"] = None

        return state_updates

    except Exception as e:
        logger.error("consultant_extract_error", error=str(e))
        return {
            "messages": [AIMessage(content="I encountered an error structuring the response. Please try again.")],
            "error": f"Consultant Extract Failed: {e!s}"
        }
