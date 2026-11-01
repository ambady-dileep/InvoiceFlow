"use client";

import { type FormEvent, useRef, useState } from "react";

import { apiFetch, errorMessage } from "@/lib/api";

const MAX_SIZE_MB = 25;

function fileProblem(file: File) {
  if (!/\.(csv|xlsx)$/i.test(file.name)) return "Choose a CSV or XLSX file.";
  if (file.size === 0) return "The file is empty.";
  if (file.size > MAX_SIZE_MB * 1024 * 1024) {
    return `The file must not be larger than ${MAX_SIZE_MB} MB.`;
  }
  return "";
}

export default function UploadForm({ onUploaded }: { onUploaded: () => void }) {
  const input = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (!file) return;
    setMessage("");
    const problem = fileProblem(file);
    setError(problem);
    if (problem) return;

    setUploading(true);
    try {
      const body = new FormData();
      body.append("file", file);
      await apiFetch("/api/uploads/", { method: "POST", body });
      setMessage(`${file.name} was uploaded and is being processed.`);
      setFile(null);
      if (input.current) input.current.value = "";
      onUploaded();
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setUploading(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="mb-6 rounded-lg border border-slate-200 bg-white p-4">
      <label className="block text-sm font-medium text-slate-700" htmlFor="invoice-file">
        Invoice file
      </label>
      <p className="mt-0.5 text-xs text-slate-500">
        CSV or XLSX, up to {MAX_SIZE_MB} MB and 100,000 rows. Required columns: invoice_number,
        customer_name, amount, invoice_date, status.
      </p>
      <div className="mt-3 flex flex-wrap items-center gap-3">
        <input
          ref={input}
          id="invoice-file"
          type="file"
          accept=".csv,.xlsx"
          className="min-w-0 flex-1 text-sm text-slate-700 file:mr-3 file:rounded-md file:border-0 file:bg-slate-100 file:px-3 file:py-2 file:text-sm file:font-medium file:text-slate-700"
          onChange={(event) => {
            setFile(event.target.files?.[0] ?? null);
            setError("");
            setMessage("");
          }}
        />
        <button
          type="submit"
          disabled={!file || uploading}
          className="rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
        >
          {uploading ? "Uploading..." : "Upload"}
        </button>
      </div>
      {error && (
        <p role="alert" className="mt-3 rounded-md bg-red-50 p-3 text-sm text-red-700">
          {error}
        </p>
      )}
      {message && (
        <p role="status" className="mt-3 rounded-md bg-green-50 p-3 text-sm text-green-800">
          {message}
        </p>
      )}
    </form>
  );
}
