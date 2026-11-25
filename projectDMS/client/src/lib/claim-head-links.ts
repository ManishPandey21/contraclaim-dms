/** What a pleaded head of claim must point at (plan 4B-34, B2). */
export interface ClaimHeadLinks {
  clause_ids: string[];
  notice_ids: string[];
  evidence_ids: string[];
}

export const EMPTY_CLAIM_HEAD_LINKS: ClaimHeadLinks = { clause_ids: [], notice_ids: [], evidence_ids: [] };
