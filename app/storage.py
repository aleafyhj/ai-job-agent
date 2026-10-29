"""Local resume files: generated keys, bounded validation and safe path resolution."""

import hashlib
from io import BytesIO
from pathlib import Path
from uuid import uuid4
from zipfile import ZipFile

from defusedxml import ElementTree
from fastapi import HTTPException, UploadFile
from fastapi.responses import JSONResponse
from pypdf import PdfReader

MAX_RESUME_BYTES = 10 * 1024 * 1024
MIME_TYPES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


def validate_resume(data: bytes, suffix: str):
    try:
        if suffix == ".pdf":
            if not data.startswith(b"%PDF-"):
                raise ValueError
            reader = PdfReader(BytesIO(data), strict=True)
            if reader.is_encrypted or not 1 <= len(reader.pages) <= 100:
                raise ValueError
        else:
            with ZipFile(BytesIO(data)) as archive:
                entries = archive.infolist()
                if (
                    len(entries) > 512
                    or len({item.filename for item in entries}) != len(entries)
                    or sum(item.file_size for item in entries) > 20 * 1024 * 1024
                ):
                    raise ValueError
                if any(item.filename.lower().endswith("vbaproject.bin") for item in entries):
                    raise ValueError
                types = ElementTree.fromstring(archive.read("[Content_Types].xml"))
                expected = (
                    "application/vnd.openxmlformats-officedocument."
                    "wordprocessingml.document.main+xml"
                )
                if not any(
                    node.attrib.get("PartName") == "/word/document.xml"
                    and node.attrib.get("ContentType") == expected
                    for node in types
                ):
                    raise ValueError
                document = ElementTree.fromstring(archive.read("word/document.xml"))
                if (
                    document.tag
                    != "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}document"
                ):
                    raise ValueError
                relationships = ElementTree.fromstring(archive.read("_rels/.rels"))
                relationship_type = (
                    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
                    "officeDocument"
                )
                if not any(
                    node.attrib.get("Type") == relationship_type
                    and node.attrib.get("Target", "").lstrip("/") == "word/document.xml"
                    and node.attrib.get("TargetMode") != "External"
                    for node in relationships
                ):
                    raise ValueError
    except Exception as exc:
        # Neither parser messages nor document content should be reflected to clients.
        raise HTTPException(422, "简历文件损坏、加密或格式不受支持") from exc


def resolve_artifact(root: Path, key: str) -> Path:
    root = root.resolve()
    path = (root / key).resolve()
    if not path.is_relative_to(root) or path == root:
        raise HTTPException(404, "文件不存在")
    return path


def save_resume(file: UploadFile, root: Path) -> dict:
    filename = (file.filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    if not filename or len(filename) > 200 or any(ord(char) < 32 for char in filename):
        raise HTTPException(422, "无效的简历文件名")
    suffix = Path(filename).suffix.lower()
    if suffix not in MIME_TYPES or file.content_type != MIME_TYPES[suffix]:
        raise HTTPException(422, "仅接受 PDF 或 DOCX，文件扩展名与 MIME 类型必须一致")
    data = file.file.read(MAX_RESUME_BYTES + 1)
    if len(data) > MAX_RESUME_BYTES:
        raise HTTPException(413, "简历不能超过 10 MiB")
    if not data:
        raise HTTPException(422, "简历不能为空")
    validate_resume(data, suffix)
    key = f"resumes/{uuid4().hex}{suffix}"
    path = resolve_artifact(root, key)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as stream:
            stream.write(data)
    except OSError:
        path.unlink(missing_ok=True)
        raise HTTPException(503, "简历存储不可用") from None
    return {
        "filename": filename,
        "mime_type": MIME_TYPES[suffix],
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "storage_key": key,
        "path": path,
    }


class RequestBodyLimit:
    """Bound complete write requests before multipart parsing (also for chunked uploads)."""

    def __init__(self, app, max_bytes=12 * 1024 * 1024):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] not in {"POST", "PUT", "PATCH"}:
            return await self.app(scope, receive, send)
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body.extend(message.get("body", b""))
            if len(body) > self.max_bytes:
                response = JSONResponse(
                    status_code=413,
                    content={
                        "error": {
                            "code": "PAYLOAD_TOO_LARGE",
                            "message": "请求体不能超过 12 MiB",
                            "details": {},
                        },
                        "request_id": scope.get("state", {}).get("request_id"),
                    },
                )
                return await response(scope, receive, send)
            if not message.get("more_body", False):
                break
        delivered = False

        async def replay():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        await self.app(scope, replay, send)
