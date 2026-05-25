import {
  Letter,
  LetterStatus,
  User,
} from "@/hooks/useLetterWorkflow";

export interface UILetterUser {
  id: string;
  name: string;
  email?: string;
  avatar?: string;
}

export interface UILetterReference {
  id: string;
  title: string;
  subject: string;
  date?: string;
  referenceNumber?: string;
}

export interface UILetter {
  id: string;
  title: string;
  recipient: string;
  subject: string;
  content: string;
  letterNo?: string;
  status: LetterStatus;
  createdAt: string;
  updatedAt: string;
  createdBy: UILetterUser;
  assignedTo: UILetterUser;
  organizationId?: string;
  projectId?: string;
  statusStartDate?: string;
  pendencyDays?: number;
  comments: string[];
  reference?: UILetterReference;
  strategicPlan?: string;
  strategyPlan?: string;
  strategyRole?: string;
  strategyRecipient?: string;
  contractorContext?: string;
  engineerContext?: string;
  employerContext?: string;
  draftBody?: string;
  draftingProfile?: string;
  draftingAssignedBy?: string;
  draftingAssignedAt?: string;
  summaryPoints?: string[];
  graphStatus?: string;
  graphWarnings?: string[];
  graphRunId?: string;
  strategyRunId?: string;
  draftTrace?: Record<string, unknown>[];
  strategyGraphTrace?: Record<string, unknown>[];
  strategyGraphStatus?: string;
  strategyGraphStartedAt?: string;
  strategyGraphCompletedAt?: string;
  strategyPlanApprovedBy?: string;
  strategyPlanApprovedAt?: string;
  threadLetters?: string[];
  contextDocumentIds?: string[];
  contextDocuments?: Record<string, unknown>[];
  backgroundSummary?: Record<string, unknown>[];
  backgroundAnnotations?: string;
  strategicOutline?: Record<string, unknown> | null;
  outlineLastEditedBy?: string;
  outlineLastEditedAt?: string;
  graphThread?: Record<string, unknown>[];
  draftSources?: Record<string, unknown>[];
  reviewerFindings?: Record<string, unknown>[];
  reviewerBlocking?: boolean;
  draftVersions?: Record<string, unknown>[];
  currentDraftVersion?: number;
}

export interface UILetterInputRequest {
  id: string;
  requestedBy: UILetterUser;
  requestDetails: string;
  dueDate?: string;
  createdAt: string;
  response?: string;
  respondedAt?: string;
}

const resolveUser = (users: User[], userId?: string | null): UILetterUser => {
  const normalizedUserId =
    typeof userId === "object" && userId !== null
      ? String((userId as any).id ?? (userId as any)._id ?? (userId as any).$oid ?? "")
      : String(userId ?? "");

  if (!normalizedUserId) {
    return {
      id: "unknown",
      name: "Unassigned",
    };
  }

  const match = users.find(
    (user) =>
      String(user.id) === normalizedUserId ||
      String(user.email ?? "") === normalizedUserId,
  );

  if (match) {
    return {
      id: String(match.id),
      name: match.name,
      email: match.email,
      avatar: match.avatar,
    };
  }

  return {
    id: normalizedUserId,
    name: "Unknown user",
  };
};

const normaliseDate = (value?: string | Date | null): string | undefined => {
  if (!value) return undefined;
  if (value instanceof Date) return value.toISOString();
  if (typeof value === "string" && value.length > 0) return value;
  return undefined;
};

export const mapLetterToUi = (
  letter: Letter,
  users: User[],
): UILetter => {
  const createdBy = resolveUser(users, letter.created_by ?? letter.createdBy);
  const assignedTo = resolveUser(users, letter.assigned_to ?? letter.assignedTo);

  const referenceRaw = (letter as any).reference;
  const reference: UILetterReference | undefined = referenceRaw
    ? {
        id: referenceRaw.id ?? referenceRaw._id ?? referenceRaw.reference_id ?? "",
        title: referenceRaw.title ?? "",
        subject: referenceRaw.subject ?? "",
        date:
          referenceRaw.date ??
          referenceRaw.referenceDate ??
          referenceRaw.reference_date ??
          undefined,
        referenceNumber:
          referenceRaw.referenceNumber ??
          referenceRaw.reference_number ??
          undefined,
      }
    : undefined;

  const statusStartDate =
    letter.statusStartDate ??
    letter.status_start_date ??
    normaliseDate((letter as any).statusstartdate);

  const pendencyDays =
    letter.pendencyDays ??
    letter.pendency_days ??
    undefined;

  const strategyPlan =
    (letter as any).strategy_plan ??
    (letter as any).draft_plan ??
    undefined;

  return {
    id: String(letter.id),
    title: letter.title ?? "",
    recipient: letter.recipient ?? "",
    subject: letter.subject ?? "",
    content: letter.content ?? "",
    letterNo: (letter as any).letter_no ?? (letter as any).letterNo ?? undefined,
    status: (letter.status ?? "Draft") as LetterStatus,
    createdAt:
      normaliseDate(letter.created_at as any) ??
      new Date().toISOString(),
    updatedAt:
      normaliseDate(letter.updated_at as any) ??
      normaliseDate(letter.created_at as any) ??
      new Date().toISOString(),
    createdBy,
    assignedTo,
    organizationId: letter.organization_id ?? undefined,
    projectId: letter.project_id ?? undefined,
    statusStartDate,
    pendencyDays,
    comments: Array.isArray(letter.comments) ? letter.comments : [],
    reference,
    strategicPlan: strategyPlan,
    strategyPlan,
    strategyRole: (letter as any).strategy_role ?? undefined,
    strategyRecipient: (letter as any).strategy_recipient ?? undefined,
    contractorContext: (letter as any).contractor_context ?? undefined,
    engineerContext: (letter as any).engineer_context ?? undefined,
    employerContext: (letter as any).employer_context ?? undefined,
    draftBody:
      (letter as any).draft_output ??
      letter.content ??
      "",
    draftingProfile: (letter as any).drafting_profile ?? undefined,
    draftingAssignedBy: (letter as any).drafting_assigned_by ?? undefined,
    draftingAssignedAt: normaliseDate((letter as any).drafting_assigned_at),
    summaryPoints: (letter as any).summary_points ?? [],
    graphStatus: (letter as any).graph_status ?? undefined,
    graphWarnings: (letter as any).graph_warnings ?? [],
    graphRunId: (letter as any).graph_run_id ?? undefined,
    strategyRunId: (letter as any).strategy_run_id ?? undefined,
    draftTrace: (letter as any).draft_trace ?? [],
    strategyGraphTrace: (letter as any).strategy_graph_trace ?? [],
    strategyGraphStatus: (letter as any).strategy_graph_status ?? undefined,
    strategyGraphStartedAt: normaliseDate(
      (letter as any).strategy_graph_started_at
    ),
    strategyGraphCompletedAt: normaliseDate(
      (letter as any).strategy_graph_completed_at
    ),
    strategyPlanApprovedBy: (letter as any).strategy_plan_approved_by ?? undefined,
    strategyPlanApprovedAt: normaliseDate(
      (letter as any).strategy_plan_approved_at
    ),
    threadLetters: (letter as any).thread_letters ?? [],
    contextDocumentIds: (letter as any).context_document_ids ?? [],
    contextDocuments: (letter as any).context_documents ?? [],
    backgroundSummary: (letter as any).background_summary ?? [],
    backgroundAnnotations: (letter as any).background_annotations ?? undefined,
    strategicOutline: (letter as any).strategic_outline ?? null,
    outlineLastEditedBy: (letter as any).outline_last_edited_by ?? undefined,
    outlineLastEditedAt: normaliseDate((letter as any).outline_last_edited_at),
    graphThread: (letter as any).graph_thread ?? [],
  draftSources: (letter as any).draft_sources ?? [],
  reviewerFindings: (letter as any).reviewer_findings ?? [],
  reviewerBlocking: (letter as any).reviewer_blocking ?? false,
  draftVersions: (letter as any).draft_versions ?? [],
  currentDraftVersion: (letter as any).current_draft_version ?? undefined,
};
};

export const mapLettersToUi = (
  letters: Letter[],
  users: User[],
): UILetter[] => letters.map((letter) => mapLetterToUi(letter, users));

export const mapInputRequestToUi = (
  request: any,
  users: User[],
): UILetterInputRequest => {
  const requester = resolveUser(
    users,
    request?.requested_from ??
      request?.requestedFrom ??
      request?.requested_by ??
      request?.requestedBy,
  );
  const responses = Array.isArray(request?.responses) ? request.responses : [];
  const latestResponse = responses.length ? responses[responses.length - 1] : null;

  return {
    id: String(request?.id ?? request?._id ?? ""),
    requestedBy: requester,
    requestDetails:
      request?.details ??
      request?.request_details ??
      request?.requestDetails ??
      "",
    dueDate:
      normaliseDate(request?.due_date ?? request?.dueDate) ?? undefined,
    createdAt:
      normaliseDate(request?.created_at ?? request?.createdAt) ??
      new Date().toISOString(),
    response: request?.response ?? latestResponse?.message ?? undefined,
    respondedAt:
      normaliseDate(
        request?.responded_at ??
          request?.respondedAt ??
          latestResponse?.responded_at ??
          latestResponse?.respondedAt
      ) ??
      undefined,
  };
};

export const mapInputRequestsToUi = (
  requests: any[],
  users: User[],
): UILetterInputRequest[] => {
  if (!Array.isArray(requests)) return [];
  return requests.map((req) => mapInputRequestToUi(req, users));
};
