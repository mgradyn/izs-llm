from typing import TypedDict, List, Optional, Dict
from langchain_core.messages import BaseMessage

class GraphState(TypedDict):
    user_query: str
    rag_context: str
    design_plan: Dict
    technical_context: str
    messages: List[BaseMessage]
    ast_json: Optional[Dict]
    validation_error: Optional[str]
    retries: int
    nextflow_code: str = ""
    mermaid_code: str = ""
    error: Optional[str] = None