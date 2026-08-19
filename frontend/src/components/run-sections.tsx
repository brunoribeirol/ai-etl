"use client";

import type { ReactNode } from "react";
import { motion } from "motion/react";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

/**
 * Sprint 7 redesign — groups the run-detail page's Silver/Gold/Science/Advisor
 * sections into tabs instead of one long vertical stack, and gives the active
 * panel's children a staggered fade/slide-in (Motion) so results don't just
 * pop in — matches the "animate what communicates state" call from the
 * research phase (see PR description). Purely presentational: all content is
 * still server-rendered and passed down as `ReactNode` props, no client-side
 * data fetching.
 */
export function RunSections({
  silver,
  pipeline,
  gold,
  science,
  advisor,
  code,
}: {
  silver: ReactNode;
  pipeline: ReactNode;
  gold: ReactNode[];
  science: ReactNode[];
  advisor: ReactNode;
  code: ReactNode;
}) {
  const tabs = [
    silver && { value: "silver", label: "Silver", content: silver },
    pipeline && { value: "pipeline", label: "Pipeline", content: pipeline },
    gold.length > 0 && { value: "gold", label: `Gold (${gold.length})`, content: gold },
    science.length > 0 && {
      value: "science",
      label: `Science (${science.length})`,
      content: science,
    },
    advisor && { value: "advisor", label: "Advisor", content: advisor },
    code && { value: "code", label: "Código", content: code },
  ].filter(Boolean) as { value: string; label: string; content: ReactNode | ReactNode[] }[];

  if (tabs.length === 0) {
    return null;
  }

  return (
    <Tabs defaultValue={tabs[0].value}>
      <TabsList>
        {tabs.map((tab) => (
          <TabsTrigger key={tab.value} value={tab.value}>
            {tab.label}
          </TabsTrigger>
        ))}
      </TabsList>
      {tabs.map((tab) => (
        <TabsContent key={tab.value} value={tab.value}>
          <div className="flex flex-col gap-6 mt-4">
            {(Array.isArray(tab.content) ? tab.content : [tab.content]).map((child, i) => (
              <motion.div
                key={i}
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.25, delay: i * 0.05 }}
              >
                {child}
              </motion.div>
            ))}
          </div>
        </TabsContent>
      ))}
    </Tabs>
  );
}
