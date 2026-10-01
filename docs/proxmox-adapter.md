# Thử Proxmox adapter

1. Điền `.env` ở thư mục gốc theo `.env.example`. Đặt `PVE_API_HOST` thành IP
   hoặc hostname của node Proxmox mà máy chạy Docker truy cập được, ví dụ
   `10.10.0.11` hoặc `10.10.0.11:8006`. Không thêm `https://`. Điền token thật
   vào `PVE_TOKEN_VALUE`; không commit `.env`.
2. Token cần quyền xem VM, clone template, cấp VMID và khởi động VM trên
   Proxmox. Đảm bảo node có thể truy cập qua TCP 8006.
3. Chạy `docker compose up --build --force-recreate -d`, rồi mở `http://localhost:8080`.
   Nhấn **Tải lại** để gọi `GET /api/vms`. Nhập template VMID và nhấn
   **Clone, khởi động và lấy IP** để gọi `POST /api/vms/clone`.

Ví dụ gọi API trực tiếp:

```bash
curl http://localhost:8080/api/vms
curl -X POST http://localhost:8080/api/vms/clone \
  -H 'X-Service-Key: YOUR_SERVICE_API_KEY' \
  -H 'Content-Type: application/json' \
  -d '{"template_vmid":9000,"name":"lab-test-01","full_clone":false}'
```

API clone tự xin VMID qua `/cluster/nextid` nếu bỏ `new_vmid`, chờ task clone,
khởi động VM, rồi chờ QEMU Guest Agent báo IPv4. Adapter ưu tiên IP từ NIC
chính của bản clone; `LAB_NETWORK_CIDR` giúp chọn IP khi có nhiều địa chỉ,
nhưng IP DHCP ở dải khác vẫn được nhận. Linux template phải cài và chạy
`qemu-guest-agent` trong guest.
Kết quả thành công gồm `vmid`, `node`, `ip_address`. Nếu clone đã được tạo
nhưng start hoặc lấy IP lỗi, thông báo lỗi vẫn chứa `vmid` để bạn kiểm tra
VM đó trên Proxmox. API không tự xóa VM khi có lỗi.

## Chọn node đích để thử adapter

`POST /api/vms/clone` giữ request cũ và clone trên node chứa template.
Endpoint thử riêng `POST /api/vms/clone-to-node` yêu cầu thêm `target_node`:

```bash
curl -X POST http://localhost:8080/api/vms/clone-to-node \
  -H 'X-Service-Key: YOUR_SERVICE_API_KEY' \
  -H 'Content-Type: application/json' \
  -d '{"template_vmid":9000,"target_node":"pve2","full_clone":true}'
```

Adapter gọi clone từ node nguồn với `target=pve2`, chờ task ở node nguồn,
rồi cấu hình, khởi động VM và hỏi Guest Agent trên node đích. Sau này
scheduler có thể truyền node đã chọn vào `target_node`.

Trong lab của `setup-promox.md`, `local` trên mỗi PVE node là disk riêng,
không phải shared storage. Proxmox chỉ cho clone trực tiếp sang node khác
nếu template gốc ở shared storage. Hơn nữa guide chỉ tạo `vmbr1` trên `pve1`;
node đích cần bridge/mạng phù hợp để VM khởi động và nhận IP. Bạn có thể thử
API với `target_node=pve1` trước; chọn `pve2`/`pve3` chỉ sau khi chuẩn bị
shared storage và mạng ở các node đó.

**Giới hạn của lab hiện tại:** `setup-vm-and-guacamole.md` cấu hình Windows
template với IP tĩnh `172.20.11.100`; `vmbr1` không có DHCP. Clone Windows
sẽ mang cùng IP tĩnh. Guest Agent chỉ *đọc* IP, không cấp IP mới. Trước khi
chạy nhiều clone đồng thời, cần thiết lập cơ chế cấp IP riêng cho từng VM
(chẳng hạn DHCP hoặc cấu hình guest sau clone). Nếu disk local không hỗ trợ
linked clone, bật ô **Full clone** để thử; thao tác này cần thêm dung lượng.

Hai endpoint clone yêu cầu header `X-Service-Key` khớp với
`PROXMOX_LB_SERVICE_KEY`. Các endpoint đọc resource không yêu cầu key.
