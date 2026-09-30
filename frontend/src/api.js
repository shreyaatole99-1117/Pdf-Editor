import axios from "axios";

const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8000";

const client = axios.create({ baseURL: API_BASE });

function unwrapError(err) {
  const detail = err?.response?.data?.detail;
  if (detail?.message) return { code: detail.code, message: detail.message };
  return { code: "unknown", message: "Something went wrong talking to the server." };
}

export async function uploadPdf(file) {
  const form = new FormData();
  form.append("file", file);
  try {
    const { data } = await client.post("/api/upload", form, {
      headers: { "Content-Type": "multipart/form-data" },
    });
    return data;
  } catch (err) {
    throw unwrapError(err);
  }
}

export async function searchKeyword(docId, keyword, caseSensitive) {
  try {
    const { data } = await client.post("/api/search", {
      doc_id: docId,
      keyword,
      case_sensitive: caseSensitive,
    });
    return data;
  } catch (err) {
    throw unwrapError(err);
  }
}

export async function deleteAll(docId, keyword, caseSensitive) {
  try {
    const { data } = await client.post("/api/delete-all", {
      doc_id: docId,
      keyword,
      case_sensitive: caseSensitive,
    });
    return data;
  } catch (err) {
    throw unwrapError(err);
  }
}

export async function replaceAll(docId, find, replace, caseSensitive) {
  try {
    const { data } = await client.post("/api/replace-all", {
      doc_id: docId,
      find,
      replace,
      case_sensitive: caseSensitive,
    });
    return data;
  } catch (err) {
    throw unwrapError(err);
  }
}

export async function listImages(docId) {
  try {
    const { data } = await client.get(`/api/images/${docId}`);
    return data;
  } catch (err) {
    throw unwrapError(err);
  }
}

export async function deleteImage(docId, page, xref) {
  try {
    const { data } = await client.post("/api/delete-image", {
      doc_id: docId,
      page,
      xref,
    });
    return data;
  } catch (err) {
    throw unwrapError(err);
  }
}

export async function deleteRegion(docId, page, bbox) {
  try {
    const { data } = await client.post("/api/delete-region", {
      doc_id: docId,
      page,
      bbox,
    });
    return data;
  } catch (err) {
    throw unwrapError(err);
  }
}

export async function undoEdit(docId) {
  try {
    const { data } = await client.post("/api/undo", { doc_id: docId });
    return data;
  } catch (err) {
    throw unwrapError(err);
  }
}

export async function redoEdit(docId) {
  try {
    const { data } = await client.post("/api/redo", { doc_id: docId });
    return data;
  } catch (err) {
    throw unwrapError(err);
  }
}

export async function resetToOriginal(docId) {
  try {
    const { data } = await client.post("/api/reset", { doc_id: docId });
    return data;
  } catch (err) {
    throw unwrapError(err);
  }
}

export function pdfUrl(docId) {
  // cache-busting param so the viewer re-fetches after each edit
  return `${API_BASE}/api/pdf/${docId}?t=${Date.now()}`;
}

export function downloadUrl(docId) {
  return `${API_BASE}/api/download/${docId}`;
}