"use client";

import { useState } from "react";

import { downloadFile, errorMessage, type Upload } from "@/lib/api";

const STATUS_CLASSES: Record<Upload["status"], string> = {
  pending: "bg-slate-100 text-slate-700",
  processing: "bg-blue-100 text-blue-800",
  completed: "bg-green-100 text-green-800",
  failed: "bg-red-100 text-red-800",
};

export default function UploadRow({ upload }: { upload: Upload }) {
  const [downloading, setDownloading] = useState(false);
  const [error, setError] = useState("");
  const active = upload.status === "pending" || upload.status === "processing";

  async function handleDownload() {
    setDownloading(true);
    setError("");
    try {
      await downloadFile(
        `/api/uploads/${upload.upload_id}/errors/`,
        `upload-${upload.upload_id}-errors.csv`,
      );
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setDownloading(false);
    }
  }

  return (
    <li className="p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="min-w-0">
          <p className="truncate text-sm font-medium text-slate-900">{upload.original_filename}</p>
          <p className="text-xs text-slate-500">
            Upload {upload.upload_id} · {new Date(upload.created_at).toLocaleString()}
          </p>
        </div>
        <span
          className={`rounded-full px-2 py-0.5 text-xs font-medium capitalize ${STATUS_CLASSES[upload.status]}`}
        >
          {upload.status}
        </span>
      </div>

      <div
        role="progressbar"
        aria-label={`Progress for ${upload.original_filename}`}
        aria-valuenow={upload.progress}
        aria-valuemin={0}
        aria-valuemax={100}
        className="mt-3 h-2 overflow-hidden rounded-full bg-slate-100"
      >
        <div
          className={`h-full transition-all ${upload.status === "failed" ? "bg-red-500" : "bg-blue-600"}`}
          style={{ width: `${upload.progress}%` }}
        />
      </div>

      <div className="mt-2 flex flex-wrap items-center justify-between gap-2 text-sm text-slate-600">
        <p>
          {upload.progress}% · {upload.processed_rows.toLocaleString()} of{" "}
          {upload.total_rows.toLocaleString()} rows ·{" "}
          <span className="text-green-700">{upload.successful_rows.toLocaleString()} successful</span> ·{" "}
          <span className="text-red-700">{upload.failed_rows.toLocaleString()} failed</span>
        </p>
        {!active && upload.failed_rows > 0 && (
          <button
            type="button"
            disabled={downloading}
            onClick={handleDownload}
            className="font-medium text-blue-700 hover:underline disabled:opacity-60"
          >
            {downloading ? "Preparing report..." : "Download error report"}
          </button>
        )}
      </div>

      {upload.failure_reason && (
        <p className="mt-2 rounded-md bg-red-50 p-2 text-sm text-red-700">{upload.failure_reason}</p>
      )}
      {error && (
        <p role="alert" className="mt-2 rounded-md bg-red-50 p-2 text-sm text-red-700">
          {error}
        </p>
      )}
    </li>
  );
}
