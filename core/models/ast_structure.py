import re
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

# ──────────────────────────────────────────────────────────────────────────────
# CATALOG REGISTRY & COMPILER
# ──────────────────────────────────────────────────────────────────────────────
from core.catalog_registry import get_registry
from core.services.ast_compiler import (
    _is_void_tool,
    generate_imports_for_code,
    heal_workflow_body,
    validate_framework_components,
)


class ImportItem(BaseModel):
    module_path: str = Field(
        description="Path to the module or local file."
    )
    functions: list[str] = Field(description="List of process names to import.")

    @field_validator('functions', mode='before')
    @classmethod
    def prevent_null_lists(cls, v: Any) -> Any:
        return v if v is not None else []

    @field_validator('functions')
    @classmethod
    def validate_aliases(cls, v: Any) -> Any:
        """Enforce correct 'as' alias formatting."""
        cleaned = []
        for func in v:
            if ' as ' in func:
                parts = func.split(' as ')
                if len(parts) != 2 or not parts[0].strip() or not parts[1].strip():
                    raise ValueError(f"Invalid alias format: '{func}'. Use 'OriginalName as AliasName'")
            cleaned.append(func)
        return cleaned


class GlobalDef(BaseModel):
    type: str = Field(description="The definition keyword, usually 'def'.")
    name: str = Field(description="The variable name.")
    value: str = Field(description="The string value.")

    @field_validator('value')
    @classmethod
    def forbid_active_channels(cls, v: Any) -> Any:
        """Blocks the LLM from putting active channel instantiations in the globals block."""
        try:
            from core.plugin_loader import get_active_plugin
            plugin = get_active_plugin()
            plugin_helpers = list(plugin.helper_imports.keys())
        except Exception:
            plugin_helpers = ['get', 'param']

        if '(' in v and ')' in v and (any(kw in v for kw in plugin_helpers) or 'Channel' in v):
            raise ValueError(f"GLOBAL SCOPE ERROR: Active func '{v}' in globals. Move to entrypoint body_code.")
        return v

class InlineProcess(BaseModel):
    name: str = Field(description="The name of the custom process.")
    container: str | None = None
    input_declarations: list[str] = Field(default_factory=list)
    output_declarations: list[str] = Field(default_factory=list)
    script_block: str = Field(description="The raw bash script.")

    @field_validator('input_declarations', 'output_declarations', mode='before')
    @classmethod
    def prevent_null_lists(cls, v: Any) -> Any:
        return v if v is not None else []

    @field_validator('script_block')
    @classmethod
    def validate_no_dsl(cls, v: Any) -> Any:
        """Forbid DSL2 logic inside bash scripts."""
        forbidden = ['workflow', '.cross(', '.join(', '.multiMap', '.map{', '.mix(']
        for kw in forbidden:
            if kw in v:
                raise ValueError(f"DSL2 keyword '{kw}' inside Process. Use sub_workflow for logic.")
        return v

    @field_validator('name')
    @classmethod
    def validate_name(cls, v: str) -> str:
        """Forbid RAG names or UPPERCASE names in inline processes."""
        try:
            exists = get_registry().component_exists(v)
        except Exception:
            exists = False

        if exists:
            raise ValueError(f"Process name '{v}' exists in the component catalog. Standard tools MUST be imported, not defined inline.")

        if v.isupper():
            raise ValueError(f"Process '{v}' is UPPERCASE. It should likely be a Global Constant, not a Process.")
        return v

class WorkflowBlock(BaseModel):
    name: str = Field(description="The name of the workflow.")
    take_channels: list[str] = Field(default_factory=list, description="List of input channel names.")
    emit_channels: list[str] = Field(default_factory=list, description="List of output channel names.")
    body_code: str = Field(description="The raw Groovy logic.")

    @field_validator('take_channels', 'emit_channels', mode='before')
    @classmethod
    def prevent_null_lists(cls, v: Any) -> Any:
        return v if v is not None else []

    @model_validator(mode='before')
    @classmethod
    def rescue_and_heal_body(cls, data: dict) -> dict:
        if not isinstance(data, dict): return data

        body = data.get('body_code', '')
        if not isinstance(body, str): return data

        cleaned_body, extracted_emits = heal_workflow_body(body)

        existing_emits = data.get('emit_channels', []) or []
        for em in extracted_emits:
            if em not in existing_emits:
                existing_emits.append(em)

        data['body_code'] = cleaned_body
        data['emit_channels'] = existing_emits

        return data

    @field_validator('take_channels')
    @classmethod
    def validate_take_identifiers(cls, v: Any) -> Any:
        """Ensures take LHS is a valid Groovy identifier."""
        for ch in v:
            cleaned = ch.strip()
            if cleaned and not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', cleaned):
                raise ValueError(f"TAKE ERROR: '{cleaned}' is invalid identifier.")
        return [ch.strip() for ch in v if ch.strip()]

    @field_validator('emit_channels')
    @classmethod
    def validate_emit_format(cls, v: Any) -> Any:
        for emit_str in v:
            if '(' in emit_str or ')' in emit_str:
                raise ValueError(f"EMIT ERROR: '{emit_str}' invalid. No logic allowed, only assignments like 'res = proc.res'.")
        return v

    @field_validator('emit_channels')
    @classmethod
    def validate_emit_identifiers(cls, v: Any) -> Any:
        """Ensures emit LHS is a valid Groovy identifier."""
        for emit_str in v:
            if '=' in emit_str:
                lhs = emit_str.split('=')[0].strip()
                if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', lhs):
                    raise ValueError(f"EMIT NAME ERROR: '{lhs}' is invalid identifier.")
            else:
                cleaned = emit_str.strip()
                if cleaned and not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', cleaned):
                    raise ValueError(f"EMIT ERROR: '{cleaned}' is invalid identifier.")
        return v

    @model_validator(mode='after')
    def enforce_take_channel_usage(self) -> Any:
        if not self.take_channels:
            return self

        combined_text = self.body_code + " " + " ".join(self.emit_channels)

        for ch in self.take_channels:
            pattern = rf"\b{re.escape(ch)}\b"
            if not re.search(pattern, combined_text):
                raise ValueError(f"LOGIC ERROR: '{ch}' in take_channels of '{self.name}' is unused and not emitted.")
        return self

    @model_validator(mode='after')
    def forbid_recursion(self) -> Any:
        if self.name and self.body_code:
            pattern = rf"\b{self.name}\b\s*\("
            if re.search(pattern, self.body_code):
                raise ValueError(f"RECURSION ERROR: Workflow '{self.name}' is trying to call itself. This is forbidden.")
        return self


    @model_validator(mode='after')
    def enforce_variable_existence(self) -> Any:
        """Ensures that any variable emitted actually exists in the take_channels or body_code."""
        if not self.body_code:
            return self

        valid_vars = set(self.take_channels)

        # Catch assignments (e.g., my_var = ... or Channel my_var = ...)
        assignments = re.findall(r'\b([a-zA-Z0-9_]+)\s*=(?!=)', self.body_code)
        valid_vars.update(assignments)

        # Catch .set { my_var }
        sets = re.findall(r'\.set\s*\{\s*([a-zA-Z0-9_]+)\s*\}', self.body_code)
        valid_vars.update(sets)

        process_calls = re.findall(r'\b([a-zA-Z0-9_]+)\s*\(', self.body_code)
        valid_vars.update(process_calls)

        for emit_str in self.emit_channels:
            rhs = emit_str.split('=')[-1].strip()

            base_var = re.split(r'[\.\[]', rhs)[0].strip()

            if not base_var or base_var.startswith("'") or base_var.startswith('"') or base_var in ['true', 'false', 'null', 'Channel', 'get', 'param']:
                continue

            if base_var not in valid_vars:
                raise ValueError(f"HALLUCINATION in '{self.name}': Emitting undefined variable '{base_var}'. Did you misspell it, forget to assign it, or emit a void tool?")
        return self

    @model_validator(mode='after')
    def forbid_set_on_processes(self) -> Any:
        if not self.body_code:
            return self

        if re.search(r'\b[a-zA-Z0-9_]+\s*\([^)]*\)\s*\.set\s*\{', self.body_code):
            raise ValueError(f"SYNTAX ERROR in '{self.name}': Do not use .set on processes. Use assignment 'var = process(...)' or call directly if void tool.")
        return self

    @model_validator(mode='after')
    def forbid_void_tool_assignment(self) -> Any:
        """Safety net: catches void tool assignments that survived deterministic healing."""
        if not self.body_code:
            return self

        assignment_matches = re.finditer(
            r'\b[a-zA-Z0-9_]+\s*=\s*([a-zA-Z0-9_]+)\s*\(',
            self.body_code
        )
        for m in assignment_matches:
            proc_name = m.group(1)
            if _is_void_tool(proc_name):
                raise ValueError(f"VOID TOOL ERROR in '{self.name}': Assigned void tool '{proc_name}' to a variable. Call it directly.")
        return self



class Entrypoint(BaseModel):
    body_code: str = Field(
        description="The code inside the main unnamed workflow. Do not write 'workflow {{ }}'."
    )

    @field_validator('body_code', mode='before')
    @classmethod
    def auto_heal_entrypoint(cls, v: Any) -> Any:
        """Silently cleans up the entrypoint logic."""
        if not isinstance(v, str): return v
        cleaned_body, _ = heal_workflow_body(v)
        return cleaned_body



class NextflowPipelineAST(BaseModel):
    reasoning: str | None = Field(None, description="Explain your thought process, what you are fixing, and how you addressed any validation errors. Do NOT place conversational text in the code fields.")
    imports: list[ImportItem] = Field(default_factory=list)
    globals: list[GlobalDef] = Field(default_factory=list)
    inline_processes: list[InlineProcess] = Field(default_factory=list)
    sub_workflows: list[WorkflowBlock] = Field(default_factory=list)
    entrypoint: Entrypoint

    @field_validator('imports', 'globals', 'inline_processes', 'sub_workflows', mode='before')
    @classmethod
    def prevent_null_lists(cls, v: Any) -> Any:
        if v is None:
            return []
        return v

    @model_validator(mode='before')
    @classmethod
    def auto_relocate_active_globals(cls, data: dict) -> dict:
        """Deterministically moves active channel calls from globals to entrypoint."""
        if not isinstance(data, dict): return data
        globals_list = data.get('globals', [])
        if not globals_list: return data

        try:
            from core.plugin_loader import get_active_plugin
            plugin = get_active_plugin()
            active_keywords = ['Channel', *list(plugin.helper_imports.keys())]
        except Exception:
            active_keywords = ['Channel', 'get', 'param']

        safe_globals = []
        relocated_lines = []
        for g in globals_list:
            if not isinstance(g, dict):
                safe_globals.append(g)
                continue
            val = g.get('value', '')
            if '(' in val and ')' in val and any(kw in val for kw in active_keywords):
                name = g.get('name', 'unknown')
                relocated_lines.append(f"{name} = {val}")
            else:
                safe_globals.append(g)

        if relocated_lines:
            data['globals'] = safe_globals
            ep = data.get('entrypoint', {})
            if isinstance(ep, dict):
                existing_body = ep.get('body_code', '')
                prefix = '\n'.join(relocated_lines)
                ep['body_code'] = f"{prefix}\n{existing_body}" if existing_body else prefix
                data['entrypoint'] = ep

        return data

    @model_validator(mode='after')
    def auto_generate_imports(self) -> Any:
        all_code = self.entrypoint.body_code
        for sw in self.sub_workflows:
            all_code += "\n" + sw.body_code
        for ip in self.inline_processes:
            all_code += "\n" + ip.script_block
        for g in self.globals:
            all_code += "\n" + g.value

        defined_sws = {sw.name for sw in self.sub_workflows}
        import_map = generate_imports_for_code(all_code, defined_sws)

        new_imports = []
        for path, funcs in import_map.items():
            new_imports.append(ImportItem(module_path=path, functions=sorted(funcs)))

        self.imports = new_imports
        return self

    @model_validator(mode='after')
    def enforce_framework_components(self) -> Any:
        """Ensures referenced tools/processes exist in the catalog."""
        all_code = self.entrypoint.body_code
        for sw in self.sub_workflows:
            all_code += "\n" + sw.body_code

        defined_sws = {sw.name for sw in self.sub_workflows}
        defined_inline = {ip.name for ip in self.inline_processes}

        invalid = validate_framework_components(all_code, defined_sws, defined_inline)

        if invalid:
            error_details = []
            for item, matches in sorted(invalid, key=lambda x: x[0]):
                suggestion = f" (Did you mean: {', '.join(matches)}?)" if matches else ""
                error_details.append(f"  - {item}{suggestion}")
            details_str = "\n".join(error_details)

            raise ValueError(f"CATALOG ERROR: Missing components/processes:\n{details_str}\nReplace with valid catalog components.")
        return self

    @model_validator(mode='after')
    def enforce_workflow_usage(self) -> Any:
        """If you define a sub_workflow, you must actually use it."""
        all_code = self.entrypoint.body_code
        for sw in self.sub_workflows:
            all_code += "\n" + sw.body_code

        for sw in self.sub_workflows:
            pattern = rf"\b{sw.name}\b\s*\("
            if not re.search(pattern, all_code):
                raise ValueError(f"UNUSED WORKFLOW: '{sw.name}' is defined but NEVER CALLED.")
        return self

    @model_validator(mode='after')
    def validate_no_undefined_variables(self) -> Any:
        from core.services.ast_compiler import validate_undefined_variables
        
        global_vars = {g.name for g in self.globals}
        
        # Check Entrypoint
        undefined_ep = validate_undefined_variables(self.entrypoint.body_code, global_vars)
        if undefined_ep:
            raise ValueError(f"UNDEFINED VAR in entrypoint: Variables {', '.join(undefined_ep)} used but not defined. Did you forget to getSingleInput()?")
            
        # Check SubWorkflows
        for sw in self.sub_workflows:
            defined = set(sw.take_channels) | global_vars
            undefined_sw = validate_undefined_variables(sw.body_code, defined)
            if undefined_sw:
                raise ValueError(f"UNDEFINED VAR in '{sw.name}': Variables {', '.join(undefined_sw)} used but not defined in take_channels or locally.")
                
        return self
