import re
from typing import Any

from jinja2 import Template
from langchain_core.messages import AIMessage

from core.services.graph_state import GraphState
from core.utils.logger import logger
from core.utils.rendering import NF_TEMPLATE_AST


def render_nextflow_code(ast: Any) -> str:
    if hasattr(ast, 'model_dump'):
        data = ast.model_dump()
    elif hasattr(ast, 'dict'):
        data = ast.dict()
    else:
        # Prevent in-place mutation of the GraphState object
        data = dict(ast)

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

def renderer_node(state: GraphState) -> Any:
    logger.info("--- [NODE] RENDERER ---")

    if state.get("error"): return {}

    raw_ast = state.get('ast_json', {})
    messages_update = []

    try:
        nf_code = render_nextflow_code(raw_ast)

        # Inject simple warning comment at the bottom if validation error persisted
        validation_error = state.get('validation_error')
        if validation_error:
            warning = "// ⚠️ WARNING: Pipeline generation failed strict DSL2 validation.\n// The code above is potentially broken or incomplete and was output as a best-effort draft.\n\n"
            nf_code = warning + nf_code
            messages_update.append(AIMessage(content="⚠️ **Generation Warning**: I reached the maximum number of attempts trying to generate a perfectly valid pipeline. I have output the current draft as a **best effort**, but please note that the generated code **might have errors or missing components** based on the strict DSL2 rules."))

    except Exception as e:
        logger.error(f"💥 NEXTFLOW RENDERER CRASH: {e}")
        return {"error": f"Nextflow Code Generation Failed: {e!s}"}

    result = {
        "nextflow_code": nf_code
    }
    if messages_update:
        result["messages"] = messages_update
    return result

def render_mermaid_from_json(data: Any) -> str:  # noqa: C901
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

    def draw_node(n: Any) -> str:
        label = n.label.replace('"', "'")
        if n.shape == 'input': return f'    {n.id}(["{label}"]):::input'
        if n.shape == 'operator': return f'    {n.id}{{"{label}"}}:::operator'
        if n.shape == 'output': return f'    {n.id}[("{label}")]:::output'
        if n.shape == 'global': return f'    {n.id}("{label}"):::global'
        return f'    {n.id}["{label}"]:::process'

    for sg_name, nodes in subgraphs.items():
        clean_sg = sg_name.replace(" ", "_").replace(".", "_")
        sg_label = sg_name.replace('"', "'")

        lines.append(f'    subgraph sg_{clean_sg} ["{sg_label}"]')
        lines.append(f'        style sg_{clean_sg} fill:#fdfbfb,stroke:#ebedee,stroke-width:2px,stroke-dasharray: 5 5')

        for n in nodes:
            lines.append(draw_node(n))
        lines.append("    end")

    for n in unassigned:
        lines.append(draw_node(n))

    for e in data.edges:
        if e.label and e.label.strip():
            lbl = e.label.replace('"', "'")
            lines.append(f'    {e.source} -->|"{lbl}"| {e.target}')
        else:
            lines.append(f'    {e.source} --> {e.target}')

    return "\n".join(lines)

class MermaidRenderer:
    def __init__(self, ast_json: dict) -> None:
        self.ast = ast_json
        self.lines = [
            "flowchart TD",
            "    classDef process fill:#4A90E2,stroke:#357ABD,stroke-width:2px,color:#fff,rx:5px,ry:5px;",
            "    classDef input fill:#50E3C2,stroke:#36A68D,stroke-width:2px,color:#111,rx:5px,ry:5px;",
            "    classDef output fill:#F5A623,stroke:#C28114,stroke-width:2px,color:#fff,rx:5px,ry:5px;",
            "    classDef operator fill:#9013FE,stroke:#6608B8,stroke-width:2px,color:#fff,rx:5px,ry:5px;",
            "    classDef global fill:#9B9B9B,stroke:#656565,stroke-width:2px,color:#fff,rx:5px,ry:5px;"
        ]
        self.nodes = []
        self.edges = []
        self.node_ids = set()
        self.instance_counts = {}
        self.scope_vars = {}
        self.sw_take_ids = {}
        self.sw_first_nodes = {}
        self.known_procs = set()

        for imp in self.ast.get('imports', []):
            for func in imp.get('functions', []):
                self.known_procs.add(func.split(' as ')[0].strip())
        for ip in self.ast.get('inline_processes', []):
            self.known_procs.add(ip.get('name', ''))
        for sw in self.ast.get('sub_workflows', []):
            self.known_procs.add(sw.get('name', 'unknown'))

    def _is_process_call(self, name: str) -> bool:
        if name in self.known_procs: return True
        try:
            from core.catalog_registry import get_registry
            if get_registry().component_exists(name): return True
        except Exception: pass
        return False

    def _add_node(self, nid: str, label: str, shape: str, subgraph: str | None = None) -> None:
        if nid not in self.node_ids:
            self.nodes.append((nid, label, shape, subgraph))
            self.node_ids.add(nid)

    def _safe_id(self, name: str) -> str:
        return re.sub(r'[^a-zA-Z0-9_]', '_', name)

    def _get_unique_id(self, name: str, scope: str) -> str:
        key = f"{scope}_{name}"
        count = self.instance_counts.get(key, 0)
        self.instance_counts[key] = count + 1
        return self._safe_id(f"n_{scope}_{name}_{count}")

    def _resolve_var(self, scope: str, var_name: str) -> str | None:
        v = self.scope_vars.get(scope, {}).get(var_name)
        return v if v else (var_name if var_name in self.node_ids else None)

    def _split_args(self, args_str: str) -> list[str]:
        if not args_str.strip():
            return []
        depth, current, parts = 0, [], []
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

    def _parse_expression(self, scope: str, expr: str) -> tuple[str | None, str]:  # noqa: C901
        expr = expr.strip()
        if not expr:
            return None, ""
        get_match = re.match(r'(get\w+)\(([^)]*)\)', expr)
        if get_match:
            f_name, f_args = get_match.groups()
            f_id = self._safe_id(f"in_{scope}_{f_name}")
            self._add_node(f_id, f"{f_name}({f_args})" if f_args else f"{f_name}()", "input", scope)
            return f_id, ""

        depth, current, parts = 0, [], []
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
        if not parts:
            return None, ""

        base_var = parts[0]
        if base_var.startswith('Channel'):
            c_id = self._safe_id(f"ch_{scope}_{base_var}")
            self._add_node(c_id, base_var, "input", scope)
            current_id = c_id
        else:
            b_val = re.split(r'[\[\]]', base_var)[0]
            current_id = self._resolve_var(scope, b_val)
            if not current_id:
                var_id = self._safe_id(f"var_{scope}_{b_val}")
                self._add_node(var_id, b_val, "input", scope)
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
                s_idx = paren_match.end()
                d, i = 1, s_idx
                while i < len(p) and d > 0:
                    if p[i] == '(': d += 1
                    elif p[i] == ')': d -= 1
                    i += 1
                if d == 0:
                    op_args = p[s_idx:i-1]
            if op_args is None:
                if op != 'out':
                    current_label = op
            else:
                op_id = self._get_unique_id(op, scope)
                self._add_node(op_id, f".{op}()", "operator", scope)
                self.edges.append((current_id, op_id, current_label))
                current_label = ""
                for arg in self._split_args(op_args):
                    a_node, a_label = self._parse_expression(scope, arg)
                    if a_node:
                        self.edges.append((a_node, op_id, a_label))
                current_id = op_id
        return current_id, current_label

    def _parse_body(self, body_code: str, scope_name: str) -> None:  # noqa: C901
        if not body_code:
            return
        self.scope_vars.setdefault(scope_name, {})
        self.sw_first_nodes.setdefault(scope_name, [])

        # Remove line comments
        clean_code = re.sub(r'//.*', '', body_code)

        stmts = []
        curr = []
        depth = 0
        for ch in clean_code:
            if ch in '({[': depth += 1
            elif ch in ')}]': depth -= 1

            if depth == 0 and ch in ('\n', ';'):
                if curr:
                    stmts.append(''.join(curr).strip())
                    curr = []
            else:
                curr.append(ch)
        if curr:
            stmts.append(''.join(curr).strip())

        for stmt in stmts:
            if not stmt:
                continue

            set_m = re.search(r'(.*?)\.set\s*\{\s*([a-zA-Z_]\w*)\s*\}', stmt, flags=re.DOTALL)
            if set_m:
                e_node, _l = self._parse_expression(scope_name, set_m.group(1).strip())
                if e_node:
                    self.scope_vars[scope_name][set_m.group(2)] = e_node
                continue

            m = re.match(r'(?:([a-zA-Z_]\w*)\s*=\s*)?([a-zA-Z0-9_]+)\s*\(', stmt, flags=re.DOTALL)
            is_proc = False
            if m:
                p_name = m.group(2)
                if self._is_process_call(p_name):
                    is_proc = True
                    s_idx = m.end()
                    d, i = 1, s_idx
                    while i < len(stmt) and d > 0:
                        if stmt[i] == '(': d += 1
                        elif stmt[i] == ')': d -= 1
                        i += 1
                    a_str = stmt[s_idx:i-1]
                    p_id = self._get_unique_id(p_name, scope_name)
                    self._add_node(p_id, p_name, "process", scope_name)
                    if not self.sw_first_nodes[scope_name]:
                        self.sw_first_nodes[scope_name].append(p_id)
                    if m.group(1):
                        self.scope_vars[scope_name][m.group(1)] = p_id
                    for arg in self._split_args(a_str):
                        s_id, s_lbl = self._parse_expression(scope_name, arg)
                        if s_id:
                            self.edges.append((s_id, p_id, s_lbl))
                    continue

            assign_m = re.match(r'([a-zA-Z_]\w*)\s*=\s*(.*)', stmt, flags=re.DOTALL)
            if assign_m and not is_proc:
                e_node, _l = self._parse_expression(scope_name, assign_m.group(2).strip())
                if e_node:
                    self.scope_vars[scope_name][assign_m.group(1)] = e_node

    def _draw(self, nid: str, label: str, shape: str) -> str:
        _l = label.replace('"', "'")
        if shape == 'input':
            return f'    {nid}(["{_l}"]):::input'
        if shape == 'output':
            return f'    {nid}[("{_l}")]:::output'
        if shape == 'global':
            return f'    {nid}("{_l}"):::global'
        if shape == 'operator':
            return f'    {nid}{{{{"{_l}"}}}}:::operator'
        return f'    {nid}["{_l}"]:::process'

    def _render_globals(self) -> None:
        for g in self.ast.get('globals', []):
            self._add_node(self._safe_id(f"global_{g.get('name', 'unknown')}"), f"{g.get('name', '?')}", "global")

    def _render_subworkflows_takes(self) -> None:
        for sw in self.ast.get('sub_workflows', []):
            sw_name = sw.get('name', 'unknown')
            self.scope_vars.setdefault(sw_name, {})
            t_ids = []
            for ch in sw.get('take_channels', []):
                c_id = self._safe_id(f"in_{sw_name}_{ch}")
                self._add_node(c_id, ch, "input", sw_name)
                self.scope_vars[sw_name][ch] = c_id
                t_ids.append(c_id)
            self.sw_take_ids[sw_name] = t_ids

    def _render_subworkflows_bodies(self) -> None:
        for sw in self.ast.get('sub_workflows', []):
            sw_name = sw.get('name', 'unknown')
            self._parse_body(sw.get('body_code', ''), sw_name)
            conn = {e[0] for e in self.edges}
            first = self.sw_first_nodes.get(sw_name, [])
            if first:
                for t_id in self.sw_take_ids.get(sw_name, []):
                    if t_id not in conn:
                        for f_id in first:
                            self.edges.append((t_id, f_id, ""))
            for em in sw.get('emit_channels', []):
                em_name = em.split('=')[0].strip() if '=' in em else em.strip()
                em_id = self._safe_id(f"out_{sw_name}_{em_name}")
                self._add_node(em_id, em_name, "output", sw_name)
                rhs = em.split('=')[1].strip() if '=' in em else em_name
                source = self._resolve_var(sw_name, re.split(r'[.\[]', rhs)[0].strip())
                if source:
                    self.edges.append((source, em_id, ""))

    def _render_entrypoint(self) -> None:
        ep = self.ast.get('entrypoint', {})
        if ep:
            ep_body = ep.get('body_code', '')
            self._parse_body(ep_body, 'entrypoint')
            for sw in self.ast.get('sub_workflows', []):
                sw_name = sw.get('name', 'unknown')
                for m in re.finditer(rf'{re.escape(sw_name)}\s*\(([^)]*)\)', ep_body):
                    args = self._split_args(m.group(1))
                    t_ids = self.sw_take_ids.get(sw_name, [])
                    for i, arg in enumerate(args):
                        if i < len(t_ids):
                            source = self._resolve_var('entrypoint', re.split(r'[.\[\(]', arg.strip())[0].strip())
                            if source:
                                self.edges.append((source, t_ids[i], ""))

    def _build_lines(self) -> None:  # noqa: C901
        by_sg, no_sg = {}, []
        for nid, lbl, shp, sg in self.nodes:
            if sg:
                by_sg.setdefault(sg, []).append((nid, lbl, shp))
            else:
                no_sg.append((nid, lbl, shp))
        if 'entrypoint' in by_sg:
            self.lines.extend(['    subgraph sg_entrypoint ["entrypoint"]', '        style sg_entrypoint fill:#eef2f3,stroke:#8e9eab,stroke-width:2px,stroke-dasharray: 5 5'])
            for nid, lbl, shp in by_sg.pop('entrypoint'):
                self.lines.append(self._draw(nid, lbl, shp))
            self.lines.append('    end')
        for sg_name, sg_nodes in by_sg.items():
            clean = self._safe_id(sg_name)
            self.lines.extend([f'    subgraph sg_{clean} ["{sg_name}"]', f'        style sg_{clean} fill:#fdfbfb,stroke:#ebedee,stroke-width:2px,stroke-dasharray: 5 5'])
            for nid, lbl, shp in sg_nodes:
                self.lines.append(self._draw(nid, lbl, shp))
            self.lines.append('    end')
        for nid, lbl, shp in no_sg:
            self.lines.append(self._draw(nid, lbl, shp))
        seen = set()
        for src, tgt, lbl in self.edges:
            if src == tgt: continue
            k = (src, tgt, lbl)
            if k in seen: continue
            seen.add(k)
            lbl = lbl.replace('"', "'")
            if lbl:
                self.lines.append(f'    {src} -->|"{lbl}"| {tgt}')
            else:
                self.lines.append(f'    {src} --> {tgt}')

    def render(self) -> str:
        self._render_globals()
        self._render_subworkflows_takes()
        self._render_subworkflows_bodies()
        self._render_entrypoint()
        self._build_lines()
        return "\n".join(self.lines)

def render_mermaid_from_ast(ast_json: dict) -> str:
    """
    Generate a Mermaid flowchart directly from the AST JSON.
    Deterministic. Same AST always produces the same diagram.
    """
    renderer = MermaidRenderer(ast_json)
    return renderer.render()
