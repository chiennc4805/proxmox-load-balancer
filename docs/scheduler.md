# Scheduler round robin

`POST /api/vms/clone` lấy danh sách node online từ Proxmox, lọc theo `eligible_nodes`,
chọn node bằng `round_robin`, rồi gọi adapter clone vào node đó. Response có thêm
`selected_node` và `algorithm`. Endpoint `/api/vms/clone-to-node` vẫn dùng để thử
adapter với node chỉ định, không cập nhật lượt round robin.

## Lưu trạng thái

Compose chạy PostgreSQL nội bộ, không mở cổng ra host. Bảng `scheduler_state` có
một dòng lưu `algorithm`, `eligible_nodes`, `last_node`. Scheduler dùng
`SELECT ... FOR UPDATE` để chọn và ghi `last_node` trong cùng transaction; nhiều
request cùng lúc sẽ nhận các lượt kế tiếp. Dữ liệu nằm trong volume
`scheduler_db`, nên vẫn còn sau khi restart container. Đây là bảng thông thường
trong DB thử nghiệm; SQL `TEMP TABLE` không phù hợp vì mất dữ liệu sau khi đóng
connection. Clone thất bại sau lúc chọn node vẫn tính một lượt.

## Cấu hình và chạy

Thêm vào `.env` trên máy LB:

```dotenv
POSTGRES_PASSWORD=<mat-khau-db-manh>
ADMIN_API_KEY=<chuoi-bi-mat-dai>
SCHEDULER_NODES=pve1
```

`SCHEDULER_NODES` chỉ áp dụng khi tạo dòng cấu hình lần đầu. Sau đó, admin thay
đổi qua giao diện hoặc API. Trong lab theo `setup-promox.md`, mặc định chỉ có
`pve1` đủ bridge guest; cần cấu hình storage chứa template, bridge/NAT/DHCP và
dung lượng trên `pve2`, `pve3` trước khi thêm chúng. Scheduler hiện chỉ kiểm tra
node online; nó chưa kiểm tra storage, RAM, CPU hoặc bridge.

Chạy bản mới:

```bash
docker compose up -d --build
docker compose ps
curl http://127.0.0.1:8080/api/health
```

Cấu hình qua API (thay `YOUR_ADMIN_API_KEY` bằng giá trị trong `.env`):

```bash
curl -H 'X-Admin-Key: YOUR_ADMIN_API_KEY' \
  http://127.0.0.1:8080/api/admin/scheduler

curl -X PUT http://127.0.0.1:8080/api/admin/scheduler \
  -H 'Content-Type: application/json' \
  -H 'X-Admin-Key: YOUR_ADMIN_API_KEY' \
  -d '{"algorithm":"round_robin","eligible_nodes":["pve1","pve2","pve3"]}'
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
{"vmid": 101, "node": "pve2", "ip_address": "192.168.1.20", "selected_node": "pve2", "algorithm": "round_robin"}
```

Khi clone khác node nguồn, Proxmox yêu cầu template ở shared storage; linked
clone còn phụ thuộc loại storage. Chọn `full_clone=true` nếu storage không hỗ trợ
linked clone giữa các node. DB không thay thế việc kiểm tra dung lượng NFS trước
khi clone.
