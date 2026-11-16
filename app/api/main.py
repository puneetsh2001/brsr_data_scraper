"""FastAPI application for the BRSR analytics API and its React frontend.

    uvicorn app.api.main:app --port 8000
    open http://localhost:8000/app/

Reads the database named by BRSR_ANALYTICS_DB_NAME through read-only
sessions.

The React build (frontend/dist, `npm run build` in frontend/) is served at
/app/. React Router uses browser history with the basename /app, so every
/app/... path that is not a file of the build returns index.html: direct
links, bookmarks and page refreshes open the right screen. Missing build
assets (/app/assets/...) return 404 instead of the page.

To call the API from another origin (e.g. the Vite dev server), list the
origins in BRSR_CORS_ORIGINS (comma separated); only GET is allowed.
"""

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse

from app.api.analytics_routes import router

ROOT = Path(__file__).resolve().parents[2]
REACT_BUILD = ROOT / "frontend" / "dist"

app = FastAPI(title="BRSR Analytics API", version="0.3.0",
              description="Company / FY BRSR metrics from the brsr schema "
              "(read-only). Missing values are null, never zero.")
app.include_router(router)

origins = [o.strip() for o in os.getenv("BRSR_CORS_ORIGINS", "").split(",")
           if o.strip()]
if origins:
    app.add_middleware(CORSMiddleware, allow_origins=origins,
                       allow_methods=["GET"], allow_headers=["*"])


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse("/app/")


@app.get("/app", include_in_schema=False)
def app_root():
    return RedirectResponse("/app/")


@app.get("/app/{path:path}", include_in_schema=False)
def frontend(path: str):
    build = REACT_BUILD.resolve()
    index = build / "index.html"
    if not index.is_file():
        raise HTTPException(503, "frontend build missing: run `npm run build` in frontend/")
    if path:
        target = (build / path).resolve()
        if target.is_file() and build in target.parents:
            immutable = target.parent == build / "assets"   # content-hashed names
            return FileResponse(target, headers={"Cache-Control":
                                "public, max-age=31536000, immutable" if immutable else "no-cache"})
        if path.startswith("assets/") or "." in path.rsplit("/", 1)[-1]:
            raise HTTPException(404, "not found")
    # client-side route: the React app resolves it (and shows its own 404)
    return FileResponse(index, headers={"Cache-Control": "no-cache"})
