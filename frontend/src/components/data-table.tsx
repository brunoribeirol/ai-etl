import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

/** Renders the first 20 rows of a DataFrame-as-records payload (matches the
 * preview-only convention `_serialize_analysis_result`/Streamlit's
 * `_render_results` already use — the full data was always a CSV download
 * away, never meant to be rendered in full inline). Sprint 7: shadcn Table
 * instead of a raw `<table>`, same data/behavior. */
export function DataTable({ rows }: { rows: Record<string, unknown>[] }) {
  if (rows.length === 0) {
    return null;
  }

  const columns = Object.keys(rows[0]);

  return (
    <div className="border border-border/60 rounded-lg overflow-hidden">
      <Table>
        <TableHeader>
          <TableRow>
            {columns.map((col) => (
              <TableHead key={col}>{col}</TableHead>
            ))}
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.slice(0, 20).map((row, i) => (
            <TableRow key={i}>
              {columns.map((col) => (
                <TableCell key={col} className="text-muted-foreground">
                  {String(row[col] ?? "")}
                </TableCell>
              ))}
            </TableRow>
          ))}
        </TableBody>
      </Table>
      {rows.length > 20 && (
        <p className="text-xs text-muted-foreground px-3 py-2 border-t border-border/60">
          Mostrando 20 de {rows.length} linhas.
        </p>
      )}
    </div>
  );
}
