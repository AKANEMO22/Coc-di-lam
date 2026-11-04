# RoleUp RAG / GraphRAG / HybridRAG

Module nằm ở `src/rag/`, tương thích với pipeline cũ qua `HybridGraphRAGRetriever`. JSON dataset hiện tại được giữ nguyên. Index chứa 25 role, 350 câu kỹ thuật và 75 câu STAR; CV không được đưa vào graph/embedding knowledge dùng chung.

## Cài đặt và chạy trên Windows

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-rag.txt
# Configure GEMINI_API_KEY securely in the process environment first.
.\.venv\Scripts\python.exe -m src.rag index --provider gemini --dimensions 768
.\.venv\Scripts\python.exe -m src.rag retrieve --request examples/rag_request.json
$env:RAG_API_TOKEN = 'your-long-random-backend-token'
.\.venv\Scripts\python.exe -m uvicorn src.rag.api:app --host 127.0.0.1 --port 8000 --workers 1
```

Sau khi index Gemini tạo xong, chạy `python -m src.rag calibrate`; export `RAG_DENSE_MIN_SCORE` bằng giá trị `recommended_min_score` của dev, rồi chạy `python -m src.rag evaluate --split test`. Giữ key và threshold trong cùng process environment khi chạy API. Lệnh build/evaluate có thể phát sinh phí API. Không gửi API key trong chat hoặc commit vào Git.

Provider mặc định: Gemini API `gemini-embedding-2`, vector 768 chiều L2-normalize, REST qua httpx; không cần Torch/model local. Query format `task: search result | query: ...`; document format `title: none | text: ...`. Mỗi văn bản là một request `embedContent` riêng vì Embedding 2 gộp nhiều input thành một vector. Không dùng `task_type` cho Embedding 2. Tham khảo [Google embeddings](https://ai.google.dev/gemini-api/docs/embeddings).

API key chỉ đọc từ `GEMINI_API_KEY` hoặc `GOOGLE_API_KEY` trong process environment, không tự đọc legacy `.env`. Provider signature pin model ID/dimensions/protocol, nhưng Google quản lý alias: không cam kết immutable weights revision. Snapshot lưu vectors để rollback. Thay provider cần rebuild toàn index; runtime luôn load provider/dimensions của snapshot, không đổi chỉ bằng env.

Timeout 10 giây mỗi request, tối đa 3 attempts với backoff có giới hạn cho transport/429/5xx; không retry auth/validation errors. Cache document bền vững tại `embedding_cache.db` chỉ chứa hash và vector, giúp tiếp tục build sau lỗi; cache query trong RAM tối đa 128 mục, không ghi query/CV xuống cache disk. Không publish active snapshot khi build thất bại. API lỗi lúc retrieve trả `dense_unavailable`, BM25/Graph vẫn có thể phục vụ trong hybrid mode.

Text gửi Google được che email và chuỗi số dạng điện thoại, nhưng đây không phải anonymization hoàn chỉnh: mô tả project/answer vẫn có thể chứa danh tính hoặc dữ liệu nhạy cảm. Cần review/minimize input, thông báo và xử lý consent theo chính sách dự án trước khi sử dụng CV thật; dùng paid services cho dữ liệu riêng tư và kiểm tra [Google data terms](https://ai.google.dev/gemini-api/terms). Không gọi API thật nếu chưa có key. Không log key, input hay error response body.

Qwen local vẫn là tùy chọn: cài `requirements-neural.txt`, chạy `index --provider neural --model Qwen/Qwen3-Embedding-0.6B`. Query dùng instruction model; document không prefix; input cap 512 token/batch 4, vector 1024 chiều. Docker mặc định chỉ cài runtime Gemini; muốn local neural phải xây image riêng có neural dependencies.

Để thử offline không tải neural model:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-rag.txt
.\.venv\Scripts\python.exe -m src.rag index --provider lsa
```

LSA là baseline TF-IDF/SVD, được ghi rõ trong health, response và benchmark. Không tự đổi neural sang LSA khi model lỗi. Các mode: `bm25`, `dense`, `text`, `graph`, `hybrid`, `auto`.

## Hợp đồng backend

`POST /rag/context` nhận `RetrievalRequest`: candidate/candidate_id, job/job_id hoặc role_id; target_level; query; required_skills; mode; stage; top_k; candidate_pool; token_budget; asked_question_ids; tenant_id. JSON embedded dùng schema Pydantic hiện có của repo.

Backend trả câu hỏi, đáp án, rubric, source refs, graph paths, covered/uncovered requirements, skill evidence và trace. Endpoint này chứa barem: chỉ backend Interviewer/Grader được gọi, không chuyển toàn payload trực tiếp tới browser ứng viên. Browser chỉ cần question_id, question, session_id và level.

Mọi endpoint `/rag/*` yêu cầu `Authorization: Bearer <RAG_API_TOKEN>`. Mỗi instance có một principal và `RAG_TENANT_ID` cố định, caller không được đổi tenant tùy ý. Đây chưa phải hệ thống OAuth nhiều người dùng. Khi tích hợp hệ thống nhiều tài khoản, ánh xạ tenant từ identity đã xác thực của backend và giữ giới hạn truy cập đó trong engine/store.

`RAG_ALLOW_LOCAL=1` là lựa chọn phát triển không có token, chỉ chấp nhận địa chỉ client loopback. Không dùng khi public hoặc đứng sau reverse proxy. Health không chứa CV/token.

`POST /rag/follow-up` nhận session_id, answer, answered_question_id, top_k. `verified_skills` / `failed_skills` chỉ do grader đáng tin gửi. Việc câu trả lời nhắc một kỹ năng không tự tạo trạng thái verified. Câu đã phục vụ được lưu vào `served` để lượt sau không lặp lại; có thể phục vụ top_k=1 nếu muốn đánh dấu chính xác từng câu thực sự được hỏi. Session giữ role/level/index version ban đầu.

`GET /rag/trace/{request_id}` trả trace thuộc principal; trace không chứa CV, tên, email hay query thô. `DELETE /rag/session/{session_id}` xóa phiên. Session/request payload được lưu tại máy trong SQLite trong tối đa 24 giờ; TTL được thực thi khi đọc, `purge()` dọn dữ liệu hết hạn. Cần mã hóa ổ đĩa/backup theo chính sách hệ thống khi dùng CV thật.

`POST /rag/index` với `{}` rebuild dữ liệu từ directory đã cấu hình; không nhận đường dẫn tùy ý. `{"activate_version":"..."}` rollback. Khi xử lý rebuild, một worker tuần tự hóa serving và indexing; tài liệu hướng dẫn một worker duy nhất. Chưa hỗ trợ coordination giữa nhiều process phục vụ chung session.

## Pipeline

1. Resolve role từ JD, không dùng chuyên môn hiện tại của ứng viên để đổi một JD rõ role. Explicit role_id là override.
2. Level ưu tiên explicit target → JD → candidate nếu JD không chỉ rõ cấp bậc ngoài phạm vi. Senior-only trả `unsupported_level`.
3. Filter role/level/stage và câu đã phục vụ trước retrieval. Trạng thái không có role/stage/candidate/job được trả rõ.
4. BM25 + neural cosine retrieval + graph traversal. Mỗi nhánh trả cùng canonical question ID.
5. RRF `sum(weight/(60+one_based_rank))`, chỉ fusion các hits thực sự. Không chèn toàn framework vào thứ hạng giả.
6. Heuristic reranking và coverage/diversity planner. Chưa có neural cross-encoder; API/trace không gọi heuristic là cross-encoder.
7. Hydrate đầy đủ question/answer/rubric, source locator và paths. Budget áp dụng nguyên khối. `token_upper_bound` dùng số byte UTF-8 như bound bảo thủ cho byte tokenizer; budget 24000 trong ví dụ không bảo đảm dùng hết 24000 token thực tế. Model embedding có tokenizer riêng và cắt input 512 token.

Graph được build theo snapshot trong SQLite, adjacency được load vào process. Concept linking gồm alias có boundary, quy tắc từ câu hỏi và các relation định nghĩa trong `ontology.py`. Query duyệt hướng quan hệ, tối đa 3 bước, 40 cạnh/node, 1000 expansions, 250ms; trả trạng thái truncated nếu hết budget. `include_prerequisites` và `graph_max_hops` phục vụ ablation. Paths đi kèm source ref và confidence; không tự suy ra proficiency từ quan hệ prerequisite.

Các liên kết `rule_defined` chưa được chuyên gia duyệt. Nguồn JSON nội bộ được giữ cho mọi bundle; không gán URL nguồn framework cho câu hỏi khi chưa chứng minh liên kết. Toàn bộ 350 câu kỹ thuật hiện thiếu citation_url riêng. Source hashes và index versions giúp audit nhưng không đảm bảo tài liệu gốc đúng về chuyên môn.

Đây là GraphRAG theo miền nghiệp vụ với evidence paths, không phải toàn bộ pipeline Microsoft GraphRAG community/global search. Community summaries, Qdrant/Neo4j adapter và cross-encoder là các mở rộng nếu benchmark chứng minh cần; module hoàn chỉnh hiện dùng vector exact search và graph adjacency phù hợp với quy mô 425 bundles.

## Versioning và xóa nguồn

Snapshot ID được tính từ source JSON, ontology/builder version và model revision. Build trùng dùng lại snapshot; validation/encoding hoàn thành trước khi transaction publish active pointer. Source bị xóa không xuất hiện trong snapshot mới. Snapshots cũ được giữ để rollback; xóa triệt để dữ liệu nguồn cần quy trình retention cho cả snapshots và backup. Session được pin snapshot cũ để tránh thay rubric giữa buổi; muốn áp dụng index mới thì tạo session mới.

```powershell
.\.venv\Scripts\python.exe -m src.rag audit
.\.venv\Scripts\python.exe -m src.rag activate <index-version>
```

## Benchmark và review

```powershell
.\.venv\Scripts\python.exe -m src.rag make-eval --count 150
.\.venv\Scripts\python.exe -m src.rag evaluate --split test
.\.venv\Scripts\python.exe -m src.rag evaluate --split test --require-reviewed
.\.venv\Scripts\python.exe -m src.rag calibrate
```

`data/rag_eval_cases.json` là dữ liệu kiểm thử tổng hợp **chưa được review**, không phải human gold. Mỗi nhóm câu hỏi/paraphrase chỉ nằm trong một split. Labels có question IDs/grades, mode, level và query type. Một số nhãn được tạo từ cùng corpus/ontology nên dùng kiểm tra kỹ thuật, chưa đủ để chứng minh chất lượng ngoài thực tế; nhiều câu liên quan hợp lệ có thể chưa được label.

Reviewer cần kiểm tra relevance, thêm paraphrase thật/alias Việt–Anh, nhóm multi-hop độc lập, CV/JD thật đã ẩn danh, hard negatives, kiểm tra group leakage và sửa `label_origin` thành `human_reviewed` sau review. `--require-reviewed` từ chối unreviewed labels. Không giả mạo review tự động.

Benchmark đo ID-based Recall/Precision/nDCG/MRR, abstention, role/level violations, rubric integrity, latency P50/P95, breakdown role/query type và paired group-bootstrap interval. So sánh BM25, dense, text hybrid, graph, hybrid, bỏ graph expansion và bỏ planner. Không suy ra quality từ duy nhất điểm trung bình.

Faithfulness là `NA` khi chưa có generated response + nguồn độc lập. Legacy `FaithfulnessLLMJudge` cũng không còn trả điểm 0.92 khi lỗi hoặc cho prompt đơn lẻ. Thí nghiệm grounding/độ chính xác chấm điểm cần output Interviewer/Grader và human labels. Báo cáo cũ đã commit là artifact lịch sử, không phải nghiệm thu của module mới.

Gemini/Qwen chưa có ngưỡng được calibrate trong repo: mặc định score floor 0.0 chỉ là cấu hình khởi động, không đảm bảo OOD abstention. Chạy `calibrate` trên split dev, rồi export `RAG_DENSE_MIN_SCORE` trước evaluate và phục vụ API. Benchmark ghi threshold, trace ghi threshold/configured flag; env configured không có nghĩa human-reviewed calibration. Không dùng E5 threshold 0.82 cho Gemini. E5 baseline vẫn dùng ngưỡng cũ khi rollback; LSA mặc định 0.05.

`python -m src.rag evaluate-grounding --samples <file.json>` đánh giá output đã sinh qua các claim annotations được người đánh giá độc lập duyệt. Schema `GroundingSample` gồm response, retrieved_source_locators, claims (text/supported/source_locators), human_reviewed, predicted_grade và reference_grade. Kết quả có faithfulness, citation validity và grading MAE; từ chối claims được đánh dấu supported nhưng không có nguồn trong context. Không tự suy ra human_reviewed từ LLM.

## PDF/text intake

```powershell
.\.venv\Scripts\python.exe -m src.rag parse 'C:\path\candidate.pdf' --kind cv
```

Parser giữ text, skills mention và page/line evidence, không bịa kinh nghiệm, project ownership, must-have/preferred hoặc mức độ thành thạo. Đầu ra cần review/mapping sang CandidateProfile/JobDescription. PDF scan cần OCR ngoài module. JSON schema là input chính cho retrieval.

## Kiểm thử

```powershell
.\.venv\Scripts\python.exe -m pip install pytest openai
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe scripts/smoke_rag.py
```

Suite legacy dùng database/index tạm. Các test mới kiểm tra all modes, multi-hop paths, filter-before-top-k, rubric/source integrity, level overrides, unknown role, session restart/isolation, API auth/validation/body limit, budget, rollback và adapter legacy. Test offline dùng LSA; neural smoke/benchmark được chạy riêng, tránh tải model mỗi test.

## Docker

```powershell
$env:RAG_API_TOKEN = 'your-long-random-backend-token'
docker compose -f compose.rag.yaml build
docker compose -f compose.rag.yaml run --rm rag python -m src.rag index --provider gemini --dimensions 768
docker compose -f compose.rag.yaml up -d
```

Compose bind loopback, persistent index và model cache; token được truyền lúc chạy. Image không chứa legacy .env. Chưa tự publish cloud hay mở firewall. SQLite snapshot/session + một worker là deployment mục tiêu hiện tại; không cam kết tải nhiều người dùng nếu chưa load-test.

## Trạng thái P0–P5 và nghiệm thu

P0: schema, data audit, provenance, explicit fallback và loại bỏ faithfulness giả. P1: BM25 + neural dense + LSA baseline. P2: graph builder, ontology/linking, bounded multi-hop và evidence retrieval. P3: hybrid fusion, coverage planner, session/follow-up, context adapter. P4: benchmark tooling, synthetic suite, review gate, ablation/reporting. P5: authenticated API, CLI, persistence/rollback, Docker packaging và tài liệu tích hợp.

Các phần nghiệm thu cần công việc ngoài code: chuyên gia duyệt nguồn/ontology và gold labels; kiểm thử grading với phản hồi Interviewer thật; load-test trên hạ tầng triển khai; Docker smoke nếu Docker chưa có ở máy phát triển. Không đồng nhất việc hoàn thành code P4/P5 với việc có human sign-off hoặc đã triển khai production.
