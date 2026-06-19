import React, { useEffect, useMemo, useState, useCallback } from "react";
import {
  ArrowUpRight,
  ArrowDownRight,
  Calendar,
  CheckCircle2,
  Clock,
  CreditCard,
  Crown,
  History,
  Package,
  Pause,
  Play,
  Plus,
  RefreshCw,
  Settings2,
  Shield,
  Sparkles,
  Trash2,
  X,
  XCircle,
  Zap,
} from "lucide-react";
import { toast } from "sonner";
import useRBAC from "@/hooks/useRBAC";
import { useStepUp } from "@/hooks/useStepUp";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import { Textarea } from "@/components/ui/textarea";
import {
  getPlanCatalog,
  listSubscriptions,
  getSubscriptionHistory,
  getInvoicePreview,
  upgradeSubscription,
  downgradeSubscription,
  cancelSubscription,
  reactivateSubscription,
  changeBillingPeriod,
  addSubscriptionAddon,
  removeSubscriptionAddon,
  convertTrial,
  type PlanCatalogResponse,
  type PlanSettingsPlan,
  type PlanAddOn,
  type Subscription,
  type SubscriptionHistoryEntry,
  type InvoicePreview,
  type BillingPeriod,
} from "@/services/plan-settings-api";
import { startSubscriptionCheckout, redirectToCheckout } from "@/services/billing-api";
import { getCurrentUserProfile } from "@/services/session-api";

/* ------------------------------------------------------------------ */
/* Helpers                                                            */
/* ------------------------------------------------------------------ */

const formatPrice = (minor: number, currency = "INR") => {
  const major = minor / 100;
  return new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency,
    minimumFractionDigits: 0,
  }).format(major);
};

const periodLabel: Record<string, string> = {
  monthly: "Monthly",
  quarterly: "Quarterly",
  semi_annual: "Semi-Annual",
  annual: "Annual",
};

const statusColor: Record<string, string> = {
  active: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-400",
  trial: "bg-amber-500/15 text-amber-700 dark:text-amber-400",
  pilot: "bg-blue-500/15 text-blue-700 dark:text-blue-400",
  cancelled: "bg-red-500/15 text-red-700 dark:text-red-400",
  past_due: "bg-orange-500/15 text-orange-700 dark:text-orange-400",
  paused: "bg-gray-500/15 text-gray-700 dark:text-gray-400",
};

const changeTypeLabel: Record<string, string> = {
  created: "Subscription Created",
  upgrade: "Plan Upgraded",
  downgrade: "Plan Downgraded",
  addon_add: "Add-On Added",
  addon_remove: "Add-On Removed",
  period_change: "Billing Period Changed",
  renewal: "Subscription Renewed",
  trial_start: "Trial Started",
  trial_convert: "Trial Converted to Paid",
  trial_expire: "Trial Expired",
  cancellation: "Subscription Cancelled",
  reactivation: "Subscription Reactivated",
};

const tierIcons = [null, Zap, Sparkles, Crown, Shield];

/* ------------------------------------------------------------------ */
/* Component                                                          */
/* ------------------------------------------------------------------ */

const SubscriptionManagementPage: React.FC = () => {
  const { can, roles, loading: rbacLoading } = useRBAC();
  const { requestToken, StepUpDialog } = useStepUp();

  const [catalog, setCatalog] = useState<PlanCatalogResponse | null>(null);
  const [subscriptions, setSubscriptions] = useState<Subscription[]>([]);
  const [history, setHistory] = useState<SubscriptionHistoryEntry[]>([]);
  const [invoicePreview, setInvoicePreview] = useState<InvoicePreview | null>(null);
  const [loading, setLoading] = useState(false);
  const [actionLoading, setActionLoading] = useState(false);

  // Dialogs
  const [upgradeDialogOpen, setUpgradeDialogOpen] = useState(false);
  const [cancelDialogOpen, setCancelDialogOpen] = useState(false);
  const [historyDialogOpen, setHistoryDialogOpen] = useState(false);
  const [invoiceDialogOpen, setInvoiceDialogOpen] = useState(false);
  const [addonDialogOpen, setAddonDialogOpen] = useState(false);
  const [periodDialogOpen, setPeriodDialogOpen] = useState(false);

  // Selection state
  const [selectedSub, setSelectedSub] = useState<Subscription | null>(null);
  const [selectedPlanCode, setSelectedPlanCode] = useState("");
  const [selectedPeriod, setSelectedPeriod] = useState<BillingPeriod>("monthly");
  const [cancelReason, setCancelReason] = useState("");

  const canManage =
    roles.includes("superadmin") ||
    can("subscription.entitlement.manage") ||
    can("subscription.upgrade");

  // Data loading
  const loadData = useCallback(async () => {
    setLoading(true);
    try {
      const [catalogData, subsData] = await Promise.all([
        getPlanCatalog(),
        listSubscriptions(),
      ]);
      setCatalog(catalogData);
      setSubscriptions(subsData);
    } catch (err: any) {
      toast.error("Failed to load subscription data", {
        description: err?.response?.data?.detail || err?.message,
      });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!rbacLoading) loadData();
  }, [rbacLoading, loadData]);

  const activeSub = useMemo(
    () =>
      subscriptions.find(
        (s) =>
          ["active", "trial", "pilot"].includes(s.status) &&
          !s.project_id
      ),
    [subscriptions]
  );

  const currentPlan = useMemo(
    () =>
      catalog?.plans.find((p) => p.code === activeSub?.plan_code) || null,
    [catalog, activeSub]
  );

  const planByCode = useMemo(() => {
    const map: Record<string, PlanSettingsPlan> = {};
    for (const p of catalog?.plans || []) map[p.code] = p;
    return map;
  }, [catalog]);

  const addonByCode = useMemo(() => {
    const map: Record<string, PlanAddOn> = {};
    for (const a of catalog?.add_ons || []) map[a.code] = a;
    return map;
  }, [catalog]);

  // Actions
  const handleUpgrade = async (isDowngrade = false) => {
    if (!selectedSub || !selectedPlanCode) return;
    setActionLoading(true);
    try {
      const token = await requestToken(
        isDowngrade ? "subscription.downgrade" : "subscription.upgrade",
        isDowngrade ? "Confirm Downgrade" : "Confirm Upgrade",
        `Enter your password to ${isDowngrade ? "downgrade" : "upgrade"} this subscription.`
      );
      const fn = isDowngrade ? downgradeSubscription : upgradeSubscription;
      await fn(
        selectedSub.id,
        { new_plan_code: selectedPlanCode, billing_period: selectedPeriod },
        { stepUpToken: token }
      );
      toast.success(isDowngrade ? "Plan downgraded" : "Plan upgraded");
      setUpgradeDialogOpen(false);
      await loadData();
    } catch (err: any) {
      toast.error(`${isDowngrade ? "Downgrade" : "Upgrade"} failed`, {
        description: err?.response?.data?.detail || err?.message,
      });
    } finally {
      setActionLoading(false);
    }
  };

  // Razorpay hosted checkout. Provisions a *pending* subscription on the gateway
  // and redirects to the hosted page; the backend billing webhook (not this
  // redirect) activates the subscription and entitlements once payment captures.
  const handleCheckout = async () => {
    if (!selectedPlanCode) return;
    setActionLoading(true);
    try {
      let organizationId =
        activeSub?.organization_id || subscriptions[0]?.organization_id || "";
      if (!organizationId) {
        const me = await getCurrentUserProfile().catch(() => null);
        organizationId = (me as any)?.organization_id || "";
      }
      if (!organizationId) {
        toast.error("No organisation found for checkout");
        return;
      }
      const token = await requestToken(
        "subscription.entitlement.manage",
        "Confirm Subscription",
        "Enter your password to start a secure payment checkout."
      );
      const checkout = await startSubscriptionCheckout(
        {
          organization_id: organizationId,
          plan_code: selectedPlanCode,
          billing_period: selectedPeriod as any,
        },
        token
      );
      setUpgradeDialogOpen(false);
      if (checkout.checkout_url) {
        toast.success("Redirecting to secure checkout…");
        redirectToCheckout(checkout);
      } else {
        toast.info("Checkout created. Complete payment to activate your plan.", {
          description: `Subscription ${checkout.subscription_id} is ${checkout.status}.`,
        });
        await loadData();
      }
    } catch (err: any) {
      toast.error("Could not start checkout", {
        description: err?.response?.data?.detail || err?.message,
      });
    } finally {
      setActionLoading(false);
    }
  };

  const handleCancel = async () => {
    if (!selectedSub) return;
    setActionLoading(true);
    try {
      const token = await requestToken(
        "subscription.cancel",
        "Confirm Cancellation",
        "Enter your password to cancel this subscription."
      );
      await cancelSubscription(
        selectedSub.id,
        { reason: cancelReason, immediate: false },
        { stepUpToken: token }
      );
      toast.success("Subscription cancelled");
      setCancelDialogOpen(false);
      await loadData();
    } catch (err: any) {
      toast.error("Cancellation failed", {
        description: err?.response?.data?.detail || err?.message,
      });
    } finally {
      setActionLoading(false);
    }
  };

  const handleReactivate = async (sub: Subscription) => {
    setActionLoading(true);
    try {
      const token = await requestToken(
        "subscription.upgrade",
        "Confirm Reactivation",
        "Enter your password to reactivate this subscription."
      );
      await reactivateSubscription(sub.id, { stepUpToken: token });
      toast.success("Subscription reactivated");
      await loadData();
    } catch (err: any) {
      toast.error("Reactivation failed", {
        description: err?.response?.data?.detail || err?.message,
      });
    } finally {
      setActionLoading(false);
    }
  };

  const handleConvertTrial = async (sub: Subscription) => {
    setActionLoading(true);
    try {
      const token = await requestToken(
        "subscription.trial.manage",
        "Convert Trial",
        "Enter your password to convert this trial to a paid plan."
      );
      await convertTrial(
        sub.id,
        { billing_period: selectedPeriod },
        { stepUpToken: token }
      );
      toast.success("Trial converted to paid subscription");
      await loadData();
    } catch (err: any) {
      toast.error("Trial conversion failed", {
        description: err?.response?.data?.detail || err?.message,
      });
    } finally {
      setActionLoading(false);
    }
  };

  const handleChangePeriod = async () => {
    if (!selectedSub) return;
    setActionLoading(true);
    try {
      const token = await requestToken(
        "subscription.entitlement.manage",
        "Change Billing Period",
        "Enter your password to change the billing period."
      );
      await changeBillingPeriod(selectedSub.id, selectedPeriod, {
        stepUpToken: token,
      });
      toast.success("Billing period updated");
      setPeriodDialogOpen(false);
      await loadData();
    } catch (err: any) {
      toast.error("Period change failed", {
        description: err?.response?.data?.detail || err?.message,
      });
    } finally {
      setActionLoading(false);
    }
  };

  const handleToggleAddon = async (
    sub: Subscription,
    addonCode: string,
    isActive: boolean
  ) => {
    setActionLoading(true);
    try {
      const token = await requestToken(
        "subscription.addon.manage",
        isActive ? "Remove Add-On" : "Add Add-On",
        `Enter your password to ${isActive ? "remove" : "add"} this add-on.`
      );
      if (isActive) {
        await removeSubscriptionAddon(sub.id, addonCode, {
          stepUpToken: token,
        });
        toast.success("Add-on removed");
      } else {
        await addSubscriptionAddon(sub.id, addonCode, { stepUpToken: token });
        toast.success("Add-on added");
      }
      await loadData();
    } catch (err: any) {
      toast.error("Add-on action failed", {
        description: err?.response?.data?.detail || err?.message,
      });
    } finally {
      setActionLoading(false);
    }
  };

  const openHistory = async (sub: Subscription) => {
    setSelectedSub(sub);
    try {
      const data = await getSubscriptionHistory(sub.id);
      setHistory(data);
      setHistoryDialogOpen(true);
    } catch {
      toast.error("Failed to load subscription history");
    }
  };

  const openInvoice = async (sub: Subscription) => {
    setSelectedSub(sub);
    try {
      const data = await getInvoicePreview(sub.id);
      setInvoicePreview(data);
      setInvoiceDialogOpen(true);
    } catch {
      toast.error("Failed to load invoice preview");
    }
  };

  if (rbacLoading) return <div className="container mx-auto p-6">Loading...</div>;

  if (!canManage) {
    return (
      <div className="container mx-auto p-6">
        <Card>
          <CardHeader>
            <CardTitle>Access Denied</CardTitle>
          </CardHeader>
          <CardContent>You do not have permission to manage subscriptions.</CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div className="container mx-auto p-6 space-y-8">
      {StepUpDialog}

      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">
            Subscription Management
          </h1>
          <p className="text-sm text-muted-foreground mt-1">
            Manage your plan, billing period, add-ons, and subscription
            lifecycle.
          </p>
        </div>
        <Button
          variant="outline"
          onClick={loadData}
          disabled={loading}
          className="gap-2"
        >
          <RefreshCw
            className={`h-4 w-4 ${loading ? "animate-spin" : ""}`}
          />
          Refresh
        </Button>
      </div>

      {/* Current Plan Card */}
      {activeSub && currentPlan && (
        <Card className="border-2 border-primary/20 bg-gradient-to-br from-background to-primary/5">
          <CardHeader className="pb-3">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-3">
                {(() => {
                  const Icon = tierIcons[currentPlan.tier || 0] || Zap;
                  return (
                    <div className="h-10 w-10 rounded-lg bg-primary/10 flex items-center justify-center">
                      <Icon className="h-5 w-5 text-primary" />
                    </div>
                  );
                })()}
                <div>
                  <CardTitle className="text-xl">{currentPlan.name}</CardTitle>
                  <CardDescription>{currentPlan.description}</CardDescription>
                </div>
              </div>
              <Badge
                className={
                  statusColor[activeSub.status] || statusColor.active
                }
              >
                {activeSub.status.toUpperCase()}
              </Badge>
            </div>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              <div className="space-y-1">
                <p className="text-xs text-muted-foreground flex items-center gap-1">
                  <CreditCard className="h-3 w-3" /> Billing
                </p>
                <p className="text-sm font-medium">
                  {periodLabel[activeSub.billing_period] || activeSub.billing_period}
                </p>
              </div>
              <div className="space-y-1">
                <p className="text-xs text-muted-foreground flex items-center gap-1">
                  <Calendar className="h-3 w-3" /> Current Period
                </p>
                <p className="text-sm font-medium">
                  {activeSub.current_period_end
                    ? new Date(activeSub.current_period_end).toLocaleDateString()
                    : "—"}
                </p>
              </div>
              <div className="space-y-1">
                <p className="text-xs text-muted-foreground flex items-center gap-1">
                  <Package className="h-3 w-3" /> Add-Ons
                </p>
                <p className="text-sm font-medium">
                  {activeSub.active_add_ons?.length || 0} active
                </p>
              </div>
              <div className="space-y-1">
                <p className="text-xs text-muted-foreground flex items-center gap-1">
                  <Settings2 className="h-3 w-3" /> Auto-Renew
                </p>
                <p className="text-sm font-medium">
                  {activeSub.auto_renew ? "Enabled" : "Disabled"}
                </p>
              </div>
            </div>

            {activeSub.trial && activeSub.trial_ends_at && (
              <div className="mt-4 rounded-lg bg-amber-50 dark:bg-amber-950/30 border border-amber-200 dark:border-amber-800 p-3 flex items-center gap-2">
                <Clock className="h-4 w-4 text-amber-600" />
                <span className="text-sm text-amber-800 dark:text-amber-300">
                  Trial ends{" "}
                  {new Date(activeSub.trial_ends_at).toLocaleDateString()}
                </span>
                <Button
                  size="sm"
                  variant="outline"
                  className="ml-auto"
                  onClick={() => handleConvertTrial(activeSub)}
                  disabled={actionLoading}
                >
                  Convert to Paid
                </Button>
              </div>
            )}

            {/* Active Add-Ons */}
            {activeSub.active_add_ons?.length > 0 && (
              <div className="mt-4">
                <p className="text-xs font-medium text-muted-foreground mb-2">
                  ACTIVE ADD-ONS
                </p>
                <div className="flex flex-wrap gap-2">
                  {activeSub.active_add_ons.map((code) => {
                    const addon = addonByCode[code];
                    return (
                      <Badge
                        key={code}
                        variant="secondary"
                        className="gap-1 pr-1"
                      >
                        <Plus className="h-3 w-3" />
                        {addon?.name || code}
                        <button
                          className="ml-1 rounded-full hover:bg-destructive/20 p-0.5"
                          onClick={() =>
                            handleToggleAddon(activeSub, code, true)
                          }
                          disabled={actionLoading}
                        >
                          <X className="h-3 w-3" />
                        </button>
                      </Badge>
                    );
                  })}
                </div>
              </div>
            )}
          </CardContent>
          <CardFooter className="flex flex-wrap gap-2 pt-0">
            <Button
              variant="default"
              size="sm"
              className="gap-1"
              onClick={() => {
                setSelectedSub(activeSub);
                setSelectedPlanCode("");
                setSelectedPeriod(
                  activeSub.billing_period as BillingPeriod
                );
                setUpgradeDialogOpen(true);
              }}
            >
              <ArrowUpRight className="h-3.5 w-3.5" /> Change Plan
            </Button>
            <Button
              variant="outline"
              size="sm"
              className="gap-1"
              onClick={() => {
                setSelectedSub(activeSub);
                setSelectedPeriod(
                  activeSub.billing_period as BillingPeriod
                );
                setPeriodDialogOpen(true);
              }}
            >
              <Calendar className="h-3.5 w-3.5" /> Change Period
            </Button>
            <Button
              variant="outline"
              size="sm"
              className="gap-1"
              onClick={() => {
                setSelectedSub(activeSub);
                setAddonDialogOpen(true);
              }}
            >
              <Package className="h-3.5 w-3.5" /> Manage Add-Ons
            </Button>
            <Button
              variant="outline"
              size="sm"
              className="gap-1"
              onClick={() => openInvoice(activeSub)}
            >
              <CreditCard className="h-3.5 w-3.5" /> Invoice Preview
            </Button>
            <Button
              variant="outline"
              size="sm"
              className="gap-1"
              onClick={() => openHistory(activeSub)}
            >
              <History className="h-3.5 w-3.5" /> History
            </Button>
            {activeSub.status === "cancelled" ? (
              <Button
                variant="outline"
                size="sm"
                className="gap-1 text-emerald-600"
                onClick={() => handleReactivate(activeSub)}
                disabled={actionLoading}
              >
                <Play className="h-3.5 w-3.5" /> Reactivate
              </Button>
            ) : (
              <Button
                variant="ghost"
                size="sm"
                className="gap-1 text-destructive"
                onClick={() => {
                  setSelectedSub(activeSub);
                  setCancelReason("");
                  setCancelDialogOpen(true);
                }}
              >
                <XCircle className="h-3.5 w-3.5" /> Cancel
              </Button>
            )}
          </CardFooter>
        </Card>
      )}

      {/* Plan Comparison */}
      <div>
        <h2 className="text-lg font-semibold mb-4">Available Plans</h2>
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {(catalog?.plans || [])
            .filter((p) => p.is_active && p.code !== "no_service_override")
            .map((plan) => {
              const isCurrentPlan = activeSub?.plan_code === plan.code;
              const monthlyPrice =
                plan.pricing_tiers?.monthly || plan.base_price_minor || 0;
              const Icon = tierIcons[plan.tier || 0] || Zap;
              return (
                <Card
                  key={plan.code}
                  className={`relative transition-all hover:shadow-md ${
                    isCurrentPlan
                      ? "border-primary ring-1 ring-primary/30"
                      : ""
                  }`}
                >
                  {plan.highlight && (
                    <div className="absolute -top-3 left-1/2 -translate-x-1/2">
                      <Badge className="bg-primary text-primary-foreground text-xs">
                        Most Popular
                      </Badge>
                    </div>
                  )}
                  <CardHeader className="pb-2">
                    <div className="flex items-center gap-2">
                      <Icon className="h-5 w-5 text-primary" />
                      <CardTitle className="text-base">{plan.name}</CardTitle>
                    </div>
                    <CardDescription className="text-xs">
                      {plan.description}
                    </CardDescription>
                  </CardHeader>
                  <CardContent className="pb-2">
                    <div className="text-2xl font-bold">
                      {monthlyPrice > 0
                        ? formatPrice(monthlyPrice)
                        : "Custom"}
                      {monthlyPrice > 0 && (
                        <span className="text-xs font-normal text-muted-foreground">
                          /mo
                        </span>
                      )}
                    </div>

                    <Separator className="my-3" />

                    <ul className="space-y-1.5 text-xs text-muted-foreground">
                      {Object.entries(plan.features || {}).map(
                        ([key, val]) =>
                          val && (
                            <li key={key} className="flex items-center gap-1.5">
                              <CheckCircle2 className="h-3 w-3 text-emerald-500" />
                              {key
                                .replace(/^feature\./, "")
                                .replace(/\./g, " ")
                                .replace(/\b\w/g, (c) => c.toUpperCase())}
                            </li>
                          )
                      )}
                      {plan.max_users && (
                        <li className="flex items-center gap-1.5">
                          <CheckCircle2 className="h-3 w-3 text-emerald-500" />
                          Up to {plan.max_users} users
                        </li>
                      )}
                      {plan.max_storage_gb && (
                        <li className="flex items-center gap-1.5">
                          <CheckCircle2 className="h-3 w-3 text-emerald-500" />
                          {plan.max_storage_gb} GB storage
                        </li>
                      )}
                    </ul>
                  </CardContent>
                  <CardFooter>
                    {isCurrentPlan ? (
                      <Badge variant="outline" className="w-full justify-center">
                        Current Plan
                      </Badge>
                    ) : (
                      <Button
                        size="sm"
                        className="w-full"
                        variant={plan.highlight ? "default" : "outline"}
                        onClick={() => {
                          if (activeSub) {
                            setSelectedSub(activeSub);
                            setSelectedPlanCode(plan.code);
                            setSelectedPeriod(
                              (activeSub.billing_period as BillingPeriod) ||
                                "monthly"
                            );
                            setUpgradeDialogOpen(true);
                          }
                        }}
                      >
                        {(plan.tier || 0) > (currentPlan?.tier || 0)
                          ? "Upgrade"
                          : "Switch"}
                      </Button>
                    )}
                  </CardFooter>
                </Card>
              );
            })}
        </div>
      </div>

      {/* All Subscriptions Table */}
      {subscriptions.length > 1 && (
        <div>
          <h2 className="text-lg font-semibold mb-4">All Subscriptions</h2>
          <Card>
            <CardContent className="p-0">
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b text-muted-foreground">
                      <th className="text-left p-3 font-medium">Plan</th>
                      <th className="text-left p-3 font-medium">Status</th>
                      <th className="text-left p-3 font-medium">Period</th>
                      <th className="text-left p-3 font-medium">Scope</th>
                      <th className="text-left p-3 font-medium">Add-Ons</th>
                      <th className="text-right p-3 font-medium">Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {subscriptions.map((sub) => (
                      <tr key={sub.id} className="border-b hover:bg-muted/30">
                        <td className="p-3 font-medium">
                          {planByCode[sub.plan_code]?.name || sub.plan_code}
                        </td>
                        <td className="p-3">
                          <Badge
                            className={
                              statusColor[sub.status] || statusColor.active
                            }
                          >
                            {sub.status}
                          </Badge>
                        </td>
                        <td className="p-3">
                          {periodLabel[sub.billing_period] ||
                            sub.billing_period}
                        </td>
                        <td className="p-3 text-muted-foreground">
                          {sub.project_id ? "Project" : "Organization"}
                        </td>
                        <td className="p-3">
                          {sub.active_add_ons?.length || 0}
                        </td>
                        <td className="p-3 text-right">
                          <div className="flex justify-end gap-1">
                            <Button
                              variant="ghost"
                              size="sm"
                              onClick={() => openHistory(sub)}
                            >
                              <History className="h-3.5 w-3.5" />
                            </Button>
                            <Button
                              variant="ghost"
                              size="sm"
                              onClick={() => openInvoice(sub)}
                            >
                              <CreditCard className="h-3.5 w-3.5" />
                            </Button>
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </CardContent>
          </Card>
        </div>
      )}

      {/* ---- Dialogs ---- */}

      {/* Upgrade/Downgrade Dialog */}
      <Dialog open={upgradeDialogOpen} onOpenChange={setUpgradeDialogOpen}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>Change Plan</DialogTitle>
            <DialogDescription>
              Select a new plan and billing period for your subscription.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-4">
            <div className="space-y-2">
              <label className="text-sm font-medium">New Plan</label>
              <Select
                value={selectedPlanCode}
                onValueChange={setSelectedPlanCode}
              >
                <SelectTrigger>
                  <SelectValue placeholder="Select a plan" />
                </SelectTrigger>
                <SelectContent>
                  {(catalog?.plans || [])
                    .filter(
                      (p) =>
                        p.is_active &&
                        p.code !== "no_service_override" &&
                        p.code !== activeSub?.plan_code
                    )
                    .map((p) => (
                      <SelectItem key={p.code} value={p.code}>
                        {p.name}
                        {p.pricing_tiers?.monthly
                          ? ` — ${formatPrice(p.pricing_tiers.monthly)}/mo`
                          : ""}
                      </SelectItem>
                    ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium">Billing Period</label>
              <Select
                value={selectedPeriod}
                onValueChange={(v) => setSelectedPeriod(v as BillingPeriod)}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="monthly">Monthly</SelectItem>
                  <SelectItem value="quarterly">Quarterly</SelectItem>
                  <SelectItem value="annual">Annual</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>
          <DialogFooter className="flex-col gap-2 sm:flex-row">
            <Button variant="outline" onClick={() => setUpgradeDialogOpen(false)}>
              Cancel
            </Button>
            <Button
              variant="outline"
              disabled={!selectedPlanCode || actionLoading}
              onClick={handleCheckout}
            >
              {actionLoading ? "Processing..." : "Pay with Razorpay"}
            </Button>
            <Button
              disabled={!selectedPlanCode || actionLoading}
              onClick={() => {
                const newPlan = planByCode[selectedPlanCode];
                const isDowngrade =
                  (newPlan?.tier || 0) < (currentPlan?.tier || 0);
                handleUpgrade(isDowngrade);
              }}
            >
              {actionLoading
                ? "Processing..."
                : (planByCode[selectedPlanCode]?.tier || 0) >
                  (currentPlan?.tier || 0)
                ? "Upgrade"
                : "Downgrade"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Cancel Dialog */}
      <Dialog open={cancelDialogOpen} onOpenChange={setCancelDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Cancel Subscription</DialogTitle>
            <DialogDescription>
              Your subscription will remain active until the end of the current
              billing period.
            </DialogDescription>
          </DialogHeader>
          <div className="py-4">
            <label className="text-sm font-medium">
              Reason (optional)
            </label>
            <Textarea
              value={cancelReason}
              onChange={(e) => setCancelReason(e.target.value)}
              placeholder="Tell us why you're cancelling..."
              className="mt-2"
            />
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setCancelDialogOpen(false)}>
              Keep Subscription
            </Button>
            <Button
              variant="destructive"
              disabled={actionLoading}
              onClick={handleCancel}
            >
              {actionLoading ? "Cancelling..." : "Cancel Subscription"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Billing Period Dialog */}
      <Dialog open={periodDialogOpen} onOpenChange={setPeriodDialogOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Change Billing Period</DialogTitle>
            <DialogDescription>
              The new billing period will take effect at your next renewal.
            </DialogDescription>
          </DialogHeader>
          <div className="py-4">
            <Select
              value={selectedPeriod}
              onValueChange={(v) => setSelectedPeriod(v as BillingPeriod)}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="monthly">Monthly</SelectItem>
                <SelectItem value="quarterly">
                  Quarterly{" "}
                  {currentPlan?.discount_percentages?.quarterly
                    ? `(${currentPlan.discount_percentages.quarterly}% off)`
                    : ""}
                </SelectItem>
                <SelectItem value="annual">
                  Annual{" "}
                  {currentPlan?.discount_percentages?.annual
                    ? `(${currentPlan.discount_percentages.annual}% off)`
                    : ""}
                </SelectItem>
              </SelectContent>
            </Select>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setPeriodDialogOpen(false)}>
              Cancel
            </Button>
            <Button disabled={actionLoading} onClick={handleChangePeriod}>
              {actionLoading ? "Saving..." : "Update Period"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Add-On Management Dialog */}
      <Dialog open={addonDialogOpen} onOpenChange={setAddonDialogOpen}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>Manage Add-Ons</DialogTitle>
            <DialogDescription>
              Add or remove features and capacity boosts for your subscription.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3 py-4 max-h-[400px] overflow-y-auto">
            {(catalog?.add_ons || []).map((addon) => {
              const isActive =
                selectedSub?.active_add_ons?.includes(addon.code) || false;
              const compatible =
                !addon.compatible_plans?.length ||
                addon.compatible_plans.includes(
                  selectedSub?.plan_code || ""
                );
              return (
                <div
                  key={addon.code}
                  className={`flex items-center justify-between rounded-lg border p-3 ${
                    !compatible ? "opacity-50" : ""
                  }`}
                >
                  <div className="space-y-1">
                    <p className="text-sm font-medium">{addon.name}</p>
                    <p className="text-xs text-muted-foreground">
                      {addon.description}
                    </p>
                    <p className="text-xs font-medium">
                      {formatPrice(addon.price_minor)}/mo
                    </p>
                  </div>
                  <Button
                    size="sm"
                    variant={isActive ? "destructive" : "outline"}
                    disabled={!compatible || actionLoading}
                    onClick={() => {
                      if (selectedSub)
                        handleToggleAddon(selectedSub, addon.code, isActive);
                    }}
                  >
                    {isActive ? (
                      <>
                        <Trash2 className="h-3.5 w-3.5 mr-1" /> Remove
                      </>
                    ) : (
                      <>
                        <Plus className="h-3.5 w-3.5 mr-1" /> Add
                      </>
                    )}
                  </Button>
                </div>
              );
            })}
          </div>
          <DialogFooter>
            <Button onClick={() => setAddonDialogOpen(false)}>Done</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* History Dialog */}
      <Dialog open={historyDialogOpen} onOpenChange={setHistoryDialogOpen}>
        <DialogContent className="sm:max-w-xl">
          <DialogHeader>
            <DialogTitle>Subscription History</DialogTitle>
            <DialogDescription>
              All changes made to this subscription.
            </DialogDescription>
          </DialogHeader>
          <div className="max-h-[400px] overflow-y-auto space-y-3 py-4">
            {history.length === 0 && (
              <p className="text-sm text-muted-foreground text-center py-8">
                No history records found.
              </p>
            )}
            {history.map((entry) => (
              <div
                key={entry.id}
                className="flex items-start gap-3 rounded-lg border p-3"
              >
                <div className="h-8 w-8 rounded-full bg-primary/10 flex items-center justify-center flex-shrink-0 mt-0.5">
                  <History className="h-4 w-4 text-primary" />
                </div>
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium">
                    {changeTypeLabel[entry.change_type] || entry.change_type}
                  </p>
                  {entry.from_plan_code && entry.to_plan_code && (
                    <p className="text-xs text-muted-foreground">
                      {planByCode[entry.from_plan_code]?.name ||
                        entry.from_plan_code}{" "}
                      →{" "}
                      {planByCode[entry.to_plan_code]?.name ||
                        entry.to_plan_code}
                    </p>
                  )}
                  {entry.add_on_code && (
                    <p className="text-xs text-muted-foreground">
                      Add-on: {addonByCode[entry.add_on_code]?.name || entry.add_on_code}
                    </p>
                  )}
                  <p className="text-xs text-muted-foreground mt-1">
                    {new Date(entry.changed_at).toLocaleString()}
                  </p>
                </div>
              </div>
            ))}
          </div>
        </DialogContent>
      </Dialog>

      {/* Invoice Preview Dialog */}
      <Dialog open={invoiceDialogOpen} onOpenChange={setInvoiceDialogOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Invoice Preview</DialogTitle>
            <DialogDescription>
              Estimated charges for the next billing period.
            </DialogDescription>
          </DialogHeader>
          {invoicePreview && (
            <div className="py-4 space-y-3">
              <div className="space-y-2">
                {invoicePreview.line_items.map((item, idx) => (
                  <div
                    key={idx}
                    className="flex justify-between text-sm"
                  >
                    <span>{item.description}</span>
                    <span className="font-medium">
                      {formatPrice(
                        item.amount_minor,
                        invoicePreview.currency
                      )}
                    </span>
                  </div>
                ))}
              </div>
              <Separator />
              <div className="flex justify-between text-sm font-bold">
                <span>Total</span>
                <span>
                  {formatPrice(
                    invoicePreview.subtotal_minor,
                    invoicePreview.currency
                  )}
                </span>
              </div>
              <p className="text-xs text-muted-foreground">
                Period:{" "}
                {invoicePreview.period_start
                  ? new Date(invoicePreview.period_start).toLocaleDateString()
                  : "—"}{" "}
                –{" "}
                {invoicePreview.period_end
                  ? new Date(invoicePreview.period_end).toLocaleDateString()
                  : "—"}
              </p>
            </div>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
};

export default SubscriptionManagementPage;
