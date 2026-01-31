import os

class Settings:
    # Base Paths
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    DATA_DIR = os.path.join(BASE_DIR, "data")
    
    # Files
    FAISS_INDEX_PATH = os.path.join(DATA_DIR, "faiss_index")
    CODE_STORE = os.path.join(DATA_DIR, "code_store_hollow.jsonl")
    CATALOG_COMPONENTS = os.path.join(DATA_DIR, "catalog/catalog_part1_components.json")
    CATALOG_TEMPLATES = os.path.join(DATA_DIR, "catalog/catalog_part2_templates.json")
    CATALOG_RESOURCES = os.path.join(DATA_DIR, "catalog/catalog_part3_resources.json")
    
    # Model Config
    EMBEDDING_MODEL = "Qwen/Qwen3-Embedding-0.6B"
    LLM_MODEL = "labs-devstral-small-2512"

settings = Settings()