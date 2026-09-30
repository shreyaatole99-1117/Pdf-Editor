import { useEffect, useRef, useState } from "react";
import * as pdfjsLib from "pdfjs-dist";
import pdfjsWorker from "pdfjs-dist/build/pdf.worker.min.mjs?url";

pdfjsLib.GlobalWorkerOptions.workerSrc = pdfjsWorker;

const RENDER_SCALE = 1.4;

/**
 * Renders every page of the PDF at `pdfUrl` stacked vertically, one <canvas>
 * per page, with highlight boxes drawn over search occurrences.
 *
 * `occurrences` bboxes come straight from PyMuPDF, which - unlike the raw PDF
 * spec - already uses a top-left-origin, y-down coordinate system (same as a
 * screen/canvas). So placing a highlight is just: box * RENDER_SCALE, no
 * coordinate flip needed, as long as we render pdf.js at the same scale.
 */
export default function PdfViewer({
  pdfUrl,
  occurrences,
  currentOccurrence,
  pageRefs,
  onPagesRendered,
  images = [],
  selectedImage = null,
  onImageClick,
  regionSelectMode = false, // ✅ NEW
  pendingRegion = null,     // ✅ NEW - {page, bbox} already drawn, awaiting confirm
  onRegionDrawn,            // ✅ NEW - (page, bboxInPdfUnits) => void
}) {
  const containerRef = useRef(null);
  const [pageCanvases, setPageCanvases] = useState([]); // [{pageNum, width, height}]
  const [loadError, setLoadError] = useState(null);

  useEffect(() => {
    let cancelled = false;
    const docRef = { current: null }; // ✅ FIX: hold the loaded doc in a stable
    // ref object rather than reassigning a plain closure variable - avoids the
    // cleanup running against a stale/partial value under React Strict Mode's
    // double-invoke, which was causing "loadedDoc?.destroy is not a function".

    async function render() {
      setLoadError(null);
      try {
        const task = pdfjsLib.getDocument({ url: pdfUrl });
        const doc = await task.promise;
        if (cancelled) {
          // effect was cleaned up while this was in flight - dispose it
          // immediately instead of handing it to render state
          if (typeof doc?.destroy === "function") doc.destroy();
          return;
        }
        docRef.current = doc;

        const pages = [];
        for (let pageNum = 1; pageNum <= doc.numPages; pageNum++) {
          const page = await doc.getPage(pageNum);
          const viewport = page.getViewport({ scale: RENDER_SCALE });

          const canvas = document.createElement("canvas");
          canvas.width = viewport.width;
          canvas.height = viewport.height;
          canvas.className = "pdf-page-canvas";
          canvas.dataset.page = pageNum;

          const ctx = canvas.getContext("2d");
          await page.render({ canvasContext: ctx, viewport }).promise;

          pages.push({ pageNum, canvas, width: viewport.width, height: viewport.height });
        }
        if (!cancelled) {
          setPageCanvases(pages);
          onPagesRendered?.(doc.numPages);
        }
      } catch (err) {
        if (!cancelled) setLoadError(err.message || "Could not load this PDF.");
      }
    }

    render();
    return () => {
      cancelled = true;
      const doc = docRef.current;
      if (doc && typeof doc.destroy === "function") {
        doc.destroy();
      }
    };
  }, [pdfUrl]);

  // mount rendered canvases into the DOM
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    container.innerHTML = "";
    for (const p of pageCanvases) {
      const wrapper = document.createElement("div");
      wrapper.className = "pdf-page-wrapper";
      wrapper.style.width = `${p.width}px`;
      wrapper.style.height = `${p.height}px`;
      wrapper.appendChild(p.canvas);
      container.appendChild(wrapper);
      if (pageRefs) pageRefs.current[p.pageNum] = wrapper;
    }
  }, [pageCanvases]);

  if (loadError) {
    return <div className="pdf-error">Couldn't display the PDF: {loadError}</div>;
  }

  return (
    <div className="pdf-viewer-scroll">
      <div className="pdf-pages-container" ref={containerRef} />
      <div className="pdf-highlight-layer">
        {pageCanvases.map((p) =>
          occurrences
            .filter((o) => o.page === p.pageNum)
            .map((o, idx) => {
              const [x0, y0, x1, y1] = o.bbox;
              const isCurrent =
                currentOccurrence &&
                currentOccurrence.page === o.page &&
                currentOccurrence.bbox[0] === x0 &&
                currentOccurrence.bbox[1] === y0;
              return (
                <Highlight
                  key={`${p.pageNum}-${idx}`}
                  pageWrapper={pageRefs?.current?.[p.pageNum]}
                  x0={x0 * RENDER_SCALE}
                  y0={y0 * RENDER_SCALE}
                  x1={x1 * RENDER_SCALE}
                  y1={y1 * RENDER_SCALE}
                  isCurrent={isCurrent}
                />
              );
            })
        )}
      </div>

      {/* separate layer for images: pointer-events enabled (unlike the text
          highlight layer, which stays click-through) so hover/click work */}
      <div className="pdf-image-layer">
        {pageCanvases.map((p) =>
          images
            .filter((img) => img.page === p.pageNum)
            .map((img) => {
              const [x0, y0, x1, y1] = img.bbox;
              const isSelected =
                selectedImage && selectedImage.page === img.page && selectedImage.xref === img.xref;
              return (
                <ImageBox
                  key={`${p.pageNum}-${img.xref}`}
                  pageWrapper={pageRefs?.current?.[p.pageNum]}
                  x0={x0 * RENDER_SCALE}
                  y0={y0 * RENDER_SCALE}
                  x1={x1 * RENDER_SCALE}
                  y1={y1 * RENDER_SCALE}
                  isSelected={isSelected}
                  onClick={() => onImageClick?.(img)}
                />
              );
            })
        )}
      </div>

      {/* ✅ NEW: freeform drag-to-select layer - draws a rectangle anywhere
          on the page (text and/or images), independent of the pre-detected
          search/image objects above. Only interactive when regionSelectMode
          is on, so it doesn't block normal clicks/scrolling otherwise. */}
      <div className="pdf-region-layer">
        {pageCanvases.map((p) => (
          <RegionSelectOverlay
            key={`region-${p.pageNum}`}
            pageWrapper={pageRefs?.current?.[p.pageNum]}
            pageNum={p.pageNum}
            width={p.width}
            height={p.height}
            active={regionSelectMode}
            pending={pendingRegion?.page === p.pageNum ? pendingRegion : null}
            onDrawn={onRegionDrawn}
          />
        ))}
      </div>
    </div>
  );
}

function RegionSelectOverlay({ pageWrapper, pageNum, width, height, active, pending, onDrawn }) {
  const [drag, setDrag] = useState(null); // {startX, startY, curX, curY} in on-screen px

  if (!pageWrapper) return null;
  const top = pageWrapper.offsetTop;
  const left = pageWrapper.offsetLeft;

  function localPoint(e) {
    const rect = e.currentTarget.getBoundingClientRect();
    return { x: e.clientX - rect.left, y: e.clientY - rect.top };
  }

  function handleMouseDown(e) {
    if (!active) return;
    const pt = localPoint(e);
    setDrag({ startX: pt.x, startY: pt.y, curX: pt.x, curY: pt.y });
  }

  function handleMouseMove(e) {
    if (!drag) return;
    const pt = localPoint(e);
    setDrag((d) => (d ? { ...d, curX: pt.x, curY: pt.y } : d));
  }

  function finishDrag() {
    if (!drag) return;
    const x0 = Math.min(drag.startX, drag.curX);
    const y0 = Math.min(drag.startY, drag.curY);
    const x1 = Math.max(drag.startX, drag.curX);
    const y1 = Math.max(drag.startY, drag.curY);
    setDrag(null);
    if (x1 - x0 < 4 || y1 - y0 < 4) return; // ignore accidental clicks/tiny drags
    // convert on-screen px back to PDF units by dividing out the render scale
    onDrawn?.(pageNum, [x0 / RENDER_SCALE, y0 / RENDER_SCALE, x1 / RENDER_SCALE, y1 / RENDER_SCALE]);
  }

  const liveBox = drag && {
    left: Math.min(drag.startX, drag.curX),
    top: Math.min(drag.startY, drag.curY),
    width: Math.abs(drag.curX - drag.startX),
    height: Math.abs(drag.curY - drag.startY),
  };

  const pendingBox = pending && {
    left: pending.bbox[0] * RENDER_SCALE,
    top: pending.bbox[1] * RENDER_SCALE,
    width: (pending.bbox[2] - pending.bbox[0]) * RENDER_SCALE,
    height: (pending.bbox[3] - pending.bbox[1]) * RENDER_SCALE,
  };

  return (
    <div
      style={{
        position: "absolute",
        top,
        left,
        width,
        height,
        pointerEvents: active ? "auto" : "none",
        cursor: active ? "crosshair" : "default",
      }}
      onMouseDown={handleMouseDown}
      onMouseMove={handleMouseMove}
      onMouseUp={finishDrag}
      onMouseLeave={finishDrag}
    >
      {liveBox && <div className="region-drag-box" style={{ position: "absolute", ...liveBox }} />}
      {pendingBox && <div className="region-pending-box" style={{ position: "absolute", ...pendingBox }} />}
    </div>
  );
}

function ImageBox({ pageWrapper, x0, y0, x1, y1, isSelected, onClick }) {
  if (!pageWrapper) return null;
  const top = pageWrapper.offsetTop + y0;
  const left = pageWrapper.offsetLeft + x0;
  return (
    <div
      className={isSelected ? "image-box image-box-selected" : "image-box"}
      style={{
        position: "absolute",
        top,
        left,
        width: x1 - x0,
        height: y1 - y0,
        cursor: "pointer",
      }}
      onClick={onClick}
      title={isSelected ? "Selected - click Delete Image to remove" : "Click to select this image"}
    />
  );
}

function Highlight({ pageWrapper, x0, y0, x1, y1, isCurrent }) {
  if (!pageWrapper) return null;
  // pageWrapper sits in normal flow inside .pdf-pages-container; the highlight
  // layer is absolutely positioned to overlay that same container starting at
  // (0,0), so offsetTop/offsetLeft (relative to the nearest positioned
  // ancestor) line up directly with the highlight layer's coordinate space.
  const top = pageWrapper.offsetTop + y0;
  const left = pageWrapper.offsetLeft + x0;
  return (
    <div
      className={isCurrent ? "highlight-box highlight-current" : "highlight-box"}
      style={{
        position: "absolute",
        top,
        left,
        width: x1 - x0,
        height: y1 - y0,
      }}
    />
  );
}