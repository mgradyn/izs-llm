from pydantic import BaseModel, Field, ConfigDict
from typing import Literal, List, Optional, Dict

# --- Component Definition ---
class ComponentDef(BaseModel):
    process_alias: str = Field(..., description="The unique variable name for this step (e.g. 'custom_filter').")
    source_type: Literal["RAG_COMPONENT", "CUSTOM_SCRIPT"] = Field(..., description="If the tool is not in RAG, select CUSTOM_SCRIPT.")
    source_description: Optional[str] = Field(None, description="Brief description of what this component does.")
    component_id: Optional[str] = Field(None, description="The RAG ID. REQUIRED if source_type is RAG_COMPONENT. Must be NULL if source_type is CUSTOM_SCRIPT.")

# --- Logic Definition ---
class LogicStep(BaseModel):
    step_type: Literal["PROCESS_RUN", "OPERATOR", "COMMENT"]
    description: str = Field(..., description="Brief explanation of intent.")
    code_snippet: str = Field(..., description="Simplified logic string.")

# --- The Blueprint ---
class PipelinePlan(BaseModel):
    strategy_selector: Literal["EXACT_MATCH", "ADAPTED_MATCH", "CUSTOM_BUILD"] = Field(...)
    used_template_id: Optional[str] = Field(None, description="Parent template ID if applicable.")
    components: List[ComponentDef] = Field(default=[], description="List of tools.")
    workflow_logic: List[LogicStep] = Field(default=[], description="Logic flow.")
    global_params: Dict[str, str] = Field(default={})

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{
                "strategy_selector": "CUSTOM_BUILD",
                "used_template_id": None,
                "components": [
                    # RAG Component (Has ID)
                    {"process_alias": "fastqc", "source_type": "RAG_COMPONENT", "component_id": "tool_fastqc_v1"},
                    # Custom Script (No ID)
                    {"process_alias": "custom_parser", "source_type": "CUSTOM_SCRIPT", "component_id": None}
                ],
                "workflow_logic": [
                    {"step_type": "PROCESS_RUN", "description": "Run QC", "code_snippet": "fastqc(input)"},
                    {"step_type": "PROCESS_RUN", "description": "Parse stats", "code_snippet": "custom_parser(fastqc.out)"}
                ]
            }]
        }
    )