# Scheduler round robin

`POST /api/vms/clone` đọc danh sách node online qua adapter, tạo một `job_state`, chọn node bằng `round_robin`, rồi clone VM vào node đó. Nếu bước clone thất bại và VM chưa xuất hiện trên cluster, controller thử node kế tiếp theo đúng thứ tự vòng. Mỗi node được thử tối đa một lần trong một request.

Nếu clone đã tạo VM, hoặc lỗi phát sinh sau clone khi khởi động/đọc IP, flow dừng để tránh tạo VM trùng. Response thành công giữ các field cũ và thêm `job_id`, `status`:

```json
{"job_id":"...","status":"succeeded","vmid":101,"node":"pve2","ip_address":"192.168.1.20","selected_node":"pve2","algorithm":"round_robin","attempted_nodes":["pve1","pve2"]}
```

`/api/vms/clone-to-node` vẫn dùng để thử adapter với node chỉ định, nhưng cũng ghi `job_state` để dễ debug.

`GET /api/nodes` trả về resource của từng node. `GET /api/resources?type=storage` đọc resource Proxmox trực tiếp; `type` hỗ trợ `vm`, `node`, `storage` hoặc để trống.

## Lưu trạng thái

Compose chạy PostgreSQL nội bộ, không mở cổng ra host. Trạng thái request clone nằm trong bảng `job_state`:

```sql
id UUID PRIMARY KEY
job_type TEXT NOT NULL
status TEXT NOT NULL
request_payload JSONB NOT NULL
result_payload JSONB
error_payload JSONB
algorithm TEXT
selected_node TEXT
selected_at TIMESTAMPTZ
attempted_nodes JSONB NOT NULL DEFAULT '[]'::jsonb
resource_type TEXT
resource_id TEXT
created_at TIMESTAMPTZ
started_at TIMESTAMPTZ
finished_at TIMESTAMPTZ
updated_at TIMESTAMPTZ
```

Round robin không dùng bảng `scheduler_state` để lưu cursor nữa. Cursor được suy ra từ `job_state`: job `vm.clone` gần nhất có `algorithm = 'round_robin'` và `selected_node IS NOT NULL`, sắp xếp theo `selected_at DESC`.

Để tránh hai request đồng thời cùng đọc một cursor, quá trình reserve node chạy trong transaction có PostgreSQL advisory lock:

```sql
SELECT pg_advisory_xact_lock(hashtext('placement:vm.clone:round_robin'));
```

Trong cùng transaction đó, hệ thống đọc `selected_node` gần nhất, gọi scheduler để chọn node kế tiếp, rồi ghi ngay `selected_node`, `selected_at`, `attempted_nodes` cho job hiện tại. Vì vậy node được tính là đã reserve ngay khi được chọn, không phải chờ clone thành công.

Cấu hình thuật toán hiện tại nằm trong bảng `scheduler_config` với một dòng `algorithm`. Đây là cấu hình admin, không phải cursor/state của scheduler. Khi chạy trên DB cũ, app copy `algorithm` từ `scheduler_state` nếu bảng cũ còn tồn tại, nhưng không dùng `last_node` cũ nữa.

## Trách nhiệm component

- `main.py`: đăng ký FastAPI endpoints và map exception sang HTTP response.
- `controllers/vm_controller.py`: điều phối use case clone sync, tạo/cập nhật job, gọi scheduler và adapter.
- `jobs/store.py`: persistence cho `job_state`, `scheduler_config`, advisory lock và job lookup.
- `scheduler/scheduler.py`: filter candidate rồi gọi algorithm; không đọc/ghi DB.
- `scheduler/algorithms.py`: chứa logic chọn node của từng thuật toán.
- `proxmox_adapter.py`: nói chuyện với Proxmox API.

## Cấu hình và chạy

Thêm vào `.env` trên máy LB:

```dotenv
POSTGRES_PASSWORD=<mat-khau-db-manh>
ADMIN_API_KEY=<chuoi-bi-mat-dai>
```

Biến `SCHEDULER_NODES` cũ không còn được dùng. Trong lab theo `setup-promox.md`, cần cấu hình storage chứa template, bridge/NAT/DHCP và dung lượng trên `pve2`, `pve3` trước khi dùng round robin cho cả cụm. Scheduler hiện chỉ kiểm tra node online; nó chưa kiểm tra storage, RAM, CPU hoặc bridge trước khi thử clone.

Chạy bản mới:

```bash
docker compose up -d --build
docker compose ps
curl http://127.0.0.1:8080/api/health
curl http://127.0.0.1:8080/api/nodes
```

Cấu hình qua API:

```bash
curl -H 'X-Admin-Key: YOUR_ADMIN_API_KEY' \
  http://127.0.0.1:8080/api/admin/scheduler

curl -X PUT http://127.0.0.1:8080/api/admin/scheduler \
  -H 'Content-Type: application/json' \
  -H 'X-Admin-Key: YOUR_ADMIN_API_KEY' \
  -d '{"algorithm":"round_robin"}'
```

Ví dụ clone:

```bash
curl -X POST http://127.0.0.1:8080/api/vms/clone \
  -H 'Content-Type: application/json' \
  -d '{"template_vmid":9000,"full_clone":true}'
```

Xem job đã ghi:

```bash
curl http://127.0.0.1:8080/api/jobs/<job_id>
```