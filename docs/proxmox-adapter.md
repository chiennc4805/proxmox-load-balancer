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
  -H 'Content-Type: application/json' \
  -d '{"template_vmid":9000,"name":"lab-test-01","full_clone":false}'
```

API clone tự xin VMID qua `/cluster/nextid` nếu bỏ `new_vmid`, chờ task clone,
khởi động VM, rồi chờ QEMU Guest Agent báo IPv4 thuộc `LAB_NETWORK_CIDR`.
Kết quả thành công gồm `vmid`, `node`, `ip_address`. Nếu clone đã được tạo
nhưng start hoặc lấy IP lỗi, thông báo lỗi vẫn chứa `vmid` để bạn kiểm tra
VM đó trên Proxmox. API không tự xóa VM khi có lỗi.

**Giới hạn của lab hiện tại:** `setup-vm-and-guacamole.md` cấu hình Windows
template với IP tĩnh `172.20.11.100`; `vmbr1` không có DHCP. Clone Windows
sẽ mang cùng IP tĩnh. Guest Agent chỉ *đọc* IP, không cấp IP mới. Trước khi
chạy nhiều clone đồng thời, cần thiết lập cơ chế cấp IP riêng cho từng VM
(chẳng hạn DHCP hoặc cấu hình guest sau clone). Nếu disk local không hỗ trợ
linked clone, bật ô **Full clone** để thử; thao tác này cần thêm dung lượng.

Các endpoint hiện chỉ để thử trong mạng tin cậy, chưa có xác thực người gọi.
