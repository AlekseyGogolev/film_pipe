import type { Job, ProcessingStatus } from "./types";

export function isActiveStatus(status: ProcessingStatus): boolean {
  return status === "pending" || status === "running";
}

export function isJobActive(job: Pick<Job, "status"> | null | undefined): boolean {
  return Boolean(job && isActiveStatus(job.status));
}

export function hasActiveJobs(jobs: Job[]): boolean {
  return jobs.some(isJobActive);
}

export function newestJob(current: Job, incoming: Job): Job {
  return timestamp(incoming.updated_at) >= timestamp(current.updated_at)
    ? incoming
    : current;
}

export function upsertNewestJob(jobs: Job[], incoming: Job): Job[] {
  let found = false;
  const nextJobs = jobs.map((job) => {
    if (job.id !== incoming.id) {
      return job;
    }
    found = true;
    return newestJob(job, incoming);
  });

  return found ? nextJobs : [incoming, ...nextJobs];
}

export function mergeNewestJobs(current: Job[], incoming: Job[]): Job[] {
  const currentById = new Map(current.map((job) => [job.id, job]));
  const seen = new Set<string>();

  const merged = incoming.map((job) => {
    seen.add(job.id);
    const knownJob = currentById.get(job.id);
    return knownJob ? newestJob(knownJob, job) : job;
  });

  for (const job of current) {
    if (!seen.has(job.id)) {
      merged.push(job);
    }
  }

  return merged;
}

function timestamp(value: string): number {
  const parsed = Date.parse(value);
  return Number.isNaN(parsed) ? 0 : parsed;
}
