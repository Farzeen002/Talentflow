"""
app/api/__init__.py

API package.
Import and register routers here as feature modules are added.

Example::

    from app.api.v1 import candidates, jobs
    from fastapi import APIRouter

    api_router = APIRouter(prefix="/api/v1")
    api_router.include_router(candidates.router, prefix="/candidates", tags=["Candidates"])
    api_router.include_router(jobs.router, prefix="/jobs", tags=["Jobs"])
"""
