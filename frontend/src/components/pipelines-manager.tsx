"use client";

import { useAuth } from "@clerk/nextjs";
import { PauseCircle, PlayCircle, Plus } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { StatusBadge } from "@/components/status-badge";
import { SCHEDULABLE_SOURCE_TYPES, type SavedPipeline } from "@/lib/types";

/**
 * Sprint 13 (ADR-016) — minimal CRUD UI for saved (recurring) pipelines,
 * distinct from the "Executar" page's one-off `POST /runs`. Client
 * Component (needs `useAuth().getToken()` fresh per request, same pattern
 * `executar-form.tsx` already established) — no server-side prefetch, since
 * every action here (create/pause/resume/edit) needs a token anyway.
 *
 * Deliberately minimal per the sprint's own scope ("não precisa ser bonita,
 * precisa funcionar"): a plain list + inline edit, no separate route per
 * pipeline, native `<select>` for source_type rather than pulling in a new
 * shadcn Select component for one dropdown.
 */
export function PipelinesManager() {
  const { getToken } = useAuth();
  const apiUrl = process.env.NEXT_PUBLIC_API_URL;

  const [pipelines, setPipelines] = useState<SavedPipeline[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);

  const [name, setName] = useState("");
  const [sourceType, setSourceType] = useState<string>(SCHEDULABLE_SOURCE_TYPES[0]);
  const [spec, setSpec] = useState("");
  const [businessQuestion, setBusinessQuestion] = useState("");
  const [cronSchedule, setCronSchedule] = useState("0 3 * * *");
  const [submitting, setSubmitting] = useState(false);

  const authedFetch = useCallback(
    async (path: string, init?: RequestInit) => {
      if (!apiUrl) throw new Error("NEXT_PUBLIC_API_URL is not configured.");
      const token = await getToken();
      const response = await fetch(`${apiUrl}${path}`, {
        ...init,
        headers: {
          Authorization: `Bearer ${token}`,
          ...(init?.body ? { "Content-Type": "application/json" } : {}),
          ...init?.headers,
        },
      });
      if (!response.ok) {
        const body = await response.json().catch(() => null);
        throw new Error(body?.detail ?? `HTTP ${response.status}`);
      }
      return response.json();
    },
    [apiUrl, getToken],
  );

  const loadPipelines = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await authedFetch("/pipelines");
      setPipelines(data as SavedPipeline[]);
    } catch (err) {
      setError(String(err));
    } finally {
      setLoading(false);
    }
  }, [authedFetch]);

  useEffect(() => {
    loadPipelines();
  }, [loadPipelines]);

  function resetForm() {
    setEditingId(null);
    setName("");
    setSourceType(SCHEDULABLE_SOURCE_TYPES[0]);
    setSpec("");
    setBusinessQuestion("");
    setCronSchedule("0 3 * * *");
  }

  function startEdit(pipeline: SavedPipeline) {
    setEditingId(pipeline.id);
    setName(pipeline.name);
    setSourceType(pipeline.source_type);
    setSpec(pipeline.spec);
    setBusinessQuestion(pipeline.business_question);
    setCronSchedule(pipeline.cron_schedule);
  }

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setSubmitting(true);
    try {
      if (editingId) {
        await authedFetch(`/pipelines/${editingId}`, {
          method: "PATCH",
          body: JSON.stringify({
            name,
            source_type: sourceType,
            spec,
            business_question: businessQuestion,
            cron_schedule: cronSchedule,
          }),
        });
        toast.success("Pipeline atualizado.");
      } else {
        await authedFetch("/pipelines", {
          method: "POST",
          body: JSON.stringify({
            name,
            source_type: sourceType,
            spec,
            business_question: businessQuestion,
            cron_schedule: cronSchedule,
          }),
        });
        toast.success("Pipeline agendado.");
      }
      resetForm();
      await loadPipelines();
    } catch (err) {
      toast.error(String(err));
    } finally {
      setSubmitting(false);
    }
  }

  async function toggleActive(pipeline: SavedPipeline) {
    try {
      await authedFetch(`/pipelines/${pipeline.id}`, {
        method: "PATCH",
        body: JSON.stringify({ is_active: !pipeline.is_active }),
      });
      toast.success(pipeline.is_active ? "Pipeline pausado." : "Pipeline retomado.");
      await loadPipelines();
    } catch (err) {
      toast.error(String(err));
    }
  }

  return (
    <div className="flex flex-col gap-6 max-w-2xl w-full">
      <Card>
        <CardContent>
          <form onSubmit={handleSubmit} className="flex flex-col gap-5">
            <h2 className="text-sm font-medium">
              {editingId ? "Editar pipeline" : "Novo pipeline agendado"}
            </h2>

            <div className="flex flex-col gap-2">
              <Label htmlFor="name">Nome</Label>
              <Input
                id="name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                disabled={submitting}
                required
                placeholder="Sincronização noturna de pedidos"
              />
            </div>

            <div className="flex flex-col gap-2">
              <Label htmlFor="sourceType">
                Tipo de fonte{" "}
                <span className="text-muted-foreground font-normal">
                  (apenas fontes &ldquo;vivas&rdquo; podem ser agendadas — ADR-016)
                </span>
              </Label>
              <select
                id="sourceType"
                value={sourceType}
                onChange={(e) => setSourceType(e.target.value)}
                disabled={submitting}
                className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-xs outline-none disabled:opacity-50"
              >
                {SCHEDULABLE_SOURCE_TYPES.map((type) => (
                  <option key={type} value={type}>
                    {type}
                  </option>
                ))}
              </select>
            </div>

            <div className="flex flex-col gap-2">
              <Label htmlFor="spec">Spec (linguagem natural)</Label>
              <Textarea
                id="spec"
                value={spec}
                onChange={(e) => setSpec(e.target.value)}
                disabled={submitting}
                rows={3}
                required
                placeholder="Read schema.orders from postgres, filter status=active..."
                className="font-mono text-sm"
              />
            </div>

            <div className="flex flex-col gap-2">
              <Label htmlFor="businessQuestion">Pergunta de negócio (opcional)</Label>
              <Textarea
                id="businessQuestion"
                value={businessQuestion}
                onChange={(e) => setBusinessQuestion(e.target.value)}
                disabled={submitting}
                rows={2}
                placeholder="Quais produtos vendem mais?"
              />
            </div>

            <div className="flex flex-col gap-2">
              <Label htmlFor="cronSchedule">
                Agendamento (cron){" "}
                <span className="text-muted-foreground font-normal">
                  ex: &ldquo;0 3 * * *&rdquo; = todo dia às 3h
                </span>
              </Label>
              <Input
                id="cronSchedule"
                value={cronSchedule}
                onChange={(e) => setCronSchedule(e.target.value)}
                disabled={submitting}
                required
                className="font-mono text-sm"
                placeholder="0 3 * * *"
              />
            </div>

            <div className="flex gap-2">
              <Button type="submit" disabled={submitting} className="flex-1">
                {editingId ? "Salvar alterações" : (
                  <>
                    <Plus className="h-4 w-4" /> Agendar pipeline
                  </>
                )}
              </Button>
              {editingId && (
                <Button type="button" variant="outline" onClick={resetForm} disabled={submitting}>
                  Cancelar
                </Button>
              )}
            </div>
          </form>
        </CardContent>
      </Card>

      <div className="flex flex-col gap-3">
        <h2 className="text-sm font-medium text-muted-foreground">Pipelines salvos</h2>

        {error && (
          <p className="text-destructive text-sm" role="alert">
            {error}
          </p>
        )}
        {!loading && !error && pipelines.length === 0 && (
          <p className="text-sm text-muted-foreground">Nenhum pipeline agendado ainda.</p>
        )}

        {pipelines.map((pipeline) => (
          <Card key={pipeline.id}>
            <CardContent className="flex flex-col gap-2 text-sm">
              <div className="flex justify-between items-start gap-3">
                <div className="flex flex-col gap-1">
                  <span className="font-medium">{pipeline.name}</span>
                  <span className="text-xs text-muted-foreground font-mono">
                    {pipeline.source_type} · {pipeline.cron_schedule}
                  </span>
                </div>
                <StatusBadge status={pipeline.is_active ? "running" : "pending"} />
              </div>
              <p className="text-xs text-muted-foreground truncate">{pipeline.spec}</p>
              <div className="flex justify-between items-center text-xs text-muted-foreground">
                <span>Próxima execução: {new Date(pipeline.next_run_at).toLocaleString()}</span>
                {pipeline.last_run_at && (
                  <span>Última: {new Date(pipeline.last_run_at).toLocaleString()}</span>
                )}
              </div>
              <div className="flex gap-2 pt-1">
                <Button size="sm" variant="outline" onClick={() => startEdit(pipeline)}>
                  Editar
                </Button>
                <Button size="sm" variant="outline" onClick={() => toggleActive(pipeline)}>
                  {pipeline.is_active ? (
                    <>
                      <PauseCircle className="h-4 w-4" /> Pausar
                    </>
                  ) : (
                    <>
                      <PlayCircle className="h-4 w-4" /> Retomar
                    </>
                  )}
                </Button>
              </div>
            </CardContent>
          </Card>
        ))}
      </div>
    </div>
  );
}
