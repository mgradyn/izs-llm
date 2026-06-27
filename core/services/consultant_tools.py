from core.utils.logger import logger

"""
Consultant Tools — LangGraph @tool functions for the consultant agent.

These tools are bound to the consultant LLM via bind_tools(), allowing it to
dynamically verify IDs, search the catalog, and inspect template logic
instead of relying solely on bulk RAG context injection.
"""

import re

from langchain.tools import ToolRuntime
from langchain_core.tools import tool

from core.config import settings
from core.loader import data_loader
from core.services.query_normalizer import (
    IGNORE_WORDS,
    build_semantic_query,
    is_discovery_query,
    normalize_query,
)

# ──────────────────────────────────────────────────────────────────────────────
# TOOL 1: Lookup Catalog Item
# ──────────────────────────────────────────────────────────────────────────────

@tool
def lookup_catalog_item(item_id: str, include_code: bool, runtime: ToolRuntime) -> dict:
    """Look up a component or template from the knowledge base to get its exact details.
    Use this to get metadata, inputs, outputs, and optionally the source code or template logic.
    This replaces separate calls for verifying IDs or fetching code.

    Args:
        item_id: The exact ID to look up (e.g. 'process_data_prep' or 'my_pipeline_template')
        include_code: Set to True to also fetch the source code or template logic flow.
    """
    store = runtime.store

    # Check components
    comp_item = store.get(("components",), item_id)
    tmpl_item = store.get(("templates",), item_id) if not comp_item else None

    if not comp_item and not tmpl_item:
        return {"valid": False, "error": f"ID '{item_id}' not found in catalog"}

    result = {"valid": True, "id": item_id}

    if comp_item:
        data = comp_item.value or {}
        result.update({
            "namespace": "component",
            "tool": data.get("tool"),
            "domain": data.get("domain"),
            "description": data.get("description"),
            "inputs": data.get("input_channels") or data.get("input_types") or [],
            "outputs": data.get("output_channels") or data.get("out") or [],
        })
    else:
        data = tmpl_item.value or {}
        result.update({
            "namespace": "template",
            "description": data.get("description"),
            "components_used": data.get("components_used") or [],
            "input_channels": data.get("input_channels") or [],
            "output_channels": data.get("output_channels") or [],
        })
        if include_code:
            result["logic_flow"] = data.get("logic_flow") or []

    if include_code:
        code_item = store.get(("code",), item_id)
        if code_item:
            code = code_item.value.get("content", "")
            result["code_available"] = True
            result["source_code"] = code[:settings.MAX_CODE_DISPLAY_LENGTH] + ("\n// ... (truncated)" if len(code) > settings.MAX_CODE_DISPLAY_LENGTH else "")

            # Parse real channels from code to override potentially stale metadata
            parsed = _parse_nextflow_channels(code)
            if parsed["takes"]:
                result["inputs"] = parsed["takes"]
            if parsed["emits"]:
                result["outputs"] = parsed["emits"]
        else:
            result["code_available"] = False
            result["warning"] = "Source code not found in the code store."

    return result

# ──────────────────────────────────────────────────────────────────────────────
# TOOL 2: Search components/templates (Full Hybrid: Keyword + FAISS)
# ──────────────────────────────────────────────────────────────────────────────

@tool
def search_components(query: str, runtime: ToolRuntime) -> list:  # noqa: C901
    """Search for available components and templates by keyword with semantic matching.
    Use this to find what tools are available for a specific domain-specific task.
    ALWAYS call this first when the user asks about a new analysis type.

    Args:
        query: Search terms describing the analysis need (e.g. 'data preprocessing', 'feature extraction', 'report generation')
    """
    store = runtime.store
    results = []
    warnings = []
    found_ids = set()
    query_info = normalize_query(query)
    query_tokens = query_info["query_tokens"]
    clean_query = query_info["clean_query"]
    query_lower = query_info["query_lower"]

    # Merge default + plugin-specific excluded templates
    excluded_templates = set(settings.RAG_EXCLUDED_TEMPLATES)
    try:
        from core.plugin_loader import get_active_plugin
        excluded_templates |= get_active_plugin().rag_excluded_templates
    except Exception:
        pass

    if is_discovery_query(clean_query) and len(query_tokens) < 2:
        return [
            {
                "type": "meta",
                "warning": "Query is too broad for targeted search.",
                "hint": "Add organism, sequencing type, and analysis goal to refine results.",
            }
        ]

    # ── Keyword: Template Scan ──
    try:
        for tmpl in store.search(("templates",), limit=settings.SEARCH_SCAN_LIMIT):
            tmpl_id = tmpl.key.lower()
            tmpl_data = tmpl.value
            score = 0

            clean_id_words = tmpl_id.replace("_", " ").split()
            for id_word in clean_id_words:
                if len(id_word) > 3 and id_word in query_tokens and id_word not in IGNORE_WORDS:
                    score += 8

            for kw in (tmpl_data.get('keywords') or []):
                if str(kw).lower() in query_tokens:
                    score += 5

            for st in (tmpl_data.get('compatible_seq_types') or []):
                if str(st).lower().replace('_', ' ') in query_lower:
                    score += 3

            if score >= settings.RAG_KEYWORD_TEMPLATE_MIN_SCORE and tmpl.key not in excluded_templates:
                found_ids.add(tmpl.key)
                results.append({
                    "id": tmpl.key,
                    "type": "template",
                    "description": str(tmpl_data.get("description") or "")[:settings.DESCRIPTION_TRUNCATE_TMPL],
                    "components_used": tmpl_data.get("components_used") or [],
                    "_score": score,
                })
    except Exception as e:
        logger.error(f"--- [NODE] CONSULTANT TOOL ERROR template scan error: {e}")

    # ── Keyword: Component Scan ──
    try:
        component_scores = {}
        for comp in store.search(("components",), limit=settings.SEARCH_SCAN_LIMIT):
            comp_id = comp.key.lower()
            comp_data = comp.value
            score = 0

            tool_name = str(comp_data.get('tool', '')).lower()
            domain_name = str(comp_data.get('domain', '')).lower()

            if '__' in comp_id:
                suffix = comp_id.split('__')[-1]
                for sw in suffix.split('_'):
                    if sw and len(sw) > 3 and sw in query_tokens and sw not in IGNORE_WORDS:
                        score += 50

            if tool_name:
                for word in re.split(r'[^a-z0-9]', tool_name):
                    if len(word) > 3 and word in query_tokens and word not in IGNORE_WORDS:
                        score += 50

            if domain_name:
                for part in re.split(r'[^a-z0-9]', domain_name):
                    if len(part) > 3 and part in query_tokens and part not in IGNORE_WORDS:
                        score += 5

            for st in (comp_data.get('compatible_seq_types') or []):
                for st_word in str(st).lower().replace('_', ' ').split():
                    if st_word and len(st_word) > 3 and st_word in query_tokens:
                        score += 5

            # Structural keyword boosting from plugin config
            try:
                from core.plugin_loader import get_active_plugin
                structural_keywords = get_active_plugin().search_keywords
            except Exception:
                structural_keywords = []
            for kw in structural_keywords:
                if kw in query_tokens and kw in comp_id:
                    score += 15

            if score > 0:
                component_scores[comp.key] = (score, comp_data)

        if component_scores:
            max_score = max(s for s, _ in component_scores.values())
            threshold = max_score * settings.RAG_KEYWORD_COMPONENT_THRESHOLD
            for comp_key, (score, comp_data) in sorted(component_scores.items(), key=lambda x: x[1][0], reverse=True):
                if score >= threshold and comp_key not in found_ids:
                    found_ids.add(comp_key)
                    results.append({
                        "id": comp_key,
                        "type": "component",
                        "tool": comp_data.get("tool"),
                        "domain": comp_data.get("domain"),
                        "description": str(comp_data.get("description") or "")[:settings.DESCRIPTION_TRUNCATE_COMP],
                        "inputs": comp_data.get("input_channels") or comp_data.get("input_types") or [],
                        "outputs": comp_data.get("output_channels") or comp_data.get("out") or [],
                        "_score": score,
                    })
    except Exception as e:
        logger.error(f"--- [NODE] CONSULTANT TOOL ERROR component scan error: {e}")

    # ── Semantic Search (FAISS) ──
    try:
        if not data_loader.vector_store:
            warnings.append("Vector store not loaded; semantic search skipped.")
        if data_loader.vector_store:
            semantic_query = build_semantic_query(clean_query, query_tokens)
            if not semantic_query:
                warnings.append("Semantic search skipped because the query was empty after normalization.")
                semantic_query = clean_query

            docs_and_scores = data_loader.vector_store.similarity_search_with_score(
                semantic_query, k=settings.RAG_FAISS_K
            )

            if docs_and_scores:
                best_l2 = docs_and_scores[0][1]
                for doc, l2_dist in docs_and_scores:
                    if l2_dist > settings.RAG_FAISS_MAX_L2_DISTANCE:
                        continue
                    if l2_dist > (best_l2 + settings.RAG_FAISS_RELATIVE_MARGIN):
                        continue

                    meta = doc.metadata
                    item_id = meta.get('id')
                    item_type = meta.get('type')

                    if item_id in found_ids or item_id in excluded_templates:
                        continue

                    found_ids.add(item_id)

                    if item_type == 'template':
                        tmpl_item = store.get(("templates",), item_id)
                        if tmpl_item:
                            tmpl_data = tmpl_item.value
                            results.append({
                                "id": item_id,
                                "type": "template",
                                "description": str(tmpl_data.get("description") or "")[:settings.DESCRIPTION_TRUNCATE_TMPL],
                                "components_used": tmpl_data.get("components_used") or [],
                                "_score": 0,
                                "_semantic": True,
                            })
                    elif item_type == 'component':
                        comp_item = store.get(("components",), item_id)
                        if comp_item:
                            comp_data = comp_item.value
                            results.append({
                                "id": item_id,
                                "type": "component",
                                "tool": comp_data.get("tool"),
                                "domain": comp_data.get("domain"),
                                "description": str(comp_data.get("description") or "")[:settings.DESCRIPTION_TRUNCATE_COMP],
                                "inputs": comp_data.get("input_channels") or comp_data.get("input_types") or [],
                                "outputs": comp_data.get("output_channels") or comp_data.get("out") or [],
                                "_score": 0,
                                "_semantic": True,
                            })
    except Exception as e:
        warnings.append("Semantic search failed; returning keyword matches only.")
        logger.error(f"--- [NODE] CONSULTANT TOOL ERROR faiss search error: {e}")

    # Sort keyword hits first (by score), then semantic hits
    results.sort(key=lambda x: x.get("_score", 0), reverse=True)

    # Clean internal scoring fields
    for r in results:
        r.pop("_score", None)
        r.pop("_semantic", None)

    final_results = results[:settings.MAX_SEARCH_RESULTS]
    if warnings:
        final_results.append({"type": "meta", "warnings": warnings})
    return final_results



# ──────────────────────────────────────────────────────────────────────────────
# TOOL 5: Check channel compatibility between two components
# ──────────────────────────────────────────────────────────────────────────────

def _parse_nextflow_channels(code: str) -> dict:  # noqa: C901
    """Parse take: and emit: blocks from Nextflow DSL2 workflow code.
    Returns {"takes": [...], "emits": [...], "partial": bool}
    """
    takes = []
    emits = []
    partial = False

    if not code or not code.strip():
        return {"takes": [], "emits": [], "partial": True}

    # Check for truncation indicators
    if "// ... (truncated)" in code or code.strip().endswith("..."):
        partial = True

    # Parse take: block — lines after "take:" until "main:" or "emit:" or "}"
    take_match = re.search(
        r'\btake\s*:\s*\n(.*?)(?=\bmain\s*:|\bemit\s*:|\}\s*$|$)',
        code, re.DOTALL
    )
    if take_match:
        take_block = take_match.group(1)
        # Each non-empty, non-comment line in the take block is a channel name
        for line in take_block.strip().split('\n'):
            line = line.strip()
            if line and not line.startswith('//') and not line.startswith('*') and line != '}':
                # Clean up any trailing comments
                clean = re.split(r'\s*//', line)[0].strip()
                if clean:
                    takes.append(clean)

    # Parse emit: block — lines after "emit:" until "}" or end
    emit_match = re.search(
        r'\bemit\s*:\s*\n(.*?)(?=\}\s*$|\bworkflow\s*\{|$)',
        code, re.DOTALL
    )
    if emit_match:
        emit_block = emit_match.group(1)
        for line in emit_block.strip().split('\n'):
            line = line.strip()
            if line and not line.startswith('//') and not line.startswith('*') and line != '}':
                # Handle "channel_name = expression" and "channel_name" forms
                clean = re.split(r'\s*//', line)[0].strip()
                # Extract the channel name (left side of = or the whole line)
                if '=' in clean:
                    chan_name = clean.split('=')[0].strip()
                else:
                    chan_name = clean.split('.')[0].strip()  # e.g. "process_out.data" → "process_out"
                if chan_name and chan_name != '}':
                    emits.append(chan_name)

    return {"takes": takes, "emits": emits, "partial": partial}


def _parse_include_statements(code: str) -> list:
    """Parse 'include { proc_xxx } from ...' statements from Nextflow code.
    Returns list of included component IDs.
    """
    includes = []
    if not code:
        return includes

    # Match: include { proc_xxx } from '...'  or  include { proc_xxx; proc_yyy } from '...'
    for match in re.finditer(r"include\s*\{([^}]+)\}\s*from", code):
        block = match.group(1)
        for item in block.split(';'):
            item = item.strip()
            # Handle "proc_xxx as alias" patterns
            name = item.split(' as ')[0].strip() if ' as ' in item else item.strip()
            # Only keep identifiers that look like proc_/mod_/multiproc_ IDs or function names
            if name and re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', name):
                includes.append(name)

    return includes



# ──────────────────────────────────────────────────────────────────────────────
# TOOL 6 Validate a complete pipeline plan
# ──────────────────────────────────────────────────────────────────────────────

@tool
def check_plan_logic(component_ids: list, template_id: str, runtime: ToolRuntime) -> dict:  # noqa: C901
    """Validate a proposed pipeline plan by checking:
    1. All component IDs exist in the catalog
    2. Channel flow is consistent between consecutive steps
    3. If a template is referenced, compare the plan against the template's declared steps
    4. Parse template source code to detect include statements and find missing components

    Call this BEFORE finalizing any APPROVED plan to catch issues early.
    Pass template_id as empty string "" if no template is used.

    Args:
        component_ids: List of component IDs in execution order
        template_id: Template ID to compare against. keep it empty if there is no template
    """
    store = runtime.store
    issues = []
    warnings = []

    logger.info(f"--- [NODE] CONSULTANT TOOL check plan logic for {len(component_ids)} components with template {template_id}")

    if not component_ids:
        logger.info("--- [NODE] CONSULTANT TOOL no component ids provided")
        return {
            "valid": False,
            "issues": ["No component IDs provided"],
            "warnings": [],
        }

    # we check if all ids are real
    valid_ids = []
    invalid_ids = []
    for comp_id in component_ids:
        comp = store.get(("components",), comp_id)
        tmpl = store.get(("templates",), comp_id) if not comp else None
        if comp or tmpl:
            valid_ids.append(comp_id)
        else:
            invalid_ids.append(comp_id)
            issues.append(f"Component '{comp_id}' not found in catalog")

    logger.info(f"--- [NODE] CONSULTANT TOOL found {len(invalid_ids)} invalid ids")

    # we look at how the data flows between the steps
    channel_report = []
    for i in range(len(valid_ids) - 1):
        src_id = valid_ids[i]
        tgt_id = valid_ids[i + 1]

        # we get the channel info from the catalog
        src_comp = store.get(("components",), src_id)
        tgt_comp = store.get(("components",), tgt_id)

        src_outputs = []
        tgt_inputs = []

        if src_comp:
            src_data = src_comp.value or {}
            src_outputs = src_data.get("output_channels") or src_data.get("out") or []
        else:
            src_tmpl = store.get(("templates",), src_id)
            if src_tmpl:
                src_outputs = (src_tmpl.value or {}).get("output_channels") or []

        if tgt_comp:
            tgt_data = tgt_comp.value or {}
            tgt_inputs = tgt_data.get("input_channels") or tgt_data.get("input_types") or []
        else:
            tgt_tmpl = store.get(("templates",), tgt_id)
            if tgt_tmpl:
                tgt_inputs = (tgt_tmpl.value or {}).get("input_channels") or []

        # we read the code to see the exact channels
        src_code_item = store.get(("code",), src_id)
        tgt_code_item = store.get(("code",), tgt_id)

        src_code = src_code_item.value.get("content", "") if src_code_item else ""
        tgt_code = tgt_code_item.value.get("content", "") if tgt_code_item else ""

        src_parsed = _parse_nextflow_channels(src_code)
        tgt_parsed = _parse_nextflow_channels(tgt_code)

        effective_outputs = src_parsed["emits"] if src_parsed["emits"] else src_outputs
        effective_inputs = tgt_parsed["takes"] if tgt_parsed["takes"] else tgt_inputs

        if src_parsed["partial"] or tgt_parsed["partial"]:
            warnings.append(f"Code for '{src_id}' to '{tgt_id}' may be partial so channel analysis is approximate")

        pair_info = {
            "source": src_id,
            "target": tgt_id,
            "source_emits": effective_outputs,
            "target_takes": effective_inputs,
        }

        if not effective_outputs:
            warnings.append(f"No output channels detected for '{src_id}' so we cannot verify connection to '{tgt_id}'")
        elif not effective_inputs:
            warnings.append(f"No input channels detected for '{tgt_id}' so we cannot verify connection from '{src_id}'")
        else:
            # we check if the channels overlap
            out_lower = {ch.lower() for ch in effective_outputs}
            in_lower = {ch.lower() for ch in effective_inputs}
            if not (out_lower & in_lower):
                pair_info["mismatch"] = True
                warnings.append(
                    f"Channel mismatch. '{src_id}' emits {effective_outputs} "
                    f"but '{tgt_id}' takes {effective_inputs}. this may need channel adaptation"
                )

        channel_report.append(pair_info)

    # we compare the plan with the template
    template_coverage = None
    if template_id:
        logger.info(f"--- [NODE] CONSULTANT TOOL comparing plan with template {template_id}")
        tmpl_item = store.get(("templates",), template_id)
        if not tmpl_item:
            issues.append(f"Template '{template_id}' not found in catalog")
        else:
            tmpl_data = tmpl_item.value or {}
            tmpl_steps = set(tmpl_data.get("components_used") or [])
            plan_steps = set(component_ids)

            missing_from_plan = tmpl_steps - plan_steps
            extra_in_plan = plan_steps - tmpl_steps

            # we also look at the include lines in the template code
            code_item = store.get(("code",), template_id)
            includes_from_code = []
            if code_item:
                tmpl_code = code_item.value.get("content", "")
                includes_from_code = _parse_include_statements(tmpl_code)

                # we only keep the includes that are recognized components in the catalog
                from core.catalog_registry import get_registry
                registry = get_registry()
                code_steps = {inc for inc in includes_from_code if registry.component_exists(inc)}

                # steps we found in the code but not in the catalog list
                code_only = code_steps - tmpl_steps
                if code_only:
                    warnings.append(
                        f"Template code includes {list(code_only)} which are not in "
                        f"the template components_used list. they may be helper dependencies"
                    )

                # steps we found in the code but not in our plan
                code_missing = code_steps - plan_steps
                if code_missing:
                    warnings.append(
                        f"Template code references {list(code_missing)} which are "
                        f"not in your plan. please verify these are not needed"
                    )
            else:
                warnings.append(f"No source code found for template '{template_id}'. include analysis skipped")

            template_coverage = {
                "template_id": template_id,
                "template_steps": list(tmpl_steps),
                "plan_steps": list(plan_steps),
                "missing_from_plan": list(missing_from_plan),
                "extra_in_plan": list(extra_in_plan),
                "code_includes": includes_from_code,
            }

            if missing_from_plan:
                warnings.append(
                    f"Plan is missing template steps {list(missing_from_plan)}. "
                    f"these may be needed for correct pipeline logic"
                )

    is_valid = len(issues) == 0
    logger.info(f"--- [NODE] CONSULTANT TOOL check plan logic finished. valid is {is_valid}")

    result = {
        "valid": is_valid,
        "checked_ids": len(component_ids),
        "valid_ids": valid_ids,
        "invalid_ids": invalid_ids,
        "channel_flow": channel_report,
        "issues": issues,
        "warnings": warnings,
    }

    if template_coverage:
        result["template_coverage"] = template_coverage

    return result


# ──────────────────────────────────────────────────────────────────────────────
# TOOL 7: Find which templates use a component (reverse lookup)
# ──────────────────────────────────────────────────────────────────────────────

@tool
def find_component_usage(component_id: str, runtime: ToolRuntime) -> dict:
    """Find which templates/modules use a specific component and HOW it is wired.
    Returns real production code snippets showing the component's calling context
    (what channels feed it, what comes before/after it in the workflow).

    Use this to understand the conventional way to connect a component,
    especially when building custom pipelines with components you haven't used before.

    Args:
        component_id: The exact component ID (e.g. 'process_my_component')
    """
    store = runtime.store

    # Check the component exists first
    comp = store.get(("components",), component_id)
    tmpl = store.get(("templates",), component_id) if not comp else None
    if not comp and not tmpl:
        return {"error": f"Component '{component_id}' not found in catalog"}

    # Look up the pre-built usage index
    usage_item = store.get(("usage",), component_id)
    if not usage_item:
        return {
            "component_id": component_id,
            "used_in_templates": [],
            "note": "This component is not used in any template. You will need to wire it based on its take/emit channels."
        }

    usages = usage_item.value.get("usages", [])
    logger.info(f"--- [NODE] CONSULTANT TOOL find_component_usage: {component_id} found in {len(usages)} templates")

    results = []
    for u in usages:
        results.append({
            "template_id": u["template_id"],
            "template_description": u.get("template_description", ""),
            "usage_snippet": u.get("snippet", "(no snippet)"),
        })

    return {
        "component_id": component_id,
        "used_in_templates": results,
        "count": len(results),
    }


# ──────────────────────────────────────────────────────────────────────────────
# TOOL 8: Search helper functions
# ──────────────────────────────────────────────────────────────────────────────

@tool
def search_helper_functions(query: str, runtime: ToolRuntime) -> list:
    """Search for available helper functions by keyword (e.g., 'input', 'reference', 'metadata').
    Use this to find built-in data retrieval and formatting functions.
    
    Args:
        query: Search terms describing the helper function (e.g., 'retrieve fastq', 'parse riscd').
    """
    store = runtime.store
    res_item = store.get(("resources",), "helper_functions")
    res_list = res_item.value.get("list", []) if res_item else []
    
    if not res_list:
        return [{"error": "No helper functions found in the catalog."}]

    query_tokens = [q.lower() for q in re.split(r'[^a-z0-9]', query.lower()) if q]
    if not query_tokens:
        return [{"error": "Invalid search query."}]

    results = []
    for h in res_list:
        score = 0
        name = h.get('name', '').lower()
        desc = h.get('description', '').lower()
        keywords = [k.lower() for k in h.get('keywords', [])]
        aliases = [a.lower() for a in h.get('aliases', [])]
        
        for q in query_tokens:
            if len(q) < 3: continue
            if q in name: score += 10
            if q in desc: score += 5
            if any(q in k for k in keywords): score += 8
            if any(q in a for a in aliases): score += 8
            
        if score > 0:
            results.append((score, h))
            
    results.sort(key=lambda x: x[0], reverse=True)
    
    if not results:
        return [{"warning": "No helper functions matched your query."}]
        
    return [
        {
            "name": h.get('name'),
            "description": h.get('description'),
            "usage": h.get('usage')
        } for _, h in results[:5]
    ]


# ──────────────────────────────────────────────────────────────────────────────
# TOOL 9: Search design patterns
# ──────────────────────────────────────────────────────────────────────────────

@tool
def search_design_patterns(query: str, runtime: ToolRuntime) -> list:
    """Search for domain-specific data-shaping design patterns by keyword.
    Use this when you are unsure how to wire domain-specific components 
    (e.g., 'host depletion', 'coverage mapping', 'dynamic branching').
    
    Args:
        query: Search terms describing the pattern (e.g., 'host depletion').
    """
    store = runtime.store
    # The patterns are stored individually in the store under ("patterns",)
    # Since we can't iterate the store keys easily here, we search the loaded list or we can fetch all.
    # We will search through the store items if possible.
    
    # Actually `InMemoryStore.search` exists in LangGraph.
    items = store.search(("patterns",))
    if not items:
        return [{"error": "No design patterns found in the catalog."}]

    query_tokens = [q.lower() for q in re.split(r'[^a-z0-9]', query.lower()) if q]
    if not query_tokens:
        return [{"error": "Invalid search query."}]

    results = []
    for item in items:
        p = item.value
        score = 0
        name = p.get('title', '').lower()
        desc = p.get('description', '').lower()
        
        for q in query_tokens:
            if len(q) < 3: continue
            if q in name: score += 10
            if q in desc: score += 5
            
        if score > 0:
            results.append((score, p))
            
    results.sort(key=lambda x: x[0], reverse=True)
    
    if not results:
        return [{"warning": "No design patterns matched your query."}]
        
    return [
        {
            "title": p.get('title'),
            "description": p.get('description')
        } for _, p in results[:3]
    ]

# ──────────────────────────────────────────────────────────────────────────────
# EXPORT: Tool list for ToolNode registration
# ──────────────────────────────────────────────────────────────────────────────

CONSULTANT_TOOLS = [
    lookup_catalog_item,
    search_components,
    check_plan_logic,
    find_component_usage,
    search_helper_functions,
    search_design_patterns,
]
