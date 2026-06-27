from fastapi.testclient import TestClient
import uuid
import os
from dotenv import load_dotenv

# Load env before importing core
load_dotenv()

from core.api import app

def test_api():
    # TestClient handles the FastAPI lifespan (which loads the vector store)
    with TestClient(app) as client:
        payload = {
            "session_id": str(uuid.uuid4()),
            "message": "I want a SARS-CoV-2 pipeline that uses step_2AS_mapping__ivar to map reads, then uses step_4TY_lineage__pangolin for lineage assignment. I approve, please build the pipeline.",
            "generate_diagrams": True
        }
        
        print("Sending POST request to /chat (this may take a minute as it runs the full agent graph)...")
        response = client.post("/chat", json=payload)
        
        print(f"HTTP Status: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            print("\n--- Response Summary ---")
            print("Status:", data.get("status"))
            
            agent_diag = data.get("mermaid_agent")
            det_diag = data.get("mermaid_deterministic")
            
            print("mermaid_agent:", "NULL" if agent_diag is None else f"POPULATED ({len(agent_diag)} bytes)")
            print("mermaid_deterministic:", "NULL" if det_diag is None else f"POPULATED ({len(det_diag)} bytes)")
            
            if det_diag:
                print("\n--- Preview of Diagram ---")
                print("\n".join(det_diag.split("\n")[:10]) + "\n...")
        else:
            print("Error:", response.text)

if __name__ == "__main__":
    test_api()
