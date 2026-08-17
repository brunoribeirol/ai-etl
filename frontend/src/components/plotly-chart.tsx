"use client";

import dynamic from "next/dynamic";
import type { Figure } from "react-plotly.js";

// react-plotly.js manipulates the DOM directly (via plotly.js) and has no
// server-side rendering story — must be dynamically imported with ssr:false,
// or `next build`'s server-side render pass fails trying to touch `window`.
const Plot = dynamic(() => import("react-plotly.js"), { ssr: false });

/**
 * Renders a Plotly figure exactly as `api/serialization.py::_serialize_figure`
 * emits it (`fig.to_plotly_json()`, ADR-011/ADR-009) — the same {data, layout}
 * shape `Plotly.newPlot()` expects, no transformation needed on this side.
 */
export function PlotlyChart({ figure }: { figure: Partial<Figure> }) {
  return (
    <Plot
      data={figure.data ?? []}
      layout={{ autosize: true, height: 360, margin: { t: 32 }, ...(figure.layout ?? {}) }}
      style={{ width: "100%" }}
      config={{ responsive: true, displaylogo: false }}
    />
  );
}
