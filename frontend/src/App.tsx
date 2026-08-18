import type { ChangeEvent } from "react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AlertCircle,
  Archive,
  CheckCircle2,
  Clock3,
  Download,
  FileImage,
  FilePlus2,
  History as HistoryIcon,
  Images,
  Maximize2,
  Play,
  RefreshCw,
  X,
  XCircle,
} from "lucide-react";
import { apiUrl, createJob, getJob, listJobs } from "./api";
import {
  hasActiveJobs,
  isJobActive,
  mergeNewestJobs,
  newestJob,
  upsertNewestJob,
} from "./jobs";
import { usePolling } from "./polling";
import type {
  Artifact,
  ArtifactType,
  FinalProcessingMode,
  InputProcessingMode,
  ImageResult,
  Job,
  ProcessingStatus,
  RestorationMode,
} from "./types";

const INPUT_PROCESSING_OPTIONS: Array<{
  id: InputProcessingMode;
  label: string;
  shortLabel: string;
  title: string;
}> = [
  {
    id: "already_positive",
    label: "Already Positive",
    shortLabel: "POS",
    title: "Uploaded image is already a positive; do not invert or prepare it as B&W.",
  },
  {
    id: "bw_negative",
    label: "Negative -> Positive",
    shortLabel: "B&W",
    title: "Convert a black-and-white negative scan into a positive.",
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

const FINAL_PROCESSING_OPTIONS: Array<{
  id: FinalProcessingMode;
  label: string;
  shortLabel: string;
  title: string;
}> = [
  {
    id: "standard",
    label: "Standard",
    shortLabel: "STD",
    title: "Run only the technical processing pipeline.",
  },
  {
    id: "creative",
    label: "Creative",
    shortLabel: "CR",
    title: "Run Creative as the final stage and save a separate artifact.",
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

const INPUT_PROCESSING_LABELS: Record<InputProcessingMode, string> = {
  already_positive: "Already Positive",
  bw_negative: "Negative -> Positive",
};

const RESTORATION_LABELS: Record<RestorationMode, string> = {
  off: "Restoration Off",
  telea: "TELEA",
  lama: "LaMa",
};

const FINAL_PROCESSING_LABELS: Record<FinalProcessingMode, string> = {
  standard: "Standard",
  creative: "Creative",
};

const ARTIFACT_LABELS: Record<ArtifactType, string> = {
  original: "Original",
  positive: "Positive",
  restored: "Restored",
  creative: "Creative",
};

const ARTIFACT_ORDER: ArtifactType[] = [
  "original",
  "positive",
  "restored",
  "creative",
];

const THUMBNAIL_ARTIFACT_ORDER: ArtifactType[] = [
  "creative",
  "restored",
  "positive",
  "original",
];

const GENERATED_ARTIFACT_ORDER: ArtifactType[] = [
  "positive",
  "restored",
  "creative",
];

const DATE_TIME_FORMATTER = new Intl.DateTimeFormat(undefined, {
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  month: "short",
});

type ViewMode = "console" | "gallery";

interface ArtifactPreview {
  image: ImageResult;
  artifact: Artifact;
  label: string;
}

interface LightboxItem {
  artifact: Artifact;
  label: string;
  imageFilename: string;
}

type OpenPreview = (
  artifact: Artifact,
  label: string,
  imageFilename: string,
) => void;

export default function App() {
  const inputRef = useRef<HTMLInputElement>(null);
  const [view, setView] = useState<ViewMode>("console");
  const [files, setFiles] = useState<File[]>([]);
  const [inputProcessing, setInputProcessing] =
    useState<InputProcessingMode>("bw_negative");
  const [restoration, setRestoration] = useState<RestorationMode>("off");
  const [finalProcessing, setFinalProcessing] =
    useState<FinalProcessingMode>("standard");
  const [creativePrompt, setCreativePrompt] = useState("");
  const [job, setJob] = useState<Job | null>(null);
  const [history, setHistory] = useState<Job[]>([]);
  const [historyLoaded, setHistoryLoaded] = useState(false);
  const [selectedImageId, setSelectedImageId] = useState<string | null>(null);
  const [selectedHistoryJobId, setSelectedHistoryJobId] = useState<
    string | null
  >(null);
  const [selectedHistoryImageId, setSelectedHistoryImageId] = useState<
    string | null
  >(null);
  const [lightbox, setLightbox] = useState<LightboxItem | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pollError, setPollError] = useState<string | null>(null);

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

  const selectedHistoryJob = useMemo(() => {
    if (!history.length) {
      return null;
    }
    return (
      history.find((historyJob) => historyJob.id === selectedHistoryJobId) ??
      history[0]
    );
  }, [history, selectedHistoryJobId]);

  const selectedHistoryImage = useMemo(() => {
    if (!selectedHistoryJob) {
      return null;
    }
    return (
      selectedHistoryJob.images.find(
        (image) => image.id === selectedHistoryImageId,
      ) ??
      selectedHistoryJob.images[0] ??
      null
    );
  }, [selectedHistoryImageId, selectedHistoryJob]);

  const counts = useMemo(() => jobStatusCounts(job), [job]);

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
    if (!history.length) {
      setSelectedHistoryJobId(null);
      return;
    }
    if (
      !selectedHistoryJobId ||
      !history.some((historyJob) => historyJob.id === selectedHistoryJobId)
    ) {
      setSelectedHistoryJobId(history[0].id);
    }
  }, [history, selectedHistoryJobId]);

  useEffect(() => {
    if (!selectedHistoryJob?.images.length) {
      setSelectedHistoryImageId(null);
      return;
    }
    if (
      !selectedHistoryImageId ||
      !selectedHistoryJob.images.some(
        (image) => image.id === selectedHistoryImageId,
      )
    ) {
      setSelectedHistoryImageId(selectedHistoryJob.images[0].id);
    }
  }, [selectedHistoryImageId, selectedHistoryJob]);

  const activeJobId = job?.id ?? null;
  const shouldPollActiveJob = isJobActive(job);
  const shouldPollHistory = !historyLoaded || hasActiveJobs(history);
  const visibleError = error ?? pollError;

  const refreshActiveJob = useCallback(async () => {
    if (!activeJobId) {
      return;
    }

    const nextJob = await getJob(activeJobId);
    setPollError(null);
    setJob((current) => {
      if (!current || current.id !== nextJob.id) {
        return current;
      }
      return newestJob(current, nextJob);
    });
    setHistory((current) => upsertNewestJob(current, nextJob));
  }, [activeJobId]);

  const refreshHistory = useCallback(async () => {
    const response = await listJobs();
    setPollError(null);
    setHistoryLoaded(true);
    setHistory((current) => mergeNewestJobs(current, response.jobs));
    setJob((current) => {
      if (!current) {
        return current;
      }
      const listedJob = response.jobs.find((item) => item.id === current.id);
      return listedJob ? newestJob(current, listedJob) : current;
    });
  }, []);

  const refreshHistoryNow = useCallback(() => {
    void refreshHistory().catch((historyError) => {
      setPollError(errorMessage(historyError));
    });
  }, [refreshHistory]);

  const openPreview = useCallback<OpenPreview>(
    (artifact, label, imageFilename) => {
      setLightbox({ artifact, label, imageFilename });
    },
    [],
  );

  useEffect(() => {
    if (view === "gallery") {
      refreshHistoryNow();
    }
  }, [refreshHistoryNow, view]);

  usePolling({
    enabled: shouldPollActiveJob,
    intervalMs: 1500,
    poll: refreshActiveJob,
    onError: (pollError) => setPollError(errorMessage(pollError)),
  });

  usePolling({
    enabled: shouldPollHistory,
    intervalMs: 3500,
    poll: refreshHistory,
    onError: (pollError) => setPollError(errorMessage(pollError)),
  });

  const promptReady =
    finalProcessing === "standard" || creativePrompt.trim().length > 0;
  const canSubmit = files.length > 0 && promptReady && !submitting;

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
    setPollError(null);
    setJob(null);
    try {
      const nextJob = await createJob(
        files,
        inputProcessing,
        restoration,
        finalProcessing,
        creativePrompt,
      );
      setJob(nextJob);
      setHistory((current) => upsertNewestJob(current, nextJob));
      setSelectedImageId(nextJob.images[0]?.id ?? null);
      setView("console");
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
          <h1>{view === "console" ? "Processing Console" : "History Gallery"}</h1>
        </div>
        <div className="topActions">
          <nav className="viewTabs" aria-label="Primary views">
            <button
              className={`viewTab ${view === "console" ? "active" : ""}`}
              type="button"
              onClick={() => setView("console")}
              aria-pressed={view === "console"}
            >
              <Play size={16} />
              Console
            </button>
            <button
              className={`viewTab ${view === "gallery" ? "active" : ""}`}
              type="button"
              onClick={() => setView("gallery")}
              aria-pressed={view === "gallery"}
            >
              <HistoryIcon size={16} />
              Gallery
              <span className="tabCount">{history.length}</span>
            </button>
          </nav>
          <div className="healthPill">
            <span className="pulse" />
            Local API
          </div>
        </div>
      </header>

      {view === "console" ? (
        <section className="controlBand" aria-label="Job setup">
          <div className="controlRow controlRowPrimary">
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

            <button
              className="primaryButton"
              type="button"
              disabled={!canSubmit}
              onClick={submitJob}
              title={
                promptReady ? "Запустить обработку" : "Введите Creative Prompt"
              }
            >
              <Play size={18} />
              {submitting ? "Обработка..." : "Process"}
            </button>
          </div>

          <div className="controlRow controlRowOptions">
            <label className="optionField">
              <span>Input Processing</span>
              <div
                className="choiceGroup inputProcessingGroup"
                role="radiogroup"
                aria-label="Input Processing"
              >
                {INPUT_PROCESSING_OPTIONS.map((item) => (
                  <button
                    key={item.id}
                    className={`choiceButton inputProcessingButton ${
                      inputProcessing === item.id ? "active" : ""
                    }`}
                    type="button"
                    disabled={submitting}
                    onClick={() => setInputProcessing(item.id)}
                    title={item.title}
                    aria-pressed={inputProcessing === item.id}
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
                className="choiceGroup restorationGroup"
                role="radiogroup"
                aria-label="Restoration"
              >
                {RESTORATION_MODES.map((item) => (
                  <button
                    key={item.id}
                    className={`choiceButton restorationButton ${
                      restoration === item.id ? "active" : ""
                    }`}
                    type="button"
                    disabled={submitting}
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

            <label className="optionField">
              <span>Final Processing</span>
              <div
                className="choiceGroup finalProcessingGroup"
                role="radiogroup"
                aria-label="Final Processing"
              >
                {FINAL_PROCESSING_OPTIONS.map((item) => (
                  <button
                    key={item.id}
                    className={`choiceButton finalProcessingButton ${
                      finalProcessing === item.id ? "active" : ""
                    }`}
                    type="button"
                    disabled={submitting}
                    onClick={() => setFinalProcessing(item.id)}
                    title={item.title}
                    aria-pressed={finalProcessing === item.id}
                  >
                    <span>{item.shortLabel}</span>
                    {item.label}
                  </button>
                ))}
              </div>
            </label>
          </div>

          {finalProcessing === "creative" ? (
            <label className="optionField promptField">
              <span>Creative Prompt</span>
              <textarea
                className="promptTextArea"
                value={creativePrompt}
                disabled={submitting}
                rows={3}
                maxLength={1200}
                placeholder="Preserve the composition and make the photo look like a clean archival print"
                onChange={(event) => setCreativePrompt(event.target.value)}
              />
            </label>
          ) : null}
        </section>
      ) : null}

      {visibleError ? (
        <div className="errorBanner" role="alert">
          <AlertCircle size={18} />
          {visibleError}
        </div>
      ) : null}

      {view === "console" ? (
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

                <JobOptionChips job={job} />

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

                {hasBatchDownload(job) ? (
                  <a
                    className="downloadButton"
                    href={apiUrl(job.download_url)}
                    title="Скачать batch ZIP"
                  >
                    <Archive size={17} />
                    Batch ZIP
                  </a>
                ) : null}
              </div>
            ) : null}
          </aside>

          <section className="resultPane" aria-label="Processing results">
            {selectedImage ? (
              <ImageDetails image={selectedImage} onOpenPreview={openPreview} />
            ) : (
              <div className="emptyState">
                <Images size={34} />
                <h2>Готов к обработке</h2>
                <p>Выбранные файлы появятся слева, результаты — здесь.</p>
              </div>
            )}
          </section>
        </section>
      ) : (
        <GalleryView
          historyLoaded={historyLoaded}
          jobs={history}
          selectedImage={selectedHistoryImage}
          selectedJob={selectedHistoryJob}
          onOpenPreview={openPreview}
          onRefresh={refreshHistoryNow}
          onSelectImage={setSelectedHistoryImageId}
          onSelectJob={(historyJob) => {
            setSelectedHistoryJobId(historyJob.id);
            setSelectedHistoryImageId(historyJob.images[0]?.id ?? null);
          }}
        />
      )}

      {lightbox ? (
        <Lightbox item={lightbox} onClose={() => setLightbox(null)} />
      ) : null}
    </main>
  );
}

function GalleryView({
  historyLoaded,
  jobs,
  selectedImage,
  selectedJob,
  onOpenPreview,
  onRefresh,
  onSelectImage,
  onSelectJob,
}: {
  historyLoaded: boolean;
  jobs: Job[];
  selectedImage: ImageResult | null;
  selectedJob: Job | null;
  onOpenPreview: OpenPreview;
  onRefresh: () => void;
  onSelectImage: (imageId: string) => void;
  onSelectJob: (job: Job) => void;
}) {
  return (
    <section className="galleryWorkspace" aria-label="History gallery">
      <aside className="historyPane" aria-label="Previous jobs">
        <div className="paneHeader">
          <div>
            <p className="eyebrow">History</p>
            <h2>Jobs</h2>
          </div>
          <button
            className="iconButton"
            type="button"
            onClick={onRefresh}
            title="Обновить историю"
            aria-label="Обновить историю"
          >
            <RefreshCw size={17} />
          </button>
        </div>

        {jobs.length ? (
          <div className="historyGrid">
            {jobs.map((historyJob) => (
              <HistoryJobCard
                key={historyJob.id}
                job={historyJob}
                selected={selectedJob?.id === historyJob.id}
                onOpenPreview={onOpenPreview}
                onSelectJob={onSelectJob}
              />
            ))}
          </div>
        ) : (
          <div className="emptyState historyEmptyState">
            <HistoryIcon size={32} />
            <h2>{historyLoaded ? "История пуста" : "Загрузка истории"}</h2>
            <p>
              {historyLoaded
                ? "Готовые jobs появятся здесь после обработки."
                : "Запрашиваю список jobs из API."}
            </p>
          </div>
        )}
      </aside>

      <section className="historyDetailPane" aria-label="Selected job details">
        {selectedJob ? (
          <JobDetails
            job={selectedJob}
            selectedImage={selectedImage}
            onOpenPreview={onOpenPreview}
            onSelectImage={onSelectImage}
          />
        ) : (
          <div className="emptyState">
            <Images size={34} />
            <h2>Job не выбран</h2>
            <p>Выберите job из истории, чтобы открыть результаты.</p>
          </div>
        )}
      </section>
    </section>
  );
}

function HistoryJobCard({
  job,
  selected,
  onOpenPreview,
  onSelectJob,
}: {
  job: Job;
  selected: boolean;
  onOpenPreview: OpenPreview;
  onSelectJob: (job: Job) => void;
}) {
  const preview = bestPreview(job);
  const artifactTypes = generatedArtifactTypes(job);

  return (
    <article className={`historyCard ${selected ? "selected" : ""}`}>
      {preview ? (
        <ThumbnailButton preview={preview} onOpenPreview={onOpenPreview} />
      ) : (
        <div className="historyThumbFallback">
          <FileImage size={28} />
          <strong>Нет preview</strong>
        </div>
      )}

      <button
        className="historyCardBody"
        type="button"
        onClick={() => onSelectJob(job)}
      >
        <div className="historyCardTitle">
          <div>
            <p className="eyebrow">Job</p>
            <h3>{shortId(job.id)}</h3>
          </div>
          <StatusBadge status={job.status} compact />
        </div>

        <div className="historyFacts">
          <span>
            <Images size={15} />
            {job.images.length} images
          </span>
          <span>
            <Clock3 size={15} />
            {formatDateTime(job.created_at)}
          </span>
          <span>
            <RefreshCw size={15} />
            {formatDateTime(job.updated_at)}
          </span>
        </div>

        <JobOptionChips job={job} compact />
        <ArtifactChips artifactTypes={artifactTypes} />
      </button>

      {hasBatchDownload(job) ? (
        <a
          className="historyZipButton"
          href={apiUrl(job.download_url)}
          title="Скачать batch ZIP"
        >
          <Archive size={16} />
          Batch ZIP
        </a>
      ) : null}
    </article>
  );
}

function ThumbnailButton({
  preview,
  onOpenPreview,
}: {
  preview: ArtifactPreview;
  onOpenPreview: OpenPreview;
}) {
  const [previewFailed, setPreviewFailed] = useState(false);

  useEffect(() => {
    setPreviewFailed(false);
  }, [preview.artifact.preview_url]);

  if (previewFailed) {
    return (
      <button className="historyThumbButton failed" type="button" disabled>
        <div className="previewFallback">
          <FileImage size={28} />
          <strong>Предпросмотр недоступен</strong>
          <span>{preview.artifact.mime_type}</span>
        </div>
      </button>
    );
  }

  return (
    <button
      className="historyThumbButton"
      type="button"
      onClick={() =>
        onOpenPreview(preview.artifact, preview.label, preview.image.filename)
      }
      title={`Открыть ${preview.artifact.filename}`}
    >
      <img
        src={previewUrl(preview.artifact, 512)}
        alt={`${preview.label}: ${preview.artifact.filename}`}
        onError={() => setPreviewFailed(true)}
      />
      <span className="historyThumbLabel">{preview.label}</span>
      <span className="previewZoomIcon" aria-hidden="true">
        <Maximize2 size={16} />
      </span>
    </button>
  );
}

function JobDetails({
  job,
  selectedImage,
  onOpenPreview,
  onSelectImage,
}: {
  job: Job;
  selectedImage: ImageResult | null;
  onOpenPreview: OpenPreview;
  onSelectImage: (imageId: string) => void;
}) {
  const counts = jobStatusCounts(job);

  return (
    <>
      <div className="resultHeader">
        <div>
          <p className="eyebrow">Selected Job</p>
          <h2>{shortId(job.id)}</h2>
        </div>
        <StatusBadge status={job.status} />
      </div>

      <div className="historyDetailSummary">
        <div className="statusGrid">
          <Metric label="Готово" value={counts.success} />
          <Metric label="Частично" value={counts.partial_success} />
          <Metric label="Ошибки" value={counts.failed} />
        </div>

        <div>
          <JobOptionChips job={job} />
          <div className="historyDetailTimes">
            <span>Created: {formatDateTime(job.created_at)}</span>
            <span>Updated: {formatDateTime(job.updated_at)}</span>
          </div>
        </div>
      </div>

      {job.errors.length ? <ErrorList errors={job.errors} /> : null}

      <div className="historyDetailLayout">
        <aside className="detailImageRail" aria-label="Images in selected job">
          <div className="paneHeader compactPaneHeader">
            <div>
              <p className="eyebrow">Images</p>
              <h2>{job.images.length}</h2>
            </div>
          </div>

          <div className="imageList">
            {job.images.map((image) => (
              <button
                key={image.id}
                className={`imageRow ${
                  selectedImage?.id === image.id ? "selected" : ""
                }`}
                type="button"
                onClick={() => onSelectImage(image.id)}
              >
                <FileImage size={18} />
                <span className="imageName">{image.filename}</span>
                <StatusBadge status={image.status} compact />
              </button>
            ))}
          </div>

          {hasBatchDownload(job) ? (
            <a
              className="downloadButton"
              href={apiUrl(job.download_url)}
              title="Скачать batch ZIP"
            >
              <Archive size={17} />
              Batch ZIP
            </a>
          ) : null}
        </aside>

        <section className="detailResult" aria-label="Selected image artifacts">
          {selectedImage ? (
            <ImageDetails
              image={selectedImage}
              onOpenPreview={onOpenPreview}
            />
          ) : (
            <div className="emptyState detailEmptyState">
              <FileImage size={32} />
              <h2>Изображений нет</h2>
              <p>В этом job пока нет доступных image records.</p>
            </div>
          )}
        </section>
      </div>
    </>
  );
}

function JobOptionChips({
  job,
  compact = false,
}: {
  job: Job;
  compact?: boolean;
}) {
  return (
    <div className={`jobMeta ${compact ? "compactMeta" : ""}`}>
      <span>{INPUT_PROCESSING_LABELS[job.input_processing]}</span>
      <span>{RESTORATION_LABELS[job.restoration]}</span>
      <span>{FINAL_PROCESSING_LABELS[job.final_processing]}</span>
      {job.legacy ? <span>Legacy</span> : null}
      {job.inferred ? <span>Inferred</span> : null}
    </div>
  );
}

function ArtifactChips({
  artifactTypes,
}: {
  artifactTypes: ArtifactType[];
}) {
  return (
    <div className="artifactChips">
      {artifactTypes.length ? (
        artifactTypes.map((type) => (
          <span className={`artifactChip ${type}`} key={type}>
            {ARTIFACT_LABELS[type]}
          </span>
        ))
      ) : (
        <span className="artifactChip muted">Original only</span>
      )}
    </div>
  );
}

function Lightbox({
  item,
  onClose,
}: {
  item: LightboxItem;
  onClose: () => void;
}) {
  const [previewFailed, setPreviewFailed] = useState(false);

  useEffect(() => {
    setPreviewFailed(false);
  }, [item.artifact.preview_url]);

  useEffect(() => {
    const originalOverflow = document.body.style.overflow;

    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        onClose();
      }
    }

    document.body.style.overflow = "hidden";
    window.addEventListener("keydown", handleKeyDown);

    return () => {
      document.body.style.overflow = originalOverflow;
      window.removeEventListener("keydown", handleKeyDown);
    };
  }, [onClose]);

  return (
    <div
      className="lightboxBackdrop"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) {
          onClose();
        }
      }}
    >
      <section
        className="lightboxPanel"
        role="dialog"
        aria-modal="true"
        aria-label={`${item.label}: ${item.artifact.filename}`}
      >
        <div className="lightboxHeader">
          <div>
            <p className="eyebrow">{item.label}</p>
            <h2>{item.artifact.filename}</h2>
            <span>{item.imageFilename}</span>
          </div>
          <div className="lightboxActions">
            <a
              className="lightboxDownload"
              href={apiUrl(item.artifact.download_url)}
              title={`Скачать ${item.artifact.filename}`}
            >
              <Download size={17} />
              Download
            </a>
            <button
              className="iconButton lightboxClose"
              type="button"
              onClick={onClose}
              title="Закрыть"
              aria-label="Закрыть"
            >
              <X size={18} />
            </button>
          </div>
        </div>

        <div className="lightboxImageFrame">
          {!previewFailed ? (
            <img
              src={previewUrl(item.artifact, 1920)}
              alt={`${item.label}: ${item.artifact.filename}`}
              onError={() => setPreviewFailed(true)}
            />
          ) : (
            <div className="previewFallback lightboxFallback">
              <FileImage size={34} />
              <strong>Предпросмотр недоступен</strong>
              <span>{item.artifact.mime_type}</span>
            </div>
          )}
        </div>
      </section>
    </div>
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

function ImageDetails({
  image,
  onOpenPreview,
}: {
  image: ImageResult;
  onOpenPreview: OpenPreview;
}) {
  const previewArtifacts = orderedArtifacts(image);

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
        {previewArtifacts.length ? (
          previewArtifacts.map(({ artifact, label }) => (
            <PreviewCard
              key={artifact.type}
              artifact={artifact}
              imageFilename={image.filename}
              label={label}
              onOpenPreview={onOpenPreview}
            />
          ))
        ) : (
          <div className="previewFallback noArtifacts">
            <FileImage size={30} />
            <strong>Артефакты не созданы</strong>
            <span>{image.status}</span>
          </div>
        )}
      </div>

      <div className="artifactStrip">
        {previewArtifacts.map(({ artifact }) => (
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
  imageFilename,
  onOpenPreview,
}: {
  label: string;
  artifact: Artifact;
  imageFilename: string;
  onOpenPreview: OpenPreview;
}) {
  const [previewFailed, setPreviewFailed] = useState(false);

  useEffect(() => {
    setPreviewFailed(false);
  }, [artifact.preview_url]);

  return (
    <article className="previewCard">
      <div className="previewHeader">
        <h3>{label}</h3>
        <a
          className="iconButton"
          href={apiUrl(artifact.download_url)}
          title={`Скачать ${artifact.filename}`}
          aria-label={`Скачать ${artifact.filename}`}
        >
          <Download size={16} />
        </a>
      </div>
      <button
        className="previewFrame previewButton"
        type="button"
        disabled={previewFailed}
        onClick={() => onOpenPreview(artifact, label, imageFilename)}
        title={`Открыть ${artifact.filename}`}
      >
        {!previewFailed ? (
          <img
            src={previewUrl(artifact)}
            alt={`${label}: ${artifact.filename}`}
            onError={() => setPreviewFailed(true)}
          />
        ) : (
          <div className="previewFallback">
            <FileImage size={30} />
            <strong>Предпросмотр недоступен</strong>
            <span>{artifact.mime_type}</span>
          </div>
        )}
        {!previewFailed ? (
          <span className="previewZoomIcon" aria-hidden="true">
            <Maximize2 size={16} />
          </span>
        ) : null}
      </button>
      <p className="artifactName">{artifact.filename}</p>
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

function bestPreview(job: Job): ArtifactPreview | null {
  for (const type of THUMBNAIL_ARTIFACT_ORDER) {
    for (const image of job.images) {
      const artifact = artifactOf(image, type);
      if (artifact) {
        return {
          artifact,
          image,
          label: ARTIFACT_LABELS[type],
        };
      }
    }
  }
  return null;
}

function orderedArtifacts(
  image: ImageResult,
): Array<{ artifact: Artifact; label: string }> {
  return ARTIFACT_ORDER.flatMap((type) => {
    const artifact = artifactOf(image, type);
    return artifact ? [{ artifact, label: ARTIFACT_LABELS[type] }] : [];
  });
}

function generatedArtifactTypes(job: Job): ArtifactType[] {
  const availableTypes = new Set(
    job.images.flatMap((image) =>
      image.artifacts.map((artifact) => artifact.type),
    ),
  );
  return GENERATED_ARTIFACT_ORDER.filter((type) => availableTypes.has(type));
}

function hasBatchDownload(job: Job): boolean {
  return Boolean(job.download_url && generatedArtifactTypes(job).length);
}

function jobStatusCounts(
  job: Pick<Job, "images"> | null | undefined,
): Record<ProcessingStatus, number> {
  const counts: Record<ProcessingStatus, number> = {
    pending: 0,
    running: 0,
    success: 0,
    partial_success: 0,
    failed: 0,
  };

  for (const image of job?.images ?? []) {
    counts[image.status] += 1;
  }

  return counts;
}

function previewUrl(artifact: Artifact, maxEdge?: number): string {
  if (!maxEdge) {
    return apiUrl(artifact.preview_url);
  }
  const separator = artifact.preview_url.includes("?") ? "&" : "?";
  return apiUrl(`${artifact.preview_url}${separator}max_edge=${maxEdge}`);
}

function formatDateTime(value: string): string {
  const timestamp = Date.parse(value);
  if (Number.isNaN(timestamp)) {
    return "Unknown";
  }
  return DATE_TIME_FORMATTER.format(timestamp);
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
