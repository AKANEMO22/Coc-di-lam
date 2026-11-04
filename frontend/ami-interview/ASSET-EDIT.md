# Chỉnh tài nguyên vở

Công cụ: built-in ImageGen.

Prompt: Remove only white PTIT logo, lettering, and underline from red notebook cover; retain geometry, red shading, texture and transparency.

Đích: assets/model/PTIT_SDK.4096/texture_03.png. Chỉ ghép vùng x2818,y2750,width122,height160 từ phần bìa được chỉnh, cân màu với bìa gốc. Không tái tạo toàn bộ atlas. Kích thước4096x4096; ngoài vùng chỉnh không có pixel khác biệt; alpha toàn bộ ảnh được giữ nguyên.

Logo áo là art mesh riêng nên được ẩn qua bộ dựng hình; không sửa texture áo.

## Cầu vai màu cam và kẹp tóc

Công cụ chỉnh màu: built-in ImageGen. Mục tiêu prompt: recolor only the red fabric shoulder tabs to warm orange, preserving white fabric, metal buttons, line art, folds and transparency.

Màu cam từ ảnh tạo được chuyển vào mặt nạ vải đỏ gốc, giữ bóng tương đối và alpha. Có10 mảnh tương ứng các tư thế. texture_00 đổi8427 pixel RGB; texture_03 đổi32525 pixel RGB. Không có thay đổi ngoài mặt nạ và không có thay đổi alpha. Phần bìa vở đã xóa logo không thay đổi.

Kẹp tóc được ẩn bằng Head_Deco và Head_Deco2, không chỉnh sửa tóc.
