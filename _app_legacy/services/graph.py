from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore
from langgraph.prebuilt import ToolNode
from langchain_core.messages import RemoveMessage, SystemMessage, AIMessage, HumanMessage, ToolMessage as LCToolMessage

from app.services.graph_state import GraphState
from app.services.agents import consultant_node, consultant_extract_node, hydrator_node, architect_precheck_node, architect_reason_node, architect_generate_node, diagram_node, deterministic_diagram_node
from app.services.repair import repair_node, should_repair
from app.services.renderer import renderer_node
from app.services.consultant_tools import CONSULTANT_TOOLS
from app.services.architect_tools import ARCHITECT_TOOLS

from app.core.config import settings

# Safety cap on tool-calling iterations to prevent runaway loops
MAX_TOOL_ITERATIONS = settings.MAX_TOOL_ITERATIONS
MAX_TOOL_ITERATIONS_APPROVAL = settings.MAX_TOOL_ITERATIONS_APPROVAL

# Memory compaction settings
MEMORY_KEEP_LAST_N = settings.MEMORY_KEEP_LAST_N
MEMORY_MAX_TOOL_FACTS = settings.MEMORY_MAX_TOOL_FACTS


def sanitize_orphaned_tool_calls(state: GraphState):
    """Inject stub ToolMessage responses for any AIMessage tool_calls that lack
    a matching ToolMessage.  This prevents the LLM API from rejecting the
    history with 'Not the same number of function calls and responses'.
    
    Typically triggered when the tool-iteration safety cap forces routing away
    from the tools node before all pending calls are answered.
    """
    messages = state.get("messages", [])
    if not messages:
        return {}

    # Collect IDs of tool calls that already have a ToolMessage response
    answered_ids = set()
    for msg in messages:
        if isinstance(msg, LCToolMessage):
            answered_ids.add(msg.tool_call_id)

    # Walk the messages and find unanswered tool calls
    stub_messages = []
    for msg in messages:
        if isinstance(msg, AIMessage) and getattr(msg, "tool_calls", None):
            for tc in msg.tool_calls:
                tc_id = tc.get("id") or tc.get("tool_call_id")
                if tc_id and tc_id not in answered_ids:
                    stub_messages.append(
                        LCToolMessage(
                            content="[Tool call skipped — iteration limit reached]",
                            tool_call_id=tc_id,
                            name=tc.get("name", "unknown"),
                        )
                    )
                    answered_ids.add(tc_id)  # avoid duplicates

    if stub_messages:
        print(f"--- [NODE] SANITIZE injected {len(stub_messages)} stub ToolMessages for orphaned calls")
        return {"messages": stub_messages}
    return {}

def check_consultant_status(state: GraphState):
    if state.get("consultant_status") == "APPROVED":
        return "approved"
    return "chatting"


def check_diagram_generation(state: GraphState):
    if state.get("generate_diagrams", True):
        return "with_diagrams"
    return "no_diagrams"


def compact_memory_node(state: GraphState):
    """Lossless memory compaction: instead of deleting messages and losing info,
    extract tool call facts into structured tool_memory before removing old messages.
    
    Strategy:
    - Keep first 2 messages (initial context) always
    - Keep last N messages (MEMORY_KEEP_LAST_N) always
    - Never remove HumanMessages or content-bearing AIMessages
    - For tool-loop messages (AIMessage with only tool_calls, ToolMessages) outside
      the keep window: extract structured facts into tool_memory, then remove
    """
    messages = state.get("messages", [])
    existing_tool_memory = state.get("tool_memory", []) or []
    
    if len(messages) <= MEMORY_KEEP_LAST_N:
        return {}
    
    # Build the keep set: first 2 + last N
    keep_indices = set(range(min(2, len(messages))))  # First 2
    keep_indices.update(range(max(0, len(messages) - MEMORY_KEEP_LAST_N), len(messages)))  # Last N
    
    # Also always keep SystemMessages and HumanMessages
    for i, msg in enumerate(messages):
        if isinstance(msg, SystemMessage):
            keep_indices.add(i)
        elif isinstance(msg, HumanMessage):
            keep_indices.add(i)
        elif isinstance(msg, AIMessage) and msg.content and not getattr(msg, 'tool_calls', None):
            # Keep AI messages that have real content (not just tool call stubs)
            keep_indices.add(i)
    
    # Extract facts from messages we're about to remove
    new_facts = []
    delete_actions = []
    
    for i, msg in enumerate(messages):
        if i in keep_indices:
            continue
        
        # Extract tool call info from AI messages before removing
        if isinstance(msg, AIMessage) and getattr(msg, 'tool_calls', None):
            for tc in msg.tool_calls:
                args_str = str(tc.get('args', {}))[:200]
                new_facts.append({
                    "tool": tc.get('name', 'unknown'),
                    "args": args_str,
                    "result": None,  # Will be filled from the ToolMessage
                })
            delete_actions.append(RemoveMessage(id=msg.id))
        
        # Extract tool results before removing
        elif isinstance(msg, LCToolMessage):
            result_preview = str(msg.content)[:500] if msg.content else "(empty)"
            # Try to attach to the last fact that has no result yet
            for fact in reversed(new_facts):
                if fact["result"] is None:
                    fact["result"] = result_preview
                    break
            else:
                # Standalone tool result — create a new fact
                new_facts.append({
                    "tool": getattr(msg, 'name', 'unknown'),
                    "args": "(from prior call)",
                    "result": result_preview,
                })
            delete_actions.append(RemoveMessage(id=msg.id))
    
    # Merge new facts into existing tool memory, cap at MEMORY_MAX_TOOL_FACTS
    merged_memory = existing_tool_memory + new_facts
    merged_memory = merged_memory[-MEMORY_MAX_TOOL_FACTS:]
    
    updates = {}
    if delete_actions:
        updates["messages"] = delete_actions
    if new_facts:
        updates["tool_memory"] = merged_memory
    
    if updates:
        print(f"--- [NODE] GRAPH compacted {len(delete_actions)} messages and got {len(new_facts)} facts")
        return updates
    return {}

def build_consultant_subgraph():
    """Consultant subgraph with ReAct tool-calling loop:
    
    consultant → [tool_calls?] → tools → consultant (loop)
                    ↓ (no tool_calls)
               sanitize → consultant_extract → compact_memory → END
    """
    sub = StateGraph(GraphState)
    
    # Nodes
    sub.add_node("consultant", consultant_node)
    sub.add_node("tools", ToolNode(CONSULTANT_TOOLS, handle_tool_errors=True))
    sub.add_node("sanitize", sanitize_orphaned_tool_calls)
    sub.add_node("consultant_extract", consultant_extract_node)
    sub.add_node("compact_memory", compact_memory_node)
    
    # Entry
    sub.set_entry_point("consultant")
    
    # Routing: if consultant produced tool_calls → tools, else → sanitize → extract
    def route_consultant(state: GraphState):
        messages = state.get("messages", [])
        if not messages:
            return "sanitize"
        last_msg = messages[-1]
        if hasattr(last_msg, "tool_calls") and last_msg.tool_calls:
            # Count tool messages only since the last HumanMessage (per-turn reset)
            tool_msg_count = 0
            for m in reversed(messages):
                if isinstance(m, HumanMessage):
                    break  # Hit the current turn boundary — stop counting
                if isinstance(m, LCToolMessage):
                    tool_msg_count += 1
            
            # Detect if the user's last message is an approval → use tighter limit
            last_human_text = ""
            for m in reversed(messages):
                if isinstance(m, HumanMessage):
                    content = m.content
                    if isinstance(content, list):
                        last_human_text = " ".join([c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text"])
                    elif isinstance(content, str):
                        last_human_text = content
                    last_human_text = last_human_text.strip().lower()
                    break
            
            # Clean punctuation for matching
            clean_human = last_human_text.rstrip("!.,;:?")
            
            # Only cut off tool calling if it's strictly "approved"
            is_approval = (clean_human == "approved")
            
            print(f"--- [NODE] GRAPH routing: last_human='{clean_human}', is_approval={is_approval}")
            
            effective_limit = MAX_TOOL_ITERATIONS_APPROVAL if is_approval else MAX_TOOL_ITERATIONS
            
            if tool_msg_count >= effective_limit:
                print(f"--- [NODE] GRAPH tool limit of {effective_limit} reached (is_approval={is_approval}, count={tool_msg_count}). forcing extraction")
                return "sanitize"
            return "tools"
        return "sanitize"
    
    sub.add_conditional_edges("consultant", route_consultant, {
        "tools": "tools",
        "sanitize": "sanitize"
    })
    
    # After tools execute, loop back to consultant for next reasoning step
    sub.add_edge("tools", "consultant")
    
    # After sanitizing orphaned tool calls, proceed to extraction
    sub.add_edge("sanitize", "consultant_extract")
    
    # After extraction, compact memory (lossless) and exit
    sub.add_edge("consultant_extract", "compact_memory")
    sub.add_edge("compact_memory", END)
    
    return sub.compile()

def build_execution_subgraph():
    sub = StateGraph(GraphState)
    sub.add_node("hydrator", hydrator_node)
    sub.add_node("architect_precheck", architect_precheck_node)
    sub.add_node("architect_reason", architect_reason_node)
    sub.add_node("architect_tools", ToolNode(ARCHITECT_TOOLS, handle_tool_errors=True))
    sub.add_node("architect_generate", architect_generate_node)
    sub.add_node("repair", repair_node)
    sub.add_node("renderer", renderer_node)
    sub.add_node("diagram", diagram_node)
    sub.add_node("deterministic_diagram", deterministic_diagram_node)
    
    # Inner loop for tool calling
    MAX_ARCHITECT_TOOL_ITERATIONS = settings.MAX_ARCHITECT_TOOL_ITERATIONS
    
    sub.set_entry_point("hydrator")
    sub.add_edge("hydrator", "architect_precheck")  # Deterministic channel/void check
    sub.add_edge("architect_precheck", "architect_generate")  # Then generate
    
    # Architect generate → check if valid
    sub.add_conditional_edges(
        "architect_generate",
        should_repair,
        {
            "success": "renderer",
            "repair": "repair",
            "fail": "renderer"
        }
    )
    
    # Repair → architect_reason (on retry, investigate with tools first)
    sub.add_edge("repair", "architect_reason")
    
    # Architect reason routing: tool calls → tools loop, else → generate
    def route_architect_reason(state: GraphState):
        messages = state.get("messages", [])
        if not messages:
            return "architect_generate"
        last_msg = messages[-1]
        if hasattr(last_msg, "tool_calls") and last_msg.tool_calls:
            # Count architect tool messages (since last repair/human message)
            arch_tool_count = 0
            for m in reversed(messages):
                if isinstance(m, HumanMessage):
                    break
                if isinstance(m, LCToolMessage):
                    arch_tool_count += 1
            if arch_tool_count >= MAX_ARCHITECT_TOOL_ITERATIONS:
                print(f"--- [NODE] GRAPH architect tool limit reached ({arch_tool_count}). proceeding to generate")
                return "architect_generate"
            return "architect_tools"
        return "architect_generate"
    
    sub.add_conditional_edges("architect_reason", route_architect_reason, {
        "architect_tools": "architect_tools",
        "architect_generate": "architect_generate"
    })
    
    # After architect tools, loop back to architect reason
    sub.add_edge("architect_tools", "architect_reason")
    sub.add_conditional_edges(
        "renderer",
        check_diagram_generation,
        {
            "with_diagrams": "deterministic_diagram",
            "no_diagrams": END,
        }
    )

    # Agentic diagram runs after deterministic diagram to keep ordering predictable.
    sub.add_edge("deterministic_diagram", "diagram")
    sub.add_edge("diagram", END)
    
    return sub.compile()

def build_graph():
    # Retaining InMemorySaver as requested by the user
    checkpointer = InMemorySaver()
    store = InMemoryStore()

    sub_planner = build_consultant_subgraph()
    sub_executor = build_execution_subgraph()

    workflow = StateGraph(GraphState)
    workflow.add_node("planner", sub_planner)
    workflow.add_node("executor", sub_executor)
    
    workflow.set_entry_point("planner")

    workflow.add_conditional_edges(
        "planner",
        check_consultant_status,
        {
            "chatting": END,
            "approved": "executor"
        }
    )
    
    workflow.add_edge("executor", END)

    return workflow.compile(checkpointer=checkpointer, store=store), store

app_graph, global_store = build_graph()