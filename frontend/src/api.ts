import type {
  FinalProcessingMode,
  InputProcessingMode,
  Job,
  RestorationMode,
} from "./types";

const configuredBase = import.meta.env.VITE_FILMPIPE_API_BASE ?? "/api";
export const API_BASE = configuredBase.replace(/\/$/, "");

export function apiUrl(path: string): string {
  if (/^https?:\/\//.test(path)) {
    return path;
  }

  const normalizedPath = path.startsWith("/") ? path : `/${path}`;
  return `${API_BASE}${normalizedPath}`;
}

export async function createJob(
  files: File[],
  inputProcessing: InputProcessingMode,
  restoration: RestorationMode,
  finalProcessing: FinalProcessingMode,
  creativePrompt?: string,
): Promise<Job> {
  const body = new FormData();
  body.append("input_processing", inputProcessing);
  body.append("restoration", restoration);
  body.append("final_processing", finalProcessing);
  if (finalProcessing === "creative") {
    const normalizedPrompt = (creativePrompt ?? "").trim();
    if (normalizedPrompt) {
      body.append("creative_prompt", normalizedPrompt);
    }
  }
  for (const file of files) {
    body.append("files", file);
  }

  return request<Job>("/jobs", {
    method: "POST",
    body,
  });
}

export async function getJob(jobId: string): Promise<Job> {
  return request<Job>(`/jobs/${jobId}`);
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(apiUrl(path), init);
  if (!response.ok) {
    throw new Error(await responseMessage(response));
  }
  return response.json() as Promise<T>;
}

async function responseMessage(response: Response): Promise<string> {
  const fallback = `HTTP ${response.status}`;
  const contentType = response.headers.get("content-type") ?? "";
  if (!contentType.includes("application/json")) {
    const text = await response.text();
    return text ? `${fallback}: ${text}` : fallback;
  }

  const payload = (await response.json()) as { detail?: unknown };
  if (typeof payload.detail === "string") {
    return payload.detail;
  }
  return fallback;
}
