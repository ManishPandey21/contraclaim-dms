import { api } from "./api";

export interface SubTag {
  _id: string;
  name: string;
  tag_id: string;
  created_by?: string;
  createdBy?: string;
  created_at: string;
  updated_at: string;
  created_by_label?: string;
}

export interface Tag {
  _id: string;
  name: string;
  organization_id: string;
  created_by: string;
  createdBy?: string;
  created_at: string;
  updated_at: string;
  visibility?: "global" | "organization" | "project";
  project_id?: string;
  organization_name?: string;
  project_name?: string;
  created_by_label?: string;
}

export interface TagsListResult {
  tags: Tag[];
  total: number;
  page: number;
  limit: number;
  has_next: boolean;
  has_prev: boolean;
}

export interface ListTagsParams {
  search?: string;
  page?: number;
  limit?: number;
}

export interface ListSubTagsParams {
  page?: number;
  limit?: number;
}

const normalizeTag = (tag: any): Tag => ({
  _id: tag?._id ?? tag?.id ?? "",
  name: tag?.name ?? "",
  organization_id: tag?.organization_id ?? tag?.organizationId ?? "",
  created_by: tag?.created_by ?? tag?.createdBy ?? "",
  createdBy: tag?.createdBy,
  created_at: tag?.created_at ?? "",
  updated_at: tag?.updated_at ?? "",
  visibility: tag?.visibility,
  project_id: tag?.project_id ?? tag?.projectId,
  organization_name: tag?.organization_name,
  project_name: tag?.project_name,
  created_by_label: tag?.created_by_label,
});

const normalizeSubTag = (subTag: any): SubTag => ({
  _id: subTag?._id ?? subTag?.id ?? "",
  name: subTag?.name ?? "",
  tag_id: subTag?.tag_id ?? subTag?.tagId ?? "",
  created_by: subTag?.created_by,
  createdBy: subTag?.createdBy,
  created_at: subTag?.created_at ?? "",
  updated_at: subTag?.updated_at ?? "",
  created_by_label: subTag?.created_by_label,
});

const normalizeList = <T>(
  payload: unknown,
  collectionKey: string,
  normalizer: (item: any) => T
): T[] => {
  const list = Array.isArray(payload)
    ? payload
    : Array.isArray((payload as Record<string, unknown> | null)?.[collectionKey])
    ? ((payload as Record<string, unknown>)[collectionKey] as any[])
    : [];

  return list.map(normalizer);
};

export async function listTags(
  params: ListTagsParams = {}
): Promise<TagsListResult> {
  const limit = Math.max(params.limit ?? 50, 1);
  const page = Math.max(params.page ?? 1, 1);
  const requestParams = {
    skip: (page - 1) * limit,
    limit,
    ...(params.search?.trim() ? { search: params.search.trim() } : {}),
  };
  const { data } = await api.get("/tags", { params: requestParams });
  const tags = normalizeList(data, "tags", normalizeTag);
  const total =
    typeof data?.total === "number" ? data.total : Array.isArray(data) ? data.length : tags.length;
  const responsePage =
    typeof data?.page === "number" && data.page > 0 ? data.page : page;
  const responseLimit =
    typeof data?.limit === "number" && data.limit > 0 ? data.limit : limit;

  return {
    tags,
    total,
    page: responsePage,
    limit: responseLimit,
    has_next:
      typeof data?.has_next === "boolean"
        ? data.has_next
        : responsePage * responseLimit < total,
    has_prev:
      typeof data?.has_prev === "boolean" ? data.has_prev : responsePage > 1,
  };
}

export async function createTag(payload: { name: string }): Promise<Tag> {
  const { data } = await api.post("/tags", payload);
  return normalizeTag(data);
}

export async function updateTag(
  tagId: string,
  payload: { name: string }
): Promise<Tag> {
  const { data } = await api.put(`/tags/${tagId}`, payload);
  return normalizeTag(data);
}

export async function deleteTag(tagId: string): Promise<void> {
  await api.delete(`/tags/${tagId}`);
}

export async function listSubTags(
  tagId: string,
  params: ListSubTagsParams = {}
): Promise<SubTag[]> {
  const limit = Math.max(params.limit ?? 200, 1);
  const page = Math.max(params.page ?? 1, 1);
  const { data } = await api.get(`/tags/${tagId}/subtags`, {
    params: {
      skip: (page - 1) * limit,
      limit,
    },
  });
  return normalizeList(data, "subtags", normalizeSubTag);
}

export async function createSubTag(
  tagId: string,
  payload: { name: string }
): Promise<SubTag> {
  const { data } = await api.post(`/tags/${tagId}/subtags`, payload);
  return normalizeSubTag(data);
}

export async function updateSubTag(
  subTagId: string,
  payload: { name: string }
): Promise<SubTag> {
  const { data } = await api.put(`/subtags/${subTagId}`, payload);
  return normalizeSubTag(data);
}

export async function deleteSubTag(subTagId: string): Promise<void> {
  await api.delete(`/subtags/${subTagId}`);
}
