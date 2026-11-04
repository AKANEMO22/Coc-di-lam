# 🐸 CÓC ĐI LÀM (RoleUp) — Nền Tảng Phỏng Vấn OJT Thực Chiến

> **Hệ thống luyện phỏng vấn chuyên sâu bằng giọng nói tương tác thời gian thực kết hợp kiểm toán CV & JD Gap Analysis.**

Toàn bộ hệ thống được phát triển theo kiến trúc **Microservices / Multi-Repository Distributed System**, phân tách thành các dịch vụ độc lập tương ứng với các repository trong tổ chức:

```text
Coc-di-lam/
├── frontend/         --> Giao diện web người dùng (React 18, Vite 6, Tailwind, Live2D Linh)
├── backend/          --> Gateway API cầu nối điều phối (FastAPI, WebSockets, PTT Audio Streaming, Job Sync)
├── core-tts-stt/     --> Dịch vụ giọng nói độc lập (STT ElevenLabs Scribe v2 / Gipformer RNN-T, TTS ElevenLabs v4 / VieNeu Turbo)
├── core_ai_agent/    --> AI Interview Core Engine (Hot-path Speaker, Turn Orchestrator, Evaluation, Postgres, Redis, MinIO)
│                         *(alias 'core_agent' trong cấu hình Docker Compose toàn cục)*
├── RAG_CocDiLam/     --> Dịch vụ RAG & Intake Tri thức (Bóc tách CV/JD, Company Intel, 31 IT Rubrics, Browserbase/Brave)
├── env/              --> Quản lý biến môi trường (.env), Docker Compose toàn hệ thống & CI/CD tự động
└── dpl_evaluation/   --> Bộ công cụ Benchmark thực nghiệm & Đánh giá chất lượng tự động (Skill-Gap vs BM25)
```

---

## 🏛️ Sơ Đồ Kiến Trúc Kết Nối Toàn Hệ Thống

```
                                      ┌────────────────────────────────────────────────────────┐
                                      │                     EDGE (Nginx:8030)                  │
                                      └───────────────────────────┬────────────────────────────┘
                                                                  │
                                                                  ▼ (Proxy pass /)
                                                      ┌─────────────────────────┐
                                                      │    WEB / FRONTEND UI    │
                                                      │      (Nginx:8080)       │
                                                      │  - React 18 / Tailwind  │
                                                      │  - Live2D Avatar "Linh" │
                                                      │  - PTT Spacebar Audio   │
                                                      └───────────┬─────────────┘
                                                                  │
                                   Proxy /api/* & /ws/*           │  (Dev: Vite:5173 -> :8020)
                                                                  ▼
                                                      ┌─────────────────────────┐
                                                      │     BACKEND GATEWAY     │
                                                      │      (FastAPI:8020)     │
                                                      │  - Session Orchestrator │
                                                      │  - CV Check & Job Sync  │
                                                      └───────┬─────┬─────┬─────┘
                                                              │     │     │
                                  WS /v1/stt/stream           │     │     │  REST /api/v1/intakes/cv
                                  POST /v1/tts/stream         │     │     │  POST /api/v1/intakes/{id_q}/prepare
                                                              ▼     │     ▼
                                               ┌──────────────────┐ │ ┌─────────────────────────┐
                                               │   CORE-TTS-STT   │ │ │       RAG_COCDILAM      │
                                               │   (Speech:8010)  │ │ │       (RAG:8200)        │
                                               │  - ElevenLabs/Neu│ │ │  - CV / JD Profiler     │
                                               │  - Scribe v2/Gip │ │ │  - 31 IT Frameworks     │
                                               └──────────────────┘ │ │  - Browserbase & Brave  │
                                                                    │ └────────────▲────────────┘
                                     WS /ws/sessions/{id}/stream    │              │
                                     POST /api/v1/sessions/from-idq │              │ GET /api/v1/internal/intakes/
                                                                    ▼              │     {id_q}/knowledge_pack
                                                      ┌─────────────────────────┐  │ (Bearer + X-RAG-Core-Token)
                                                      │      CORE_AI_AGENT      │  │
                                                      │       (Core:8001)       ├──┘
                                                      │  - Speaker / Assessor   │
                                                      │  - Postgres/Redis/MinIO │
                                                      └─────────────────────────┘

 ─── Quản trị viên truy cập qua Edge Proxy (Yêu cầu xác thực EDGE_ADMIN_PASSWORD): ───
  • Port 8031: Core AI Studio (/test/) & Trace Inspector (/inspect/)
  • Port 8032: RAG / ID_Q Intake Dashboard (/idq/) & Quản trị tri thức
  • Port 8033: MinIO Object Storage Console
  • Port 8034: RedisInsight (Giám sát Redis Streams & CAS State)
  • Port 8035: Speech Service Test Page (Kiểm thử STT / TTS trực quan)
```

---

## 📂 Chi Tiết Công Nghệ & Trách Nhiệm Từng Thư Mục (Repository Breakdown)

---

### 1. 📁 `frontend/` — Giao Diện Người Dùng & Live2D Avatar
Giao diện web trực quan, phụ trách tương tác đàm thoại giọng nói qua phím cách (Push-To-Talk) và hiển thị nhân vật ảo trợ lý:

* **Công nghệ & Framework:**
  * **Core Web:** `React 18.3.1`, `Vite 6.0.5`, `TypeScript 5.6.3`.
  * **Styling & Hiệu ứng:** `Tailwind CSS 3.4.17`, `Framer Motion 11.18.2`, `Lucide React 0.468.0`.
  * **Markdown & Khử độc:** `marked 14.1.4`, `dompurify 3.2.3`.
  * **Điều hướng:** `react-router-dom 6.28.1`.
* **Trải nghiệm tương tác âm thanh & Hình ảnh:**
  * **Live2D Avatar "Linh":** Nhân vật phỏng vấn ảo tương tác được nhúng trực tiếp trong `src/interview/` (model nạp từ `public/linh/`).
  * **Push-To-Talk (PTT):** Nhấn giữ phím Spacebar để kích hoạt `AudioWorkletProcessor` thu âm và downsample chuẩn PCM 16kHz mono (frame 100ms) gửi tới backend; tự động ngắt tiếng phỏng vấn viên (barge-in).
* **Môi trường chạy & Cổng dịch vụ:**
  * Vite Dev Server: `http://localhost:5173` (proxy tự động `/api` và `/ws` về backend 8020).
  * Docker Container: Nginx phục vụ web tĩnh trên cổng `8080`, đồng thời reverse proxy `/api/` và `/ws/` về Gateway; được Edge mapping ra ngoài host tại cổng `8030`.

---

### 2. 📁 `backend/` — Gateway Điều Phối, Bảo Mật, Kiểm Toán CV & Job Sync
Đóng vai trò API Gateway duy nhất giao tiếp với Client, bảo vệ toàn bộ API token và secret của các dịch vụ nội bộ:

* **Công nghệ & Thư viện:**
  * **Web Framework:** `FastAPI 0.115.0+`, `Uvicorn 0.30.0+` (ASGI).
  * **Giao thức mạng:** `websockets 13.0+`, `httpx 0.27.0+` (Async client kết nối dịch vụ nội bộ), `python-multipart 0.0.20+`.
  * **Xác thực & Bảo mật:** `pyjwt 2.8.0` (JWT Auth HS256), `pydantic-settings 2.4.0`.
  * **Xử lý tài liệu:** `pypdf 5.0.0+`, `beautifulsoup4 4.12.0+`.
* **Trách nhiệm chính:**
  * **Điều phối phiên phỏng vấn:** Quản lý vòng đời phiên (`/api/sessions`), khởi tạo session từ gói tri thức RAG (`/api/v1/sessions/from-idq`), theo dõi tiến trình và xuất báo cáo (`/api/sessions/{id}/report`).
  * **Cầu nối âm thanh song công (Voice Bridge):** Điều phối luồng WebSocket hai chiều giữa Client, `core-tts-stt` và `core_ai_agent`, quản lý hàng đợi phát TTS lookahead và ngắt câu (sentence splitting).
  * **Tiền kiểm toán CV (`app/cv_check.py`):** API `/api/cv/analyze` tự động rà soát cấu trúc CV, bóc tách kỹ năng, học vấn, số năm kinh nghiệm và cảnh báo thiếu thông tin trước khi chuyển sang RAG.
  * **Đồng bộ việc làm OJT (`app/job_sync`):** Lập lịch định kỳ thu thập, chuẩn hóa và quản lý danh mục tin tuyển dụng thực tập OJT ngành IT (`/api/jobs`).
  * **Bảo vệ hệ thống:** Giới hạn tốc độ truy cập (Rate Limiting) và quản lý hạn mức tài khoản sinh viên.
* **Cổng dịch vụ:** `http://127.0.0.1:8020`.

---

### 3. 📁 `core-tts-stt/` — Microservice Giọng Nói Chuyên Biệt
Dịch vụ xử lý âm thanh độ trễ thấp độc lập: chỉ nhận **âm thanh $\rightarrow$ văn bản (STT)** và **văn bản $\rightarrow$ âm thanh (TTS)**, không chứa logic hội thoại hay cơ sở dữ liệu:

* **Công nghệ & Mô hình:**
  * **Speech-to-Text (STT) — ElevenLabs Cloud & Local Gipformer:**
    * *Streaming Mode (Realtime WebSocket):* **ElevenLabs Scribe v2 Realtime** (`scribe_v2_realtime`, kết nối trực tiếp `wss://api.elevenlabs.io/v1/speech-to-text/realtime`), bóc băng thời gian thực độ trễ thấp khi ứng viên nhấn giữ phím Spacebar.
    * *Batch Mode (REST API):* **ElevenLabs Scribe v2** (`scribe_v2`, endpoint `/v1/speech-to-text`), xử lý file âm thanh hoàn chỉnh với độ chính xác cao.
    * *Offline / Edge Engine (Local Fallback):* **Gipformer 1.5** (`g-group-ai-lab/gipformer1.5-68M-rnnt`, kiến trúc Zipformer RNN-T 68M) chạy suy luận offline cục bộ qua `sherpa-onnx 1.13.0+` và `onnxruntime 1.24.3+`.
    * *Phân tích độ lưu loát (`app/fluency.py`):* Đo đạc nhịp độ (WPM), thời lượng nói/ngắt nghỉ, phát hiện và thống kê các từ đệm ("ờ", "ừm", lặp từ) của ứng viên qua 2 chế độ `verbatim` (nguyên văn) và `smart` (văn bản sạch).
  * **Text-to-Speech (TTS):**
    * *Cloud Engine:* **ElevenLabs v4 Turbo** (`eleven_v4_turbo`), sử dụng giọng đọc tiếng Việt chuẩn phỏng vấn chuyên nghiệp `Nhu - Calm and Confident` (Voice ID: `A5w1fw5x0uXded1LDvZp`), xuất PCM 48kHz (`pcm_48000`).
    * *Local Engine:* **VieNeu-TTS v3 Turbo** (giọng `Trúc Ly` hoặc `Hải Đăng`, 48kHz), chạy trên GPU NVIDIA qua `torch CUDA fp32` hoặc tối ưu `int8` trên CPU qua ONNX Runtime.
    * Cơ chế tối ưu đệm âm thanh và cache LRU các câu thoại lặp lại (`_CACHE_ENTRIES = 128`).
* **Cổng dịch vụ:** `http://127.0.0.1:8010`.

---

### 4. 📁 `core_ai_agent/` — AI Interview Core Runtime Engine
Trái tim suy luận và điều phối phiên phỏng vấn:

* **Công nghệ & Thư viện:**
  * **Backend Core:** `FastAPI`, `uvicorn`, `pydantic 2.8+`, `pyjwt`, `jsonschema`.
  * **LLM Engine:** `openai 1.40.0+` (tương thích linh hoạt Gemini / OpenAI API endpoints).
  * **State & Message Broker:** `redis 7.4` (Redis Streams worker pool, quản lý trạng thái CAS, chống trùng lặp idempotency qua `message_id`).
  * **Cơ sở dữ liệu bền vững:** `PostgreSQL 17` (`psycopg[binary] 3.3.6+`, `psycopg-pool 3.3.3+`) lưu trữ bền vững catalog archive và toàn bộ vết thực thi (inspector traces).
  * **Tính toán khoa học & Reranking:** `numpy 2.1.0+`, `scipy 1.14.1+`, `rank-bm25 0.2.2`.
  * **Lưu trữ đối tượng:** `boto3 1.40.20+` (MinIO / S3 Storage).
* **Kiến trúc phân tầng:**
  * **Hot path (Speaker):** Gọi LLM tức thời theo từng lượt đàm thoại bám sát persona Tech Lead và stage playbook.
  * **Control plane (Orchestrator):** Đánh giá tiêu chí năng lực song song, theo dõi claim kiểm chứng, lên kế hoạch cho câu hỏi tiếp theo bám sát ngân sách thời lượng.
  * **Assessment & Rubrics:** Đánh giá bằng chứng theo các mỏ neo rubric, đưa ra phán quyết `PASS` / `FAIL` / `NEEDS_REVIEW`.
* **Cổng dịch vụ:** `http://127.0.0.1:8001` (chứa sẵn Studio `/test/` và Trace Inspector `/inspect/`).

---

### 5. 📁 `RAG_CocDiLam/` — Dịch Vụ RAG & Khai Thác Tri Thức Tuyển Dụng (ID_Q)
Dịch vụ tiền phỏng vấn, chịu trách nhiệm tiếp nhận hồ sơ ứng viên (Intake), đối chiếu khoảng trống kỹ năng (Skill Gap) và truy xuất dữ liệu khung năng lực:

* **Công nghệ & Thư viện:**
  * **Core RAG Engine:** `rank-bm25 0.2.2`, SQLite vector index (`data/rag/index.db`), cơ chế LSA offline mặc định (`RAG_EMBEDDING_PROVIDER=lsa`) cùng tùy chọn Neural Embeddings (`Qwen/Qwen3-Embedding-0.6B` qua `sentence-transformers 5.0.0+`).
  * **Document Parsing:** `pypdf`, bóc tách định dạng DOCX nguyên bản qua thư viện chuẩn `zipfile` & `xml.etree.ElementTree` (không phụ thuộc thư viện nặng ngoài `python-docx`), `beautifulsoup4`.
  * **Web Intelligence Search:** Tích hợp **Browserbase API** (headless browser search & fetch nội dung chuyên sâu) kết hợp **Brave Search API** fallback (`RAG_WEB_SEARCH_PROVIDER=auto|browserbase|brave`) để truy cứu thông tin thực tế về doanh nghiệp phỏng vấn.
* **Nhiệm vụ chính:**
  * Khởi tạo gói tri thức (`Knowledge Pack`) gồm: hồ sơ ứng viên đã trích xuất, mô tả công việc (JD), **31 khung năng lực chuyên ngành IT** (chuẩn hóa trên 27 role ID) và bộ câu hỏi phỏng vấn chuẩn hóa (Intern/Fresher/Junior từ corpus, Mid/Senior sinh động bám sát hồ sơ & rubric).
* **Cổng dịch vụ:** `http://127.0.0.1:8200` (Intake Dashboard tại `/idq/`).

---

### 6. 📁 `env/` — Quản Lý Cấu Hình Môi Trường, Docker Toàn Cục & CI/CD
Kho chứa cấu hình tập trung và kịch bản triển khai tự động:

* **Thành phần cốt lõi:**
  * `compose.yaml`: File Docker Compose tổng hợp nạp tất cả các service con (`core_ai_agent` alias `core_agent`, `RAG_CocDiLam`, `core-tts-stt`, `backend`, `frontend`, `edge`).
  * `compose.edge.yaml`: Cấu hình Nginx Edge Reverse Proxy bảo vệ toàn bộ mạng nội bộ và điều hướng các dashboard quản trị.
  * `compose.gateway.yaml` & `compose.core.yaml`: Các cấu hình override phục vụ kết nối mạng giữa các container.
  * Mẫu biến môi trường `.env` chuẩn hóa cho từng service (`backend/`, `core-agent/`, `tts-stt/`, `edge/`).
  * `CI-CD.md`: Hướng dẫn cài đặt Runner tự động hóa CI/CD, kiểm thử lint/test độc lập, build Docker và gửi thông báo trạng thái qua Zalo Bot API (`ci/zalo.py`).

---

### 7. 📁 `dpl_evaluation/` — Hệ Thống Benchmark & Đánh Giá Thực Nghiệm
Công cụ kiểm thử chất lượng và đo lường độ chính xác phỏng vấn tự động:

* **Nội dung & Công cụ:**
  * `batch_evaluator.py`: Chạy đánh giá hàng loạt kịch bản phỏng vấn theo các chiến lược RAG khác nhau.
  * `evaluate_interactive.py`: Giao diện dòng lệnh kiểm tra tương tác từng câu hỏi với model.
  * `HUONG_DAN_CHI_TIET_HE_THONG_DANH_GIA.md`: Tài liệu hướng dẫn phương pháp luận chấm điểm rubric và đo lường sai số MAE.
  * `strategy_4_rag_report.md` & `evaluation_report.md`: Báo cáo kết quả thực nghiệm chi tiết (chứng minh độ chính xác **84.4%** của chiến lược Knowledge-Guided Skill Gap so với **49.2%** của BM25 truyền thống).

---

## 🚀 Hướng Dẫn Khởi Chạy Toàn Bộ Hệ Thống Bằng Docker

### Bước 1: Chuẩn bị cấu hình biến môi trường (`.env`)

Sao chép các file `.env` mẫu trong thư mục `env/` vào các thư mục dịch vụ tương ứng:

```bash
# 1. Cấu hình Core AI Agent (Yêu cầu mật khẩu quản trị và LLM key)
cp env/core-agent/.env.example core_ai_agent/.env
# Chỉnh sửa core_ai_agent/.env:
# - EDGE_ADMIN_PASSWORD=mat_khau_quan_tri_cua_ban  (BẮT BUỘC, nếu thiếu compose sẽ từ chối chạy)
# - LLM_API_KEY=your_gemini_or_openai_api_key      (BẮT BUỘC để AI sinh câu hỏi phỏng vấn)
# - LLM_API_URL / LLM_MODEL                        (Cấu hình endpoint và model tương ứng)

# 2. Cấu hình Backend Gateway
cp env/backend/.env.example backend/.env

# 3. Cấu hình Speech Service (TTS/STT)
cp env/tts-stt/.env.example core-tts-stt/.env
# (Tùy chọn) Bổ sung ELEVENLABS_API_KEY nếu dùng cloud TTS/STT; mặc định hệ thống tự fallback local
```

> [!NOTE]
> **Đồng bộ định danh thư mục:**
> Cấu hình `compose.yaml` toàn cục tham chiếu đường dẫn `core_agent/`. Nếu thư mục trên máy bạn là `core_ai_agent`, hệ thống đã hỗ trợ sẵn Directory Junction / Symlink:
> * Windows (PowerShell): `New-Item -ItemType Junction -Path core_agent -Target core_ai_agent`
> * Linux / macOS: `ln -s core_ai_agent core_agent`

---

### Bước 2: Khởi chạy toàn bộ hệ thống

```bash
docker compose up -d --build
```

---

### Bước 3: Danh mục cổng và liên kết truy cập ứng dụng

| Dịch vụ / Giao diện | Địa chỉ truy cập | Ghi chú |
| :--- | :--- | :--- |
| **Giao diện Web Người Dùng (Frontend)** | `http://127.0.0.1:8030` | Phục vụ người dùng luyện phỏng vấn qua Edge Proxy |
| **Frontend Dev Server (Chạy ngoài Docker)** | `http://127.0.0.1:5173` | Chạy lệnh `npm run dev` trong thư mục `frontend` |
| **Backend Gateway API Health** | `http://127.0.0.1:8020/api/health` | Kiểm tra trạng thái hoạt động của Gateway |
| **Speech Testing Page (Trực tiếp)** | `http://127.0.0.1:8010/` | Kiểm thử tính năng STT & TTS cục bộ |

#### 🔐 Hệ Thống Quản Trị & Giám Sát (Yêu cầu mật khẩu `EDGE_ADMIN_PASSWORD`):
Khi truy cập các đường dẫn dưới đây, Nginx Edge sẽ yêu cầu đăng nhập với username mặc định là `admin` (hoặc `EDGE_ADMIN_USER`) và mật khẩu đã đặt ở biến `EDGE_ADMIN_PASSWORD`:

* **Core AI Studio & Test UI:** `http://127.0.0.1:8031/test/`
* **Core Trace Inspector:** `http://127.0.0.1:8031/inspect/` *(Xem chi tiết các lượt đàm thoại và rubric score)*
* **RAG & ID_Q Intake Dashboard:** `http://127.0.0.1:8032/idq/` *(Quản lý tri thức JD, khung năng lực và intake)*
* **MinIO Storage Console:** `http://127.0.0.1:8033` *(Đăng nhập tiếp bằng user/pass MinIO để xem file)*
* **RedisInsight Dashboard:** `http://127.0.0.1:8034` *(Giám sát Redis Streams, hàng đợi câu hỏi và state CAS)*
* **Speech Service Testing Page:** `http://127.0.0.1:8035` *(Kiểm thử STT & TTS qua proxy an toàn)*

---

*© 2026 Cóc Đi Làm (RoleUp). Dự án nguồn mở phục vụ cộng đồng sinh viên chuẩn bị kỳ thực tập OJT.*
