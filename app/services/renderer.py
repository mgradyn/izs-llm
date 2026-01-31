import re
import hashlib
from jinja2 import Template
from typing import Any, Dict, Union
from app.services.graph_state import GraphState
from app.utils.rendering import NF_TEMPLATE_AST

def render_nextflow_code(ast) -> str:
    """
    Renders the Nextflow AST into a DSL2 string.
    """
    # 1. Convert Pydantic model to Dict if necessary
    if hasattr(ast, 'model_dump'):
        data = ast.model_dump()
    elif hasattr(ast, 'dict'):
        data = ast.dict()
    else:
        data = ast

    # 2. Render Template
    t = Template(NF_TEMPLATE_AST)
    
    rendered = t.render(**data)
    
    while "\n\n\n" in rendered:
        rendered = rendered.replace("\n\n\n", "\n\n")
        
    return rendered.strip()

def render_mermaid(ast: Union[Any, Dict[str, Any]]) -> str:
    def get_val(obj, key, default=None):
        if isinstance(obj, dict): 
            return obj.get(key, default)
        return getattr(obj, key, default)

    def make_id(name):
        if not name: return "Unknown"
        clean = re.sub(r'[\$\{\}\(\)\.\s\-\[\]\'\",]', '_', str(name))
        if len(clean) > 30 or clean.startswith('_') or clean[0].isdigit():
            h = hashlib.md5(str(name).encode()).hexdigest()[:6]
            return f"node_{h}"
        return clean

    def clean_label(text):
        return str(text).replace('\n', ' ').replace('"', "'")

    def resolve_variable_link(text_fragment, target_node_id, variable_registry, lines, seen_edges, style="-->"):
        """
        Smartly links variables found in 'text_fragment' (STRING) to 'target_node_id'.
        """
        if not isinstance(text_fragment, str): return # Guard against non-string input

        # --- Strategy A: Exact or Dot-Notation Match ---
        root_candidate = text_fragment.split('.')[0] if "." in text_fragment else text_fragment
        
        is_func_call = "(" in text_fragment and ")" in text_fragment

        if root_candidate in variable_registry and not is_func_call and " " not in text_fragment:
            src_id = variable_registry[root_candidate]
            prop_name = text_fragment.split('.')[1] if "." in text_fragment else ""
            label = f".{prop_name}" if prop_name else None
            
            edge_key = f"{src_id}|{target_node_id}|{label}"
            if edge_key not in seen_edges:
                arrow = f'-- "{label}" -->' if label else f'{style}'
                lines.append(f'    {src_id} {arrow} {target_node_id}')
                seen_edges.add(edge_key)
            return

        # --- Strategy B: Deep Scan for Embedded Variables ---
        potential_vars = re.findall(r'\b([a-zA-Z_][\w\.]*)\b', text_fragment)
        
        ignore_keywords = {
            'mix', 'join', 'groupTuple', 'collect', 'map', 'flatten', 'cross', 'multiMap',
            'true', 'false', 'null', 'it', 'get', 'return', 'branch', 'file', 'extractKey',
            'baseName', 'simpleName', 'id', 'size', 'exists', 'toInteger', 'toString', 'view'
        }

        for token in potential_vars:
            cleaned_token = token
            parts = token.split('.')
            if len(parts) > 1 and parts[-1] in ignore_keywords:
                cleaned_token = ".".join(parts[:-1])
            
            root = cleaned_token.split('.')[0]
            
            if root in variable_registry and root not in ignore_keywords:
                src_id = variable_registry[root]
                label = None
                if "." in cleaned_token:
                    suffix = cleaned_token.split('.', 1)[1]
                    if suffix not in ignore_keywords:
                        label = f".{suffix}"

                edge_key = f"{src_id}|{target_node_id}|{label}"
                if edge_key not in seen_edges:
                    arrow = f'-- "{label}" -->' if label else f'{style}'
                    lines.append(f'    {src_id} {arrow} {target_node_id}')
                    seen_edges.add(edge_key)

    # --- 2. CONFIGURATION ---
    lines = ["flowchart TD"]
    lines.append("    classDef process fill:#e1f5fe,stroke:#01579b,stroke-width:2px;")
    lines.append("    classDef subworkflow fill:#e8eaf6,stroke:#3f51b5,stroke-width:2px,stroke-dasharray: 5 5;")
    lines.append("    classDef operator fill:#fff9c4,stroke:#fbc02d,stroke-width:2px,stroke-dasharray: 5 5;")
    lines.append("    classDef data fill:#e0e0e0,stroke:#333,stroke-width:2px;")
    lines.append("    classDef global fill:#f3e5f5,stroke:#7b1fa2,stroke-width:1px;")

    variable_registry = {} 
    seen_edges = set()

    mw = get_val(ast, 'main_workflow')
    if not mw: return "flowchart TD\n    Empty[Empty Pipeline]"

    sub_workflows = get_val(ast, 'sub_workflows', [])
    sub_workflow_names = {get_val(s, 'name') for s in sub_workflows}

    # --- 3. REGISTER GLOBALS ---
    globals_list = get_val(ast, 'globals', [])
    for g in globals_list:
        g_name = get_val(g, 'name')
        if g_name:
            variable_registry[g_name] = f"Glob_{make_id(g_name)}"

    # --- 4. RENDER INPUTS ---
    take_channels = get_val(mw, 'take_channels', [])
    for channel in take_channels:
        node_id = f"Var_{make_id(channel)}"
        lines.append(f'    {node_id}([{channel}]):::data')
        variable_registry[channel] = node_id

    # --- 5. RECURSIVE BUILDER ---
    def process_statements(statements, subgraph_prefix=None):
        for stmt in statements:
            stype = get_val(stmt, 'type')

            # ==============================
            # CASE A: PROCESS or SUB-WORKFLOW CALL
            # ==============================
            if stype == 'process_call':
                proc_name = get_val(stmt, 'process_name')
                assign_to = get_val(stmt, 'assign_to')
                proc_node_id = make_id(proc_name)
                
                if proc_name in sub_workflow_names:
                    lines.append(f'    {proc_node_id}[[{proc_name}]]:::subworkflow')
                else:
                    lines.append(f'    {proc_node_id}[{proc_name}]:::process')
                
                # --- [FIX] UNPACK TYPED ARGUMENTS ---
                args = get_val(stmt, 'args', [])
                for arg in args:
                    # Arg is now a Dict: {'type': 'variable', 'name': 'reads'}
                    atype = get_val(arg, 'type')
                    
                    if atype == 'variable':
                        # Link to existing variable node
                        var_name = get_val(arg, 'name')
                        resolve_variable_link(var_name, proc_node_id, variable_registry, lines, seen_edges)
                    
                    elif atype in ['string', 'numeric']:
                        # Create constant node
                        val = str(get_val(arg, 'value'))
                        const_id = make_id(f"const_{val}_{proc_node_id}")
                        if const_id not in seen_edges:
                            lines.append(f'    {const_id}({val}):::global')
                            lines.append(f'    {const_id} -.-> {proc_node_id}')
                            seen_edges.add(const_id)
                    
                    # Fallback for legacy strings (just in case)
                    elif isinstance(arg, str):
                        resolve_variable_link(arg, proc_node_id, variable_registry, lines, seen_edges)

                if assign_to:
                    var_node_id = f"Var_{make_id(assign_to)}"
                    lines.append(f'    {var_node_id}(({assign_to})):::data')
                    lines.append(f'    {proc_node_id} --> {var_node_id}')
                    variable_registry[assign_to] = var_node_id
                
                variable_registry[proc_name] = proc_node_id

            # ==============================
            # CASE B: CHANNEL CHAIN
            # ==============================
            elif stype == 'channel_chain':
                start_var = get_val(stmt, 'start_variable')
                set_var = get_val(stmt, 'set_variable')
                steps = get_val(stmt, 'steps', [])
                
                ops = [get_val(s, 'operator') for s in steps]
                op_name = "\\n".join(ops)
                op_node_id = make_id(f"op_{start_var}_{len(seen_edges)}")
                lines.append(f'    {op_node_id}{{{{{op_name}}}}}:::operator')

                if start_var:
                    resolve_variable_link(start_var, op_node_id, variable_registry, lines, seen_edges)

                for step in steps:
                    # Args in Operators are still list[str] (e.g., ['host'])
                    args = get_val(step, 'args', [])
                    for arg in args:
                         # Direct string resolution for operators
                         if isinstance(arg, str):
                            resolve_variable_link(arg, op_node_id, variable_registry, lines, seen_edges, style="-.->")
                    
                    closure_lines = get_val(step, 'closure_lines', [])
                    if closure_lines:
                        raw_text = " ".join(closure_lines)
                        resolve_variable_link(raw_text, op_node_id, variable_registry, lines, seen_edges, style="-.->")

                if set_var:
                    var_node_id = f"Var_{make_id(set_var)}"
                    lines.append(f'    {var_node_id}(({set_var})):::data')
                    lines.append(f'    {op_node_id} --> {var_node_id}')
                    variable_registry[set_var] = var_node_id

            # ==============================
            # CASE C: CONDITIONAL
            # ==============================
            elif stype == 'conditional':
                cond_str = clean_label(get_val(stmt, 'condition'))
                sub_id = f"sub_{hashlib.md5(cond_str.encode()).hexdigest()[:4]}"
                
                lines.append(f'    subgraph {sub_id} ["if {cond_str}"]')
                lines.append(f'    direction TB')
                process_statements(get_val(stmt, 'body', []), subgraph_prefix=sub_id)
                lines.append("    end")
                lines.append(f'    style {sub_id} fill:#ffebee,stroke:#c62828,stroke-dasharray: 5 5')

    process_statements(get_val(mw, 'body', []))
    
    # --- 6. RENDER FINAL EMITS ---
    emits = get_val(mw, 'emit_channels', [])
    if emits:
        lines.append("    %% --- Outputs ---")
        for em in emits:
            export_name = get_val(em, 'export_name')
            internal_var = get_val(em, 'internal_variable') or export_name
            final_id = f"Out_{make_id(export_name)}"
            lines.append(f'    {final_id}([{export_name}]):::data')
            resolve_variable_link(internal_var, final_id, variable_registry, lines, seen_edges)

    return "\n".join(lines)

def renderer_node(state: GraphState):
    print("--- [NODE] RENDERER ---")

    if state.get("error"): return {}

    # 1. Normalize Input
    raw_ast = state['ast_json']
    ast_dict = raw_ast.model_dump() if hasattr(raw_ast, 'model_dump') else raw_ast

    try:
        nf_code = render_nextflow_code(ast_dict)
    except Exception as e:
        print(f"💥 NEXTFLOW RENDERER CRASH: {e}")
        return {"error": f"Nextflow Code Generation Failed: {str(e)}"}

    try:
        mermaid_code = render_mermaid(ast_dict)
    except Exception as e:
        mermaid_code = f"graph TD;\nError['{str(e)}']"

    return {
        "nextflow_code": nf_code,
        "mermaid_code": mermaid_code
    }