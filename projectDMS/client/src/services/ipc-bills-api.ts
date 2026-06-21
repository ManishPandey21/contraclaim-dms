import { api } from "./api";

// IPC / Contractor Bill Register API. Mirrors routers/ipc_bills.py.

export type IPCStatus =
  | "draft" | "submitted" | "under_verification" | "verified"
  | "approved" | "partially_paid" | "paid" | "rejected";

export type PaymentStructure = "full" | "80_20" | "20" | "partial" | "custom";

export interface CurrencyAmount {
  currency: string;
  conversion_rate: number; // to base, fixed at award
  amount: number;
}

// Each component is a list of currency amounts (per-component multi-currency).
export interface IPCComponents {
  gross: CurrencyAmount[];
  deductions: CurrencyAmount[];
  recovery_of_advances: CurrencyAmount[];
  it_tax: CurrencyAmount[];
  gst: CurrencyAmount[];
  withheld: CurrencyAmount[];
  penalties_ld: CurrencyAmount[];
}

export const COMPONENT_KEYS: (keyof IPCComponents)[] = [
  "gross", "deductions", "recovery_of_advances", "it_tax", "gst", "withheld", "penalties_ld",
];
export const COMPONENT_LABELS: Record<keyof IPCComponents, string> = {
  gross: "Gross value",
  deductions: "Deductions",
  recovery_of_advances: "Recovery of advances",
  it_tax: "Income tax (IT)",
  gst: "GST",
  withheld: "Withheld",
  penalties_ld: "Penalties / LD",
};

export const PERSPECTIVE_KEYS = [
  "contractor_claimed", "engineer_verified", "employer_approved", "actually_paid",
] as const;
export type PerspectiveKey = (typeof PERSPECTIVE_KEYS)[number];
export const PERSPECTIVE_LABELS: Record<PerspectiveKey, string> = {
  contractor_claimed: "Contractor claimed",
  engineer_verified: "Engineer/GC verified",
  employer_approved: "Employer approved",
  actually_paid: "Actually paid",
};

export const emptyComponents = (): IPCComponents => ({
  gross: [], deductions: [], recovery_of_advances: [], it_tax: [], gst: [], withheld: [], penalties_ld: [],
});

export interface IPCBillDTO {
  id: string;
  ipc_number?: string | null;
  ipc_period?: string | null;
  contract_id?: string | null;
  contractor_name?: string | null;
  base_currency: string;
  payment_structure: PaymentStructure;
  payment_percentage?: number | null;
  contractor_claimed: IPCComponents;
  engineer_verified: IPCComponents;
  employer_approved: IPCComponents;
  actually_paid: IPCComponents;
  submission_date?: string | null;
  verification_date?: string | null;
  approval_date?: string | null;
  payment_date?: string | null;
  status: IPCStatus;
  remarks?: string | null;
  letter_references: string[];
  linked_document_ids: string[];
  original_contract_value?: number | null;
  project_id?: string | null;
  // Derived (base currency)
  claimed_total_base?: number | null;
  verified_total_base?: number | null;
  approved_total_base?: number | null;
  net_payable_base?: number | null;
  paid_base?: number | null;
  balance_payable_base?: number | null;
  percent_billed?: number | null;
  percent_approved?: number | null;
  created_at?: string | null;
}

export interface IPCBillPayload {
  project_id: string;
  ipc_number?: string;
  ipc_period?: string;
  contract_id?: string;
  contractor_name?: string;
  base_currency?: string;
  payment_structure?: PaymentStructure;
  payment_percentage?: number;
  contractor_claimed?: IPCComponents;
  engineer_verified?: IPCComponents;
  employer_approved?: IPCComponents;
  actually_paid?: IPCComponents;
  submission_date?: string;
  verification_date?: string;
  approval_date?: string;
  payment_date?: string;
  status?: IPCStatus;
  remarks?: string;
  letter_references?: string[];
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
  letter_references: raw?.letter_references ?? [],
  linked_document_ids: raw?.linked_document_ids ?? [],
  contractor_claimed: { ...emptyComponents(), ...(raw?.contractor_claimed || {}) },
  engineer_verified: { ...emptyComponents(), ...(raw?.engineer_verified || {}) },
  employer_approved: { ...emptyComponents(), ...(raw?.employer_approved || {}) },
  actually_paid: { ...emptyComponents(), ...(raw?.actually_paid || {}) },
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

// Base-currency total of a component (sum of amount x rate).
export const componentBase = (items: CurrencyAmount[]): number =>
  (items || []).reduce((s, x) => s + (Number(x.amount) || 0) * (Number(x.conversion_rate) || 1), 0);
