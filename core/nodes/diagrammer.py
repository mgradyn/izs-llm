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

    # Extract the final generated code logic
    entrypoint_code = ast_json.get("entrypoint", {}).get("body_code", "")
    sub_workflows = ast_json.get("sub_workflows", [])
    
    code_context = f"## ENTRYPOINT CODE:\n{entrypoint_code}\n\n"
    for sw in sub_workflows:
        code_context += f"## SUB-WORKFLOW {sw.get('name', '')}:\n{sw.get('body_code', '')}\n\n"

    # Extract the Architect's declared nodes from the DataFlowPlan
    try:
        nodes = ast_json["data_flow_plan"]["nodes"]
        plan_context = json.dumps({
            "nodes": nodes,
            "entrypoint_instantiations": ast_json["data_flow_plan"].get("entrypoint_instantiations", [])
        }, indent=2)
    except Exception as e:
        logger.warning(f"diagrammer missing valid data_flow_plan: {e}")
        plan_context = json.dumps(ast_json.get("data_flow_plan", {}))

    # Build a fresh prompt every time since diagramming is a one-shot process per pipeline build
    sys_prompt = load_diagram_prompt()
    diagram_messages = [
        SystemMessage(content=sys_prompt),
        HumanMessage(content=f"Here is the Architect's Data Flow Plan and the FINAL GENERATED CODE. Please read the code to understand the exact data operations, then submit the final diagram structure and detailed operation descriptions.\n\n### DATA FLOW PLAN (Component IDs)\n```json\n{plan_context}\n```\n\n### FINAL PIPELINE CODE\n```groovy\n{code_context}\n```")
    ]
    
    llm = get_llm().with_structured_output(DiagramData, method="json_schema", include_raw=False)
    
    try:
        diagram_data = llm.invoke(diagram_messages)
        mermaid_string = render_mermaid_from_json(diagram_data)
        
        return {
            "diagram_data": diagram_data.model_dump(),
            "mermaid_deterministic": mermaid_string,
            "mermaid_agent": mermaid_string
        }
    except Exception as e:
        logger.error(f"Failed to generate structured diagram: {e}")
        return {"error": str(e)}
