import { useState } from "react";

export default function Toolbar({
  hasDoc,
  onUpload,
  onSearch,
  onDeleteAll,
  onReplaceAll,
  onUndo,
  onRedo,
  onDownload,
  onNextOccurrence,
  onPrevOccurrence,
  searchResult,
  currentIndex,
  canUndo,
  canRedo,
  busy,
  selectedImage,       // ✅ NEW
  onDeleteImage,       // ✅ NEW
  onCancelImageSelect, // ✅ NEW
  regionSelectMode,      // ✅ NEW
  onToggleRegionSelect,  // ✅ NEW
  pendingRegion,         // ✅ NEW
  onDeleteRegion,        // ✅ NEW
  onCancelRegion,        // ✅ NEW
}) {
  const [keyword, setKeyword] = useState("");
  const [replaceWith, setReplaceWith] = useState("");
  const [caseSensitive, setCaseSensitive] = useState(false);
  const [showReplaceInput, setShowReplaceInput] = useState(false);
  const [confirm, setConfirm] = useState(null); // "delete" | null
  const [confirmImageDelete, setConfirmImageDelete] = useState(false); // ✅ NEW
  const [confirmRegionDelete, setConfirmRegionDelete] = useState(false); // ✅ NEW

  return (
    <div className="toolbar">
      <div className="toolbar-row">
        <label className="upload-btn">
          Upload PDF
          <input
            type="file"
            accept="application/pdf"
            hidden
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) onUpload(file);
              e.target.value = "";
            }}
          />
        </label>

        <button disabled={!hasDoc || !canUndo || busy} onClick={onUndo}>
          Undo
        </button>
        <button disabled={!hasDoc || !canRedo || busy} onClick={onRedo}>
          Redo
        </button>
        <button disabled={!hasDoc || busy} onClick={onDownload} className="download-btn">
          ⬇ Generate / Download PDF
        </button>
        <button
          type="button"
          disabled={!hasDoc || busy}
          onClick={onToggleRegionSelect}
          className={regionSelectMode ? "region-toggle-btn region-toggle-active" : "region-toggle-btn"}
        >
          {regionSelectMode ? "✓ Selecting Area (click to stop)" : "▭ Select Any Area to Delete"}
        </button>
      </div>

      {hasDoc && (
        <div className="toolbar-row">
          <input
            type="text"
            placeholder="Search keyword..."
            value={keyword}
            onChange={(e) => setKeyword(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && keyword.trim() && onSearch(keyword, caseSensitive)}
          />
          <label className="checkbox-label">
            <input
              type="checkbox"
              checked={caseSensitive}
              onChange={(e) => setCaseSensitive(e.target.checked)}
            />
            Case sensitive
          </label>
          <button disabled={!keyword.trim() || busy} onClick={() => onSearch(keyword, caseSensitive)}>
            Search
          </button>

          {searchResult && (
            <span className="occurrence-count">
              {searchResult.count} occurrence{searchResult.count !== 1 ? "s" : ""} on{" "}
              {searchResult.pages.length} page{searchResult.pages.length !== 1 ? "s" : ""}
            </span>
          )}

          {searchResult && searchResult.count > 0 && (
            <>
              <button onClick={onPrevOccurrence}>◀ Prev</button>
              <span className="occurrence-position">
                {currentIndex + 1} / {searchResult.count}
              </span>
              <button onClick={onNextOccurrence}>Next ▶</button>
            </>
          )}
        </div>
      )}

      {hasDoc && searchResult && searchResult.count > 0 && (
        <div className="toolbar-row">
          {confirm === "delete" ? (
            // ✅ FIX: confirm block moved to its own full-width row, button made
            // bigger/bolder so it can't be mistaken for an empty input or get
            // visually lost - this is the actual "yes, delete" action.
            <div className="confirm-inline">
              <span>
                Delete all {searchResult.count} occurrence(s) of "{keyword}"?
              </span>
              <button
                type="button"
                className="danger-btn"
                onClick={() => {
                  setConfirm(null);
                  onDeleteAll(keyword, caseSensitive);
                }}
              >
                ✕ Yes, Delete All
              </button>
              <button type="button" onClick={() => setConfirm(null)}>
                Cancel
              </button>
            </div>
          ) : (
            <button type="button" disabled={busy} onClick={() => setConfirm("delete")}>
              Delete All
            </button>
          )}

          {confirm !== "delete" && !showReplaceInput ? (
            <button type="button" disabled={busy} onClick={() => setShowReplaceInput(true)}>
              Replace All...
            </button>
          ) : confirm !== "delete" && showReplaceInput ? (
            <span className="replace-inline">
              Replace with:
              <input
                type="text"
                value={replaceWith}
                onChange={(e) => setReplaceWith(e.target.value)}
                placeholder="Replacement text"
              />
              <button
                type="button"
                disabled={busy}
                onClick={() => {
                  onReplaceAll(keyword, replaceWith, caseSensitive);
                  setShowReplaceInput(false);
                  setReplaceWith("");
                }}
              >
                Replace All
              </button>
              <button type="button" onClick={() => setShowReplaceInput(false)}>
                Cancel
              </button>
            </span>
          ) : null}
        </div>
      )}

      {/* ✅ NEW: image select/delete row - shows only when an image on the
          page has been clicked (App tracks selectedImage) */}
      {hasDoc && selectedImage && (
        <div className="toolbar-row">
          {!confirmImageDelete ? (
            <span className="confirm-inline" style={{ background: "#e8f0ff" }}>
              Image selected (page {selectedImage.page})
              <button type="button" className="danger-btn" onClick={() => setConfirmImageDelete(true)}>
                🗑 Delete Image
              </button>
              <button
                type="button"
                onClick={() => {
                  setConfirmImageDelete(false);
                  onCancelImageSelect?.();
                }}
              >
                Cancel selection
              </button>
            </span>
          ) : (
            <div className="confirm-inline">
              <span>Are you sure you want to delete this image?</span>
              <button
                type="button"
                className="danger-btn"
                onClick={() => {
                  setConfirmImageDelete(false);
                  onDeleteImage(selectedImage);
                }}
              >
                ✕ Yes, Delete Image
              </button>
              <button type="button" onClick={() => setConfirmImageDelete(false)}>
                Cancel
              </button>
            </div>
          )}
        </div>
      )}

      {/* ✅ NEW: freeform region select/delete row  */}
      {hasDoc && regionSelectMode && !pendingRegion && (
        <div className="toolbar-row">
          <span className="confirm-inline" style={{ background: "#e8f0ff" }}>
            Drag a box anywhere on the page to select what to delete (text and/or images).
          </span>
        </div>
      )}

      {hasDoc && pendingRegion && (
        <div className="toolbar-row">
          {!confirmRegionDelete ? (
            <span className="confirm-inline" style={{ background: "#e8f0ff" }}>
              Area selected on page {pendingRegion.page}
              <button type="button" className="danger-btn" onClick={() => setConfirmRegionDelete(true)}>
                🗑 Delete Selected Area
              </button>
              <button
                type="button"
                onClick={() => {
                  setConfirmRegionDelete(false);
                  onCancelRegion?.();
                }}
              >
                Cancel selection
              </button>
            </span>
          ) : (
            <div className="confirm-inline">
              <span>Delete everything inside the selected area? This can't target text/images outside the box.</span>
              <button
                type="button"
                className="danger-btn"
                onClick={() => {
                  setConfirmRegionDelete(false);
                  onDeleteRegion?.(pendingRegion);
                }}
              >
                ✕ Yes, Delete Area
              </button>
              <button type="button" onClick={() => setConfirmRegionDelete(false)}>
                Cancel
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}