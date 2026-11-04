# Interview Gateway (backend)

Cầu nối giữa frontend, core AI (`core_agent`), dịch vụ ID_Q/RAG (`RAG_CocDiLam`) và speech service
(`core-tts-stt`). Trình duyệt chỉ gọi service này; gateway tự ký service token cho core và giữ token của
speech service và ID_Q, nên không secret nào ra tới client.

```
Browser ══ WS /ws/sessions/{id} (audio + JSON) ══▶ Gateway ──▶ speech WS /v1/stt/stream   (mỗi lần giữ Space)
                                                    │      ──▶ core WS /ws/sessions/{id}/stream
        ◀══ PCM câu hỏi (từng câu) ═════════════════│      ──▶ speech POST /v1/tts/stream (từng câu)
Browser ── REST /api/* ─────────────────────────────▶ Gateway ──▶ core REST (tạo phiên, kế hoạch, báo cáo)
                                                            ──▶ ID_Q REST (CV/form, knowledge pack)
```

Tạo phiên: CV (tệp hoặc nội dung dán, gửi như `cv.txt`) hoặc form hồ sơ → `POST /api/v1/intakes/cv|form`
của ID_Q (trả `id_q`) → `POST /api/v1/intakes/{id_q}/prepare` (trả `pack_id`) → core
`POST /api/v1/sessions/from-idq` với `id_q` + `pack_id`. Core tự lấy knowledge pack (hồ sơ, rubric, câu
hỏi, JD) từ ID_Q theo id; core không tự chạy RAG. Cùng `Idempotency-Key` khi gửi lại ⇒ cùng `id_q`,
cùng `pack_id`, cùng phiên.

## Chạy local

```powershell
copy .env.example .env      # CORE_JWT_SECRET phải trùng JWT_SECRET của core
uv sync
uv run uvicorn app.main:app --host 127.0.0.1 --port 8020
```

Cần core (mặc định `http://127.0.0.1:8001`), ID_Q (`http://127.0.0.1:8200`, `RAG_API_TOKEN` = `RAG_IDQ_API_TOKEN`
của dịch vụ đó) và speech service (`http://127.0.0.1:8010`) đang chạy. `GET /api/health` cho biết gateway có
gọi được ba service đó không.

Docker (gateway + giao diện web): `docker compose up -d --build` → giao diện ở `http://127.0.0.1:8030`,
gateway ở `127.0.0.1:8020`. Container gọi core/speech qua `host.docker.internal` (đổi bằng
`DOCKER_CORE_URL`, `DOCKER_SPEECH_URL`).

## REST

| Route | Core |
|---|---|
| `GET /api/health`, `GET /api/config` | health của core + speech; cấu hình provider của core |
| `GET /api/sessions?q=` | danh sách phiên |
| `POST /api/sessions` | multipart: `payload` (JSON: `candidate_name`, `target_role`, `target_company`, `seniority`, `interview_round`, `duration_minutes`, `interview_language`, `job_description`, `cv_text`, hoặc `profile` {`summary`, `current_role`, `experience_years`, `skills`} khi không có CV), `cv_file` (PDF/DOCX/TXT/MD ≤ 12 MB), `job_description_file` (PDF/DOCX/TXT ≤ 12 MB, core trích nội dung); header `Idempotency-Key`. Trả thêm `id_q`, `pack_id`, `limitations` |
| `GET/PATCH /api/intakes/{id_q}`, `POST /api/intakes/{id_q}/prepare`, `GET /api/intakes/{id_q}/{company_knowledge\|role_profile\|rubric\|question_bank}` | chuyển tiếp tới ID_Q (xem/sửa intake, chuẩn bị lại pack, xem trước pack) |
| `GET /api/sessions/{id}` | trạng thái + form + hồ sơ trích xuất + kế hoạch + transcript (kèm `fluency` của từng câu trả lời) + `last_sequence` |
| `PATCH /api/sessions/{id}/profile`, `/plan` | sửa hồ sơ / kế hoạch khi READY |
| `POST /api/sessions/{id}/start`, `/end`, `/cancel` | bắt đầu (chấp nhận giới hạn kế hoạch), kết thúc + báo cáo, hủy |
| `GET /api/sessions/{id}/report` | báo cáo |
| `GET /api/jobs`, `GET /api/jobs/stats` | Danh sách việc làm IT tổng hợp từ TopCV, ITviec, TopDev, VietnamWorks & thống kê |
| `POST /api/job-sync` | Kích hoạt cào và đồng bộ việc làm mới |
| `GET /admin/jobs` | Bảng điều khiển giao diện Audit Dashboard trực quan |

## WebSocket `/ws/sessions/{id}?after=<last_sequence>`

Giao thức chi tiết ở đầu `app/bridge.py`. Tóm tắt một lượt trả lời:

1. `{"type":"ptt_start"}` khi nhấn Space: gateway dừng giọng người phỏng vấn (ngắt lời) và mở một phiên STT.
   Audio gửi trước khi STT sẵn sàng được giữ lại, không mất chữ đầu.
2. Frame nhị phân PCM16 16 kHz trong lúc giữ phím.
3. `{"type":"ptt_end"}` khi thả phím: STT trả `final` (chữ đã bỏ từ đệm + `verbatim` + `fluency`); gateway
   gửi `candidate_answer` cho core với `vad_metrics = fluency` rồi báo `answer_sent`.
4. Token của core được chuyển tiếp (`{"type":"core"}`) và cắt theo câu để gọi TTS ngay; trình duyệt nhận
   `tts_sentence` (sample rate) rồi PCM. Sự kiện replay từ trước khi kết nối chỉ hiện chữ, không đọc lại.

Core báo `busy` (lượt trước chưa xong) thì gateway gửi lại cùng `message_id`.

## Đăng nhập và bảo mật

Mặc định `AUTH_REQUIRED=false`: giao diện hiện tại chạy như cũ. Bật `AUTH_REQUIRED=true` khi giao diện đã gửi token.

- `GET /api/auth/status` (không cần token) cho giao diện biết có phải đăng nhập và có cần mã truy cập không.
- `POST /api/auth/login` `{student_id, name, access_code}` → `access_token` (JWT HS256, ký bằng `AUTH_JWT_SECRET`, khác
  secret của core). Mã số sinh viên phải có dạng 2 chữ cái và 6 chữ số (ví dụ `HE185962`), tự đổi sang chữ hoa. `ACCESS_CODE` (mã cả lớp) và `STUDENT_ID_PATTERN` (tùy chọn) giới hạn thêm ai được vào. `GET /api/auth/me`.
- Khi bật: mọi `/api/*` (trừ `health` và `login`) cần `Authorization: Bearer <token>`; WebSocket cần `?token=`.
  Mỗi phiên thuộc về người tạo (bảng `owners` trong SQLite `DATABASE_PATH`, Docker dùng volume `/data`); người khác
  gọi vào phiên đó nhận 404 như phiên không tồn tại. `GET /api/sessions` chỉ liệt kê phiên của mình (tối đa
  2000 phiên gần nhất của core được quét). Gateway ghi `metadata.owner` vào phiên ở core để đối chiếu khi cần.
- WebSocket đóng với mã `4401` (chưa đăng nhập), `4403` (Origin không được phép), `4404` (không phải phiên của bạn),
  `4429` (kết nối quá nhanh).
- `APP_ENV=production` từ chối khởi động nếu `CORE_JWT_SECRET` còn là chuỗi mẫu hoặc ngắn hơn 32 ký tự,
  hoặc `CORS_ORIGINS=*`.
- Giới hạn tốc độ (`RATE_*`, 0 = tắt): đăng nhập và WebSocket theo địa chỉ máy khách, tạo phiên theo sinh viên.
  Core giới hạn theo người gọi là `backend` chung cho cả lớp, nên giới hạn theo sinh viên phải nằm ở gateway.
  Phía sau nginx mọi người có cùng địa chỉ; chỉ bật `TRUST_PROXY_HEADERS=true` khi nginx ghi đè
  `proxy_set_header X-Forwarded-For $remote_addr;` (trong cả hai `location`), nếu không khách có thể giả header này.
- Hạn mức phiên mỗi ngày (`QUOTA_SESSIONS_PER_DAY`, 0 = tắt): mỗi sinh viên tạo tối đa ngần ấy phiên trong 24 giờ gần nhất. Xóa phiên không trả lại hạn mức.
- `DELETE /api/sessions/{id}`: chủ phiên xóa phiên của mình, core xóa dữ liệu và gateway gỡ quyền sở hữu.
- Hồ sơ CV trong dịch vụ RAG (`/api/intakes/*`) cũng chỉ chủ phiên truy cập được. Xóa phiên xóa luôn hồ sơ này trong RAG; nếu RAG lỗi, phản hồi ghi `intake_deleted: false`.
- Đồng bộ việc làm: khi `AUTH_REQUIRED=true`, các route ghi (`POST /api/sync`, `/api/job-sync`, `/api/scheduler/start|stop`) cần header `X-Admin-Token` khớp `ADMIN_TOKEN` (để trống = đóng). Các route đọc danh sách việc làm vẫn công khai.
- Tệp CV/JD phải là PDF, DOCX hoặc TXT và phần đầu tệp phải đúng định dạng; `payload` tối đa 200 000 ký tự.
- `GET /live` (không gọi dịch vụ ngoài), `GET /metrics` (Prometheus, cần `METRICS_TOKEN`, tắt nếu để trống),
  header `X-Request-ID` trên mọi phản hồi.

Chưa làm: chứng minh danh tính thật (SSO/OTP; hiện chỉ có mã cả lớp), nhiều worker hoặc nhiều máy (giới hạn tốc độ nằm
trong RAM, owners là SQLite một máy), giao diện chưa có màn hình đăng nhập.

## Kiểm thử

```powershell
uv run pytest
```

Core, STT và TTS đều là bản giả (WebSocket giả, `httpx.MockTransport`), không cần mạng.
