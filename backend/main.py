from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

import pdf_service as svc

app = FastAPI(title="PDF Text & Image Editor API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # dev only - restrict this in production
    allow_methods=["*"],
    allow_headers=["*"],
)


def _pdf_error_to_http(e: svc.PDFError):
    status_map = {
        "invalid_pdf": 400,
        "password_protected": 400,
        "empty_pdf": 400,
        "not_found": 404,
        "not_found_keyword": 404,
        "empty_keyword": 400,
        "nothing_to_undo": 400,
        "nothing_to_redo": 400,
    }
    raise HTTPException(status_code=status_map.get(e.code, 400), detail={"code": e.code, "message": e.message})


@app.post("/api/upload")
async def upload(file: UploadFile = File(...)):
    if file.content_type not in ("application/pdf", "application/octet-stream") and not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail={"code": "unsupported_file", "message": "Please upload a .pdf file."})
    content = await file.read()
    try:
        return svc.upload_pdf(content)
    except svc.PDFError as e:
        _pdf_error_to_http(e)


class SearchRequest(BaseModel):
    doc_id: str
    keyword: str
    case_sensitive: bool = False


@app.post("/api/search")
async def search(req: SearchRequest):
    try:
        return svc.search_keyword(req.doc_id, req.keyword, req.case_sensitive)
    except svc.PDFError as e:
        _pdf_error_to_http(e)


class DeleteAllRequest(BaseModel):
    doc_id: str
    keyword: str
    case_sensitive: bool = False


@app.post("/api/delete-all")
async def delete_all(req: DeleteAllRequest):
    try:
        return svc.delete_all(req.doc_id, req.keyword, req.case_sensitive)
    except svc.PDFError as e:
        _pdf_error_to_http(e)


class ReplaceAllRequest(BaseModel):
    doc_id: str
    find: str
    replace: str
    case_sensitive: bool = False


@app.post("/api/replace-all")
async def replace_all(req: ReplaceAllRequest):
    try:
        return svc.replace_all(req.doc_id, req.find, req.replace, req.case_sensitive)
    except svc.PDFError as e:
        _pdf_error_to_http(e)


class DocIdRequest(BaseModel):
    doc_id: str


@app.post("/api/undo")
async def undo(req: DocIdRequest):
    try:
        return svc.undo(req.doc_id)
    except svc.PDFError as e:
        _pdf_error_to_http(e)


@app.post("/api/redo")
async def redo(req: DocIdRequest):
    try:
        return svc.redo(req.doc_id)
    except svc.PDFError as e:
        _pdf_error_to_http(e)


@app.post("/api/reset")
async def reset(req: DocIdRequest):
    try:
        return svc.reset_to_original(req.doc_id)
    except svc.PDFError as e:
        _pdf_error_to_http(e)


@app.get("/api/pdf/{doc_id}")
async def get_pdf(doc_id: str):
    """Serves the current working PDF bytes, for the frontend viewer (pdf.js) to render."""
    try:
        path = svc.get_working_pdf_path(doc_id)
    except svc.PDFError as e:
        _pdf_error_to_http(e)
    return FileResponse(path, media_type="application/pdf")


@app.get("/api/download/{doc_id}")
async def download(doc_id: str):
    try:
        path = svc.get_working_pdf_path(doc_id)
    except svc.PDFError as e:
        _pdf_error_to_http(e)
    return FileResponse(path, media_type="application/pdf", filename=f"edited_{doc_id[:8]}.pdf")


@app.get("/api/images/{doc_id}")
async def list_images(doc_id: str):
    try:
        return svc.list_images(doc_id)
    except svc.PDFError as e:
        _pdf_error_to_http(e)


class DeleteImageRequest(BaseModel):
    doc_id: str
    page: int
    xref: int


@app.post("/api/delete-image")
async def delete_image(req: DeleteImageRequest):
    try:
        return svc.delete_image(req.doc_id, req.page, req.xref)
    except svc.PDFError as e:
        _pdf_error_to_http(e)


class DeleteRegionRequest(BaseModel):
    doc_id: str
    page: int
    bbox: list[float]


@app.post("/api/delete-region")
async def delete_region(req: DeleteRegionRequest):
    try:
        return svc.delete_region(req.doc_id, req.page, req.bbox)
    except svc.PDFError as e:
        _pdf_error_to_http(e)


@app.get("/api/health")
async def health():
    return {"status": "ok"}