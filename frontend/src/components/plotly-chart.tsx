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
 *
 * Sprint 7: the app is dark-mode-by-default now, so a light-background Plotly
 * chart would look broken embedded in a dark Card — these defaults (applied
 * *before* the figure's own layout, so a figure that sets its own colors
 * still wins) make the chart transparent/dark-friendly without touching what
 * the backend sends.
 */
export function PlotlyChart({ figure }: { figure: Partial<Figure> }) {
  return (
    <Plot
      data={figure.data ?? []}
      layout={{
        autosize: true,
        height: 360,
        margin: { t: 32 },
        paper_bgcolor: "transparent",
        plot_bgcolor: "transparent",
        font: { color: "#b3b3b3" },
        xaxis: { gridcolor: "rgba(255,255,255,0.1)", zerolinecolor: "rgba(255,255,255,0.1)" },
        yaxis: { gridcolor: "rgba(255,255,255,0.1)", zerolinecolor: "rgba(255,255,255,0.1)" },
        ...(figure.layout ?? {}),
      }}
      style={{ width: "100%" }}
      config={{ responsive: true, displaylogo: false }}
    />
  );
}
