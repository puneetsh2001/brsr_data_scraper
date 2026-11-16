"""BRSR analytics API (read-only). Run: uvicorn app.api.main:app"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from app.analytics import db, service
from app.analytics import repository as repo

router = APIRouter(prefix="/api", tags=["BRSR analytics"])

FY = Query(..., description="financial year slot, e.g. 2024-25")


def get_cursor():
    try:
        with db.cursor() as cur:
            yield cur
    except db.AnalyticsDatabaseRefused as e:
        raise HTTPException(503, str(e))


def call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except service.NotFound as e:
        raise HTTPException(404, str(e))
    except service.BadRequest as e:
        raise HTTPException(400, str(e))


@router.get("/health")
def health(cur=Depends(get_cursor)):
    cur.execute("SELECT current_database() AS database, "
                "current_setting('transaction_read_only') AS read_only")
    return dict(cur.fetchone())


@router.get("/metrics")
def metrics(pillar: Optional[str] = None, theme: Optional[str] = None,
            cur=Depends(get_cursor)):
    cat = service.catalog(cur)
    return {"themes": list(service.THEMES), "pillars": list(service.PILLARS),
            "metrics": call(service.select, cat, pillar, theme)}


@router.get("/companies")
def companies(q: str = Query("", max_length=100),
              limit: int = Query(25, ge=1, le=100),
              offset: int = Query(0, ge=0), cur=Depends(get_cursor)):
    rows = repo.search_companies(cur, q.strip(), limit, offset)
    total = rows[0]["total"] if rows else 0
    return {"query": q, "total": total, "limit": limit, "offset": offset,
            "companies": [{k: v for k, v in r.items() if k != "total"}
                          for r in rows]}


@router.get("/companies/{company_id}")
def company(company_id: str, cur=Depends(get_cursor)):
    c = call(service.company_or_404, cur, company_id)
    return {"company": c, "identifiers": repo.identifiers(
        cur, c["company_id"]), "years": service.years(cur, c)}


@router.get("/companies/{company_id}/years")
def company_years(company_id: str, cur=Depends(get_cursor)):
    c = call(service.company_or_404, cur, company_id)
    return {"company": service.company_ref(c),
            "years": service.years(cur, c)}


@router.get("/companies/{company_id}/years/{fy}/metrics")
def company_year_metrics(company_id: str, fy: str,
                         pillar: Optional[str] = None,
                         theme: Optional[str] = None,
                         metric: Optional[list[str]] = Query(None),
                         include_missing: bool = True,
                         cur=Depends(get_cursor)):
    return call(service.company_year_metrics, cur, company_id, fy, pillar,
                theme, metric, include_missing)


@router.get("/companies/{company_id}/years/{fy}/energy")
def energy(company_id: str, fy: str, cur=Depends(get_cursor)):
    return call(service.company_year_metrics, cur, company_id, fy,
                theme="energy")


@router.get("/companies/{company_id}/years/{fy}/workforce")
def workforce(company_id: str, fy: str, cur=Depends(get_cursor)):
    return call(service.workforce, cur, company_id, fy)


@router.get("/companies/{company_id}/years/{fy}/posh")
def posh(company_id: str, fy: str, cur=Depends(get_cursor)):
    return call(service.company_year_metrics, cur, company_id, fy,
                theme="posh")


@router.get("/companies/{company_id}/years/{fy}/themes/{theme}")
def by_theme(company_id: str, fy: str, theme: str, cur=Depends(get_cursor)):
    return call(service.company_year_metrics, cur, company_id, fy,
                theme=theme)


@router.get("/companies/{company_id}/years/{fy}/pillars/{pillar}")
def by_pillar(company_id: str, fy: str, pillar: str,
              cur=Depends(get_cursor)):
    return call(service.company_year_metrics, cur, company_id, fy,
                pillar=pillar)


@router.get("/companies/{company_id}/years/{fy}/data-quality")
def data_quality(company_id: str, fy: str, cur=Depends(get_cursor)):
    return call(service.data_quality, cur, company_id, fy)


@router.get("/companies/{company_id}/metrics/{requirement_id}/history")
def history(company_id: str, requirement_id: str,
            breakdown: str = Query("", max_length=500),
            cur=Depends(get_cursor)):
    return call(service.history, cur, company_id, requirement_id, breakdown)


@router.get("/compare")
def compare(metric: str, companies: list[str] = Query(..., max_length=20),
            fy: Optional[str] = None,
            breakdown: str = Query("", max_length=500),
            cur=Depends(get_cursor)):
    keys = [k.strip() for c in companies for k in c.split(",") if k.strip()]
    return call(service.compare, cur, keys, metric, fy, breakdown)
