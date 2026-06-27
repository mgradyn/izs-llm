import json
from typing import Any
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from core.config import settings
from core.services.graph_state import GraphState
from core.services.llm import get_llm
from core.services.prompt_loader import load_diagram_prompt
from core.models.diagram_structure import DiagramData
from core.services.renderer import render_mermaid_from_json
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
            HumanMessage(content=f"Here is the Architect's Data Flow Plan. Please submit the final diagram structure.\n\n```json\n{plan_context}\n```")
        ]

    # Add tool results or new tool calls if any
    # (LangGraph ToolNode automatically appends ToolMessages to whatever message key it receives, if configured correctly, 
    # but wait, we need to make sure `diagram_messages` is mapped correctly in State. Let's just use `messages` for simplicity,
    # or append to `diagram_messages`.)
    # ACTUALLY, if we use `diagram_messages`, we need to define it in GraphState.
    
    llm = get_llm().with_structured_output(DiagramData)
    
    try:
        diagram_data = llm.invoke(diagram_messages)
        mermaid_string = render_mermaid_from_json(diagram_data)
        
        return {
            "diagram_messages": diagram_messages + [AIMessage(content="Diagram generated.")],
            "diagram_data": diagram_data.model_dump(),
            "mermaid_deterministic": mermaid_string,
            "mermaid_agent": mermaid_string
        }
    except Exception as e:
        logger.error(f"Failed to generate structured diagram: {e}")
        return {"error": str(e)}
