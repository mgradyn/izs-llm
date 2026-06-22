from typing import Any

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.prompts import ChatPromptTemplate

from core.config import settings
from core.models.diagram_structure import DiagramData
from core.services.graph_state import GraphState
from core.services.llm import get_llm
from core.services.prompt_loader import load_diagram_prompt
from core.services.renderer import render_mermaid_from_ast, render_mermaid_from_json
from core.utils.logger import logger
from core.utils.retry import with_exponential_backoff

DIAGRAM_SYSTEM_PROMPT = load_diagram_prompt()


def diagram_node(state: GraphState) -> Any:
    logger.info("node_start", node="diagram_agent")
    if state.get("error"):
        return {"error": state['error']}

    final_code = state.get("nextflow_code", "")
    if not final_code:
        logger.warning("diagram_no_code")
        return {"mermaid_agent": "flowchart TD\n    Empty[No code generated]"}

    llm = get_llm()
    diagram_agent = llm.with_structured_output(DiagramData, method="json_schema", include_raw=False)

    prompt = ChatPromptTemplate.from_messages([
        ("system", DIAGRAM_SYSTEM_PROMPT),
        ("human", "Map this Nextflow code into a JSON Node/Edge Graph:\n\n{code}")
    ])

    messages = prompt.invoke({"code": final_code}).to_messages()

    max_retries = settings.MAX_DIAGRAM_RETRIES
    for attempt in range(max_retries):
        try:
            result = with_exponential_backoff(diagram_agent.invoke)(messages)

            if not result or not result.nodes:
                raise ValueError("LLM returned empty graph data.")

            mermaid_string = render_mermaid_from_json(result)

            logger.info("diagram_agent_success", attempt=attempt + 1)
            return {
                "mermaid_agent": mermaid_string
            }

        except Exception as e:
            logger.error("diagram_agent_error", attempt=attempt + 1, error=str(e))

            # Extract raw LLM generation if available so the agent sees what it did wrong
            raw_output = getattr(e, "llm_output", "I generated an invalid JSON graph structure.")
            messages.append(AIMessage(content=str(raw_output)))
            messages.append(HumanMessage(content=f"Validation Error: {e!s}\nFix the data and try again."))

    return {
        "mermaid_agent": f'flowchart TD\n    Error["Agentic diagram generation failed after {max_retries} attempts."]'
    }


def deterministic_diagram_node(state: GraphState) -> Any:
    logger.info("node_start", node="deterministic_diagram")
    if state.get("error"):
        return {"error": state['error']}

    ast_json = state.get("ast_json", {})
    if not ast_json:
        logger.warning("deterministic_diagram_no_ast")
        return {"mermaid_deterministic": "flowchart TD\n    Empty[No AST generated]"}

    try:
        mermaid_string = render_mermaid_from_ast(ast_json)
        logger.info("deterministic_diagram_success", length=len(mermaid_string))
        return {
            "mermaid_deterministic": mermaid_string
        }
    except Exception as e:
        logger.error("deterministic_diagram_error", error=str(e))

        # Strip quotes from the error message to prevent Mermaid syntax crashing
        safe_err = str(e)[:100].replace('"', "'")
        return {
            "mermaid_deterministic": f'flowchart TD\n    Error["Deterministic diagram error: {safe_err}"]'
        }
