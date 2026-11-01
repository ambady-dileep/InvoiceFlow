"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import ErrorBanner from "@/components/ErrorBanner";
import ExportButton from "@/components/ExportButton";
import InvoiceFilters, { type Filters, NO_FILTERS } from "@/components/InvoiceFilters";
import InvoiceTable from "@/components/InvoiceTable";
import Pagination from "@/components/Pagination";
import { apiJson, errorMessage, type Invoice, type Paginated } from "@/lib/api";

const PAGE_SIZE = 25;
const DEBOUNCE_MS = 300;

type Result = { key: string; data?: Paginated<Invoice>; error?: string };

function buildQuery(filters: Filters, ordering: string, page: number) {
  const params: Record<string, string> = {
    search: filters.search.trim(),
    status: filters.status,
    invoice_date__gte: filters.dateFrom,
    invoice_date__lte: filters.dateTo,
    amount__gte: filters.amountMin,
    amount__lte: filters.amountMax,
    ordering,
    page: String(page),
    page_size: String(PAGE_SIZE),
  };
  const query = new URLSearchParams();
  for (const [name, value] of Object.entries(params)) {
    if (value) query.set(name, value);
  }
  return query.toString();
}

function useDebounced<T>(value: T, delay: number) {
  const [debounced, setDebounced] = useState(value);

  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delay);
    return () => clearTimeout(timer);
  }, [value, delay]);

  return debounced;
}

export default function InvoicesPage() {
  const [filters, setFilters] = useState(NO_FILTERS);
  const [ordering, setOrdering] = useState("-invoice_date");
  const [page, setPage] = useState(1);
  const [attempt, setAttempt] = useState(0);
  const [result, setResult] = useState<Result | null>(null);

  const debouncedFilters = useDebounced(filters, DEBOUNCE_MS);
  const query = buildQuery(debouncedFilters, ordering, page);
  const key = `${query}#${attempt}`;
  const loading = result?.key !== key;

  useEffect(() => {
    const controller = new AbortController();
    apiJson<Paginated<Invoice>>(`/api/invoices/?${query}`, { signal: controller.signal })
      .then((data) => setResult({ key, data }))
      .catch((error) => {
        if (!controller.signal.aborted) setResult({ key, error: errorMessage(error) });
      });
    return () => controller.abort();
  }, [key, query]);

  const data = result?.data;
  const hasFilters = Object.values(filters).some(Boolean);

  return (
    <>
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-4">
          <h1 className="text-xl font-semibold text-slate-900">Invoices</h1>
          {hasFilters && (
            <button
              type="button"
              className="text-sm font-medium text-blue-700 hover:underline"
              onClick={() => {
                setFilters(NO_FILTERS);
                setPage(1);
              }}
            >
              Clear filters
            </button>
          )}
        </div>
        <ExportButton query={query} disabled={!data?.count} />
      </div>

      <InvoiceFilters
        filters={filters}
        onChange={(next) => {
          setFilters(next);
          setPage(1);
        }}
      />

      {result?.error && !loading && (
        <ErrorBanner message={result.error} onRetry={() => setAttempt(attempt + 1)} />
      )}

      {!data && loading && <p className="py-12 text-center text-sm text-slate-500">Loading invoices...</p>}

      {data && data.count === 0 && !loading && (
        <div className="rounded-lg border border-slate-200 bg-white py-12 text-center text-sm text-slate-600">
          {hasFilters ? (
            "No invoices match these filters."
          ) : (
            <>
              No invoices yet.{" "}
              <Link href="/uploads" className="font-medium text-blue-700 hover:underline">
                Upload a file
              </Link>{" "}
              to get started.
            </>
          )}
        </div>
      )}

      {data && data.count > 0 && (
        <div aria-busy={loading} className={loading ? "opacity-60" : ""}>
          <InvoiceTable
            invoices={data.results}
            ordering={ordering}
            onOrderingChange={(next) => {
              setOrdering(next);
              setPage(1);
            }}
          />
          <Pagination page={page} pageSize={PAGE_SIZE} count={data.count} onChange={setPage} />
        </div>
      )}
    </>
  );
}
