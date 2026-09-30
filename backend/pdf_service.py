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
- Replace re-inserts text after redaction using a fallback font matched on size/color,
  since the original PDF's embedded font is almost always subsetted (only contains the
  glyphs actually used) and may not contain glyphs for the replacement string.
- Image object handling is a separate module (added in phase 2).
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
        # fitz can sometimes still open encrypted docs with empty owner password;
        # if pages aren't readable, treat as password-protected.
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

    # validate it opens, gather metadata
    try:
        doc = _open_doc(orig_path)
    except PDFError:
        os.remove(orig_path)
        raise

    page_count = doc.page_count
    is_scanned = not any(_page_has_extractable_text(doc.load_page(i)) for i in range(page_count))
    doc.close()

    # working copy starts identical to the original
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

    _get_state(doc_id)  # validates doc exists
    doc = _open_doc(_working_path(doc_id))

    flags = 0 if case_sensitive else fitz.TEXT_DEHYPHENATE
    occurrences = []
    pages_with_hits = set()

    for page_index in range(doc.page_count):
        page = doc.load_page(page_index)
        # search_for is case-sensitive by default in PyMuPDF; do case-insensitive
        # ourselves by searching lowercased text when requested.
        if case_sensitive:
            rects = page.search_for(keyword)
        else:
            rects = page.search_for(keyword, flags=fitz.TEXT_PRESERVE_WHITESPACE) or []
            if not rects:
                rects = _case_insensitive_search(page, keyword)
            else:
                # search_for is already exact-case; for case-insensitive we still need
                # to also catch differently-cased occurrences, so union with manual search.
                manual = _case_insensitive_search(page, keyword)
                rects = _merge_rects(rects, manual)

        for r in rects:
            occurrences.append({
                "page": page_index + 1,  # 1-indexed for the UI
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
    """Fallback manual case-insensitive search using word-level text extraction."""
    kw_lower = keyword.lower()
    results = []
    words = page.get_text("words")  # (x0, y0, x1, y1, word, block_no, line_no, word_no)
    kw_tokens = kw_lower.split()

    if len(kw_tokens) == 1:
        for w in words:
            if w[4].lower() == kw_tokens[0]:
                results.append(fitz.Rect(w[0], w[1], w[2], w[3]))
        # also try substring match within longer words (keyword as part of a word)
        if kw_lower not in [w[4].lower() for w in words]:
            for w in words:
                if kw_lower in w[4].lower():
                    results.append(fitz.Rect(w[0], w[1], w[2], w[3]))
    else:
        # multi-word keyword: use page.search_for case-insensitively by trying
        # both the given case and a title/upper/lower variant, then de-dupe.
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
    """Push current working file onto the undo history stack before mutating it."""
    state = _get_state(doc_id)
    snap_path = os.path.join(_history_dir(doc_id), f"{len(state.history_stack)}_{uuid.uuid4().hex}.pdf")
    shutil.copyfile(_working_path(doc_id), snap_path)
    state.history_stack.append(snap_path)
    state.redo_stack.clear()  # any new edit invalidates redo history


def delete_all(doc_id: str, keyword: str, case_sensitive: bool = False) -> dict:
    result = search_keyword(doc_id, keyword, case_sensitive)  # raises if not found

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
            # images=0 keeps images on the page untouched; only text under the
            # redaction box is stripped from the content stream.
            page.apply_redactions(images=0)

    _save_and_replace(doc, working_path)

    return {
        "keyword": keyword,
        "removed_count": total_removed,
        "pages_affected": result["pages"],
    }


def replace_all(doc_id: str, find: str, replace: str, case_sensitive: bool = False) -> dict:
    if replace is None:
        replace = ""

    result = search_keyword(doc_id, find, case_sensitive)  # raises if not found

    _snapshot_before_edit(doc_id)

    working_path = _working_path(doc_id)
    doc = _open_doc(working_path)

    total_replaced = 0
    for page_index in range(doc.page_count):
        page = doc.load_page(page_index)
        occs = [o for o in result["occurrences"] if o["page"] == page_index + 1]
        if not occs:
            continue

        # Capture font size/color hints from the original text before redacting it.
        insert_specs = []
        for occ in occs:
            bbox = fitz.Rect(*occ["bbox"])
            size, color = _guess_text_style(page, bbox)
            insert_specs.append((bbox, size, color))
            page.add_redact_annot(bbox)
            total_replaced += 1

        page.apply_redactions(images=0)

        for bbox, size, color in insert_specs:
            if replace:
                # Insert replacement text left-aligned within the original bbox,
                # using a standard fallback font (helv) since the original font
                # is very likely subsetted and won't contain glyphs for new text.
                page.insert_text(
                    (bbox.x0, bbox.y1 - 1),  # baseline approx at bbox bottom
                    replace,
                    fontsize=size,
                    fontname="helv",
                    color=color,
                )

    _save_and_replace(doc, working_path)

    return {
        "find": find,
        "replace": replace,
        "replaced_count": total_replaced,
        "pages_affected": result["pages"],
    }


def _guess_text_style(page: fitz.Page, bbox: fitz.Rect) -> tuple:
    """Best-effort extraction of font size + RGB color for text under bbox, for
    visually-close (not identical) replacement text."""
    size = max(bbox.y1 - bbox.y0, 6.0) * 0.8  # fallback: derive from box height
    color = (0, 0, 0)
    try:
        d = page.get_text("dict", clip=bbox)
        for block in d.get("blocks", []):
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    size = span.get("size", size)
                    c = span.get("color", 0)
                    color = ((c >> 16 & 255) / 255, (c >> 8 & 255) / 255, (c & 255) / 255)
                    return size, color
    except Exception:
        pass
    return size, color


def list_images(doc_id: str) -> dict:
    """Per-page image placements: bbox + xref, for the frontend to draw
    clickable selection boxes over. xref is the stable id used to target a
    specific image for deletion (a reused image can appear at multiple
    placements/bboxes sharing the same xref - each placement gets its own
    entry here since the user clicks a specific spot on the page)."""
    _get_state(doc_id)
    doc = _open_doc(_working_path(doc_id))

    images = []
    for page_index in range(doc.page_count):
        page = doc.load_page(page_index)
        for info in page.get_image_info(xrefs=True):
            # skip the 1x1 transparent placeholders that delete_image() leaves
            # behind - they're not real content, just PyMuPDF's internal way
            # of "removing" an image without disturbing the page structure
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
    """Removes exactly one image object (by xref, on the given page) from
    the PDF. Uses PyMuPDF's Page.delete_image, which replaces the image's
    raster content with a 1x1 transparent placeholder rather than touching
    the page's content stream/resource structure - so surrounding text,
    other images, page size and layout are all left alone."""
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
        # undo the snapshot we just pushed since nothing is actually changing
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
    """Deletes whatever falls inside an arbitrary user-drawn rectangle on one
    page - text and/or image content alike, and only the part of each that
    actually overlaps the box. Text fully or partially inside the box is
    removed; images get only their overlapping pixels blanked out (not the
    whole image) via apply_redactions(images=2); vector graphics overlapping
    the box are removed too. Verified: a box covering half an image + part
    of a text line removed exactly that half/part and left the rest intact."""
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
        # images=2: blank out only the overlapping part of any image, not the
        # whole image. graphics=2: remove any vector graphics touching the
        # box. text=0: remove any text touching the box (default already).
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
    # push current state onto redo stack, restore previous snapshot
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
    """Like _snapshot_before_edit but doesn't clear the redo stack (used internally by redo())."""
    state = _get_state(doc_id)
    snap_path = os.path.join(_history_dir(doc_id), f"{len(state.history_stack)}_{uuid.uuid4().hex}.pdf")
    shutil.copyfile(_working_path(doc_id), snap_path)
    state.history_stack.append(snap_path)


def get_working_pdf_path(doc_id: str) -> str:
    _get_state(doc_id)
    return _working_path(doc_id)


def reset_to_original(doc_id: str) -> dict:
    """Discard all edits, restore the working copy back to the untouched original."""
    state = _get_state(doc_id)
    shutil.copyfile(_original_path(doc_id), _working_path(doc_id))
    state.history_stack.clear()
    state.redo_stack.clear()
    return {"reset": True}