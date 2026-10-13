# Proxmox Gateway — Config-Driven Workflow Orchestration & Resource Scheduling

> **Trạng thái:** Tài liệu định hướng kiến trúc (architecture baseline), dùng làm ngữ cảnh triển khai dự án. Các API, DSL, database schema và đoạn mã bên dưới là **thiết kế đề xuất**, chưa phải tính năng đã được hiện thực.
>
> **Mục tiêu:** Xây dựng một lớp điều khiển mở rộng cho Proxmox VE, cho phép điều phối tài nguyên, tự động cấp phát một hoặc nhiều VM, và tùy biến hành vi API bằng workflow cấu hình động mà không phải chỉnh sửa Gateway cho từng nghiệp vụ.

## Mục lục

- [1. Bối cảnh và bài toán](#1-bối-cảnh-và-bài-toán)
- [2. Mục tiêu, phạm vi và nguyên tắc](#2-mục-tiêu-phạm-vi-và-nguyên-tắc)
- [3. Quyết định kiến trúc đã chốt](#3-quyết-định-kiến-trúc-đã-chốt)
- [4. Kiến trúc tổng thể](#4-kiến-trúc-tổng-thể)
- [5. Thành phần và trách nhiệm](#5-thành-phần-và-trách-nhiệm)
- [6. Vòng đời Action, Workflow và Policy](#6-vòng-đời-action-workflow-và-policy)
- [7. Luồng xử lý chính](#7-luồng-xử-lý-chính)
- [8. Workflow DSL và Action Contract](#8-workflow-dsl-và-action-contract)
- [9. Resource Scheduling và Multi-VM Lab](#9-resource-scheduling-và-multi-vm-lab)
- [10. Persistence, concurrency và recovery](#10-persistence-concurrency-và-recovery)
- [11. API và hợp đồng tích hợp](#11-api-và-hợp-đồng-tích-hợp)
- [12. Folder structure cuối cùng](#12-folder-structure-cuối-cùng)
- [13. Deployment, cấu hình và vận hành](#13-deployment-cấu-hình-và-vận-hành)
- [14. Bảo mật, observability và testing](#14-bảo-mật-observability-và-testing)
- [15. Lộ trình triển khai và tiêu chí nghiệm thu](#15-lộ-trình-triển-khai-và-tiêu-chí-nghiệm-thu)
- [16. Giới hạn và vấn đề cần quyết định khi hiện thực](#16-giới-hạn-và-vấn-đề-cần-quyết-định-khi-hiện-thực)
- [17. Tài liệu tham khảo](#17-tài-liệu-tham-khảo)

## 1. Bối cảnh và bài toán

### 1.1. Hệ thống hiện tại

Website cung cấp giao diện để người dùng khởi tạo, quản lý và sử dụng VM trực tiếp trên trình duyệt. Backend tương tác với **Proxmox VE API** để quản lý VM; **Apache Guacamole** tạo phiên SSH/RDP đến hệ điều hành khách khi VM đã sẵn sàng.

```mermaid
flowchart LR
    USER[Người dùng] --> WEB[Website / Backend]
    WEB --> PVE[Proxmox API]
    PVE --> CLUSTER[Proxmox Cluster]
    WEB --> GUA[Apache Guacamole]
    GUA -. SSH / RDP tới guest VM .-> CLUSTER
```

Một số Proxmox API tạo VM sử dụng đường dẫn chứa tên node, ví dụ `POST /api2/json/nodes/{node}/qemu`; clone sử dụng `POST /api2/json/nodes/{source_node}/qemu/{vmid}/clone`. Điều này khiến hệ thống hiện tại phải biết/chỉ định node, thường dẫn đến hardcode.

### 1.2. Những bài toán cần giải quyết

1. **Automatic placement:** Chọn node dựa trên CPU, RAM, storage, network, template và chính sách scheduling thay vì hardcode node.
2. **Resource accounting:** Ngăn nhiều yêu cầu đồng thời cấp phát vượt khả năng của cluster; quản lý reservation và allocations.
3. **VM lifecycle:** Clone/create, configure, start, theo dõi Proxmox task, guest readiness và xử lý lỗi.
4. **Multi-VM lab:** Triển khai nhiều VM theo topology, dependency, affinity/anti-affinity và chính sách xử lý thất bại một phần.
5. **Extensible API behavior:** Có thể can thiệp vào nhiều endpoint Proxmox, không chỉ provisioning.
6. **Runtime customization:** Người quản trị tạo/sửa/publish workflow và placement policy khi ứng dụng đang chạy.
7. **Maintainability:** Logic tái sử dụng, định dạng workflow chuẩn hóa và hạn chế sửa core khi phát triển tính năng mới.

> 💡 **Giới hạn bản chất:** Cluster Proxmox không gộp CPU/RAM các node thành một host vật lý duy nhất. Mỗi VM chạy trên một node tại một thời điểm; scheduler chịu trách nhiệm placement và quản lý tài nguyên cấp cluster.

## 2. Mục tiêu, phạm vi và nguyên tắc

### 2.1. Mục tiêu sản phẩm

- Giữ **một catch-all controller** cho bề mặt Proxmox-compatible API; website không phải biết chi tiết routing nội bộ của proxy.
- Hỗ trợ **pass-through**, **policy interception** và **workflow override** bằng cấu hình route.
- Cho phép workflow dạng **declarative DAG** được định nghĩa/publish qua Management API/UI.
- Chuẩn hóa Action Contract và tái sử dụng capability qua nhiều workflow.
- Cho phép chỉnh chiến lược/constraints/trọng số scheduler bằng Policy Editor nếu DSL đã hỗ trợ.
- Xử lý job bất đồng bộ, ghi trạng thái bền vững, hỗ trợ retry có kiểm soát và reconciliation.
- Triển khai on-premise bằng Python, PostgreSQL, Docker Compose; mở rộng Worker replicas khi cần.

### 2.2. Ngoài phạm vi ban đầu

- Không xây môi trường thực thi Python tùy ý từ mã do người dùng nhập trên UI.
- Không xây **Action Builder**; Action Catalog trên UI là **read-only**.
- Không bắt buộc RabbitMQ/Redis/Outbox trong phiên bản kiến trúc đầu tiên.
- Không cố mô phỏng 100% response semantics của Proxmox ở những API đã bị workflow override.
- Không xây microservice riêng cho Compiler, Registry, DAG Engine, Scheduler hoặc từng Action.
- Không tạo hệ thống live migration/HA mới thay thế cơ chế Proxmox hiện hữu.

### 2.3. Nguyên tắc thiết kế

- **Workflow = What to do:** định nghĩa trình tự, dependencies, branching, output, failure behavior.
- **Policy/Strategy = How to decide:** quy định ràng buộc và cách đánh giá/lựa chọn, ví dụ node placement.
- **Action/Capability = What the platform can do:** thực thi năng lực đã được cài đặt, có schema và quyền rõ ràng.
- **Gateway routes once:** Controller/Route Matcher quyết định proxy hoặc workflow; Worker không phân loại lại HTTP request.
- **Worker executes; Engine orchestrates:** DAG Engine là **module nằm bên trong Worker process**, không phải service riêng.
- **Database is source of truth:** PostgreSQL lưu workflow đã publish, plan, job, step, reservation, inventory.
- **No mandatory semantic versioning:** Không bắt buộc `workflow@1.0.0` hoặc `action@1`. Vẫn giữ execution plan snapshot bất biến cho mỗi job.
- **Safe by default:** Không cho workflow tùy ý thực thi lệnh, gọi URL bất kỳ hoặc vượt quyền của tenant.

## 3. Quyết định kiến trúc đã chốt

| Chủ đề | Quyết định | Lý do |
|:--|:--|:--|
| Core | **Modular monolith / shared codebase** | Phân định trách nhiệm bằng module, tránh phân tán quá sớm |
| Runtime | `gateway`, `worker`, `reconciler` (có thể chạy riêng) | Scale theo tải nhưng dùng chung contracts |
| Queue | **PostgreSQL-backed job queue** | Tránh thêm RabbitMQ và Outbox khi chưa cần |
| HTTP | **Một catch-all Proxmox controller** + Management API | Không phải mirror từng Proxmox endpoint |
| Workflow | **YAML/JSON DSL, runtime publish vào PostgreSQL** | Tạo workflow mà không restart worker |
| Workflow store | **PostgreSQL** | Lưu definition, compiled plan, trạng thái active |
| Action Registry | **In-memory, trong từng process cần dùng** | Tên Action ánh xạ tới contract và Python implementation |
| Action discovery | **Startup qua trusted Python packages** | Có thể bổ sung capability mà không sửa core framework |
| Action Catalog UI | **Chỉ đọc metadata / schema** | Không mở thực thi code tùy ý cho người dùng |
| Scheduling | **Capability `placement`** với Policy/Strategy | Không hardcode scheduler trong Engine hoặc Workflow |
| Policy | **Cấu hình động, validate/publish qua DB** | Chỉnh constraints/weights không deploy code |
| Engine | **Durable DAG executor bên trong Worker** | Quản lý state, dependencies và recovery |
| Execution snapshot | **Compiled plan được pin vào Job** | Job cũ không đổi cấu trúc khi workflow được cập nhật |
| Guacamole | **Website backend quản lý session** | Gateway chỉ cấp VM và thông tin readiness |

> ⚠️ **Về thay đổi Action code:** Plan snapshot chỉ giữ cấu trúc workflow và dữ liệu cấu hình, **không đóng băng Python implementation**. Khi đổi implementation không tương thích, cần drain job hoặc triển khai theo chính sách tương thích; nếu cần bảo đảm chạy logic cũ tuyệt đối thì phải giữ runtime/image cũ cho các job đang chạy.

## 4. Kiến trúc tổng thể

### 4.1. Sơ đồ logical architecture

```mermaid
flowchart TB
    WEB[Website Backend / Admin UI]

    subgraph API[Gateway process]
        CTRL[Single catch-all Proxmox Controller]
        AUTH[Authentication / Authorization]
        ROUTE[Route Matcher + Policy Pipeline]
        PROXY[Pass-through Proxy]
        JOBAPI[Job Submission + Status]
        MGMT[Workflow / Policy Management API]
        COMP[Workflow Compiler]
        CTRL --> AUTH --> ROUTE
        ROUTE -->|proxy| PROXY
        ROUTE -->|workflow| JOBAPI
        MGMT --> COMP
    end

    subgraph DB[PostgreSQL]
        WF[(Definitions / Active Plans / Route Rules / Policies)]
        JOBS[(Jobs / Step Runs / External Tasks)]
        RES[(Inventory / Reservations / Resource State)]
    end

    subgraph WORKER[Worker process - scalable replicas]
        CONSUMER[Job Claim / Lease / Heartbeat]
        ENGINE[DAG Execution Engine]
        CONTEXT[Context + Dependency Resolver]
        RUNNER[Action Runner]
        REG[Local Action Registry]
        CAP[Domain Capabilities]
        CONSUMER --> ENGINE --> CONTEXT --> RUNNER --> REG --> CAP
    end

    subgraph AUX[Background process]
        RECON[Task + Resource Reconciler]
    end

    PVE[Proxmox Cluster API]
    GUA[Apache Guacamole]

    WEB --> CTRL
    WEB --> MGMT
    WEB --> GUA
    MGMT --> WF
    COMP --> WF
    ROUTE <--> WF
    JOBAPI --> WF
    JOBAPI --> JOBS
    JOBS --> CONSUMER
    ENGINE <--> JOBS
    CAP <--> RES
    CAP --> PVE
    PROXY --> PVE
    RECON <--> JOBS
    RECON <--> RES
    RECON --> PVE
    GUA -. guest SSH/RDP .-> PVE
```

**Lưu ý sơ đồ:** Đường Guacamole → Proxmox Cluster tượng trưng cho tuyến mạng tới *guest VM*, không phải Guacamole SSH/RDP vào hypervisor. Management API có thể nằm trong cùng FastAPI application nhưng là nhóm route quản trị khác với catch-all Proxmox API.

### 4.2. Sơ đồ physical deployment

```mermaid
flowchart LR
    BACKEND[Website Backend]
    subgraph COMPOSE[Docker Compose]
        GW[gateway - FastAPI]
        W1[worker replica 1]
        W2[worker replica N]
        REC[reconciler]
        PG[(PostgreSQL)]
        GW --> PG
        W1 <--> PG
        W2 <--> PG
        REC <--> PG
    end
    PVE[Proxmox Cluster pve1 / pve2 / pve3]
    BACKEND --> GW
    GW --> PVE
    W1 --> PVE
    W2 --> PVE
    REC --> PVE
```

Khởi đầu có thể chạy **1 Gateway + 1 Worker + PostgreSQL**; Reconciler có thể chạy trong cùng container Worker ở giai đoạn thử nghiệm, rồi tách process khi cần. Mỗi Worker có thể giao tiếp với *mọi node được phép* trong cluster.

## 5. Thành phần và trách nhiệm

### 5.1. Gateway

| Module | Nhiệm vụ |
|:--|:--|
| `controller` | Nhận mọi HTTP method trên `/api2/json/{path:path}` |
| `request_context` | Chuẩn hóa request, identity, tenant, correlation ID |
| `authorization` | Phân quyền trên VM, lab, cluster và action |
| `route_matcher` | Tra cứu route rules đang active, áp dụng precedence rõ ràng |
| `policy_pipeline` | Quota, audit, request/response modification theo policy |
| `proxy_executor` | Chuyển tiếp API được cho phép tới Proxmox |
| `job_service` | Validate input, idempotency, chọn compiled plan, tạo job |
| `management` | Tạo draft, validate, publish, deactivate workflow/policy/route |

**Bảo đảm:** Không bắt Controller biết `create`, `clone`, `migrate`, `snapshot` ở mức business logic. Các operation đặc biệt được cấu hình bằng route binding và workflow.

### 5.2. Compiler và Workflow Store

- **Parser/Schema Validator:** YAML/JSON → typed workflow definition.
- **Action Contract Resolver:** kiểm tra Action được cài đặt và schema input/output tương ứng.
- **DAG Validator:** kiểm tra `needs`, cycle, tham chiếu output, điều kiện và bước unreachable.
- **Safety Validator:** kiểm tra action allowlist, quyền, side effects và retry/recovery policy.
- **Plan Builder:** tạo *compiled execution plan* với dependencies, bindings và metadata có thể serialize.
- **Workflow Store:** PostgreSQL lưu draft, plan immutable, active pointer; không phải microservice Registry riêng.

Không compile tùy ý khi Worker nhận mỗi job. **Compile lúc publish**; Worker tải compiled plan gắn với job và validate contract/runtime tương ứng.

### 5.3. Worker và DAG Engine

- **Worker** là process chạy vòng lặp claim job, gia hạn lease, giới hạn concurrency, quản lý shutdown/health.
- **DAG Engine** là Python object/module bên trong Worker, lựa chọn step đủ dependencies, binding input, gọi Action Runner và persist step state.
- **Action Runner** resolve `uses` qua Local Action Registry và thực thi đúng Action Contract.
- **Checkpoint/Recovery** lưu bước đã thành công, trạng thái tác vụ ngoài, retry schedule và lỗi.
- **External Task Tracking** theo dõi Proxmox UPID; tác vụ lâu có thể chuyển sang `WAITING` để nhường slot.

```mermaid
flowchart TD
    DB[(Jobs in PostgreSQL)] --> CLAIM[Worker claim job + lease]
    CLAIM --> LOAD[Load pinned plan]
    LOAD --> DAG[DAG ready-step selector]
    DAG --> BIND[Resolve inputs]
    BIND --> RUN[Action Runner]
    RUN --> REG[Local Action Registry]
    REG --> ACTION[Execute capability]
    ACTION --> SAVE[Persist output / state]
    SAVE --> DAG
```

### 5.4. Action Registry — không phải Action Builder

Action Registry đơn giản là **mapping trong RAM của mỗi process** từ tên Action đến implementation Python được deploy và contract của nó:

```python
{
    "proxmox.request": ProxmoxRequestAction,
    "placement.reserve": ReservePlacementAction,
    "placement.commit": CommitPlacementAction,
    "proxmox.task.wait": WaitProxmoxTaskAction,
    "guest.wait_ready": GuestReadyAction,
}
```

- **Lưu ở đâu?** Python source/package trong image; mapping tạo lại trong RAM khi process khởi động.
- **Đăng ký khi nào?** Bootstrap tự import các module Action trong trusted packages; decorator/registration function đưa class vào mapping.
- **Ai cần nó?** Compiler ở Gateway cần **contracts**; Worker cần **contracts và implementation**.
- **Workflow mới có tạo Action mới không?** Không. Workflow chỉ tham chiếu Action đã có; người dùng không tạo Action code trên UI.
- **Action mới hoàn toàn?** Developer viết plugin Python và deploy worker/gateway có hỗ trợ. Có thể thêm mô hình external plugin executor về sau, không thuộc baseline.

Có thể cung cấp API **read-only Action Catalog** để Workflow Builder hiển thị tên, mô tả, input/output schema. Không cần một Action Registry server riêng hoặc DB chứa Python classes.

### 5.5. Domain Capabilities

| Capability | Những năng lực chính |
|:--|:--|
| `placement` | Thu thập resources, filtering, scoring, strategy selection, atomic reservation |
| `resources` | Capacity, committed allocations, reservations, VM inventory reconciliation |
| `proxmox` | Các Proxmox operations được allowlist, VM clone/create/start/migrate, task state |
| `network` | IP allocation, VNet/bridge requirements, conflict prevention |
| `guest` | SSH/RDP readiness, guest-agent/connection checks theo capability |
| `lab` | Group placement, topology, anti-affinity, aggregate inventory/lifecycle |

**Ranh giới:** Framework không biết `scheduler`, `VMID`, `IP` hay `NFS`. Nó chỉ gọi Action có contract. Bên trong `placement`, các strategy là plugin nội bộ, không phải Action riêng cho từng thuật toán.

## 6. Vòng đời Action, Workflow và Policy

### 6.1. Action: từ source code đến Registry

```mermaid
flowchart LR
    CODE[Developer viết Action Python] --> DEPLOY[Build / Deploy image]
    DEPLOY --> BOOT[Gateway / Worker startup]
    BOOT --> DISCOVER[Discover trusted Action modules]
    DISCOVER --> REG[Local Action Registry]
    REG --> USE[Compiler / Action Runner]
```

`bootstrap()` **không phải container riêng**. Entry point `gateway` và `worker` gọi bootstrap khi startup. Action mới viết sau thời điểm startup chỉ có hiệu lực khi code được deploy và process được restart/rolling restart.

### 6.2. Workflow: người dùng publish ngay khi runtime

```mermaid
sequenceDiagram
    actor U as Workflow Admin
    participant UI as Workflow Builder
    participant API as Management API
    participant C as Compiler
    participant DB as PostgreSQL
    participant W as Worker
    U->>UI: Tạo / sửa YAML hoặc graph
    UI->>API: Save draft
    API->>DB: Persist DRAFT
    U->>UI: Publish
    UI->>API: Publish request
    API->>C: Compile against Action contracts
    C-->>API: Immutable compiled plan
    API->>DB: Atomic write plan + activate pointer
    Note over DB,W: Không restart Worker
    W->>DB: Claim job mới, đọc plan được pin
```

**Workflow Store PostgreSQL** là nguồn sự thật; không cần đồng bộ một dictionary workflow tới mọi worker. Một workflow đã publish có thể dùng ngay nếu các Worker đang vận hành hỗ trợ đủ các Action được tham chiếu.

### 6.3. Policy: runtime editing, validation và snapshot

- Người quản trị tạo/sửa policy qua Policy Editor; cấu hình có schema hạn chế (constraints, metric, weights, strategy đã hỗ trợ).
- Publish chỉ thành công sau validation và kiểm tra capability cần thiết.
- Job mới sử dụng **snapshot policy** đã resolve hoặc policy data được nhúng trong compiled job context để job cũ không bị thay đổi kết quả do sửa config.
- Nếu muốn thuật toán mới mà DSL không biểu diễn được, viết **Strategy Plugin** trong `placement`; không cần Action mới nếu contract `placement.reserve` giữ nguyên.

### 6.4. Không bắt buộc semantic versioning

Mỗi workflow có một **active plan pointer**. Khi publish thay đổi, hệ thống tạo một plan immutable mới và đổi pointer; job hiện hữu tiếp tục tham chiếu plan cũ. Có thể dùng `plan_id` và `content_hash` **như định danh kỹ thuật**, không phải người dùng phải quản lý `v1/v2`.

> ⚠️ **Quan trọng:** Nội dung compiled plan phải persist sau khi publish; nếu chỉ lưu trong RAM thì Worker restart không thể resume những job đang sử dụng kế hoạch cũ. Action implementation thay đổi không tương thích vẫn cần chính sách deploy bảo toàn hành vi.

## 7. Luồng xử lý chính

### 7.1. Pass-through API

1. Website gọi một endpoint thuộc `/api2/json/{path:path}`.
2. Gateway xác thực identity/tenant, match route, thực hiện policy bắt buộc.
3. Nếu route cho phép pass-through, Gateway forward request tới Proxmox với quyền phù hợp.
4. Response trả về Website; audit log ghi lại correlation ID.

> ⚠️ **Không được pass-through mù quáng:** Các lệnh thay đổi VM/node/storage có thể ảnh hưởng resource accounting. Cần route allowlist, authorization, audit và reconciliation hoặc workflow override. Hạn chế proxy tới endpoint nguy hiểm bằng token đặc quyền.

### 7.2. Provision một VM

```mermaid
sequenceDiagram
    autonumber
    participant WEB as Website Backend
    participant GW as Gateway
    participant DB as PostgreSQL
    participant WK as Worker / Engine
    participant PL as Placement Capability
    participant PVE as Proxmox API
    WEB->>GW: POST provision + idempotency key
    GW->>DB: Transaction: pin active plan, create job
    GW-->>WEB: 202 + job_id
    WK->>DB: Claim job + lease
    WK->>PL: placement.reserve(requirements)
    PL->>PVE: Read cluster/VM/storage state
    PL->>DB: Atomic capacity check + reservation
    PL-->>WK: Target node + reservation_id
    WK->>PVE: Clone/create VM + config
    PVE-->>WK: UPID / task reference
    WK->>DB: Persist external task + WAITING
    WK->>PVE: Poll/reconcile task status
    PVE-->>WK: Finished + exit status
    WK->>PVE: Start VM / verify readiness
    WK->>DB: Commit allocation + job SUCCEEDED
    WEB->>GW: GET job status
    GW-->>WEB: READY + VM details
```

**Quy tắc:** Trả UPID không đồng nghĩa task thành công. Worker phải xác nhận task kết thúc thành công và guest thực sự sẵn sàng. Website chỉ tạo Guacamole connection khi đã nhận `READY` và có tuyến mạng SSH/RDP tới guest VM.

### 7.3. Tạo lab gồm nhiều VM

1. Website gửi `LabSpec` gồm danh sách VM, topology, dependencies và placement constraints.
2. Worker dùng **group planning/reservation** để kiểm tra năng lực của toàn bộ lab trước khi tạo VM đầu tiên.
3. Engine triển khai những VM độc lập song song với concurrency limit.
4. Các VM phụ thuộc sẽ chỉ chạy khi prerequisite đã hoàn thành; readiness theo từng VM.
5. Nếu một VM thất bại, áp dụng **partial-failure policy** (`retain`/`cleanup` theo cấu hình), reconcile những VM đã được tạo.
6. Group inventory phản ánh trạng thái lab và VM con; không giả định Proxmox cung cấp một distributed transaction cho toàn bộ lab.

### 7.4. Override hành vi endpoint khác

Ví dụ migrate VM:

- Route matcher chọn workflow `vm.migrate` thay vì proxy trực tiếp.
- Workflow dùng lại `placement.reserve` với *migration constraints* riêng.
- Gọi Proxmox migrate API và theo dõi UPID.
- Reconcile allocation của **source + target node** khi migration hoàn tất.

Clone, migrate, snapshot hoặc delete có thể chia sẻ Action theo contract, nhưng không cần ép các nghiệp vụ sử dụng cùng một workflow cứng.

## 8. Workflow DSL và Action Contract

### 8.1. Ví dụ Workflow Definition (minh họa)

```yaml
name: vm.provision
mode: async
input_schema: vm.provision.input

steps:
  - id: template
    uses: template.resolve
    with:
      vmid: "${input.template_vmid}"

  - id: placement
    uses: placement.reserve
    needs: [template]
    with:
      resources: "${input.resources}"
      policy: "${input.placement_policy}"
      template: "${steps.template.output}"

  - id: clone
    uses: proxmox.vm.clone
    needs: [placement]
    with:
      source: "${steps.template.output}"
      target_node: "${steps.placement.output.node}"
      reservation_id: "${steps.placement.output.reservation_id}"

  - id: clone_done
    uses: proxmox.task.wait
    needs: [clone]
    with:
      task_node: "${steps.clone.output.task_node}"
      upid: "${steps.clone.output.upid}"

  - id: ready
    uses: guest.wait_ready
    needs: [clone_done]
    with:
      vmid: "${steps.clone.output.vmid}"
      protocol: "${input.protocol}"

  - id: commit
    uses: placement.commit
    needs: [ready]
    with:
      reservation_id: "${steps.placement.output.reservation_id}"

on_failure:
  uses: provision.reconcile

outputs:
  vmid: "${steps.clone.output.vmid}"
  node: "${steps.placement.output.node}"
```

> 💡 **Ví dụ rút gọn:** Trong thực tế còn cần cloud-init, IP allocation, cập nhật network, start VM (nếu clone chưa tự start), guest readiness, status tracking và idempotency theo từng step. Cấu trúc DSL trên là **đề xuất của dự án**, chưa có parser/runtime sẵn.

### 8.2. Action Contract

Action công bố tối thiểu:

| Thuộc tính | Mục đích |
|:--|:--|
| `name` | Tên duy nhất, ví dụ `placement.reserve` |
| `input_schema` | Schema/kiểu đầu vào để compiler và runtime validate |
| `output_schema` | Schema dữ liệu mà downstream step được tham chiếu |
| `required_permissions` | Phạm vi quyền để được invoke |
| `side_effect` | Action có thay đổi trạng thái bên ngoài hay không |
| `retry_policy` | Khi nào được retry an toàn |
| `reconcile` | Cách xác định kết quả của operation chưa rõ trạng thái |
| `execute(context, inputs)` | Entry point duy nhất Engine gọi |

```python
from typing import Protocol

class Action(Protocol):
    name: str
    input_schema: type
    output_schema: type

    async def execute(self, context, inputs: dict) -> dict:
        ...
```

**Quy tắc granularity:** `placement.reserve` là một action atomic theo nghiệp vụ; không tách tùy tiện thành những Action độc lập `read_capacity` → `check_capacity` → `write_reservation` gây race condition.

### 8.3. Placement Policy Definition

```yaml
name: balanced
strategy: weighted
constraints:
  - metric: node.online
    op: eq
    value: true
  - metric: node.allocatable_memory_mb
    op: gte
    value: "${input.resources.memory_mb}"
scoring:
  direction: maximize
  metrics:
    - name: cpu_headroom_ratio
      weight: 0.4
    - name: memory_headroom_ratio
      weight: 0.6
```

Người quản trị có thể thay weights, constraints và chọn chiến lược đã được hệ thống hỗ trợ mà không thay đổi Action hoặc deploy code. Policy engine phải giới hạn các operator/metric được hỗ trợ; không `eval()` biểu thức Python từ database.

## 9. Resource Scheduling và Multi-VM Lab

### 9.1. Placement Pipeline

```mermaid
flowchart TD
    A[placement.reserve] --> B[Collect Proxmox node / VM / storage state]
    B --> C[Join inventory + active reservations]
    C --> D[Hard constraint filtering]
    D --> E[Strategy ranking - Weighted / TOPSIS / ...]
    E --> F[Choose candidate]
    F --> G[DB transaction: recheck + reserve]
    G --> H{Capacity still available?}
    H -->|No| I[Next candidate / pending capacity]
    I --> F
    H -->|Yes| J[Return target + reservation_id]
```

### 9.2. Resource accounting

- CPU: phân biệt vCPU đã cấp phát và capacity/overcommit policy; không chỉ đọc % CPU usage tức thời.
- RAM: xét committed memory, host reserve, active reservation và overcommit policy; không chỉ đọc `free` hiện tại.
- Storage: kiểm tra datastore có truy cập được từ target node; **shared NAS được tính một lần** theo datastore, không nhân lên theo số node.
- Network: kiểm tra VNet, MTU, IP/subnet, route đến guest từ Guacamole và vùng cấp phát IP.
- Template: phân biệt **source node** của template và **target node** của VM; kiểm tra điều kiện clone cross-node/storage.
- Placement: affinity/anti-affinity, maintenance/drain node, quota theo tenant/lab.

### 9.3. Reservation và đồng thời

- Trước khi ghi reservation phải khóa tài nguyên accounting liên quan trong PostgreSQL transaction.
- Cùng một logical resource có một quy tắc khóa nhất quán để hạn chế deadlock.
- Commit transaction nhanh; **không giữ DB row lock trong lúc Proxmox clone VM**.
- Reservation có lifecycle `HELD` → `ALLOCATED` hoặc `RELEASED`/`RECONCILING`.
- Reconciliation đối chiếu tình trạng VM thật nếu người dùng thay đổi Proxmox trực tiếp hoặc external task mất kết quả.

## 10. Persistence, concurrency và recovery

### 10.1. Nhóm bảng dự kiến

| Nhóm | Bảng | Dữ liệu chính |
|:--|:--|:--|
| Management | `workflows` | name, draft, active_plan_id, status, owner |
| Management | `workflow_plans` | immutable compiled JSON, hash, action contract manifest |
| Management | `route_rules` | method/path, execution type, workflow binding, policy chain |
| Management | `placement_policies` | cấu hình và trạng thái publish |
| Execution | `jobs` | workflow/plan reference, input snapshot, status, idempotency key, lease |
| Execution | `step_runs` | step status, attempts, input/output, dependencies |
| Execution | `external_tasks` | Proxmox UPID, task node, reconciliation state |
| Resource | `vm_inventory` | vmid, node, lab, owner, allocated resources |
| Resource | `resource_reservations` | job, node/datastore, amounts, state |
| Resource | `node_capacity` | capacity snapshot/allocatable limits |
| Audit | `audit_events` | actor, action, target, correlation ID, result |

`workflow_plans` sử dụng **plan_id/hash** để giữ immutable snapshot, **không ép người dùng quản lý semantic version**. Không xóa plan khi còn Job tham chiếu.

### 10.2. PostgreSQL work queue

Worker claim job bằng transaction ngắn, có thể sử dụng `FOR UPDATE SKIP LOCKED` để nhiều consumer tránh tranh chấp cùng một hàng. Sau claim, cập nhật `lease_owner`, `lease_until`, status và commit; job thực thi bên ngoài transaction.

```sql
-- Pseudocode SQL để minh họa lựa chọn job; không phải migration hoàn chỉnh.
BEGIN;

SELECT id
FROM jobs
WHERE status IN ('QUEUED', 'RETRY_PENDING')
  AND (next_run_at IS NULL OR next_run_at <= NOW())
ORDER BY created_at, id
FOR UPDATE SKIP LOCKED
LIMIT 1;

-- UPDATE claimed job: status, lease_owner, lease_until, attempt_count
COMMIT;
```

**Quan trọng:** Lease có thể hết hạn do Worker crash; chỉ một process được sở hữu *điều phối* tại một thời điểm, nhưng điều này **không tự bảo đảm external Proxmox side effect được thực hiện đúng một lần**. Cần idempotency + external task reconciliation.

### 10.3. Retry, timeout và crash recovery

- Các action chỉ đọc thông tin có thể retry khi transient error.
- `clone/create/migrate/delete` là action có side effect: timeout/connection loss có thể là **unknown outcome**, phải kiểm tra VMID/UPID/operation reference trước khi POST lại.
- Persist UPID và task node; chờ Proxmox task kết thúc, kiểm tra exit status rồi mới coi step hoàn tất.
- Step `WAITING` có thể trả worker slot; một background monitor/reconciler đánh thức job tiếp tục.
- Compensation phải dựa trên trạng thái thực tế; không mặc định xóa VM nếu lỗi xảy ra sau khi clone.
- Job có thể `QUEUED`, `RUNNING`, `WAITING`, `RECONCILING`, `SUCCEEDED`, `FAILED`, `CANCELLED` (chuyển trạng thái được Engine kiểm soát).

## 11. API và hợp đồng tích hợp

### 11.1. API surface

| Route (đề xuất) | Chức năng | Ghi chú |
|:--|:--|:--|
| `ANY /api2/json/{path:path}` | Single catch-all Proxmox API | Route rule quyết định proxy/workflow |
| `POST /api2/json/provision` | Provision VM không cần node | Gateway-specific extension |
| `POST /api2/json/labs/provision` | Provision multi-VM lab | Gateway-specific extension |
| `POST /management/workflows` | Save draft | Chỉ admin/workflow author |
| `POST /management/workflows/{id}/publish` | Validate, compile, activate | Kiểm tra Action availability |
| `POST /management/workflows/{id}/execute` | Chạy workflow đang active | Cần quyền invoke + tenant scope |
| `GET /management/actions` | Read-only Action Catalog | Không nhận code người dùng |
| `GET /jobs/{job_id}` | Job status/result | Website polling hoặc bổ sung webhook |

**Response contract:** API pass-through cố gắng giữ status/body phù hợp Proxmox; API override trả **HTTP `202 Accepted` + `job_id`** theo contract riêng, không giả làm Proxmox UPID. Website cần tích hợp rõ hành vi bất đồng bộ này.

### 11.2. Ví dụ request/response

```json
{
  "operation": "clone",
  "template_vmid": 9000,
  "name": "lab-001-vm-01",
  "resources": {
    "cpu": 2,
    "memory_mb": 4096,
    "disk_gb": 30
  },
  "storage": "nas-nfs",
  "network": {
    "vnet": "vnet20",
    "ip": "auto"
  },
  "access": {
    "protocol": "ssh",
    "username": "student"
  },
  "placement_policy": "balanced"
}
```

```json
{
  "job_id": "job-abc123",
  "status": "QUEUED",
  "status_url": "/jobs/job-abc123"
}
```

Khuyến nghị client gửi header `Idempotency-Key` ổn định cho mỗi intent tạo VM, thay vì tạo key mới khi retry cùng một request.

## 12. Folder structure cuối cùng

```text
proxmox-gateway/
├── app/
│   ├── gateway/                         # HTTP ingress & route decisions
│   │   ├── controller.py                 # Single catch-all Proxmox API
│   │   ├── request_context.py           # Normalize request, identity, correlation
│   │   ├── authorization.py             # Tenant / VM / operation authorization
│   │   ├── route_matcher.py             # Dynamic route rules
│   │   ├── policy_pipeline.py           # Request/response policies
│   │   ├── proxy_executor.py            # Proxmox pass-through
│   │   ├── job_service.py               # Submit jobs; pin immutable plan
│   │   └── management/                  # Admin Workflow/Policy management API
│   │       ├── workflows.py
│   │       ├── placement_policies.py
│   │       └── action_catalog.py
│   │
│   ├── framework/                       # Generic; no Proxmox-specific concepts
│   │   ├── contracts/
│   │   │   ├── action.py                 # Action interface, schemas, metadata
│   │   │   ├── workflow.py               # Workflow DSL model
│   │   │   └── context.py                # Execution context contract
│   │   ├── discovery/
│   │   │   └── actions.py                # Import trusted Action modules
│   │   ├── compiler/
│   │   │   ├── parser.py
│   │   │   ├── schema_validator.py
│   │   │   ├── dag_validator.py
│   │   │   ├── contract_checker.py
│   │   │   └── plan_builder.py
│   │   ├── registry/
│   │   │   ├── action_registry.py         # In-process Action mapping
│   │   │   └── workflow_store.py          # PostgreSQL workflow/plans repository
│   │   └── engine/
│   │       ├── executor.py                # Execute pinned plan
│   │       ├── dag_scheduler.py           # Ready step selection
│   │       ├── context_resolver.py        # Input/output bindings
│   │       ├── action_runner.py           # Contract-aware Action invocation
│   │       ├── state_manager.py           # Durable checkpoints
│   │       └── recovery.py                # Retry/reconcile/compensation
│   │
│   ├── capabilities/                    # Domain logic + Action adapters
│   │   ├── placement/
│   │   │   ├── actions.py                 # placement.reserve/commit/release
│   │   │   ├── service.py
│   │   │   ├── resource_collector.py
│   │   │   ├── constraints.py
│   │   │   ├── reservation.py
│   │   │   └── strategies/
│   │   │       ├── registry.py            # Local strategy mapping
│   │   │       ├── weighted.py
│   │   │       └── topsis.py
│   │   ├── resources/
│   │   │   ├── accounting.py
│   │   │   └── inventory.py
│   │   ├── proxmox/
│   │   │   └── actions.py                 # Allowed Proxmox operations
│   │   ├── virtual_machine/
│   │   │   ├── actions.py
│   │   │   └── service.py
│   │   ├── network/
│   │   │   ├── actions.py
│   │   │   └── ipam.py
│   │   ├── guest/
│   │   │   └── readiness.py
│   │   └── lab/
│   │       └── group_placement.py
│   │
│   ├── infrastructure/                  # DB, external APIs, integrations
│   │   ├── database/
│   │   │   ├── models.py
│   │   │   ├── session.py
│   │   │   └── repositories/
│   │   ├── proxmox/
│   │   │   └── client.py
│   │   ├── security/
│   │   └── observability/
│   │
│   └── runtimes/                        # Process entrypoints (not microservices)
│       ├── gateway.py                   # FastAPI startup, routes, bootstrap
│       ├── worker.py                    # Action bootstrap + claim/job loop
│       └── reconciler.py                # External task/resource reconciliation
│
├── definitions/                         # Seed/example definitions only
│   ├── workflows/
│   │   ├── vm-provision.yaml
│   │   ├── vm-migrate.yaml
│   │   └── lab-provision.yaml
│   ├── routes.yaml
│   └── placement-policies.yaml
│
├── migrations/                          # Alembic migrations
├── scripts/                             # Import seeds, validate definitions
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── concurrency/
│   └── e2e/
├── docs/                                # Detailed specifications / ADRs
├── .env.example
├── pyproject.toml
├── Dockerfile
├── docker-compose.yml
└── README.md
```

### 12.1. Tại sao cấu trúc như vậy?

- `framework/` giữ các abstraction tổng quát: compiler, contracts, engine, registry.
- `capabilities/` giữ các logic nghiệp vụ; scheduling chỉ là **placement capability**, không ngang hàng với framework.
- `gateway/` chỉ xử lý HTTP, auth, route và submission; không chứa scheduling algorithm.
- `infrastructure/` giữ adapter database và Proxmox API để business layer không phụ thuộc HTTP chi tiết.
- `runtimes/` xác định **ai chạy** bootstrap, worker loop và background reconciliation.
- `definitions/` chứa **seed/examples**, không phải nguồn sự thật của workflow đang active sau khi hệ thống đã hỗ trợ runtime UI. Source of truth lúc đó là PostgreSQL.

## 13. Deployment, cấu hình và vận hành

### 13.1. Công nghệ đề xuất

| Nhóm | Công nghệ |
|:--|:--|
| Language / API | Python, FastAPI, Pydantic |
| Async HTTP | HTTPX hoặc Proxmox adapter có contract tương đương |
| Database / queue | PostgreSQL, SQLAlchemy, Alembic |
| Workflow config | YAML/JSON, validated schema |
| Runtime | Python asyncio workers, PostgreSQL lease/claim |
| Deployment | Docker Compose |
| Metrics/tracing | Prometheus, OpenTelemetry (triển khai sau) |
| Infrastructure target | Proxmox VE API, Guacamole do Website quản lý |

### 13.2. Startup và runtime loading

**Gateway startup** (FastAPI lifespan):

1. Init DB connections, security, Proxmox HTTP client.
2. Discover Action contracts từ installed/trusted packages.
3. Load route management services, compiler và Workflow Store.
4. Kiểm tra readiness; **không cần compile lại toàn bộ active workflow từ file mỗi lần startup**.

**Worker startup** (`python -m app.runtimes.worker`):

1. Init DB, Proxmox client và dependencies.
2. Discover Action implementations vào local registry.
3. Khởi tạo DAG Engine dùng Action Registry.
4. Start job claim loop, renew leases và phục hồi những job có thể tiếp quản.

**Runtime workflow publish:** Management API nhận YAML/JSON → compile bằng local Action contracts → validate availability ở worker deployment → persist plan → atomic activate. Job mới tham chiếu active plan; job cũ giữ plan cũ.

**Runtime policy publish:** Management API validate policy → persist/activate → job mới lấy policy mới hoặc snapshot tương ứng. Không hot-load Python class từ người dùng.

### 13.3. Môi trường triển khai Proxmox mẫu

Một cụm dev/test có thể gồm `pve1`, `pve2`, `pve3`, network VXLAN/SDN như `vnet20`, và shared NFS storage như `nas-nfs`. Các tên này là **ví dụ cấu hình**, không phải giá trị hardcode trong framework. Các cluster khác được khai báo qua environment và metadata.

Các biến nhạy cảm (Proxmox token, DB password, Guacamole credentials) phải truyền qua secret/env management, **không commit vào repository hay file workflow**.

## 14. Bảo mật, observability và testing

### 14.1. Bảo mật

- Chỉ người có quyền Workflow Admin mới publish workflow hoặc route override có side effect.
- Người thực thi workflow phải có **invoke permission** và quyền với VM/cluster/tenant tương ứng.
- Action Catalog công bố metadata/schemas nhưng không cho upload executable code.
- `proxmox.request` phải dùng allowlist method/path, scope quyền và kiểm tra tenant; chặn endpoint quản trị ngoài phạm vi.
- Không cho workflow gọi Python eval, shell tùy ý hoặc URL do người dùng tùy chọn (tránh SSRF/credential abuse).
- Tách quyền Proxmox của Gateway/Worker theo nguyên tắc least privilege; ghi audit log đối với mutation.
- Bảo vệ dữ liệu input/output của job vì có thể chứa IP, usernames và cấu hình hạ tầng; không lưu secrets trong logs.

### 14.2. Quan sát hệ thống

Các metric/log cần ưu tiên:

- Số lượng job `QUEUED`, `RUNNING`, `WAITING`, `FAILED`.
- Job queue latency, workflow duration, step duration và retry attempts.
- Số Action execution theo tên/status; Proxmox response/error rate.
- Resource reservation count, available capacity và allocation drift.
- Worker lease expiration / recovery count.
- Correlation ID nối HTTP request → Job → Step → Proxmox task UPID.

### 14.3. Chiến lược kiểm thử

1. **Unit:** schema/compiler, DAG validation, expression binding, policy scoring.
2. **Contract:** input/output Action, permissions và side-effect metadata.
3. **Integration:** Gateway–PostgreSQL, Worker–PostgreSQL, Proxmox mock adapter.
4. **Concurrency:** nhiều Worker claim job; nhiều placement request đồng thời; không vượt reservation limit.
5. **Failure injection:** HTTP timeout sau Proxmox POST, worker crash sau khi nhận UPID, DB restart, task thất bại.
6. **E2E:** provision VM, migrate, lab nhiều VM, readiness, website hiển thị trạng thái.

## 15. Lộ trình triển khai và tiêu chí nghiệm thu

### 15.1. Giai đoạn 1 — Framework foundation

- [ ] Định nghĩa Action Contract, local Action Registry, trusted discovery.
- [ ] Workflow DSL + Compiler: schema, dependencies, input/output binding, security checks.
- [ ] Workflow Management API: draft, publish, activate, list, execute.
- [ ] PostgreSQL schema cho workflows/plans/jobs/steps.

**Hoàn thành khi:** tạo workflow YAML mới từ Action có sẵn qua API, publish và chạy mà không restart Worker.

### 15.2. Giai đoạn 2 — Durable execution

- [ ] PostgreSQL queue, atomic claim, lease, heartbeat, backoff.
- [ ] DAG Engine, Action Runner, step persistence, resume.
- [ ] Proxmox API adapter, UPID tracking, reconciliation.
- [ ] Idempotency và side-effect-aware retry.

**Hoàn thành khi:** Worker crash rồi restart vẫn resume job, không tạo VM trùng do retry.

### 15.3. Giai đoạn 3 — Resource scheduling

- [ ] Resource collector, accounting, hard filtering và strategy scoring.
- [ ] Atomic reservation + VM inventory.
- [ ] Policy Editor hoặc Management API cho weighted scoring/constraints.
- [ ] Workflow `vm.provision` không yêu cầu hardcode node.

**Hoàn thành khi:** nhiều request đồng thời được phân phối có kiểm soát tới các node phù hợp, không vượt limit nội bộ.

### 15.4. Giai đoạn 4 — Extensibility validation

- [ ] Workflow migration dùng lại placement và task tracking.
- [ ] Multi-VM lab: group reservation, bounded parallel, partial failure.
- [ ] Route override cho các endpoint mới ngoài provisioning.
- [ ] UI cho Workflow Builder, Policy Editor và read-only Action Catalog.

**Hoàn thành khi:** thêm workflow mới từ các Action có sẵn, không sửa Gateway/Engine; đổi placement weights bằng policy runtime; thêm strategy mới chỉ cần capability plugin.

## 16. Giới hạn và vấn đề cần quyết định khi hiện thực

| Vấn đề | Quyết định hiện tại / lưu ý |
|:--|:--|
| API tương thích Proxmox | Pass-through giữ response gốc khi khả thi; override dùng job contract riêng |
| Dynamic Action | Không hỗ trợ executable code upload qua UI trong baseline |
| Action compatibility | Dùng contract hash/deployment compatibility check; không bắt semantic version |
| Policy snapshot | Pin policy đã resolve để bảo toàn hành vi của job |
| Workflow route conflict | Publish phải validate trùng method/path, specificity và precedence |
| Multi-tenant | Required: auth, ownership, quotas và permissions trên mọi mutation |
| Workflow expression | DSL sandboxed, không Python `eval` |
| External side effect | Không bảo đảm exactly-once chỉ bằng DB lease; cần idempotency/reconcile |
| NFS/shared storage | Tính capacity theo datastore dùng chung, không nhân theo node |
| Metrics và UI | Có thể triển khai incremental, không chặn phát triển core |
| High availability | Phụ thuộc HA/quorum/storage/network của Proxmox, ngoài placement ban đầu |

> 💡 **Quy tắc mở rộng:** Nếu người dùng muốn kết hợp thao tác đã có → tạo Workflow. Nếu muốn thay đổi cách chọn/đánh giá → tạo hoặc sửa Policy. Nếu cần thuật toán hoàn toàn mới → viết Strategy Plugin. Nếu cần khả năng thực thi mới → bổ sung Action/Capability Plugin. **Không phải chỉnh sửa core Engine cho từng nghiệp vụ.**

## 17. Tài liệu tham khảo

- [Proxmox VE Administration Guide](https://pve.proxmox.com/pve-docs/pve-admin-guide.pdf) — tổng quan VM, storage, cluster và task management.
- [Proxmox VE API Viewer](https://pve.proxmox.com/pve-docs/api-viewer/index.html) — kiểm tra endpoint, request parameters và task semantics theo phiên bản Proxmox đang dùng.
- [PostgreSQL SELECT / SKIP LOCKED](https://www.postgresql.org/docs/current/sql-select.html) — cơ sở cho multiple-consumer job claiming.

---

**Tóm tắt một câu:** *Proxmox Gateway là một control plane mở rộng: Gateway chọn proxy hoặc workflow; workflow được người dùng publish động vào PostgreSQL; Worker dùng DAG Engine thực thi compiled plan qua những Action/Capability được đăng ký từ code đã deploy; Resource Scheduling là một capability có policy cấu hình động và reservation an toàn dưới cạnh tranh.*
