type PaginationProps = {
  page: number;
  pageSize: number;
  count: number;
  onChange: (page: number) => void;
};

const buttonClass =
  "rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50";

export default function Pagination({ page, pageSize, count, onChange }: PaginationProps) {
  const lastPage = Math.max(1, Math.ceil(count / pageSize));
  const first = (page - 1) * pageSize + 1;
  const last = Math.min(page * pageSize, count);

  return (
    <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
      <p className="text-sm text-slate-600">
        Showing {first.toLocaleString()}–{last.toLocaleString()} of {count.toLocaleString()}
      </p>
      <div className="flex items-center gap-2">
        <button
          type="button"
          className={buttonClass}
          disabled={page <= 1}
          onClick={() => onChange(page - 1)}
        >
          Previous
        </button>
        <span className="text-sm text-slate-600">
          Page {page.toLocaleString()} of {lastPage.toLocaleString()}
        </span>
        <button
          type="button"
          className={buttonClass}
          disabled={page >= lastPage}
          onClick={() => onChange(page + 1)}
        >
          Next
        </button>
      </div>
    </div>
  );
}
