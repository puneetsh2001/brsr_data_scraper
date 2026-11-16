"""FastAPI application for the BRSR analytics API and its web frontend.

    uvicorn app.api.main:app --port 8000
    open http://localhost:8000/app/

Reads the database named by BRSR_ANALYTICS_DB_NAME (default: the Phase 14
test database) through read-only sessions.

Frontends (served from the same origin, so no CORS configuration is needed):
  /app/     the React build in frontend/dist (`npm run build` in frontend/);
            falls back to the previous UI when no build exists
  /legacy/  the previous HTML/JS UI (app/frontend/), kept recoverable until
            the React UI is accepted

To call the API from another origin (e.g. the Vite dev server), list the
origins in BRSR_CORS_ORIGINS (comma separated); only GET is allowed.
"""

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.api.analytics_routes import router

ROOT = Path(__file__).resolve().parents[2]
LEGACY_FRONTEND = ROOT / "app" / "frontend"
REACT_BUILD = ROOT / "frontend" / "dist"

app = FastAPI(title="BRSR Analytics API", version="0.2.0",
              description="Company / FY BRSR metrics from the brsr schema "
              "(read-only). Missing values are null, never zero.")
app.include_router(router)

origins = [o.strip() for o in os.getenv("BRSR_CORS_ORIGINS", "").split(",")
           if o.strip()]
if origins:
    app.add_middleware(CORSMiddleware, allow_origins=origins,
                       allow_methods=["GET"], allow_headers=["*"])

if (REACT_BUILD / "index.html").is_file():
    app.mount("/app", StaticFiles(directory=REACT_BUILD, html=True),
              name="frontend")
elif LEGACY_FRONTEND.is_dir():
    app.mount("/app", StaticFiles(directory=LEGACY_FRONTEND, html=True),
              name="frontend")

if LEGACY_FRONTEND.is_dir():
    app.mount("/legacy", StaticFiles(directory=LEGACY_FRONTEND, html=True),
              name="legacy-frontend")


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse("/app/")
