from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any

# Import your custom modules
from app.core.loader import data_loader
from app.services.graph import app_graph

# --- 1. DATA MODELS (Request/Response) ---
class PipelineQuery(BaseModel):
    query: str = Field(..., description="The user's bioinformatics request")

class PipelineResponse(BaseModel):
    status: str
    plan: Optional[Dict[str, Any]] = None
    ast_json: Optional[Dict[str, Any]] = None
    nextflow_code: Optional[str] = None
    mermaid_code: Optional[str] = None
    error: Optional[str] = None

# --- 2. LIFESPAN (Startup/Shutdown Logic) ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Executes when the server starts.
    Loads the massive FAISS index and JSON catalogs into memory
    so they are ready for the agents immediately.
    """
    try:
        data_loader.load_all()
    except Exception as e:
        print(f"❌ CRITICAL STARTUP ERROR: {e}")
    yield
    print("🛑 Server shutting down...")

# --- 3. API APP DEFINITION ---
app = FastAPI(
    title="Mistral-Nextflow Agent API",
    version="1.0.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    # For production, put specific website URL: ["https://my-website.com"]
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],    
    allow_headers=["*"],    
)

# --- 4. ENDPOINTS ---

@app.get("/health")
def health_check():
    """Simple check to see if server is alive."""
    return {
        "status": "online", 
        "vector_store": "loaded" if data_loader.vector_store else "not_loaded"
    }

@app.post("/generate", response_model=PipelineResponse)
async def generate_pipeline(request: PipelineQuery):
    """
    Main Endpoint:
    1. Receives user query.
    2. Runs the LangGraph agents (Planner -> Hydrator -> Architect -> Renderer).
    3. Returns the Nextflow code.
    """
    try:
        # Run the graph
        # ainvoke (async) to allow concurrency
        result = await app_graph.ainvoke({"user_query": request.query})
        
        # Check for agent errors
        if result.get("error"):
            return {
                "status": "failed",
                "error": result["error"]
            }

        return {
            "status": "success",
            "plan": result.get("design_plan"),
            "ast_json": result.get("ast_json"),
            "nextflow_code": result.get("nextflow_code"),
            "mermaid_code": result.get("mermaid_code"),
            "error": None
        }

    except Exception as e:
        # Catch unexpected crashes
        print(f"Server Error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))