import type { Invoice } from "@/lib/api";

const COLUMNS = [
  { field: "invoice_number", label: "Invoice number" },
  { field: "customer_name", label: "Customer" },
  { field: "amount", label: "Amount" },
  { field: "invoice_date", label: "Date" },
  { field: "status", label: "Status" },
];

const STATUS_CLASSES: Record<Invoice["status"], string> = {
  paid: "bg-green-100 text-green-800",
  pending: "bg-amber-100 text-amber-800",
  overdue: "bg-red-100 text-red-800",
};

type InvoiceTableProps = {
  invoices: Invoice[];
  ordering: string;
  onOrderingChange: (ordering: string) => void;
};

export default function InvoiceTable({
  invoices,
  ordering,
  onOrderingChange,
}: InvoiceTableProps) {
  const sortedField = ordering.replace("-", "");
  const descending = ordering.startsWith("-");

  return (
    <div className="overflow-x-auto rounded-lg border border-slate-200 bg-white">
      <table className="w-full min-w-[42rem] text-left text-sm">
        <thead className="border-b border-slate-200 bg-slate-50 text-xs uppercase text-slate-600">
          <tr>
            <th scope="col" className="px-4 py-3 font-medium">
              ID
            </th>
            {COLUMNS.map(({ field, label }) => {
              const active = sortedField === field;
              return (
                <th
                  key={field}
                  scope="col"
                  aria-sort={active ? (descending ? "descending" : "ascending") : "none"}
                  className={`px-4 py-3 font-medium ${field === "amount" ? "text-right" : ""}`}
                >
                  <button
                    type="button"
                    className="font-medium uppercase hover:text-slate-900"
                    onClick={() =>
                      onOrderingChange(active && !descending ? `-${field}` : field)
                    }
                  >
                    {label} {active ? (descending ? "↓" : "↑") : ""}
                  </button>
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100 text-slate-900">
          {invoices.map((invoice) => (
            <tr key={invoice.id}>
              <td className="px-4 py-3 text-slate-500">{invoice.id}</td>
              <td className="px-4 py-3 font-medium">{invoice.invoice_number}</td>
              <td className="px-4 py-3">{invoice.customer_name}</td>
              <td className="px-4 py-3 text-right tabular-nums">{invoice.amount}</td>
              <td className="px-4 py-3 whitespace-nowrap">{invoice.invoice_date}</td>
              <td className="px-4 py-3">
                <span
                  className={`rounded-full px-2 py-0.5 text-xs font-medium capitalize ${STATUS_CLASSES[invoice.status]}`}
                >
                  {invoice.status}
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
