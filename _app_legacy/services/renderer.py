from jinja2 import Template
import re
from langchain_core.messages import AIMessage
from app.services.graph_state import GraphState
from app.utils.rendering import NF_TEMPLATE_AST

def render_nextflow_code(ast) -> str:
    if hasattr(ast, 'model_dump'):
        data = ast.model_dump()
    elif hasattr(ast, 'dict'):
        data = ast.dict()
    else:
        data = ast

    # Ensure keys are present to prevent Jinja2 crashes on incomplete generated data
    data.setdefault('imports', [])
    data.setdefault('globals', [])
    data.setdefault('inline_processes', [])
    data.setdefault('sub_workflows', [])
    if 'entrypoint' not in data:
        data['entrypoint'] = {'body_code': '// Missing entrypoint in generated AST'}
    elif not isinstance(data['entrypoint'], dict):
        data['entrypoint'] = {'body_code': str(data['entrypoint'])}

    # Render Template
    t = Template(NF_TEMPLATE_AST)
    rendered = t.render(**data)
    
    # Clean up excess whitespace
    while "\n\n\n" in rendered:
        rendered = rendered.replace("\n\n\n", "\n\n")
        
    return rendered.strip()

def renderer_node(state: GraphState):
    print("--- [NODE] RENDERER ---")

    if state.get("error"): return {}

    raw_ast = state.get('ast_json', {})
    messages_update = []
    
    try:
        nf_code = render_nextflow_code(raw_ast)
        
        # Inject simple warning comment at the bottom if validation error persisted
        validation_error = state.get('validation_error')
        if validation_error:
            warning = f"// ⚠️ WARNING: Pipeline generation failed strict DSL2 validation.\n// The code above is potentially broken or incomplete and was output as a best-effort draft.\n\n"
            nf_code = warning + nf_code
            messages_update.append(AIMessage(content="⚠️ **Generation Warning**: I reached the maximum number of attempts trying to generate a perfectly valid pipeline. I have output the current draft as a **best effort**, but please note that the generated code **might have errors or missing components** based on the strict DSL2 rules."))
            
    except Exception as e:
        print(f"💥 NEXTFLOW RENDERER CRASH: {e}")
        return {"error": f"Nextflow Code Generation Failed: {str(e)}"}

    result = {
        "nextflow_code": nf_code
    }
    if messages_update:
        result["messages"] = messages_update
    return result

def render_mermaid_from_json(data) -> str:
    lines = [
        "flowchart TD",
        "    classDef process fill:#4A90E2,stroke:#357ABD,stroke-width:2px,color:#fff,rx:5px,ry:5px;",
        "    classDef input fill:#50E3C2,stroke:#36A68D,stroke-width:2px,color:#111,rx:5px,ry:5px;",
        "    classDef output fill:#F5A623,stroke:#C28114,stroke-width:2px,color:#fff,rx:5px,ry:5px;",
        "    classDef operator fill:#9013FE,stroke:#6608B8,stroke-width:2px,color:#fff,rx:5px,ry:5px;",
        "    classDef global fill:#9B9B9B,stroke:#656565,stroke-width:2px,color:#fff,rx:5px,ry:5px;"
    ]
    
    subgraphs = {}
    unassigned = []
    
    for node in data.nodes:
        if node.subgraph:
            sg = node.subgraph.strip()
            if sg not in subgraphs: 
                subgraphs[sg] = []
            subgraphs[sg].append(node)
        else:
            unassigned.append(node)

    def draw_node(n):
        label = n.label 
        if n.shape == 'input': return f'    {n.id}(["{label}"]):::input'
        elif n.shape == 'operator': return f'    {n.id}{{"{label}"}}:::operator'
        elif n.shape == 'output': return f'    {n.id}[("{label}")]:::output'
        elif n.shape == 'global': return f'    {n.id}("{label}"):::global'
        else: return f'    {n.id}["{label}"]:::process' 

    for sg_name, nodes in subgraphs.items():
        clean_sg = sg_name.replace(" ", "_").replace(".", "_")
        
        lines.append(f'    subgraph sg_{clean_sg} ["{sg_name}"]')
        lines.append(f'        style sg_{clean_sg} fill:#fdfbfb,stroke:#ebedee,stroke-width:2px,stroke-dasharray: 5 5')
        
        for n in nodes:
            lines.append(draw_node(n))
        lines.append("    end")

    for n in unassigned:
        lines.append(draw_node(n))

    for e in data.edges:
        if e.label and e.label.strip():
            lines.append(f'    {e.source} -->|"{e.label}"| {e.target}')
        else:
            lines.append(f'    {e.source} --> {e.target}')

    return "\n".join(lines)

def render_mermaid_from_ast(ast_json: dict) -> str:
    """
    Generate a Mermaid flowchart directly from the AST JSON.
    Deterministic. Same AST always produces the same diagram.
    """
    lines = [
        "flowchart TD",
        "    classDef process fill:#4A90E2,stroke:#357ABD,stroke-width:2px,color:#fff,rx:5px,ry:5px;",
        "    classDef input fill:#50E3C2,stroke:#36A68D,stroke-width:2px,color:#111,rx:5px,ry:5px;",
        "    classDef output fill:#F5A623,stroke:#C28114,stroke-width:2px,color:#fff,rx:5px,ry:5px;",
        "    classDef operator fill:#9013FE,stroke:#6608B8,stroke-width:2px,color:#fff,rx:5px,ry:5px;",
        "    classDef global fill:#9B9B9B,stroke:#656565,stroke-width:2px,color:#fff,rx:5px,ry:5px;"
    ]
    nodes = []
    edges = []
    node_ids = set()
    instance_counts = {}

    scope_vars = {}
    sw_take_ids = {}
    sw_first_nodes = {}

    known_procs = set()
    for imp in ast_json.get('imports', []):
        for func in imp.get('functions', []):
            name = func.split(' as ')[0].strip()
            known_procs.add(name)
            
    for ip in ast_json.get('inline_processes', []):
        known_procs.add(ip.get('name', ''))
        
    for sw in ast_json.get('sub_workflows', []):
        known_procs.add(sw.get('name', 'unknown'))

    def _is_process_call(name):
        if name.startswith(('step_', 'module_', 'multi_', 'wf_')):
            return True
        return name in known_procs

    def _add_node(nid, label, shape, subgraph=None):
        if nid not in node_ids:
            nodes.append((nid, label, shape, subgraph))
            node_ids.add(nid)

    def _safe_id(name):
        return re.sub(r'[^a-zA-Z0-9_]', '_', name)

    def _get_unique_id(name, scope):
        key = f"{scope}_{name}"
        count = instance_counts.get(key, 0)
        instance_counts[key] = count + 1
        return _safe_id(f"n_{scope}_{name}_{count}")

    def _resolve_var(scope, var_name):
        v = scope_vars.get(scope, {}).get(var_name)
        if v:
            return v
        if var_name in node_ids:
            return var_name
        return None

    def _split_args(args_str):
        if not args_str.strip():
            return []
        depth = 0
        current = []
        parts = []
        for ch in args_str:
            if ch in '({[':
                depth += 1
                current.append(ch)
            elif ch in ')}]':
                depth -= 1
                current.append(ch)
            elif ch == ',' and depth == 0:
                parts.append(''.join(current).strip())
                current = []
            else:
                current.append(ch)
        if current:
            parts.append(''.join(current).strip())
        return [p for p in parts if p]

    def _parse_expression(scope, expr):
        expr = expr.strip()
        if not expr: return None, ""
        
        get_match = re.match(r'(get\w+)\(([^)]*)\)', expr)
        if get_match:
            func_name = get_match.group(1)
            func_args = get_match.group(2)
            func_id = _safe_id(f"in_{scope}_{func_name}")
            display = f"{func_name}({func_args})" if func_args else f"{func_name}()"
            _add_node(func_id, display, "input", scope)
            return func_id, ""

        parts = []
        depth = 0
        current = []
        for ch in expr:
            if ch in '({[':
                depth += 1
                current.append(ch)
            elif ch in ')}]':
                depth -= 1
                current.append(ch)
            elif ch == '.' and depth == 0:
                parts.append(''.join(current).strip())
                current = []
            else:
                current.append(ch)
        if current:
            parts.append(''.join(current).strip())
            
        if not parts: return None, ""
        
        base_var = parts[0]
        if base_var.startswith('Channel'):
            ch_id = _safe_id(f"ch_{scope}_{base_var}")
            _add_node(ch_id, base_var, "input", scope)
            current_id = ch_id
        else:
            b_val = re.split(r'[\[\]]', base_var)[0]
            current_id = _resolve_var(scope, b_val)
            if not current_id:
                var_id = _safe_id(f"var_{scope}_{b_val}")
                _add_node(var_id, b_val, "input", scope) 
                current_id = var_id
        
        current_label = ""
        for p in parts[1:]:
            m2 = re.match(r'^([a-zA-Z0-9_]+)', p)
            if not m2:
                continue
                
            op = m2.group(1)
            op_args = None
            
            paren_match = re.match(r'^[a-zA-Z0-9_]+\s*\(', p)
            if paren_match:
                start_idx = paren_match.end()
                depth = 1
                i = start_idx
                while i < len(p) and depth > 0:
                    if p[i] == '(': depth += 1
                    elif p[i] == ')': depth -= 1
                    i += 1
                if depth == 0:
                    op_args = p[start_idx:i-1]

            if op_args is None:
                if op != 'out':
                    current_label = op
            else:
                op_id = _get_unique_id(op, scope)
                _add_node(op_id, f".{op}()", "operator", scope)
                edges.append((current_id, op_id, current_label))
                current_label = ""
                for arg in _split_args(op_args):
                    arg_node, a_label = _parse_expression(scope, arg)
                    if arg_node:
                        edges.append((arg_node, op_id, a_label))
                current_id = op_id
                
        return current_id, current_label

    def _parse_body(body_code, scope_name):
        if not body_code: return

        scope_vars.setdefault(scope_name, {})
        sw_first_nodes.setdefault(scope_name, [])

        for line in body_code.split('\n'):
            line = line.strip()
            if not line or line.startswith('//'):
                continue

            set_match = re.search(r'(.*?)\.set\s*\{\s*([a-zA-Z_]\w*)\s*\}', line)
            if set_match:
                rhs = set_match.group(1).strip()
                var_name = set_match.group(2)
                end_node, label = _parse_expression(scope_name, rhs)
                if end_node:
                    scope_vars[scope_name][var_name] = end_node
                continue

            m = re.match(r'(?:([a-zA-Z_]\w*)\s*=\s*)?([a-zA-Z0-9_]+)\s*\(', line)
            is_proc_call = False
            if m:
                proc_name = m.group(2)
                if _is_process_call(proc_name):
                    is_proc_call = True
                    start_idx = m.end()
                    depth = 1
                    i = start_idx
                    while i < len(line) and depth > 0:
                        if line[i] == '(': depth += 1
                        elif line[i] == ')': depth -= 1
                        i += 1
                    args_str = line[start_idx:i-1]
                    var_name = m.group(1)
                    
                    proc_id = _get_unique_id(proc_name, scope_name)
                    _add_node(proc_id, proc_name, "process", scope_name)

                    if not sw_first_nodes[scope_name]:
                        sw_first_nodes[scope_name].append(proc_id)

                    if var_name:
                        scope_vars[scope_name][var_name] = proc_id

                    for arg in _split_args(args_str):
                        src_id, src_label = _parse_expression(scope_name, arg)
                        if src_id:
                            edges.append((src_id, proc_id, src_label))
                    continue

            assign_match = re.match(r'([a-zA-Z_]\w*)\s*=\s*(.*)', line)
            if assign_match and not is_proc_call:
                var_name = assign_match.group(1)
                rhs = assign_match.group(2).strip()
                end_node, label = _parse_expression(scope_name, rhs)
                if end_node:
                    scope_vars[scope_name][var_name] = end_node
                continue

    for g in ast_json.get('globals', []):
        gid = _safe_id(f"global_{g.get('name', 'unknown')}")
        _add_node(gid, f"{g.get('name', '?')}", "global")

    for sw in ast_json.get('sub_workflows', []):
        sw_name = sw.get('name', 'unknown')
        scope_vars.setdefault(sw_name, {})
        take_ids = []
        for ch in sw.get('take_channels', []):
            ch_id = _safe_id(f"in_{sw_name}_{ch}")
            _add_node(ch_id, ch, "input", sw_name)
            scope_vars[sw_name][ch] = ch_id
            take_ids.append(ch_id)
        sw_take_ids[sw_name] = take_ids

    for sw in ast_json.get('sub_workflows', []):
        sw_name = sw.get('name', 'unknown')
        _parse_body(sw.get('body_code', ''), sw_name)

        connected_sources = {e[0] for e in edges}
        first_nodes = sw_first_nodes.get(sw_name, [])
        if first_nodes:
            for t_id in sw_take_ids.get(sw_name, []):
                if t_id not in connected_sources:
                    for f_id in first_nodes:
                        edges.append((t_id, f_id, ""))

        for em in sw.get('emit_channels', []):
            em_name = em.split('=')[0].strip() if '=' in em else em.strip()
            em_id = _safe_id(f"out_{sw_name}_{em_name}")
            _add_node(em_id, em_name, "output", sw_name)
            
            rhs = em.split('=')[1].strip() if '=' in em else em_name
            base = re.split(r'[.\[]', rhs)[0].strip()
            source = _resolve_var(sw_name, base)
            if source:
                edges.append((source, em_id, ""))

    ep = ast_json.get('entrypoint', {})
    if ep:
        ep_body = ep.get('body_code', '')
        _parse_body(ep_body, 'entrypoint')

        for sw in ast_json.get('sub_workflows', []):
            sw_name = sw.get('name', 'unknown')
            matches = re.finditer(rf'{re.escape(sw_name)}\s*\(([^)]*)\)', ep_body)
            for m in matches:
                args = _split_args(m.group(1))
                take_ids = sw_take_ids.get(sw_name, [])
                for i, arg in enumerate(args):
                    if i < len(take_ids):
                        base_var = re.split(r'[.\[\(]', arg.strip())[0].strip()
                        source = _resolve_var('entrypoint', base_var)
                        if source:
                            edges.append((source, take_ids[i], ""))

    by_subgraph = {}
    no_subgraph = []
    for nid, label, shape, sg in nodes:
        if sg:
            by_subgraph.setdefault(sg, []).append((nid, label, shape))
        else:
            no_subgraph.append((nid, label, shape))

    def _draw(nid, label, shape):
        label = label.replace('"', "'")
        if shape == 'input': return f'    {nid}(["{label}"]):::input'
        elif shape == 'output': return f'    {nid}[("{label}")]:::output'
        elif shape == 'global': return f'    {nid}("{label}"):::global'
        elif shape == 'operator': return f'    {nid}{{{{"{label}"}}}}:::operator'
        else: return f'    {nid}["{label}"]:::process'

    if 'entrypoint' in by_subgraph:
        lines.append('    subgraph sg_entrypoint ["entrypoint"]')
        lines.append('        style sg_entrypoint fill:#eef2f3,stroke:#8e9eab,stroke-width:2px,stroke-dasharray: 5 5')
        for nid, label, shape in by_subgraph.pop('entrypoint'):
            lines.append(_draw(nid, label, shape))
        lines.append('    end')

    for sg_name, sg_nodes in by_subgraph.items():
        clean = _safe_id(sg_name)
        lines.append(f'    subgraph sg_{clean} ["{sg_name}"]')
        lines.append(f'        style sg_{clean} fill:#fdfbfb,stroke:#ebedee,stroke-width:2px,stroke-dasharray: 5 5')
        for nid, label, shape in sg_nodes:
            lines.append(_draw(nid, label, shape))
        lines.append('    end')

    for nid, label, shape in no_subgraph:
        lines.append(_draw(nid, label, shape))

    seen_edges = set()
    for src, tgt, label in edges:
        if src == tgt: continue
        key = (src, tgt, label)
        if key in seen_edges: continue
        seen_edges.add(key)
        label = label.replace('"', "'")
        if label:
            lines.append(f'    {src} -->|"{label}"| {tgt}')
        else:
            lines.append(f'    {src} --> {tgt}')

    return "\n".join(lines)