import React, { useCallback, useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { CheckCircle2, Clock, Loader2, XCircle } from "lucide-react";
import { listSubscriptions, type Subscription } from "@/services/plan-settings-api";
import { clearPendingCheckout, peekPendingCheckout } from "@/services/billing-api";
import {
  shouldKeepPolling,
  subscriptionDisplayState,
  type CheckoutDisplayState,
} from "@/lib/billing-helpers";

// Webhook-driven activation can lag the payment by a minute or more; poll for
// up to ~2 minutes before falling back to the "still processing" guidance.
const MAX_POLLS = 40;
const POLL_MS = 3000;

const BillingReturnPage: React.FC = () => {
  const [params] = useSearchParams();
  const subscriptionId =
    params.get("subscription_id") || params.get("subscription") || peekPendingCheckout() || "";
  const [sub, setSub] = useState<Subscription | null>(null);
  const [state, setState] = useState<CheckoutDisplayState>("unknown");
  const [polls, setPolls] = useState(0);
  const [done, setDone] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const poll = useCallback(async () => {
    try {
      const subs = await listSubscriptions();
      const match =
        (subscriptionId && subs.find((s) => s.id === subscriptionId)) ||
        // newest non-project subscription as a fallback
        subs
          .filter((s) => !s.project_id)
          .sort((a, b) => String(b.created_at || "").localeCompare(String(a.created_at || "")))[0] ||
        subs[0] ||
        null;
      setSub(match || null);
      const ds = subscriptionDisplayState(match?.status);
      setState(ds.state);
      return ds.state;
    } catch {
      return "unknown" as CheckoutDisplayState;
    }
  }, [subscriptionId]);

  useEffect(() => {
    // This page IS the pending checkout's destination; clear the marker so the
    // subscription page stops re-routing here on subsequent visits.
    clearPendingCheckout();
    let active = true;
    const run = async () => {
      const ds = await poll();
      if (!active) return;
      setPolls((p) => p + 1);
      if (!shouldKeepPolling(ds) || polls + 1 >= MAX_POLLS) {
        setDone(true);
        return;
      }
      timer.current = setTimeout(run, POLL_MS);
    };
    void run();
    return () => {
      active = false;
      if (timer.current) clearTimeout(timer.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const polling = !done && shouldKeepPolling(state);

  return (
    <div className="container mx-auto max-w-xl space-y-6 p-6">
      <h1 className="text-2xl font-bold">Payment Status</h1>
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            {state === "active" ? (
              <CheckCircle2 className="h-6 w-6 text-green-600" />
            ) : state === "failed" ? (
              <XCircle className="h-6 w-6 text-red-600" />
            ) : (
              <Clock className="h-6 w-6 text-amber-500" />
            )}
            Payment status
          </CardTitle>
          <CardDescription>
            Your subscription is activated by our payment provider, not by this page —
            we&apos;re confirming the latest status with the server.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {polling ? (
            <div className="flex items-center gap-2 text-muted-foreground">
              <Loader2 className="h-5 w-5 animate-spin" />
              Confirming your payment…
            </div>
          ) : state === "active" ? (
            <div className="space-y-2">
              <Badge className="bg-green-600">Subscription active</Badge>
              <p className="text-sm text-muted-foreground">
                Your plan {sub?.plan_code ? <strong>{sub.plan_code}</strong> : null} is now active.
              </p>
            </div>
          ) : state === "failed" ? (
            <div className="space-y-2">
              <Badge variant="destructive">Payment not completed</Badge>
              <p className="text-sm text-muted-foreground">
                We couldn&apos;t confirm a successful payment. You can retry from the subscription page.
              </p>
            </div>
          ) : (
            <div className="space-y-2">
              <Badge variant="outline">Still processing</Badge>
              <p className="text-sm text-muted-foreground">
                Payment confirmation can take a moment. Refresh the subscription page shortly to see
                the updated status.
              </p>
            </div>
          )}

          <div className="flex gap-2">
            <Button asChild>
              <Link to="/subscription-management">Back to subscription</Link>
            </Button>
            {(state === "failed" || (done && state !== "active")) && (
              <Button asChild variant="outline">
                <Link to="/subscription-management">Retry payment</Link>
              </Button>
            )}
          </div>
        </CardContent>
      </Card>
    </div>
  );
};

export default BillingReturnPage;
