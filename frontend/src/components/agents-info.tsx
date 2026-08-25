"use client";

import { Info } from "lucide-react";
import { useTranslations } from "next-intl";
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

type PyramidText = { label: string; description: string };
type RosterText = { description: string };

/**
 * Sprint 7 — replaces the always-visible Streamlit sidebar (pyramid +
 * "how it works" + agent list + model caption) with an on-demand Sheet, so
 * the 3 pages stay uncluttered by default but the same explanation is one
 * click away. `modelName` comes from `GET /config` (server-fetched in
 * `layout.tsx`, passed down — this component stays a Client Component only
 * for the Sheet's open/close interaction, not for data fetching).
 *
 * Sprint 25 (ADR-036): `PYRAMID`/`AGENTS` (`lib/agents.ts`) now hold only
 * structural data (id/emoji/name) — translated label/description text comes
 * from `messages/{locale}.json`'s `agents` namespace (`pyramid`/`roster`
 * arrays, read via `t.raw`, zipped back to `PYRAMID`/`AGENTS` by index), the
 * same pattern the landing page uses for the same data.
 */
export function AgentsInfo({ modelName }: { modelName: string | null }) {
  const t = useTranslations("agentsInfo");
  const tAgents = useTranslations("agents");
  const pyramidText = tAgents.raw("pyramid") as PyramidText[];
  const rosterText = tAgents.raw("roster") as RosterText[];

  return (
    <Sheet>
      <SheetTrigger
        render={
          <Button variant="ghost" size="icon-sm">
            <Info aria-hidden="true" />
            <span className="sr-only">{t("trigger")}</span>
          </Button>
        }
      />
      <SheetContent className="w-full sm:max-w-md overflow-y-auto">
        <SheetHeader>
          <SheetTitle>{t("title")}</SheetTitle>
          <SheetDescription>{t("description")}</SheetDescription>
        </SheetHeader>

        <div className="flex flex-col gap-6 px-4 pb-4">
          {modelName && (
            <div className="flex items-center justify-between text-sm">
              <span className="text-muted-foreground">{t("modelInUse")}</span>
              <Badge variant="outline" className="font-mono">
                {modelName}
              </Badge>
            </div>
          )}

          <div>
            <h3 className="text-sm font-medium mb-2">{t("pyramidHeading")}</h3>
            <ul className="flex flex-col gap-2">
              {PYRAMID.map((tier, i) => (
                <li key={tier.id} className="flex gap-2 text-sm">
                  <span>{tier.emoji}</span>
                  <div>
                    <span className="font-medium">{pyramidText[i].label}</span>
                    <span className="text-muted-foreground"> — {pyramidText[i].description}</span>
                  </div>
                </li>
              ))}
            </ul>
          </div>

          <Separator />

          <div>
            <h3 className="text-sm font-medium mb-2">{t("rosterHeading")}</h3>
            <ul className="flex flex-col gap-3">
              {AGENTS.map((agent, i) => (
                <li key={agent.id} className="flex gap-2 text-sm">
                  <span>{agent.emoji}</span>
                  <div>
                    <span className="font-medium">{agent.name}</span>
                    <p className="text-muted-foreground">{rosterText[i].description}</p>
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
