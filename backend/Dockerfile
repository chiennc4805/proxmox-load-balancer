FROM python:3.12-slim AS rust-builder

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential cargo rustc \
    && rm -rf /var/lib/apt/lists/*

ENV CARGO_BUILD_JOBS=1

RUN pip install --no-cache-dir maturin==1.8.3

COPY backend/app/scheduler /build/scheduler
WORKDIR /build/scheduler
RUN cargo test -p proxmox-resource-scheduling
RUN maturin build --release \
    --manifest-path python-bindings/Cargo.toml \
    --interpreter python3 \
    --out /wheels


FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY backend/app/requirements.txt /app/requirements.txt
COPY --from=rust-builder /wheels /wheels
RUN pip install --no-cache-dir -r /app/requirements.txt /wheels/*.whl \
    && rm -rf /wheels

COPY backend /app/backend

RUN useradd --create-home --uid 10001 appuser
USER appuser

EXPOSE 8000
CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
