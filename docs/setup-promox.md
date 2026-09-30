# GCP + Proxmox VE 9.2 Nested Virtualization Lab Setup Guide

> **Mục tiêu:** dựng 3 node Proxmox VE chạy bên trong Google Compute Engine, tạo cluster 3 node, expose Web UI của `pve1`, cấu hình local storage, và tạo private bridge/NAT cho nested VM trên `pve1`.
>
> **Điểm dừng của guide:** ngay **trước khi tạo `vm-test01`**.
>
> **Không bao gồm:** các lệnh tạo snapshot.
>
> **Môi trường đã kiểm chứng:** 2026-09-18, Debian 13 (Trixie), Proxmox VE 9.2, kernel `7.0.14-17-pve`, GCP `asia-southeast1-b`.

---

## 0. Kiến trúc cuối cùng của guide

```text
Google Cloud
└── VPC: pve-vpc
    └── Subnet: pve-subnet (10.10.0.0/24)
        ├── pve1  10.10.0.11
        │   └── vmbr1 172.20.11.1/24
        │       └── [nested VM network - chưa tạo VM]
        ├── pve2  10.10.0.12
        └── pve3  10.10.0.13

Proxmox cluster: pve-malsec
Corosync link0: 10.10.0.11 / .12 / .13
```

### Thông số lab

| Thành phần | Giá trị |
|---|---|
| GCP project | thay bằng project của bạn |
| Region | `asia-southeast1` |
| Zone | `asia-southeast1-b` |
| VPC | `pve-vpc` |
| Subnet | `pve-subnet` |
| VPC CIDR | `10.10.0.0/24` |
| pve1 | `10.10.0.11` |
| pve2 | `10.10.0.12` |
| pve3 | `10.10.0.13` |
| Machine type | `n2-standard-2` |
| Boot disk | `50GB pd-balanced` |
| OS | Debian 13 |
| Proxmox | VE 9.2 |
| Cluster | `pve-malsec` |
| Guest subnet pve1 | `172.20.11.0/24` |
| Guest gateway pve1 | `172.20.11.1` |
| GCP MTU | `1460` |

---

# 1. Chuẩn bị Google Cloud

Chạy trong **Cloud Shell** hoặc máy đã cài `gcloud`.

## 1.1 Chọn project và bật Compute Engine API

```bash
export PROJECT_ID="YOUR_GCP_PROJECT_ID"
export REGION="asia-southeast1"
export ZONE="asia-southeast1-b"

gcloud config set project "$PROJECT_ID"
gcloud config set compute/region "$REGION"
gcloud config set compute/zone "$ZONE"

gcloud services enable compute.googleapis.com
```

Kiểm tra:

```bash
gcloud config list
gcloud services list --enabled --filter='name:compute.googleapis.com'
```

---

# 2. Tạo VPC và subnet

## 2.1 Tạo custom VPC

```bash
gcloud compute networks create pve-vpc \
  --subnet-mode=custom \
  --bgp-routing-mode=regional \
  --mtu=1460
```

Kiểm tra:

```bash
gcloud compute networks describe pve-vpc \
  --format='yaml(name,subnetworks,mtu,routingConfig)'
```

## 2.2 Tạo subnet

```bash
gcloud compute networks subnets create pve-subnet \
  --network=pve-vpc \
  --region=asia-southeast1 \
  --range=10.10.0.0/24
```

Kiểm tra:

```bash
gcloud compute networks subnets describe pve-subnet \
  --region=asia-southeast1 \
  --format='yaml(name,network,ipCidrRange,region)'
```

---

# 3. Firewall của GCP

## 3.1 Cho phép traffic nội bộ giữa 3 Proxmox node

Lab này cho phép toàn bộ TCP, UDP và ICMP bên trong subnet `10.10.0.0/24`.

```bash
gcloud compute firewall-rules create pve-internal \
  --network=pve-vpc \
  --direction=INGRESS \
  --priority=1000 \
  --source-ranges=10.10.0.0/24 \
  --target-tags=pve-node \
  --allow=tcp,udp,icmp
```

Rule này cũng bao phủ traffic Corosync nội bộ.

## 3.2 Cho phép SSH qua IAP

```bash
gcloud compute firewall-rules create pve-iap-ssh \
  --network=pve-vpc \
  --direction=INGRESS \
  --priority=1000 \
  --source-ranges=35.235.240.0/20 \
  --target-tags=pve-node \
  --allow=tcp:22
```

Kiểm tra:

```bash
gcloud compute firewall-rules list \
  --filter='network:pve-vpc' \
  --format='table(name,direction,sourceRanges,allowed,targetTags)'
```

> Không cần public-open port 22. SSH quản trị dùng IAP.

---

# 4. Tạo 3 GCP VM cho Proxmox

Ba VM dùng nested virtualization và GCP IP forwarding.

## 4.1 pve1

```bash
gcloud compute instances create pve1 \
  --zone=asia-southeast1-b \
  --machine-type=n2-standard-2 \
  --image-family=debian-13 \
  --image-project=debian-cloud \
  --boot-disk-size=50GB \
  --boot-disk-type=pd-balanced \
  --network-interface=network=pve-vpc,subnet=pve-subnet,private-network-ip=10.10.0.11 \
  --tags=pve-node \
  --enable-nested-virtualization \
  --can-ip-forward
```

## 4.2 pve2

```bash
gcloud compute instances create pve2 \
  --zone=asia-southeast1-b \
  --machine-type=n2-standard-2 \
  --image-family=debian-13 \
  --image-project=debian-cloud \
  --boot-disk-size=50GB \
  --boot-disk-type=pd-balanced \
  --network-interface=network=pve-vpc,subnet=pve-subnet,private-network-ip=10.10.0.12 \
  --tags=pve-node \
  --enable-nested-virtualization \
  --can-ip-forward
```

## 4.3 pve3

```bash
gcloud compute instances create pve3 \
  --zone=asia-southeast1-b \
  --machine-type=n2-standard-2 \
  --image-family=debian-13 \
  --image-project=debian-cloud \
  --boot-disk-size=50GB \
  --boot-disk-type=pd-balanced \
  --network-interface=network=pve-vpc,subnet=pve-subnet,private-network-ip=10.10.0.13 \
  --tags=pve-node \
  --enable-nested-virtualization \
  --can-ip-forward
```

Nếu không dùng `no-address`, GCP sẽ cấp ephemeral external IPv4 mặc định. Trong lab này `pve1` được promote thành static IP ở bước sau.

Kiểm tra cả ba VM:

```bash
gcloud compute instances list \
  --filter='name~^pve[123]$' \
  --format='table(name,zone,machineType.basename(),networkInterfaces[0].networkIP,networkInterfaces[0].accessConfigs[0].natIP,status)'
```

Kiểm tra nested virtualization / IP forwarding:

```bash
for n in pve1 pve2 pve3; do
  echo "===== $n ====="
  gcloud compute instances describe "$n" \
    --zone=asia-southeast1-b \
    --format='yaml(name,canIpForward,advancedMachineFeatures.enableNestedVirtualization,networkInterfaces)'
done
```

---

# 5. SSH vào từng node

Ví dụ `pve1`:

```bash
gcloud compute ssh pve1 \
  --zone=asia-southeast1-b \
  --tunnel-through-iap
```

Tương tự:

```bash
gcloud compute ssh pve2 \
  --zone=asia-southeast1-b \
  --tunnel-through-iap
```

```bash
gcloud compute ssh pve3 \
  --zone=asia-southeast1-b \
  --tunnel-through-iap
```

---

# 6. Hostname và `/etc/hosts`

Thực hiện đúng hostname trên từng node:

```bash
# trên pve1
sudo hostnamectl set-hostname pve1
```

```bash
# trên pve2
sudo hostnamectl set-hostname pve2
```

```bash
# trên pve3
sudo hostnamectl set-hostname pve3
```

Trên **cả 3 node**, cấu hình `/etc/hosts`:

```bash
sudo tee /etc/hosts > /dev/null <<'EOF_HOSTS'
127.0.0.1 localhost

10.10.0.11 pve1.pve.test pve1
10.10.0.12 pve2.pve.test pve2
10.10.0.13 pve3.pve.test pve3

::1 localhost ip6-localhost ip6-loopback
ff02::1 ip6-allnodes
ff02::2 ip6-allrouters
EOF_HOSTS
```

Kiểm tra:

```bash
hostname
hostname --fqdn
hostname --ip-address

getent ahostsv4 pve1
getent ahostsv4 pve2
getent ahostsv4 pve3
```

Ví dụ trên `pve1`, mong đợi:

```text
hostname              -> pve1
hostname --fqdn       -> pve1.pve.test
hostname --ip-address -> 10.10.0.11
```

---

# 7. Kiểm tra network, nested virtualization và time sync

Trên từng node:

```bash
ip -br addr
ip route
grep -cw vmx /proc/cpuinfo
nproc
ls -l /dev/kvm
timedatectl status
```

Điểm cần kiểm tra:

```text
ens4       UP  10.10.0.1X/32
/dev/kvm   tồn tại
vmx        > 0
NTP        synchronized
```

> `10.10.0.11/32`, `.12/32`, `.13/32` là bình thường trên GCP. Không đổi thành `/24`.

Kiểm tra connectivity giữa các node:

```bash
ping -c 2 pve1
ping -c 2 pve2
ping -c 2 pve3
```

---

# 8. Cài Proxmox VE an toàn trên Debian/GCP

Thực hiện phần này trên **từng node**, lần lượt `pve1`, `pve2`, `pve3`.

## 8.1 Cài công cụ cơ bản

```bash
sudo apt update
sudo apt install -y wget ca-certificates
```

## 8.2 Thêm Proxmox archive key

```bash
sudo wget \
  https://enterprise.proxmox.com/debian/proxmox-archive-keyring-trixie.gpg \
  -O /usr/share/keyrings/proxmox-archive-keyring.gpg
```

Kiểm tra SHA256:

```bash
sha256sum /usr/share/keyrings/proxmox-archive-keyring.gpg
```

Giá trị đã kiểm chứng trong lab:

```text
136673be77aba35dcce385b28737689ad64fd785a797e57897589aed08db6e45
```

## 8.3 Thêm `pve-no-subscription` repository

```bash
sudo tee /etc/apt/sources.list.d/proxmox.sources > /dev/null <<'EOF_PVE_REPO'
Types: deb
URIs: http://download.proxmox.com/debian/pve
Suites: trixie
Components: pve-no-subscription
Signed-By: /usr/share/keyrings/proxmox-archive-keyring.gpg
EOF_PVE_REPO
```

---

# 9. QUAN TRỌNG: giữ Debian/GCP GRUB

## Vì sao?

Trong lab này, cài kernel Proxmox theo cách mặc định đã từng làm APT thay Debian/GCP GRUB bằng các gói GRUB từ Proxmox, sau reboot GCP VM dừng ở:

```text
grub>
```

Giải pháp đã kiểm chứng:

1. chặn các package GRUB từ `download.proxmox.com`;
2. giữ Debian GRUB;
3. cài PVE bằng `--no-install-recommends`.

> Đây là workaround **riêng cho boot chain của lab Debian-on-GCP này**, không phải yêu cầu chung cho mọi Proxmox host.

## 9.1 Pin GRUB của Proxmox

```bash
sudo tee /etc/apt/preferences.d/keep-gcp-grub > /dev/null <<'EOF_GRUB_PIN'
Package: grub-common grub2-common grub-pc grub-pc-bin grub-efi-amd64 grub-efi-amd64-bin grub-efi-amd64-signed grub-efi-amd64-unsigned proxmox-grub
Pin: origin "download.proxmox.com"
Pin-Priority: -1
EOF_GRUB_PIN
```

```bash
sudo apt update
```

Kiểm tra:

```bash
apt-cache policy grub-common
```

Cần thấy Debian GRUB là `Installed/Candidate`, còn bản `+pmx...` từ Proxmox có priority `-1`.

Ví dụ:

```text
Installed: 2.12-9+deb13u2
Candidate: 2.12-9+deb13u2
...
2.12-9+pmx2  -1
```

## 9.2 Simulation trước khi cài kernel

```bash
apt-get -s --no-install-recommends install proxmox-default-kernel
```

Không được thấy các package GRUB `+pmx` chuẩn bị upgrade/install.

---

# 10. Backup boot config cục bộ trước khi cài kernel

> Đây **không phải snapshot**. Chỉ backup boot files bên trong VM.

```bash
sudo tar -czf /root/gcp-boot-before-pve.tar.gz \
  /etc/default/grub \
  /boot/grub \
  /boot/efi/EFI
```

Lưu checksum EFI:

```bash
sudo sha256sum \
  /boot/efi/EFI/BOOT/BOOTX64.EFI \
  /boot/efi/EFI/BOOT/grubx64.efi \
  /boot/efi/EFI/debian/grubx64.efi \
  /boot/efi/EFI/debian/shimx64.efi \
  /boot/efi/EFI/debian/grub.cfg \
  | tee ~/efi-before-pve.txt
```

---

# 11. Cài Proxmox kernel

```bash
sudo apt-get install --no-install-recommends proxmox-default-kernel
```

Kiểm tra kernel mới:

```bash
ls -lh /boot/vmlinuz* /boot/initrd.img*
```

Kiểm tra Debian GRUB vẫn còn:

```bash
dpkg-query -W \
  grub-common \
  grub2-common \
  grub-efi-amd64-bin \
  grub-efi-amd64-signed
```

Không được thấy version `+pmx...`.

Kiểm tra EFI không bị đổi:

```bash
sudo sha256sum \
  /boot/efi/EFI/BOOT/BOOTX64.EFI \
  /boot/efi/EFI/BOOT/grubx64.efi \
  /boot/efi/EFI/debian/grubx64.efi \
  /boot/efi/EFI/debian/shimx64.efi \
  /boot/efi/EFI/debian/grub.cfg \
  | tee ~/efi-after-pve.txt

diff -u ~/efi-before-pve.txt ~/efi-after-pve.txt
```

Nếu không có output từ `diff` là đúng.

Regenerate GRUB config bằng Debian GRUB:

```bash
sudo update-grub
```

Kiểm tra PVE kernel có entry:

```bash
grep -n 'pve' /boot/grub/grub.cfg | head -20
```

---

# 12. Reboot vào Proxmox kernel

```bash
sudo reboot
```

SSH lại bằng IAP, rồi kiểm tra:

```bash
uname -r
cat /proc/version

ls -l /dev/kvm
grep -cw vmx /proc/cpuinfo

ip -br addr
ip route
```

Kernel mong đợi trong lab:

```text
7.0.14-17-pve
```

Connectivity check:

```bash
ping -c 2 pve1
ping -c 2 pve2
ping -c 2 pve3
```

---

# 13. Cài full Proxmox VE

Simulation:

```bash
apt-get -s --no-install-recommends install proxmox-ve | grep '^Remv'
```

Nếu không có package quan trọng bị remove, cài:

```bash
sudo apt-get install --no-install-recommends proxmox-ve
```

Kiểm tra:

```bash
pveversion
```

```bash
sudo systemctl is-active \
  pve-cluster \
  pvedaemon \
  pveproxy \
  pvestatd
```

Tất cả nên trả về:

```text
active
```

Kiểm tra `/etc/pve`:

```bash
mount | grep /etc/pve
sudo ls -la /etc/pve
```

Kiểm tra API/Web UI port:

```bash
sudo ss -lntp | grep ':8006'
```

```bash
curl -k -I https://127.0.0.1:8006
```

Nếu `curl -I` trả:

```text
HTTP/1.1 501 method 'HEAD' not available
Server: pve-api-daemon/...
```

thì HTTPS/API đã reachable.

Kiểm tra GRUB một lần nữa:

```bash
apt-cache policy grub-common
```

---

# 14. Đặt root password trên pve1

Cần cho Web UI `root@pam` và cluster join.

Trên `pve1`:

```bash
sudo passwd root
```

> Không cần bật SSH password login cho root. SSH quản trị vẫn dùng IAP/user hiện tại.

---

# 15. Public Proxmox Web UI trên pve1

## 15.1 Xem external IP hiện tại

Chạy từ Cloud Shell:

```bash
gcloud compute instances describe pve1 \
  --zone=asia-southeast1-b \
  --format='get(networkInterfaces[0].accessConfigs[0].natIP)'
```

Lưu IP:

```bash
export PVE1_PUBLIC_IP="$(
  gcloud compute instances describe pve1 \
    --zone=asia-southeast1-b \
    --format='get(networkInterfaces[0].accessConfigs[0].natIP)'
)"

echo "$PVE1_PUBLIC_IP"
```

## 15.2 Promote ephemeral external IP thành static regional IP

```bash
gcloud compute addresses create pve1-public-ip \
  --region=asia-southeast1 \
  --addresses="$PVE1_PUBLIC_IP"
```

Kiểm tra:

```bash
gcloud compute addresses describe pve1-public-ip \
  --region=asia-southeast1
```

## 15.3 Thêm tag cho Web UI

```bash
gcloud compute instances add-tags pve1 \
  --zone=asia-southeast1-b \
  --tags=pve-ui-public
```

## 15.4 Mở TCP/8006

```bash
gcloud compute firewall-rules create pve-ui-public \
  --network=pve-vpc \
  --direction=INGRESS \
  --priority=1000 \
  --source-ranges=0.0.0.0/0 \
  --target-tags=pve-ui-public \
  --allow=tcp:8006
```

Kiểm tra:

```bash
gcloud compute firewall-rules describe pve-ui-public
```

Truy cập:

```text
https://PVE1_PUBLIC_IP:8006
```

> Certificate self-signed sẽ gây browser warning.
>
> `0.0.0.0/0` chỉ phù hợp khi thật sự cần public UI. Với production nên giới hạn source IP, dùng VPN/access proxy, domain + TLS, và 2FA.

---

# 16. Hardening Web UI

Phần này đã thực hiện bằng GUI:

```text
Datacenter
└── Permissions
    ├── Users
    └── Two Factor Authentication
```

Tạo:

```text
pveadmin@pve
Role: Administrator
Path: /
Propagate: enabled
TOTP: enabled
```

Dùng `pveadmin@pve + TOTP` cho thao tác GUI hàng ngày.

Giữ `root@pam` cho recovery / low-level admin.

---

# 17. Tạo Proxmox cluster

Điều kiện trước khi join:

```text
pve1 10.10.0.11
pve2 10.10.0.12
pve3 10.10.0.13
```

Cả ba:

```bash
pveversion
```

và ping lẫn nhau thành công.

> Node chuẩn bị join cluster không nên có guest/config cần giữ trong `/etc/pve`, vì cấu hình cluster sẽ thay thế config local của node join.

## 17.1 Tạo cluster trên pve1

Trên `pve1`:

```bash
sudo pvecm create pve-malsec --link0 10.10.0.11
```

Kiểm tra:

```bash
sudo pvecm status
sudo pvecm nodes
sudo systemctl --no-pager --full status corosync
sudo cat /etc/pve/corosync.conf
```

Mong đợi:

```text
Nodes: 1
Quorate: Yes
ring0_addr: 10.10.0.11
```

## 17.2 Join pve2

Trên `pve2`:

```bash
sudo pvecm add 10.10.0.11 --link0 10.10.0.12
```

Nhập password `root` của `pve1`.

Kiểm tra:

```bash
sudo pvecm status
sudo pvecm nodes
sudo cat /etc/pve/corosync.conf
```

Mong đợi:

```text
Nodes: 2
Expected votes: 2
Total votes: 2
Quorum: 2
Quorate: Yes
```

## 17.3 Join pve3

Trên `pve3`:

```bash
sudo pvecm add 10.10.0.11 --link0 10.10.0.13
```

Kiểm tra:

```bash
sudo pvecm status
sudo pvecm nodes
```

Mong đợi:

```text
Nodes: 3
Expected votes: 3
Total votes: 3
Quorum: 2
Quorate: Yes
```

---

# 18. Health check cluster 3 node

Trên `pve1`:

```bash
sudo pvecm status
sudo pvecm nodes
sudo corosync-cfgtool -s
sudo ls /etc/pve/nodes
```

Expected:

```text
pve1
pve2
pve3
```

Corosync:

```text
nodeid 1: localhost
nodeid 2: connected
nodeid 3: connected
```

Kiểm tra warning gần đây:

```bash
sudo journalctl -u corosync \
  --since "10 minutes ago" \
  -p warning \
  --no-pager
```

---

# 19. Cấu hình local storage cho VM images

Vì cài Proxmox trên Debian/GCP disk hiện có, lab này chỉ có directory storage:

```text
/dev/sda1 ext4
/
└── /var/lib/vz
```

Kiểm tra:

```bash
sudo pvesm status
df -h /var/lib/vz
lsblk -f
```

Cho phép `local` chứa VM images, ISO, backups, LXC templates, snippets:

```bash
sudo pvesm set local \
  --content iso,vztmpl,backup,images,rootdir,snippets
```

Kiểm tra:

```bash
sudo cat /etc/pve/storage.cfg
sudo pvesm status
sudo pvesm status --content images
```

Config mong đợi:

```text
dir: local
        path /var/lib/vz
        content backup,snippets,images,iso,rootdir,vztmpl
        prune-backups keep-all=1
```

> `local` có cùng Storage ID trên cluster nhưng `/var/lib/vz` của mỗi node vẫn là disk cục bộ riêng, không phải shared storage.

---

# 20. Guest network plan

Mục tiêu dài hạn:

```text
pve1 vmbr1 -> 172.20.11.1/24
pve2 vmbr1 -> 172.20.12.1/24   [chưa cấu hình ở điểm dừng này]
pve3 vmbr1 -> 172.20.13.1/24   [chưa cấu hình ở điểm dừng này]
```

Guide hiện tại chỉ cấu hình `vmbr1` trên `pve1`.

Flow:

```text
Nested VM
172.20.11.x
    |
    v
vmbr1
172.20.11.1
    |
    v
pve1 NAT/MASQUERADE
    |
    v
ens4
10.10.0.11
    |
    v
GCP VPC / Internet
```

Không bridge trực tiếp `ens4` vào guest bridge.

---

# 21. Bật IPv4 forwarding trên pve1

```bash
sudo tee /etc/sysctl.d/99-proxmox-nested-routing.conf > /dev/null <<'EOF_SYSCTL'
net.ipv4.ip_forward=1
EOF_SYSCTL
```

Apply:

```bash
sudo sysctl --system
```

Kiểm tra:

```bash
sudo /usr/sbin/sysctl net.ipv4.ip_forward
```

Expected:

```text
net.ipv4.ip_forward = 1
```

---

# 22. Cấu hình `vmbr1` trên pve1

## 22.1 Kiểm tra network hiện tại

```bash
sudo cat /etc/network/interfaces
ip -br addr
ip route
```

Trong lab này `ens4` do GCP/Debian quản lý bên ngoài `/etc/network/interfaces`.

**Không thêm `ens4` vào bridge. Không đổi `10.10.0.11/32`.**

Backup file config:

```bash
sudo cp /etc/network/interfaces \
  /etc/network/interfaces.before-vmbr1
```

## 22.2 Final `/etc/network/interfaces` đã kiểm chứng

Nếu file của node giống lab này (chỉ có loopback trước khi thêm `vmbr1`), ghi:

```bash
sudo tee /etc/network/interfaces > /dev/null <<'EOF_INTERFACES'
# interfaces(5) file used by ifup(8) and ifdown(8)

auto lo
iface lo inet loopback

# Private network for nested Proxmox guests
auto vmbr1
iface vmbr1 inet static
    address 172.20.11.1/24
    bridge-ports none
    bridge-stp off
    bridge-fd 0
    mtu 1460

    # NAT guests to GCP Internet.
    # Traffic to 172.20.0.0/16 is deliberately NOT NATed,
    # so future guest subnets on pve2/pve3 can be routed with real source IPs.
    post-up /bin/sh -c 'iptables -t nat -C POSTROUTING -s 172.20.11.0/24 ! -d 172.20.0.0/16 -o ens4 -j MASQUERADE 2>/dev/null || iptables -t nat -A POSTROUTING -s 172.20.11.0/24 ! -d 172.20.0.0/16 -o ens4 -j MASQUERADE'
    post-down /bin/sh -c 'iptables -t nat -C POSTROUTING -s 172.20.11.0/24 ! -d 172.20.0.0/16 -o ens4 -j MASQUERADE 2>/dev/null && iptables -t nat -D POSTROUTING -s 172.20.11.0/24 ! -d 172.20.0.0/16 -o ens4 -j MASQUERADE || true'

    # Guests may initiate traffic toward ens4.
    post-up /bin/sh -c 'iptables -C FORWARD -i vmbr1 -o ens4 -j ACCEPT 2>/dev/null || iptables -A FORWARD -i vmbr1 -o ens4 -j ACCEPT'
    post-down /bin/sh -c 'iptables -C FORWARD -i vmbr1 -o ens4 -j ACCEPT 2>/dev/null && iptables -D FORWARD -i vmbr1 -o ens4 -j ACCEPT || true'

    # Return traffic back to guests.
    post-up /bin/sh -c 'iptables -C FORWARD -i ens4 -o vmbr1 -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT 2>/dev/null || iptables -A FORWARD -i ens4 -o vmbr1 -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT'
    post-down /bin/sh -c 'iptables -C FORWARD -i ens4 -o vmbr1 -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT 2>/dev/null && iptables -D FORWARD -i ens4 -o vmbr1 -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT || true'
EOF_INTERFACES
```

> Các `iptables -C ... || iptables -A ...` làm rule **idempotent**: chạy `ifreload -a` nhiều lần không tạo duplicate rules.

---

# 23. Parse config trước khi apply

```bash
sudo ifquery vmbr1
```

Expected:

```text
auto vmbr1
iface vmbr1 inet static
        address 172.20.11.1/24
        bridge-ports none
        bridge-stp off
        bridge-fd 0
        mtu 1460
        ...
```

Nếu `ifquery` báo lỗi thì không chạy `ifreload`.

---

# 24. Apply guest bridge

```bash
sudo ifreload -a
```

Kiểm tra IP:

```bash
ip -br addr
```

Expected:

```text
lo      UNKNOWN  127.0.0.1/8
ens4    UP       10.10.0.11/32
vmbr1   UNKNOWN  172.20.11.1/24
```

`vmbr1 UNKNOWN` là bình thường khi bridge chưa có guest/tap active.

Kiểm tra route:

```bash
ip route
```

Phải giữ:

```text
default via 10.10.0.1 dev ens4 ...
```

và có thêm:

```text
172.20.11.0/24 dev vmbr1 ... src 172.20.11.1
```

---

# 25. Kiểm tra NAT / forwarding rules

```bash
sudo /usr/sbin/sysctl net.ipv4.ip_forward
```

Expected:

```text
net.ipv4.ip_forward = 1
```

NAT:

```bash
sudo iptables -t nat -S POSTROUTING
```

Expected đúng **1 rule**:

```text
-A POSTROUTING -s 172.20.11.0/24 ! -d 172.20.0.0/16 -o ens4 -j MASQUERADE
```

Forward:

```bash
sudo iptables -S FORWARD
```

Expected đúng **1 bản mỗi rule**:

```text
-A FORWARD -i vmbr1 -o ens4 -j ACCEPT
-A FORWARD -i ens4 -o vmbr1 -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT
```

Có thể chạy lại:

```bash
sudo ifreload -a
```

rồi kiểm tra hai lệnh `iptables` lần nữa. Rule vẫn chỉ có một bản.

---

# 26. Kiểm tra cluster sau khi thay đổi network

```bash
ping -c 2 pve2
ping -c 2 pve3
sudo pvecm status
```

Expected:

```text
Nodes:          3
Expected votes: 3
Total votes:    3
Quorum:         2
Quorate:        Yes
```

Corosync:

```bash
sudo corosync-cfgtool -s
```

Expected:

```text
nodeid 2: connected
nodeid 3: connected
```

---

# 27. Checkpoint cuối — ngay trước khi tạo `vm-test01`

Tại điểm này hệ thống cần có trạng thái:

```text
GCP
├── pve1 10.10.0.11/32
│   ├── Proxmox VE 9.2
│   ├── cluster member
│   ├── local storage /var/lib/vz
│   ├── IPv4 forwarding = 1
│   └── vmbr1 172.20.11.1/24
│       └── NAT -> ens4
│
├── pve2 10.10.0.12/32
│   ├── Proxmox VE 9.2
│   └── cluster member
│
└── pve3 10.10.0.13/32
    ├── Proxmox VE 9.2
    └── cluster member

Cluster
├── name: pve-malsec
├── nodes: 3
├── total votes: 3
├── quorum: 2
└── quorate: yes

Nested guest network prepared on pve1:
172.20.11.0/24
gateway = 172.20.11.1
MTU = 1460
```

Final validation commands:

```bash
pveversion
sudo pvecm status
sudo pvecm nodes
sudo corosync-cfgtool -s

sudo pvesm status
sudo pvesm status --content images

ip -br addr
ip route

sudo /usr/sbin/sysctl net.ipv4.ip_forward
sudo iptables -t nat -S POSTROUTING
sudo iptables -S FORWARD

ping -c 2 pve2
ping -c 2 pve3
```

**Dừng tại đây. Chưa tạo `vm-test01`.**

---

# 28. Troubleshooting nhanh

## `sysctl: command not found`

User thường có thể không có `/usr/sbin` trong `PATH`.

Dùng:

```bash
sudo /usr/sbin/sysctl net.ipv4.ip_forward
```

hoặc:

```bash
sudo sysctl net.ipv4.ip_forward
```

## `vmbr1` hiển thị `UNKNOWN`

Không phải lỗi nếu chưa có guest NIC/tap gắn vào bridge.

Kiểm tra:

```bash
ip addr show vmbr1
ip route | grep 172.20.11.0
```

## iptables rule bị duplicate

Với final config ở trên, `post-up` đã dùng `iptables -C` trước khi `-A`, nên không nên duplicate.

Nếu cần kiểm tra:

```bash
sudo iptables -t nat -S POSTROUTING
sudo iptables -S FORWARD
```

## GCP NIC có `/32`

Ví dụ:

```text
ens4 10.10.0.11/32
```

Đây là expected trong GCP routed networking. Không tự đổi thành `/24`.

## Proxmox boot vào `grub>`

Trong lab này nguyên nhân đã gặp là GRUB packages của Proxmox thay boot stack Debian/GCP.

Kiểm tra:

```bash
apt-cache policy grub-common
```

Debian version phải là candidate, Proxmox `+pmx` phải bị pin `-1`.

---

# 29. Official references

Google Cloud:

- VPC network creation:  
  https://cloud.google.com/vpc/docs/create-modify-vpc-networks
- `gcloud compute instances create`:  
  https://cloud.google.com/sdk/gcloud/reference/compute/instances/create
- Nested virtualization:  
  https://cloud.google.com/compute/docs/instances/nested-virtualization/enabling
- Create nested VMs:  
  https://cloud.google.com/compute/docs/instances/nested-virtualization/creating-nested-vms
- IP forwarding:  
  https://cloud.google.com/vpc/docs/using-routes
- Firewall rules:  
  https://cloud.google.com/sdk/gcloud/reference/compute/firewall-rules/create
- Reserve/promote external IP:  
  https://cloud.google.com/sdk/gcloud/reference/compute/addresses/create

Proxmox VE:

- Proxmox VE Administration Guide:  
  https://pve.proxmox.com/pve-docs/pve-admin-guide.pdf
- Network configuration / bridges / NAT:  
  https://pve.proxmox.com/wiki/Network_Configuration

---

## Scope note

Guide này cố tình phản ánh **đúng kiến trúc đã chạy thành công trong lab GCP này**, bao gồm workaround giữ Debian/GCP GRUB.

Không nên copy phần GRUB pin một cách máy móc sang bare-metal hoặc cloud khác mà chưa hiểu boot chain của môi trường đó.
