const API_BASE = "/api";

export async function checkHealth(): Promise<boolean> {
  try {
    const res = await fetch(`${API_BASE}/health`);
    return res.ok;
  } catch {
    return false;
  }
}

export interface StreamHandlers {
  onSession?: (sessionId: string) => void;
  onStatus?: (text: string) => void;
  onDone: (text: string) => void;
  onError: (message: string) => void;
}

// Consumes POST /chat/stream (Server-Sent Events): live "status" lines as
// each tool runs, then one "done" (or "error") event with the final text.
// Parses the SSE wire format by hand (fetch + ReadableStream) rather than
// EventSource, since EventSource can't send a POST body / JSON payload.
export async function sendMessageStreaming(
  message: string,
  sessionId: string | null,
  handlers: StreamHandlers
): Promise<void> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}/chat/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, session_id: sessionId }),
    });
  } catch {
    handlers.onError("Couldn't reach the analysis service — the backend may not be running.");
    return;
  }

  if (!res.ok || !res.body) {
    handlers.onError(`Chat request failed: ${res.status} ${res.statusText}`);
    return;
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    // SSE frames are separated by a blank line; each frame is
    // "event: <type>\ndata: <json>".
    let sepIndex: number;
    while ((sepIndex = buffer.indexOf("\n\n")) !== -1) {
      const frame = buffer.slice(0, sepIndex);
      buffer = buffer.slice(sepIndex + 2);

      const eventLine = frame.split("\n").find((l) => l.startsWith("event: "));
      const dataLine = frame.split("\n").find((l) => l.startsWith("data: "));
      if (!eventLine || !dataLine) continue;

      const eventType = eventLine.slice("event: ".length).trim();
      let payload: { text?: string; session_id?: string };
      try {
        payload = JSON.parse(dataLine.slice("data: ".length));
      } catch {
        continue;
      }

      if (eventType === "session" && payload.session_id) {
        handlers.onSession?.(payload.session_id);
      } else if (eventType === "status" && payload.text) {
        handlers.onStatus?.(payload.text);
      } else if (eventType === "done") {
        handlers.onDone(payload.text ?? "");
      } else if (eventType === "error") {
        handlers.onError(payload.text ?? "Something went wrong.");
      }
    }
  }
}
