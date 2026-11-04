from fastapi import HTTPException

SIGNATURES = {".pdf": (b"%PDF-",), ".docx": (b"PK",)}
PDF_HEADER_WINDOW = 1024
TEXT_LIMIT = 4096


def check_upload(filename: str, content: bytes) -> None:
    name = filename.lower()
    ext = name[name.rfind("."):] if "." in name else ""
    if ext == ".txt":
        head = content[:TEXT_LIMIT]
        if b"\x00" in head:
            raise HTTPException(415, f"{filename}: không phải tệp văn bản.")
        return
    signatures = SIGNATURES.get(ext)
    if signatures is None:
        raise HTTPException(415, f"{filename}: chỉ nhận PDF, DOCX hoặc TXT.")
    if ext == ".pdf":
        valid = any(sig in content[:PDF_HEADER_WINDOW] for sig in signatures)
    else:
        valid = content.startswith(signatures)
    if not valid:
        raise HTTPException(415, f"{filename}: nội dung không đúng định dạng {ext[1:].upper()}.")
