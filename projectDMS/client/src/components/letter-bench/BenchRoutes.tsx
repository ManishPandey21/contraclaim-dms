import React, { useEffect, useRef } from "react";
import { Navigate, useNavigate, useParams, useSearchParams } from "react-router-dom";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { errorMessage, useBench, useBenchAvailability, useOpenBench } from "@/hooks/useLetterBench";

const RouteSkeleton = () => (
  <div className="space-y-3 p-2" aria-busy="true" aria-label="Loading">
    <Skeleton className="h-10 w-1/2" />
    <Skeleton className="h-64 w-full" />
  </div>
);

/**
 * Wraps the earlier input / strategy / draft screens. A letter that has a Bench
 * goes to the Bench; a letter started in the earlier screens keeps them, which
 * is also the rollback path. `?classic=1` always shows the earlier screen.
 */
export const LegacyOrBench: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const { id } = useParams<{ id: string }>();
  const [params] = useSearchParams();
  const classic = params.get("classic") === "1";
  const availability = useBenchAvailability();
  const bench = useBench(id, { enabled: availability.enabled && !classic });

  if (classic || (!availability.isLoading && !availability.enabled)) return <>{children}</>;
  if (availability.isLoading || bench.isLoading) return <RouteSkeleton />;
  if (bench.data) return <Navigate to={`/letters/${id}/bench`} replace />;
  return <>{children}</>;
};

/** `/letters/bench/new?documentId=…` — opens (or reopens) the Bench for a document, no form. */
export const BenchOpenRoute: React.FC = () => {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const open = useOpenBench();
  const started = useRef(false);
  const documentId = params.get("documentId");
  const variationId = params.get("variationId");

  useEffect(() => {
    if (started.current || (!documentId && !variationId)) return;
    started.current = true;
    open.mutate(
      documentId
        ? { incoming_document_id: documentId }
        : { trigger_type: "variation", trigger_id: variationId as string },
      { onSuccess: (result) => navigate(`/letters/${result.letter_id}/bench`, { replace: true }) }
    );
  }, [documentId, navigate, open, variationId]);

  if (!documentId && !variationId) return <Navigate to="/letters" replace />;
  if (open.isError) {
    return (
      <div role="alert" className="mx-auto max-w-xl space-y-3 p-8 text-center">
        <h1 className="text-lg font-semibold">The reply could not be started</h1>
        <p className="text-sm text-muted-foreground">
          {errorMessage(open.error, "Check that you have access to this record, then try again.")}
        </p>
        <Button variant="outline" onClick={() => navigate("/letters")}>
          Back to letters
        </Button>
      </div>
    );
  }
  return <RouteSkeleton />;
};

/**
 * Shown on the review and approval pages for a letter that was drafted on the
 * Bench: one link to the decided positions, sources and the reply tied to them.
 * Renders nothing when the Bench is off or the letter has none.
 */
export const BenchReviewLink: React.FC<{ letterId?: string }> = ({ letterId }) => {
  const availability = useBenchAvailability();
  const bench = useBench(letterId, { enabled: availability.enabled });
  const navigate = useNavigate();
  if (!letterId || !bench.data) return null;
  const { assertions } = bench.data.board;
  return (
    <aside
      aria-label="Positions behind this draft"
      className="flex flex-wrap items-center gap-3 rounded-lg border bg-card p-4"
    >
      <div className="min-w-0 flex-1">
        <p className="text-sm font-semibold">Positions behind this draft</p>
        <p className="text-sm text-muted-foreground">
          {assertions.length} assertion{assertions.length === 1 ? "" : "s"} from the incoming letter, each with
          its decided position, clause check and the reply paragraph that answers it.
        </p>
      </div>
      <Button variant="outline" onClick={() => navigate(`/letters/${letterId}/bench`)}>
        Open positions (read-only)
      </Button>
    </aside>
  );
};
