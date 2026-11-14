from fastapi import APIRouter, Depends

from app.api.dependencies import enforce_global_rate_limit
from app.api.routes import account, auth, cv, jobs, matching

api_router = APIRouter(dependencies=[Depends(enforce_global_rate_limit)])
api_router.include_router(auth.router)
api_router.include_router(account.router)
api_router.include_router(cv.router)
api_router.include_router(jobs.router)
api_router.include_router(matching.router)
