/**
 * Splits a failure message of a run into the line a user reads and the technical
 * remainder.
 *
 * The backend stores the full text of the failure in `state.error`: for a failed
 * transformation that is one line saying what failed, followed by the Python traceback
 * with internal paths. Both pages that show a failed run used to print all of it, or
 * nothing (functional verification of 2026-10-12, checklist row 3.8).
 */
export type SplitError = { summary: string; details: string | null };

export function splitErrorMessage(error: string): SplitError {
  const text = error.trim();
  const cut = text.search(/\r?\n/);
  if (cut === -1) return { summary: text, details: null };
  const details = text.slice(cut).trim();
  return { summary: text.slice(0, cut).trim(), details: details.length > 0 ? details : null };
}
