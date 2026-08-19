"use client";

import { Check, Copy } from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/ui/button";

/** Sprint 7 — plain `<pre>` + copy button, not a syntax-highlighting library:
 * the generated code (Transformer/Analyst/Science) is short pandas/sklearn
 * snippets, not something worth a new dependency for. */
export function CodeBlock({ code, filename }: { code: string; filename?: string }) {
  const [copied, setCopied] = useState(false);

  async function handleCopy() {
    await navigator.clipboard.writeText(code);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  }

  return (
    <div className="relative border border-border/60 rounded-lg overflow-hidden bg-muted/30">
      <div className="flex items-center justify-between px-3 py-1.5 border-b border-border/60">
        <span className="text-xs text-muted-foreground font-mono">{filename ?? "code.py"}</span>
        <Button variant="ghost" size="icon-xs" onClick={handleCopy}>
          {copied ? <Check className="h-3 w-3" /> : <Copy className="h-3 w-3" />}
          <span className="sr-only">Copiar código</span>
        </Button>
      </div>
      <pre className="overflow-x-auto p-3 text-xs leading-relaxed">
        <code className="font-mono">{code}</code>
      </pre>
    </div>
  );
}
