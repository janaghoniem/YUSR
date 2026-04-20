const STT_ENDPOINT = "http://localhost:8000/transcribe";

function isConnectivityError(error) {
  // Only treat as connectivity error if we never reached the server
  // (not if the server responded with an error status)
  if (error?.status && error.status >= 400) return false;
  const message = String(error?.message || error || "").toLowerCase();
  return (
    message.includes("failed to fetch") ||
    message.includes("networkerror") ||
    message.includes("network request failed") ||
    message.includes("err_connection_refused") ||
    message.includes("load failed") ||
    message.includes("the operation was aborted") ||
    message.includes("aborted due to timeout")
  );
}

function parseErrorDetail(data, status) {
  if (!data) return `STT HTTP ${status}`;
  if (typeof data === "string") return data;
  if (typeof data?.detail === "string") return data.detail;
  return `STT HTTP ${status}`;
}

export async function requestTranscription(payload, { timeoutMs = 20000 } = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);

  try {
    console.info("[STT] Sending renderer request", {
      endpoint: STT_ENDPOINT,
      session_id: payload?.session_id,
      bytes: String(payload?.audio_data || "").length,
    });
    const response = await fetch(STT_ENDPOINT, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      signal: controller.signal,
    });

    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const error = new Error(parseErrorDetail(data, response.status));
      error.status = response.status;
      error.isHttpError = true;
      throw error;
    }

    console.info("[STT] Renderer request succeeded", { status: response.status });

    return data;
  } catch (error) {
    const canFallback =
      isConnectivityError(error) &&
      typeof window !== "undefined" &&
      typeof window?.electronAPI?.transcribeAudio === "function";

    if (!canFallback) {
      throw error;
    }

    console.warn("[STT] Renderer request failed, using IPC fallback", error?.message || error);

    const proxied = await window.electronAPI.transcribeAudio({
      ...payload,
      timeoutMs,
    });

    if (!proxied?.ok) {
      throw new Error(proxied?.error || parseErrorDetail(proxied?.data, proxied?.status || 0));
    }

    if (proxied.status >= 400) {
      throw new Error(parseErrorDetail(proxied?.data, proxied.status));
    }

    return proxied.data || {};
  } finally {
    clearTimeout(timer);
  }
}