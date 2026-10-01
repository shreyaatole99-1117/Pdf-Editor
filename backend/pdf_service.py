"""
Core PDF editing logic, built on PyMuPDF (fitz).

Design notes:
- Each uploaded PDF gets a `doc_id`. We keep:
    originals/<doc_id>.pdf   -> never touched after upload
    working/<doc_id>.pdf     -> current editable state
    history/<doc_id>/        -> undo stack of previous working states (as raw bytes on disk)
- Text delete/replace uses real redaction (page.add_redact_annot + apply_redactions),
  which removes the underlying content-stream operators for the matched glyphs -
  not a white-box overlay. Extracted text after deletion no longer contains the keyword.
- Replace re-inserts text after redaction using a fallback font matched on
  family/weight/style (see _pick_fallback_font), since the original PDF's
  embedded font is almost always subsetted (only contains the glyphs actually
  used) and may not contain glyphs for the replacement string.
- Images: list_images/delete_image handle individual image objects (by xref).
- delete_region deletes whatever falls inside an arbitrary user-drawn box -
  text and/or image content, only the overlapping part of each.
"""

import fitz  # PyMuPDF
import os
import uuid
import shutil
import tempfile
from dataclasses import dataclass, field
from typing import Optional

BASE_DIR = os.path.join(os.path.dirname(__file__), "storage")
ORIGINALS_DIR = os.path.join(BASE_DIR, "originals")
WORKING_DIR = os.path.join(BASE_DIR, "working")
HISTORY_DIR = os.path.join(BASE_DIR, "history")

for d in (ORIGINALS_DIR, WORKING_DIR, HISTORY_DIR):
    os.makedirs(d, exist_ok=True)


class PDFError(Exception):
    """Raised for user-facing PDF errors (invalid/corrupt/password-protected/etc.)."""
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


@dataclass
class DocState:
    doc_id: str
    history_stack: list = field(default_factory=list)   # list of file paths, oldest -> newest, for undo
    redo_stack: list = field(default_factory=list)       # for redo
    page_count: int = 0
    is_scanned: bool = False


# in-memory registry of doc states (paired with on-disk files). Fine for a single-process dev server.
_DOCS: dict[str, DocState] = {}


def _working_path(doc_id: str) -> str:
    return os.path.join(WORKING_DIR, f"{doc_id}.pdf")


def _original_path(doc_id: str) -> str:
    return os.path.join(ORIGINALS_DIR, f"{doc_id}.pdf")


def _history_dir(doc_id: str) -> str:
    p = os.path.join(HISTORY_DIR, doc_id)
    os.makedirs(p, exist_ok=True)
    return p


def _save_and_replace(doc: fitz.Document, target_path: str):
    """fitz refuses to save a doc over the same path it was opened from
    (unless doing an incremental save), so write to a temp file and swap it in."""
    fd, tmp_path = tempfile.mkstemp(suffix=".pdf", dir=os.path.dirname(target_path))
    os.close(fd)
    doc.save(tmp_path, deflate=True)
    doc.close()
    os.replace(tmp_path, target_path)


def _open_doc(path: str) -> fitz.Document:
    try:
        doc = fitz.open(path)
    except Exception as e:
        raise PDFError("invalid_pdf", f"Could not open PDF: {e}")
    if doc.is_encrypted:
        try:
            doc.load_page(0)
        except Exception:
            doc.close()
            raise PDFError("password_protected", "This PDF is password-protected.")
    if doc.page_count == 0:
        doc.close()
        raise PDFError("empty_pdf", "The PDF has no pages.")
    return doc


def _page_has_extractable_text(page: fitz.Page) -> bool:
    return len(page.get_text("text").strip()) > 0


def upload_pdf(file_bytes: bytes) -> dict:
    if not file_bytes:
        raise PDFError("empty_pdf", "Uploaded file is empty.")

    doc_id = uuid.uuid4().hex
    orig_path = _original_path(doc_id)
    with open(orig_path, "wb") as f:
        f.write(file_bytes)

    try:
        doc = _open_doc(orig_path)
    except PDFError:
        os.remove(orig_path)
        raise

    page_count = doc.page_count
    is_scanned = not any(_page_has_extractable_text(doc.load_page(i)) for i in range(page_count))
    doc.close()

    shutil.copyfile(orig_path, _working_path(doc_id))

    state = DocState(doc_id=doc_id, page_count=page_count, is_scanned=is_scanned)
    _DOCS[doc_id] = state

    return {
        "doc_id": doc_id,
        "page_count": page_count,
        "is_scanned": is_scanned,
    }


def _get_state(doc_id: str) -> DocState:
    state = _DOCS.get(doc_id)
    if state is None:
        raise PDFError("not_found", "Unknown document id. Upload the PDF again.")
    return state


def search_keyword(doc_id: str, keyword: str, case_sensitive: bool = False) -> dict:
    if not keyword or not keyword.strip():
        raise PDFError("empty_keyword", "Search keyword cannot be empty.")

    _get_state(doc_id)
    doc = _open_doc(_working_path(doc_id))

    occurrences = []
    pages_with_hits = set()

    for page_index in range(doc.page_count):
        page = doc.load_page(page_index)
        if case_sensitive:
            rects = page.search_for(keyword)
        else:
            rects = page.search_for(keyword, flags=fitz.TEXT_PRESERVE_WHITESPACE) or []
            manual = _case_insensitive_search(page, keyword)
            rects = _merge_rects(rects, manual)

        for r in rects:
            occurrences.append({
                "page": page_index + 1,
                "bbox": [r.x0, r.y0, r.x1, r.y1],
            })
            pages_with_hits.add(page_index + 1)

    doc.close()

    if not occurrences:
        raise PDFError("not_found_keyword", f'"{keyword}" was not found in this PDF.')

    return {
        "keyword": keyword,
        "case_sensitive": case_sensitive,
        "count": len(occurrences),
        "pages": sorted(pages_with_hits),
        "occurrences": occurrences,
    }


def _case_insensitive_search(page: fitz.Page, keyword: str) -> list:
    kw_lower = keyword.lower()
    results = []
    words = page.get_text("words")
    kw_tokens = kw_lower.split()

    if len(kw_tokens) == 1:
        for w in words:
            if w[4].lower() == kw_tokens[0]:
                results.append(fitz.Rect(w[0], w[1], w[2], w[3]))
        if kw_lower not in [w[4].lower() for w in words]:
            for w in words:
                if kw_lower in w[4].lower():
                    results.append(fitz.Rect(w[0], w[1], w[2], w[3]))
    else:
        variants = {keyword, keyword.lower(), keyword.upper(), keyword.title()}
        for v in variants:
            results.extend(page.search_for(v))
    return results


def _merge_rects(a: list, b: list) -> list:
    merged = list(a)
    for rb in b:
        if not any(abs(ra.x0 - rb.x0) < 0.5 and abs(ra.y0 - rb.y0) < 0.5 for ra in merged):
            merged.append(rb)
    return merged


def _snapshot_before_edit(doc_id: str):
    state = _get_state(doc_id)
    snap_path = os.path.join(_history_dir(doc_id), f"{len(state.history_stack)}_{uuid.uuid4().hex}.pdf")
    shutil.copyfile(_working_path(doc_id), snap_path)
    state.history_stack.append(snap_path)
    state.redo_stack.clear()


def delete_all(doc_id: str, keyword: str, case_sensitive: bool = False) -> dict:
    result = search_keyword(doc_id, keyword, case_sensitive)

    _snapshot_before_edit(doc_id)

    working_path = _working_path(doc_id)
    doc = _open_doc(working_path)

    total_removed = 0
    for page_index in range(doc.page_count):
        page = doc.load_page(page_index)
        rects = [o["bbox"] for o in result["occurrences"] if o["page"] == page_index + 1]
        for bbox in rects:
            page.add_redact_annot(fitz.Rect(*bbox))
            total_removed += 1
        if rects:
            page.apply_redactions(images=0)

    _save_and_replace(doc, working_path)

    return {
        "keyword": keyword,
        "removed_count": total_removed,
        "pages_affected": result["pages"],
    }


# ---------------------------------------------------------------------------
# Font matching for Replace All
# ---------------------------------------------------------------------------
# PyMuPDF span "flags" bitmask (from insert_text/get_text("dict") testing):
#   1  = superscript
#   2  = italic
#   4  = serifed (Times-like)
#   8  = monospaced (Courier-like)
#   16 = bold
_FLAG_ITALIC = 2
_FLAG_BOLD = 16

# PyMuPDF's built-in Base-14 Helvetica (sans-serif) aliases - the only
# family used now, see _pick_fallback_font for why.
_HELV = {(False, False): "helv", (True, False): "hebo", (False, True): "heit", (True, True): "hebi"}


def _pick_fallback_font(font_name: str, flags: int) -> str:
    """Maps an original span's style to a built-in font matching its
    bold/italic weight at the matched size.

    Originally this also tried to detect serif-vs-sans-vs-monospace family
    from the span's "serif"/"monospace" flag bits and font-name keywords,
    but on some PDFs (confirmed: an engineering/CAD title block) those flags
    don't reliably reflect the actual rendered font, causing a plain
    sans-serif label to come back flagged as serif and the replacement text
    to render in a visibly different, wrong-looking typeface. Since the
    overwhelming majority of real documents use a sans-serif family for body
    text anyway, family detection was dropped - only bold/italic and size
    (handled by the caller) are matched now, which is both simpler and
    matches what was actually asked for."""
    name_lower = (font_name or "").lower()
    bold = bool(flags & _FLAG_BOLD) or "bold" in name_lower or "black" in name_lower or "heavy" in name_lower
    italic = bool(flags & _FLAG_ITALIC) or "italic" in name_lower or "oblique" in name_lower
    return _HELV[(bold, italic)]


def _rect_overlap_area(a: fitz.Rect, b: tuple) -> float:
    """Intersection area between fitz.Rect `a` and a plain (x0,y0,x1,y1) tuple `b`."""
    x0 = max(a.x0, b[0])
    y0 = max(a.y0, b[1])
    x1 = min(a.x1, b[2])
    y1 = min(a.y1, b[3])
    if x1 <= x0 or y1 <= y0:
        return 0.0
    return (x1 - x0) * (y1 - y0)


def _guess_text_style(page: fitz.Page, bbox: fitz.Rect) -> tuple:
    """Best-effort extraction of font size, RGB color, and a matching
    fallback font name for the text actually under bbox.

    get_text("dict", clip=bbox) can return MULTIPLE spans when bbox sits
    close to other text (e.g. a small label crammed next to a big number in
    a dense CAD title block) - it returns anything whose bbox overlaps the
    clip region at all, not just an exact match. Picking the first one
    blindly can grab a neighboring, differently-sized span instead of the
    actual matched text (confirmed: a 7pt label 5px above a 20pt number both
    got returned, first-in-list being the wrong 7pt one). Instead, score
    every candidate span by how much it overlaps our target bbox and take
    the best match - the real match should cover ~all of the target box,
    while a bleeding-in neighbor only clips a small corner of it."""
    size = max(bbox.y1 - bbox.y0, 6.0) * 0.8  # fallback: derive from box height
    color = (0, 0, 0)
    fontname = "helv"  # fallback default

    target_area = max((bbox.x1 - bbox.x0) * (bbox.y1 - bbox.y0), 0.01)
    best_ratio = 0.0

    try:
        d = page.get_text("dict", clip=bbox)
        for block in d.get("blocks", []):
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    overlap = _rect_overlap_area(bbox, span["bbox"])
                    ratio = overlap / target_area
                    if ratio > best_ratio:
                        best_ratio = ratio
                        size = span.get("size", size)
                        c = span.get("color", 0)
                        color = ((c >> 16 & 255) / 255, (c >> 8 & 255) / 255, (c & 255) / 255)
                        fontname = _pick_fallback_font(span.get("font", ""), span.get("flags", 0))
    except Exception:
        pass

    return size, color, fontname


def replace_all(doc_id: str, find: str, replace: str, case_sensitive: bool = False) -> dict:
    if replace is None:
        replace = ""

    result = search_keyword(doc_id, find, case_sensitive)

    _snapshot_before_edit(doc_id)

    working_path = _working_path(doc_id)
    doc = _open_doc(working_path)

    total_replaced = 0
    for page_index in range(doc.page_count):
        page = doc.load_page(page_index)
        occs = [o for o in result["occurrences"] if o["page"] == page_index + 1]
        if not occs:
            continue

        # Capture font size/color/family hints from the original text before redacting it.
        insert_specs = []
        for occ in occs:
            bbox = fitz.Rect(*occ["bbox"])
            size, color, fontname = _guess_text_style(page, bbox)
            insert_specs.append((bbox, size, color, fontname))
            page.add_redact_annot(bbox)
            total_replaced += 1

        page.apply_redactions(images=0)

        for bbox, size, color, fontname in insert_specs:
            if replace:
                # ✅ fontname now matches the original's family/weight/style
                # (serif/sans/mono, bold, italic) instead of always plain
                # Helvetica - see _pick_fallback_font above.
                page.insert_text(
                    (bbox.x0, bbox.y1 - 1),
                    replace,
                    fontsize=size,
                    fontname=fontname,
                    color=color,
                )

    _save_and_replace(doc, working_path)

    return {
        "find": find,
        "replace": replace,
        "replaced_count": total_replaced,
        "pages_affected": result["pages"],
    }


def list_images(doc_id: str) -> dict:
    _get_state(doc_id)
    doc = _open_doc(_working_path(doc_id))

    images = []
    for page_index in range(doc.page_count):
        page = doc.load_page(page_index)
        for info in page.get_image_info(xrefs=True):
            if info["width"] <= 1 and info["height"] <= 1:
                continue
            images.append({
                "page": page_index + 1,
                "xref": info["xref"],
                "bbox": list(info["bbox"]),
                "width": info["width"],
                "height": info["height"],
            })

    doc.close()
    return {"images": images}


def delete_image(doc_id: str, page_number: int, xref: int) -> dict:
    state = _get_state(doc_id)

    if page_number < 1 or page_number > state.page_count:
        raise PDFError("invalid_page", f"Page {page_number} does not exist in this document.")

    _snapshot_before_edit(doc_id)

    working_path = _working_path(doc_id)
    doc = _open_doc(working_path)
    page = doc.load_page(page_number - 1)

    xrefs_on_page = {info["xref"] for info in page.get_image_info(xrefs=True)}
    if xref not in xrefs_on_page:
        doc.close()
        state.history_stack.pop()
        raise PDFError("image_not_found", "That image could not be found on this page (it may have already been removed).")

    try:
        page.delete_image(xref)
    except Exception as e:
        doc.close()
        state.history_stack.pop()
        raise PDFError("image_delete_failed", f"Could not remove this image: {e}")

    _save_and_replace(doc, working_path)

    return {"page": page_number, "xref": xref, "deleted": True}


def delete_region(doc_id: str, page_number: int, bbox: list) -> dict:
    state = _get_state(doc_id)

    if page_number < 1 or page_number > state.page_count:
        raise PDFError("invalid_page", f"Page {page_number} does not exist in this document.")
    if not bbox or len(bbox) != 4:
        raise PDFError("invalid_region", "A selection region needs exactly 4 coordinates.")

    x0, y0, x1, y1 = bbox
    if x1 <= x0 or y1 <= y0:
        raise PDFError("invalid_region", "The selected region is empty.")

    _snapshot_before_edit(doc_id)

    working_path = _working_path(doc_id)
    doc = _open_doc(working_path)
    page = doc.load_page(page_number - 1)

    rect = fitz.Rect(x0, y0, x1, y1)
    try:
        page.add_redact_annot(rect)
        page.apply_redactions(images=2, graphics=2, text=0)
    except Exception as e:
        doc.close()
        state.history_stack.pop()
        raise PDFError("region_delete_failed", f"Could not delete that region: {e}")

    _save_and_replace(doc, working_path)

    return {"page": page_number, "bbox": bbox, "deleted": True}


def undo(doc_id: str) -> dict:
    state = _get_state(doc_id)
    if not state.history_stack:
        raise PDFError("nothing_to_undo", "No edits to undo.")

    working_path = _working_path(doc_id)
    redo_snap = os.path.join(_history_dir(doc_id), f"redo_{uuid.uuid4().hex}.pdf")
    shutil.copyfile(working_path, redo_snap)
    state.redo_stack.append(redo_snap)

    last_snapshot = state.history_stack.pop()
    shutil.copyfile(last_snapshot, working_path)
    os.remove(last_snapshot)

    return {"undone": True, "remaining_undo_steps": len(state.history_stack)}


def redo(doc_id: str) -> dict:
    state = _get_state(doc_id)
    if not state.redo_stack:
        raise PDFError("nothing_to_redo", "No edits to redo.")

    working_path = _working_path(doc_id)
    _snapshot_before_edit_no_clear(doc_id)

    next_snapshot = state.redo_stack.pop()
    shutil.copyfile(next_snapshot, working_path)
    os.remove(next_snapshot)

    return {"redone": True, "remaining_redo_steps": len(state.redo_stack)}


def _snapshot_before_edit_no_clear(doc_id: str):
    state = _get_state(doc_id)
    snap_path = os.path.join(_history_dir(doc_id), f"{len(state.history_stack)}_{uuid.uuid4().hex}.pdf")
    shutil.copyfile(_working_path(doc_id), snap_path)
    state.history_stack.append(snap_path)


def get_working_pdf_path(doc_id: str) -> str:
    _get_state(doc_id)
    return _working_path(doc_id)


def reset_to_original(doc_id: str) -> dict:
    state = _get_state(doc_id)
    shutil.copyfile(_original_path(doc_id), _working_path(doc_id))
    state.history_stack.clear()
    state.redo_stack.clear()
    return {"reset": True}
