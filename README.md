# StrideIQ

AI-powered training analytics platform. Ingests Strava workout data and processes it through multiple analysis modules.

## Architecture

```
StrideIQ/
└── ingestion/     ← Strava webhook receiver + R2 storage (this module)
```

## Modules

| Module | Status | Description |
|--------|--------|-------------|
| `ingestion` | ✅ Active | Strava webhook receiver, raw data ingestion to Cloudflare R2 |

## Getting Started

See each module's README for setup instructions.

- [Ingestion service](./ingestion/README.md)
