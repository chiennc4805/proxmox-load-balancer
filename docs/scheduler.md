# Scheduler round robin

`POST /api/vms/clone` đọc `/cluster/resources?type=node` qua adapter, lấy mọi node
online, chọn bằng `round_robin`, rồi clone vào node đó. Nếu bước clone thất bại
và VM chưa xuất hiện trên cluster, scheduler thử node kế tiếp đúng thứ tự vòng.
Mỗi node được thử tối đa một lần trong request. Nếu clone đã tạo VM, hoặc lỗi
phát sinh sau clone khi khởi động/đọc IP, scheduler dừng để tránh tạo VM trùng.
Response có `selected_node`, `algorithm` và `attempted_nodes`. Endpoint
`/api/vms/clone-to-node` vẫn dùng để thử adapter với node chỉ định.

`GET /api/nodes` trả về resource của từng node. `GET /api/resources?type=storage`
đọc resource Proxmox trực tiếp; `type` hỗ trợ `vm`, `node`, `storage` hoặc để trống.

## Lưu trạng thái

Compose chạy PostgreSQL nội bộ, không mở cổng ra host. Bảng `scheduler_state` có
một dòng lưu `algorithm`, `last_node`. Scheduler dùng
`SELECT ... FOR UPDATE` để chọn và ghi `last_node` trong cùng transaction; nhiều
request cùng lúc sẽ nhận các lượt kế tiếp. Dữ liệu nằm trong volume
`scheduler_db`, nên vẫn còn sau khi restart container. Đây là bảng thông thường
trong DB thử nghiệm; SQL `TEMP TABLE` không phù hợp vì mất dữ liệu sau khi đóng
connection. Mỗi lần thử clone, kể cả thất bại, vẫn tính một lượt.

## Cấu hình và chạy

Thêm vào `.env` trên máy LB:

```dotenv
POSTGRES_PASSWORD=<mat-khau-db-manh>
ADMIN_API_KEY=<chuoi-bi-mat-dai>
```

Biến `SCHEDULER_NODES` cũ không còn được dùng. Khi khởi động với database cũ,
ứng dụng bỏ cột allowlist cũ nhưng giữ `algorithm` và `last_node`. Admin vẫn có
thể đổi thuật toán qua giao diện hoặc API. Trong lab theo `setup-promox.md`,
cần cấu hình storage chứa template, bridge/NAT/DHCP và dung lượng trên `pve2`,
`pve3` trước khi dùng round robin cho cả cụm. Scheduler hiện chỉ kiểm tra node
online; nó chưa kiểm tra storage, RAM, CPU hoặc bridge trước khi thử clone.

Chạy bản mới:

```bash
docker compose up -d --build
docker compose ps
curl http://127.0.0.1:8080/api/health
curl http://127.0.0.1:8080/api/nodes
```

Cấu hình qua API (thay `YOUR_ADMIN_API_KEY` bằng giá trị trong `.env`):

```bash
curl -H 'X-Admin-Key: YOUR_ADMIN_API_KEY' \
  http://127.0.0.1:8080/api/admin/scheduler

curl -X PUT http://127.0.0.1:8080/api/admin/scheduler \
  -H 'Content-Type: application/json' \
  -H 'X-Admin-Key: YOUR_ADMIN_API_KEY' \
  -d '{"algorithm":"round_robin"}'
```

Trên giao diện, nhập admin key vào mục **Cấu hình scheduler**, bấm **Đọc cấu
hình** hoặc **Lưu cấu hình**. Key chỉ ở ô nhập trong trang, không được lưu vào
localStorage. Nếu truy cập UI qua Internet, dùng HTTPS hoặc IAP tunnel để key
không đi dưới dạng HTTP thuần. API clone hiện tại vẫn theo cơ chế truy cập cũ.

Ví dụ clone:

```bash
curl -X POST http://127.0.0.1:8080/api/vms/clone \
  -H 'Content-Type: application/json' \
  -d '{"template_vmid":9000,"full_clone":true}'
```

Response sau khi clone, khởi động và đọc IP thành công:

```json
{"vmid": 101, "node": "pve2", "ip_address": "192.168.1.20", "selected_node": "pve2", "algorithm": "round_robin", "attempted_nodes": ["pve1", "pve2"]}
```

Khi clone khác node nguồn, Proxmox yêu cầu template ở shared storage; linked
clone còn phụ thuộc loại storage. Chọn `full_clone=true` nếu storage không hỗ trợ
linked clone giữa các node. DB không thay thế việc kiểm tra dung lượng NFS trước
khi clone.
