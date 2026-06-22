import { api } from "./api";

// IPC / Contractor Bill Register API. Mirrors routers/ipc_bills.py.
// Function-first model: line items (gross, 3 perspective columns), deductions
// per perspective, and discrete payment records.

export type IPCStatus =
  | "draft" | "submitted" | "under_verification" | "verified"
  | "approved" | "partially_paid" | "paid" | "rejected";

export type PaymentStructure = "full" | "80_20" | "20" | "partial" | "custom";

// One BOQ/scope line with the three estimate perspectives side by side.
export interface IPCLineItem {
  description?: string | null;
  currency: string;
  conversion_rate: number;
  claimed: number;
  verified: number;
  approved: number;
}

// One deduction / recovery line.
export interface CurrencyAmount {
  currency: string;
  conversion_rate: number; // to base, fixed at award
  amount: number;
  category?: string | null;    // master code (advance / deduction type)
  description?: string | null; // free-text reason / detail
}

// One deduction line: same column shape as a line item (claimed/verified/
// approved side by side), plus an optional master category and description.
export interface IPCDeductionLine {
  category?: string | null;
  description?: string | null;
  currency: string;
  conversion_rate: number;
  claimed: number;
  verified: number;
  approved: number;
}

// All deduction components, each a list of lines. One editor tab per component.
export interface IPCDeductions {
  recovery_of_advances: IPCDeductionLine[];
  withheld: IPCDeductionLine[];
  penalties_ld: IPCDeductionLine[];
  deductions: IPCDeductionLine[];  // statutory: Income Tax, Labour Cess (via master)
  gst: IPCDeductionLine[];
}

export const DEDUCTION_KEYS: (keyof IPCDeductions)[] = [
  "recovery_of_advances", "withheld", "penalties_ld", "deductions", "gst",
];
// Tab + editor title per deduction component.
export const DEDUCTION_LABELS: Record<keyof IPCDeductions, string> = {
  recovery_of_advances: "Recoveries",
  withheld: "Withhold",
  penalties_ld: "Penalties",
  deductions: "Deductions",
  gst: "GST",
};

// Which deduction rows carry a typed category (from which master) and/or a
// free-text description. Drives the editor UI.
export interface ComponentFieldConfig {
  categoryKind?: "advance" | "deduction";
  description?: boolean;
}
export const COMPONENT_FIELDS: Record<keyof IPCDeductions, ComponentFieldConfig> = {
  recovery_of_advances: { categoryKind: "advance", description: true },
  withheld: { description: true },
  penalties_ld: { description: true },
  deductions: { categoryKind: "deduction", description: true },
  gst: { description: true },
};

export interface IPCPaymentRecord {
  payment_date?: string | null;
  reference?: string | null;
  method?: string | null;
  currency: string;
  conversion_rate: number;
  amount: number;
}

export const emptyDeductions = (): IPCDeductions => ({
  recovery_of_advances: [], withheld: [], penalties_ld: [], deductions: [], gst: [],
});

export interface IPCBillDTO {
  id: string;
  ipc_number?: string | null;
  ipc_date?: string | null;
  ipc_period?: string | null;
  period_from?: string | null;
  period_to?: string | null;
  contract_id?: string | null;
  contractor_name?: string | null;
  approver?: string | null;
  base_currency: string;
  payment_structure: PaymentStructure;
  payment_percentage?: number | null;
  line_items: IPCLineItem[];
  deductions: IPCDeductions;
  payments: IPCPaymentRecord[];
  status: IPCStatus;
  remarks?: string | null;
  letter_references: string[];
  linked_document_ids: string[];
  original_contract_value?: number | null;
  project_id?: string | null;
  current_revision?: number | null;
  revisions?: IPCRevision[];
  // Derived (base currency)
  claimed_total_base?: number | null;
  verified_total_base?: number | null;
  approved_total_base?: number | null;
  total_deductions_base?: number | null;
  net_payable_base?: number | null;
  paid_base?: number | null;
  balance_payable_base?: number | null;
  percent_billed?: number | null;
  percent_approved?: number | null;
  created_at?: string | null;
}

export interface IPCRevision {
  revision_number: number;
  status?: string | null;
  remarks?: string | null;
  changed_by?: string | null;
  changed_at?: string | null;
}

export interface IPCBillPayload {
  project_id: string;
  ipc_number?: string;
  ipc_date?: string;
  ipc_period?: string;
  period_from?: string;
  period_to?: string;
  contract_id?: string;
  contractor_name?: string;
  approver?: string;
  base_currency?: string;
  payment_structure?: PaymentStructure;
  payment_percentage?: number;
  line_items?: IPCLineItem[];
  deductions?: IPCDeductions;
  payments?: IPCPaymentRecord[];
  status?: IPCStatus;
  remarks?: string;
  letter_references?: string[];
  linked_document_ids?: string[];
  original_contract_value?: number;
}

export interface IPCBillSummaryDTO {
  total_ipcs: number;
  base_currency: string;
  total_claimed_base: number;
  total_approved_base: number;
  total_net_payable_base: number;
  total_paid_base: number;
  total_balance_payable_base: number;
  cumulative_ipc_value_base: number;
  percent_of_contract_billed: number;
  percent_of_contract_approved: number;
  pending_count: number;
  approved_count: number;
  paid_count: number;
}

const norm = (raw: any): IPCBillDTO => ({
  ...raw,
  id: raw?._id ?? raw?.id,
  line_items: raw?.line_items ?? [],
  payments: raw?.payments ?? [],
  letter_references: raw?.letter_references ?? [],
  linked_document_ids: raw?.linked_document_ids ?? [],
  deductions: { ...emptyDeductions(), ...(raw?.deductions || {}) },
});

export async function getIPCBills(params?: {
  project_id?: string; contract_id?: string; status?: string;
  payment_status?: string; currency?: string; date_from?: string; date_to?: string;
}): Promise<IPCBillDTO[]> {
  const { data } = await api.get("/ipc-bills", { params });
  return Array.isArray(data) ? data.map(norm) : [];
}

export async function getIPCSummary(params?: { project_id?: string; contract_id?: string }): Promise<IPCBillSummaryDTO> {
  const { data } = await api.get("/ipc-bills/summary", { params });
  return data as IPCBillSummaryDTO;
}

export async function createIPCBill(payload: IPCBillPayload): Promise<IPCBillDTO> {
  const { data } = await api.post("/ipc-bills", payload);
  return norm(data);
}

export async function updateIPCBill(id: string, payload: Partial<IPCBillPayload>): Promise<IPCBillDTO> {
  const { data } = await api.put(`/ipc-bills/${id}`, payload);
  return norm(data);
}

export async function deleteIPCBill(id: string): Promise<void> {
  await api.delete(`/ipc-bills/${id}`);
}

export async function exportIPCBills(
  format: "csv" | "xlsx" | "pdf",
  params?: { project_id?: string; contract_id?: string; ipc_id?: string },
): Promise<Blob> {
  const { data } = await api.get("/ipc-bills/export", { params: { format, ...params }, responseType: "blob" });
  return data instanceof Blob ? data : new Blob([data]);
}

// Base-currency total of a deduction/payment list (sum of amount x rate).
export const componentBase = (items: CurrencyAmount[] | IPCPaymentRecord[]): number =>
  (items || []).reduce((s, x: any) => s + (Number(x.amount) || 0) * (Number(x.conversion_rate) || 1), 0);

// Base-currency total of one column (claimed/verified/approved) of line items
// or deduction lines — both share the column shape.
export type ColumnKey = "claimed" | "verified" | "approved";
interface ColumnLine {
  claimed?: number;
  verified?: number;
  approved?: number;
  conversion_rate?: number;
}
export const lineTotal = (items: ColumnLine[], col: ColumnKey): number =>
  (items || []).reduce((s, x) => s + (Number(x[col]) || 0) * (Number(x.conversion_rate) || 1), 0);

// Base-currency total of one perspective column summed across every deduction
// component.
export const deductionsColTotal = (d: IPCDeductions | undefined, col: ColumnKey): number =>
  d ? DEDUCTION_KEYS.reduce((s, k) => s + lineTotal(d[k], col), 0) : 0;
