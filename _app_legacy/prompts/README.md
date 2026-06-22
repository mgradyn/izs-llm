# `_app_legacy/prompts/` - [DEPRECATED]

> [!CAUTION]
> **THIS DIRECTORY IS DEPRECATED.** 
> These are the V1 static prompts. They did not support dynamic plugin injection and suffered from monolithic design, leading to severe hallucination rates.

## 1. Historical Architecture (V1)

```mermaid
flowchart LR
    MegaPrompt[Monolithic System Prompt] --> LLM[V1 Mega Agent]
    Note right of MegaPrompt: Contained both Planning AND Coding instructions.
    Note right of MegaPrompt: Consumed 60% of context window.
```

In the modern architecture (`core/prompts/`), planning and coding prompts are bifurcated into separate subgraphs, utilizing dynamic plugins for domain-agnostic execution.
