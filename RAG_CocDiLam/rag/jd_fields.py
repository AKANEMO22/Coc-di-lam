"""Conservative, extractive JD fields. Unmapped web text is never a claim."""

import re


_HEADINGS = re.compile(
    r"\b(?P<requirements>requirements?|qualifications?|candidate requirements|"
    r"yêu cầu(?: ứng tuyển| ứng viên| công việc)?|tiêu chuẩn ứng viên)\b"
    r"|\b(?P<responsibilities>responsibilities|job description|what you will do|"
    r"what you'll do|mô tả công việc|nhiệm vụ|trách nhiệm công việc)\b"
    r"|\b(?P<excluded>benefits?|quyền lợi(?: dành cho bạn)?|phúc lợi|"
    r"cách thức ứng tuyển|cách ứng tuyển|how to apply|apply now|"
    r"about (?:us|the company)|mô tả công ty|thông tin công ty|"
    r"vị trí đang tuyển|related jobs|similar jobs|việc làm liên quan|"
    r"xem thêm việc làm|tìm kiếm phổ biến|privacy policy|chính sách bảo mật)\b",
    re.I,
)
_NOISE = re.compile(
    r"https?://|www\.|\b(?:đăng nhập|đăng ký|ứng tuyển tại|điền thông tin|"
    r"nhanh tay|apply now|sign in|log in|subscribe|cookie|"
    r"follow us|theo dõi|bản quyền|copyright|hotline|liên hệ|"
    r"câu hỏi phỏng vấn|luyện phỏng vấn|đã ứng tuyển|thỏa thuận)\b",
    re.I,
)
_REQUIREMENT = re.compile(
    r"\b(?:must have|experience (?:with|in)|knowledge of|proficien\w*|"
    r"familiar\w*|preferred|yêu cầu|kinh nghiệm|thành thạo|kỹ năng)\b", re.I,
)
_RESPONSIBILITY = re.compile(
    r"^(?:you will |you'll |bạn sẽ )?(?:build|develop|design|implement|maintain|"
    r"collaborate|deploy|test|analyze|xây dựng|phát triển|thiết kế|triển khai|"
    r"kiểm thử|phân tích|tham gia phát triển)\b", re.I,
)


def extract_jd_fields(content: str) -> dict[str, list[str]]:
    """Keep bounded source sentences only in their JD section; omit uncertain text."""
    result: dict[str, list[str]] = {"responsibilities": [], "requirements": []}
    seen: set[str] = set()
    section = None

    def collect(text: str, active: str | None) -> None:
        for part in re.split(r"\n+|\s*[•●▪]\s*|\s+[-–—]\s+|(?<=[.!?;])\s+", text):
            value = re.sub(r"\s+", " ", part).strip(" \t:#*-–—✅📌🔥👉")
            if not 12 <= len(value) <= 400 or _NOISE.search(value):
                continue
            field = active
            if field is None:
                field = "requirements" if _REQUIREMENT.search(value) else "responsibilities" if _RESPONSIBILITY.search(value) else None
            key = value.casefold()
            if field not in result or key in seen or len(result[field]) >= 12:
                continue
            seen.add(key)
            result[field].append(value)

    start = 0
    for match in _HEADINGS.finditer(str(content or "")):
        collect(content[start:match.start()], section)
        section = match.lastgroup
        start = match.end()
    collect(content[start:], section)
    return result
