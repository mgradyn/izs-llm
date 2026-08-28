<!-- OPENWIKI:START -->

## OpenWiki

This repository uses OpenWiki for recurring code documentation. Start with `openwiki/quickstart.md`, then follow its links to architecture, workflows, domain concepts, operations, integrations, testing guidance, and source maps.

The scheduled OpenWiki GitHub Actions workflow refreshes the repository wiki. Do not hand-edit generated OpenWiki pages unless explicitly asked; prefer updating source code/docs and letting OpenWiki regenerate.

<!-- OPENWIKI:END -->

## Mandatory Repository Rule: 100% Plugin-Agnostic Core

The core engine (`core/`), including all prompts (`core/prompts/*.md`), graph nodes (`core/nodes/*.py`), AST generators, models, and state handlers, **MUST ALWAYS REMAIN 100% PLUGIN-AGNOSTIC**.

- **Zero Hardcoded Tools**: Never hardcode tool names or pipeline-specific identifiers in `core/`.
- **Runtime Reflection**: All discovery and wiring is dynamically driven via active plugins (`plugins/<plugin_name>/catalog/components.json`).
- **Domain Abstraction**: All prompts and AST directives use abstract topological concepts.
