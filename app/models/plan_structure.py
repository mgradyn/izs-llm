import os
import json
from pydantic import BaseModel, Field, ConfigDict, model_validator
from typing import Literal, List, Optional, Dict

CATALOG_PATH = os.path.join(os.path.dirname(__file__), '..', '..', 'data', 'catalog', 'catalog_part1_components.json')
VALID_COMPONENT_IDS = set()

try:
    with open(CATALOG_PATH, 'r') as f:
        catalog_data = json.load(f)
        for comp in catalog_data.get('components', []):
            if 'id' in comp:
                VALID_COMPONENT_IDS.add(comp['id'])
except Exception as e:
    print(f"Warning Could not load catalog for validation {e}")

class ComponentDef(BaseModel):
    process_alias: str = Field(..., description="The unique variable name for this step.")
    source_type: Literal["RAG_COMPONENT", "CUSTOM_SCRIPT"] = Field(..., description="If the tool is not in RAG select CUSTOM_SCRIPT.")
    source_description: Optional[str] = Field(None, description="Brief description of what this component does.")
    component_id: Optional[str] = Field(None, description="The RAG ID. REQUIRED if source_type is RAG_COMPONENT.")

    input_type: Optional[str] = Field(None, description="The primary input data format.")
    output_type: Optional[str] = Field(None, description="The primary output data format.")

    @model_validator(mode='after')
    def enforce_rag_for_standard_tools(self):
        if self.source_type == "CUSTOM_SCRIPT":
            description_text = str(self.process_alias) + " " + str(self.source_description)
            description_text = description_text.lower()
            
            standard_tools = [
                # QC & Preprocessing
                "fastp", "fastqc", "nanoplot", "trimmomatic", "chopper", "bbnorm", "downsampl", "trimming",
                # Mapping & Filtering
                "bowtie", "minimap", "samtools", "krakentools",
                # Assembly
                "shovill", "spades", "unicycler", 
                # Variant Calling & Consensus
                "snippy", "ivar", "medaka",
                # Taxonomy & Classification
                "kraken", "bracken", "centrifuge", "confindr", "kmerfinder", "mash",
                # AMR, Genes & Annotation
                "abricate", "resfinder", "staramr", "prokka",
                # Typing & Lineage
                "mlst", "cgmlst", "chewbbaca", "flaa", "pangolin", "mobsuite", "westnile",
                # Multi-sample
                "panaroo", "augur", "reportree"
            ]
            
            for tool in standard_tools:
                if tool in description_text:
                    raise ValueError(
                        f"VALIDATION ERROR. You marked '{self.process_alias}' as a CUSTOM_SCRIPT. "
                        f"However '{tool}' is a standard tool. "
                        f"You must find its exact component_id in the provided RAG context and set source_type to RAG_COMPONENT."
                    )
        return self

    @model_validator(mode='after')
    def validate_real_rag_id(self):
        if self.source_type == "RAG_COMPONENT":
            if not self.component_id:
                raise ValueError("VALIDATION ERROR RAG_COMPONENT must have a component_id.")
            
            if VALID_COMPONENT_IDS and self.component_id not in VALID_COMPONENT_IDS:
                raise ValueError(
                    f"VALIDATION ERROR '{self.component_id}' is a fake component_id. "
                    f"You MUST select the exact matching ID from the allowed catalog."
                )
        return self

    @model_validator(mode='after')
    def enforce_alias_matches_id(self):
        if self.source_type == "RAG_COMPONENT" and self.component_id:
            if self.process_alias != self.component_id:
                raise ValueError(
                    f"VALIDATION ERROR For RAG components the process_alias ('{self.process_alias}') "
                    f"MUST exactly match the component_id ('{self.component_id}'). Do not invent a new alias."
                )
        return self

class OperatorMacro(BaseModel):
    macro_type: Literal[
        "COLLECT_ALL", 
        "CROSS_SYNC", 
        "MULTI_MAP_SPLIT", 
        "JOIN_BY_KEY",
        "GROUP_BY_KEY",
        "MIX_CHANNELS",
        "FILTER_DATA",
        "BRANCH_SPLIT"
    ] = Field(
        ..., 
        description="The type of logic operation to perform."
    )
    input_channels: List[str] = Field(
        ..., 
        description="The names of the channels you want to combine or map."
    )
    output_variable: str = Field(
        ..., 
        description="The clean variable name to store the final result."
    )
    mapping_rules: Optional[List[str]] = Field(
        default=[], 
        description="Rules for how to structure the output. Used mainly for MULTI_MAP_SPLIT and BRANCH_SPLIT to define keys."
    )
    condition_rules: Optional[List[str]] = Field(
        default=[],
        description="Simple condition strings used for FILTER_DATA or BRANCH_SPLIT."
    )

class LogicStep(BaseModel):
    step_type: Literal["PROCESS_RUN", "MACRO", "COMMENT"]
    description: str = Field(..., description="Brief explanation of intent.")
    code_snippet: Optional[str] = Field(
        None, 
        description="Use this ONLY for PROCESS_RUN to write the tool call. Leave empty for MACRO."
    )
    macro_details: Optional[OperatorMacro] = Field(
        None, 
        description="Use this ONLY for MACRO to define the complex channel operation safely."
    )

    @model_validator(mode='after')
    def enforce_correct_fields(self):
        if self.step_type == "PROCESS_RUN" and not self.code_snippet:
            raise ValueError("VALIDATION ERROR PROCESS_RUN steps must have a code_snippet.")
        if self.step_type == "MACRO" and not self.macro_details:
            raise ValueError("VALIDATION ERROR MACRO steps must have macro_details defined.")
        if self.step_type == "MACRO" and self.code_snippet:
            raise ValueError("VALIDATION ERROR MACRO steps should not have a code_snippet. Use macro_details instead.")
        return self

class PipelinePlan(BaseModel):
    strategy_selector: Literal["EXACT_MATCH", "ADAPTED_MATCH", "CUSTOM_BUILD"] = Field(...)
    used_template_id: Optional[str] = Field(None, description="Parent template ID if applicable.")
    workflow_name: str = Field(default="main_workflow", description="A descriptive name for the modified or custom pipeline.")
    components: List[ComponentDef] = Field(default=[], description="List of tools.")
    workflow_logic: List[LogicStep] = Field(default=[], description="Logic flow.")
    global_params: Dict[str, str] = Field(default={})

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{
                "strategy_selector": "ADAPTED_MATCH",
                "used_template_id": "target_filter_template",
                "workflow_name": "target_filter_and_verify",
                "components": [
                    {
                        "process_alias": "step_3TX_species__vdabricate",
                        "source_type": "RAG_COMPONENT",
                        "component_id": "step_3TX_species__vdabricate",
                        "input_type": "Fasta",
                        "output_type": "CSV"
                    }
                ],
                "workflow_logic": [
                    {
                        "step_type": "MACRO",
                        "description": "Sync assembly with database safely",
                        "macro_details": {
                            "macro_type": "CROSS_SYNC",
                            "input_channels": ["assembled", "abricatedatabase"],
                            "output_variable": "scaffoldsAndDatabase",
                            "mapping_rules": []
                        }
                    },
                    {
                        "step_type": "PROCESS_RUN",
                        "description": "Identify viral segments",
                        "code_snippet": "step_3TX_species__vdabricate(scaffoldsAndDatabase)"
                    },
                    {
                        "step_type": "MACRO",
                        "description": "Split results into separate channels for the next step",
                        "macro_details": {
                            "macro_type": "MULTI_MAP_SPLIT",
                            "input_channels": ["step_3TX_species__vdabricate.out.calls", "assembled", "reference"],
                            "output_variable": "filt",
                            "mapping_rules": ["calls", "assembly", "reference"]
                        }
                    }
                ],
                "global_params": {}
            }]
        }
    )