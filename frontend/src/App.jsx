import { useEffect, useMemo, useRef, useState } from "react";
import Toolbar from "./components/Toolbar";
import PdfViewer from "./components/PdfViewer";
import ViewerErrorBoundary from "./components/ViewerErrorBoundary";
import * as api from "./api";
import "./App.css";

export default function App() {
  const [docId, setDocId] = useState(null);
  const [pageCount, setPageCount] = useState(0);
  const [refreshKey, setRefreshKey] = useState(0); // bump to force-reload the viewer after an edit
  const [searchResult, setSearchResult] = useState(null);
  const [currentIndex, setCurrentIndex] = useState(0);
  const [canUndo, setCanUndo] = useState(false);
  const [canRedo, setCanRedo] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [toast, setToast] = useState(null);
  const [images, setImages] = useState([]);           // ✅ NEW
  const [selectedImage, setSelectedImage] = useState(null); // ✅ NEW
  const [regionSelectMode, setRegionSelectMode] = useState(false); // ✅ NEW
  const [pendingRegion, setPendingRegion] = useState(null);        // ✅ NEW - {page, bbox}

  const pageRefs = useRef({});

  // ✅ FIX: only recompute the PDF url when the doc actually changes (new
  // upload or an edit bumps refreshKey) - not on every render. Previously
  // this was called inline in JSX as api.pdfUrl(docId), which appends
  // Date.now() and so produced a brand-new url on EVERY App re-render
  // (including the renders triggered by a search), causing PdfViewer's
  // load effect to re-fetch and re-render the whole PDF on every search.
  const pdfUrl = useMemo(() => (docId ? api.pdfUrl(docId) : null), [docId, refreshKey]);

  // ✅ NEW: (re)fetch the selectable image list whenever the doc changes -
  // new upload, or any edit (text or image) that bumps refreshKey. Also
  // clears any stale selection since xrefs can shift after an edit.
  useEffect(() => {
    if (!docId) {
      setImages([]);
      return;
    }
    setSelectedImage(null);
    api
      .listImages(docId)
      .then((res) => setImages(res.images))
      .catch(() => setImages([])); // non-fatal - image select just won't be available
  }, [docId, refreshKey]);


  function showError(err) {
    setError(err?.message || "Something went wrong.");
    setTimeout(() => setError(null), 5000);
  }

  function showToast(msg) {
    setToast(msg);
    setTimeout(() => setToast(null), 3500);
  }

  async function handleUpload(file) {
    setBusy(true);
    try {
      const res = await api.uploadPdf(file);
      setDocId(res.doc_id);
      setPageCount(res.page_count);
      setSearchResult(null);
      setCanUndo(false);
      setCanRedo(false);
      pageRefs.current = {};
      setRefreshKey((k) => k + 1);
      if (res.is_scanned) {
        showToast("This looks like a scanned PDF — text search/edit may not find selectable text. OCR support is planned.");
      }
    } catch (err) {
      showError(err);
    } finally {
      setBusy(false);
    }
  }

  async function handleSearch(keyword, caseSensitive) {
    setBusy(true);
    try {
      const res = await api.searchKeyword(docId, keyword, caseSensitive);
      setSearchResult(res);
      setCurrentIndex(0);
      scrollToOccurrence(res.occurrences[0]);
    } catch (err) {
      setSearchResult(null);
      showError(err);
    } finally {
      setBusy(false);
    }
  }

  function scrollToOccurrence(occ) {
    if (!occ) return;
    const wrapper = pageRefs.current[occ.page];
    wrapper?.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  function handleNextOccurrence() {
    if (!searchResult) return;
    const next = (currentIndex + 1) % searchResult.occurrences.length;
    setCurrentIndex(next);
    scrollToOccurrence(searchResult.occurrences[next]);
  }

  function handlePrevOccurrence() {
    if (!searchResult) return;
    const prev = (currentIndex - 1 + searchResult.occurrences.length) % searchResult.occurrences.length;
    setCurrentIndex(prev);
    scrollToOccurrence(searchResult.occurrences[prev]);
  }

  async function handleDeleteAll(keyword, caseSensitive) {
    setBusy(true);
    try {
      const res = await api.deleteAll(docId, keyword, caseSensitive);
      showToast(`Deleted ${res.removed_count} occurrence(s) across ${res.pages_affected.length} page(s).`);
      setSearchResult(null);
      setCanUndo(true);
      setCanRedo(false);
      setRefreshKey((k) => k + 1);
    } catch (err) {
      showError(err);
    } finally {
      setBusy(false);
    }
  }

  async function handleReplaceAll(find, replace, caseSensitive) {
    setBusy(true);
    try {
      const res = await api.replaceAll(docId, find, replace, caseSensitive);
      showToast(`Replaced ${res.replaced_count} occurrence(s) across ${res.pages_affected.length} page(s).`);
      setSearchResult(null);
      setCanUndo(true);
      setCanRedo(false);
      setRefreshKey((k) => k + 1);
    } catch (err) {
      showError(err);
    } finally {
      setBusy(false);
    }
  }

  async function handleUndo() {
    setBusy(true);
    try {
      const res = await api.undoEdit(docId);
      setCanUndo(res.remaining_undo_steps > 0);
      setCanRedo(true);
      setSearchResult(null);
      setRefreshKey((k) => k + 1);
    } catch (err) {
      showError(err);
    } finally {
      setBusy(false);
    }
  }

  async function handleRedo() {
    setBusy(true);
    try {
      const res = await api.redoEdit(docId);
      setCanRedo(res.remaining_redo_steps > 0);
      setCanUndo(true);
      setSearchResult(null);
      setRefreshKey((k) => k + 1);
    } catch (err) {
      showError(err);
    } finally {
      setBusy(false);
    }
  }

  function handleImageClick(img) {
    setSelectedImage(img);
  }

  function handleCancelImageSelect() {
    setSelectedImage(null);
  }

  async function handleDeleteImage(img) {
    setBusy(true);
    try {
      await api.deleteImage(docId, img.page, img.xref);
      showToast(`Image removed from page ${img.page}.`);
      setSelectedImage(null);
      setCanUndo(true);
      setCanRedo(false);
      setRefreshKey((k) => k + 1); // reloads viewer + refetches image list
    } catch (err) {
      showError(err);
    } finally {
      setBusy(false);
    }
  }

  function handleToggleRegionSelect() {
    setRegionSelectMode((v) => !v);
    setPendingRegion(null);
  }

  function handleRegionDrawn(page, bbox) {
    setPendingRegion({ page, bbox });
  }

  function handleCancelRegion() {
    setPendingRegion(null);
  }

  async function handleDeleteRegion(region) {
    setBusy(true);
    try {
      await api.deleteRegion(docId, region.page, region.bbox);
      showToast(`Deleted selected area on page ${region.page}.`);
      setPendingRegion(null);
      setRegionSelectMode(false);
      setCanUndo(true);
      setCanRedo(false);
      setRefreshKey((k) => k + 1);
    } catch (err) {
      showError(err);
    } finally {
      setBusy(false);
    }
  }

  function handleDownload() {
    // ✅ FIX: window.open() can get silently blocked by popup blockers.
    // A programmatically-clicked <a download> link is not treated as a
    // popup, so it reliably triggers a file download instead.
    const link = document.createElement("a");
    link.href = api.downloadUrl(docId);
    link.download = `edited_${docId.slice(0, 8)}.pdf`;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  }

  return (
    <div className="app-root">
      <header className="app-header">
        <h1>PDF Text &amp; Image Editor</h1>
      </header>

      <Toolbar
        hasDoc={!!docId}
        onUpload={handleUpload}
        onSearch={handleSearch}
        onDeleteAll={handleDeleteAll}
        onReplaceAll={handleReplaceAll}
        onUndo={handleUndo}
        onRedo={handleRedo}
        onDownload={handleDownload}
        onNextOccurrence={handleNextOccurrence}
        onPrevOccurrence={handlePrevOccurrence}
        searchResult={searchResult}
        currentIndex={currentIndex}
        canUndo={canUndo}
        canRedo={canRedo}
        busy={busy}
        selectedImage={selectedImage}
        onDeleteImage={handleDeleteImage}
        onCancelImageSelect={handleCancelImageSelect}
        regionSelectMode={regionSelectMode}
        onToggleRegionSelect={handleToggleRegionSelect}
        pendingRegion={pendingRegion}
        onDeleteRegion={handleDeleteRegion}
        onCancelRegion={handleCancelRegion}
      />

      {error && <div className="banner banner-error">{error}</div>}
      {toast && <div className="banner banner-info">{toast}</div>}

      <main className="app-main">
        {!docId ? (
          <div className="empty-state">Upload a PDF to get started.</div>
        ) : (
          <ViewerErrorBoundary resetKey={refreshKey}>
            <PdfViewer
              key={refreshKey}
              pdfUrl={pdfUrl}
              occurrences={searchResult?.occurrences || []}
              currentOccurrence={searchResult?.occurrences?.[currentIndex]}
              pageRefs={pageRefs}
              onPagesRendered={setPageCount}
              images={images}
              selectedImage={selectedImage}
              onImageClick={handleImageClick}
              regionSelectMode={regionSelectMode}
              pendingRegion={pendingRegion}
              onRegionDrawn={handleRegionDrawn}
            />
          </ViewerErrorBoundary>
        )}
      </main>
    </div>
  );
}