import { useTranslations } from "next-intl";
import { Skeleton } from "@/components/ui/skeleton";

/**
 * Loading boundary for every page of the signed-in app (2026-10-16). Without it, Next.js does
 * not prefetch a dynamic route, so a click on a link left the old page on screen until the
 * server had fetched the new page's data from the API (2 to 12 s from Railway in Amsterdam).
 * With it, links in view prefetch the layout up to this boundary, the navigation switches at
 * once to this skeleton, and the page streams in when its data arrives.
 */
export default function AppLoading() {
  const t = useTranslations("common");
  return (
    <main
      className="mx-auto flex w-full max-w-5xl flex-1 flex-col gap-8 px-4 py-10 sm:px-6 sm:py-14"
      aria-busy="true"
    >
      <span className="sr-only" role="status">
        {t("loading")}
      </span>
      <div className="flex flex-col gap-3 border-b border-border pb-8">
        <Skeleton className="h-3 w-24" />
        <Skeleton className="h-10 w-80 max-w-full" />
        <Skeleton className="h-4 w-[28rem] max-w-full" />
      </div>
      <div className="flex flex-col gap-3">
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-14 w-full" />
        <Skeleton className="h-14 w-full" />
        <Skeleton className="h-14 w-5/6" />
      </div>
    </main>
  );
}
