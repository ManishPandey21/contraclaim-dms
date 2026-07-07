import { api } from "./api";

export type LegalWordStatus =
  | "pending_review"
  | "approved"
  | "scheduled"
  | "published"
  | "rejected"
  | "inactive";

export type LegalWordSource = "system" | "admin_created" | "user_requested" | "ai_suggested";

export type LegalWordSuggestionEligibility =
  | "new_word"
  | "blocked_recently_published"
  | "eligible_for_republish";

export interface LegalWord {
  id: string;
  _id?: string;
  word: string;
  normalized_word: string;
  meaning?: string | null;
  synonyms: string[];
  example_sentence?: string | null;
  source: LegalWordSource;
  requested_by_user_id?: string | null;
  requested_at?: string | null;
  approved_by_admin_id?: string | null;
  approved_at?: string | null;
  first_suggested_date?: string | null;
  last_published_date?: string | null;
  scheduled_date?: string | null;
  published_date?: string | null;
  published_count?: number;
  publication_history?: string[];
  is_eligible_for_republish?: boolean;
  status: LegalWordStatus;
  created_at: string;
  updated_at: string;
  created_by?: string | null;
  updated_by?: string | null;
}

export interface TodayLegalWordsResponse {
  published_date: string;
  words: LegalWord[];
  count: number;
  message?: string | null;
}

export interface LegalWordSearchResponse {
  found: boolean;
  requested: boolean;
  message?: string | null;
  word?: LegalWord | null;
}

export interface LegalWordListResponse {
  words: LegalWord[];
  total: number;
  page: number;
  limit: number;
  has_next?: boolean;
  has_prev?: boolean;
}

export interface LegalWordPayload {
  word: string;
  meaning?: string | null;
  synonyms?: string[];
  example_sentence?: string | null;
  source?: LegalWordSource;
  status?: LegalWordStatus;
  scheduled_date?: string | null;
  published_date?: string | null;
  first_suggested_date?: string | null;
  last_published_date?: string | null;
  published_count?: number;
  publication_history?: string[];
  is_eligible_for_republish?: boolean;
}

export interface LegalWordAISuggestion {
  word: string;
  meaning: string;
  synonyms: string[];
  example_sentence: string;
  existing_word_id?: string | null;
  word_record?: LegalWord | null;
  previous_publication_status: string;
  last_published_date?: string | null;
  eligibility_status: LegalWordSuggestionEligibility;
  eligibility_label: string;
  is_eligible_for_republish: boolean;
  blocked_reason?: string | null;
}

export interface LegalWordAISuggestionResponse {
  suggestions: LegalWordAISuggestion[];
  generated_at: string;
  count: number;
}

export interface ListLegalWordsParams {
  status?: LegalWordStatus;
  source?: LegalWordSource;
  search?: string;
  skip?: number;
  limit?: number;
}

const normalizeWord = (raw: any): LegalWord => ({
  ...raw,
  id: String(raw?.id ?? raw?._id ?? ""),
  _id: raw?._id ?? raw?.id,
  synonyms: Array.isArray(raw?.synonyms) ? raw.synonyms : [],
  publication_history: Array.isArray(raw?.publication_history) ? raw.publication_history : [],
});

const normalizeSuggestion = (raw: any): LegalWordAISuggestion => ({
  ...raw,
  synonyms: Array.isArray(raw?.synonyms) ? raw.synonyms : [],
  word_record: raw?.word_record ? normalizeWord(raw.word_record) : null,
});

export async function getTodayLegalWords(date?: string): Promise<TodayLegalWordsResponse> {
  const { data } = await api.get("/legal-words/today", {
    params: date ? { date } : undefined,
  });
  return {
    ...data,
    words: Array.isArray(data?.words) ? data.words.map(normalizeWord) : [],
  };
}

export async function searchLegalWord(query: string): Promise<LegalWordSearchResponse> {
  const { data } = await api.post("/legal-words/search", { query });
  return {
    ...data,
    word: data?.word ? normalizeWord(data.word) : null,
  };
}

export async function listAdminLegalWords(
  params: ListLegalWordsParams = {},
): Promise<LegalWordListResponse> {
  const { data } = await api.get("/admin/legal-words", { params });
  return {
    ...data,
    words: Array.isArray(data?.words) ? data.words.map(normalizeWord) : [],
  };
}

export async function createAdminLegalWord(payload: LegalWordPayload): Promise<LegalWord> {
  const { data } = await api.post("/admin/legal-words", payload);
  return normalizeWord(data);
}

export async function updateAdminLegalWord(
  wordId: string,
  payload: Partial<LegalWordPayload>,
): Promise<LegalWord> {
  const { data } = await api.patch(`/admin/legal-words/${wordId}`, payload);
  return normalizeWord(data);
}

export async function approveAdminLegalWord(wordId: string): Promise<LegalWord> {
  const { data } = await api.post(`/admin/legal-words/${wordId}/approve`);
  return normalizeWord(data);
}

export async function rejectAdminLegalWord(wordId: string): Promise<LegalWord> {
  const { data } = await api.post(`/admin/legal-words/${wordId}/reject`);
  return normalizeWord(data);
}

export async function deactivateAdminLegalWord(wordId: string): Promise<LegalWord> {
  const { data } = await api.post(`/admin/legal-words/${wordId}/deactivate`);
  return normalizeWord(data);
}

export async function scheduleAdminLegalWord(
  wordId: string,
  scheduledDate: string,
): Promise<LegalWord> {
  const { data } = await api.post(`/admin/legal-words/${wordId}/schedule`, {
    scheduled_date: scheduledDate,
  });
  return normalizeWord(data);
}

export async function suggestAdminLegalWordsWithAI(): Promise<LegalWordAISuggestionResponse> {
  const { data } = await api.post("/admin/legal-words/suggest-ai");
  return {
    ...data,
    suggestions: Array.isArray(data?.suggestions)
      ? data.suggestions.map(normalizeSuggestion)
      : [],
  };
}

export async function publishAdminLegalWord(
  wordId: string,
  publishedDate?: string,
): Promise<TodayLegalWordsResponse> {
  const { data } = await api.post(
    `/admin/legal-words/${wordId}/publish`,
    publishedDate ? { published_date: publishedDate } : undefined,
  );
  return {
    ...data,
    words: Array.isArray(data?.words) ? data.words.map(normalizeWord) : [],
  };
}
