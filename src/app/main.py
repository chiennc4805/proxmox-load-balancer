from contextlib import asynccontextmanager
import logging
from fastapi import FastAPI

from app.gateway import controller
from app.infrastructure.database.operations import OperationStore
from app.settings import get_settings


@asynccontextmanager
async def lifespan(app):
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s')
    settings = get_settings()
    if settings.database_dsn:
        await OperationStore(settings.database_dsn, settings.cluster_key).migrate()
    yield

app = FastAPI(
    title="Proxmox Gateway",
    version="0.1.0",
    lifespan=lifespan,
)

# Everything under /api2/json/... is handled by one controller.
app.include_router(controller.router, prefix="/api2/json")


@app.get("/health", include_in_schema=False)
async def health() -> dict[str, str]:
    return {"status": "ok"}
