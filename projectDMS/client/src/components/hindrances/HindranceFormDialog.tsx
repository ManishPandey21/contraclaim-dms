import { useEffect, useMemo, useState } from "react";
import { Loader2, Save } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import {
  HINDRANCE_CATEGORY_OPTIONS,
  HINDRANCE_CLAIM_STATUS_OPTIONS,
  HINDRANCE_RESPONSIBILITY_OPTIONS,
  HINDRANCE_STATUS_OPTIONS,
  HINDRANCE_TYPE_OPTIONS,
  fromDateInput,
  hindranceStatusLabel,
  toDateInput,
} from "@/lib/hindrance-labels";
import {
  createHindrance,
  hindrancePatch,
  updateHindrance,
  type HindranceCreatePayload,
  type HindranceDTO,
} from "@/services/hindrance-api";

type FormState = {
  event_type: string;
  title: string;
  category: string;
  start_date: string;
  end_date: string;
  status: string;
  responsibility: string;
  responsible_party: string;
  affected_party: string;
  location: string;
  cause: string;
  description: string;
  impact_description: string;
  programme_activity: string;
  critical_path_impact: boolean;
  float_consumed_days: string;
  claimed_days: string;
  assessed_days: string;
  entitlement: string;
  claim_status: string;
};

const EMPTY: FormState = {
  event_type: "hindrance",
  title: "",
  category: "",
  start_date: "",
  end_date: "",
  status: "open",
  responsibility: "under_review",
  responsible_party: "",
  affected_party: "",
  location: "",
  cause: "",
  description: "",
  impact_description: "",
  programme_activity: "",
  critical_path_impact: false,
  float_consumed_days: "",
  claimed_days: "",
  assessed_days: "",
  entitlement: "",
  claim_status: "",
};

function fromItem(item: HindranceDTO): FormState {
  const text = (value?: string | null) => value ?? "";
  const number = (value?: number | null) => (value === null || value === undefined ? "" : String(value));
  return {
    event_type: item.event_type,
    title: item.title,
    category: text(item.category),
    start_date: toDateInput(item.start_date),
    end_date: toDateInput(item.end_date),
    status: item.status,
    responsibility: item.responsibility,
    responsible_party: text(item.responsible_party),
    affected_party: text(item.affected_party),
    location: text(item.location),
    cause: text(item.cause),
    description: text(item.description),
    impact_description: text(item.impact_description),
    programme_activity: text(item.programme_activity),
    critical_path_impact: Boolean(item.critical_path_impact),
    float_consumed_days: number(item.float_consumed_days),
    claimed_days: number(item.claimed_days),
    assessed_days: number(item.assessed_days),
    entitlement: text(item.entitlement),
    claim_status: text(item.claim_status),
  };
}

function hasAssessment(form: FormState): boolean {
  return Boolean(
    form.critical_path_impact ||
      form.float_consumed_days ||
      form.claimed_days ||
      form.assessed_days ||
      form.entitlement ||
      form.claim_status,
  );
}

function apiError(error: unknown, fallback: string): string {
  const detail = (error as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((entry: { msg?: string; loc?: unknown[] }) => {
        const field = Array.isArray(entry?.loc) ? entry.loc[entry.loc.length - 1] : "";
        return [field, entry?.msg].filter(Boolean).join(": ");
      })
      .join("; ");
  }
  return fallback;
}

export interface HindranceFormDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Present when editing; absent when recording a new entry. */
  item?: HindranceDTO | null;
  projectId?: string;
  organizationId?: string;
  projectName?: string;
  onSaved: (saved: HindranceDTO) => void;
}

export default function HindranceFormDialog({
  open,
  onOpenChange,
  item,
  projectId,
  organizationId,
  projectName,
  onSaved,
}: HindranceFormDialogProps) {
  const editing = Boolean(item);
  const [form, setForm] = useState<FormState>(EMPTY);
  const [assessment, setAssessment] = useState(false);
  const [saving, setSaving] = useState(false);
  const [errors, setErrors] = useState<Record<string, string>>({});

  useEffect(() => {
    if (!open) return;
    const initial = item ? fromItem(item) : { ...EMPTY };
    setForm(initial);
    setAssessment(item ? hasAssessment(initial) : false);
    setErrors({});
  }, [item, open]);

  const legacyStatus = useMemo(
    () => (item && !HINDRANCE_STATUS_OPTIONS.some((option) => option.value === item.status) ? item.status : null),
    [item],
  );

  const set = <K extends keyof FormState>(key: K, value: FormState[K]) =>
    setForm((current) => ({ ...current, [key]: value }));

  const validate = (): Record<string, string> => {
    const next: Record<string, string> = {};
    if (!form.title.trim()) next.title = "Title is required";
    if (!form.start_date) next.start_date = "Start date is required";
    if (form.start_date && form.end_date && form.end_date < form.start_date) {
      next.end_date = "End date cannot be before the start date";
    }
    for (const key of ["float_consumed_days", "claimed_days", "assessed_days"] as const) {
      if (form[key] && (Number.isNaN(Number(form[key])) || Number(form[key]) < 0)) {
        next[key] = "Enter zero or a positive number of days";
      }
    }
    if (!editing && !projectId) next.project = "Select a project in the navbar first";
    return next;
  };

  const payload = (): HindranceCreatePayload => {
    const text = (value: string) => (value.trim() ? value.trim() : null);
    const days = (value: string) => (value.trim() ? Number(value) : null);
    const body: HindranceCreatePayload = {
      project_id: projectId || item?.project_id || "",
      event_type: form.event_type as HindranceCreatePayload["event_type"],
      title: form.title.trim(),
      start_date: fromDateInput(form.start_date) as string,
      end_date: fromDateInput(form.end_date),
      category: (form.category || null) as HindranceCreatePayload["category"],
      responsibility: form.responsibility as HindranceCreatePayload["responsibility"],
      responsible_party: text(form.responsible_party),
      affected_party: text(form.affected_party),
      location: text(form.location),
      cause: text(form.cause),
      description: text(form.description),
      impact_description: text(form.impact_description),
      programme_activity: text(form.programme_activity),
      critical_path_impact: assessment ? form.critical_path_impact : false,
      float_consumed_days: assessment ? days(form.float_consumed_days) : null,
      claimed_days: assessment ? days(form.claimed_days) : null,
      assessed_days: assessment ? days(form.assessed_days) : null,
      entitlement: assessment ? text(form.entitlement) : null,
      claim_status: (assessment && form.claim_status ? form.claim_status : null) as HindranceCreatePayload["claim_status"],
    };
    if (!legacyStatus || form.status !== legacyStatus) {
      body.status = form.status as HindranceCreatePayload["status"];
    }
    if (!editing && organizationId) body.organization_id = organizationId;
    return body;
  };

  const submit = async () => {
    const found = validate();
    setErrors(found);
    if (Object.keys(found).length) return;
    setSaving(true);
    try {
      const body = payload();
      let saved: HindranceDTO;
      if (item) {
        const patch = hindrancePatch(item, body);
        if (Object.keys(patch).length === 0) {
          toast.info("No changes to save");
          onOpenChange(false);
          return;
        }
        saved = await updateHindrance(item.id, patch);
        toast.success(`${saved.hindrance_ref || "Entry"} updated`);
      } else {
        saved = await createHindrance(body);
        toast.success(`${saved.hindrance_ref} recorded`);
      }
      if (saved.timeline_sync_status === "failed") {
        toast.warning("Saved, but the Contract Timeline could not be updated. Retry from the record.");
      }
      onSaved(saved);
      onOpenChange(false);
    } catch (error) {
      toast.error(apiError(error, editing ? "Failed to update the entry" : "Failed to record the entry"));
    } finally {
      setSaving(false);
    }
  };

  const fieldError = (key: string) =>
    errors[key] ? (
      <p id={`hindrance-${key}-error`} className="mt-1 text-xs text-destructive">
        {errors[key]}
      </p>
    ) : null;

  const selectClass =
    "flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50";

  return (
    <Dialog open={open} onOpenChange={(next) => !saving && onOpenChange(next)}>
      <DialogContent className="max-h-[90vh] w-[calc(100vw-2rem)] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>{editing ? `Edit ${item?.hindrance_ref || "entry"}` : "Record hindrance or constraint"}</DialogTitle>
          <DialogDescription>
            {editing
              ? "Only the fields you change are saved. Clearing an optional field removes it."
              : `The reference is assigned by the server${projectName ? ` for ${projectName}` : ""}.`}
          </DialogDescription>
        </DialogHeader>

        {errors.project && (
          <p role="alert" className="rounded-md border border-destructive/40 bg-destructive/5 p-2 text-sm text-destructive">
            {errors.project}
          </p>
        )}

        <div className="space-y-4">
          <section aria-labelledby="hindrance-info-heading" className="space-y-3">
            <h3 id="hindrance-info-heading" className="text-sm font-semibold">
              Hindrance / constraint information
            </h3>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <div>
                <Label htmlFor="hindrance-type">Type</Label>
                <select id="hindrance-type" className={selectClass} value={form.event_type}
                  onChange={(event) => set("event_type", event.target.value)}>
                  {HINDRANCE_TYPE_OPTIONS.map((option) => (
                    <option key={option.value} value={option.value}>{option.label}</option>
                  ))}
                </select>
              </div>
              <div>
                <Label htmlFor="hindrance-category">Category</Label>
                <select id="hindrance-category" className={selectClass} value={form.category}
                  onChange={(event) => set("category", event.target.value)}>
                  <option value="">Uncategorised</option>
                  {HINDRANCE_CATEGORY_OPTIONS.map((option) => (
                    <option key={option.value} value={option.value}>{option.label}</option>
                  ))}
                </select>
              </div>
            </div>
            <div>
              <Label htmlFor="hindrance-title">Title *</Label>
              <Input id="hindrance-title" value={form.title} maxLength={300}
                aria-invalid={Boolean(errors.title)} aria-describedby={errors.title ? "hindrance-title-error" : undefined}
                onChange={(event) => set("title", event.target.value)} />
              {fieldError("title")}
            </div>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
              <div>
                <Label htmlFor="hindrance-start">Start / occurrence date *</Label>
                <Input id="hindrance-start" type="date" value={form.start_date}
                  aria-invalid={Boolean(errors.start_date)} aria-describedby={errors.start_date ? "hindrance-start_date-error" : undefined}
                  onChange={(event) => set("start_date", event.target.value)} />
                {fieldError("start_date")}
              </div>
              <div>
                <Label htmlFor="hindrance-end">End / resolution date</Label>
                <Input id="hindrance-end" type="date" value={form.end_date}
                  aria-invalid={Boolean(errors.end_date)} aria-describedby={errors.end_date ? "hindrance-end_date-error" : undefined}
                  onChange={(event) => set("end_date", event.target.value)} />
                {fieldError("end_date")}
              </div>
              <div>
                <Label htmlFor="hindrance-status">Status</Label>
                <select id="hindrance-status" className={selectClass} value={form.status}
                  onChange={(event) => set("status", event.target.value)}>
                  {legacyStatus && <option value={legacyStatus}>{hindranceStatusLabel(legacyStatus)}</option>}
                  {HINDRANCE_STATUS_OPTIONS.map((option) => (
                    <option key={option.value} value={option.value}>{option.label}</option>
                  ))}
                </select>
              </div>
            </div>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
              <div>
                <Label htmlFor="hindrance-responsibility">Responsibility</Label>
                <select id="hindrance-responsibility" className={selectClass} value={form.responsibility}
                  onChange={(event) => set("responsibility", event.target.value)}>
                  {HINDRANCE_RESPONSIBILITY_OPTIONS.map((option) => (
                    <option key={option.value} value={option.value}>{option.label}</option>
                  ))}
                </select>
              </div>
              <div>
                <Label htmlFor="hindrance-responsible-party">Responsible party</Label>
                <Input id="hindrance-responsible-party" value={form.responsible_party} maxLength={200}
                  onChange={(event) => set("responsible_party", event.target.value)} />
              </div>
              <div>
                <Label htmlFor="hindrance-affected-party">Affected party</Label>
                <Input id="hindrance-affected-party" value={form.affected_party} maxLength={200}
                  onChange={(event) => set("affected_party", event.target.value)} />
              </div>
            </div>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <div>
                <Label htmlFor="hindrance-location">Location</Label>
                <Input id="hindrance-location" value={form.location} maxLength={300}
                  onChange={(event) => set("location", event.target.value)} />
              </div>
              <div>
                <Label htmlFor="hindrance-activity-text">Programme activity (free text)</Label>
                <Input id="hindrance-activity-text" value={form.programme_activity} maxLength={300}
                  onChange={(event) => set("programme_activity", event.target.value)} />
              </div>
            </div>
            <div>
              <Label htmlFor="hindrance-description">Description</Label>
              <Textarea id="hindrance-description" rows={3} value={form.description}
                onChange={(event) => set("description", event.target.value)} />
            </div>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <div>
                <Label htmlFor="hindrance-cause">Cause</Label>
                <Textarea id="hindrance-cause" rows={2} value={form.cause}
                  onChange={(event) => set("cause", event.target.value)} />
              </div>
              <div>
                <Label htmlFor="hindrance-impact">Impact</Label>
                <Textarea id="hindrance-impact" rows={2} value={form.impact_description}
                  onChange={(event) => set("impact_description", event.target.value)} />
              </div>
            </div>
          </section>

          <section aria-labelledby="hindrance-assessment-heading" className="space-y-3 rounded-md border p-3">
            <div className="flex items-start justify-between gap-3">
              <div>
                <h3 id="hindrance-assessment-heading" className="text-sm font-semibold">Delay assessment</h3>
                <p className="text-xs text-muted-foreground">
                  Optional. A hindrance or constraint is not presumed to delay the works or carry an EOT entitlement.
                </p>
              </div>
              <label className="flex shrink-0 items-center gap-2 text-sm">
                <input type="checkbox" checked={assessment} aria-controls="hindrance-assessment-fields"
                  onChange={(event) => setAssessment(event.target.checked)} />
                Record assessment
              </label>
            </div>
            {assessment && (
              <div id="hindrance-assessment-fields" className="space-y-3">
                <label className="flex items-center gap-2 text-sm">
                  <input type="checkbox" checked={form.critical_path_impact}
                    onChange={(event) => set("critical_path_impact", event.target.checked)} />
                  Affects the critical path
                </label>
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
                  <div>
                    <Label htmlFor="hindrance-float">Float consumed (days)</Label>
                    <Input id="hindrance-float" type="number" min={0} step="0.5" value={form.float_consumed_days}
                      aria-invalid={Boolean(errors.float_consumed_days)}
                      onChange={(event) => set("float_consumed_days", event.target.value)} />
                    {fieldError("float_consumed_days")}
                  </div>
                  <div>
                    <Label htmlFor="hindrance-claimed">Claimed (days)</Label>
                    <Input id="hindrance-claimed" type="number" min={0} step="0.5" value={form.claimed_days}
                      aria-invalid={Boolean(errors.claimed_days)}
                      onChange={(event) => set("claimed_days", event.target.value)} />
                    {fieldError("claimed_days")}
                  </div>
                  <div>
                    <Label htmlFor="hindrance-assessed">Assessed (days)</Label>
                    <Input id="hindrance-assessed" type="number" min={0} step="0.5" value={form.assessed_days}
                      aria-invalid={Boolean(errors.assessed_days)}
                      onChange={(event) => set("assessed_days", event.target.value)} />
                    {fieldError("assessed_days")}
                  </div>
                </div>
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                  <div>
                    <Label htmlFor="hindrance-claim-status">Claim status</Label>
                    <select id="hindrance-claim-status" className={selectClass} value={form.claim_status}
                      onChange={(event) => set("claim_status", event.target.value)}>
                      <option value="">Not recorded</option>
                      {HINDRANCE_CLAIM_STATUS_OPTIONS.map((option) => (
                        <option key={option.value} value={option.value}>{option.label}</option>
                      ))}
                    </select>
                  </div>
                  <div>
                    <Label htmlFor="hindrance-entitlement">Entitlement</Label>
                    <Input id="hindrance-entitlement" value={form.entitlement}
                      onChange={(event) => set("entitlement", event.target.value)} />
                  </div>
                </div>
              </div>
            )}
          </section>
        </div>

        <DialogFooter className="gap-2">
          <Button type="button" variant="outline" disabled={saving} onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button type="button" disabled={saving} onClick={() => void submit()}>
            {saving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Save className="mr-2 h-4 w-4" />}
            {editing ? "Save changes" : "Record entry"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
