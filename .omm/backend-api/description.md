# Backend API Architecture

The project has **two distinct HTTP API surfaces**:

## Service A: Private Pilot (Primary)
- **Location**: `ai_test_asset_center/private_pilot_service.py` (6700 lines)
- **Framework**: Python stdlib `http.server.ThreadingHTTPServer`
- **Port**: 8088 (default)
- **Routes**: Manual URL dispatch in `do_GET()` / `do_POST()`
- **Key endpoints**: `/api/scan/run`, `/api/knowledge/ingest`, `/api/findings`, `/api/health`

## Service B: FastAPI Gateway (Legacy)
- **Location**: `backend/main.py` (331 lines)
- **Framework**: FastAPI
- **Purpose**: Enterprise API gateway with access-controlled CRUD
- **Key endpoints**: `/v1/source-assets/*`, `/v1/scans`, `/v1/evidence-bundles/*`

## Authentication
- Three modes: static token, opaque token policy, JWT identity policy
- JWT: HMAC-SHA256, `QUALIBUG_JWT_SECRET` required
- Fallback: legacy `QUALIBUG_API_TOKEN` single-token mode

## Patch Architecture
Before the Private Pilot starts, ~15+ runtime patches are applied:
- Customer delivery gate
- Scan campaign context bridge
- Credential safety guards
- Regression oracle/suite/visibility
- System behavior space
- Coverage matrix/steering
- Browser UI smoke
- No-fix-advice stripping
