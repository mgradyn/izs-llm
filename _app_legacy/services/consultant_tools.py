"""
Consultant Tools — LangGraph @tool functions for the consultant agent.

These tools are bound to the consultant LLM via bind_tools(), allowing it to
dynamically verify IDs, search the catalog, and inspect template logic
instead of relying solely on bulk RAG context injection.
"""

import re
from langchain_core.tools import tool
from langchain.tools import ToolRuntime
from app.core.config import settings
from app.core.loader import data_loader
from app.services.query_normalizer import (
    IGNORE_WORDS,
    build_semantic_query,
    is_discovery_query,
    normalize_query,
)


# ──────────────────────────────────────────────────────────────────────────────
# TOOL 1: Verify a component or template ID
# ──────────────────────────────────────────────────────────────────────────────

@tool
def verify_component_id(component_id: str, runtime: ToolRuntime) -> dict:
    """Verify if a component or template ID exists in the knowledge base.
    Use this to confirm an ID is real before including it in a pipeline plan.
    Returns metadata if found, or error details if not found.
    
    Args:
        component_id: The exact ID to verify (e.g. 'step_2AS_mapping__ivar' or 'module_covid_emergency')
    """
    store = runtime.store
    
    # Check components namespace
    item = store.get(("components",), component_id)
    if item:
        data = item.value
        return {
            "valid": True,
            "namespace": "component",
            "id": component_id,
            "tool": data.get("tool"),
            "domain": data.get("domain"),
            "description": data.get("description"),
            "inputs": data.get("input_channels", data.get("input_types", [])),
            "outputs": data.get("output_channels", data.get("out", [])),
        }
    
    # Check templates namespace
    item = store.get(("templates",), component_id)
    if item:
        data = item.value
        return {
            "valid": True,
            "namespace": "template",
            "id": component_id,
            "description": data.get("description"),
            "components_used": data.get("components_used", []),
            "input_channels": data.get("input_channels", []),
            "output_channels": data.get("output_channels", []),
        }
    
    return {"valid": False, "error": f"ID '{component_id}' not found in catalog"}


# ──────────────────────────────────────────────────────────────────────────────
# TOOL 2: Search components/templates (Full Hybrid: Keyword + FAISS)
# ──────────────────────────────────────────────────────────────────────────────

@tool
def search_components(query: str, runtime: ToolRuntime) -> list:
    """Search for available components and templates by keyword with semantic matching.
    Use this to find what tools are available for a specific bioinformatics task.
    ALWAYS call this first when the user asks about a new analysis type.
    
    Args:
        query: Search terms describing the analysis need (e.g. 'illumina trimming', 'amr resistance detection', 'nanopore assembly')
    """
    store = runtime.store
    results = []
    warnings = []
    found_ids = set()
    query_info = normalize_query(query)
    query_tokens = query_info["query_tokens"]
    clean_query = query_info["clean_query"]
    query_lower = query_info["query_lower"]
    
    EXCLUDED_TEMPLATES = settings.RAG_EXCLUDED_TEMPLATES

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
            
            clean_id_words = tmpl_id.replace("module_", "").replace("_", " ").split()
            for id_word in clean_id_words:
                if len(id_word) > 3 and id_word in query_tokens and id_word not in IGNORE_WORDS:
                    score += 8
            
            for kw in tmpl_data.get('keywords', []):
                if str(kw).lower() in query_tokens:
                    score += 5
            
            for st in tmpl_data.get('compatible_seq_types', []):
                if str(st).lower().replace('_', ' ') in query_lower:
                    score += 3
            
            if score >= settings.RAG_KEYWORD_TEMPLATE_MIN_SCORE and tmpl.key not in EXCLUDED_TEMPLATES:
                found_ids.add(tmpl.key)
                results.append({
                    "id": tmpl.key,
                    "type": "template",
                    "description": tmpl_data.get("description", "")[:150],
                    "components_used": tmpl_data.get("components_used", []),
                    "_score": score,
                })
    except Exception as e:
        print(f"--- [NODE] CONSULTANT TOOL ERROR template scan error: {e}")

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
            
            for st in comp_data.get('compatible_seq_types', []):
                for st_word in str(st).lower().replace('_', ' ').split():
                    if st_word and len(st_word) > 3 and st_word in query_tokens:
                        score += 5
            
            structural_keywords = [
                'lineage', 'denovo', 'trimming', 'mapping', 'qc', 'clustering',
                'class', 'hostdepl', 'filtering', 'typing', 'annotation',
                'pangenome', 'phylogeny', 'metagenomics', 'amr', 'plasmid',
                'polishing', 'consensus', 'alignment', 'surveillance',
            ]
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
                        "description": comp_data.get("description", "")[:settings.DESCRIPTION_TRUNCATE_COMP],
                        "inputs": comp_data.get("input_channels", comp_data.get("input_types", [])),
                        "outputs": comp_data.get("output_channels", comp_data.get("out", [])),
                        "_score": score,
                    })
    except Exception as e:
        print(f"--- [NODE] CONSULTANT TOOL ERROR component scan error: {e}")

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
                    
                    if item_id in found_ids or item_id in EXCLUDED_TEMPLATES:
                        continue
                    
                    found_ids.add(item_id)
                    
                    if item_type == 'template':
                        tmpl_item = store.get(("templates",), item_id)
                        if tmpl_item:
                            tmpl_data = tmpl_item.value
                            results.append({
                                "id": item_id,
                                "type": "template",
                                "description": tmpl_data.get("description", "")[:settings.DESCRIPTION_TRUNCATE_TMPL],
                                "components_used": tmpl_data.get("components_used", []),
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
                                "description": comp_data.get("description", "")[:settings.DESCRIPTION_TRUNCATE_COMP],
                                "inputs": comp_data.get("input_channels", comp_data.get("input_types", [])),
                                "outputs": comp_data.get("output_channels", comp_data.get("out", [])),
                                "_score": 0,
                                "_semantic": True,
                            })
    except Exception as e:
        warnings.append("Semantic search failed; returning keyword matches only.")
        print(f"--- [NODE] CONSULTANT TOOL ERROR faiss search error: {e}")

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
# TOOL 3: Get template logic and source code
# ──────────────────────────────────────────────────────────────────────────────

@tool
def get_template_logic(template_id: str, runtime: ToolRuntime) -> dict:
    """Fetch the detailed logic flow and source code for a specific template.
    Use this when you need to understand HOW a template works to decide
    if it can be used as EXACT_MATCH or needs ADAPTED_MATCH.
    Also use this to verify how components connect in an existing template.
    
    Args:
        template_id: The exact template ID (e.g. 'module_covid_emergency')
    """
    store = runtime.store
    tmpl = store.get(("templates",), template_id)
    if not tmpl:
        return {"error": f"Template '{template_id}' not found"}
    
    tmpl_data = tmpl.value
    code_item = store.get(("code",), template_id)
    
    result = {
        "id": template_id,
        "description": tmpl_data.get("description"),
        "logic_flow": tmpl_data.get("logic_flow", []),
        "components_used": tmpl_data.get("components_used", []),
        "input_channels": tmpl_data.get("input_channels", []),
        "output_channels": tmpl_data.get("output_channels", []),
        "compatible_seq_types": tmpl_data.get("compatible_seq_types", []),
    }
    
    if code_item:
        code = code_item.value.get("content", "")
        result["code_available"] = bool(code)
        # Truncate very long code to avoid context explosion
        if len(code) > settings.MAX_CODE_DISPLAY_LENGTH:
            result["code_snippet"] = code[:settings.MAX_CODE_DISPLAY_LENGTH] + "\n// ... (truncated)"
        else:
            result["code_snippet"] = code
    else:
        result["code_available"] = False
        result["code_snippet"] = None
        result["warning"] = "Template code not found in the code store."
    
    return result


# ──────────────────────────────────────────────────────────────────────────────
# TOOL 4: Get component source code (for verifying connection logic)
# ──────────────────────────────────────────────────────────────────────────────

@tool
def get_component_code(component_id: str, runtime: ToolRuntime) -> dict:
    """Fetch the source code for a specific component/step.
    Use this to verify HOW a component works — its inputs, outputs, 
    and channel structure — so you can plan correct data connections.
    
    Args:
        component_id: The exact component ID (e.g. 'step_1PP_trimming__fastp')
    """
    store = runtime.store
    
    # Get metadata
    comp_item = store.get(("components",), component_id)
    if not comp_item:
        return {"error": f"Component '{component_id}' not found"}
    
    comp_data = comp_item.value
    code_item = store.get(("code",), component_id)
    
    result = {
        "id": component_id,
        "tool": comp_data.get("tool"),
        "domain": comp_data.get("domain"),
        "description": comp_data.get("description"),
        "inputs": comp_data.get("input_channels", comp_data.get("input_types", [])),
        "outputs": comp_data.get("output_channels", comp_data.get("out", [])),
        "container": comp_data.get("container"),
    }
    
    if code_item:
        code = code_item.value.get("content", "")
        result["code_available"] = bool(code)
        if len(code) > 3000:
            result["source_code"] = code[:3000] + "\n// ... (truncated)"
        else:
            result["source_code"] = code
    else:
        result["code_available"] = False
        result["source_code"] = "// Source code not available"
        result["warning"] = "Component code not found in the code store."
    
    return result


# ──────────────────────────────────────────────────────────────────────────────
# TOOL 5: Check channel compatibility between two components
# ──────────────────────────────────────────────────────────────────────────────

def _parse_nextflow_channels(code: str) -> dict:
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
        r'\btake\s*:\s*\n(.*?)(?=\bmain\s*:|$)',
        code, re.DOTALL
    )
    if take_match:
        take_block = take_match.group(1)
        # Each non-empty, non-comment line in the take block is a channel name
        for line in take_block.strip().split('\n'):
            line = line.strip()
            if line and not line.startswith('//') and not line.startswith('*'):
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
            if line and not line.startswith('//') and not line.startswith('*') and not line.startswith('}'):
                # Handle "channel_name = expression" and "channel_name" forms
                clean = re.split(r'\s*//', line)[0].strip()
                # Extract the channel name (left side of = or the whole line)
                if '=' in clean:
                    chan_name = clean.split('=')[0].strip()
                else:
                    chan_name = clean.split('.')[0].strip()  # e.g. "trimmed_out.reads" → "trimmed_out"
                if chan_name and not chan_name.startswith('}'):
                    emits.append(chan_name)
    
    return {"takes": takes, "emits": emits, "partial": partial}


def _parse_include_statements(code: str) -> list:
    """Parse 'include { step_xxx } from ...' statements from Nextflow code.
    Returns list of included component IDs.
    """
    includes = []
    if not code:
        return includes
    
    # Match: include { step_xxx } from '...'  or  include { step_xxx; step_yyy } from '...'
    for match in re.finditer(r"include\s*\{([^}]+)\}\s*from", code):
        block = match.group(1)
        for item in block.split(';'):
            item = item.strip()
            # Handle "step_xxx as alias" patterns
            name = item.split(' as ')[0].strip() if ' as ' in item else item.strip()
            # Only keep identifiers that look like step_/module_/multi_ IDs or function names
            if name and re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', name):
                includes.append(name)
    
    return includes


@tool
def check_channel_compatibility(source_component_id: str, target_component_id: str, runtime: ToolRuntime) -> dict:
    """We check if two components can connect. We look at the output channels of the first one and the input channels of the second one. We read the real code to find the exact names.
    
    Args:
        source_component_id: The first component ID
        target_component_id: The second component ID
    """
    store = runtime.store
    warnings = []
    print(f"--- [NODE] CONSULTANT TOOL check channel compatibility between {source_component_id} and {target_component_id}")
    
    # we load the first component
    source_comp = store.get(("components",), source_component_id)
    source_tmpl = store.get(("templates",), source_component_id) if not source_comp else None
    if not source_comp and not source_tmpl:
        print(f"--- [NODE] CONSULTANT ERROR source {source_component_id} not found")
        return {"error": f"Source '{source_component_id}' not found in catalog"}
    
    # we load the second component
    target_comp = store.get(("components",), target_component_id)
    target_tmpl = store.get(("templates",), target_component_id) if not target_comp else None
    if not target_comp and not target_tmpl:
        print(f"--- [NODE] CONSULTANT ERROR target {target_component_id} not found")
        return {"error": f"Target '{target_component_id}' not found in catalog"}
    
    # we get the channel info from the catalog
    if source_comp:
        source_data = source_comp.value
        source_outputs_catalog = source_data.get("output_channels", source_data.get("out", []))
    else:
        source_data = source_tmpl.value
        source_outputs_catalog = source_data.get("output_channels", [])
    
    if target_comp:
        target_data = target_comp.value
        target_inputs_catalog = target_data.get("input_channels", target_data.get("input_types", []))
    else:
        target_data = target_tmpl.value
        target_inputs_catalog = target_data.get("input_channels", [])
    
    # we read the actual code to get the correct channel info
    source_code_item = store.get(("code",), source_component_id)
    target_code_item = store.get(("code",), target_component_id)
    
    source_code = source_code_item.value.get("content", "") if source_code_item else ""
    target_code = target_code_item.value.get("content", "") if target_code_item else ""
    
    source_parsed = _parse_nextflow_channels(source_code)
    target_parsed = _parse_nextflow_channels(target_code)
    
    if source_parsed["partial"]:
        warnings.append(f"Source '{source_component_id}' code may be partial so channel analysis may be incomplete")
    if target_parsed["partial"]:
        warnings.append(f"Target '{target_component_id}' code may be partial so channel analysis may be incomplete")
    if not source_code:
        warnings.append(f"No source code found for '{source_component_id}'. using catalog metadata only")
    if not target_code:
        warnings.append(f"No source code found for '{target_component_id}'. using catalog metadata only")
    
    # we use the channels we found in the code. if we found nothing we use the catalog
    source_emits = source_parsed["emits"] if source_parsed["emits"] else source_outputs_catalog
    target_takes = target_parsed["takes"] if target_parsed["takes"] else target_inputs_catalog
    
    print(f"--- [NODE] CONSULTANT TOOL source emits {source_emits}. target takes {target_takes}")

    # we check if the channel names match exactly
    source_emit_lower = {ch.lower() for ch in source_emits}
    target_take_lower = {ch.lower() for ch in target_takes}
    
    exact_matches = source_emit_lower & target_take_lower
    
    # we do a fuzzy match here. we see if the channel types make sense together
    # for example when a trimmed output connects to a reads input
    READ_COMPATIBLE = {"reads", "rawreads", "trimmed", "fastq", "filtered"}
    ASSEMBLY_COMPATIBLE = {"assembly", "contigs", "scaffolds", "consensus"}
    
    fuzzy_matches = []
    for s_ch in source_emit_lower:
        for t_ch in target_take_lower:
            if s_ch == t_ch:
                continue
            if (s_ch in READ_COMPATIBLE and t_ch in READ_COMPATIBLE) or \
               (s_ch in ASSEMBLY_COMPATIBLE and t_ch in ASSEMBLY_COMPATIBLE):
                fuzzy_matches.append({"source_emit": s_ch, "target_take": t_ch, "reason": "type-compatible"})
                print(f"--- [NODE] CONSULTANT TOOL fuzzy match found for {s_ch} and {t_ch}")
    
    compatible = bool(exact_matches) or bool(fuzzy_matches) or (len(source_emits) > 0 and len(target_takes) > 0 and len(target_takes) == 1)
    
    if not compatible and len(target_takes) == 1:
        warnings.append(f"Target takes a single channel '{target_takes[0]}' so it may accept any upstream output by convention")
        compatible = True
    
    print(f"--- [NODE] CONSULTANT TOOL compatibility result is {compatible}")
    
    return {
        "compatible": compatible,
        "source_id": source_component_id,
        "target_id": target_component_id,
        "source_emits": source_emits,
        "target_takes": target_takes,
        "exact_matches": list(exact_matches),
        "fuzzy_matches": fuzzy_matches,
        "warnings": warnings,
    }


# ──────────────────────────────────────────────────────────────────────────────
# TOOL 6 Validate a complete pipeline plan
# ──────────────────────────────────────────────────────────────────────────────

@tool
def check_plan_logic(component_ids: list, template_id: str, runtime: ToolRuntime) -> dict:
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
    
    print(f"--- [NODE] CONSULTANT TOOL check plan logic for {len(component_ids)} components with template {template_id}")
    
    if not component_ids:
        print("--- [NODE] CONSULTANT TOOL no component ids provided")
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
            
    print(f"--- [NODE] CONSULTANT TOOL found {len(invalid_ids)} invalid ids")
    
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
            src_data = src_comp.value
            src_outputs = src_data.get("output_channels", src_data.get("out", []))
        else:
            src_tmpl = store.get(("templates",), src_id)
            if src_tmpl:
                src_outputs = src_tmpl.value.get("output_channels", [])
        
        if tgt_comp:
            tgt_data = tgt_comp.value
            tgt_inputs = tgt_data.get("input_channels", tgt_data.get("input_types", []))
        else:
            tgt_tmpl = store.get(("templates",), tgt_id)
            if tgt_tmpl:
                tgt_inputs = tgt_tmpl.value.get("input_channels", [])
        
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
        print(f"--- [NODE] CONSULTANT TOOL comparing plan with template {template_id}")
        tmpl_item = store.get(("templates",), template_id)
        if not tmpl_item:
            issues.append(f"Template '{template_id}' not found in catalog")
        else:
            tmpl_data = tmpl_item.value
            tmpl_steps = set(tmpl_data.get("components_used", []))
            plan_steps = set(component_ids)
            
            missing_from_plan = tmpl_steps - plan_steps
            extra_in_plan = plan_steps - tmpl_steps
            
            # we also look at the include lines in the template code
            code_item = store.get(("code",), template_id)
            includes_from_code = []
            if code_item:
                tmpl_code = code_item.value.get("content", "")
                includes_from_code = _parse_include_statements(tmpl_code)
                
                # we only keep the includes that look like steps or modules
                code_steps = {inc for inc in includes_from_code 
                             if inc.startswith(("step_", "module_", "multi_"))}
                
                # steps we found in the code but not in the catalog list
                code_only = code_steps - tmpl_steps
                if code_only:
                    warnings.append(
                        f"Template code includes {list(code_only)} which are not in "
                        f"the template steps_used list. they may be helper dependencies"
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
    print(f"--- [NODE] CONSULTANT TOOL check plan logic finished. valid is {is_valid}")
    
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
        component_id: The exact component ID (e.g. 'step_3TX_species__kmerfinder')
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
    print(f"--- [NODE] CONSULTANT TOOL find_component_usage: {component_id} found in {len(usages)} templates")
    
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
# EXPORT: Tool list for ToolNode registration
# ──────────────────────────────────────────────────────────────────────────────

CONSULTANT_TOOLS = [
    verify_component_id,
    search_components,
    get_template_logic,
    get_component_code,
    check_channel_compatibility,
    check_plan_logic,
    find_component_usage,
]
