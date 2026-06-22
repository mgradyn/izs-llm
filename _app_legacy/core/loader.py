import os
import json
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_community.vectorstores import FAISS
from app.core.config import settings

class DataLoader:
    def __init__(self):
        self.vector_store = None
        self.code_db = {}
        self.comp_db = {}
        self.tmpl_db = {}
        self.res_list = []

    def load_all(self, store=None):
        print("Loading Resources...")
        self._load_lookups(store)
        self._load_vector_store()
        print("✅ Resources Loaded.")

    def _load_lookups(self, store=None):
        # Load Code Store
        if os.path.exists(settings.CODE_STORE):
            with open(settings.CODE_STORE, 'r') as f:
                for line in f:
                    try:
                        entry = json.loads(line)
                        if entry.get('id'): 
                            self.code_db[entry['id']] = entry['content']
                            if store: store.put(("code",), entry['id'], {"content": entry['content']})
                    except Exception: continue

        # Load Catalogs
        if os.path.exists(settings.CATALOG_COMPONENTS):
            with open(settings.CATALOG_COMPONENTS, 'r') as f:
                self.comp_db = {c['id']: c for c in json.load(f).get('components', [])}
                if store:
                    for k,v in self.comp_db.items(): store.put(("components",), k, v)
                
        if os.path.exists(settings.CATALOG_TEMPLATES):
            with open(settings.CATALOG_TEMPLATES, 'r') as f:
                self.tmpl_db = {c['id']: c for c in json.load(f).get('templates', [])}
                if store:
                    for k,v in self.tmpl_db.items(): store.put(("templates",), k, v)
               
        if os.path.exists(settings.CATALOG_RESOURCES):
            with open(settings.CATALOG_RESOURCES, 'r') as f:
                raw_resources = json.load(f).get('resources', {})
                self.res_list = raw_resources.get('helper_functions', [])
                self.containers_list = raw_resources.get('containers', [])
                if store:
                    store.put(("resources",), "helper_functions", {"list": self.res_list})
                    store.put(("resources",), "containers", {"list": self.containers_list})

        # Build reverse index: component_id → list of templates that use it
        if store:
            self._build_usage_index(store)

    def _build_usage_index(self, store):
        """Build reverse index: for each component, find all templates that
        include it and extract the relevant code snippet showing how it's wired.
        Stored under ("usage", component_id) in the InMemoryStore.
        """
        import re
        
        # Collect: component_id → [{template_id, snippet}]
        usage_map = {}
        
        for tmpl_id, tmpl_meta in self.tmpl_db.items():
            tmpl_code = self.code_db.get(tmpl_id, "")
            if not tmpl_code:
                continue
            
            # Get steps from catalog + parse include statements from code
            catalog_steps = set(tmpl_meta.get("components_used", []))
            code_includes = set()
            for match in re.finditer(r"include\s*\{([^}]+)\}\s*from", tmpl_code):
                block = match.group(1)
                for item in block.split(';'):
                    name = item.strip().split(' as ')[0].strip() if ' as ' in item else item.strip()
                    if name and re.match(r'^(step_|multi_|module_)', name):
                        code_includes.add(name)
            
            all_steps = catalog_steps | code_includes
            
            for comp_id in all_steps:
                # Extract the snippet showing how this component is called
                snippet = self._extract_usage_snippet(tmpl_code, comp_id)
                
                if comp_id not in usage_map:
                    usage_map[comp_id] = []
                usage_map[comp_id].append({
                    "template_id": tmpl_id,
                    "template_description": tmpl_meta.get("description", "")[:200],
                    "snippet": snippet,
                })
        
        # Store in the InMemoryStore
        for comp_id, usages in usage_map.items():
            store.put(("usage",), comp_id, {"usages": usages})
        
        print(f"✅ Usage index: {len(usage_map)} components mapped to templates")
    
    def _extract_usage_snippet(self, template_code: str, component_id: str) -> str:
        """Extract lines around a component's usage in template code.
        Returns the calling context (a few lines before/after) so the agent
        can see how the component is wired (what channels go in/out).
        """
        lines = template_code.split('\n')
        hit_lines = []
        for i, line in enumerate(lines):
            if component_id in line and 'include' not in line.lower():
                hit_lines.append(i)
        
        if not hit_lines:
            return "(component included but call site not found in code)"
        
        # Gather context: 2 lines before and 2 lines after each hit
        context_indices = set()
        for h in hit_lines:
            for j in range(max(0, h - 2), min(len(lines), h + 3)):
                context_indices.add(j)
        
        snippet_lines = [lines[i] for i in sorted(context_indices)]
        return '\n'.join(snippet_lines).strip()

    def _load_vector_store(self):
        print(f"Loading Embeddings {settings.EMBEDDING_MODEL} (CPU)...")
        embeddings = HuggingFaceEmbeddings(
            model_name=settings.EMBEDDING_MODEL,
            model_kwargs={'device': 'cpu', 'trust_remote_code': True},
            encode_kwargs={'normalize_embeddings': True, 'batch_size': 4}
        )
        try:
            self.vector_store = FAISS.load_local(
                settings.FAISS_INDEX_PATH, 
                embeddings, 
                allow_dangerous_deserialization=True
            )
        except Exception as e:
            print(f"⚠️ Vector Store Error: {e}")

# Global Instance
data_loader = DataLoader()