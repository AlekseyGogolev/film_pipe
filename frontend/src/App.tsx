import type { ChangeEvent } from "react";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  AlertCircle,
  CheckCircle2,
  Clock3,
  Download,
  FileImage,
  FilePlus2,
  Images,
  Play,
  RefreshCw,
  XCircle,
} from "lucide-react";
import { apiUrl, createJob, getJob } from "./api";
import type {
  Artifact,
  ArtifactType,
  InputPolarity,
  ImageResult,
  Job,
  ProcessingMode,
  ProcessingStatus,
  RestorationMode,
} from "./types";

const MODES: Array<{
  id: ProcessingMode;
  label: string;
  shortLabel: string;
  enabled: boolean;
}> = [
  { id: "bw", label: "B&W", shortLabel: "BW", enabled: true },
  { id: "colorize", label: "Colorize", shortLabel: "CO", enabled: false },
  { id: "creative", label: "Creative", shortLabel: "CR", enabled: false },
];

const INPUT_POLARITIES: Array<{
  id: InputPolarity;
  label: string;
  shortLabel: string;
  title: string;
}> = [
  {
    id: "negative",
    label: "Negative",
    shortLabel: "NEG",
    title: "Film negative input; convert to positive",
  },
  {
    id: "positive",
    label: "Positive",
    shortLabel: "POS",
    title: "Already-positive input; do not invert",
  },
];

const RESTORATION_MODES: Array<{
  id: RestorationMode;
  label: string;
  shortLabel: string;
  title: string;
}> = [
  {
    id: "off",
    label: "Off",
    shortLabel: "OFF",
    title: "Без удаления дефектов",
  },
  {
    id: "telea",
    label: "TELEA",
    shortLabel: "TE",
    title: "Быстрый OpenCV restoration",
  },
  {
    id: "lama",
    label: "LaMa",
    shortLabel: "AI",
    title: "AI restoration",
  },
];

const STATUS_LABELS: Record<ProcessingStatus, string> = {
  pending: "Ожидает",
  running: "Обработка",
  success: "Готово",
  partial_success: "Частично",
  failed: "Ошибка",
};

const STATUS_ICONS: Record<ProcessingStatus, typeof Clock3> = {
  pending: Clock3,
  running: RefreshCw,
  success: CheckCircle2,
  partial_success: AlertCircle,
  failed: XCircle,
};

export default function App() {
  const inputRef = useRef<HTMLInputElement>(null);
  const [files, setFiles] = useState<File[]>([]);
  const [mode, setMode] = useState<ProcessingMode>("bw");
  const [polarity, setPolarity] = useState<InputPolarity>("negative");
  const [restoration, setRestoration] = useState<RestorationMode>("off");
  const [prompt, setPrompt] = useState("");
  const [job, setJob] = useState<Job | null>(null);
  const [selectedImageId, setSelectedImageId] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const selectedImage = useMemo(() => {
    if (!job) {
      return null;
    }
    return (
      job.images.find((image) => image.id === selectedImageId) ??
      job.images[0] ??
      null
    );
  }, [job, selectedImageId]);

  const counts = useMemo(() => {
    const initial: Record<ProcessingStatus, number> = {
      pending: 0,
      running: 0,
      success: 0,
      partial_success: 0,
      failed: 0,
    };
    for (const image of job?.images ?? []) {
      initial[image.status] += 1;
    }
    return initial;
  }, [job]);

  useEffect(() => {
    if (!job?.images.length) {
      setSelectedImageId(null);
      return;
    }
    if (!selectedImageId || !job.images.some((image) => image.id === selectedImageId)) {
      setSelectedImageId(job.images[0].id);
    }
  }, [job, selectedImageId]);

  useEffect(() => {
    if (!job || (job.status !== "pending" && job.status !== "running")) {
      return;
    }

    const intervalId = window.setInterval(async () => {
      try {
        setJob(await getJob(job.id));
      } catch (pollError) {
        setError(errorMessage(pollError));
      }
    }, 1500);

    return () => window.clearInterval(intervalId);
  }, [job]);

  const canSubmit = files.length > 0 && mode === "bw" && !submitting;

  function handleFiles(event: ChangeEvent<HTMLInputElement>) {
    setFiles(Array.from(event.target.files ?? []));
    setError(null);
  }

  async function submitJob() {
    if (!canSubmit) {
      return;
    }

    setSubmitting(true);
    setError(null);
    setJob(null);
    try {
      const nextJob = await createJob(files, mode, polarity, restoration, prompt);
      setJob(nextJob);
      setSelectedImageId(nextJob.images[0]?.id ?? null);
    } catch (submitError) {
      setError(errorMessage(submitError));
    } finally {
      setSubmitting(false);
    }
  }

  function resetSelection() {
    setFiles([]);
    setError(null);
    if (inputRef.current) {
      inputRef.current.value = "";
    }
  }

  return (
    <main className="appShell">
      <header className="topBar">
        <div>
          <p className="eyebrow">FilmPipe MVP</p>
          <h1>Processing Console</h1>
        </div>
        <div className="healthPill">
          <span className="pulse" />
          Local API
        </div>
      </header>

      <section className="controlBand" aria-label="Job setup">
        <div className="fileControl">
          <input
            ref={inputRef}
            className="fileInput"
            type="file"
            multiple
            aria-hidden="true"
            tabIndex={-1}
            accept=".tif,.tiff,.png,.jpg,.jpeg,image/tiff,image/png,image/jpeg"
            onChange={handleFiles}
          />
          <button
            className="fileButton"
            type="button"
            onClick={() => inputRef.current?.click()}
            disabled={submitting}
            title="Выбрать сканы"
          >
            <FilePlus2 size={18} />
            Выбрать файлы
          </button>
          <button
            className="iconButton"
            type="button"
            onClick={resetSelection}
            disabled={files.length === 0 || submitting}
            title="Очистить выбор"
          >
            <RefreshCw size={17} />
          </button>
        </div>

        <div className="modeGroup" role="radiogroup" aria-label="Processing mode">
          {MODES.map((item) => (
            <button
              key={item.id}
              className={`modeButton ${mode === item.id ? "active" : ""}`}
              type="button"
              disabled={!item.enabled || submitting}
              onClick={() => setMode(item.id)}
              title={item.enabled ? item.label : `${item.label}: не реализовано`}
              aria-pressed={mode === item.id}
            >
              <span>{item.shortLabel}</span>
              {item.label}
            </button>
          ))}
        </div>

        <label className="optionField">
          <span>Input</span>
          <div
            className="modeGroup polarityGroup"
            role="radiogroup"
            aria-label="Input polarity"
          >
            {INPUT_POLARITIES.map((item) => (
              <button
                key={item.id}
                className={`modeButton polarityButton ${
                  polarity === item.id ? "active" : ""
                }`}
                type="button"
                disabled={mode !== "bw" || submitting}
                onClick={() => setPolarity(item.id)}
                title={item.title}
                aria-pressed={polarity === item.id}
              >
                <span>{item.shortLabel}</span>
                {item.label}
              </button>
            ))}
          </div>
        </label>

        <label className="optionField">
          <span>Restoration</span>
          <div
            className="modeGroup restorationGroup"
            role="radiogroup"
            aria-label="Restoration"
          >
            {RESTORATION_MODES.map((item) => (
              <button
                key={item.id}
                className={`modeButton restorationButton ${
                  restoration === item.id ? "active" : ""
                }`}
                type="button"
                disabled={mode !== "bw" || submitting}
                onClick={() => setRestoration(item.id)}
                title={item.title}
                aria-pressed={restoration === item.id}
              >
                <span>{item.shortLabel}</span>
                {item.label}
              </button>
            ))}
          </div>
        </label>

        <label className="promptField">
          <span>Creative prompt</span>
          <input
            value={prompt}
            disabled={mode !== "creative" || submitting}
            onChange={(event) => setPrompt(event.target.value)}
            placeholder="Для будущего creative mode"
          />
        </label>

        <button
          className="primaryButton"
          type="button"
          disabled={!canSubmit}
          onClick={submitJob}
          title="Запустить обработку"
        >
          <Play size={18} />
          {submitting ? "Обработка..." : "Process"}
        </button>
      </section>

      {error ? (
        <div className="errorBanner" role="alert">
          <AlertCircle size={18} />
          {error}
        </div>
      ) : null}

      <section className="workspace">
        <aside className="queuePane" aria-label="Selected and processed images">
          <div className="paneHeader">
            <div>
              <p className="eyebrow">Input</p>
              <h2>Файлы</h2>
            </div>
            <span className="countBadge">{files.length}</span>
          </div>

          <FileList files={files} />

          {job ? (
            <div className="jobBlock">
              <div className="jobHeader">
                <div>
                  <p className="eyebrow">Job</p>
                  <h2>{shortId(job.id)}</h2>
                </div>
                <StatusBadge status={job.status} />
              </div>

              <div className="statusGrid">
                <Metric label="Готово" value={counts.success} />
                <Metric label="Частично" value={counts.partial_success} />
                <Metric label="Ошибки" value={counts.failed} />
              </div>

              {job.errors.length ? <ErrorList errors={job.errors} /> : null}

              <div className="imageList">
                {job.images.map((image) => (
                  <button
                    key={image.id}
                    className={`imageRow ${
                      selectedImage?.id === image.id ? "selected" : ""
                    }`}
                    type="button"
                    onClick={() => setSelectedImageId(image.id)}
                  >
                    <FileImage size={18} />
                    <span className="imageName">{image.filename}</span>
                    <StatusBadge status={image.status} compact />
                  </button>
                ))}
              </div>

              <a
                className="downloadButton"
                href={apiUrl(job.download_url)}
                title="Скачать batch ZIP"
              >
                <Download size={17} />
                Batch ZIP
              </a>
            </div>
          ) : null}
        </aside>

        <section className="resultPane" aria-label="Processing results">
          {selectedImage ? (
            <ImageDetails image={selectedImage} />
          ) : (
            <div className="emptyState">
              <Images size={34} />
              <h2>Готов к обработке</h2>
              <p>Выбранные файлы появятся слева, результаты — здесь.</p>
            </div>
          )}
        </section>
      </section>
    </main>
  );
}

function FileList({ files }: { files: File[] }) {
  if (!files.length) {
    return <p className="mutedText">Файлы не выбраны.</p>;
  }

  return (
    <div className="fileList">
      {files.map((file) => (
        <div className="fileRow" key={`${file.name}-${file.size}-${file.lastModified}`}>
          <FileImage size={17} />
          <span>{file.name}</span>
          <small>{formatBytes(file.size)}</small>
        </div>
      ))}
    </div>
  );
}

function ImageDetails({ image }: { image: ImageResult }) {
  const original = artifactOf(image, "original");
  const positive = artifactOf(image, "positive");
  const restored = artifactOf(image, "restored");

  return (
    <>
      <div className="resultHeader">
        <div>
          <p className="eyebrow">Result</p>
          <h2>{image.filename}</h2>
        </div>
        <StatusBadge status={image.status} />
      </div>

      {image.errors.length ? <ErrorList errors={image.errors} /> : null}

      <div className="comparisonGrid">
        <PreviewCard label="Original" artifact={original} />
        <PreviewCard label="Positive" artifact={positive} />
        {restored ? <PreviewCard label="Restored" artifact={restored} /> : null}
      </div>

      <div className="artifactStrip">
        {image.artifacts.map((artifact) => (
          <a
            key={artifact.type}
            className="artifactLink"
            href={apiUrl(artifact.download_url)}
            title={`Скачать ${artifact.filename}`}
          >
            <Download size={16} />
            <span>{artifact.type}</span>
          </a>
        ))}
      </div>
    </>
  );
}

function PreviewCard({
  label,
  artifact,
}: {
  label: string;
  artifact: Artifact | undefined;
}) {
  const [previewFailed, setPreviewFailed] = useState(false);

  useEffect(() => {
    setPreviewFailed(false);
  }, [artifact?.preview_url]);

  return (
    <article className="previewCard">
      <div className="previewHeader">
        <h3>{label}</h3>
        {artifact ? (
          <a
            className="iconButton"
            href={apiUrl(artifact.download_url)}
            title={`Скачать ${artifact.filename}`}
            aria-label={`Скачать ${artifact.filename}`}
          >
            <Download size={16} />
          </a>
        ) : null}
      </div>
      <div className="previewFrame">
        {artifact && !previewFailed ? (
          <img
            src={apiUrl(artifact.preview_url)}
            alt={`${label}: ${artifact.filename}`}
            onError={() => setPreviewFailed(true)}
          />
        ) : artifact ? (
          <div className="previewFallback">
            <FileImage size={30} />
            <strong>Предпросмотр недоступен</strong>
            <span>{artifact.mime_type}</span>
          </div>
        ) : (
          <div className="previewFallback">
            <FileImage size={30} />
            <strong>Артефакт не создан</strong>
            <span>{label}</span>
          </div>
        )}
      </div>
      {artifact ? <p className="artifactName">{artifact.filename}</p> : null}
    </article>
  );
}

function ErrorList({
  errors,
}: {
  errors: Array<{
    stage: string;
    message: string;
    recoverable: boolean;
    exception_type: string | null;
  }>;
}) {
  return (
    <div className="errorList">
      {errors.map((item, index) => (
        <div className="errorItem" key={`${item.stage}-${index}`}>
          <AlertCircle size={17} />
          <div>
            <strong>{item.stage}</strong>
            <span>{item.message}</span>
          </div>
        </div>
      ))}
    </div>
  );
}

function StatusBadge({
  status,
  compact = false,
}: {
  status: ProcessingStatus;
  compact?: boolean;
}) {
  const Icon = STATUS_ICONS[status];
  return (
    <span className={`statusBadge ${status} ${compact ? "compact" : ""}`}>
      <Icon size={compact ? 14 : 16} />
      {STATUS_LABELS[status]}
    </span>
  );
}

function Metric({ label, value }: { label: string; value: number }) {
  return (
    <div className="metric">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function artifactOf(image: ImageResult, type: ArtifactType): Artifact | undefined {
  return image.artifacts.find((artifact) => artifact.type === type);
}

function formatBytes(value: number): string {
  if (value < 1024) {
    return `${value} B`;
  }
  const units = ["KB", "MB", "GB"];
  let size = value / 1024;
  let index = 0;
  while (size >= 1024 && index < units.length - 1) {
    size /= 1024;
    index += 1;
  }
  return `${size.toFixed(size >= 10 ? 0 : 1)} ${units[index]}`;
}

function shortId(id: string): string {
  return id.length <= 10 ? id : `${id.slice(0, 10)}...`;
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "Неизвестная ошибка.";
}
