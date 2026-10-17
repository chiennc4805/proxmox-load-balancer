# Clone concurrency verification — 2026-10-17

## Phạm vi và kết luận

- Kiểm tra code trong workspace hiện tại, gồm thay đổi `BaseAction` và bỏ workflow `mode`.
- Không thay đổi logic ứng dụng; chưa triển khai tự cấp `newid` trong Gateway.
- Build và chạy stack Compose của dự án: Gateway, PostgreSQL 17 và tracker.
- 49 test khác nhau đều pass, không skip, trong 58.855 giây trên Python 3.12/Linux.
- 11 test concurrency mới được lặp thêm ba lượt: 33/33 pass trong 119.900 giây.
- Live E2E: bốn request HTTP bắt đầu đồng thời, gồm ba VMID khác nhau và một request trùng VMID. Ba clone thành công, request trùng bị từ chối 409.
- Đã quan sát cả ba task Proxmox cùng ở trạng thái `running`, sau đó đều terminal `exitstatus=OK`.
- Không thấy over-allocation trong các ca đã chạy. Đây không phải cam kết chống mọi race condition ngoài Gateway hay kết luận production-ready.

## PostgreSQL thật + Proxmox giả lập

Các ca mới nằm trong `tests/test_clone_concurrency.py`; các ca đã có được chạy lại trong `tests/test_clone_postgres.py`, `tests/test_workflow.py` và `tests/test_default_proxy.py`.

| Ca | Kết quả đã quan sát/khẳng định bằng test |
|:--|:--|
| 9 AUTO request đồng thời, tài nguyên đủ | Placement theo thứ tự reservation: `pve1 → pve2 → pve3`, lặp ba vòng; cursor cuối `pve3` |
| 4 request tranh 3 suất CPU | 3 submit, 1 từ chối; mỗi node một VM |
| 40 request tranh 3 suất CPU | 3 submit; không treo vì thread pool chỉ chờ khóa |
| 12 request tranh RAM, CPU dư | 3 submit, 9 bị từ chối 409 |
| 12 request tranh 7 GiB storage chung, mỗi clone cần 3 GiB | 2 submit, tổng hold 6 GiB; không nhân capacity theo số node |
| 12 request cố định `pve2`, node chỉ đủ một VM | 1 submit, 11 bị từ chối; không fallback, không cập nhật cursor |
| Một node offline, một node thiếu storage | AUTO chỉ chọn node còn eligible |
| VM chưa xuất hiện trong inventory | HELD reservations vẫn chặn cấp phát vượt CPU |
| 20 request cùng VMID | Chỉ một submit và một operation |
| Native submit bị cố ý làm chậm | Đo được ít nhất hai POST đang chồng nhau; khóa admission không bao toàn bộ upstream submit |
| Admission thất bại do hết tài nguyên | Không đổi cursor, không submit thêm |
| Task thất bại trước khi VM được tạo | Release reservation, batch sau sử dụng lại được capacity |
| 8 process riêng, mỗi process có fake inventory/asyncio lock riêng, cùng DB và cluster key | 3 nhận 200, 5 nhận 409; mỗi node đúng một reservation |
| Known upstream rejection / submit timeout | Known rejection release; unknown outcome giữ reservation, không tự retry |
| Poll timeout / task terminal failure | Poll timeout giữ reservation; task failure release |
| Task success | Chỉ consume khi terminal OK và VM config truy cập được; poll lại không đổi terminal state |
| Inventory lag sau task success | CONSUMED allocation vẫn được tính khi VM chưa xuất hiện trong cluster inventory |
| Inventory đã thấy VM còn HELD | Unit test kiểm tra CPU/RAM không bị tính hai lần |
| Eligible set thay đổi | Unit test kiểm tra chọn node tiếp theo và quay vòng; fixed target không fallback |

Lưu ý: fake task-failure case giả định VM chưa được tạo. Chưa chứng minh cleanup của clone thật thất bại giữa chừng.

## Live E2E trên cluster dev

### Chuẩn bị

- Template `9001`, source node `pve1`, full clone, shared storage `nas-nfs`.
- Mỗi clone cần 1 vCPU, 1 GiB RAM và 3 GiB + 4 MiB disk theo estimator hiện tại.
- Script đọc inventory và lịch sử operation để chọn các ID chưa dùng: `104`, `105`, `106`. Gateway vẫn nhận `newid` tường minh.
- Preflight dùng collector/filter hiện có và mô phỏng reservation của cả batch trước khi submit.
- Cursor ban đầu `pve1`; eligible nodes là `pve2`, `pve3`. Thứ tự dự kiến `pve2 → pve3 → pve2`.
- Thread barrier cho bốn request bắt đầu cùng lúc: `104`, `105`, `106`, `104`.
- Thứ tự request tới server không được bảo đảm; round-robin kiểm tra theo thứ tự reservation, không theo thứ tự caller tạo thread.

### Kết quả

| VMID | HTTP của request thắng | Target | Operation | Reservation | VM |
|:--|:--|:--|:--|:--|:--|
| 104 | 200, UPID thật | pve2 | SUCCEEDED | CONSUMED | stopped |
| 105 | 200, UPID thật | pve3 | SUCCEEDED | CONSUMED | stopped |
| 106 | 200, UPID thật | pve2 | SUCCEEDED | CONSUMED | stopped |

- Request thứ hai cho `104` nhận 409 `Requested newid is already reserved`, không tạo operation thứ hai.
- Quan sát được reservation `HELD` khi operation còn `RESERVED`, trước khi submit upstream.
- Cả ba task cùng `running`; kết thúc đều `exitstatus=OK`.
- UPID trả client khớp UPID lưu DB; task node là `pve1`, khác target trong cả ba trường hợp.
- Cursor cuối đọc lại từ DB: `pve2`.
- Không còn HELD reservation cho ba ID này; tổng allocation: pve2 2 vCPU/2 GiB RAM, pve3 1 vCPU/1 GiB RAM.
- Gửi thêm một request fixed target không tồn tại sau batch: HTTP 409, không có operation/reservation cho ID thử này.
- Giữ nguyên ba VM stopped `104`, `105`, `106`, không tự xóa hoặc khởi động chúng.

### Điểm cần chú ý

- Ba response thành công mất lần lượt 62.949, 62.956 và 62.971 giây; request trùng bị từ chối sau 20.890 giây.
- Code lấy inventory khi giữ khóa admission và các cập nhật trạng thái cũng dùng khóa cluster. Đây là nguyên nhân có khả năng góp phần vào độ trễ; chưa profiling để xác định tỷ trọng hay kết luận nguyên nhân duy nhất.
- Chưa có durable queue/backpressure: tăng tải có thể làm request chờ lâu hoặc client timeout dù reservation đã được tạo.

## Chưa test

- Hàng chục/hàng trăm clone thật đồng thời hoặc soak test kéo dài; live batch chỉ có ba clone.
- Cố tình làm cạn CPU/RAM/NFS trên cluster thật; capacity boundaries chỉ dùng fixtures.
- Node/NFS mất kết nối, đầy storage, mất quorum hoặc Proxmox restart giữa clone thật.
- Kill Gateway/tracker/PostgreSQL đúng cửa sổ giữa reserve, submit và persist UPID; restart/recovery thật.
- Hai Gateway HTTP replica hoặc nhiều tracker replica trên cluster thật. Multi-process test mới xác thực phối hợp database bằng fake Proxmox.
- Admin hoặc client ngoài Gateway tạo/xóa/migrate VM đồng thời.
- Clone thật thất bại giữa copy, leftover disks/VM và cleanup/reconciliation.
- Request cancellation/disconnect, database commit outcome không rõ, idempotency/retry.
- Linked clone, nhiều template đồng thời, local storage hoặc cấu hình nguồn khác fixture 9001.
- Trạng thái CONSUMED sau khi VM bị xóa hoặc VMID được tái sử dụng; chống accounting drift dài hạn.
- Fairness dưới tải lâu dài, giới hạn I/O NFS và performance/latency SLO.

## Chạy lại

Từ PowerShell tại root repository, stack Compose đã sẵn sàng:

```powershell
$testWorkspace = (Get-Location).Path
docker compose --env-file src/app/.env run --rm --no-deps --volume "${testWorkspace}:/workspace:ro" --workdir /workspace -e PYTHONPATH=/workspace/src:/workspace/tests -e RUN_POSTGRES_TESTS=1 gateway python -u -m unittest discover -s tests -v
```

Preflight read-only, không tạo VM:

```powershell
docker compose --env-file src/app/.env run --rm --no-deps --volume "${testWorkspace}:/workspace:ro" --workdir /workspace -e PYTHONPATH=/workspace/src gateway python -u scripts/verify_clone_concurrency.py --gateway http://gateway:8000
```

Chỉ thêm `--submit` khi chủ đích tạo thêm tối đa ba VM stopped. Script không retry hoặc cleanup VM. Mỗi lần chạy có thể cấp bộ VMID mới, không phải thao tác idempotent. Namespace dữ liệu test PostgreSQL được tạo riêng và cleanup bởi fixtures; không xóa operation của môi trường dev.
