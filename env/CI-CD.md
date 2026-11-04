# Tự build và cập nhật Docker trên máy 104

Runner GitHub Actions chạy ngay trên CT 104 và chủ động kết nối HTTPS ra GitHub.
Không cần public IP, NAT port hay cho GitHub SSH vào mạng nhà.
Máy 104 phải có Internet và Docker Compose đang hoạt động.
Python hệ thống từ 3.8 được hỗ trợ. Nếu Python chưa có bộ lọc giải nén an toàn,
bộ cài dùng image `python:3.13-slim` để giải nén gói runner đã xác minh SHA-256;
không thay thế Python hệ thống. Image này có thể cần tải một lần khi cài.

Repo speech có thay đổi mới `7b6dbf6` yêu cầu GPU NVIDIA và cài dependency CUDA.
Máy 104 cần được cấp GPU cùng NVIDIA Container Toolkit/runtime tương ứng để chạy phiên bản này.
Mặc định (`gpu-mode` = `auto`) pipeline dùng GPU khi Docker có runtime `nvidia`, nếu không thì chạy các
service đó bằng CPU. Ghi `require` vào `/var/lib/dpl-ci/config/gpu-mode` để bắt buộc GPU (thiếu runtime thì
báo lỗi và giữ nguyên dịch vụ đang chạy). Bộ cài CI không tự cấu hình GPU passthrough trên host Proxmox.

## Phạm vi

| Repo | Nhánh triển khai | Thư mục source |
| --- | --- | --- |
| Coc-di-lam/backend | main | backend |
| Coc-di-lam/core_ai_agent | main | core_agent |
| Coc-di-lam/core-tts-stt | main | core-tts-stt |
| Coc-di-lam/frontend | main | frontend |
| Coc-di-lam/RAG_CocDiLam | main | RAG_CocDiLam |
| Coc-di-lam/env | master | env |

Mỗi lần push, trước khi build, pipeline chạy **lint và test của repo vừa push** trong container
dùng một lần (không có `.env` của máy 104); lỗi ở bước này dừng lại, không build, không deploy:

| Repo | Lint | Test |
| --- | --- | --- |
| env | `ruff` (lỗi cú pháp, tên chưa định nghĩa) | `python -m unittest discover -s tests` |
| backend, core_ai_agent, core-tts-stt, RAG_CocDiLam | `ruff` (như trên) | `uv sync --frozen` rồi `pytest` |
| frontend | `tsc -b` (kiểm tra kiểu) | — |

Cache dependency nằm ở volume Docker `dpl-ci-check-cache`; lần đầu chạy chậm hơn vì phải tải image và gói.

Push vào nhánh bất kỳ sẽ build cả sáu image `app`, `rag-idq`, `speech`, `gateway`, `web`, `minio`.
Chỉ push vào nhánh triển khai ở bảng trên mới cập nhật ứng dụng đang chạy.
Postgres, Redis và RedisInsight dùng image upstream có sẵn.
`RAG_CocDiLam` chạy service `rag-idq` (ID_Q: nhận CV/form, chuẩn bị knowledge pack cho core) từ `compose.service.yaml`.
Token `RAG_IDQ_API_TOKEN`/`RAG_CORE_TOKEN` lấy từ cấu hình `core_agent.env` trên máy 104; thiếu thì Compose dùng
giá trị mặc định chỉ dành cho local. Thêm hai dòng này vào `/var/lib/dpl-ci/config/core_agent.env` trước lần deploy đầu.

Các lượt chạy dùng khóa chung trên máy 104, không triển khai song song.
Mã nguồn được lấy vào release riêng; repo gây sự kiện được ghim đúng commit.
Push nhánh chính đã bị commit mới hơn thay thế sẽ được bỏ qua để tránh triển khai lùi.
Build xong tất cả image mới cập nhật dịch vụ và chờ healthcheck.
Tên Compose vẫn là `dpl-pvan`, giữ các volume hiện có; không chạy `down -v` hoặc prune.

## Cài trên CT 104

Chạy trong shell **bên trong CT 104**, không phải shell host Proxmox.
Nếu đang ở host Proxmox, vào CT bằng `pct enter 104` trước.

Chuẩn bị GitHub fine-grained PAT: resource owner `Coc-di-lam`, chọn sáu repo ở bảng trên,
cấp quyền repository **Administration: Read and write**, **Contents: Read-only** (sáu repo, gồm `RAG_CocDiLam`).
Lần đầu nhập, PAT được lưu ở `/root/.config/dpl-ci/github-pat` (chỉ root, quyền 0600) để các lần chạy lại
installer không hỏi nữa; không lưu vào Git. Đổi PAT: sửa hoặc xóa file đó.
Deploy key do PAT tạo có thể bị GitHub xóa khi PAT bị thu hồi, nên không thu hồi PAT ngay sau cài.

Clone bộ cài từ repo private (Git có thể hỏi username và PAT thay cho password):

```bash
sudo apt-get update
sudo apt-get install -y git python3 ca-certificates
git clone --depth 1 https://github.com/Coc-di-lam/env.git /tmp/dpl-ci-setup
sudo bash /tmp/dpl-ci-setup/ci/install.sh
```

Nếu shell đã là `root` (ví dụ sau `pct enter 104`), bỏ `sudo` ở đầu các lệnh.

Installer tìm thư mục Compose đang chạy hoặc hỏi đường dẫn thật chứa `compose.yaml`,
`core_agent/.env`, `core-tts-stt/.env`, `backend/.env`.
Có thể chỉ rõ đường dẫn:

```bash
sudo bash /tmp/dpl-ci-setup/ci/install.sh --workspace /duong/dan/dpl
```

Thay `/duong/dan/dpl` bằng đường dẫn thật trên CT 104.
Không dùng đường dẫn Windows `C:\Users\...` trên Linux.
Không thay secret ứng dụng bằng các file `.env.example`.

Bộ cài tạo user `dpl-ci`, deploy key chỉ đọc riêng cho mỗi repo và sáu runner service.
Không cần quyền quản trị organization. Script điều phối ở `/opt/dpl-ci`;
cấu hình và release ở `/var/lib/dpl-ci`.
User runner có quyền Docker, vì vậy chỉ người được tin cậy mới được push vào các repo này.
Workflow không nhận sự kiện pull request từ fork.

## Cổng mở ra mạng và mật khẩu

Chỉ service `edge` (nginx, `compose.edge.yaml`) được mở ra LAN; `bind-address` trên 104 chỉ áp cho nó.
Mọi service khác chỉ nghe trên `127.0.0.1` của máy 104 (Postgres, Redis… dùng qua SSH tunnel nếu cần).

| Cổng | Trang | Bảo vệ |
| --- | --- | --- |
| 8030 | Giao diện phỏng vấn (web + `/api/`, `/ws/` của gateway) | công khai |
| 8031 | Core: Studio `/test/`, Inspect `/inspect/` | đăng nhập edge |
| 8032 | RAG / ID_Q: `/idq/`, quản trị tri thức | đăng nhập edge (+ `RAG_ADMIN_PASSWORD`) |
| 8033 | MinIO console | đăng nhập edge (+ `MINIO_ROOT_USER`/`MINIO_ROOT_PASSWORD`) |
| 8034 | RedisInsight | đăng nhập edge |
| 8035 | Trang thử speech | đăng nhập edge |

Tài khoản edge là `EDGE_ADMIN_USER` (mặc định `admin`) / `EDGE_ADMIN_PASSWORD` trong
`/var/lib/dpl-ci/config/core_agent.env`; thiếu mật khẩu thì `edge` không khởi động.
Mở một cổng quản trị bất kỳ sẽ chuyển tới `/__edge/login`; đăng nhập một lần dùng cho mọi cổng quản trị
trong 12 giờ (cookie theo host). Khởi động lại `edge` sẽ đăng xuất mọi người.
Kết nối là HTTP trong LAN: không mở các cổng này ra Internet khi chưa có HTTPS.

## Thông báo nhóm Zalo

Nhập token bot bằng trường nhập ẩn của installer.
Dùng token còn hiệu lực trong [Bot Creator](https://zalo.me/s/botcreator/).
Nếu API trả `401 Unauthorized`, kiểm tra hoặc cấp lại token trước khi cấu hình.
Token không được ghi vào Git. Gói cấu hình riêng dùng để chuyển máy cần được giữ kín
vì có thể chứa token và các secret ứng dụng.

Khi đã có file cấu hình riêng ngoài Git chứa `token` và `chat_id`, có thể nạp trực tiếp:

```bash
sudo bash ci/install.sh --workspace /opt/dpl-104 --zalo-config /opt/dpl-104/.private/zalo.json
```

Bộ cài xác thực bot, gửi xác nhận tới nhóm đã chỉ định và lưu cấu hình với quyền hạn chế;
không hỏi lại token/nhóm hoặc mở phiên polling khi dùng tùy chọn này.

Thêm bot vào nhóm, sau đó **nhắc bot** bằng tin `CI/CD` (hoặc trả lời tin của bot).
Installer dùng sự kiện thật để lấy `chat_id` nhóm; link mời không phải `chat_id`.
Nếu bot có webhook sẵn, installer giữ webhook và yêu cầu nhập `chat_id` trực tiếp.
Zalo mô tả hỗ trợ nhóm đang thử nghiệm; phải kiểm tra bot thực tế nhận được sự kiện nhóm.

Secret Zalo lưu tại `/var/lib/dpl-ci/config/zalo.json` với quyền hạn chế.
Mỗi lần chạy chỉ gửi một tin kết quả (lần chạy bị bỏ qua vì đã có commit mới hơn thì không gửi), ví dụ:

```text
✅ backend build thành công, đã deploy lên CT104
👤 Push bởi: nguyenvana
📝 Commit: 1a2b3c4 – Sửa lỗi đăng nhập
🔗 https://github.com/Coc-di-lam/backend/actions/runs/123
```

Khi lỗi, dòng đầu là `❌ <repo> thất bại ở bước <lint/test | build | deploy | chuẩn bị mã nguồn>`,
kèm dòng khôi phục (`↩️ Đã khôi phục bản đang chạy trước đó`) nếu deploy lỗi.
Lỗi Zalo được báo riêng và không biến lần deploy lỗi thành thành công.
Muốn cài CI trước khi có token hợp lệ, dùng `--skip-zalo`.

Cấu hình hoặc đổi token Zalo sau khi cài:

```bash
sudo python3 /opt/dpl-ci/install.py --configure-zalo
```

## Kiểm tra sau cài

```bash
systemctl list-units 'actions.runner.*' --all
docker compose -p dpl-pvan ps
```

Trên GitHub, vào **Settings → Actions → Runners** từng repo: runner `dpl-104-*` phải Online.
Mở **Actions → DPL - build stack and deploy CT 104 → Run workflow** trên nhánh triển khai
để kiểm tra lượt đầu, rồi thử push một commit mới.
Xác minh đủ: Actions thành công, các service khỏe và nhóm Zalo nhận thông báo.

Mỗi release dùng image tag riêng. Khi healthcheck lỗi, pipeline thử khôi phục cấu hình/image
của release đã biết trước đó; nếu không có baseline hợp lệ thì báo rõ và không giả vờ rollback thành công.
Rollback container không hoàn tác thay đổi dữ liệu/schema đã xảy ra trong ứng dụng.
Release được giữ để điều tra; theo dõi dung lượng `/var/lib/dpl-ci` và Docker theo thời gian.
Nếu máy mất điện, runner bị tắt hoặc job bị hủy/timeout, không bảo đảm chạy được bước rollback
và gửi thông báo cuối. Kiểm tra Actions cùng trạng thái container khi có gián đoạn như vậy.
Khi cập nhật mã bộ cài `ci/*.py`, lấy phiên bản mới của repo `env` và chạy lại installer
để cập nhật script điều phối đã cài trên máy 104.

## Kiểm chứng mã cài đặt

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q ci tests
```

Unit test kiểm tra logic với tiến trình/API được giả lập; không thay thế một lượt build/deploy thật trên 104.

Tài liệu chính thức: [GitHub self-hosted runners](https://docs.github.com/en/actions/how-tos/manage-runners/self-hosted-runners/add-runners),
[Docker Compose up](https://docs.docker.com/reference/cli/docker/compose/up/),
[Zalo sendMessage](https://bot.zaloplatforms.com/docs/apis/sendMessage/),
[Zalo getUpdates](https://bot.zaloplatforms.com/docs/apis/getUpdates/),
[Zalo tích hợp nhóm](https://bot.zaloplatforms.com/docs/build-bot-interaction-with-group/).
