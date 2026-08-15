export type ProcessingMode = "bw" | "colorize" | "creative";

export type ProcessingStatus =
  | "pending"
  | "running"
  | "success"
  | "partial_success"
  | "failed";

export type ArtifactType =
  | "original"
  | "positive"
  | "restored"
  | "colorized"
  | "creative";

export interface ProcessingError {
  stage: string;
  message: string;
  recoverable: boolean;
  exception_type: string | null;
}

export interface Artifact {
  type: ArtifactType;
  filename: string;
  mime_type: string;
  preview_url: string;
  download_url: string;
}

export interface ImageResult {
  id: string;
  filename: string;
  status: ProcessingStatus;
  artifacts: Artifact[];
  errors: ProcessingError[];
}

export interface Job {
  id: string;
  status: ProcessingStatus;
  mode: ProcessingMode;
  selected_modes: ProcessingMode[];
  created_at: string;
  updated_at: string;
  images: ImageResult[];
  errors: ProcessingError[];
  download_url: string;
}
