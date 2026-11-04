# CI/CD DPL trên CT 104

Người dùng yêu cầu build toàn bộ Docker khi push, cập nhật máy 104 và thông báo nhóm Zalo. Máy hiện tại không có đường SSH tới CT 104; người dùng chọn nhận bộ cài chạy trên CT 104. Không có quyền quản trị runner cấp tổ chức từ credential hiện tại, nên sử dụng năm runner cấp repository, cùng nhãn `dpl-104`, cùng một khóa triển khai toàn máy.

Các repo tham gia: `backend` (main), `core_ai_agent` (main, thư mục core_agent), `core-tts-stt` (main), `frontend` (main), `env` (master). RAG_CocDiLam không thuộc compose tổng hiện tại. Build năm image app, speech, gateway, web, minio. Redis/Postgres/RedisInsight dùng image upstream. Push nhánh khác chỉ build; chỉ nhánh mặc định được cập nhật dịch vụ đang chạy. Không chạy pull_request trên runner này.

Workflow gọi chương trình root-owned `/opt/dpl-ci/pipeline.py`. Nguồn được clone vào release riêng bằng deploy key chỉ đọc cho từng repo; repo gây sự kiện được ghim đúng commit. Lượt build/deploy giữ khóa flock chung, kiểm tra commit nhánh chính đã cũ để không triển khai lùi. Compose dùng tên `dpl-pvan`, không down, không xóa volume, không prune. Build xong toàn bộ mới up --wait. Image có tag riêng cho mỗi lượt; giữ release trước để rollback khi kiểm tra sức khỏe thất bại.

Installer chỉ chạy trên Linux CT 104, kiểm tra Docker Compose và dữ liệu cấu hình sẵn có. Tạo user dpl-ci, deploy keys, runner services. GitHub PAT được nhập ẩn, chỉ dùng trong bộ nhớ cho đăng ký runner/deploy keys; không lưu PAT. Secret ứng dụng copy từ .env hiện tại vào /var/lib/dpl-ci/config. Zalo token và chat_id lưu ngoài Git, quyền hạn chế. Không tự xóa webhook đang tồn tại.

Zalo API gửi sendMessage với chat_id string và text tối đa 2000 ký tự; kiểm tra cả HTTP và ok. Báo bắt đầu, thành công hoặc thất bại kèm repo/commit/link Actions. Thất bại thông báo không được che lỗi triển khai. Token người dùng cung cấp đã trả 401 khi getMe/getWebhookInfo, nên cần nhập token hợp lệ khi cài.

Xác minh: unittest cho lỗi build không chạm dịch vụ, rollback khi health lỗi, bỏ commit chính cũ, nhánh khác không deploy, lỗi/thông báo không lộ token, payload/group discovery. Kiểm tra syntax, workflow YAML, compose thực tế. Chưa tuyên bố chạy thật trên 104 khi chưa có kết quả cài/Actions.
