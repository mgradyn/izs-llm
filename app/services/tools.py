import json
import re
from app.core.loader import data_loader
from app.services.graph_state import GraphState

from langgraph.store.base import BaseStore

def _inject_component(comp_id, found_ids, context_blocks, store: BaseStore, embed_code=True):
    if comp_id in found_ids: return
    
    comp_item = store.get(("components",), comp_id)
    if not comp_item: return
    comp_data = comp_item.value

    found_ids.add(comp_id)
    
    code_item = store.get(("code",), comp_id)
    code_snippet = code_item.value.get("content", "// Code not found") if code_item else "// Code not found in repository"

    block = f"""
--- COMPONENT: {comp_id} ---
TOOL: {comp_data.get('tool', 'Unknown')}
DESCRIPTION: {comp_data.get('description', 'No description')}
CONTAINER: {comp_data.get('container', 'None')}
INPUTS: {', '.join(comp_data.get('input_types', []))}
OUTPUTS: {', '.join(comp_data.get('out', []))}
"""

    if embed_code:
        block += f"\n**SOURCE CODE ({comp_id}.nf):**\n```groovy\n{code_snippet}\n```\n"

    context_blocks.append(block)

def _inject_template(template_id, found_ids, context_blocks, store: BaseStore, embed_code=True):
    if template_id in found_ids: return
    
    tmpl_item = store.get(("templates",), template_id)
    if not tmpl_item: return

    found_ids.add(template_id)

    block = ""

    if embed_code:
        code_item = store.get(("code",), template_id)
        code_snippet = code_item.value.get("content", "// Code not found") if code_item else "// Code not found in repository"
        block += f"\n**SOURCE CODE ({template_id}.nf):**\n```groovy\n{code_snippet}\n```\n"

    context_blocks.append(block)

def retrieve_rag_context(user_query, store: BaseStore, embed_code=False):
    """Retrieves context using a Hybrid Approach: Keyword/Metadata Matching + FAISS Semantic Search."""
    if not data_loader.vector_store:
        return "Vector Store not loaded."
    
    found_ids = set()
    context_blocks = []
    query_lower = user_query.lower()
    
    # ==========================================
    # 1. HYBRID KEYWORD & METADATA SEARCH
    # ==========================================
    ignore_words = {
        'step', 'mapping', 'module', 'genes', 'denovo', 'assembly', 'tool', 
        'pipeline', 'workflow', 'build', 'create', 'make', 'run', 'using',
        'file', 'data', 'reads', 'fastq', 'fasta', 'generate', 'process',
        'custom', 'script', 'and', 'plus', 'with'
    }

    # --- SCAN TEMPLATES ---
    try:
        for tmpl in store.search(("templates",)):
            tmpl_id = tmpl.key.lower()
            tmpl_data = tmpl.value
            
            tmpl_name = str(tmpl_data.get('name', tmpl_data.get('template_name', ''))).lower()
            keywords = [str(k).lower() for k in tmpl_data.get('keywords', [])]
            
            match_found = False
            
            clean_id = tmpl_id.replace("module_", "").replace("_", " ")
            if clean_id and clean_id in query_lower:
                match_found = True
                
            for kw in keywords:
                if kw and len(kw) > 3 and kw in query_lower:
                    match_found = True
                    break
                    
            if match_found:
                context_blocks.append(f"### PIPELINE BLUEPRINT (Keyword Match): {tmpl.key}")
                _inject_template(tmpl.key, found_ids, context_blocks, store, embed_code=True)
                
                for flow_step in tmpl_data.get('logic_flow', []):
                    if 'step' in flow_step: 
                        _inject_component(flow_step['step'], found_ids, context_blocks, store, embed_code)
                    for sub_key in ['parallel_execution', 'branches', 'options']:
                        if sub_key in flow_step:
                            for item in flow_step[sub_key]:
                                if 'step' in item: _inject_component(item['step'], found_ids, context_blocks, store, embed_code)
                                if 'next' in item:
                                    for sub_item in item['next']:
                                        if 'step' in sub_item: _inject_component(sub_item['step'], found_ids, context_blocks, store, embed_code)
    except Exception as e:
        print(f"Template hybrid search error: {e}")

    # --- SCAN COMPONENTS ---
    try:
        for comp in store.search(("components",)):
            comp_id = comp.key.lower()
            comp_data = comp.value
            
            tool_name = str(comp_data.get('tool', '')).lower()
            seq_types = [str(s).lower() for s in comp_data.get('compatible_seq_types', [])]
            
            match_found = False
            
            # Check 1: Suffix/ID Name (e.g., step_4TY_lineage__westnile -> westnile)
            if '__' in comp_id:
                suffix = comp_id.split('__')[-1]
                suffix_words = suffix.split('_')
                for sw in suffix_words:
                    if sw and len(sw) > 3 and sw in query_lower and sw not in ignore_words:
                        match_found = True
                        break

            # Check 2: Break down complex tool names (e.g., "Snippy + Custom Script")
            if not match_found and tool_name:
                tool_words = re.split(r'[^a-z0-9]', tool_name)
                for word in tool_words:
                    if len(word) > 3 and word in query_lower and word not in ignore_words:
                        match_found = True
                        break

            # Check 3: Compatible Sequence Types (e.g., "west_nile_virus" -> "west nile virus")
            if not match_found:
                for st in seq_types:
                    clean_st = st.replace('_', ' ')
                    if clean_st and len(clean_st) > 3 and clean_st in query_lower:
                        match_found = True
                        break
                        
            # Check 4: Specific structural words in ID if mentioned (e.g., "lineage")
            if not match_found:
                if "lineage" in query_lower and "lineage" in comp_id:
                    match_found = True
                    
            if match_found:
                _inject_component(comp.key, found_ids, context_blocks, store, embed_code)
    except Exception as e:
        print(f"Component hybrid search error: {e}")


    # ==========================================
    # 2. SEMANTIC SEARCH (FAISS)
    # ==========================================
    docs = data_loader.vector_store.similarity_search(user_query, k=20)
    
    for doc in docs:
        meta = doc.metadata
        item_id = meta.get('id')
        item_type = meta.get('type')

        if item_id in found_ids:
            continue

        if item_type == 'template':
            tmpl_item = store.get(("templates",), item_id)
            if tmpl_item:
                tmpl = tmpl_item.value
                context_blocks.append(f"### PIPELINE BLUEPRINT (Semantic Match): {item_id}\n{doc.page_content}")
                _inject_template(tmpl['id'], found_ids, context_blocks, store, embed_code=True)

                found_ids.add(item_id)

                # Recursive Expansion
                for flow_step in tmpl.get('logic_flow', []):
                    if 'step' in flow_step:
                        _inject_component(flow_step['step'], found_ids, context_blocks, store, embed_code)
                    for sub_key in ['parallel_execution', 'branches', 'options']:
                        if sub_key in flow_step:
                            for item in flow_step[sub_key]:
                                if 'step' in item:
                                    _inject_component(item['step'], found_ids, context_blocks, store, embed_code)
                                if 'next' in item:
                                    for sub_item in item['next']:
                                        if 'step' in sub_item:
                                            _inject_component(sub_item['step'], found_ids, context_blocks, store, embed_code)

        elif item_type == 'component':
            _inject_component(item_id, found_ids, context_blocks, store, embed_code)

    final_context = "\n".join(context_blocks) + "\n\n"

    return final_context