import React, { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { Edit, Loader2, PackageOpen } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import { Switch } from "@/components/ui/switch";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useStepUp } from "@/hooks/useStepUp";
import {
  AdminAddOn,
  AdminExpertAllocation,
  AdminPlan,
  listAddOns,
  listExpertAllocations,
  listPlans,
  updateAddOn,
  updateExpertAllocation,
  updatePlan,
} from "@/services/monetization-admin-api";

const money = (minor?: number, ccy = "INR") =>
  minor == null ? "—" : `${ccy} ${(minor / 100).toLocaleString()}`;

const ActiveBadge = ({ active }: { active?: boolean }) => (
  <Badge variant="secondary" className={active ? "bg-green-100 text-green-800" : "bg-gray-100 text-gray-600"}>
    {active ? "Active" : "Inactive"}
  </Badge>
);

// Parse a JSON-textarea value; throws with a friendly message on bad JSON.
const parseJson = (raw: string, field: string): unknown => {
  const trimmed = raw.trim();
  if (!trimmed) return undefined;
  try {
    return JSON.parse(trimmed);
  } catch {
    throw new Error(`"${field}" is not valid JSON.`);
  }
};

const STEP_UP = "billing.plan.manage";

const BillingCatalogPage = () => {
  const { requestToken, StepUpDialog } = useStepUp();
  const [plans, setPlans] = useState<AdminPlan[]>([]);
  const [addons, setAddons] = useState<AdminAddOn[]>([]);
  const [allocations, setAllocations] = useState<AdminExpertAllocation[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  // Single edit dialog reused across the three entities.
  const [editKind, setEditKind] = useState<"plan" | "addon" | "allocation" | null>(null);
  const [editId, setEditId] = useState<string>("");
  const [form, setForm] = useState<Record<string, string>>({});

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [p, a, e] = await Promise.all([
        listPlans().catch(() => [] as AdminPlan[]),
        listAddOns().catch(() => [] as AdminAddOn[]),
        listExpertAllocations().catch(() => [] as AdminExpertAllocation[]),
      ]);
      setPlans(p);
      setAddons(a);
      setAllocations(e);
    } catch {
      toast.error("Failed to load billing catalog");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const openPlan = (p: AdminPlan) => {
    setEditKind("plan");
    setEditId(p.id);
    setForm({
      name: p.name ?? "",
      description: p.description ?? "",
      base_price_minor: String(p.base_price_minor ?? 0),
      billing_cadence: p.billing_cadence ?? "monthly",
      currency: p.currency ?? "INR",
      tier: String(p.tier ?? 0),
      display_order: String(p.display_order ?? 0),
      is_active: String(p.is_active ?? true),
      pricing_tiers: JSON.stringify(p.pricing_tiers ?? {}, null, 2),
      features: JSON.stringify(p.features ?? {}, null, 2),
      default_limits: JSON.stringify(p.default_limits ?? {}, null, 2),
    });
  };

  const openAddon = (a: AdminAddOn) => {
    setEditKind("addon");
    setEditId(a.id);
    setForm({
      name: a.name ?? "",
      description: a.description ?? "",
      price_minor: String(a.price_minor ?? 0),
      billing_cadence: a.billing_cadence ?? "monthly",
      currency: a.currency ?? "INR",
      add_on_type: a.add_on_type ?? "",
      is_active: String(a.is_active ?? true),
      features: JSON.stringify(a.features ?? {}, null, 2),
      limits: JSON.stringify(a.limits ?? {}, null, 2),
    });
  };

  const openAllocation = (e: AdminExpertAllocation) => {
    setEditKind("allocation");
    setEditId(e.id);
    setForm({
      assignment_role: e.assignment_role ?? "",
      status: e.status ?? "",
      allocation_reason: e.allocation_reason ?? "",
      work_order_reference: e.work_order_reference ?? "",
    });
  };

  const setF = (k: string, v: string) => setForm((f) => ({ ...f, [k]: v }));

  const onSave = async () => {
    if (!editKind) return;
    setSaving(true);
    try {
      let payload: Record<string, unknown>;
      if (editKind === "plan") {
        payload = {
          name: form.name,
          description: form.description || null,
          base_price_minor: Number(form.base_price_minor) || 0,
          billing_cadence: form.billing_cadence,
          currency: form.currency,
          tier: Number(form.tier) || 0,
          display_order: Number(form.display_order) || 0,
          is_active: form.is_active === "true",
          pricing_tiers: parseJson(form.pricing_tiers, "Pricing tiers"),
          features: parseJson(form.features, "Features"),
          default_limits: parseJson(form.default_limits, "Default limits"),
        };
      } else if (editKind === "addon") {
        payload = {
          name: form.name,
          description: form.description || null,
          price_minor: Number(form.price_minor) || 0,
          billing_cadence: form.billing_cadence,
          currency: form.currency,
          add_on_type: form.add_on_type || undefined,
          is_active: form.is_active === "true",
          features: parseJson(form.features, "Features"),
          limits: parseJson(form.limits, "Limits"),
        };
      } else {
        payload = {
          assignment_role: form.assignment_role || undefined,
          status: form.status || undefined,
          allocation_reason: form.allocation_reason || undefined,
          work_order_reference: form.work_order_reference || undefined,
        };
      }

      const token = await requestToken(
        STEP_UP,
        "Confirm catalog change",
        "Enter your password to update the billing catalog.",
      );
      if (editKind === "plan") await updatePlan(editId, payload, { stepUpToken: token });
      else if (editKind === "addon") await updateAddOn(editId, payload, { stepUpToken: token });
      else await updateExpertAllocation(editId, payload, { stepUpToken: token });

      toast.success("Saved");
      setEditKind(null);
      await load();
    } catch (e: any) {
      if (String(e?.message || "").toLowerCase().includes("cancelled")) return;
      toast.error(e?.response?.data?.detail || e?.message || "Failed to save");
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center py-24 text-muted-foreground">
        <Loader2 className="mr-2 h-5 w-5 animate-spin" /> Loading catalog…
      </div>
    );
  }

  return (
    <div className="container mx-auto space-y-6 p-6">
      <div className="flex items-center gap-2">
        <PackageOpen className="h-6 w-6 text-blue-600" />
        <div>
          <h1 className="text-2xl font-bold">Billing Catalog</h1>
          <p className="text-sm text-muted-foreground">
            Administer plans, add-ons and expert allocations. Changes require step-up.
          </p>
        </div>
      </div>

      <Tabs defaultValue="plans">
        <TabsList>
          <TabsTrigger value="plans">Plans ({plans.length})</TabsTrigger>
          <TabsTrigger value="addons">Add-ons ({addons.length})</TabsTrigger>
          <TabsTrigger value="allocations">Expert Allocations ({allocations.length})</TabsTrigger>
        </TabsList>

        <TabsContent value="plans">
          <Card>
            <CardHeader><CardTitle>Plans</CardTitle></CardHeader>
            <CardContent>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Code</TableHead><TableHead>Name</TableHead>
                    <TableHead>Family</TableHead><TableHead>Tier</TableHead>
                    <TableHead>Base price</TableHead><TableHead>Status</TableHead>
                    <TableHead className="text-right">Edit</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {plans.map((p) => (
                    <TableRow key={p.id}>
                      <TableCell className="font-mono text-xs">{p.code}</TableCell>
                      <TableCell className="font-medium">{p.name}</TableCell>
                      <TableCell>{p.family ?? "—"}</TableCell>
                      <TableCell>{p.tier ?? 0}</TableCell>
                      <TableCell>{money(p.base_price_minor, p.currency)}</TableCell>
                      <TableCell><ActiveBadge active={p.is_active} /></TableCell>
                      <TableCell className="text-right">
                        <Button variant="ghost" size="icon" className="h-8 w-8" onClick={() => openPlan(p)}>
                          <Edit className="h-4 w-4" />
                        </Button>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="addons">
          <Card>
            <CardHeader><CardTitle>Add-ons</CardTitle></CardHeader>
            <CardContent>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Code</TableHead><TableHead>Name</TableHead>
                    <TableHead>Type</TableHead><TableHead>Price</TableHead>
                    <TableHead>Status</TableHead><TableHead className="text-right">Edit</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {addons.map((a) => (
                    <TableRow key={a.id}>
                      <TableCell className="font-mono text-xs">{a.code}</TableCell>
                      <TableCell className="font-medium">{a.name}</TableCell>
                      <TableCell>{a.add_on_type ?? "—"}</TableCell>
                      <TableCell>{money(a.price_minor, a.currency)}</TableCell>
                      <TableCell><ActiveBadge active={a.is_active} /></TableCell>
                      <TableCell className="text-right">
                        <Button variant="ghost" size="icon" className="h-8 w-8" onClick={() => openAddon(a)}>
                          <Edit className="h-4 w-4" />
                        </Button>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="allocations">
          <Card>
            <CardHeader><CardTitle>Expert Allocations</CardTitle></CardHeader>
            <CardContent>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>ID</TableHead><TableHead>Role</TableHead>
                    <TableHead>Status</TableHead><TableHead>Work order</TableHead>
                    <TableHead className="text-right">Edit</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {allocations.map((e) => (
                    <TableRow key={e.id}>
                      <TableCell className="font-mono text-xs">{e.id}</TableCell>
                      <TableCell>{e.assignment_role ?? "—"}</TableCell>
                      <TableCell>{e.status ?? "—"}</TableCell>
                      <TableCell>{e.work_order_reference ?? "—"}</TableCell>
                      <TableCell className="text-right">
                        <Button variant="ghost" size="icon" className="h-8 w-8" onClick={() => openAllocation(e)}>
                          <Edit className="h-4 w-4" />
                        </Button>
                      </TableCell>
                    </TableRow>
                  ))}
                  {allocations.length === 0 && (
                    <TableRow><TableCell colSpan={5} className="text-center text-muted-foreground">No expert allocations.</TableCell></TableRow>
                  )}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>

      <Dialog open={editKind !== null} onOpenChange={(o) => !o && setEditKind(null)}>
        <DialogContent className="sm:max-w-[560px] max-h-[85vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>
              Edit {editKind === "plan" ? "Plan" : editKind === "addon" ? "Add-on" : "Expert Allocation"}
            </DialogTitle>
            <DialogDescription>Saving requires step-up verification.</DialogDescription>
          </DialogHeader>

          <div className="grid gap-3 py-2">
            {(editKind === "plan" || editKind === "addon") && (
              <>
                <Field label="Name"><Input value={form.name} onChange={(e) => setF("name", e.target.value)} /></Field>
                <Field label="Description">
                  <Textarea rows={2} value={form.description} onChange={(e) => setF("description", e.target.value)} />
                </Field>
                <div className="grid grid-cols-3 gap-3">
                  <Field label="Price (minor)">
                    <Input type="number" value={editKind === "plan" ? form.base_price_minor : form.price_minor}
                      onChange={(e) => setF(editKind === "plan" ? "base_price_minor" : "price_minor", e.target.value)} />
                  </Field>
                  <Field label="Cadence"><Input value={form.billing_cadence} onChange={(e) => setF("billing_cadence", e.target.value)} /></Field>
                  <Field label="Currency"><Input value={form.currency} onChange={(e) => setF("currency", e.target.value)} /></Field>
                </div>
                <div className="flex items-center gap-2 pt-1">
                  <Switch checked={form.is_active === "true"} onCheckedChange={(v) => setF("is_active", String(v))} />
                  <Label>Active</Label>
                </div>
              </>
            )}

            {editKind === "plan" && (
              <>
                <div className="grid grid-cols-2 gap-3">
                  <Field label="Tier"><Input type="number" value={form.tier} onChange={(e) => setF("tier", e.target.value)} /></Field>
                  <Field label="Display order"><Input type="number" value={form.display_order} onChange={(e) => setF("display_order", e.target.value)} /></Field>
                </div>
                <JsonField label="Pricing tiers (JSON)" value={form.pricing_tiers} onChange={(v) => setF("pricing_tiers", v)} />
                <JsonField label="Features (JSON)" value={form.features} onChange={(v) => setF("features", v)} />
                <JsonField label="Default limits (JSON)" value={form.default_limits} onChange={(v) => setF("default_limits", v)} />
              </>
            )}

            {editKind === "addon" && (
              <>
                <Field label="Add-on type"><Input value={form.add_on_type} onChange={(e) => setF("add_on_type", e.target.value)} /></Field>
                <JsonField label="Features (JSON)" value={form.features} onChange={(v) => setF("features", v)} />
                <JsonField label="Limits (JSON)" value={form.limits} onChange={(v) => setF("limits", v)} />
              </>
            )}

            {editKind === "allocation" && (
              <>
                <Field label="Assignment role"><Input value={form.assignment_role} onChange={(e) => setF("assignment_role", e.target.value)} /></Field>
                <Field label="Status"><Input value={form.status} onChange={(e) => setF("status", e.target.value)} /></Field>
                <Field label="Allocation reason"><Textarea rows={2} value={form.allocation_reason} onChange={(e) => setF("allocation_reason", e.target.value)} /></Field>
                <Field label="Work order reference"><Input value={form.work_order_reference} onChange={(e) => setF("work_order_reference", e.target.value)} /></Field>
              </>
            )}
          </div>

          <DialogFooter>
            <Button variant="outline" onClick={() => setEditKind(null)}>Cancel</Button>
            <Button onClick={onSave} disabled={saving}>{saving ? "Saving…" : "Save"}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      {StepUpDialog}
    </div>
  );
};

const Field = ({ label, children }: { label: string; children: React.ReactNode }) => (
  <div className="grid gap-1.5">
    <Label className="text-xs text-muted-foreground">{label}</Label>
    {children}
  </div>
);

const JsonField = ({ label, value, onChange }: { label: string; value: string; onChange: (v: string) => void }) => (
  <div className="grid gap-1.5">
    <Label className="text-xs text-muted-foreground">{label}</Label>
    <Textarea rows={4} className="font-mono text-xs" value={value} onChange={(e) => onChange(e.target.value)} />
  </div>
);

export default BillingCatalogPage;
