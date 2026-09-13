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
