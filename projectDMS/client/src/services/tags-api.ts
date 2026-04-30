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

export async function listTags(): Promise<Tag[]> {
  const { data } = await api.get("/tags");
  return normalizeList(data, "tags", normalizeTag);
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

export async function listSubTags(tagId: string): Promise<SubTag[]> {
  const { data } = await api.get(`/tags/${tagId}/subtags`);
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
