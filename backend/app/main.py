from fastapi import FastAPI


app = FastAPI(title="Proxmox Load Balancer")


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
