# 4-Worker Architecture

This document details the multi-threaded architecture used for processing GST invoices. The system utilizes four distinct worker threads operating in a Producer-Consumer model, synchronized via a central `StateManager`.

## Architecture Diagram

```mermaid
flowchart TD
    %% Central State Manager
    SM{{"State Manager (FileRecord Tracker)"}}
    
    %% Input Source
    Q_DL[/"Input Queue (List of IRNs)"/]
    
    %% The 4 Workers
    subgraph Workers ["Parallel Worker Threads"]
        W_DL["Worker 1: Downloader (Playwright)"]
        W_BK["Worker 2: Backup Thread"]
        
        subgraph Modifiers ["Modifier Pool (PyMuPDF)"]
            W_M1["Worker 3: PDF Modifier 1"]
            W_M2["Worker 4: PDF Modifier 2"]
        end
    end
    
    %% Disk Storage
    subgraph Storage ["Disk Storage (data/)"]
        STG[("staging/ (Temporary)")]
        ORG[("original/ (Backups)")]
        MOD[("modified_invoices/ (Final)")]
    end
    
    %% Workflow & Data Flow
    Q_DL -->|Pulls IRN| W_DL
    
    %% Worker 1 Flow
    W_DL -->|1. Downloads PDF| STG
    W_DL -.->|2. Registers File as STAGED| SM
    
    %% Worker 2 Flow
    SM -.->|3. Allocates STAGED file| W_BK
    W_BK -->|4. Reads from| STG
    W_BK -->|5. Copies to| ORG
    W_BK -.->|6. Updates State to BACKED_UP| SM
    
    %% Worker 3 & 4 Flow
    SM -.->|7. Allocates BACKED_UP file| Modifiers
    Modifiers -->|8. Reads Original PDF| ORG
    Modifiers -->|9. Applies Overlays & Writes| MOD
    Modifiers -->|10. Deletes Temp File| STG
    Modifiers -.->|11. Updates State to COMPLETED| SM
    
    %% Styling
    classDef worker fill:#1f4287,stroke:#071e3d,stroke-width:2px,color:#fff;
    classDef storage fill:#278ea5,stroke:#21e6c1,stroke-width:2px,color:#fff;
    classDef state fill:#900c3f,stroke:#c70039,stroke-width:2px,color:#fff;
    
    class W_DL,W_BK,W_M1,W_M2 worker;
    class STG,ORG,MOD storage;
    class SM state;
```

## Worker Responsibilities

1. **Worker 1 (Downloader)**: An asynchronous Playwright thread that stealthily navigates the GST portal, searches for IRNs, and downloads the raw PDF into the `staging/` directory. Once downloaded, it registers the file in the `StateManager` as `STAGED`.
2. **Worker 2 (Backup Thread)**: Continuously polls the `StateManager` for `STAGED` files. It safely copies the raw PDF from `staging/` to `original/` to prevent data loss. It then updates the state to `BACKED_UP`.
3. **Workers 3 & 4 (PDF Modifiers)**: These CPU-bound PyMuPDF threads listen for `BACKED_UP` files. They read the original PDF, apply the custom overlays/text formatting defined in `pdf_config.yaml`, and save the result to `modified_invoices/`. Finally, they delete the temporary file from `staging/` and mark the task as `COMPLETED`.
