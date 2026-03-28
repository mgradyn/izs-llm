from pydantic import BaseModel, Field, field_validator, model_validator
import re
from typing import List, Optional

class ImportItem(BaseModel):
    module_path: str = Field(
        description="Path to the module. MUST start with '../steps/' or '../functions/'. NEVER use 'nf-core'."
    )
    functions: List[str] = Field(description="List of process names to import.")

    @field_validator('functions')
    def validate_aliases(cls, v):
        """Enforce correct 'as' alias formatting."""
        cleaned = []
        for func in v:
            if ' as ' in func:
                parts = func.split(' as ')
                if len(parts) != 2 or not parts[0].strip() or not parts[1].strip():
                    raise ValueError(f"Invalid alias format: '{func}'. Use 'OriginalName as AliasName'")
            cleaned.append(func)
        return cleaned

    @field_validator('module_path')
    def forbid_nf_core(cls, v):
        if 'nf-core' in v:
            raise ValueError(
                f"HALLUCINATION DETECTED: 'nf-core' paths are strictly forbidden. "
                f"You MUST use local paths based on the tool prefix (e.g., '../steps/...' or '../modules/...'). Got: {v}"
            )
        return v
        
    @model_validator(mode='after')
    def auto_fix_module_paths(self):
        """Automatically correct paths based on the function prefix."""
        if "../functions/" in self.module_path:
            return self

        for func in self.functions:
            base_name = func.split(' as ')[0].strip()
            if base_name.startswith('multi_'):
                self.module_path = f"../multi/{base_name}"
            elif base_name.startswith('step_'):
                self.module_path = f"../steps/{base_name}"
            elif base_name.startswith('module_'):
                self.module_path = f"../modules/{base_name}"
        return self
    
class GlobalDef(BaseModel):
    type: str = Field(description="The definition keyword, usually 'def'.")
    name: str = Field(description="The variable name.")
    value: str = Field(description="The string value.")

class InlineProcess(BaseModel):
    name: str = Field(description="The name of the custom process.")
    container: Optional[str] = None
    input_declarations: List[str] = []
    output_declarations: List[str] = []
    script_block: str = Field(description="The raw bash script.")

    @field_validator('script_block')
    def validate_no_dsl(cls, v):
        """Forbid DSL2 logic inside bash scripts."""
        forbidden = ['workflow', '.cross(', '.join(', '.multiMap', '.map{{', '.mix(']
        for kw in forbidden:
            if kw in v:
                raise ValueError(
                    f"INVALID PROCESS CONTENT: Found DSL2 keyword '{kw}' inside a Process script.\n"
                    f"Processes are for BASH/SHELL commands only. If you need logic, define a 'sub_workflow'."
                )
        return v
    
    @field_validator('name')
    def validate_name(cls, v):
        """Forbid RAG names or UPPERCASE names in inline processes."""
        if v.startswith("step_") or v.startswith("multi_"):
            raise ValueError(f"Process name '{v}' starts with a reserved prefix. Standard tools MUST be imported, not defined inline.")
        if v.isupper():
            raise ValueError(f"Process '{v}' is UPPERCASE. It should likely be a Global Constant, not a Process.")
        return v

class WorkflowBlock(BaseModel):
    name: str = Field(description="The name of the workflow.")
    take_channels: List[str] = Field(default=[], description="List of input channel names.")
    emit_channels: List[str] = Field(default=[], description="List of output channel names.")
    body_code: str = Field(
        description="The raw Groovy logic. DO NOT write 'workflow {{ }}' wrappers here. Just write the inner logic."
    )

    @field_validator('body_code')
    def forbid_workflow_wrapper(cls, v):
        if re.search(r'^\s*workflow\s+[_a-zA-Z0-9]*\s*\{', v) or re.search(r'^\s*workflow\s*\{', v) or 'main:' in v:
            raise ValueError(
                "FORMAT ERROR: DO NOT wrap the body_code in 'workflow {{ ... }}' or 'main:'. "
                "The rendering engine does this automatically. Only write the actual steps and operators inside the body."
            )
        return v

    @model_validator(mode='after')
    def forbid_recursion(self):
        """Prevent workflows from calling themselves."""
        if self.name and self.body_code:
            pattern = rf"\b{self.name}\b\s*\("
            if re.search(pattern, self.body_code):
                raise ValueError(f"RECURSION ERROR: Workflow '{self.name}' is trying to call itself. This is forbidden.")
        return self

class Entrypoint(BaseModel):
    body_code: str = Field(
        description="The code inside the main unnamed workflow. Do not write 'workflow { }'."
    )

    @field_validator('body_code')
    def forbid_workflow_wrapper(cls, v):
        if re.search(r'^\s*workflow\s*\{', v):
            raise ValueError("FORMAT ERROR: DO NOT wrap the entrypoint body_code in 'workflow { ... }'.")
        return v

class NextflowPipelineAST(BaseModel):
    imports: List[ImportItem] = []
    globals: List[GlobalDef] = []
    inline_processes: List[InlineProcess] = []
    sub_workflows: List[WorkflowBlock] = []
    entrypoint: Entrypoint

    @model_validator(mode='after')
    def enforce_defined_processes(self):
        """If a step_ or multi_ tool is used in the body, it MUST be imported."""
        allowed_callables = set()
        
        for imp in self.imports:
            for func in imp.functions:
                alias = func.split(' as ')[-1].strip()
                allowed_callables.add(alias)
        for p in self.inline_processes:
            allowed_callables.add(p.name)
        for sw in self.sub_workflows:
            allowed_callables.add(sw.name)

        all_code = self.entrypoint.body_code
        for sw in self.sub_workflows:
            all_code += "\n" + sw.body_code

        pattern = re.compile(r'\b((?:step_|multi_|module_|prepare_)[a-zA-Z0-9_]+)\s*\(')
        for match in pattern.finditer(all_code):
            func_name = match.group(1)
            if func_name not in allowed_callables:
                raise ValueError(
                    f"VALIDATION ERROR: The process '{func_name}' is used in your code but NEVER IMPORTED. "
                    f"You MUST add '{func_name}' to the 'imports' list so Nextflow knows where to find it."
                )
        return self

    @model_validator(mode='after')
    def enforce_workflow_usage(self):
        """If you define a sub_workflow, you must actually use it."""
        all_code = self.entrypoint.body_code
        for sw in self.sub_workflows:
            pattern = rf"\b{sw.name}\b\s*\("
            if not re.search(pattern, all_code):
                raise ValueError(
                    f"VALIDATION ERROR: The sub_workflow '{sw.name}' is defined but NEVER CALLED in the pipeline. "
                    f"Either call it in the entrypoint workflow or remove it."
                )
        return self