"use client";

import { Info } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";
import { AGENTS, PYRAMID } from "@/lib/agents";

/**
 * Sprint 7 — replaces the always-visible Streamlit sidebar (pyramid +
 * "how it works" + agent list + model caption) with an on-demand Sheet, so
 * the 3 pages stay uncluttered by default but the same explanation is one
 * click away. `modelName` comes from `GET /config` (server-fetched in
 * `layout.tsx`, passed down — this component stays a Client Component only
 * for the Sheet's open/close interaction, not for data fetching).
 */
export function AgentsInfo({ modelName }: { modelName: string | null }) {
  return (
    <Sheet>
      <SheetTrigger
        render={
          <Button variant="ghost" size="icon-sm">
            <Info />
            <span className="sr-only">Como funciona</span>
          </Button>
        }
      />
      <SheetContent className="w-full sm:max-w-md overflow-y-auto">
        <SheetHeader>
          <SheetTitle>Como funciona</SheetTitle>
          <SheetDescription>
            Dado bruto → ETL auditável → insights descritivos → modelos preditivos →
            recomendações.
          </SheetDescription>
        </SheetHeader>

        <div className="flex flex-col gap-6 px-4 pb-4">
          {modelName && (
            <div className="flex items-center justify-between text-sm">
              <span className="text-muted-foreground">Modelo em uso</span>
              <Badge variant="outline" className="font-mono">
                {modelName}
              </Badge>
            </div>
          )}

          <div>
            <h3 className="text-sm font-medium mb-2">Pirâmide analítica</h3>
            <ul className="flex flex-col gap-2">
              {PYRAMID.map((tier) => (
                <li key={tier.label} className="flex gap-2 text-sm">
                  <span>{tier.emoji}</span>
                  <div>
                    <span className="font-medium">{tier.label}</span>
                    <span className="text-muted-foreground"> — {tier.description}</span>
                  </div>
                </li>
              ))}
            </ul>
          </div>

          <Separator />

          <div>
            <h3 className="text-sm font-medium mb-2">Agentes</h3>
            <ul className="flex flex-col gap-3">
              {AGENTS.map((agent) => (
                <li key={agent.name} className="flex gap-2 text-sm">
                  <span>{agent.emoji}</span>
                  <div>
                    <span className="font-medium">{agent.name}</span>
                    <p className="text-muted-foreground">{agent.description}</p>
                  </div>
                </li>
              ))}
            </ul>
          </div>
        </div>
      </SheetContent>
    </Sheet>
  );
}
