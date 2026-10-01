# Tạo VM `proxmox-lb` trên GCP và chạy bản thử

Máy này chạy Docker/FastAPI/Nginx, **không** join Proxmox cluster. Theo
`setup-promox.md`, các PVE node đang ở `pve-vpc`, `pve-subnet`
(`10.10.0.0/24`), zone `asia-southeast1-b`. Dùng `10.10.0.20` nếu IP đó còn
trống. Tên GCE phải viết thường, nên dùng `proxmox-lb` thay cho `promoxLB`.

Các lệnh `gcloud` dưới đây dùng cú pháp Bash (Cloud Shell hoặc terminal có
Google Cloud CLI). Chọn đúng project trước khi chạy:

```bash
gcloud config set project YOUR_PROJECT_ID
gcloud compute networks subnets describe pve-subnet \
  --region=asia-southeast1 \
  --format='table(name,ipCidrRange,network)'
gcloud compute instances list \
  --format='table(name,zone,networkInterfaces[0].networkIP,networkInterfaces[0].accessConfigs[0].natIP)'
```

## Tạo VM và firewall cho IAP

Rule hiện có `pve-internal` cho phép nguồn `10.10.0.0/24` vào các node gắn
tag `pve-node`, nên LB cùng subnet có thể gọi PVE API trên TCP 8006. LB dùng
tag riêng `proxmox-lb` và chỉ nhận SSH/UI qua IAP. Rule IAP cần mở cổng 22
và 8080 từ dải `35.235.240.0/20`.

```bash
gcloud compute firewall-rules create proxmox-lb-iap \
  --network=pve-vpc \
  --direction=INGRESS \
  --priority=1000 \
  --source-ranges=35.235.240.0/20 \
  --target-tags=proxmox-lb \
  --allow=tcp:22,tcp:8080

gcloud compute instances create proxmox-lb \
  --zone=asia-southeast1-b \
  --machine-type=e2-standard-2 \
  --image-family=debian-13 \
  --image-project=debian-cloud \
  --boot-disk-size=30GB \
  --boot-disk-type=pd-balanced \
  --network-interface=network=pve-vpc,subnet=pve-subnet,private-network-ip=10.10.0.20 \
  --tags=proxmox-lb

gcloud compute instances describe proxmox-lb \
  --zone=asia-southeast1-b \
  --format='yaml(name,status,networkInterfaces)'
```

VM có external IP tạm thời để tải package/image; không mở cổng 8080 cho
Internet. Máy LB không cần nested virtualization hoặc IP forwarding.

## Cài Docker trên LB

```bash
gcloud compute ssh proxmox-lb \
  --zone=asia-southeast1-b \
  --tunnel-through-iap
```

Các lệnh tiếp theo chạy **bên trong VM Debian**:

```bash
sudo apt update
sudo apt install -y ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/debian/gpg \
  -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc

sudo tee /etc/apt/sources.list.d/docker.sources >/dev/null <<EOF
Types: deb
URIs: https://download.docker.com/linux/debian
Suites: $(. /etc/os-release && echo "$VERSION_CODENAME")
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF

sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo systemctl enable --now docker
sudo docker compose version
curl -k https://10.10.0.11:8006/api2/json/version
```

Lệnh `curl` cuối cần nhận JSON version của PVE; nếu lỗi, kiểm tra firewall
`pve-internal`, route và địa chỉ `PVE_API_HOST`.

## Chép code hiện có từ máy phát triển

Chạy từ **thư mục gốc repo trên máy có code mới nhất và `gcloud`**, không chạy
trong SSH. Cách này chép cả thay đổi chưa push lên Git:

```bash
gcloud compute ssh proxmox-lb \
  --zone=asia-southeast1-b \
  --tunnel-through-iap \
  --command='mkdir -p ~/proxmox-load-balancer'

gcloud compute scp --recurse \
  backend frontend compose.yaml .dockerignore .env.example \
  proxmox-lb:~/proxmox-load-balancer/ \
  --zone=asia-southeast1-b \
  --tunnel-through-iap
```

Nếu chạy lệnh từ PowerShell, đặt toàn bộ lệnh `gcloud compute scp ...` trên
một dòng, hoặc dùng dấu nối dòng PowerShell (backtick) thay cho `\`.

Nếu máy phát triển chưa có `gcloud`, dùng Cloud Shell cho các lệnh GCP và
clone nhánh `dev` từ GitHub ngay trên LB (chỉ lấy những commit đã push):

```bash
sudo apt install -y git
git clone --branch dev https://github.com/chiennc4805/proxmox-load-balancer.git \
  ~/proxmox-load-balancer
```

## Điền cấu hình và chạy

SSH lại vào LB, rồi chạy trong VM:

```bash
cd ~/proxmox-load-balancer
cp .env.example .env
nano .env
chmod 600 .env
sudo docker compose up --build -d
sudo docker compose ps
curl http://127.0.0.1:8080/api/health
curl http://127.0.0.1:8080/api/vms
```

Trong `.env`, đặt `PVE_API_HOST=10.10.0.11` (hoặc IP node PVE bạn truy cập
được), `PVE_API_USER`, `PVE_TOKEN_NAME` và token thật ở `PVE_TOKEN_VALUE`.
Không thêm `https://` vào host. Giữ `LAB_NETWORK_CIDR=172.20.11.0/24` nếu
clone chạy trên mạng `vmbr1` của `pve1`.

Scheduler cần thêm `POSTGRES_PASSWORD` và `ADMIN_API_KEY`
trong `.env` trước khi chạy `docker compose up`. PostgreSQL chạy nội bộ trong Compose;
chi tiết cấu hình round robin và API admin xem [scheduler.md](scheduler.md).

Để mở giao diện trên **máy phát triển** (terminal cần chạy liên tục):

```bash
gcloud compute start-iap-tunnel proxmox-lb 8080 \
  --zone=asia-southeast1-b \
  --local-host-port=localhost:8080
```

Mở `http://localhost:8080` trên chính máy chạy tunnel. Nếu cổng 8080 máy
phát triển đang được dùng, chọn `--local-host-port=localhost:18080` và mở
`http://localhost:18080`.

Template Windows trong guide có IP tĩnh `172.20.11.100`; nhiều clone sẽ
trùng IP cho đến khi bạn triển khai cơ chế cấp IP riêng. Test một clone trước.
API clone yêu cầu `X-Service-Key` khớp với `PROXMOX_LB_SERVICE_KEY`;
API cấu hình scheduler yêu cầu `X-Admin-Key`. Dùng private network hoặc
HTTPS khi MalSec gửi service key đến load balancer.

Tài liệu tham chiếu: [GCE instance create](https://docs.cloud.google.com/sdk/gcloud/reference/compute/instances/create),
[IAP TCP forwarding](https://docs.cloud.google.com/iap/docs/using-tcp-forwarding),
[IAP tunnel CLI](https://docs.cloud.google.com/sdk/gcloud/reference/compute/start-iap-tunnel),
[Docker trên Debian](https://docs.docker.com/engine/install/debian/).
