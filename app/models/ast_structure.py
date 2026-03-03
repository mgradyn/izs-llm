from pydantic import BaseModel, Field, field_validator, model_validator
import re
from typing import Any, Dict, Literal, List, Optional, Union

def repair_lazy_calls(statements: List[Any]) -> List[Any]:
    if not isinstance(statements, list): return statements

    cleaned = []
    for stmt in statements:
        if isinstance(stmt, dict):
            if stmt.get('type') == 'conditional':
                stmt['body'] = repair_lazy_calls(stmt.get('body', []))
                cleaned.append(stmt)
                continue

            if stmt.get('type') == 'assignment':
                val = stmt.get('value', '').strip()
                var = stmt.get('variable')
                
                match = re.match(r'^([a-zA-Z0-9_]+)\s*\((.*)\)(\.[a-zA-Z0-9_]+)?$', val)
                
                if match and any(x in val for x in ["step_", "prepare_", "module_", "get", "multi_"]):
                    proc_name = match.group(1)
                    raw_args = match.group(2)
                    suffix = match.group(3)
                    
                    args_list = [a.strip() for a in raw_args.split(',')] if raw_args.strip() else []
                    
                    new_stmt = {
                        "type": "process_call",
                        "process_name": proc_name,
                        "args": args_list,
                        "assign_to": var,
                        "output_attribute": suffix[1:] if suffix else None
                    }
                    cleaned.append(new_stmt)
                    continue 
        
        cleaned.append(stmt)
    
    return cleaned

class ImportItem(BaseModel):
    module_path: str = Field(..., description="Path to the module. MUST start with '../steps/' or '../functions/'.")
    functions: List[str] = Field(..., description="List of process names to import.")

    @field_validator('functions')
    def validate_aliases(cls, v):
        cleaned = []
        for func in v:
            if ' as ' in func:
                parts = func.split(' as ')
                if len(parts) != 2 or not parts[0].strip() or not parts[1].strip():
                    raise ValueError(f"Invalid alias format '{func}'. Use 'OriginalName as AliasName'")
            cleaned.append(func)
        return cleaned
        
    @model_validator(mode='after')
    def auto_fix_module_paths(self):
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
    
class GlobalString(BaseModel):
    type: Literal['string'] = 'string'
    name: str = Field(..., description="The variable name.")
    value: str = Field(..., description="The string value.")

class GlobalNumber(BaseModel):
    type: Literal['number'] = 'number'
    name: str = Field(..., description="The variable name.")
    value: Union[float, int] = Field(..., description="The numeric value.")

class GlobalVar(BaseModel):
    type: Literal['variable'] = 'variable'
    name: str = Field(..., description="The variable name.")
    value: str = Field(..., description="The name of the referenced variable.")

GlobalDef = Union[GlobalString, GlobalNumber, GlobalVar]

class LogicOperator(BaseModel):
    operator: Literal['multiMap', 'branch', 'map']
    closure_lines: List[str] = Field(..., description="The lines of code inside the closure block.")
    args: List[str] = Field(default=[], max_length=0, description="Must be empty for this operator.")

class ParametricOperator(BaseModel):
    operator: Literal['groupTuple', 'join', 'mix', 'concat']
    args: List[str] = Field(..., min_length=1, description="Arguments inside parentheses.")
    closure_lines: List[str] = Field(default=[], max_length=0, description="Must be empty for this operator.")

class FlexibleOperator(BaseModel):
    operator: Literal['filter', 'unique', 'distinct', 'collect', 'buffer']
    args: List[str] = Field(default=[], description="Optional arguments.")
    closure_lines: List[str] = Field(default=[], description="Optional closure block.")

    @model_validator(mode='after')
    def validate_has_content(self):
        if not self.args and not self.closure_lines:
             if self.operator in ['filter']:
                raise ValueError(f"Operator '{self.operator}' requires either arguments or a closure block.")
        return self

class StructuralOperator(BaseModel):
    operator: Literal['flatten', 'transpose']
    args: List[str] = Field(default=[], description="Usually empty for these operators.")
    closure_lines: List[str] = Field(default=[], max_length=0)

class HybridPairingOperator(BaseModel):
    operator: Literal['cross']
    args: List[str] = Field(..., min_length=1, max_length=1, description="Single channel argument.")
    closure_lines: List[str] = Field(default=[], description="Optional closure to define the matching key.")

ChainOperator = Union[LogicOperator, ParametricOperator, FlexibleOperator, StructuralOperator, HybridPairingOperator]

class VarArg(BaseModel):
    type: Literal["variable"] = "variable"
    name: str = Field(..., description="The variable name.")

class StringArg(BaseModel):
    type: Literal["string"] = "string"
    value: str = Field(..., description="The string value.")

class NumericArg(BaseModel):
    type: Literal["numeric"] = "numeric"
    value: Union[int, float, bool]

class ChannelChain(BaseModel):
    type: Literal["channel_chain"] = "channel_chain"
    start_variable: str = Field(..., description="The source of the channel.")
    steps: List[ChainOperator] = Field(..., min_length=1)
    set_variable: Optional[str] = Field(None, description="Variable to set at the end.")

    @field_validator('start_variable')
    def validate_source_syntax(cls, v):
        v = v.strip()
        if v.startswith("Channel."):
            valid_factories = {
                "Channel.fromPath", "Channel.fromFilePairs", "Channel.of", 
                "Channel.value", "Channel.fromSRA", "Channel.empty", "Channel.fromList",
                "Channel.topic"
            }
            factory = v.split('(')[0].strip()
            if factory not in valid_factories:
                raise ValueError(f"Unknown Channel factory '{factory}'. Supported {valid_factories}")
            return v
            
        if re.match(r'^[a-zA-Z_][\w]*(\.[a-zA-Z_][\w]*)*$', v):
            return v
            
        if re.match(r'^[a-zA-Z_][\w]*\(.*\)$', v):
            return v
            
        raise ValueError(f"Invalid start_variable format '{v}'. Must be a variable or factory.")
    
    @model_validator(mode='after')
    def validate_logic_flow(self):
        if self.set_variable and self.start_variable == self.set_variable:
            raise ValueError(
                f"Self-assignment detected for '{self.set_variable}'. "
                f"Please use a new variable name for the output."
            )
        return self

ProcessArgument = Union[VarArg, StringArg, NumericArg]

class ArgumentParser(BaseModel):
    @classmethod
    def parse(cls, v: Any) -> ProcessArgument:
        if isinstance(v, dict) and 'type' in v:
            return v
        
        if isinstance(v, str):
            v = v.strip()
            if (v.startswith("'") and v.endswith("'")) or (v.startswith('"') and v.endswith('"')):
                return {"type": "string", "value": v[1:-1]}
            if v.isdigit() or v.lower() in ['true', 'false', 'null']:
                val = True if v.lower() == 'true' else False if v.lower() == 'false' else None
                if val is None and v.lower() != 'null': val = int(v) 
                return {"type": "numeric", "value": val if val is not None else 0}
            return {"type": "variable", "name": v}
        
        return v

class ProcessCall(BaseModel):
    type: Literal["process_call"] = "process_call"
    process_name: str = Field(..., description="Name of process.")
    args: List[ProcessArgument] = Field(default=[], description="List of inputs.")
    assign_to: Optional[str] = Field(None, description="Clean variable name to capture the result.")    
    output_attribute: Optional[str] = Field(None, description="Extract exact name here.")

    @field_validator('args', mode='before')
    def allow_lazy_args(cls, v):
        if isinstance(v, list):
            return [ArgumentParser.parse(item) for item in v]
        return v
    
    @model_validator(mode='after')
    def validate_process_call_logic(self):
        name = self.process_name
        arguments = self.args
        
        if name.startswith("step_") and not arguments:
             raise ValueError(
                f"LOGIC ERROR Process '{name}' has NO arguments. "
                f"FIX Check the previous output variable and pass it here."
            )

        if self.output_attribute and not self.assign_to:
             raise ValueError(
                 f"INVALID AST You specified output_attribute '{self.output_attribute}' but no assign_to variable. "
                 f"FIX Add an assign_to variable name."
             )
             
        return self

    @model_validator(mode='after')
    def validate_naming_conventions(self):
        if self.assign_to:
            if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', self.assign_to):
                 raise ValueError(
                     f"INVALID VARIABLE NAME '{self.assign_to}'. "
                     f"Groovy variable names must start with a letter and contain only alphanumerics or underscores."
                 )
        return self

class Assignment(BaseModel):
    type: Literal["assignment"] = "assignment"
    variable: str
    value: str

    @field_validator('value')
    def forbid_hidden_logic(cls, v):
        if "step_" in v and "(" in v:
            raise ValueError(f"Use ProcessCall node type for step execution '{v}' not Assignment.")
        if ".map" in v or ".cross" in v:
            raise ValueError(f"Use ChannelChain node type for logic '{v}' not Assignment.")
        return v

class ConditionalBlock(BaseModel):
    type: Literal["conditional"] = "conditional"
    condition: str = Field(..., description="The condition string.")
    body: List[Union[ProcessCall, ChannelChain, Assignment, 'ConditionalBlock']] = Field(..., description="Logic to execute if true")

    @field_validator('condition')
    def validate_groovy_condition(cls, v):
        v = v.strip()
        if not v:
            raise ValueError("Condition string cannot be empty.")
            
        if v.count('(') != v.count(')'):
             raise ValueError(f"SYNTAX ERROR Unbalanced parentheses in condition '{v}'")
             
        import re
        if re.search(r'(?<!=)[^!<>]=\s', v) or re.search(r'\s=[^=]', v):
             raise ValueError(
                 f"POSSIBLE SYNTAX ERROR Condition '{v}' uses single equal sign. "
                 f"Did you mean double equal sign for comparison."
             )
             
        return v

class MacroCall(BaseModel):
    type: Literal["macro_call"] = "macro_call"
    macro_type: Literal[
        "COLLECT_ALL", 
        "CROSS_SYNC", 
        "MULTI_MAP_SPLIT", 
        "JOIN_BY_KEY",
        "GROUP_BY_KEY",
        "MIX_CHANNELS",
        "FILTER_DATA",
        "BRANCH_SPLIT"
    ]
    input_channels: List[str] = Field(..., min_length=1)
    output_variable: str
    mapping_rules: List[str] = Field(default=[])
    condition_rules: List[str] = Field(default=[])

def compile_macro_to_chain(macro: MacroCall) -> ChannelChain:
    start_var = macro.input_channels[0]
    steps = []
    
    if macro.macro_type == "COLLECT_ALL":
        steps.append(FlexibleOperator(operator="collect", args=[], closure_lines=[]))
        
    elif macro.macro_type == "CROSS_SYNC":
        for ch in macro.input_channels[1:]:
            steps.append(HybridPairingOperator(operator="cross", args=[ch], closure_lines=["extractKey(it)"]))
            
        if len(macro.input_channels) == 2:
            steps.append(LogicOperator(operator="map", args=[], closure_lines=["[ it[0][0], it[0][1], it[1][1] ]"]))
        elif len(macro.input_channels) == 3:
            steps.append(LogicOperator(operator="map", args=[], closure_lines=["[ it[0][0][0], it[0][0][1], it[0][1][1], it[1][1] ]"]))
        elif len(macro.input_channels) > 3:
            steps.append(LogicOperator(operator="map", args=[], closure_lines=["it.flatten()"]))
            
    elif macro.macro_type == "MULTI_MAP_SPLIT":
        lines = []
        for i, rule in enumerate(macro.mapping_rules):
            lines.append(f"{rule}: it[{i}]")
        if not lines:
            lines.append("out: it")
        steps.append(LogicOperator(operator="multiMap", args=[], closure_lines=lines))
        
    elif macro.macro_type == "JOIN_BY_KEY":
        for ch in macro.input_channels[1:]:
            steps.append(ParametricOperator(operator="join", args=[ch], closure_lines=[]))
            
    elif macro.macro_type == "GROUP_BY_KEY":
        steps.append(FlexibleOperator(operator="groupTuple", args=[], closure_lines=[]))
        
    elif macro.macro_type == "MIX_CHANNELS":
        args = macro.input_channels[1:]
        if not args:
            args = ["Channel.empty()"]
        steps.append(ParametricOperator(operator="mix", args=args, closure_lines=[]))
        
    elif macro.macro_type == "FILTER_DATA":
        lines = macro.condition_rules if macro.condition_rules else ["it != null"]
        steps.append(FlexibleOperator(operator="filter", args=[], closure_lines=lines))
        
    elif macro.macro_type == "BRANCH_SPLIT":
        lines = []
        for i, rule in enumerate(macro.mapping_rules):
            cond = macro.condition_rules[i] if i < len(macro.condition_rules) else "true"
            lines.append(f"{rule}: {cond}")
        if not lines:
            lines.append("keep_all: true")
        steps.append(LogicOperator(operator="branch", args=[], closure_lines=lines))
        
    return ChannelChain(
        type="channel_chain",
        start_variable=start_var,
        steps=steps,
        set_variable=macro.output_variable
    )

Statement = Union[ProcessCall, ChannelChain, Assignment, ConditionalBlock, MacroCall]
EntrypointStatement = Union[ProcessCall, Assignment, ConditionalBlock]
ModuleStatement = Union[ProcessCall, ChannelChain, Assignment, ConditionalBlock, MacroCall]

class EmitItem(BaseModel):
    export_name: str = Field(..., description="The public name exposed by the workflow.")
    internal_variable: Optional[str] = Field(None, description="The internal source.")

    @field_validator('export_name')
    def validate_export_name(cls, v):
        if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', v):
            raise ValueError(
                f"SYNTAX ERROR Invalid export name '{v}'. "
                f"Workflow output keys must be simple identifiers."
            )
        return v

    @field_validator('internal_variable')
    def validate_internal_source(cls, v):
        if v is None: return v
        v = v.strip()
        if not v: raise ValueError("Internal variable path cannot be empty.")
        if not re.match(r'^[a-zA-Z_][\w\.]*$', v):
             raise ValueError(
                 f"SYNTAX ERROR Invalid internal variable path '{v}'."
             )
        return v

    @model_validator(mode='before')
    def handle_implicit_shorthand(cls, values):
        export = values.get('export_name', '')
        internal = values.get('internal_variable')

        if '.' in export and not internal:
            parts = export.split('.')
            new_export = parts[-1]
            values['export_name'] = new_export
            values['internal_variable'] = export
        
        return values

    def render(self):
        if self.internal_variable and self.internal_variable != self.export_name:
            return f"{self.export_name} = {self.internal_variable}"
        return self.export_name

class NextflowProcess(BaseModel):
    name: str
    container: Optional[str] = None
    input_declarations: List[str] = Field(default=[])
    output_declarations: List[str] = Field(default=[])
    script_block: str

    @field_validator('script_block')
    def validate_no_dsl(cls, v):
        forbidden = ['workflow', '.cross(', '.join(', '.multiMap', '.map{', '.mix(']
        for kw in forbidden:
            if kw in v:
                raise ValueError(
                    f"INVALID PROCESS CONTENT Found DSL2 keyword '{kw}' inside a Process script."
                )
        return v
    
    @field_validator('name')
    def validate_name(cls, v):
        if v.startswith("step_"):
            raise ValueError(f"Process name '{v}' starts with 'step_'. Standard tools must be imported.")
        if v.isupper():
             raise ValueError(f"Process '{v}' is UPPERCASE. It should likely be a Global Constant.")
        return v

class NextflowWorkflow(BaseModel):
    name: str = Field(..., description="The name of the workflow.")
    take_channels: List[str] = Field(default=[])
    body: List[ModuleStatement]
    emit_channels: List[EmitItem] = Field(default=[])

    @model_validator(mode='after')
    def compile_macros(self):
        compiled_body = []
        for stmt in self.body:
            if hasattr(stmt, 'type') and stmt.type == 'macro_call':
                compiled_body.append(compile_macro_to_chain(stmt))
            else:
                compiled_body.append(stmt)
        self.body = compiled_body
        return self

    @model_validator(mode='after')
    def auto_fix_emits(self):
        for stmt in self.body:
            if isinstance(stmt, ProcessCall):
                if stmt.output_attribute and not stmt.assign_to:
                    internal = f"{stmt.process_name}.out.{stmt.output_attribute}"
                    export = "out" if stmt.output_attribute == '*' else stmt.output_attribute
                    
                    if not any(e.export_name == export for e in self.emit_channels):
                        self.emit_channels.append(EmitItem(export_name=export, internal_variable=internal))
                    
                    stmt.output_attribute = None
        return self

    @model_validator(mode='after')
    def validate_and_prune_scope(self):
        defined = set(self.take_channels)
        
        for stmt in self.body:
            if isinstance(stmt, Assignment):
                defined.add(stmt.variable)
            elif isinstance(stmt, ProcessCall):
                if stmt.assign_to: defined.add(stmt.assign_to)
                defined.add(stmt.process_name) 
            elif isinstance(stmt, ChannelChain) and stmt.set_variable:
                defined.add(stmt.set_variable)

        valid_emits = []
        for emit in self.emit_channels:
            target = emit.internal_variable or emit.export_name
            root = target.split('.')[0]
            
            if root in defined:
                valid_emits.append(emit)
        
        self.emit_channels = valid_emits
        return self

    @model_validator(mode='after')
    def forbid_recursion(self):
        def check_body(statements):
            for stmt in statements:
                if isinstance(stmt, ProcessCall):
                    if stmt.process_name == self.name:
                        raise ValueError(
                            f"VALIDATION ERROR The workflow '{self.name}' is trying to call itself. "
                        )
                elif isinstance(stmt, ConditionalBlock):
                    check_body(stmt.body)

        check_body(self.body)
        return self
    
    @model_validator(mode='after')
    def auto_fix_double_channel_access(self):
        extracted_channels = set()
        
        for stmt in self.body:
            if hasattr(stmt, 'type') and stmt.type == 'process_call':
                for arg in stmt.args:
                    arg_name = None
                    if isinstance(arg, dict) and arg.get("type") == "variable":
                        arg_name = arg.get("name")
                    elif hasattr(arg, "type") and arg.type == "variable":
                        arg_name = arg.name
                        
                    if arg_name and '.' in arg_name:
                        base_var = arg_name.split('.')[0]
                        if base_var in extracted_channels:
                            if isinstance(arg, dict):
                                arg["name"] = base_var
                            else:
                                arg.name = base_var
                                
            elif hasattr(stmt, 'type') and stmt.type == 'channel_chain':
                if stmt.start_variable and '.' in stmt.start_variable:
                    base_var = stmt.start_variable.split('.')[0]
                    if base_var in extracted_channels:
                        stmt.start_variable = base_var

            elif hasattr(stmt, 'type') and stmt.type == 'assignment':
                if stmt.value and isinstance(stmt.value, str) and '.' in stmt.value:
                    base_var = stmt.value.split('.')[0]
                    if base_var in extracted_channels:
                        stmt.value = base_var

            if hasattr(stmt, 'type') and stmt.type == 'process_call':
                if stmt.assign_to and stmt.output_attribute:
                    extracted_channels.add(stmt.assign_to)
            elif hasattr(stmt, 'type') and stmt.type == 'assignment':
                if stmt.variable:
                    extracted_channels.add(stmt.variable)
                    
        for emit in self.emit_channels:
            if emit.internal_variable and '.' in emit.internal_variable:
                base_var = emit.internal_variable.split('.')[0]
                if base_var in extracted_channels:
                    emit.internal_variable = base_var
                    
        return self

class EntrypointWorkflow(BaseModel):
    body: List[EntrypointStatement] = Field(..., description="List of execution statements.")

    @field_validator('body', mode='before')
    def fix_lazy_process_calls(cls, v):
        return repair_lazy_calls(v)

    @model_validator(mode='after')
    def forbid_complex_logic(self):
        for stmt in self.body:
            if isinstance(stmt, ChannelChain):
                raise ValueError("ARCHITECTURE ERROR Entrypoint contains complex logic.")
        return self

class NextflowPipelineAST(BaseModel):
    imports: List[ImportItem] = Field(default_factory=list)
    globals: List[GlobalDef] = Field(default_factory=list, description="Define ALL constants here.")
    processes: List[NextflowProcess] = Field(default=[])
    sub_workflows: List[NextflowWorkflow] = Field(default=[], description="Helper workflows.")
    main_workflow: NextflowWorkflow
    entrypoint: EntrypointWorkflow

    @model_validator(mode='before')
    def deduplicate_logic(cls, values: Dict[str, Any]) -> Dict[str, Any]:
        main_wf = values.get('main_workflow')
        sub_wfs = values.get('sub_workflows', [])
        
        if not sub_wfs or not isinstance(main_wf, dict): return values
        
        inputs = main_wf.get('take_channels', [])
        if not isinstance(inputs, list): inputs = []

        def clean_block(statements, parent_scope):
            if not isinstance(statements, list): return statements
            
            current_scope = set(parent_scope)
            cleaned = []
            
            for stmt in statements:
                if not isinstance(stmt, dict): continue
                
                if stmt.get('type') == 'conditional':
                    stmt['body'] = clean_block(stmt.get('body', []), current_scope)
                    if stmt['body']: 
                        cleaned.append(stmt)
                    continue

                is_chain = stmt.get('type') == 'channel_chain' or 'start_variable' in stmt
                is_call  = stmt.get('type') == 'process_call' or 'process_name' in stmt
                is_assign = stmt.get('type') == 'assignment'

                if is_chain and stmt.get('set_variable'):
                    current_scope.add(stmt.get('set_variable'))
                if is_call and stmt.get('assign_to'):
                    current_scope.add(stmt.get('assign_to'))
                if is_assign and stmt.get('variable'):
                    current_scope.add(stmt.get('variable'))

                if is_call:
                    args = stmt.get('args', [])
                    new_args = []
                    for i, arg in enumerate(args):
                        arg_val = str(arg.get('name') or arg.get('value') or "") if isinstance(arg, dict) else str(arg)
                        match_root = re.match(r'^([a-zA-Z_][a-zA-Z0-9_]*)', arg_val)
                        root_var = match_root.group(1) if match_root else arg_val

                        if root_var in current_scope or root_var in inputs:
                            new_args.append(arg)
                        else:
                            match = next((inp for inp in inputs if inp in arg_val), None)
                            
                            if not match and i < len(inputs): 
                                match = inputs[i]
                            
                            if match:
                                new_args.append({"type": "variable", "name": match})
                            else:
                                new_args.append(arg)
                            
                    stmt['args'] = new_args
                    if 'type' not in stmt: stmt['type'] = 'process_call'
                    
                cleaned.append(stmt)
            return cleaned

        main_wf['body'] = clean_block(main_wf.get('body', []), set(inputs))
        values['main_workflow'] = main_wf
        return values

    @model_validator(mode='after')
    def validate_prepare_inputs_location(self):
        for p in self.processes:
            if not p.input_declarations and not p.output_declarations:
                if 'prepare' in p.name.lower() or 'logic' in p.name.lower():
                    raise ValueError(f"'{p.name}' looks like logic but is defined as a Process. Move to sub workflows.")
        return self
    
    @model_validator(mode='after')
    def ensure_entrypoint_connectivity(self):
        if not self.entrypoint.body:
            if not self.main_workflow.take_channels:
                call = ProcessCall(
                    type="process_call",
                    process_name=self.main_workflow.name,
                    args=[],
                    assign_to=None
                )
                self.entrypoint.body.append(call)
        return self
    
    @model_validator(mode='after')
    def enforce_defined_processes(self):
        allowed = set()
        
        for imp in self.imports:
            for func in imp.functions:
                alias = func.split(' as ')[-1].strip()
                allowed.add(alias)
                
        for p in self.processes:
            allowed.add(p.name)
            
        for sw in self.sub_workflows:
            allowed.add(sw.name)
            
        allowed.add(self.main_workflow.name)
        allowed.update({"Channel", "file", "tuple", "set", "println"})

        def check_body(statements):
            for stmt in statements:
                if isinstance(stmt, ProcessCall):
                    if stmt.process_name not in allowed:
                        raise ValueError(
                            f"VALIDATION ERROR The process or function '{stmt.process_name}' is undefined. "
                        )
                elif isinstance(stmt, ConditionalBlock):
                    check_body(stmt.body)

        check_body(self.main_workflow.body)
        check_body(self.entrypoint.body)
        for sw in self.sub_workflows:
            check_body(sw.body)

        return self

    @model_validator(mode='after')
    def enforce_entrypoint_variables(self):
        valid_globals = {g.name for g in self.globals}
        
        valid_functions = set()
        for imp in self.imports:
            for func in imp.functions:
                valid_functions.add(func.split(' as ')[-1].strip())
                
        scope = set(valid_globals)
        implicit_nf_vars = {"params", "projectDir", "workDir", "baseDir", "launchDir"}
        
        for stmt in self.entrypoint.body:
            if isinstance(stmt, ProcessCall):
                for arg in stmt.args:
                    arg_name = None
                    if isinstance(arg, dict) and arg.get("type") == "variable":
                        arg_name = arg.get("name")
                    elif hasattr(arg, "type") and arg.type == "variable":
                        arg_name = arg.name
                        
                    if arg_name:
                        base_var = arg_name.split('(')[0].split('.')[0].strip()
                        
                        if base_var not in scope and base_var not in valid_functions and base_var not in implicit_nf_vars:
                            if base_var.startswith("get"):
                                raise ValueError(
                                    f"VALIDATION ERROR You used '{base_var}()' in the entrypoint but forgot to import it. "
                                )
                            else:
                                raise ValueError(
                                    f"VALIDATION ERROR Variable '{base_var}' is not defined. "
                                )
                
                if stmt.assign_to:
                    scope.add(stmt.assign_to)
                    
            elif isinstance(stmt, Assignment):
                scope.add(stmt.variable)
                
        return self

    @model_validator(mode='after')
    def enforce_workflow_usage_and_scope(self):
        valid_globals = {g.name for g in self.globals}
        
        valid_functions = set()
        for imp in self.imports:
            for func in imp.functions:
                valid_functions.add(func.split(' as ')[-1].strip())
        
        implicit_vars = {"params", "it", "projectDir", "baseDir", "workDir", "Channel", "file", "tuple", "set", "println", "getEmpty"}
        
        called_processes = set()
        
        def track_calls(statements):
            for stmt in statements:
                if isinstance(stmt, ProcessCall):
                    called_processes.add(stmt.process_name)
                elif isinstance(stmt, ConditionalBlock):
                    track_calls(stmt.body)

        track_calls(self.main_workflow.body)
        track_calls(self.entrypoint.body)
        for sw in self.sub_workflows:
            track_calls(sw.body)
            
        for sw in self.sub_workflows:
            if sw.name not in called_processes:
                raise ValueError(
                    f"VALIDATION ERROR The sub workflow '{sw.name}' is defined but never used. "
                )
                
        def check_block_scope(statements, current_scope, wf_name):
            for stmt in statements:
                if isinstance(stmt, ProcessCall):
                    for arg in stmt.args:
                        arg_name = None
                        if isinstance(arg, dict) and arg.get("type") == "variable":
                            arg_name = arg.get("name")
                        elif hasattr(arg, "type") and arg.type == "variable":
                            arg_name = arg.name
                            
                        if arg_name:
                            base = arg_name.split('(')[0].split('.')[0].strip()
                            if base not in current_scope and base not in valid_globals and base not in valid_functions and base not in implicit_vars:
                                if not (base.startswith("'") or base.startswith('"') or base.isdigit()):
                                    raise ValueError(
                                        f"VALIDATION ERROR in '{wf_name}'. Variable '{base}' is not defined. "
                                    )
                    if stmt.assign_to:
                        current_scope.add(stmt.assign_to)
                
                elif isinstance(stmt, ChannelChain):
                    base_start = stmt.start_variable.split('(')[0].split('.')[0].strip()
                    if base_start not in current_scope and base_start not in valid_globals and base_start not in valid_functions and base_start not in implicit_vars:
                        raise ValueError(
                            f"VALIDATION ERROR in '{wf_name}'. Channel source '{base_start}' is not defined. "
                        )
                    if stmt.set_variable:
                        current_scope.add(stmt.set_variable)
                
                elif isinstance(stmt, Assignment):
                    current_scope.add(stmt.variable)
                
                elif isinstance(stmt, ConditionalBlock):
                    check_block_scope(stmt.body, current_scope, wf_name)

        for sw in self.sub_workflows:
            wf_scope = set(sw.take_channels)
            check_block_scope(sw.body, wf_scope, sw.name)
            
        main_scope = set(self.main_workflow.take_channels)
        check_block_scope(self.main_workflow.body, main_scope, self.main_workflow.name)

        return self

ConditionalBlock.model_rebuild()
NextflowProcess.model_rebuild()
NextflowWorkflow.model_rebuild()
EntrypointWorkflow.model_rebuild()