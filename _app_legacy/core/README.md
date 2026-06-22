# `_app_legacy/core/` - [DEPRECATED]

> [!CAUTION]
> **THIS DIRECTORY IS DEPRECATED.** 
> This is a historical snapshot of the V1 core utilities. See `core/loader.py` and `core/config.py` in the root `core/` directory for the active domain-agnostic system.

## 1. Historical Context: V1 Data Loaders

The V1 data loaders attempted to stream catalogs dynamically from disk during execution, rather than pre-caching them in memory during the Uvicorn Lifespan event. 

```mermaid
sequenceDiagram
    participant User
    participant V1_Loader
    participant Disk

    User->>V1_Loader: Request Pipeline
    V1_Loader->>Disk: I/O Read (Bottleneck)
    Disk-->>V1_Loader: Raw JSON
    V1_Loader-->>User: Pipeline
```

This architecture was deprecated due to extreme latency under load. V2 utilizes `InMemoryStore` for zero-latency lookups.
