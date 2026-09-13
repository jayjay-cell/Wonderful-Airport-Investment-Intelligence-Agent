export interface ChatResponse {
  session_id: string;
  response: string;
}

export interface StructuredError {
  error_code: string;
  source: string | null;
  message: string;
  retryable: boolean;
  request_id: string;
  partial_result: Record<string, unknown> | null;
}

// Thrown instead of a generic Error so the UI can render the actual
// backend-classified failure (source name, retryability) rather than a
// single hardcoded "couldn't reach the analysis service" message for
// every possible failure (network error, 503, provider rate-limit, tool
// timeout, ...).
export class ChatApiError extends Error {
  structured: StructuredError | null;

  constructor(message: string, structured: StructuredError | null) {
    super(message);
    this.structured = structured;
  }
}

const API_BASE = "/api";

export async function sendMessage(
  message: string,
  sessionId: string | null
): Promise<ChatResponse> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, session_id: sessionId }),
    });
  } catch (networkErr) {
    // The fetch itself failed — the backend process is genuinely
    // unreachable (not running, wrong port, DNS/network issue). This is
    // the ONE case where "couldn't reach the analysis service" is
    // actually true — every other failure below has a specific cause.
    throw new ChatApiError(
      "Couldn't reach the analysis service — the backend may not be running.",
      null
    );
  }

  if (!res.ok) {
    let structured: StructuredError | null = null;
    try {
      const body = await res.json();
      structured = (body?.detail ?? null) as StructuredError | null;
    } catch {
      // response body wasn't JSON — fall through with structured=null
    }

    if (structured && structured.message) {
      throw new ChatApiError(structured.message, structured);
    }
    throw new ChatApiError(`Chat request failed: ${res.status} ${res.statusText}`, null);
  }

  return res.json();
}

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
