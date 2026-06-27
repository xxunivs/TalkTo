# Architecture

## Request flow

```text
TalkTo App -> TalkTo Backend -> TalkTo APP AI
                              -> Backend database/storage
```

The backend authenticates users, checks consent and entitlements, owns recording storage, retrieves authorized memories, orchestrates analysis jobs, and persists results. APP AI performs stateless AI processing and returns typed results.

## Staged recording analysis

The current backend assigns database UUIDs while persisting each stage. The intended integration is therefore staged:

1. APP AI returns transcript segments.
2. The backend persists them and assigns transcript segment IDs.
3. APP AI receives those IDs and returns memory segments with provenance.
4. The backend persists memory segments and assigns their IDs.
5. APP AI receives memory segment IDs and returns embeddings.
6. The backend stores embeddings and later enrichment artifacts.

This avoids APP AI owning backend identifiers or database access.

## Contract policy

New API JSON uses camelCase to match current backend worker transition DTOs. Python code uses snake_case internally through Pydantic aliases. Any future legacy `/ai/*` compatibility routes must preserve their existing contract separately.
