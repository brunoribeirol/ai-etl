/** Renders the first 20 rows of a DataFrame-as-records payload (matches the
 * preview-only convention `_serialize_analysis_result`/Streamlit's
 * `_render_results` already use — the full data was always a CSV download
 * away, never meant to be rendered in full inline). */
export function DataTable({ rows }: { rows: Record<string, unknown>[] }) {
  if (rows.length === 0) {
    return null;
  }

  const columns = Object.keys(rows[0]);

  return (
    <div className="overflow-auto border rounded">
      <table className="text-sm w-full">
        <thead>
          <tr className="border-b bg-gray-50 dark:bg-gray-900">
            {columns.map((col) => (
              <th key={col} className="text-left p-2 font-medium whitespace-nowrap">
                {col}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.slice(0, 20).map((row, i) => (
            <tr key={i} className="border-b last:border-0">
              {columns.map((col) => (
                <td key={col} className="p-2 whitespace-nowrap">
                  {String(row[col] ?? "")}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {rows.length > 20 && (
        <p className="text-xs text-gray-400 p-2">
          Mostrando 20 de {rows.length} linhas.
        </p>
      )}
    </div>
  );
}
