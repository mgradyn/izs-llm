from core.catalog_registry import CatalogRegistry

def init_from_plugin_patched(self, plugin):
    # Load void tools from plugin config
    self._void_suffixes = plugin.void_tool_suffixes
    self._void_exact = {n.lower() for n in plugin.void_tool_exact_names}
    
    # Load void tool exceptions (e.g., vdabricate is NOT void despite __abricate suffix)
    self._void_exceptions = plugin.void_tool_exceptions

    # Load valid components from framework dir (if available)
    self._valid_components = plugin.load_framework_components()

    # Also load component names from catalog
    if plugin.catalog_components_path:
        self._load_catalog_components(plugin.catalog_components_path)
        
    if plugin.catalog_templates_path:
        self._load_catalog_components(plugin.catalog_templates_path)

    # Build import path mappings (from catalog or plugin prefix)
    self._build_import_paths(plugin)

    self._initialized = True
