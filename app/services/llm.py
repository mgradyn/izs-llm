import os
from langchain_mistralai import ChatMistralAI
from app.core.config import settings

def get_llm():
    """Returns the configured Mistral LLM instance."""
    return ChatMistralAI(
        model=settings.LLM_MODEL,
        api_key=os.getenv("MISTRAL_API_KEY"),
        temperature=0.1,
        max_tokens=128000, 
        top_p=0.9,
    )