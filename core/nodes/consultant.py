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
    """Removes orphaned tool calls and patches missing tool responses."""
    answered_ids = {m.tool_call_id for m in messages if isinstance(m, LCToolMessage)}

    patched = []
    for msg in messages:
        if isinstance(msg, AIMessage) and getattr(msg, "tool_calls", None):
            # If all tool calls are unanswered AND they belong to the architect, we skip the AI message entirely
            tc_ids = {tc.get("id") or tc.get("tool_call_id") for tc in msg.tool_calls}
            tc_names = {tc.get("name", "") for tc in msg.tool_calls}

            is_orphan = (
                tc_names <= {"lookup_component_code", "validate_body_code"}
                and all(t_id not in answered_ids for t_id in tc_ids if t_id)
            )
            if is_orphan:
                continue

        patched.append(msg)

        # Patch any missing tool responses for remaining AI tool calls
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

    revision_context = f"""
    # CURRENT PIPELINE STATE
    If you are making a revision, here is the current approved state of the pipeline:
    - Current Modules: {current_components}
    - Current Template: {current_template}
    - Current Plan: {current_plan}

    ## Previously Gathered Tool Facts (from earlier in this conversation):
    {formatted_facts if formatted_facts else '(none yet)'}
    """

    from langchain_core.messages import SystemMessage
    system_msg = SystemMessage(content=CONSULTANT_SYSTEM_PROMPT + "\n\n" + revision_context)
    prompt = ChatPromptTemplate.from_messages([
        system_msg,
        MessagesPlaceholder(variable_name="messages")
    ])

    from core.tool_registry import get_consultant_tools
    if is_approval_turn:
        logger.info("consultant_approval_turn_no_tools")
        chain = prompt | llm
    else:
        llm_with_tools = llm.bind_tools(get_consultant_tools())
        chain = prompt | llm_with_tools

    safe_messages = _sanitize_messages_for_api(current_messages)

    try:
        result = with_exponential_backoff(chain.invoke)({"messages": safe_messages})

        if is_approval_turn and getattr(result, "tool_calls", None):
            logger.info("consultant_stripped_hallucinated_tool_calls", count=len(result.tool_calls))
            result = AIMessage(
                content=result.content or "Understood — I'll proceed to build the pipeline as planned.",
                id=getattr(result, "id", None),
            )

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


def _synthesize_ai_content(messages: list) -> str | None:
    for msg in reversed(messages):
        if isinstance(msg, AIMessage) and msg.content and not getattr(msg, 'tool_calls', None):
            return msg.content

    for msg in reversed(messages):
        if isinstance(msg, AIMessage) and msg.content:
            return msg.content

    tool_summaries = []
    for msg in reversed(messages[-settings.CONTEXT_WINDOW_REPAIR:]):
        if hasattr(msg, 'type') and msg.type == 'tool' and msg.content:
            tool_summaries.append(str(msg.content)[:settings.MAX_TOOL_RESULT_PREVIEW])
        if len(tool_summaries) >= 5:
            break

    if tool_summaries:
        logger.info("consultant_extract_synthesized_reasoning")
        return (
            "The consultant was investigating with tools but did not produce "
            "a final text response. Here are the most recent tool results:\n"
            + "\n---\n".join(reversed(tool_summaries))
        )
    return None


def _validate_approved_components(result: ConsultantOutput, store: BaseStore) -> None:
    if result.used_template_id and not store.get(("templates",), result.used_template_id):
        logger.warning("hallucinated_template", id=result.used_template_id)
        result.used_template_id = None

    verified_components = []
    for mod_id in result.selected_component_ids:
        if store.get(("components",), mod_id) or store.get(("templates",), mod_id):
            verified_components.append(mod_id)
        else:
            logger.warning("hallucinated_module", id=mod_id)

    result.selected_component_ids = verified_components


def _build_extraction_context(messages: list) -> tuple[str, list[dict]]:
    """Builds the string context and captures any new tool facts for memory."""
    conversation_summary = []
    tool_memory_new = []

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

    last_ai_content = _synthesize_ai_content(messages)
    if not last_ai_content:
        return {
            "messages": [AIMessage(content="I couldn't generate a response. Please try rephrasing your request.")],
            "error": "No consultant response to extract from"
        }

    context_text, tool_memory_new = _build_extraction_context(messages)

    extraction_prompt = ChatPromptTemplate.from_messages([
        ("system", EXTRACTOR_SYSTEM_PROMPT),
        ("human", "CONVERSATION CONTEXT:\n{context}\n\nFINAL CONSULTANT MESSAGE:\n{reasoning}")
    ])

    extractor = llm.with_structured_output(ConsultantOutput)
    chain = extraction_prompt | extractor

    try:
        result = chain.invoke({
            "context": context_text,
            "reasoning": last_ai_content
        })

        logger.info("consultant_extract_status", status=result.status)

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
                result.selected_component_ids = extracted

            _validate_approved_components(result, store)

        is_hard_reset = (result.status == "CHATTING" and not result.draft_plan and len(result.selected_component_ids) == 0)

        # FIX: Ensure we slice the *combined* array, not just the new additions, to prevent infinite growth.
        combined_memory = (state.get("tool_memory", []) or []) + tool_memory_new
        pruned_memory = combined_memory[-settings.MEMORY_MAX_TOOL_FACTS:]

        final_ids = result.selected_component_ids if result.selected_component_ids else ([] if is_hard_reset else state.get("selected_component_ids", []))

        state_updates = {
            "messages": [AIMessage(content=result.response_to_user)],
            "consultant_status": result.status,
            "design_plan": result.draft_plan if result.draft_plan else (None if is_hard_reset else state.get("design_plan")),
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
