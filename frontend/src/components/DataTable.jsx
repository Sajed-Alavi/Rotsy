/**
 * Minimal dense table, theme-aware. Columns: { key, header, render?, className?,
 * headClassName?, mono? }. Rows are plain objects.
 */
export default function DataTable({ columns, rows, empty = 'No data.', onRowClick }) {
  return (
    <div className="overflow-x-auto border border-slate-200 dark:border-slate-800">
      <table className="w-full border-collapse text-sm">
        <thead>
          <tr className="border-b border-slate-200 bg-slate-50 dark:border-slate-800 dark:bg-slate-900/60">
            {columns.map((col) => (
              <th
                key={col.key}
                className={`px-3 py-2 text-left font-mono text-[10px] font-medium uppercase tracking-wider text-slate-500 ${col.headClassName || ''}`}
              >
                {col.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.length === 0 ? (
            <tr>
              <td colSpan={columns.length} className="px-3 py-8 text-center text-slate-400 dark:text-slate-600">
                {empty}
              </td>
            </tr>
          ) : (
            rows.map((row, i) => (
              <tr
                key={row.id ?? i}
                onClick={onRowClick ? () => onRowClick(row) : undefined}
                className={`border-b border-slate-100 transition-colors last:border-0 dark:border-slate-800/60 ${
                  onRowClick ? 'cursor-pointer hover:bg-slate-100/60 dark:hover:bg-slate-800/40' : 'hover:bg-slate-100/40 dark:hover:bg-slate-800/30'
                }`}
              >
                {columns.map((col) => (
                  <td
                    key={col.key}
                    className={`px-3 py-2 align-middle text-slate-700 dark:text-slate-300 ${col.mono ? 'font-mono tabular-nums text-slate-800 dark:text-slate-200' : ''} ${col.className || ''}`}
                  >
                    {col.render ? col.render(row[col.key], row) : row[col.key]}
                  </td>
                ))}
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  );
}
