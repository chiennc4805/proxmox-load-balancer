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

Dockerfile backend dùng build context là thư mục gốc để chứa cả `backend/`,
`core/` và `config.py`. Dockerfile frontend dùng build context là `frontend/`.
Hiện API chỉ có endpoint kiểm tra trạng thái; phần xử lý job, Kafka và
PostgreSQL chưa được kết nối.
