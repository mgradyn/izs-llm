# izs-llm Quickstart Guide

## Overview

The izs-llm framework is a domain-agnostic hybrid system that integrates FastAPI, LangGraph, Pydantic Guardrails, and Advanced LLMs to generate Nextflow DSL2 pipelines. It's designed as a two-stage autonomous agent with a Planner Subgraph (Consultant) and Execution Subgraph (Architect).

## Key Features

- **Two-Stage Architecture**: Consultant (planning) → Architect (execution)
- **Domain Agnostic**: Works with any domain via plugin system
- **Pydantic AST Enforcement**: Generates valid Nextflow DSL2 via JSON AST validation
- **Dual Diagram Generation**: Both deterministic (AST-based) and probabilistic (LLM-based) visualization
- **Modular Design**: Swappable LLM providers, plugin-based domain data

## Recent Changes (July 2026)

### Diagrammer Refactoring (8f3e6e2)
- **Updated diagrammer logic** to use one-shot prompting instead of iterative tool calling
- **Removed message history accumulation** in diagram generation
- **Simplified state management** by eliminating `diagram_messages` tracking
- **Added integration tests** for tools and LangGraph state

### Architect Improvements (bf5e484, 19c52ff)
- **Added internal agent tagging** to suppress architect tool messages in API responses
- **Enforced mandatory tool calling** in architect node
- **Enhanced component channel metadata** with usage examples
- **Improved data loader integration**

### API Enhancements
- **Better response filtering** to hide internal tool messages from clients
- **Improved error handling** in architect workflows

### Additional Updates
- **Modularized tool execution** with dedicated iteration counter
- **Enhanced message sanitization** for LLM API compatibility
- **Centralized search tools** for improved component validation
- **Updated system prompts** to clarify tool usage requirements

## Getting Started

### Prerequisites
- Python 3.9+
- Docker (for containerized deployment)
- OpenAI API key (or other supported LLM provider)

### Installation

```bash
# Clone the repository
git clone https://github.com/mgradyn/izs-llm.git
cd izs-llm

# Set up virtual environment
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Configure environment
export OPENAI_API_KEY="your_key"
export NF_AGENT_PLUGIN="izs"

# Run the API
uvicorn main:app --reload --port 8080
```

### Docker Deployment

```bash
# Build the image
docker build -t izs-llm:latest .

# Run with Docker Compose
docker-compose up -d
```

## Architecture Overview

The system follows a tri-state event horizon:

1. **Boot Phase**: Data loading and vector DB hydration
2. **Request Phase**: User input processing and semantic routing  
3. **Execution Phase**: Pipeline generation and validation

### Core Components

- **FastAPI Entry**: `/chat` endpoint for user requests
- **LangGraph State Machine**: Consultant → Architect workflow
- **Vector DB**: Semantic search for component matching
- **Pydantic Validators**: AST validation for Nextflow DSL2
- **Jinja2 Renderer**: AST to code generation

## Development Workflow

### Adding New Domain Tools
1. Update plugin catalogs in `plugins/<domain>/catalog/`
2. Add component implementations to `plugins/<domain>/code_store.jsonl`
3. No core Python code changes required

### Fixing LLM Syntax Errors
1. Update validation schemas in `core/models/ast_structure.py`
2. Add test cases to `tests/` directory

### Modifying Pipeline Logic
1. Update graph definitions in `core/services/graph.py`
2. Modify node implementations in `core/nodes/`
3. Add validation steps as needed

## Key Files

- `main.py`: API entrypoint
- `core/api.py`: HTTP routing
- `core/nodes/`: Graph node implementations
- `core/services/graph.py`: State machine definition
- `core/models/ast_structure.py`: Pydantic validation
- `plugins/`: Domain-specific data

## Testing

The system includes comprehensive pairwise Glicko-2 evaluation tests. Run with:

```bash
pytest tests/
```

## Troubleshooting

- **Diagram generation issues**: Check `core/nodes/diagrammer.py` for one-shot prompting logic
- **Architect tool errors**: Verify tool bindings in `core/nodes/architect.py`
- **API response problems**: Inspect response filtering in `core/api.py`

## Next Steps

- [Architecture Deep Dive](/openwiki/architecture/overview.md)
- [Plugin System Documentation](/openwiki/plugins.md)
- [API Reference](/openwiki/api.md)
- [Testing Guide](/openwiki/testing.md)