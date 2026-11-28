import type { Assertion, BenchResponse, ChainLink, EffectiveSkill, ReplyParagraph } from "@/types/letterBench";

export const TEXT = "Dear Sir, the Site was handed over late. We claim payment under Clause 13. Yours faithfully";

export function assertion(overrides: Partial<Assertion> = {}): Assertion {
  return {
    assertion_id: "A1",
    order: 1,
    quoted_text: "the Site was handed over late.",
    span: { start: 10, end: 40 },
    clause_refs: [],
    clause_checks: [],
    proposed_verdict: "none",
    proposed_reason: "Notice out of time.",
    verdict: null,
    verdict_source: null,
    user_direction: null,
    source_ids: [],
    routed_skill_key: null,
    step_ids: [],
    needs: [],
    reply_paragraph_id: null,
    state: "open",
    origin: "ai",
    ...overrides,
  };
}

export function link(overrides: Partial<ChainLink> = {}): ChainLink {
  return {
    link_id: "l-var",
    skill_key: "indian-infra-variations",
    label: "Variations",
    kind: "evaluation",
    target_assertion_ids: ["A2"],
    skill_version: "1.0.0",
    status: "done",
    step_id: "s-var",
    reason: null,
    added_by: "router",
    ...overrides,
  };
}

export const CHAIN: ChainLink[] = [
  link({ link_id: "l-an", skill_key: "indian-infra-letter-analysis", label: "Letter Analysis", kind: "analysis", target_assertion_ids: [], step_id: "s-an" }),
  link(),
  link({ link_id: "l-eot", skill_key: "indian-infra-eot-claims", label: "Extension of time review", status: "unavailable", skill_version: null, step_id: null, reason: "Not installed", target_assertion_ids: [] }),
  link({ link_id: "l-dr", skill_key: "indian-infra-contract-letters", label: "Draft reply", kind: "drafting", target_assertion_ids: [], status: "pending", step_id: null }),
];

export function paragraph(overrides: Partial<ReplyParagraph> = {}): ReplyParagraph {
  return {
    paragraph_id: "P-A1",
    assertion_id: "A1",
    part: "point",
    text: "Not accepted.",
    step_id: "s-p1",
    stale: false,
    edited_by_user: false,
    degraded: false,
    clause_refs: [],
    source_ids: [],
    ...overrides,
  };
}

export function skill(overrides: Partial<EffectiveSkill> = {}): EffectiveSkill {
  return {
    skill_key: "indian-infra-variations",
    name: "Variations",
    description: "Variation evaluation",
    kind: "evaluation",
    use_when: "Deciding whether something is a variation.",
    version: "1.0.0",
    roles_supported: ["contractor", "engineer", "employer"],
    contract_forms: [],
    chains_to: [],
    required_inputs: [],
    enabled: true,
    pinned: false,
    available: true,
    ...overrides,
  };
}

export function bench(overrides: Partial<BenchResponse["board"]> = {}, gate: Partial<BenchResponse["gate"]> = {}): BenchResponse {
  const assertions = overrides.assertions ?? [
    assertion(),
    assertion({ assertion_id: "A2", order: 2, quoted_text: "We claim payment under Clause 13.", span: { start: 41, end: 74 } }),
  ];
  return {
    letter_id: "L1",
    letter_title: "Reply to CTR/118",
    letter_status: "Draft",
    board: {
      letter_id: "L1",
      organization_id: "org1",
      project_id: "p1",
      board_version: 5,
      stage: "positions",
      role: "employer",
      incoming_document_id: "d1",
      trigger: null,
      incoming_text: TEXT,
      incoming_meta: { letter_no: "CTR/118" },
      issue_type: "delay",
      deadline: { source: "none" },
      chain: CHAIN,
      questions: [],
      reply_paragraphs: [],
      internal_note: null,
      composing: false,
      last_error: null,
      ...overrides,
      assertions,
    },
    steps: [],
    gate: {
      undecided: assertions.filter((a) => !a.verdict).length,
      unanswered_questions: 0,
      stale_steps: 0,
      running_steps: 0,
      can_lock: false,
      can_compose: false,
      blockers: [],
      ...gate,
    },
    rollout_mode: "primary",
    read_only: false,
  };
}
