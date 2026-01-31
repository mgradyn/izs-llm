from pydantic import BaseModel, Field, field_validator, model_validator
import re
from typing import Literal, List, Optional, Union

class ImportItem(BaseModel):
    module_path: str = Field(..., description="Path to the module. MUST start with '../steps/' for tools or '../functions/' for helpers.")
    
    functions: List[str] = Field(..., description="List of process names to import (e.g., ['step_2AS_mapping__ivar']).")

    @field_validator('functions')
    def validate_aliases(cls, v):
        cleaned = []
        for func in v:
            if ' as ' in func:
                # This is valid DSL2, but ensure strict formatting if needed
                parts = func.split(' as ')
                if len(parts) != 2 or not parts[0].strip() or not parts[1].strip():
                    raise ValueError(f"Invalid alias format: '{func}'. Use 'OriginalName as AliasName'")
            cleaned.append(func)
        return cleaned

    def get_names_in_scope(self):
        """Helper to return the name that will actually exist in the workflow scope"""
        names = []
        for f in self.functions:
            if ' as ' in f:
                names.append(f.split(' as ')[1].strip())
            else:
                names.append(f)
        return names

class GlobalDef(BaseModel):
    name: str = Field(..., description="The variable name.")
    value: str = Field(..., description="The value.")

# --- 2. LOGIC BUILDING BLOCKS (The "Atoms") ---

# --- Category 1: Logic Operators (MUST have a closure block { ... }) ---
class LogicOperator(BaseModel):
    operator: Literal['multiMap', 'branch', 'map']

    closure_lines: List[str] = Field(
        ..., 
        description="The lines of code inside the closure block { ... }. "
                    "Example: ['trimmed: it', 'ref: it.id']"
    )
    
    args: List[str] = Field(default=[], max_length=0, description="Must be empty for this operator.")

# --- Category 2: Parametric Operators (MUST have (...) args, NO closure) ---
class ParametricOperator(BaseModel):
    operator: Literal['groupTuple', 'join', 'cross', 'mix', 'concat']
    
    args: List[str] = Field(
        ..., 
        min_length=1,
        description="Arguments inside parentheses (...). "
                    "For groupTuple use named args like ['by: 0', 'size: 3']. "
                    "For join/mix use channel names."
    )
    
    closure_lines: List[str] = Field(default=[], max_length=0, description="Must be empty for this operator.")

# --- Category 3: Flexible Operators (Can have Args OR Closure) ---
class FlexibleOperator(BaseModel):
    operator: Literal['filter', 'unique', 'distinct', 'collect', 'buffer']
    
    args: List[str] = Field(
        default=[], 
        description="Optional arguments (e.g. 'flat: false' for collect)."
    )
    
    closure_lines: List[str] = Field(
        default=[], 
        description="Optional closure block. Use this for mapping logic or filter conditions."
    )

    @model_validator(mode='after')
    def validate_has_content(self):
        if not self.args and not self.closure_lines:
             if self.operator in ['filter']:
                raise ValueError(f"Operator '{self.operator}' requires either arguments or a closure block.")
        return self

# --- Category 4: Structural Operators (Pure topology, usually empty) ---
class StructuralOperator(BaseModel):
    operator: Literal['flatten', 'transpose']
    
    args: List[str] = Field(default=[], description="Usually empty for these operators.")
    closure_lines: List[str] = Field(default=[], max_length=0)

ChainOperator = Union[LogicOperator, ParametricOperator, FlexibleOperator, StructuralOperator]

class ChannelChain(BaseModel):
    type: Literal["channel_chain"] = "channel_chain"
    
    start_variable: str = Field(
        ..., 
        description="The source of the channel. Can be a variable ('trimmed'), a function call ('getReads()'), or a Channel factory ('Channel.fromPath(...)')."
    )
    
    steps: List[ChainOperator] = Field(..., min_length=1)
    
    set_variable: Optional[str] = Field(
        None, 
        description="Variable to set at the end. LEAVE EMPTY if this chain flows into a process input."
    )

    @field_validator('start_variable')
    def validate_source_syntax(cls, v):
        v = v.strip()
        
        # 1. Channel Factories (Strict Allow List)
        if v.startswith("Channel."):
            valid_factories = {
                "Channel.fromPath", "Channel.fromFilePairs", "Channel.of", 
                "Channel.value", "Channel.fromSRA", "Channel.empty", "Channel.fromList",
                "Channel.topic"
            }
            factory = v.split('(')[0].strip()
            if factory not in valid_factories:
                raise ValueError(f"Unknown Channel factory: '{factory}'. Supported: {valid_factories}")
            return v
            
        # 2. Variable Names (simple identifiers) or Param access
        # Matches: "trimmed", "params.reads", "step1_out"
        if re.match(r'^[a-zA-Z_][\w]*(\.[a-zA-Z_][\w]*)*$', v):
            return v
            
        # 3. Function Calls
        # Matches: "getReads()", "collectFile(name: 'x')"
        if re.match(r'^[a-zA-Z_][\w]*\(.*\)$', v):
            return v
            
        raise ValueError(f"Invalid start_variable format: '{v}'. Must be a variable, param, or Channel.* factory.")
    
    @model_validator(mode='after')
    def validate_logic_flow(self):
        # Prevent self-assignment which confuses DAGs
        if self.set_variable and self.start_variable == self.set_variable:
            raise ValueError(
                f"Self-assignment detected for '{self.set_variable}'. "
                f"Nextflow allows this, but it creates ambiguous DAGs. "
                f"Please use a new variable name for the output."
            )
        return self

class VarArg(BaseModel):
    type: Literal["variable"] = "variable"
    name: str = Field(..., description="The variable name.")

class StringArg(BaseModel):
    type: Literal["string"] = "string"
    value: str = Field(..., description="The string value. Do NOT add quotes; renderer will add them.")

class NumericArg(BaseModel):
    type: Literal["numeric"] = "numeric"
    value: Union[int, float, bool]

ProcessArgument = Union[VarArg, StringArg, NumericArg]

class ProcessCall(BaseModel):
    type: Literal["process_call"] = "process_call"
    
    process_name: str = Field(..., description="Name of process. MUST match an Import or Inline Process.")

    args: List[ProcessArgument] = Field(
        default=[], 
        description="List of inputs. Select 'variable' for channels, 'string' for text options."
    )
    
    # DSL2 Output Handling:
    # 1. 'assign_to' captures the WHOLE process object or the default channel.
    assign_to: Optional[str] = Field(None, description="Variable name to capture the result (e.g., 'fastqc_results').")
    
    # 2. 'output_attribute' handles the '.out.channelName' pattern.
    output_attribute: Optional[str] = Field(None, description="Specific named output to extract (e.g., 'bam' implies accessing '.out.bam').")

    @field_validator('args')
    def validate_literals(cls, v):
        reserved = {'params', 'it', 'workflow', 'launchDir', 'projectDir', 'true', 'false', 'null'}
        for arg in v:
            # If alphanumeric, not reserved, and not quoted -> Suspect
            if re.match(r'^[a-zA-Z0-9]+$', arg) and arg not in reserved:
                raise ValueError(
                    f"AMBIGUOUS ARGUMENT: '{arg}'.\n"
                    f"Did you mean the STRING '{arg}' or the VARIABLE {arg}?\n"
                    f" - For strings, use QUOTES: \"'{arg}'\"\n"
                    f" - For variables, ensure it is defined."
                )
        return v
    
    @field_validator('args')
    def validate_args_content(cls, v):
        if any(a is None for a in v):
            raise ValueError("Arguments cannot be None")
        return v

    @model_validator(mode='after')
    def validate_process_call_logic(self):
        name = self.process_name
        arguments = self.args
        
        # --- RULE 1: Standard Step Arguments ---
        # "step_" implies a data processing tool, which always needs input data.
        if name.startswith("step_") and not arguments:
             raise ValueError(
                f"LOGIC ERROR: Process '{name}' has NO arguments.\n"
                f"Nextflow processes function like pipes; they require input channels.\n"
                f"FIX: Check the previous step's output variable and pass it here."
            )

        # --- RULE 2: Output Access Syntax ---
        # You cannot ask for an 'output_attribute' if you haven't assigned the result to something.
        # INCORRECT AST: assign_to=None, output_attribute='bam'
        # (This would mean generating code like "step().out.bam", which is valid but rarely what users mean in this AST structure)
        
        # Actually, in Nextflow DSL2:
        # valid: bam_ch = ALIGN.out.bam
        # valid: ALIGN(reads)
        
        # If the user wants to access a specific output, they MUST assign it to a variable.
        if self.output_attribute and not self.assign_to:
             raise ValueError(
                 f"INVALID AST: You specified 'output_attribute' ('{self.output_attribute}') but no 'assign_to' variable.\n"
                 f"Nextflow DSL2 requires a variable to hold the output.\n"
                 f"Example intent: 'bam_ch = {name}(...).out.{self.output_attribute}'\n"
                 f"FIX: Add an 'assign_to' variable name."
             )
             
        return self

    @model_validator(mode='after')
    def validate_naming_conventions(self):
        """
        Enforce clean variable naming for 'assign_to' to avoid Groovy syntax errors.
        """
        if self.assign_to:
            # Must start with letter, only alphanumeric + underscores
            if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', self.assign_to):
                 raise ValueError(
                     f"INVALID VARIABLE NAME: '{self.assign_to}'.\n"
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
            raise ValueError(f"Use 'ProcessCall' node type for step execution '{v}', not Assignment.")
        if ".map" in v or ".cross" in v:
            raise ValueError(f"Use 'ChannelChain' node type for logic '{v}', not Assignment.")
        return v

class ConditionalBlock(BaseModel):
    type: Literal["conditional"] = "conditional"
    condition: str = Field(..., description="The condition string, e.g. '!params.skip_mapping'")
    # Recursive definition: A block contains statements, which can be calls, chains, or nested conditionals
    body: List[Union[ProcessCall, ChannelChain, Assignment, 'ConditionalBlock']] = Field(..., description="Logic to execute if true")

    @field_validator('condition')
    def validate_groovy_condition(cls, v):
        v = v.strip()
        if not v:
            raise ValueError("Condition string cannot be empty.")
            
        # Basic Heuristic Check for Groovy Syntax
        # 1. Parentheses balance (simple check)
        if v.count('(') != v.count(')'):
             raise ValueError(f"SYNTAX ERROR: Unbalanced parentheses in condition: '{v}'")
             
        # 2. Block commonly misused characters that break strict Nextflow
        # e.g., using single '=' for comparison (common beginner mistake)
        # We try to catch "if (x = 5)" which is assignment, not comparison "=="
        # This regex looks for = surrounded by spaces/vars, but not ==, !=, >=, <=
        import re
        # This is a heuristic; might flag valid complex cases, but safe for 99% of pipelines
        if re.search(r'(?<!=)[^!<>]=\s', v) or re.search(r'\s=[^=]', v):
             # We warn, or strictly fail. For this AST, let's warn via error to prompt a fix.
             # Exception: param assignment inside if? No, usually bad practice in workflow logic.
             raise ValueError(
                 f"POSSIBLE SYNTAX ERROR: Condition '{v}' uses single '='.\n"
                 f"Did you mean '==' for comparison?\n"
                 f"Groovy requires '==' to compare values."
             )
             
        return v

# Union type for any valid statement in a workflow body
Statement = Union[ProcessCall, ChannelChain, Assignment, ConditionalBlock]
EntrypointStatement = Union[ProcessCall, Assignment, ConditionalBlock]
ModuleStatement = Union[ProcessCall, ChannelChain, Assignment, ConditionalBlock]

class EmitItem(BaseModel):
    export_name: str = Field(
        ..., 
        description="The public name exposed by the workflow (e.g., 'bam'). Must be a simple identifier (no dots)."
    )
    
    internal_variable: Optional[str] = Field(
        None, 
        description="The internal source. Can be a variable ('bam_ch') or a process output path ('ALIGN.out.bam')."
    )

    @field_validator('export_name')
    def validate_export_name(cls, v):
        """
        STRICT RULE: The export key must be a simple identifier.
        Invalid: 'step.out'
        Valid: 'out', 'bam', 'results'
        """
        # If the user tries to put a dot here, we will try to fix it in the model_validator below.
        # But if it persists, this regex is the final guard.
        if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', v):
            raise ValueError(
                f"SYNTAX ERROR: Invalid export name '{v}'.\n"
                f"Workflow output keys must be simple identifiers (e.g., 'consensus').\n"
                f"They CANNOT contain dots."
            )
        return v

    @field_validator('internal_variable')
    def validate_internal_source(cls, v):
        if v is None: return v
        
        v = v.strip()
        if not v: raise ValueError("Internal variable path cannot be empty.")

        # Internal paths allow dots: 'PROCESS_NAME.out.CHANNEL'
        if not re.match(r'^[a-zA-Z_][\w\.]*$', v):
             raise ValueError(
                 f"SYNTAX ERROR: Invalid internal variable path '{v}'.\n"
                 f"Must be a valid variable or property path (e.g., 'fastqc_ch' or 'FASTQC.out.zip')."
             )
        return v

    @model_validator(mode='before')
    def handle_implicit_shorthand(cls, values):
        """
        SMART FIX: Handles the common shorthand mistake.
        Input:  { export_name: "step_A.out" }  (User tried to emit the whole object using dot notation)
        Fix:    { export_name: "out", internal_variable: "step_A.out" }
        """
        export = values.get('export_name', '')
        internal = values.get('internal_variable')

        # If export has dots and internal is missing, we assume shorthand intent.
        if '.' in export and not internal:
            parts = export.split('.')
            # The last part becomes the public name (e.g. 'out')
            new_export = parts[-1]
            # The full string becomes the source
            values['export_name'] = new_export
            values['internal_variable'] = export
        
        return values

    def render(self):
        """
        Renders the Nextflow DSL2 emit statement.
        """
        # Case A: Explicit Renaming ( emit: bam = ALIGN.out.bam )
        if self.internal_variable and self.internal_variable != self.export_name:
            return f"{self.export_name} = {self.internal_variable}"
        
        # Case B: Direct Passthrough ( emit: results )
        return self.export_name

        
# --- 3. WORKFLOW DEFINITIONS ---

class NextflowProcess(BaseModel):
    """Raw Bash/Script Processes (step_* are NOT allowed here)"""
    name: str
    container: Optional[str] = None
    input_declarations: List[str] = Field(default=[])
    output_declarations: List[str] = Field(default=[])
    script_block: str

    @field_validator('script_block')
    def validate_no_dsl(cls, v):
        # Forbidden keywords that imply DSL2 logic inside a bash script
        forbidden = ['workflow', '.cross(', '.join(', '.multiMap', '.map{', '.mix(']
        for kw in forbidden:
            if kw in v:
                raise ValueError(
                    f"INVALID PROCESS CONTENT: Found DSL2 keyword '{kw}' inside a Process script.\n"
                    f"Processes are for BASH/SHELL commands only.\n"
                    f"If you need logic, define this as a 'sub_workflow', not a 'process'."
                )
        return v
    
    @field_validator('name')
    def validate_name(cls, v):
        if v.startswith("step_"):
            raise ValueError(f"Process name '{v}' starts with 'step_'. Standard tools must be imported, not defined inline.")
        if v.isupper():
             raise ValueError(f"Process '{v}' is UPPERCASE. It should likely be a Global Constant, not a Process.")
        return v

class NextflowWorkflow(BaseModel):
    """Used for Main Workflow AND Sub-Workflows"""
    name: str
    take_channels: List[str] = Field(default=[])
    body: List[ModuleStatement]
    emit_channels: List[EmitItem] = Field(default=[])

    @model_validator(mode='after')
    def auto_fix_emits(self):
        """Converts inline 'output_attribute' usage into proper 'emit' statements."""
        for stmt in self.body:
            if isinstance(stmt, ProcessCall):
                if stmt.output_attribute and not stmt.assign_to:
                    # Logic: Create implicit emit
                    internal = f"{stmt.process_name}.out.{stmt.output_attribute}"
                    export = "out" if stmt.output_attribute == '*' else stmt.output_attribute
                    
                    # Add if not exists
                    if not any(e.export_name == export for e in self.emit_channels):
                        self.emit_channels.append(EmitItem(export_name=export, internal_variable=internal))
                    
                    # Clear attribute to prevent double rendering
                    stmt.output_attribute = None
        return self

    @model_validator(mode='after')
    def validate_scope(self):
        """Ensures all emitted variables exist."""
        defined = set(self.take_channels)
        
        # Harvest definitions from body
        for stmt in self.body:
            if isinstance(stmt, Assignment):
                defined.add(stmt.variable)
            elif isinstance(stmt, ProcessCall):
                if stmt.assign_to: defined.add(stmt.assign_to)
                defined.add(stmt.process_name) # Process object itself is valid
            elif isinstance(stmt, ChannelChain) and stmt.set_variable:
                defined.add(stmt.set_variable)

        # Check Emits
        for emit in self.emit_channels:
            target = emit.internal_variable or emit.export_name
            root = target.split('.')[0]
            if root not in defined:
                 raise ValueError(f"SCOPE ERROR: Emitting '{target}' in workflow '{self.name}', but '{root}' is undefined.")
        return self

class EntrypointWorkflow(BaseModel):
    # Restrict type defined in entry point
    body: List[EntrypointStatement] = Field(
        ..., 
        description="List of execution statements. NOTE: Complex logic (Chains, multiMap) is FORBIDDEN here. Logic must be inside the NamedWorkflow."
    )

    @model_validator(mode='after')
    def forbid_complex_logic(self):
        for stmt in self.body:
            if isinstance(stmt, ChannelChain):
                ops = [s.operator for s in stmt.steps]
                raise ValueError(
                    f"ARCHITECTURE ERROR: Entrypoint contains complex logic {ops}. "
                    f"Move this logic into a 'sub_workflow' and call it here."
                )
        return self

# --- 4. MASTER AST ---

class NextflowPipelineAST(BaseModel):
    imports: List[ImportItem] = Field(default_factory=list)
    globals: List[GlobalDef] = Field(
        default_factory=list, 
        description="CRITICAL: Define ALL constants here. If you use e.g. 'referencePath' in logic, it MUST be defined here."
    )

    # 1. Bash Scripts
    processes: List[NextflowProcess] = Field(default=[])

    # 2. Helper Workflows (e.g. prepare_inputs)
    sub_workflows: List[NextflowWorkflow] = Field(
        default=[], 
        description="Helper workflows containing DSL logic (cross, map, etc). NOT processes."
    )

    # 3. Main Logic
    main_workflow: NextflowWorkflow

    # 4. Entrypoint
    entrypoint: EntrypointWorkflow

    @model_validator(mode='after')
    def validate_prepare_inputs_location(self):
        for p in self.processes:
            if not p.input_declarations and not p.output_declarations:
                if 'prepare' in p.name.lower() or 'logic' in p.name.lower():
                    raise ValueError(
                        f"CLASSIFICATION ERROR: '{p.name}' is defined as a Process but looks like logic.\n"
                        f"Does it contain DSL code? Move it to 'sub_workflows'."
                    )
        return self
    
    @model_validator(mode='after')
    def validate_full_scope(self):
        """Linear Scan of the Main Workflow to check variable usage."""
        scope = {g.name for g in self.globals}
        scope.update({"params", "workflow", "Channel"})
        
        # Add Imports
        for imp in self.imports:
            for f in imp.functions:
                name = f.split(' as ')[-1].strip()
                scope.add(name)

        for sub in self.sub_workflows:
            scope.add(sub.name)

        # Add Main Inputs
        scope.update(self.main_workflow.take_channels)

        # Scan Body
        for i, stmt in enumerate(self.main_workflow.body):
            if isinstance(stmt, ProcessCall):
                for arg in stmt.args:
                    root = arg.split('.')[0].split('(')[0]
                    if root and root[0].isalpha() and root not in scope:
                        raise ValueError(f"SCOPE ERROR: Step {i} uses undefined variable '{root}'.")
                
                if stmt.assign_to: scope.add(stmt.assign_to)
                scope.add(stmt.process_name)

            elif isinstance(stmt, ChannelChain):
                start = stmt.start_variable.split('.')[0].split('(')[0]
                if not start.startswith("Channel") and start not in scope:
                     raise ValueError(f"SCOPE ERROR: Chain starts with undefined variable '{start}'.")
                if stmt.set_variable: scope.add(stmt.set_variable)

            elif isinstance(stmt, Assignment):
                scope.add(stmt.variable)

        return self

# --- REBUILD MODELS FOR RECURSION ---
ConditionalBlock.model_rebuild()
NextflowProcess.model_rebuild()
NextflowWorkflow.model_rebuild()
EntrypointWorkflow.model_rebuild()