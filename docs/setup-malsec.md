# Triển khai hệ thống MalSec LMS — Step-by-Step Guide

> **Điểm bắt đầu:** Đã hoàn thành 2 guide trước:
> - Proxmox 3-node cluster chạy trên GCP (`pve1`, `pve2`, `pve3`)
> - Windows VM (`win-test`, VMID 9000, IP 172.20.11.100) chạy trên `pve1`
> - Guacamole chạy trên `guac-server` (10.10.0.50), RDP vào Windows VM OK
>
> **Mục tiêu:** Dựng MalSec LMS lên và kết nối với Proxmox + Guacamole **mà không cần sửa code**.
>
> **Nguyên tắc:** Chỉ cấu hình — file `.env`, Proxmox permissions, Guacamole extension. Zero code changes.

---

## Luồng nghiệp vụ tổng quan — MalSec hoạt động như thế nào?

Trước khi bắt tay vào triển khai, hãy hiểu luồng từ đầu đến cuối:

### 1. Giảng viên tạo bài Lab

```
Giảng viên đăng nhập MalSec Web UI
    ↓
Tạo bài Lab mới: chọn Template VM (VMID 9000), giao thức RDP, port 3389
    ↓
MalSec lưu cấu hình bài Lab vào PostgreSQL database
    ↓
Bài Lab xuất hiện trên dashboard của sinh viên
```

> **Tại sao cần Template VM?** Template là "bản gốc" Windows đã cài sẵn tool. Mỗi sinh viên sẽ nhận 1 bản copy (clone) riêng từ template này.

### 2. Sinh viên bấm "Start Lab" — clone VM tự động

```
Sinh viên bấm "Start Lab" trên browser
    ↓
MalSec Backend gọi Proxmox API:
    "Clone VM 9000 thành VM mới (VMID 10001) cho sinh viên này"
    ↓
Proxmox tạo Linked Clone (chỉ lưu phần khác biệt, nhanh + nhẹ)
    ↓
Proxmox bật VM mới lên
    ↓
MalSec Backend hỏi QEMU Guest Agent: "VM 10001 có IP gì?"
    → Guest Agent trả: "172.20.11.xxx"
    ↓
MalSec Backend tạo Guacamole encrypted token:
    "Kết nối RDP đến 172.20.11.xxx:3389, user=Administrator, pass=***"
    ↓
Token được mã hóa AES-256 + HMAC-SHA256 (cùng secret key với Guacamole)
    ↓
MalSec trả về URL Guacamole chứa token đã mã hóa
    ↓
Browser sinh viên nhúng iframe Guacamole → thấy desktop Windows
```

> **Tại sao cần QEMU Guest Agent?** Vì khi clone VM, IP mới do DHCP hoặc cấu hình bên trong VM quyết định. Proxmox bên ngoài không biết IP — phải hỏi Agent bên trong VM.
>
> **Tại sao dùng encrypted JSON thay vì đăng nhập Guacamole thủ công?** Để sinh viên không cần biết Guacamole credentials. MalSec tự sinh token 1 lần, Guacamole verify token bằng shared secret key → mở RDP session tự động.

### 3. Luồng mạng khi sinh viên dùng VM

```
Browser sinh viên
    ↓ HTTPS
MalSec Frontend (Nginx trên guac-server hoặc server riêng)
    ↓ /guacamole/ proxy
Guacamole Web App (guac-server:8080)
    ↓ RDP protocol
guacd daemon → 172.20.11.xxx:3389
    ↓ GCP route: 172.20.11.0/24 → pve1
pve1 forward vào bridge vmbr1
    ↓
Windows VM của sinh viên
```

### 4. Sơ đồ kiến trúc cuối cùng

```text
Google Cloud VPC: pve-vpc
│
├── Subnet: pve-subnet (10.10.0.0/24) — Proxmox + Guacamole
│   ├── pve1  10.10.0.11  (Proxmox VE 9.2)
│   │   ├── vmbr1 172.20.11.1/24 (NAT)
│   │   ├── VM 9000: win-test (Template, 172.20.11.100)
│   │   └── VM 10001..19999: Student VMs (auto-cloned)
│   ├── pve2  10.10.0.12  (Proxmox VE 9.2, cluster member)
│   ├── pve3  10.10.0.13  (Proxmox VE 9.2, cluster member)
│   └── guac-server  10.10.0.50  (Apache Guacamole + auth-json)
│
└── Subnet: malsec-subnet (10.20.0.0/24) — MalSec LMS
    └── malsec-server  10.20.0.10
        └── Docker: MalSec LMS (frontend + backend + PostgreSQL)
```

> **Tại sao tách subnet riêng cho MalSec?**
> - **Phân tách trách nhiệm:** Proxmox/Guacamole xử lý VM + remote desktop. MalSec xử lý web app + quản lý lab. Tách ra dễ bảo trì, scale, và troubleshoot.
> - **Bảo mật:** Có thể áp firewall rule riêng cho từng subnet. Ví dụ: chỉ cho phép MalSec gọi Proxmox API (port 8006), không cho phép truy cập SSH trực tiếp vào Proxmox node.
> - **Mở rộng:** Sau này thêm server MalSec thứ 2 (load balancing) chỉ cần thêm VM vào `malsec-subnet`.
>
> **Dù khác subnet nhưng cùng VPC** → các server vẫn "nhìn thấy nhau" qua mạng nội bộ GCP. Không cần VPN.

---

# PHẦN 1: CẤU HÌNH PROXMOX CHO MALSEC

## 1.1 Tạo API Token cho MalSec Backend

> **Làm gì?** Tạo "chìa khóa API" để MalSec Backend gọi Proxmox API (clone VM, start/stop VM, lấy IP...). Token này giống "thẻ nhân viên" — cho phép MalSec thao tác VM mà không cần biết password root.
>
> **Tại sao không dùng root password?** Nguyên tắc bảo mật: dùng token có quyền giới hạn, không dùng root. Nếu token bị lộ, chỉ cần xóa token, không ảnh hưởng root.

SSH vào `pve1`:

```bash
gcloud compute ssh pve1 --zone=asia-southeast1-b --tunnel-through-iap
```

Tạo user và token:

```bash
# Tạo user riêng cho MalSec trên Proxmox
sudo pveum user add malsec-api@pve --comment "MalSec LMS API Service Account"

# Gán quyền quản lý VM trên toàn cluster
sudo pveum aclmod / -user malsec-api@pve -role PVEVMAdmin

# Tạo API token (KHÔNG tách quyền — token có đủ quyền của user)
sudo pveum user token add malsec-api@pve malsec-token --privsep=0
```

**Output sẽ hiển thị token value — LƯU LẠI NGAY:**

```text
┌──────────────┬──────────────────────────────────────┐
│ key          │ value                                │
├──────────────┼──────────────────────────────────────┤
│ full-tokenid │ malsec-api@pve!malsec-token          │
│ info         │ {"privsep":"0"}                      │
│ value        │ xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx │
└──────────────┴──────────────────────────────────────┘
```

> **Lưu `value` vào đâu đó an toàn.** Đây là `PVE_TOKEN_VALUE` sẽ dùng trong `.env`. Proxmox chỉ hiện 1 lần, không xem lại được.

Kiểm tra:

```bash
sudo pveum user list
sudo pveum user token list malsec-api@pve
```

### 1.1.1 Test API token hoạt động

```bash
curl -k -s \
  -H "Authorization: PVEAPIToken=malsec-api@pve!malsec-token=YOUR_TOKEN_VALUE" \
  "https://127.0.0.1:8006/api2/json/version"
```

Expected: trả về JSON chứa version Proxmox.

---

## 1.2 Chuẩn bị Windows Template VM

> **Làm gì?** Convert VM `win-test` (VMID 9000) thành Template — "bản gốc" không thể boot trực tiếp, chỉ dùng để clone. MalSec sẽ clone từ template này mỗi khi sinh viên cần VM.
>
> **Tại sao phải convert thành template?** Template đảm bảo bản gốc không bao giờ bị sửa đổi. Mỗi sinh viên nhận bản copy riêng, hỏng thì rollback (xóa clone, clone lại).

**Trước khi convert, đảm bảo Windows VM đã:**
- ✅ Cài VirtIO driver + QEMU Guest Agent
- ✅ IP tĩnh 172.20.11.100 (hoặc DHCP nếu có DHCP server)
- ✅ RDP bật, firewall mở port 3389
- ✅ Cài sẵn tool phân tích malware (nếu cần)

Trên `pve1`:

```bash
# Tắt VM trước
sudo qm shutdown 9000

# Chờ VM tắt hẳn
sudo qm wait 9000

# Gỡ CD-ROM (ISO không cần nữa)
sudo qm set 9000 --ide2 none
sudo qm set 9000 --ide3 none

# Convert thành template
sudo qm template 9000
```

Kiểm tra:

```bash
sudo qm list
```

Expected: VM 9000 hiện trạng thái `stopped` và có flag template.

> **Lưu ý quan trọng:** Sau khi convert, VM 9000 không boot được nữa. Nếu cần chỉnh sửa template, phải clone ra VM mới → sửa → convert lại.

---

## 1.3 Thêm iptables rule cho phép Guacamole/MalSec RDP vào guest VM

> **Làm gì?** Mở đường cho traffic RDP từ guac-server (10.10.0.50) đi qua pve1 vào mạng guest VM (172.20.11.0/24).
>
> **Tại sao cần?** Ở guide Proxmox trước, iptables FORWARD chỉ cho phép:
> - Guest VM → Internet (outbound)
> - Internet → Guest VM nếu là traffic trả lời (RELATED,ESTABLISHED)
>
> Nhưng Guacamole cần **chủ động** mở kết nối RDP mới vào guest VM — đây là traffic "mới" từ ens4 vào vmbr1, bị chặn nếu default policy là DROP.

Trên `pve1`:

```bash
# Kiểm tra default policy
sudo iptables -L FORWARD | head -1
```

Nếu thấy `policy ACCEPT` → không cần thêm rule (mọi traffic đều qua). Chuyển sang bước tiếp.

Nếu thấy `policy DROP` → thêm rule:

```bash
sudo iptables -A FORWARD -i ens4 -o vmbr1 -s 10.10.0.0/24 -d 172.20.11.0/24 -j ACCEPT
```

Thêm rule cho MalSec subnet (10.20.0.0/24) nữa:

```bash
sudo iptables -A FORWARD -i ens4 -o vmbr1 -s 10.20.0.0/24 -d 172.20.11.0/24 -j ACCEPT
```

Để rule persist qua reboot, thêm vào `/etc/network/interfaces`:

```bash
sudo tee -a /etc/network/interfaces > /dev/null <<'EOF_MALSEC_FW'

    # Allow VPC hosts (MalSec/Guacamole) to reach guest VMs
    post-up /bin/sh -c 'iptables -C FORWARD -i ens4 -o vmbr1 -s 10.10.0.0/24 -d 172.20.11.0/24 -j ACCEPT 2>/dev/null || iptables -A FORWARD -i ens4 -o vmbr1 -s 10.10.0.0/24 -d 172.20.11.0/24 -j ACCEPT'
    post-down /bin/sh -c 'iptables -C FORWARD -i ens4 -o vmbr1 -s 10.10.0.0/24 -d 172.20.11.0/24 -j ACCEPT 2>/dev/null && iptables -D FORWARD -i ens4 -o vmbr1 -s 10.10.0.0/24 -d 172.20.11.0/24 -j ACCEPT || true'
    # Allow MalSec subnet to reach guest VMs
    post-up /bin/sh -c 'iptables -C FORWARD -i ens4 -o vmbr1 -s 10.20.0.0/24 -d 172.20.11.0/24 -j ACCEPT 2>/dev/null || iptables -A FORWARD -i ens4 -o vmbr1 -s 10.20.0.0/24 -d 172.20.11.0/24 -j ACCEPT'
    post-down /bin/sh -c 'iptables -C FORWARD -i ens4 -o vmbr1 -s 10.20.0.0/24 -d 172.20.11.0/24 -j ACCEPT 2>/dev/null && iptables -D FORWARD -i ens4 -o vmbr1 -s 10.20.0.0/24 -d 172.20.11.0/24 -j ACCEPT || true'
EOF_MALSEC_FW
```

Kiểm tra:

```bash
sudo iptables -S FORWARD
```

---

# PHẦN 2: CẤU HÌNH GUACAMOLE AUTH-JSON EXTENSION

## 2.1 Tại sao cần extension này?

> MalSec **không** dùng hệ thống user/connection của Guacamole Web UI (cái bạn vừa test thủ công). Thay vào đó, MalSec tự sinh **encrypted JSON token** chứa thông tin kết nối RDP, gửi cho Guacamole qua URL parameter.
>
> Guacamole nhận token → giải mã bằng shared secret key → nếu hợp lệ → mở RDP session tự động.
>
> **Ưu điểm:** Sinh viên không cần tài khoản Guacamole riêng. MalSec quản lý tập trung, mỗi phiên tự tạo/tự hết hạn.

## 2.2 Tạo shared secret key

> **Secret key là gì?** Là chuỗi bí mật dùng chung giữa MalSec Backend và Guacamole. MalSec dùng key này để mã hóa token, Guacamole dùng cùng key để giải mã. Nếu key khác nhau → Guacamole từ chối token.

SSH vào `guac-server`:

```bash
gcloud compute ssh guac-server --zone=asia-southeast1-b --tunnel-through-iap
```

Tạo key 128-bit hex (32 ký tự hex = 16 bytes):

```bash
openssl rand -hex 16
```

Output ví dụ:

```text
a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6
```

**Lưu chuỗi này — sẽ dùng cho cả `guacamole.properties` và `.env` MalSec.**

## 2.3 Cài extension vào Guacamole container

> **Làm gì?** Download file `.jar` của extension `guacamole-auth-json` và mount vào Guacamole Docker container. Extension này cho phép Guacamole nhận encrypted JSON token qua URL.

```bash
cd ~/guacamole

# Tạo thư mục chứa extension và config
mkdir -p extensions
mkdir -p guacamole-config

# Download extension JAR phiên bản 1.5.5 (khớp với Guacamole image)
curl -L -o extensions/guacamole-auth-json-1.5.5.jar \
  "https://apache.org/dyn/closer.lua/guacamole/1.5.5/binary/guacamole-auth-json-1.5.5.tar.gz?action=download" \
  2>/dev/null || true
```

Nếu link trên không hoạt động, tải thủ công:

```bash
# Tải tarball
curl -L -o /tmp/guac-auth-json.tar.gz \
  "https://downloads.apache.org/guacamole/1.5.5/binary/guacamole-auth-json-1.5.5.tar.gz"

# Giải nén lấy file JAR
tar -xzf /tmp/guac-auth-json.tar.gz -C /tmp/
cp /tmp/guacamole-auth-json-1.5.5/guacamole-auth-json-1.5.5.jar extensions/

# Dọn dẹp
rm -rf /tmp/guac-auth-json.tar.gz /tmp/guacamole-auth-json-1.5.5
```

Kiểm tra:

```bash
ls -lh extensions/guacamole-auth-json-1.5.5.jar
```

## 2.4 Tạo file guacamole.properties

> **File này nói cho Guacamole:** "Hãy chấp nhận encrypted JSON tokens được mã hóa bằng key này."

```bash
cat > guacamole-config/guacamole.properties <<EOF
# MalSec encrypted JSON authentication
json-secret-key: YOUR_HEX_KEY_FROM_STEP_2_2
EOF
```

**Thay `YOUR_HEX_KEY_FROM_STEP_2_2` bằng chuỗi hex 32 ký tự đã tạo ở bước 2.2.**

## 2.5 Cập nhật docker-compose.yml của Guacamole

> **Làm gì?** Mount thư mục extensions và file config vào Guacamole container để nó nhận extension auth-json.

```bash
cat > ~/guacamole/docker-compose.yml <<'EOF'
services:
  guac-db:
    image: postgres:16-alpine
    container_name: guac-db
    restart: always
    environment:
      POSTGRES_USER: guac_user
      POSTGRES_PASSWORD: guac_password_change_me
      POSTGRES_DB: guac_db
    volumes:
      - guac_db_data:/var/lib/postgresql/data
      - ./initdb.sql:/docker-entrypoint-initdb.d/initdb.sql:ro
    networks:
      - guac-net

  guacd:
    image: guacamole/guacd:1.5.5
    container_name: guacd
    restart: always
    networks:
      - guac-net

  guacamole:
    image: guacamole/guacamole:1.5.5
    container_name: guacamole
    restart: always
    environment:
      GUACD_HOSTNAME: guacd
      POSTGRESQL_HOSTNAME: guac-db
      POSTGRESQL_PORT: 5432
      POSTGRESQL_DATABASE: guac_db
      POSTGRESQL_USER: guac_user
      POSTGRESQL_PASSWORD: guac_password_change_me
      GUACAMOLE_HOME: /etc/guacamole
    ports:
      - "8080:8080"
    volumes:
      - ./extensions:/etc/guacamole/extensions:ro
      - ./guacamole-config/guacamole.properties:/etc/guacamole/guacamole.properties:ro
    depends_on:
      - guacd
      - guac-db
    networks:
      - guac-net

volumes:
  guac_db_data:

networks:
  guac-net:
    driver: bridge
EOF
```

> **Thay `guac_password_change_me`** bằng password thực tế (giữ giống cả 2 service `guac-db` và `guacamole`).

Restart Guacamole:

```bash
cd ~/guacamole
docker compose down
docker compose up -d
```

Kiểm tra extension đã load:

```bash
docker compose logs guacamole 2>&1 | grep -i "json"
```

Expected: thấy dòng log liên quan đến JSON authentication provider loaded.

---

# PHẦN 3: TẠO GCP SUBNET + VM RIÊNG CHO MALSEC

## 3.1 Tạo subnet mới trong cùng VPC

> **Làm gì?** Tạo dải mạng `10.20.0.0/24` trong cùng VPC `pve-vpc`. Dù khác dải IP với `pve-subnet` (10.10.0.0/24), nhưng cùng VPC nên GCP tự động route giữa 2 subnet — không cần cấu hình gì thêm.
>
> **Tại sao tạo subnet riêng thay vì dùng `pve-subnet`?**
> - Phân tách rõ ràng: `10.10.0.x` = hạ tầng ảo hóa (Proxmox, Guacamole). `10.20.0.x` = ứng dụng web (MalSec).
> - Firewall rule riêng cho từng nhóm server.
> - Dễ quản lý khi hệ thống mở rộng.

Chạy từ **Cloud Shell**:

```bash
gcloud compute networks subnets create malsec-subnet \
  --network=pve-vpc \
  --region=asia-southeast1 \
  --range=10.20.0.0/24
```

Kiểm tra:

```bash
gcloud compute networks subnets list \
  --network=pve-vpc \
  --format='table(name,ipCidrRange,region)'
```

Expected:

```text
NAME            IP_CIDR_RANGE   REGION
pve-subnet      10.10.0.0/24    asia-southeast1
malsec-subnet   10.20.0.0/24    asia-southeast1
```

---

## 3.2 Tạo firewall rules cho MalSec subnet

> **Làm gì?** Cho phép:
> 1. MalSec server gọi Proxmox API (port 8006) và Guacamole (port 8080)
> 2. SSH qua IAP vào MalSec server
> 3. Truy cập MalSec Web UI (port 80) từ internet

```bash
# Rule 1: Cho phép MalSec subnet nói chuyện với Proxmox subnet (và ngược lại)
gcloud compute firewall-rules create malsec-to-pve \
  --network=pve-vpc \
  --direction=INGRESS \
  --priority=1000 \
  --source-ranges=10.20.0.0/24 \
  --target-tags=pve-node \
  --allow=tcp,udp,icmp

# Rule 2: Cho phép Proxmox/Guac subnet nói chuyện với MalSec subnet
gcloud compute firewall-rules create pve-to-malsec \
  --network=pve-vpc \
  --direction=INGRESS \
  --priority=1000 \
  --source-ranges=10.10.0.0/24 \
  --target-tags=malsec-server \
  --allow=tcp,udp,icmp

# Rule 3: SSH qua IAP vào MalSec server
gcloud compute firewall-rules create malsec-iap-ssh \
  --network=pve-vpc \
  --direction=INGRESS \
  --priority=1000 \
  --source-ranges=35.235.240.0/20 \
  --target-tags=malsec-server \
  --allow=tcp:22

# Rule 4: Mở MalSec Web UI (port 80) từ internet
gcloud compute firewall-rules create malsec-web-public \
  --network=pve-vpc \
  --direction=INGRESS \
  --priority=1000 \
  --source-ranges=0.0.0.0/0 \
  --target-tags=malsec-server \
  --allow=tcp:80
```

Kiểm tra:

```bash
gcloud compute firewall-rules list \
  --filter='network:pve-vpc' \
  --format='table(name,direction,sourceRanges,targetTags,allowed)'
```

---

## 3.3 Tạo GCP VM cho MalSec

> **Làm gì?** Tạo máy ảo `malsec-server` (IP 10.20.0.10) trên `malsec-subnet`. Dùng `e2-medium` (2 vCPU, 4GB RAM) — đủ chạy MalSec (3 Docker container: Nginx + FastAPI + PostgreSQL).

```bash
gcloud compute instances create malsec-server \
  --zone=asia-southeast1-b \
  --machine-type=e2-medium \
  --image-family=debian-13 \
  --image-project=debian-cloud \
  --boot-disk-size=30GB \
  --boot-disk-type=pd-balanced \
  --network-interface=network=pve-vpc,subnet=malsec-subnet,private-network-ip=10.20.0.10 \
  --tags=malsec-server
```

Kiểm tra:

```bash
gcloud compute instances describe malsec-server \
  --zone=asia-southeast1-b \
  --format='table(name,networkInterfaces[0].networkIP,networkInterfaces[0].subnetwork,status)'
```

Expected:

```text
name: malsec-server
networkIP: 10.20.0.10
subnet: .../malsec-subnet
status: RUNNING
```

---

## 3.4 Kiểm tra connectivity giữa 2 subnet

> **Tại sao test?** Đảm bảo MalSec server (10.20.0.10) nói chuyện được với Proxmox (10.10.0.11) và Guacamole (10.10.0.50). Dù khác subnet, GCP tự route trong cùng VPC.

SSH vào `malsec-server`:

```bash
gcloud compute ssh malsec-server \
  --zone=asia-southeast1-b \
  --tunnel-through-iap
```

Test connectivity:

```bash
# Ping Proxmox pve1
ping -c 2 10.10.0.11

# Ping Guacamole server
ping -c 2 10.10.0.50

# Test Proxmox API port
curl -k -s -o /dev/null -w "%{http_code}" https://10.10.0.11:8006/api2/json/version

# Test Guacamole port
curl -s -o /dev/null -w "%{http_code}" http://10.10.0.50:8080/guacamole/
```

Expected: ping reply, HTTP 200 hoặc 301/302.

Nếu timeout → kiểm tra firewall rules ở bước 3.2.

---

## 3.5 Cài Docker trên malsec-server

```bash
sudo apt update
sudo apt install -y ca-certificates curl gnupg

sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/debian/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg

echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/debian \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin

sudo usermod -aG docker $USER
```

Logout và SSH lại:

```bash
exit
```

```bash
gcloud compute ssh malsec-server --zone=asia-southeast1-b --tunnel-through-iap
```

Kiểm tra:

```bash
docker --version
docker compose version
docker run --rm hello-world
```

---

## 3.6 Thêm route cho guest subnet (172.20.11.0/24)

> **Tại sao?** MalSec Nginx proxy `/guacamole/` đến Guacamole server. Guacamole daemon (guacd) kết nối RDP đến 172.20.11.x. Nhưng MalSec server cũng cần biết đường đến 172.20.11.x — vì MalSec Backend dùng `VM_VERIFY_CONNECTION` để test port trước khi trả URL cho sinh viên.
>
> Route `route-guest-via-pve1` đã tạo ở guide trước chỉ áp dụng cho `pve-subnet`. Subnet mới `malsec-subnet` cũng cần route.

Thực tế, GCP custom route áp dụng cho **toàn bộ VPC** (không riêng subnet) nếu không filter bằng tag. Kiểm tra route đã tạo:

```bash
gcloud compute routes describe route-guest-via-pve1 \
  --format='yaml(name,destRange,nextHopInstance,tags)'
```

Nếu output **không có `tags`** → route đã áp dụng cho toàn VPC → MalSec server tự biết đường → không cần làm gì thêm.

Nếu có `tags` filter → cần thêm tag cho malsec-server hoặc tạo route mới. Nhưng guide trước không dùng tag cho route, nên bước này thường không cần.

Test từ `malsec-server`:

```bash
ping -c 2 172.20.11.1
```

Expected: reply → route hoạt động.

---

# PHẦN 4: TRIỂN KHAI MALSEC LMS

## 4.1 Clone source code lên malsec-server

> **Làm gì?** Đưa source code MalSec lên server để Docker build và chạy.

SSH vào `malsec-server`:

```bash
gcloud compute ssh malsec-server --zone=asia-southeast1-b --tunnel-through-iap
```

```bash
cd ~
git clone https://github.com/YOUR_USERNAME/MalSec-Public.git malsec
cd malsec
```

Hoặc nếu có sẵn source trên máy local, dùng `gcloud compute scp`:

```bash
# Từ máy local
gcloud compute scp --recurse ./MalSec-Public-main malsec-server:~/malsec \
  --zone=asia-southeast1-b \
  --tunnel-through-iap
```

## 4.2 Tạo file `.env`

> **Đây là bước quan trọng nhất.** File `.env` kết nối tất cả: MalSec ↔ PostgreSQL ↔ Proxmox API ↔ Guacamole. Mỗi biến có vai trò cụ thể.

```bash
cd ~/malsec
cp .env.example .env
```

Mở editor sửa `.env`:

```bash
nano .env
```

Dưới đây là nội dung `.env` đầy đủ **với giải thích từng nhóm:**

```dotenv
# ============================================================
# DATABASE — PostgreSQL chạy trong Docker container
# ============================================================
# MalSec lưu mọi thứ vào đây: users, classes, labs, submissions, grades
DB_USER=postgres
DB_PASSWORD=ThayDoiMatKhauManh123!
DB_NAME=malsec_lms
DB_HOST_PORT=5432

# ============================================================
# BACKEND — FastAPI server xử lý logic nghiệp vụ
# ============================================================
BACKEND_HOST=0.0.0.0
BACKEND_PORT=8000
BACKEND_HOST_PORT=8000

# JWT token dùng để xác thực user đăng nhập
# Sinh random: openssl rand -hex 32
JWT_SECRET=THAY_BANG_CHUOI_RANDOM_64_KY_TU_HEX
JWT_ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=480

# CORS: domain được phép gọi API
# Đặt đúng URL bạn sẽ truy cập MalSec (IP hoặc domain)
# KHÔNG dùng wildcard * (MalSec không cho phép)
# Dùng IP public của malsec-server (lấy ở bước 4.5)
CORS_ORIGINS=http://MALSEC_SERVER_PUBLIC_IP

# ============================================================
# TÀI KHOẢN ADMIN MẶC ĐỊNH — chỉ dùng lần đầu database trống
# ============================================================
INITIAL_ADMIN_USERNAME=admin
INITIAL_ADMIN_PASSWORD=Admin@MalSec2026!
INITIAL_ADMIN_EMAIL=admin@malsec.lab
INITIAL_ADMIN_FULL_NAME=System Administrator

# Password mặc định khi import sinh viên hàng loạt từ CSV
DEFAULT_STUDENT_PASSWORD=Student@123

# ============================================================
# UPLOAD — Chính sách tải file bài nộp sinh viên
# ============================================================
UPLOAD_DIR=/app/uploads
MAX_FILE_SIZE_MB=50
ALLOWED_EXTENSIONS=png,jpg,jpeg,txt,log,pcap,pdf,docx,zip
# Password dùng để giải nén file zip malware sample
MALWARE_ZIP_PASSWORD=infected

# ============================================================
# PROXMOX VE — Kết nối API để quản lý VM
# ============================================================
# IP nội bộ của pve1 (MalSec backend chạy cùng VPC nên dùng IP nội bộ)
PVE_API_HOST=10.10.0.11
# User + Token đã tạo ở Phần 1
PVE_API_USER=malsec-api@pve
PVE_TOKEN_NAME=malsec-token
PVE_TOKEN_VALUE=THAY_BANG_TOKEN_VALUE_TU_BUOC_1_1
# Node Proxmox chứa VM template
PVE_NODE=pve1
# Tắt verify SSL vì Proxmox dùng self-signed cert
PVE_VERIFY_SSL=false

# Dải VMID cho Template VM (giảng viên chọn template từ dải này)
TEMPLATE_VMID_MIN=9000
TEMPLATE_VMID_MAX=9999
# Dải VMID cho Student VM (MalSec tự allocate VMID trong dải này khi clone)
STUDENT_VMID_MIN=10000
STUDENT_VMID_MAX=19999
# Template VM mặc định (VMID của win-test đã convert template)
DEFAULT_TEMPLATE_VMID=9000

# Mạng guest VM — MalSec dùng để verify IP từ Guest Agent nằm đúng dải
LAB_NETWORK_CIDR=172.20.11.0/24

# Giao thức và port kết nối VM
VM_PROTOCOL_PORTS=rdp:3389,vnc:5900,ssh:22
DEFAULT_VM_PROTOCOL=rdp

# Timeout khi clone/boot/kết nối VM (giây)
VM_CLONE_TIMEOUT_SECONDS=900
VM_CONNECTION_TIMEOUT_SECONDS=180
VM_AGENT_TIMEOUT_SECONDS=180
VM_BOOT_WAIT_SECONDS=15
VM_VERIFY_CONNECTION=false

# ============================================================
# APACHE GUACAMOLE — Encrypted JSON Authentication
# ============================================================
# URL path tới Guacamole (Nginx sẽ proxy /guacamole/ tới đây)
GUAC_BASE_URL=/guacamole/
# CÙNG KEY đã cấu hình trong guacamole.properties ở Phần 2
GUAC_JSON_SECRET=THAY_BANG_HEX_KEY_32_KY_TU_TU_BUOC_2_2
# Thời gian sống của mỗi session token (giây) — 24 giờ
GUAC_SESSION_TTL_SECONDS=86400
# Cấu hình RDP qua Guacamole
GUAC_RDP_SECURITY=any
GUAC_RDP_SERVER_LAYOUT=en-us-qwerty
GUAC_RDP_IGNORE_CERT=true
GUAC_RDP_ENABLE_WALLPAPER=true
GUAC_RDP_ENABLE_THEMING=true
GUAC_RDP_ENABLE_FONT_SMOOTHING=true
GUAC_RDP_ENABLE_FULL_WINDOW_DRAG=true
GUAC_RDP_ENABLE_MENU_ANIMATIONS=true
GUAC_RDP_ENABLE_DESKTOP_COMPOSITION=true
GUAC_SSH_IGNORE_HOST_KEY=true

# ============================================================
# FRONTEND — Nginx reverse proxy
# ============================================================
FRONTEND_HTTP_PORT=80
NGINX_LISTEN_PORT=80
NGINX_SERVER_NAME=_
# Backend upstream (Docker internal network — container name "backend")
BACKEND_UPSTREAM=http://backend:8000
# Guacamole upstream — trỏ đến Guacamole container trên guac-server (khác server)
# Dùng IP nội bộ của guac-server vì cùng VPC
GUAC_UPSTREAM=http://10.10.0.50:8080/guacamole/
CLIENT_MAX_BODY_SIZE=100M
PROXY_CONNECT_TIMEOUT=30s
PROXY_SEND_TIMEOUT=1000s
PROXY_READ_TIMEOUT=1000s

# Vite build config (chỉ dùng lúc build frontend Docker image)
VITE_DEV_HOST=0.0.0.0
VITE_DEV_PORT=3000
VITE_API_PROXY_TARGET=http://backend:8000
VITE_API_PROXY_SECURE=false
```

### Tóm tắt 4 giá trị BẮT BUỘC phải thay:

| Biến | Lấy từ đâu |
|------|------------|
| `JWT_SECRET` | Chạy `openssl rand -hex 32` trên server |
| `PVE_TOKEN_VALUE` | Output bước 1.1 khi tạo Proxmox API token |
| `GUAC_JSON_SECRET` | Output bước 2.2 khi tạo hex key |
| `DB_PASSWORD` | Tự đặt password mạnh |

---

## 4.3 Build và chạy MalSec

> **Làm gì?** Docker Compose sẽ:
> 1. Build backend image (Python 3.11 + FastAPI + dependencies)
> 2. Build frontend image (Node.js build React → Nginx serve)
> 3. Khởi động PostgreSQL → chờ healthy → khởi động backend → khởi động frontend
> 4. Backend tự tạo database tables + tài khoản admin mặc định

SSH vào `malsec-server`:

```bash
cd ~/malsec
docker compose up -d --build
```

> Lần đầu build mất 3-8 phút (download base images + install dependencies).

Kiểm tra:

```bash
docker compose ps
```

Expected: 3 container đều `running`:

```text
NAME              STATUS
malsec-db         running (healthy)
malsec-backend    running
malsec-frontend   running
```

Xem log backend (kiểm tra khởi tạo thành công):

```bash
docker compose logs backend | head -30
```

Expected thấy:

```text
[SYSTEM] MalSec Logging System initialized successfully.
Default admin account created successfully.
```

Test API:

```bash
curl -s http://localhost/api/ | head
```

Expected: `{"message":"MalSec LMS API is running normally"}`

---

## 4.4 Truy cập MalSec Web UI

Lấy external IP của malsec-server (từ **Cloud Shell**):

```bash
export MALSEC_PUBLIC_IP="$(
  gcloud compute instances describe malsec-server \
    --zone=asia-southeast1-b \
    --format='get(networkInterfaces[0].accessConfigs[0].natIP)'
)"
echo "MalSec URL: http://$MALSEC_PUBLIC_IP/"
```

**Quan trọng:** Quay lại sửa `CORS_ORIGINS` trong `.env` cho đúng IP public:

```bash
# SSH vào malsec-server
cd ~/malsec
# Sửa .env: thay CORS_ORIGINS=http://MALSEC_SERVER_PUBLIC_IP bằng IP thực
nano .env

# Restart sau khi sửa
docker compose down
docker compose up -d
```

Mở browser:

```text
http://MALSEC_PUBLIC_IP/
```

Đăng nhập:

```text
Username: admin
Password: Admin@MalSec2026!   (hoặc password bạn đặt trong .env)
```

> Nếu hiện trang login MalSec = thành công! Nếu trắng hoặc lỗi, xem troubleshooting phía dưới.

---

# PHẦN 5: TEST END-TO-END

## 5.1 Tạo test lab

1. Đăng nhập MalSec với tài khoản `admin`
2. Chuyển sang tab **Instructor Portal** (admin có quyền truy cập)
3. Trước tiên tạo **Class**: vào tab "Classes & Students" → tạo class mới
4. Tạo **Lab**: click "Design New Dynamic Lab"
   - Title: `Test Lab RDP`
   - Class: chọn class vừa tạo
   - Enable VM: ☑ (checked)
   - Template VM: chọn `VM 9000 - win-test (Template)`
   - Protocol: `RDP`
   - Port: `3389`
   - VM Username: `Administrator`
   - VM Password: password của Windows VM
   - Thêm ít nhất 1 question field
   - Deadline: ngày tương lai
   - Save

## 5.2 Test với tài khoản sinh viên

1. Tạo student account: Admin Dashboard → User Management → Add New User (role: student)
2. Gán student vào class
3. Logout → đăng nhập bằng student account
4. Thấy bài Lab → click "Start Lab"
5. MalSec sẽ:
   - Clone VM 9000 → VM mới (VMID 10xxx)
   - Boot VM → lấy IP qua Guest Agent
   - Sinh Guacamole encrypted token
   - Hiển thị desktop Windows trong iframe

> **Lần đầu clone mất 30-60 giây** (Linked Clone). Lần sau nếu VM đã tồn tại thì chỉ mất vài giây.

---

# PHẦN 6: TROUBLESHOOTING

## 6.1 MalSec hiện "Cannot connect to the Proxmox API"

Backend không kết nối được Proxmox. Kiểm tra:

```bash
# Từ malsec-server, test kết nối đến Proxmox API
curl -k https://10.10.0.11:8006/api2/json/version
```

Nếu timeout → kiểm tra firewall rule `malsec-to-pve` đã tạo ở bước 3.2.

## 6.2 VM clone thành công nhưng "did not report a VLAN 30 IP"

Guest Agent chưa trả IP đúng dải `LAB_NETWORK_CIDR`.

Nguyên nhân phổ biến:
- **QEMU Guest Agent chưa cài trong template** → boot template VM, cài agent, convert lại
- **IP trong VM không thuộc dải `LAB_NETWORK_CIDR`** → kiểm tra `.env` có đúng `LAB_NETWORK_CIDR=172.20.11.0/24`

## 6.3 Guacamole hiện "Invalid or expired authentication token"

Secret key không khớp giữa MalSec và Guacamole.

Kiểm tra:
1. `.env` → `GUAC_JSON_SECRET` = ?
2. `guacamole-config/guacamole.properties` → `json-secret-key` = ?

Hai giá trị phải **giống hệt nhau**.

## 6.4 MalSec frontend trắng / không load

```bash
docker compose logs frontend | tail -20
```

Kiểm tra Nginx config:

```bash
docker compose exec frontend cat /etc/nginx/conf.d/default.conf
```

Biến `BACKEND_UPSTREAM` và `GUAC_UPSTREAM` phải được substitute đúng.

## 6.5 CORS error trong browser console

`CORS_ORIGINS` trong `.env` phải khớp **chính xác** URL bạn đang truy cập.

Ví dụ truy cập `http://34.126.xxx.xxx` thì:

```dotenv
CORS_ORIGINS=http://34.126.xxx.xxx
```

Không có trailing slash, không có port 80 (mặc định HTTP).

Sau khi sửa:

```bash
docker compose down
docker compose up -d
```

---

# PHẦN 7: CHECKPOINT CUỐI

## Trạng thái hệ thống hoàn chỉnh

```text
Google Cloud VPC: pve-vpc (10.10.0.0/24)
│
├── pve1  10.10.0.11  (Proxmox VE 9.2, cluster pve-malsec)
│   ├── vmbr1 172.20.11.1/24 (NAT + FORWARD cho 10.10.0.0/24)
│   ├── API Token: malsec-api@pve!malsec-token
│   ├── Template VM 9000: win-test (Windows Server 2016, RDP, Guest Agent)
│   └── Student VMs: VMID 10000-19999 (auto-cloned by MalSec)
│
├── pve2  10.10.0.12  (cluster member)
├── pve3  10.10.0.13  (cluster member)
│
└── guac-server  10.10.0.50
    └── Docker: Apache Guacamole 1.5.5
        ├── guac-db (PostgreSQL 16)
        ├── guacd (RDP/VNC/SSH proxy daemon)
        ├── guacamole (web app :8080)
        └── Extension: guacamole-auth-json (shared secret key)

Subnet: malsec-subnet (10.20.0.0/24)
└── malsec-server  10.20.0.10
    └── Docker: MalSec LMS
        ├── malsec-db (PostgreSQL 16, :5432)
        ├── malsec-backend (FastAPI, :8000)
        └── malsec-frontend (Nginx, :80)
            ├── / → React SPA
            ├── /api → proxy to backend:8000
            └── /guacamole/ → proxy to 10.10.0.50:8080

GCP Route: 172.20.11.0/24 → next-hop pve1
GCP Firewall: port 80 (MalSec), 8080 (Guacamole), 8006 (Proxmox UI)
Cross-subnet: 10.10.0.0/24 ↔ 10.20.0.0/24
```

## Luồng hoàn chỉnh khi sinh viên làm lab

```
1. Sinh viên mở http://MALSEC_SERVER_PUBLIC_IP/ → trang login MalSec
2. Đăng nhập → thấy danh sách bài Lab
3. Click "Start Lab" →
4. MalSec Backend (10.20.0.10) → Proxmox API (10.10.0.11:8006): "Clone VM 9000 → VM 10xxx"
5. Proxmox clone xong → MalSec start VM → hỏi Guest Agent IP
6. Guest Agent trả IP 172.20.11.xxx
7. MalSec sinh encrypted JSON token (AES + HMAC, secret key)
8. Browser nhận URL: /guacamole/#/client/c/Lab-VM-student?data=ENCRYPTED
9. Nginx (10.20.0.10) proxy /guacamole/ → Guacamole (10.10.0.50:8080)
10. Guacamole giải mã token (cùng secret key) → mở RDP session
11. guacd → 172.20.11.xxx:3389 (qua GCP route → pve1 → vmbr1 → VM)
12. Sinh viên thấy desktop Windows trong browser, làm bài lab
13. Nộp bài qua form bên phải màn hình
14. Giảng viên chấm điểm qua Speed Grader
```