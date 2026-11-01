"use client";

import { useEffect, useState } from "react";

import ErrorBanner from "@/components/ErrorBanner";
import Pagination from "@/components/Pagination";
import UploadForm from "@/components/UploadForm";
import UploadRow from "@/components/UploadRow";
import {
  apiJson,
  errorMessage,
  type Paginated,
  type Upload,
  type UploadStatus,
} from "@/lib/api";

const PAGE_SIZE = 10;
const POLL_MS = 2000;

type Result = { key: string; data?: Paginated<Upload>; error?: string };

export default function UploadsPage() {
  const [page, setPage] = useState(1);
  const [attempt, setAttempt] = useState(0);
  const [result, setResult] = useState<Result | null>(null);
  const [pollError, setPollError] = useState("");

  const key = `${page}#${attempt}`;
  const loading = result?.key !== key;
  const data = result?.data;
  const activeIds =
    data?.results
      .filter(({ status }) => status === "pending" || status === "processing")
      .map(({ upload_id }) => upload_id)
      .join(",") ?? "";

  useEffect(() => {
    const controller = new AbortController();
    apiJson<Paginated<Upload>>(`/api/uploads/?page=${page}&page_size=${PAGE_SIZE}`, {
      signal: controller.signal,
    })
      .then((loaded) => setResult({ key, data: loaded }))
      .catch((error) => {
        if (!controller.signal.aborted) setResult({ key, error: errorMessage(error) });
      });
    return () => controller.abort();
  }, [key, page]);

  useEffect(() => {
    if (!activeIds) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;

    async function poll() {
      try {
        const statuses = await Promise.all(
          activeIds.split(",").map((id) =>
            apiJson<UploadStatus>(`/api/uploads/${id}/status/`, {
              signal: controller.signal,
            }),
          ),
        );
        setResult((current) => {
          if (!current?.data) return current;
          const results = current.data.results.map((upload) => ({
            ...upload,
            ...statuses.find((status) => status.upload_id === upload.upload_id),
          }));
          return { ...current, data: { ...current.data, results } };
        });
        setPollError("");
      } catch (error) {
        if (controller.signal.aborted) return;
        setPollError(`Progress updates paused. ${errorMessage(error)}`);
      }
      timer = setTimeout(poll, POLL_MS);
    }

    timer = setTimeout(poll, POLL_MS);
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [activeIds]);

  return (
    <>
      <h1 className="mb-4 text-xl font-semibold text-slate-900">Uploads</h1>

      <UploadForm
        onUploaded={() => {
          setPage(1);
          setAttempt((current) => current + 1);
        }}
      />

      {pollError && <ErrorBanner message={pollError} />}
      {result?.error && !loading && (
        <ErrorBanner message={result.error} onRetry={() => setAttempt(attempt + 1)} />
      )}

      {!data && loading && <p className="py-12 text-center text-sm text-slate-500">Loading uploads...</p>}

      {data && data.count === 0 && !loading && (
        <div className="rounded-lg border border-slate-200 bg-white py-12 text-center text-sm text-slate-600">
          No uploads yet. Choose a file above to get started.
        </div>
      )}

      {data && data.count > 0 && (
        <div aria-busy={loading} className={loading ? "opacity-60" : ""}>
          <ul className="divide-y divide-slate-100 rounded-lg border border-slate-200 bg-white">
            {data.results.map((upload) => (
              <UploadRow key={upload.upload_id} upload={upload} />
            ))}
          </ul>
          <Pagination page={page} pageSize={PAGE_SIZE} count={data.count} onChange={setPage} />
        </div>
      )}
    </>
  );
}
