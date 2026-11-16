import React from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Radar } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { claimBenchKeys, useClaimBenchAvailability, useClaimSignals } from "@/hooks/useClaimBench";
import { errorMessage } from "@/hooks/useLetterBench";
import { NOTICE_BADGE_VARIANT, describeNotice } from "@/lib/claims-helpers";
import { formatDateIN } from "@/lib/format-in";
import { raisePotentialClaim } from "@/services/claim-bench-api";
import type { ClaimSignal } from "@/types/claimBench";

/**
 * Records that indicate a claim nobody has raised yet. Each already has a
 * notice clock running. Raising one creates a potential claim; nothing is
 * created until a person asks for it. Renders nothing when there are none.
 */
const ClaimSignalsStrip: React.FC<{ onRaised: () => void }> = ({ onRaised }) => {
  const availability = useClaimBenchAvailability();
  const signals = useClaimSignals(Boolean(availability.data?.enabled));
  const client = useQueryClient();
  const navigate = useNavigate();
  const raise = useMutation({
    mutationFn: (signal: ClaimSignal) => raisePotentialClaim(signal.source_type, signal.source_id),
    onSuccess: ({ claim, created }) => {
      toast.success(created ? "Potential claim raised" : "A claim had already been raised from this record");
      void client.invalidateQueries({ queryKey: claimBenchKeys.signalsAll });
      onRaised();
      navigate(`/claims/${claim.id}`);
    },
    onError: (error) => toast.error(errorMessage(error, "The potential claim could not be raised")),
  });

  if (!signals.data || signals.data.length === 0) return null;
  return (
    <section aria-labelledby="claim-signals-heading" className="mb-6 rounded-lg border border-violet-300 bg-violet-50 p-4">
      <h2 id="claim-signals-heading" className="flex items-center gap-2 text-sm font-semibold text-violet-950">
        <Radar className="h-4 w-4" aria-hidden="true" />
        {signals.data.length} record{signals.data.length === 1 ? "" : "s"} may be a claim that has not been raised
      </h2>
      <p className="text-xs text-violet-900">
        The notice period for each is already running. Raise a potential claim to start tracking it.
      </p>
      <ul className="mt-3 space-y-2">
        {signals.data.map((signal) => {
          const notice = describeNotice(signal.notice_assessment);
          return (
            <li
              key={`${signal.source_type}-${signal.source_id}`}
              className="flex flex-wrap items-center justify-between gap-3 rounded-md border bg-white p-3"
            >
              <div className="min-w-0">
                <p className="text-sm font-medium">{signal.title}</p>
                <p className="text-xs text-muted-foreground">
                  {signal.label}
                  {signal.event_date && ` · ${formatDateIN(signal.event_date)}`} · {signal.reason}
                </p>
              </div>
              <div className="flex items-center gap-2">
                {notice && (
                  <span title={notice.sentence}>
                    <Badge variant={NOTICE_BADGE_VARIANT[notice.tone]} className="whitespace-nowrap">
                      {notice.label}
                    </Badge>
                  </span>
                )}
                <Button size="sm" variant="outline" disabled={raise.isPending} onClick={() => raise.mutate(signal)}>
                  Raise potential claim
                </Button>
              </div>
            </li>
          );
        })}
      </ul>
    </section>
  );
};

export default ClaimSignalsStrip;
