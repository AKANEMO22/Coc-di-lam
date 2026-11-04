# Ami · Phỏng vấn bán thân

Bản thử local: bấm đúp `start.bat` hoặc mở PowerShell tại thư mục này rồi chạy `./start.ps1` hoặc chạy `node server.cjs`. Truy cập http://127.0.0.1:4173. Không mở index.html bằng file:// vì trình duyệt cần tải model qua HTTP. Đóng cửa sổ server để dừng; nếu server đang chạy thì mở URL trực tiếp, không chạy launcher lần nữa. Trên máy khác cần Node.js; launcher có đường dẫn dự phòng cho runtime Codex trên máy hiện tại.

Các nút: lắng nghe, suy nghĩ, mô phỏng nói không âm thanh, chào, gật đầu, mỉm cười, ghi chép, không đồng tình, chưa rõ, trung tính. Các động tác ngắn tự trở về lắng nghe. Khung mặc định 117%, theo giá trị người dùng chọn. Mở “Khung bán thân” để thay độ phóng đại; áo trắng và kính được giữ cố định.

Suy nghĩ: nhắm mắt và chạy hai lượt gật đầu theo motion gốc, kéo chậm toàn bộ nhịp 35%; hai lượt bắt đầu ở giây 0 và 1,35, cùng biên độ. Tổng motion dài 2,6325 giây; sau hai lượt trở về lắng nghe. Không đồng tình: nhắm mắt và lắc đầu nhẹ trong 2,4 giây; góc đầu được giữ theo quỹ đạo xác định để lần đầu và bấm lại nhất quán. Chưa rõ: nghiêng đầu và hơi nhướng một bên chân mày để yêu cầu giải thích thêm.

Nói thử: nhịp miệng 0,9 lần/giây (khoảng 1,11 giây/nhịp); trong lúc nói độ mở luôn từ 0,4 tới 0,95, hé nhẹ ở nhịp khép. Hết 8 giây hoặc chọn hành động khác thì miệng khép hẳn. Mỉm cười: riêng hai lớp môi rộng thêm 12%, mắt mở tối đa 85% và híp nhẹ; vẫn giữ chớp mắt tự nhiên. Hình học môi gốc không bị sửa và được dùng lại khi đổi trạng thái.

Chưa nối AI, micro hay TTS. Khung bán thân và bàn tạo bố cục phỏng vấn, không có rig tư thế ngồi mới. Dùng các motion, expression thật của model. Phần nói thử sử dụng mô phỏng khẩu hình theo thời gian, không phải lip-sync âm thanh.

## Tài nguyên

Thư viện: PixiJS6.5.10 (MIT), pixi-live2d-display0.4.0 (MIT), Live2D Cubism Core (giấy phép riêng của Live2D). Các file được lưu local trong assets để không phụ thuộc CDN khi sử dụng. File model moc3 và motion gốc được giữ nguyên; thêm Think_Double.motion3.json ghép từ motion gật đầu, có tính cả hiệu ứng fade của từng lượt. Tắt áo khoác bằng Param_Jacket và giữ kính bằng Param_Glasses. Logo áo và kẹp tóc được ẩn bằng các lớp Shirt_Logo, Head_Deco, Head_Deco2. Logo trắng trên vở được xóa trong vùng nhỏ của texture_03 bằng ImageGen; các miếng vải đỏ ở cầu vai đổi sang cam ở đủ10 biến thể tư thế. Kích thước atlas, UV và alpha được giữ nguyên. Tên tác giả và nguồn gốc model vẫn được ghi ở trên.

Kiểm tra hành vi tự động: `node --test tests/gesture-behavior.test.cjs`.
