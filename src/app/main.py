from fastapi import FastAPI

from app.gateway import controller

app = FastAPI(
    title="Proxmox Gateway",
    version="0.1.0",
)

# Everything under /api2/json/... is handled by one controller.
app.include_router(controller.router, prefix="/api2/json")


@app.get("/health", include_in_schema=False)
async def health() -> dict[str, str]:
    return {"status": "ok"}
