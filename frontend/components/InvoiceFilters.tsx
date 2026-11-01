export type Filters = {
  search: string;
  status: string;
  dateFrom: string;
  dateTo: string;
  amountMin: string;
  amountMax: string;
};

export const NO_FILTERS: Filters = {
  search: "",
  status: "",
  dateFrom: "",
  dateTo: "",
  amountMin: "",
  amountMax: "",
};

type InvoiceFiltersProps = {
  filters: Filters;
  onChange: (filters: Filters) => void;
};

const labelClass = "text-xs font-medium text-slate-600";
const inputClass =
  "mt-1 w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 focus:border-blue-600 focus:outline-none focus:ring-1 focus:ring-blue-600";

export default function InvoiceFilters({ filters, onChange }: InvoiceFiltersProps) {
  function update(field: keyof Filters, value: string) {
    onChange({ ...filters, [field]: value });
  }

  return (
    <div className="mb-4 grid grid-cols-2 gap-3 rounded-lg border border-slate-200 bg-white p-4 md:grid-cols-3 lg:grid-cols-7">
      <label className={`${labelClass} col-span-2 md:col-span-3 lg:col-span-2`}>
        Search
        <input
          type="search"
          className={inputClass}
          placeholder="Invoice number or customer"
          value={filters.search}
          onChange={(event) => update("search", event.target.value)}
        />
      </label>
      <label className={labelClass}>
        Status
        <select
          className={inputClass}
          value={filters.status}
          onChange={(event) => update("status", event.target.value)}
        >
          <option value="">All</option>
          <option value="pending">Pending</option>
          <option value="paid">Paid</option>
          <option value="overdue">Overdue</option>
        </select>
      </label>
      <label className={labelClass}>
        From date
        <input
          type="date"
          className={inputClass}
          value={filters.dateFrom}
          onChange={(event) => update("dateFrom", event.target.value)}
        />
      </label>
      <label className={labelClass}>
        To date
        <input
          type="date"
          className={inputClass}
          value={filters.dateTo}
          onChange={(event) => update("dateTo", event.target.value)}
        />
      </label>
      <label className={labelClass}>
        Min amount
        <input
          type="number"
          min="0"
          step="0.01"
          className={inputClass}
          value={filters.amountMin}
          onChange={(event) => update("amountMin", event.target.value)}
        />
      </label>
      <label className={labelClass}>
        Max amount
        <input
          type="number"
          min="0"
          step="0.01"
          className={inputClass}
          value={filters.amountMax}
          onChange={(event) => update("amountMax", event.target.value)}
        />
      </label>
    </div>
  );
}
