import json
from typing import Any
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from core.config import settings
from core.services.graph_state import GraphState
from core.services.llm import get_llm
from core.services.prompt_loader import load_diagram_prompt
from core.tool_registry import get_diagrammer_tools
from core.utils.logger import logger

def diagram_reason_node(state: GraphState) -> dict:
    logger.info("node_start", node="diagram_reason")
    if state.get("error"): return {}

    # Check if the architect successfully output an AST
    ast_json = state.get("ast_json")
    if not ast_json:
        return {"error": "No AST generated. Cannot diagram."}

    # Extract the Architect's declared nodes from the DataFlowPlan
    try:
        nodes = ast_json["data_flow_plan"]["nodes"]
        # Format the context for the Diagrammer
        plan_context = json.dumps({
            "nodes": nodes,
            "entrypoint_instantiations": ast_json["data_flow_plan"].get("entrypoint_instantiations", []),
            "sub_workflows": ast_json["data_flow_plan"].get("sub_workflows", [])
        }, indent=2)
    except Exception as e:
        logger.warning(f"diagrammer missing valid data_flow_plan: {e}")
        plan_context = json.dumps(ast_json.get("data_flow_plan", {}))

    # We use a separate subset of messages for the diagrammer so it doesn't get confused by Architect's tools
    diagram_messages = state.get("diagram_messages", [])
    if not diagram_messages:
        sys_prompt = load_diagram_prompt()
        diagram_messages = [
            SystemMessage(content=sys_prompt),
            HumanMessage(content=f"Here is the Architect's Data Flow Plan. Please research the components and submit the diagram structure.\n\nYou MUST use the `submit_diagram_structure` tool to output the diagram. Do not reply with conversational text.\n\n```json\n{plan_context}\n```")
        ]

    # Add tool results or new tool calls if any
    # (LangGraph ToolNode automatically appends ToolMessages to whatever message key it receives, if configured correctly, 
    # but wait, we need to make sure `diagram_messages` is mapped correctly in State. Let's just use `messages` for simplicity,
    # or append to `diagram_messages`.)
    # ACTUALLY, if we use `diagram_messages`, we need to define it in GraphState.
    
    llm = get_llm().bind_tools(get_diagrammer_tools(), tool_choice="any")
    result = llm.invoke(diagram_messages)

    return {"diagram_messages": [result]}
