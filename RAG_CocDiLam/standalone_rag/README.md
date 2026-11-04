# RAG administration service

Restored from the local standalone `GitHub/RAG_CocDiLam` project. This snapshot
contains its document ingestion, retrieval, administration API and original UI.
The current ID_Q implementation remains in `rag/` and `ui-intake/`.

`rag.gateway` runs both lifespans and dispatches administration requests to this
service and ID_Q requests to the current implementation. The two applications
retain independent indexes and databases. The Compose administration volume
reuses `dpl-pvan_rag_service_data`, preserving the original knowledge catalog.

Keep intake changes in `rag/`; do not add legacy ID_Q routes to this snapshot.
