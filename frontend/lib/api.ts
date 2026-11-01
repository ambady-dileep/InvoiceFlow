import {
  getAccessToken,
  getRefreshToken,
  setAccessToken,
  signOut,
} from "@/lib/auth";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export type Paginated<T> = {
  count: number;
  next: string | null;
  previous: string | null;
  results: T[];
};

export type Invoice = {
  id: number;
  invoice_number: string;
  customer_name: string;
  amount: string;
  invoice_date: string;
  status: "pending" | "paid" | "overdue";
};

export type UploadStatus = {
  upload_id: number;
  status: "pending" | "processing" | "completed" | "failed";
  progress: number;
  total_rows: number;
  processed_rows: number;
  successful_rows: number;
  failed_rows: number;
  failure_reason: string;
  created_at: string;
};

export type Upload = UploadStatus & { original_filename: string };

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
  }
}

export function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : "Something went wrong.";
}

function flattenErrors(data: unknown): string[] {
  if (typeof data === "string") return [data];
  if (Array.isArray(data)) return data.flatMap(flattenErrors);
  if (data && typeof data === "object") {
    return Object.values(data).flatMap(flattenErrors);
  }
  return [];
}

async function readError(response: Response) {
  const fallback = `The request failed (${response.status}).`;
  try {
    return flattenErrors(await response.json()).join(" ") || fallback;
  } catch {
    return fallback;
  }
}

async function request(path: string, init: RequestInit) {
  try {
    return await fetch(`${API_URL}${path}`, init);
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      throw error;
    }
    throw new ApiError("Could not reach the server. Please try again.", 0);
  }
}

/** Calls an endpoint that needs no token, such as login. */
export async function publicPost<T>(path: string, body: object): Promise<T> {
  const response = await request(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    throw new ApiError(await readError(response), response.status);
  }
  return response.json();
}

function send(path: string, init: RequestInit) {
  const headers = new Headers(init.headers);
  headers.set("Authorization", `Bearer ${getAccessToken()}`);
  return request(path, { ...init, headers });
}

async function refreshAccessToken() {
  const refresh = getRefreshToken();
  if (!refresh) return false;
  try {
    const { access } = await publicPost<{ access: string }>(
      "/api/auth/token/refresh/",
      { refresh },
    );
    setAccessToken(access);
    return true;
  } catch (error) {
    if (error instanceof ApiError && error.status === 0) throw error;
    return false;
  }
}

// Requests that fail together share one refresh.
let refreshing: Promise<boolean> | null = null;

function sharedRefresh() {
  refreshing ??= refreshAccessToken().finally(() => {
    refreshing = null;
  });
  return refreshing;
}

/** Calls an authenticated endpoint, refreshing the token once on a 401. */
export async function apiFetch(path: string, init: RequestInit = {}) {
  let response = await send(path, init);
  if (response.status === 401 && (await sharedRefresh())) {
    response = await send(path, init);
  }
  if (response.status === 401) {
    signOut();
    throw new ApiError("Your session has expired.", 401);
  }
  if (!response.ok) {
    throw new ApiError(await readError(response), response.status);
  }
  return response;
}

export async function apiJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await apiFetch(path, init);
  return response.json();
}
