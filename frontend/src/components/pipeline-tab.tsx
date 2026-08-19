import { CheckCircle2, AlertTriangle, XCircle } from "lucide-react";
import type { ReactNode } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { AGENTS } from "@/lib/agents";
import type { QualityCheck } from "@/lib/types";

const SEVERITY_ICON: Record<string, ReactNode> = {
  ok: <CheckCircle2 className="h-3.5 w-3.5 text-emerald-400 shrink-0" />,
  warning: <AlertTriangle className="h-3.5 w-3.5 text-amber-400 shrink-0" />,
  error: <XCircle className="h-3.5 w-3.5 text-red-400 shrink-0" />,
};

// Silver/LangGraph node keys, in execution order — matches
// `services/pipeline_service.py::AGENT_STEPS` and `core/graph.py`'s edges.
const SILVER_NODES = ["orchestrator", "extractor", "transformer", "quality", "loader"];

function agentLabel(node: string): string {
  const match = AGENTS.find((a) => a.name.toLowerCase() === node);
  return match ? `${match.emoji} ${match.name}` : node;
}

/**
 * Sprint 7 — replaces `app.py`'s "Execução dos Agentes Silver" + "Plano do
 * Pipeline" + "Relatório de Qualidade" sections. Reads `stage_durations`
 * (the real field — the old Streamlit read a `_agent_timings` key that was
 * never actually set on `PipelineState`, a pre-existing dead-code bug fixed
 * here, not reproduced), `pipeline_plan`, and `quality_report`, all already
 * present in `GET /runs/{run_id}`'s `state` — no backend change needed.
 */
export function PipelineTab({
  stageDurations,
  pipelinePlan,
  qualityReport,
}: {
  stageDurations?: Record<string, number>;
  pipelinePlan?: Record<string, unknown>;
  qualityReport?: { checks?: QualityCheck[]; severity?: string; summary?: string };
}) {
  const hasAny =
    (stageDurations && Object.keys(stageDurations).length > 0) ||
    (qualityReport?.checks && qualityReport.checks.length > 0) ||
    (pipelinePlan && Object.keys(pipelinePlan).length > 0);

  if (!hasAny) {
    return (
      <p className="text-sm text-muted-foreground">
        Nenhum detalhe de execução disponível para este run.
      </p>
    );
  }

  return (
    <div className="flex flex-col gap-6">
      {stageDurations && Object.keys(stageDurations).length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>Execução dos agentes Silver</CardTitle>
          </CardHeader>
          <CardContent>
            <ul className="flex flex-col gap-2">
              {SILVER_NODES.filter((node) => node in stageDurations).map((node) => (
                <li key={node} className="flex items-center justify-between text-sm">
                  <span>{agentLabel(node)}</span>
                  <span className="text-muted-foreground font-mono text-xs">
                    {stageDurations[node].toFixed(2)}s
                  </span>
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
      )}

      {qualityReport?.checks && qualityReport.checks.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>Relatório de qualidade</CardTitle>
          </CardHeader>
          <CardContent>
            <ul className="flex flex-col gap-2">
              {qualityReport.checks.map((check, i) => (
                <li key={i} className="flex items-start gap-2 text-sm">
                  {SEVERITY_ICON[check.severity] ?? SEVERITY_ICON.ok}
                  <span>
                    <span className="font-medium">{check.check}</span>
                    {check.column && (
                      <>
                        {" "}
                        em <span className="font-mono text-xs">{check.column}</span>
                      </>
                    )}
                    {check.null_ratio != null && (
                      <span className="text-muted-foreground">
                        {" "}
                        — razão de nulos: {(check.null_ratio * 100).toFixed(1)}%
                      </span>
                    )}
                    {check.count != null && (
                      <span className="text-muted-foreground"> — {check.count} duplicatas</span>
                    )}
                    {check.outlier_count != null && (
                      <span className="text-muted-foreground">
                        {" "}
                        — {check.outlier_count} outliers
                      </span>
                    )}
                  </span>
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
      )}

      {pipelinePlan && Object.keys(pipelinePlan).length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>Plano do pipeline</CardTitle>
          </CardHeader>
          <CardContent>
            <pre className="text-xs overflow-x-auto text-muted-foreground">
              {JSON.stringify(pipelinePlan, null, 2)}
            </pre>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
