# Source Code Map

## Repository Structure

```
izs-llm/
├── core/                  # Core system logic
│   ├── api.py              # FastAPI routing
│   ├── config.py           # System configuration
│   ├── loader.py           # Data hydration
│   ├── models/             # Pydantic validation
│   ├── nodes/              # Graph node implementations
│   ├── prompts/            # System prompts
│   ├── services/           # Core services
│   └── utils/              # Utility functions
├── plugins/               # Domain-specific plugins
│   └── <domain>/           # Individual plugin directories
├── tests/                 # Test suites
├── main.py                # API entrypoint
└── Dockerfile             # Container configuration
```

## Key Source Files

### Recent Changes (July 2026)

#### Modified Files
- `core/nodes/diagrammer.py` - Diagram generation refactoring
- `core/nodes/architect.py` - Architect node enhancements
- `core/api.py` - API response filtering
- `core/services/architect_tools.py` - Tool improvements

#### New Files
- Various test files for integration testing

### Core Components

#### API Layer
- **File**: `core/api.py`
- **Purpose**: HTTP routing and request handling
- **Key Functions**:
  - `chat_endpoint()`: Main user interface
  - `health_endpoint()`: System status
  - `filter_internal_messages()`: Response processing

#### Graph Nodes
- **Directory**: `core/nodes/`
- **Key Files**:
  - `architect.py`: Execution subgraph logic
  - `consultant.py`: Planning subgraph logic
  - `diagrammer.py`: Diagram generation
  - `renderer.py`: Code rendering

#### Services
- **Directory**: `core/services/`
- **Key Files**:
  - `graph.py`: State machine definition
  - `architect_tools.py`: Architect toolset
  - `consultant_tools.py`: Consultant toolset
  - `llm.py`: LLM provider management
  - `graph_state.py`: State definitions

#### Models
- **Directory**: `core/models/`
- **Key Files**:
  - `ast_structure.py`: AST validation schemas
  - `diagram_structure.py`: Diagram data models
  - `consultant_structure.py`: Planning structures

### Recent Changes Details

#### Diagrammer Refactoring (8f3e6e2)
**File**: `core/nodes/diagrammer.py`

**Changes**:
- Removed iterative tool-calling loop
- Implemented one-shot prompting
- Eliminated `diagram_messages` state tracking
- Simplified error handling
- Added integration test support

**Before**:
```python
# Old iterative approach
diagram_messages = state.get("diagram_messages", [])
if not diagram_messages:
    # Build messages
    
# Process tool results
# Accumulate message history
```

**After**:
```python
# New one-shot approach
sys_prompt = load_diagram_prompt()
diagram_messages = [
    SystemMessage(content=sys_prompt),
    HumanMessage(content=f"...{plan_context}...{code_context}...")
]

llm = get_llm().with_structured_output(DiagramData)
diagram_data = llm.invoke(diagram_messages)
```

#### Architect Enhancements (bf5e484, 19c52ff)
**Files**: `core/nodes/architect.py`, `core/api.py`, `core/services/architect_tools.py`

**Changes**:
- Added internal agent tagging for message filtering
- Enforced mandatory tool calling
- Enhanced component metadata
- Improved data loader integration
- Better error handling and validation
- Modularized tool execution with dedicated iteration counter
- Enhanced message sanitization for LLM API compatibility

**Key Updates**:
- `architect_reason_node()`: Now requires tool usage
- API response filtering: Hides internal tool messages
- Tool bindings: More robust error handling
- Component lookup: Includes usage examples
- Tool execution: Modularized with iteration counter

### Development Patterns

#### Adding New Features
1. **Domain Tools**: Update plugin catalogs only
2. **Core Logic**: Modify `core/services/` files
3. **Validation**: Update `core/models/` schemas
4. **API**: Extend `core/api.py` endpoints

#### Testing Strategy
- Unit tests: `tests/unit/`
- Integration tests: `tests/integration/`
- Evaluation: `tests/evaluation/`
- Pairwise comparison: `tests/pairwise/`

### Important Notes

#### Diagram Generation
- **Deterministic**: Uses AST parsing for exact representation
- **Probabilistic**: Uses LLM interpretation for readability
- **Recent Change**: One-shot prompting replaces iterative approach

#### Architect Workflow
- **Mandatory Tools**: Architect must use tools for investigations
- **Internal Messages**: Filtered from API responses
- **Validation**: Strict Pydantic enforcement before rendering

#### API Responses
- **Message Filtering**: Internal tool messages suppressed
- **Error Handling**: Enhanced validation and retry logic
- **Structure**: Standardized response format

### Troubleshooting Guide

#### Common Issues
1. **Diagram Generation Fails**:
   - Check `core/nodes/diagrammer.py` one-shot logic
   - Verify AST structure in `core/models/ast_structure.py`
   - Inspect diagram prompt in `core/prompts/diagram.md`

2. **Architect Tool Errors**:
   - Review tool bindings in `core/nodes/architect.py`
   - Check tool definitions in `core/services/architect_tools.py`
   - Validate tool usage in `core/services/architect_tools.py`

3. **API Response Problems**:
   - Examine response filtering in `core/api.py`
   - Check message tagging logic
   - Verify state management in `core/services/graph_state.py`

### Version Information

**Current HEAD**: `8f3e6e2902fce5a115c7bfd2f6018f1c048f637f`

**Recent Commits**:
- `8f3e6e2`: Diagrammer refactoring
- `bf5e484`: Architect tagging and tests
- `19c52ff`: Mandatory tool calling
- `50b49a5`: Tool execution modularization
- `6d9d1e9`: Search tool centralization

### Related Documentation

- [Quickstart Guide](/openwiki/quickstart.md)
- [Architecture Overview](/openwiki/architecture/overview.md)
- [Development Guide](/openwiki/development.md)
- [Testing Reference](/openwiki/testing.md)