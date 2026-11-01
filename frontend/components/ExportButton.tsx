"use client";

import { useEffect, useState } from "react";

import { apiJson, downloadFile, errorMessage, type InvoiceExport } from "@/lib/api";

const POLL_MS = 2000;

type ExportButtonProps = {
  query: string;
  disabled: boolean;
};

export default function ExportButton({ query, disabled }: ExportButtonProps) {
  const [invoiceExport, setInvoiceExport] = useState<InvoiceExport | null>(null);
  const [starting, setStarting] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [error, setError] = useState("");

  const exportId = invoiceExport?.export_id;
  const processing =
    invoiceExport?.status === "pending" || invoiceExport?.status === "processing";

  useEffect(() => {
    if (!processing) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;

    async function poll() {
      try {
        const latest = await apiJson<InvoiceExport>(`/api/exports/${exportId}/`, {
          signal: controller.signal,
        });
        setInvoiceExport(latest);
        setError("");
      } catch (caught) {
        if (controller.signal.aborted) return;
        setError(`Export updates paused. ${errorMessage(caught)}`);
      }
      timer = setTimeout(poll, POLL_MS);
    }

    timer = setTimeout(poll, POLL_MS);
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [processing, exportId]);

  async function handleExport() {
    setStarting(true);
    setError("");
    setInvoiceExport(null);
    try {
      const created = await apiJson<InvoiceExport>(`/api/invoices/export/?${query}`, {
        method: "POST",
      });
      setInvoiceExport(created);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setStarting(false);
    }
  }

  async function handleDownload() {
    if (!invoiceExport?.download_url) return;
    setDownloading(true);
    setError("");
    try {
      await downloadFile(invoiceExport.download_url, `invoices-${invoiceExport.export_id}.csv`);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setDownloading(false);
    }
  }

  const busy = starting || processing;

  return (
    <div className="flex flex-wrap items-center justify-end gap-3">
      <p role="status" className="text-sm text-slate-600">
        {busy && "Preparing your export..."}
      </p>
      {invoiceExport?.status === "failed" && (
        <p role="alert" className="text-sm text-red-700">
          {invoiceExport.failure_reason}
        </p>
      )}
      {error && (
        <p role="alert" className="text-sm text-red-700">
          {error}
        </p>
      )}
      {invoiceExport?.status === "completed" && (
        <button
          type="button"
          disabled={downloading}
          onClick={handleDownload}
          className="text-sm font-medium text-blue-700 hover:underline disabled:opacity-60"
        >
          {downloading
            ? "Downloading..."
            : `Download CSV (${invoiceExport.row_count.toLocaleString()} rows)`}
        </button>
      )}
      <button
        type="button"
        disabled={disabled || busy}
        onClick={handleExport}
        className="rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
      >
        {busy ? "Exporting..." : "Export CSV"}
      </button>
    </div>
  );
}
