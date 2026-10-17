// A detail page shares its parent's segment (/history and /history/[runId]), so the app-wide
// boundary in (app)/loading.tsx does not cover a move from the list to a detail: without this
// file the list stayed on screen until the detail's data arrived (11 s for a run, 2026-10-16).
export { default } from "@/app/(app)/loading";
