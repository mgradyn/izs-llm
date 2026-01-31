import os
from langchain_mistralai import ChatMistralAI
from app.core.config import settings

def get_llm():
    """Returns the configured Mistral LLM instance."""

    api_key = os.getenv("MISTRAL_API_KEY")
    if not api_key:
        print("❌ CRITICAL ERROR: MISTRAL_API_KEY is missing from environment variables!")
        # We raise an error here so the node catches it and prints the traceback
        raise ValueError("MISTRAL_API_KEY is not set.")
    
    return ChatMistralAI(
        model=settings.LLM_MODEL,
        api_key=api_key,
        temperature=0.1,
        max_tokens=128000, 
        top_p=0.9,
    )