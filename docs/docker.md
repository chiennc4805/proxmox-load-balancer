# Chạy backend và frontend bằng Docker

Yêu cầu Docker Engine và Docker Compose. Từ thư mục gốc của repository:

```bash
docker compose up --build -d
```

Mở `http://localhost:8080`. Nginx phục vụ `frontend/index.html` và chuyển
`/api/*` đến FastAPI trong service `backend`. Kiểm tra API tại
`http://localhost:8080/api/health` (trả về `{"status":"ok"}`).

```bash
docker compose logs -f backend frontend
docker compose down
```

Dockerfile backend dùng build context là thư mục gốc và chỉ copy `backend/` vào image.
Dockerfile frontend dùng build context là `frontend/`. API hiện có health check,
danh sách VM/node/resources, clone VM qua scheduler và cấu hình scheduler lưu trong PostgreSQL.
