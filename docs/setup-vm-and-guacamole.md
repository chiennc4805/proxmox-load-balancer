# Tạo Windows VM trên Proxmox & Dựng Guacamole trên GCP — Step-by-Step Guide

> **Điểm bắt đầu:** Đã hoàn thành `gcp-proxmox-ve-9-nested-lab-setup-guide.md` — cluster 3 node Proxmox VE 9.2 đang chạy, `vmbr1` trên `pve1` đã có NAT.
>
> **Mục tiêu của guide này:**
> 1. Tạo 1 VM Windows trên Proxmox `pve1` nằm trong dải `vmbr1` (172.20.11.0/24)
> 2. Tạo 1 GCP VM chạy Apache Guacamole (Docker) trong cùng VPC `pve-vpc`
> 3. Test RDP từ Guacamole vào Windows VM qua Proxmox NAT
> 4. Chuẩn bị sẵn sàng cho bước tiếp theo: dựng hệ thống MalSec LMS
>
> **Môi trường:** GCP `asia-southeast1-b`, Proxmox VE 9.2, Debian 13, cluster `pve-malsec`

---

## 0. Kiến trúc mục tiêu của guide

```text
Google Cloud VPC: pve-vpc (10.10.0.0/24)
│
├── pve1  10.10.0.11
│   └── vmbr1 172.20.11.1/24 (NAT → ens4)
│       └── win-test  172.20.11.100 (Windows VM, RDP 3389)
│
├── pve2  10.10.0.12
├── pve3  10.10.0.13
│
└── guac-server  10.10.0.50 (GCP VM)
    └── Docker: Apache Guacamole
        └── RDP → 172.20.11.100:3389 (qua pve1 routing)

Luồng kết nối RDP:
Browser → guac-server:8080 → Guacamole → 172.20.11.100:3389 → Windows VM
```

### Bảng thông số

| Thành phần | Giá trị |
|---|---|
| Windows VM name | `win-test` |
| Windows VM VMID | `9000` |
| Windows VM IP | `172.20.11.100/24` |
| Windows VM Gateway | `172.20.11.1` |
| Windows VM DNS | `8.8.8.8` |
| Windows VM bridge | `vmbr1` |
| Guacamole GCP VM | `guac-server` |
| Guacamole GCP IP | `10.10.0.50` |
| Guacamole port | `8080` |
| Machine type (guac) | `e2-medium` |

---

## Giải thích tổng quan cho người mới — Mỗi bước làm gì và tại sao?

### Bức tranh toàn cảnh

Hệ thống MalSec cho phép sinh viên mở browser → thấy 1 màn hình Windows chạy trong trình duyệt → phân tích malware trên đó. Để làm được vậy, cần 3 thứ:

```
1. MÁY ẢO WINDOWS (chạy trên Proxmox)
   → Là "con máy tính ảo" cho sinh viên làm bài lab

2. GUACAMOLE (chạy trên 1 server riêng)
   → Là "cầu nối" giúp biến màn hình Windows thành trang web
   → Sinh viên không cần cài phần mềm gì, chỉ mở browser

3. MẠNG NỐI CHÚNG LẠI
   → Guacamole phải "tìm thấy" con Windows VM để kết nối vào
```

### Phần A — Tạo Windows VM: Tại sao và từng bước

| Bước | Làm gì | Tại sao |
|------|--------|---------|
| **A1** | Tải file ISO Windows + VirtIO driver | ISO là "đĩa cài Windows". VirtIO là driver giúp Windows nhận ổ cứng ảo và mạng ảo — nếu thiếu thì Windows không thấy disk để cài, không thấy mạng để kết nối |
| **A2** | Tạo VM bằng lệnh `qm create` | Giống "lắp ráp 1 cái máy tính ảo": chọn CPU, RAM, ổ cứng, gắn đĩa cài, cắm dây mạng vào `vmbr1` |
| **A3** | Cài Windows qua console | Giống cài Windows trên máy thật — nhưng xem qua giao diện web Proxmox. Phải load driver VirtIO trước khi chọn ổ cứng |
| **A4** | Đặt IP tĩnh `172.20.11.100` | Windows VM nằm trong mạng riêng `172.20.11.0/24`. Cần IP cố định để Guacamole luôn biết kết nối đến đâu. Gateway `172.20.11.1` là cửa ra internet (bridge trên pve1) |
| **A5** | Bật RDP (Remote Desktop) | RDP là giao thức cho phép điều khiển Windows từ xa. Guacamole dùng RDP để "nhìn thấy" và gửi chuột/bàn phím vào Windows VM |
| **A6** | Kiểm tra QEMU Guest Agent | Agent là phần mềm nhỏ chạy bên trong Windows, giúp Proxmox biết IP của VM. MalSec dùng thông tin này để tự động kết nối |

### Phần B — Dựng Guacamole: Tại sao và từng bước

| Bước | Làm gì | Tại sao |
|------|--------|---------|
| **B1** | Tạo 1 GCP VM mới (`guac-server`) | Guacamole cần 1 máy riêng để chạy. Đặt cùng mạng VPC với Proxmox để chúng "nhìn thấy nhau" |
| **B2** | Thêm GCP route `172.20.11.0/24 → pve1` | Mặc định GCP không biết mạng `172.20.11.x` ở đâu. Route này nói: "muốn đến `172.20.11.x` thì đi qua pve1" — giống biển chỉ đường trên cao tốc |
| **B3** | Cài Docker | Guacamole chạy trong Docker container — giống "hộp đóng gói sẵn" chứa Guacamole + database, chỉ cần bật lên là chạy |
| **B4** | Chạy Guacamole bằng Docker Compose | Khởi động 3 container: **guac-db** (database lưu cấu hình), **guacd** (daemon xử lý kết nối RDP/VNC/SSH), **guacamole** (giao diện web) |
| **B5** | Mở firewall + lấy public IP | Để truy cập Guacamole từ browser bên ngoài, cần mở port 8080 và biết IP public của server |
| **B6** | Tạo connection RDP trong Guacamole | Nói cho Guacamole: "Khi tôi click vào, hãy kết nối RDP đến IP `172.20.11.100` port `3389` với user/password này" |

### Tóm lại luồng hoạt động

```
Bạn mở browser
    ↓
Truy cập http://guac-server:8080/guacamole/
    ↓
Click vào connection "win-test-rdp"
    ↓
Guacamole (trong Docker) gửi kết nối RDP đến 172.20.11.100:3389
    ↓
GCP route chỉ đường: "172.20.11.x → đi qua pve1"
    ↓
pve1 chuyển tiếp vào bridge vmbr1 → đến Windows VM
    ↓
Windows VM trả lại màn hình desktop qua RDP
    ↓
Guacamole render thành HTML5 hiển thị trong browser của bạn
    ↓
Bạn thấy desktop Windows ngay trong trình duyệt, dùng chuột/bàn phím bình thường
```

---

# PHẦN A: TẠO WINDOWS VM TRÊN PROXMOX

## A1. Upload Windows ISO lên Proxmox pve1

> **Bước này làm gì?** Đưa "đĩa cài Windows" lên server Proxmox. Giống như ngày xưa bạn cắm USB cài Windows cho máy tính — nhưng ở đây là upload file ISO lên cloud.
>
> **Tại sao?** Proxmox cần file ISO để "gắn đĩa" cho máy ảo khi cài hệ điều hành. Không có ISO = không có gì để cài.

### A1.1 Tải Windows Server 2016 Evaluation ISO

> **Tại sao chọn Server 2016?** Nhẹ (~6.5GB), RDP bật sẵn, miễn phí dùng thử 180 ngày, đủ dùng cho lab. Bản Evaluation là bản chính thức từ Microsoft cho mục đích test.

Dùng Windows Server 2016 Datacenter Evaluation — nhẹ, RDP có sẵn, phù hợp cho lab.

SSH vào `pve1`:

```bash
gcloud compute ssh pve1 --zone=asia-southeast1-b --tunnel-through-iap
```

Download trực tiếp trên pve1:

```bash
cd /var/lib/vz/template/iso/
sudo wget -O windows-server-2016.iso \
  "https://software-static.download.prss.microsoft.com/pr/download/Windows_Server_2016_Datacenter_EVAL_en-us_14393_refresh.ISO"
```

> File khoảng ~6.5GB, thời gian download tùy bandwidth GCP (thường 3-8 phút).

Kiểm tra:

```bash
ls -lh /var/lib/vz/template/iso/windows-server-2016.iso
```

Expected: file khoảng 6.5GB.

Cách thay thế — upload qua Proxmox Web UI:

1. Truy cập `https://PVE1_PUBLIC_IP:8006`
2. Chọn node `pve1` → `local` (dưới mục Storage) → tab `ISO Images`
3. Click `Download from URL` → paste URL trên → Download

### A1.2 Tải VirtIO driver ISO

> **VirtIO là gì?** Là bộ driver đặc biệt giúp Windows "nói chuyện" được với phần cứng ảo của Proxmox (ổ cứng ảo, card mạng ảo). Không có driver này, Windows sẽ **không thấy ổ cứng** để cài đặt và **không có mạng** sau khi cài xong.
>
> **Ví dụ dễ hiểu:** Giống như khi bạn mua máy in mới, phải cài driver thì máy tính mới nhận máy in. Ở đây Windows cần driver VirtIO để nhận ổ cứng ảo và card mạng ảo.

```bash
cd /var/lib/vz/template/iso/
sudo wget https://fedorapeople.org/groups/virt/virtio-win/direct-downloads/stable-virtio/virtio-win.iso
```

Kiểm tra:

```bash
ls -lh /var/lib/vz/template/iso/virtio-win.iso
```

---

## A2. Tạo Windows VM qua CLI

> **Bước này làm gì?** "Lắp ráp" một cái máy tính ảo trên Proxmox. Giống như đi mua linh kiện: chọn bao nhiêu CPU, bao nhiêu RAM, ổ cứng bao nhiêu GB, rồi gắn đĩa cài Windows vào.
>
> **Tại sao dùng CLI thay vì giao diện web?** Cả hai đều được. CLI nhanh hơn, copy-paste 1 lệnh là xong. Giao diện web phải click qua nhiều bước.

SSH vào `pve1`:

```bash
gcloud compute ssh pve1 --zone=asia-southeast1-b --tunnel-through-iap
```

### A2.1 Tạo VM

```bash
sudo qm create 9000 \
  --name win-test \
  --ostype win10 \
  --machine q35 \
  --bios ovmf \
  --efidisk0 local:1,efitype=4m,pre-enrolled-keys=0 \
  --cpu host \
  --cores 2 \
  --memory 4096 \
  --scsihw virtio-scsi-single \
  --scsi0 local:32,iothread=1 \
  --ide2 local:iso/windows-server-2016.iso,media=cdrom \
  --ide3 local:iso/virtio-win.iso,media=cdrom \
  --net0 virtio,bridge=vmbr1 \
  --agent enabled=1 \
  --boot order='ide2;scsi0' \
  --vga std
```

> Windows Server 2016 dùng `--ostype win10` (cùng generation).

Giải thích các tham số quan trọng:
- `--name win-test`: Tên máy ảo, để dễ nhận biết
- `--bios ovmf`: Dùng UEFI boot — chuẩn boot hiện đại, Windows 10+ cần UEFI
- `--efidisk0`: Phân vùng EFI — nơi lưu thông tin boot UEFI
- `--cpu host`: Cho VM dùng CPU giống hệt CPU thật — tốc độ tốt nhất
- `--cores 2 --memory 4096`: 2 nhân CPU, 4GB RAM — đủ chạy Windows Server 2016
- `--scsi0 local:32`: Tạo ổ cứng ảo 32GB, dùng driver VirtIO SCSI (nhanh hơn IDE truyền thống)
- `--ide2`: Gắn đĩa CD chứa file ISO Windows (để cài)
- `--ide3`: Gắn đĩa CD chứa VirtIO driver (để Windows nhận ổ cứng + mạng)
- `--net0 virtio,bridge=vmbr1`: Cắm "dây mạng ảo" vào bridge `vmbr1` — mạng riêng 172.20.11.0/24
- `--agent enabled=1`: Bật QEMU Guest Agent — phần mềm nhỏ giúp Proxmox biết IP của VM (MalSec cần thông tin này để tự động kết nối)
- `--boot order='ide2;scsi0'`: Boot từ đĩa CD trước (để cài Windows), sau đó boot từ ổ cứng

### A2.2 Kiểm tra config VM

```bash
sudo qm config 9000
```

Expected thấy:

```text
boot: order=ide2;scsi0
cores: 2
memory: 4096
name: win-test
net0: virtio=...,bridge=vmbr1
...
```

---

## A3. Cài đặt Windows

> **Bước này làm gì?** Cài Windows lên máy ảo — giống y hệt cài Windows trên máy tính thật, chỉ khác là bạn xem và thao tác qua giao diện web (noVNC console) của Proxmox thay vì ngồi trước màn hình vật lý.

### A3.1 Khởi động VM

```bash
sudo qm start 9000
```

### A3.2 Truy cập console

Dùng **Proxmox Web UI** → node `pve1` → VM `9000 (win-test)` → tab `Console` (noVNC).

### A3.3 Quá trình cài Windows

1. Chọn ngôn ngữ → Next → "Install now"
2. Chọn edition: **Windows Server 2016 Datacenter Evaluation (Desktop Experience)** — bản có GUI
3. Accept license → chọn **Custom: Install Windows only**
4. Đến bước chọn disk: **Windows sẽ không thấy disk** (màn hình trống) vì dùng VirtIO SCSI

**Load driver SCSI (bắt buộc):**

5. Click `Load driver` → `Browse` → chọn ổ CD chứa VirtIO ISO (thường là `D:\` hoặc `E:\`)
6. Tìm đến thư mục `vioscsi\2k16\amd64`
7. Chọn `Red Hat VirtIO SCSI pass-through controller` → Next
8. Disk 32GB xuất hiện trong danh sách

**⚠️ CHƯA BẤM NEXT để cài Windows. Load tiếp driver network trước:**

9. Click `Load driver` lần nữa → `Browse` → cùng ổ CD VirtIO
10. Tìm đến thư mục `NetKVM\2k16\amd64`
11. Chọn `Red Hat VirtIO Ethernet Adapter` → Next

**Bây giờ mới cài:**

12. Chọn disk 32GB → **Next** → Windows bắt đầu cài đặt (chờ 10-20 phút)
13. Sau khi reboot, đặt password cho `Administrator` (bắt buộc trên Server)

> **Tại sao phải load network driver trước khi cài?** Nếu không, sau khi Windows boot sẽ không thấy NIC. Có thể cài bổ sung sau qua Device Manager nhưng phiền hơn nhiều.

### A3.4 Cài VirtIO Guest Agent sau khi Windows boot

> **Guest Agent là gì?** Là một phần mềm nhỏ chạy ngầm bên trong Windows VM. Nó giúp Proxmox (bên ngoài) "hỏi" Windows (bên trong): "IP của mày là gì?", "Mày đang chạy chưa?". Không có Guest Agent, Proxmox chỉ biết VM đang bật/tắt, không biết IP.
>
> **Tại sao MalSec cần?** Khi sinh viên bấm "Start Lab", MalSec tự clone VM rồi hỏi Guest Agent lấy IP → rồi tạo link Guacamole RDP tới IP đó. Không có Agent = MalSec không biết kết nối vào đâu.

Sau khi Windows boot lần đầu:

1. Mở **File Explorer** → tìm ổ CD VirtIO ISO
2. Chạy `virtio-win-guest-tools.exe` hoặc `guest-agent\qemu-ga-x86_64.msi`
3. Cài đặt xong → QEMU Guest Agent sẽ chạy

Kiểm tra từ `pve1`:

```bash
sudo qm agent 9000 ping
```

Expected: không có lỗi (trả về rỗng hoặc `{"return":{}}`)

---

## A4. Cấu hình network cho Windows VM

> **Bước này làm gì?** Đặt "địa chỉ nhà" (IP) cố định cho Windows VM trong mạng nội bộ `172.20.11.0/24`.
>
> **Tại sao cần IP tĩnh?** Vì mạng `vmbr1` không có DHCP server (không có ai tự động phát IP). Nếu không đặt IP tĩnh, Windows sẽ tự đặt IP 169.254.x.x (APIPA) — Guacamole không biết IP này, không kết nối được.
>
> **Gateway 172.20.11.1 là gì?** Là "cửa ra" duy nhất để Windows VM truy cập internet. Cổng này chính là bridge `vmbr1` trên pve1, nơi có NAT (biến đổi địa chỉ) giúp traffic từ mạng riêng 172.20.11.x ra được internet.

### A4.1 Đặt IP tĩnh trong Windows

Trong Windows VM (qua Proxmox console):

1. Mở **Settings** → **Network & Internet** → **Change adapter options**
2. Right-click `Ethernet` → **Properties** → **Internet Protocol Version 4 (TCP/IPv4)** → **Properties**
3. Cấu hình:

```text
IP address:      172.20.11.100
Subnet mask:     255.255.255.0
Default gateway: 172.20.11.1
Preferred DNS:   8.8.8.8
Alternate DNS:   8.8.4.4
```

4. OK → Close

### A4.2 Kiểm tra kết nối

Trong Windows Command Prompt:

```cmd
ipconfig
ping 172.20.11.1
ping 8.8.8.8
ping google.com
```

Tất cả phải thành công. Nếu `ping 172.20.11.1` OK nhưng `ping 8.8.8.8` fail, kiểm tra NAT rules trên `pve1`.

Từ `pve1`:

```bash
ping -c 2 172.20.11.100
```

Phải thành công.

---

## A5. Bật Remote Desktop (RDP) trên Windows

> **RDP là gì?** Remote Desktop Protocol — giao thức của Microsoft cho phép điều khiển máy Windows từ xa. Bạn thấy màn hình, dùng chuột, gõ bàn phím — y như đang ngồi trước máy tính đó.
>
> **Tại sao cần bật?** Guacamole dùng RDP để kết nối vào Windows VM. Nếu RDP tắt, Guacamole "gõ cửa" nhưng Windows không mở = không kết nối được.
>
> **Port 3389** là cửa mặc định của RDP. Giống như cổng 80 cho web, cổng 3389 cho RDP.

### A5.1 Bật RDP

Windows Server 2016 đã bật RDP sẵn theo mặc định. Kiểm tra:

1. **Server Manager** → **Local Server** → **Remote Desktop**: phải là `Enabled`
2. Nếu `Disabled`: click vào → chọn `Allow remote connections to this computer` → OK

### A5.2 Tắt Network Level Authentication (NLA) cho Guacamole

> **NLA là gì?** Là lớp xác thực bổ sung của RDP — yêu cầu client phải chứng minh danh tính TRƯỚC KHI thấy màn hình đăng nhập. Nhiều phần mềm RDP client cũ (hoặc Guacamole với security mode "Any") không hỗ trợ NLA tốt.
>
> **Tại sao tắt?** Guacamole kết nối đơn giản hơn khi NLA tắt. Trong môi trường lab nội bộ (mạng riêng, không expose internet trực tiếp) thì an toàn.

1. Mở **Run** (`Win+R`) → gõ `sysdm.cpl` → tab **Remote**
2. Bỏ check `Allow connections only from computers running Remote Desktop with Network Level Authentication`
3. OK

### A5.3 Mở firewall cho RDP

> **Tại sao?** Windows Firewall mặc định chặn hầu hết kết nối đến. Dù RDP đã bật, nếu firewall block port 3389 thì bên ngoài vẫn không vào được. Bước này "mở cửa" port 3389 trên firewall Windows.

Trong Windows PowerShell (Run as Administrator):

```powershell
Enable-NetFirewallRule -DisplayGroup "Remote Desktop"
```

Kiểm tra:

```powershell
Get-NetFirewallRule -DisplayGroup "Remote Desktop" | Select-Object DisplayName, Enabled
```

Tất cả rule Remote Desktop phải `Enabled = True`.

### A5.4 Test RDP từ pve1

Từ `pve1`:

```bash
sudo apt install -y nmap
nmap -p 3389 172.20.11.100
```

Expected:

```text
PORT     STATE SERVICE
3389/tcp open  ms-wrd-rdp
```

---

## A6. Cài QEMU Guest Agent (xác nhận)

> **Bước này làm gì?** Kiểm tra xem Guest Agent hoạt động chưa — Proxmox có lấy được IP của Windows VM không. Đây là bước xác nhận quan trọng vì MalSec hoàn toàn phụ thuộc vào Guest Agent để biết IP từng VM sinh viên.

Từ `pve1`, kiểm tra Guest Agent trả IP:

```bash
sudo qm agent 9000 network-get-interfaces
```

Expected: thấy interface với IP `172.20.11.100`.

---

## A7. Tạo Template từ VM (tùy chọn, chuẩn bị cho MalSec)

> **Template là gì?** Là "bản gốc" đã cài đặt sẵn mọi thứ (Windows + driver + cấu hình). Khi sinh viên bấm "Start Lab", MalSec sẽ "copy" (clone) từ template này ra 1 VM riêng cho sinh viên đó. 30 sinh viên = 30 bản copy, mỗi người có máy riêng.
>
> **Linked Clone vs Full Clone:**
> - **Linked Clone** (mặc định MalSec): Chỉ lưu phần KHÁC BIỆT so với template. Nhanh, tiết kiệm ổ cứng. 30 VM chỉ tốn thêm vài GB.
> - **Full Clone**: Copy nguyên ổ cứng 32GB cho mỗi sinh viên. 30 VM = 30 × 32GB = 960GB. Chậm hơn nhưng độc lập hoàn toàn.
>
> **Chưa cần làm bước này nếu chỉ muốn test RDP trước.**

Nếu muốn dùng VM này làm template cho MalSec sau này:

```bash
# Tắt VM trước
sudo qm shutdown 9000

# Chờ VM tắt hẳn
sudo qm wait 9000

# Gỡ CD-ROM
sudo qm set 9000 --ide2 none
sudo qm set 9000 --ide3 none

# Convert thành template
sudo qm template 9000
```

> **Lưu ý:** Sau khi convert thành template, không thể boot VM này nữa. Chỉ có thể clone. Nếu chưa muốn convert, **bỏ qua bước này** và giữ VM 9000 chạy bình thường để test RDP.

---

# PHẦN B: DỰNG GUACAMOLE TRÊN GCP

## B1. Tạo GCP VM cho Guacamole

> **Bước này làm gì?** Tạo một máy ảo mới trên Google Cloud (tên `guac-server`) để chạy Apache Guacamole. Đặt IP `10.10.0.50` trong cùng mạng VPC với các node Proxmox (10.10.0.11/.12/.13).
>
> **Tại sao cần server riêng?** Guacamole là ứng dụng web — cần server riêng để chạy. Không nên chạy trên Proxmox node vì sẽ chiếm tài nguyên và có thể conflict port.
>
> **Tại sao phải cùng VPC?** Để Guacamole "nhìn thấy" Proxmox node qua mạng nội bộ. Nếu khác VPC, phải cấu hình VPN/peering phức tạp hơn nhiều.

Chạy từ **Cloud Shell**:

### B1.1 Tạo VM

```bash
gcloud compute instances create guac-server \
  --zone=asia-southeast1-b \
  --machine-type=e2-medium \
  --image-family=debian-13 \
  --image-project=debian-cloud \
  --boot-disk-size=20GB \
  --boot-disk-type=pd-balanced \
  --network-interface=network=pve-vpc,subnet=pve-subnet,private-network-ip=10.10.0.50 \
  --tags=pve-node,guac-server \
  --can-ip-forward
```

Kiểm tra:

```bash
gcloud compute instances describe guac-server \
  --zone=asia-southeast1-b \
  --format='yaml(name,networkInterfaces[0].networkIP,status)'
```

Expected:

```text
name: guac-server
networkInterfaces[0].networkIP: 10.10.0.50
status: RUNNING
```

### B1.2 Kiểm tra connectivity

SSH vào `guac-server`:

```bash
gcloud compute ssh guac-server \
  --zone=asia-southeast1-b \
  --tunnel-through-iap
```

Ping các Proxmox node:

```bash
ping -c 2 10.10.0.11
ping -c 2 10.10.0.12
ping -c 2 10.10.0.13
```

Tất cả phải thành công (đã có firewall rule `pve-internal` cho phép traffic trong `10.10.0.0/24`).

---

## B2. Thêm GCP route cho guest subnet 172.20.11.0/24

> **Bước này làm gì?** Nói cho Google Cloud biết: "Khi ai đó muốn gửi dữ liệu đến địa chỉ `172.20.11.x`, hãy chuyển qua máy `pve1`".
>
> **Tại sao cần?** Mạng `172.20.11.0/24` là mạng riêng bên trong Proxmox (bridge `vmbr1`). Google Cloud không biết mạng này tồn tại — nó chỉ biết mạng `10.10.0.0/24` (VPC subnet). Route này giống **biển chỉ đường trên cao tốc**: "Muốn đến 172.20.11.x → rẽ vào pve1".
>
> **Ví dụ thực tế:** Guacamole (10.10.0.50) muốn RDP đến Windows VM (172.20.11.100). Không có route, packet đi lạc. Có route, GCP chuyển packet đến pve1 (10.10.0.11), pve1 forward vào bridge vmbr1, đến Windows VM.
>
> **`--can-ip-forward` ở bước tạo pve1 là gì?** Flag này cho phép pve1 nhận và chuyển tiếp packet không phải của mình (packet đến 172.20.11.x). Không có flag này, GCP sẽ drop packet.

Chạy từ **Cloud Shell**:

```bash
gcloud compute routes create route-guest-via-pve1 \
  --network=pve-vpc \
  --destination-range=172.20.11.0/24 \
  --next-hop-instance=pve1 \
  --next-hop-instance-zone=asia-southeast1-b \
  --priority=100
```

Kiểm tra:

```bash
gcloud compute routes list \
  --filter='network:pve-vpc AND destRange:172.20.11.0/24' \
  --format='table(name,destRange,nextHopInstance,priority)'
```

Expected:

```text
NAME                    DEST_RANGE       NEXT_HOP_INSTANCE  PRIORITY
route-guest-via-pve1    172.20.11.0/24   .../pve1            100
```

### B2.1 Test route từ guac-server

SSH vào `guac-server`:

```bash
ping -c 2 172.20.11.1
```

Expected: reply từ `172.20.11.1` (bridge vmbr1 trên pve1).

```bash
ping -c 2 172.20.11.100
```

Expected: reply từ `172.20.11.100` (Windows VM) — chỉ thành công nếu Windows đã tắt ICMP block (mặc định Windows block ping, có thể bỏ qua bước này).

> Nếu Windows block ping nhưng RDP port vẫn mở, đó là bình thường. Dùng `nmap` hoặc `nc` để test port 3389:

```bash
sudo apt install -y nmap
nmap -p 3389 172.20.11.100
```

Expected:

```text
PORT     STATE SERVICE
3389/tcp open  ms-wrd-rdp
```

---

## B3. Cài Docker trên guac-server

> **Docker là gì?** Là công cụ chạy ứng dụng trong "container" — giống hộp đóng gói sẵn chứa ứng dụng + tất cả thứ nó cần. Không cần cài từng thành phần một cách thủ công.
>
> **Tại sao dùng Docker?** Guacamole gồm 3 thành phần (web app + daemon + database). Cài thủ công rất phức tạp. Dùng Docker chỉ cần 1 file cấu hình + 1 lệnh là chạy xong cả 3.

SSH vào `guac-server`:

```bash
gcloud compute ssh guac-server \
  --zone=asia-southeast1-b \
  --tunnel-through-iap
```

### B3.1 Cài Docker Engine

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
```

### B3.2 Thêm user vào docker group

```bash
sudo usermod -aG docker $USER
```

Logout và SSH lại:

```bash
exit
```

```bash
gcloud compute ssh guac-server \
  --zone=asia-southeast1-b \
  --tunnel-through-iap
```

Kiểm tra:

```bash
docker --version
docker compose version
docker run --rm hello-world
```

---

## B4. Triển khai Apache Guacamole bằng Docker Compose

> **Apache Guacamole là gì?** Là phần mềm mã nguồn mở biến kết nối RDP/VNC/SSH thành trang web HTML5. Người dùng chỉ cần browser, không cần cài bất kỳ phần mềm client nào.
>
> **3 container Guacamole gồm:**
> - **guac-db** (PostgreSQL): Database lưu user, connection, quyền — giống "sổ danh bạ" của Guacamole
> - **guacd**: Daemon (chương trình chạy nền) xử lý kết nối RDP/VNC/SSH thực tế — là "bộ phiên dịch" giữa browser và Windows RDP
> - **guacamole**: Giao diện web cho người dùng — trang web bạn mở trong browser
>
> **Docker Compose là gì?** Là file YAML mô tả cách chạy nhiều container cùng lúc. Thay vì gõ 3 lệnh Docker riêng lẻ, gõ 1 lệnh `docker compose up` là chạy cả 3.

### B4.1 Tạo thư mục project

```bash
mkdir -p ~/guacamole && cd ~/guacamole
```

### B4.2 Khởi tạo database schema

> **Làm gì?** Tạo file SQL chứa cấu trúc bảng (tables) mà Guacamole cần trong database. Khi PostgreSQL khởi động lần đầu, nó tự chạy file này để tạo bảng. Giống "bản thiết kế nhà" — phải có trước khi xây.

```bash
docker run --rm guacamole/guacamole:1.5.5 /opt/guacamole/bin/initdb.sh --postgresql > initdb.sql
```

### B4.3 Tạo file docker-compose.yml

```bash
cat > docker-compose.yml <<'EOF'
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
    ports:
      - "8080:8080"
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

> **Quan trọng:** Đổi `guac_password_change_me` thành password thực tế trong cả `guac-db` và `guacamole` service.

### B4.4 Khởi động Guacamole

```bash
docker compose up -d
```

Kiểm tra:

```bash
docker compose ps
```

Expected: 3 container đều `running`.

```bash
docker compose logs guacamole | tail -20
```

Chờ thấy `Server startup in [XXXXX] milliseconds` nghĩa là Guacamole đã sẵn sàng.

### B4.5 Test truy cập local

```bash
curl -s -o /dev/null -w "%{http_code}" http://localhost:8080/guacamole/
```

Expected: `200` hoặc `302` (redirect đến login).

---

## B5. Expose Guacamole Web UI

> **Bước này làm gì?** Mở "cánh cửa" trên Google Cloud firewall để bạn truy cập Guacamole từ browser ở nhà/laptop. Mặc định GCP chặn mọi kết nối từ bên ngoài vào — phải tạo firewall rule cho phép port 8080.
>
> **Tại sao cần public IP?** Máy `guac-server` có 2 IP: internal (10.10.0.50) chỉ dùng trong VPC, và external (34.x.x.x) dùng từ internet. Bạn cần external IP để mở trong browser.

### B5.1 Thêm firewall rule cho Guacamole

Chạy từ **Cloud Shell**:

```bash
gcloud compute firewall-rules create guac-web-public \
  --network=pve-vpc \
  --direction=INGRESS \
  --priority=1000 \
  --source-ranges=0.0.0.0/0 \
  --target-tags=guac-server \
  --allow=tcp:8080
```

> `0.0.0.0/0` cho phép truy cập từ mọi nơi. Trong production nên giới hạn source IP hoặc dùng VPN/IAP.

### B5.2 Lấy external IP của guac-server

```bash
gcloud compute instances describe guac-server \
  --zone=asia-southeast1-b \
  --format='get(networkInterfaces[0].accessConfigs[0].natIP)'
```

Lưu IP:

```bash
export GUAC_PUBLIC_IP="$(
  gcloud compute instances describe guac-server \
    --zone=asia-southeast1-b \
    --format='get(networkInterfaces[0].accessConfigs[0].natIP)'
)"
echo "Guacamole URL: http://$GUAC_PUBLIC_IP:8080/guacamole/"
```

### B5.3 Truy cập Guacamole

Mở browser:

```text
http://GUAC_PUBLIC_IP:8080/guacamole/
```

Đăng nhập mặc định:

```text
Username: guacadmin
Password: guacadmin
```

> **Quan trọng:** Đổi password `guacadmin` ngay sau lần đăng nhập đầu tiên!

---

## B6. Cấu hình RDP connection trong Guacamole

> **Bước này làm gì?** Tạo "danh bạ kết nối" trong Guacamole: nói cho nó biết Windows VM ở IP nào, port nào, đăng nhập bằng user/password gì. Khi bạn click vào connection, Guacamole tự kết nối RDP theo thông tin đã lưu.
>
> **Security mode "Any"** nghĩa là Guacamole sẽ thử mọi phương thức bảo mật RDP (NLA, TLS, RDP) cho đến khi cái nào hoạt động.
>
> **Ignore server certificate** bỏ qua cảnh báo certificate self-signed — Windows tạo certificate RDP tự ký, không phải certificate từ CA uy tín, nên Guacamole sẽ cảnh báo nếu không bật tùy chọn này.

### B6.1 Tạo connection mới

1. Đăng nhập Guacamole → `Settings` (góc phải trên) → tab `Connections`
2. Click `New Connection`

### B6.2 Điền thông tin

**Name & Protocol:**

```text
Name:     win-test-rdp
Protocol: RDP
```

**Parameters — Network:**

```text
Hostname: 172.20.11.100
Port:     3389
```

**Parameters — Authentication:**

```text
Username: Administrator
Password: (password bạn đặt khi cài Windows Server)
Security mode: Any
Ignore server certificate: ☑ (checked)
```

**Parameters — Display (tùy chọn, tăng trải nghiệm):**

```text
Enable wallpaper:           ☑
Enable theming:             ☑
Enable font smoothing:      ☑
Enable full window drag:    ☑
Enable desktop composition: ☑
```

3. Click `Save`

### B6.3 Test kết nối RDP

Quay lại trang chủ Guacamole → click vào `win-test-rdp`.

Expected: thấy desktop Windows trong browser.

---

## B7. Troubleshooting

> **Phần này là gì?** Danh sách lỗi thường gặp và cách sửa. Nếu mọi thứ hoạt động rồi, bỏ qua phần này.

### B7.1 Guacamole không kết nối được Windows VM

**Kiểm tra route:**

Từ `guac-server`:

```bash
# Test kết nối TCP đến RDP port
nc -zv 172.20.11.100 3389 -w 5
```

Nếu timeout, kiểm tra:

1. GCP route đã tạo chưa:
```bash
# Từ Cloud Shell
gcloud compute routes list --filter='destRange:172.20.11.0/24'
```

2. IP forwarding trên pve1:
```bash
# Trên pve1
sudo /usr/sbin/sysctl net.ipv4.ip_forward
```

3. iptables FORWARD rules trên pve1:
```bash
# Trên pve1
sudo iptables -S FORWARD
```

Phải có:
```text
-A FORWARD -i vmbr1 -o ens4 -j ACCEPT
-A FORWARD -i ens4 -o vmbr1 -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT
```

4. Thêm rule cho phép traffic từ `ens4` vào `vmbr1` cho dải `10.10.0.0/24`:

> **Giải thích:** Ở guide trước, iptables FORWARD trên pve1 chỉ cho phép:
> - `vmbr1 → ens4`: Guest VM ra internet (outbound)
> - `ens4 → vmbr1` với `RELATED,ESTABLISHED`: Chỉ packet "trả lời" (return traffic)
>
> Nhưng khi guac-server muốn **chủ động** RDP vào guest VM, đó là traffic **mới** từ ens4 → vmbr1 — bị chặn!
>
> Rule dưới đây cho phép traffic mới từ dải VPC (10.10.0.0/24) vào guest subnet (172.20.11.0/24).

Kiểm tra default policy:

```bash
sudo iptables -L FORWARD | head -1
```

Nếu thấy `Chain FORWARD (policy DROP)`, cần thêm rule:

```bash
# Cho phép traffic từ VPC subnet vào guest subnet
sudo iptables -A FORWARD -i ens4 -o vmbr1 -s 10.10.0.0/24 -d 172.20.11.0/24 -j ACCEPT
```

Để rule persist qua reboot, thêm vào `/etc/network/interfaces` phần `vmbr1`:

```bash
sudo tee -a /etc/network/interfaces > /dev/null <<'EOF_EXTRA_FW'

    # Allow VPC hosts (like guac-server) to reach guest VMs directly
    post-up /bin/sh -c 'iptables -C FORWARD -i ens4 -o vmbr1 -s 10.10.0.0/24 -d 172.20.11.0/24 -j ACCEPT 2>/dev/null || iptables -A FORWARD -i ens4 -o vmbr1 -s 10.10.0.0/24 -d 172.20.11.0/24 -j ACCEPT'
    post-down /bin/sh -c 'iptables -C FORWARD -i ens4 -o vmbr1 -s 10.10.0.0/24 -d 172.20.11.0/24 -j ACCEPT 2>/dev/null && iptables -D FORWARD -i ens4 -o vmbr1 -s 10.10.0.0/24 -d 172.20.11.0/24 -j ACCEPT || true'
EOF_EXTRA_FW
```

### B7.2 Windows VM không có internet

Trong Windows VM:

```cmd
ipconfig
ping 172.20.11.1
ping 8.8.8.8
```

Nếu `ping 172.20.11.1` fail → kiểm tra IP/gateway config.
Nếu `ping 172.20.11.1` OK nhưng `ping 8.8.8.8` fail → kiểm tra NAT trên pve1:

```bash
# Trên pve1
sudo iptables -t nat -S POSTROUTING
```

Phải có:
```text
-A POSTROUTING -s 172.20.11.0/24 ! -d 172.20.0.0/16 -o ens4 -j MASQUERADE
```

### B7.3 Guacamole hiện "Connection closed" ngay lập tức

Thường do Windows chưa bật RDP hoặc firewall block.

Kiểm tra từ `guac-server`:

```bash
nmap -p 3389 172.20.11.100
```

Nếu `filtered` hoặc `closed` → vào Windows bật RDP và mở firewall.

### B7.4 Guacamole hiện màn hình đen

- Kiểm tra `Security mode` trong connection settings → thử đổi sang `Any` hoặc `RDP`
- Bật `Ignore server certificate`
- Kiểm tra username/password chính xác

---

# PHẦN C: CHECKPOINT — SẴN SÀNG CHO MALSEC

## C1. Kiểm tra trạng thái cuối

### Trên pve1:

```bash
# Cluster health
sudo pvecm status
sudo pvecm nodes

# VM đang chạy
sudo qm list

# Network
ip -br addr
sudo iptables -S FORWARD
sudo iptables -t nat -S POSTROUTING
```

### Trên guac-server:

```bash
# Docker containers
docker compose ps

# Connectivity đến Windows VM
nmap -p 3389 172.20.11.100

# Guacamole health
curl -s -o /dev/null -w "%{http_code}" http://localhost:8080/guacamole/
```

### Trong browser:

```text
1. Proxmox UI: https://PVE1_PUBLIC_IP:8006 — VM 9000 win-test running
2. Guacamole:  http://GUAC_PUBLIC_IP:8080/guacamole/ — RDP vào Windows thành công
```

## C2. Trạng thái hệ thống mong đợi

```text
GCP VPC: pve-vpc (10.10.0.0/24)
│
├── pve1 10.10.0.11 (Proxmox VE 9.2, cluster pve-malsec)
│   ├── vmbr1 172.20.11.1/24 (NAT + FORWARD)
│   └── VM 9000: win-test (172.20.11.100, RDP OK, Guest Agent OK)
│
├── pve2 10.10.0.12 (Proxmox VE 9.2, cluster member)
├── pve3 10.10.0.13 (Proxmox VE 9.2, cluster member)
│
└── guac-server 10.10.0.50
    └── Docker: Apache Guacamole 1.5.5
        ├── guac-db (PostgreSQL 16)
        ├── guacd (proxy daemon)
        └── guacamole (web app :8080)
            └── Connection: win-test-rdp → 172.20.11.100:3389 ✓

GCP Route: 172.20.11.0/24 → next-hop pve1
```

## C3. Bước tiếp theo — Chuẩn bị dựng MalSec LMS

> **Từ đây trở đi là gì?** Bạn đã có "nền tảng hạ tầng" hoạt động: Windows VM + Guacamole RDP OK. Bước tiếp là dựng phần mềm MalSec LMS (web app quản lý lab) và kết nối nó vào hạ tầng này.

Sau khi đã xác nhận RDP hoạt động end-to-end, các bước tiếp theo để triển khai MalSec:

### 1. Chọn server chạy MalSec LMS

MalSec LMS (Docker Compose: frontend + backend + PostgreSQL) có thể chạy trên:
- **