import React, { useState, useEffect, useCallback } from "react";
import {
  Plus,
  Search,
  ChevronLeft,
  ChevronRight,
  ChevronDown,
  Save,
  Edit,
  Trash,
  Tag as TagIcon,
  Loader2,
} from "lucide-react";
import {
  Card,
  CardHeader,
  CardTitle,
  CardDescription,
  CardContent,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { toast } from "sonner";
import {
  createSubTag,
  createTag,
  deleteSubTag as deleteSubTagRequest,
  deleteTag as deleteTagRequest,
  listSubTags,
  listTags,
  type SubTag,
  type Tag as MainTag,
  updateSubTag as updateSubTagRequest,
  updateTag as updateTagRequest,
} from "@/services/tags-api";
import { extractErrorMessage, logError } from "@/lib/error-logger";

interface TagWithFrontendState extends MainTag {
  subTags: SubTag[];
  isExpanded?: boolean;
  isEditing?: boolean;
  subtagsLoading?: boolean;
}

const TagsPage = () => {
  const tagsPerPage = 10;
  const [tags, setTags] = useState<TagWithFrontendState[]>([]);
  const [totalTags, setTotalTags] = useState(0);
  const [currentPage, setCurrentPage] = useState(1);
  const [tagSearchTerm, setTagSearchTerm] = useState("");
  const [newTagName, setNewTagName] = useState("");
  const [newSubTagNames, setNewSubTagNames] = useState<Record<string, string>>(
    {}
  );
  const [editingTagId, setEditingTagId] = useState<string | null>(null);
  const [editingSubTagId, setEditingSubTagId] = useState<{
    tagId: string;
    subTagId: string;
  } | null>(null);
  const [editingValue, setEditingValue] = useState("");
  const [loading, setLoading] = useState(false);
  const [operationLoading, setOperationLoading] = useState({
    addTag: false,
    deleteTag: false,
    updateTag: false,
  });
  const [subTagOperationLoading, setSubTagOperationLoading] = useState<
    Record<string, boolean>
  >({});

  const totalPages = Math.max(1, Math.ceil(totalTags / tagsPerPage));
  const firstResult = totalTags === 0 ? 0 : (currentPage - 1) * tagsPerPage + 1;
  const lastResult = Math.min(currentPage * tagsPerPage, totalTags);

  const setSubTagBusy = useCallback((key: string, busy: boolean) => {
    setSubTagOperationLoading((prev) => {
      if (busy) return { ...prev, [key]: true };
      const next = { ...prev };
      delete next[key];
      return next;
    });
  }, []);

  const isSubTagBusy = useCallback(
    (key: string) => Boolean(subTagOperationLoading[key]),
    [subTagOperationLoading]
  );

  const fetchTags = useCallback(async () => {
    setLoading(true);
    try {
      const result = await listTags({
        search: tagSearchTerm,
        page: currentPage,
        limit: tagsPerPage,
      });
      const nextTotalPages = Math.max(
        1,
        Math.ceil(result.total / tagsPerPage)
      );
      setTotalTags(result.total);
      if (result.total > 0 && currentPage > nextTotalPages) {
        setCurrentPage(nextTotalPages);
        return;
      }

      const withSubtags = result.tags.map((tag) => ({
        ...tag,
        subTags: [],
        isExpanded: false,
        isEditing: false,
        subtagsLoading: false,
      }));
      setTags(withSubtags);
    } catch (error) {
      logError(error, {
        scope: "TagsPage",
        action: "fetchTags",
      });
      toast.error("Failed to load tags", {
        description: extractErrorMessage(error, "Unable to load tags."),
      });
    } finally {
      setLoading(false);
    }
  }, [currentPage, tagSearchTerm]);

  useEffect(() => {
    fetchTags();
  }, [fetchTags]);

  const addNewTag = async () => {
    if (!newTagName.trim()) {
      toast.error("Tag name cannot be empty");
      return;
    }

    setOperationLoading((prev) => ({ ...prev, addTag: true }));
    try {
      await createTag({ name: newTagName.trim() });
      toast.success("Tag created");
      setNewTagName("");
      if (currentPage !== 1) {
        setCurrentPage(1);
      } else {
        fetchTags().catch(() => {});
      }
    } catch (error) {
      logError(error, {
        scope: "TagsPage",
        action: "createTag",
      });
      toast.error("Error creating tag", {
        description: extractErrorMessage(error, "Unable to create tag."),
      });
    } finally {
      setOperationLoading((prev) => ({ ...prev, addTag: false }));
    }
  };

  const addSubTag = async (tagId: string) => {
    const busyKey = `add:${tagId}`;
    if (isSubTagBusy(busyKey)) return;

    const input = (newSubTagNames[tagId] || "").trim();
    if (!input) {
      toast.error("Subtag name cannot be empty");
      return;
    }

    const selectedTag = tags.find((tag) => tag._id === tagId);
    if (
      selectedTag?.subTags.some(
        (subTag) => subTag.name.trim().toLowerCase() === input.toLowerCase()
      )
    ) {
      toast.error("Subtag already exists", {
        description: "Use a unique subtag name within this tag.",
      });
      return;
    }

    setSubTagBusy(busyKey, true);
    try {
      await createSubTag(tagId, { name: input });
      const latest = await listSubTags(tagId);
      setTags((prev) =>
        prev.map((t) =>
          t._id === tagId ? { ...t, subTags: latest, isExpanded: true } : t
        )
      );
      setNewSubTagNames((prev) => ({ ...prev, [tagId]: "" }));
      toast.success("Subtag added");
    } catch (error) {
      logError(error, {
        scope: "TagsPage",
        action: "createSubTag",
        metadata: { tagId },
      });
      toast.error("Error adding subtag", {
        description: extractErrorMessage(error, "Unable to add subtag."),
      });
    } finally {
      setSubTagBusy(busyKey, false);
    }
  };

  const saveTag = async (tagId: string, newName: string): Promise<boolean> => {
    const name = newName.trim();
    if (!name) {
      toast.error("Tag name cannot be empty");
      return false;
    }
    if (operationLoading.updateTag) return false;

    setOperationLoading((prev) => ({ ...prev, updateTag: true }));
    try {
      await updateTagRequest(tagId, { name });
      setTags((prev) =>
        prev.map((t) => (t._id === tagId ? { ...t, name } : t))
      );

      toast.success("Tag updated");
      return true;
    } catch (error) {
      logError(error, {
        scope: "TagsPage",
        action: "updateTag",
        metadata: { tagId },
      });
      toast.error("Error updating tag", {
        description: extractErrorMessage(error, "Unable to update tag."),
      });
      return false;
    } finally {
      setOperationLoading((prev) => ({ ...prev, updateTag: false }));
    }
  };

  const saveSubTag = async (
    tagId: string,
    subTagId: string,
    newName: string
  ): Promise<boolean> => {
    const name = newName.trim();
    if (!name) {
      toast.error("Subtag name cannot be empty");
      return false;
    }

    const busyKey = `update:${subTagId}`;
    if (isSubTagBusy(busyKey)) return false;

    setSubTagBusy(busyKey, true);
    try {
      await updateSubTagRequest(subTagId, { name });
      setTags((prev) =>
        prev.map((t) =>
          t._id === tagId
            ? {
                ...t,
                subTags: t.subTags.map((st) =>
                  st._id === subTagId ? { ...st, name } : st
                ),
              }
            : t
        )
      );

      toast.success("Subtag updated");
      return true;
    } catch (error) {
      logError(error, {
        scope: "TagsPage",
        action: "updateSubTag",
        metadata: { tagId, subTagId },
      });
      toast.error("Error updating subtag", {
        description: extractErrorMessage(error, "Unable to update subtag."),
      });
      return false;
    } finally {
      setSubTagBusy(busyKey, false);
    }
  };

  const saveEditing = async () => {
    let saved = false;
    if (editingTagId) {
      saved = await saveTag(editingTagId, editingValue);
    } else if (editingSubTagId) {
      saved = await saveSubTag(
        editingSubTagId.tagId,
        editingSubTagId.subTagId,
        editingValue
      );
    }
    if (saved) cancelEditing();
  };

  const deleteTag = async (tag: TagWithFrontendState) => {
    if (operationLoading.deleteTag) return;

    const confirmed = window.confirm(
      `Delete tag "${tag.name}"? This action cannot be undone.`
    );
    if (!confirmed) return;

    setOperationLoading((prev) => ({ ...prev, deleteTag: true }));
    try {
      await deleteTagRequest(tag._id);
      setTags((prev) => prev.filter((t) => t._id !== tag._id));
      const nextTotal = Math.max(0, totalTags - 1);
      const nextTotalPages = Math.max(1, Math.ceil(nextTotal / tagsPerPage));
      setTotalTags(nextTotal);
      if (currentPage > nextTotalPages) {
        setCurrentPage(nextTotalPages);
      } else {
        fetchTags().catch(() => {});
      }

      toast.success("Tag deleted");
    } catch (error) {
      logError(error, {
        scope: "TagsPage",
        action: "deleteTag",
        metadata: { tagId: tag._id },
      });
      toast.error("Error deleting tag", {
        description: extractErrorMessage(error, "Unable to delete tag."),
      });
    } finally {
      setOperationLoading((prev) => ({ ...prev, deleteTag: false }));
    }
  };

  const deleteSubTag = async (tagId: string, subTag: SubTag) => {
    const busyKey = `delete:${subTag._id}`;
    if (isSubTagBusy(busyKey)) return;

    const confirmed = window.confirm(
      `Delete subtag "${subTag.name}"? This action cannot be undone.`
    );
    if (!confirmed) return;

    setSubTagBusy(busyKey, true);
    try {
      await deleteSubTagRequest(subTag._id);
      setTags((prev) =>
        prev.map((t) =>
          t._id === tagId
            ? {
                ...t,
                subTags: t.subTags.filter((st) => st._id !== subTag._id),
              }
            : t
        )
      );

      toast.success("Subtag deleted");
    } catch (error) {
      logError(error, {
        scope: "TagsPage",
        action: "deleteSubTag",
        metadata: { tagId, subTagId: subTag._id },
      });
      toast.error("Error deleting subtag", {
        description: extractErrorMessage(error, "Unable to delete subtag."),
      });
    } finally {
      setSubTagBusy(busyKey, false);
    }
  };

  const startEditing = (tagId: string, value: string) => {
    setEditingTagId(tagId);
    setEditingValue(value);
  };

  const startEditingSubTag = (
    tagId: string,
    subTagId: string,
    value: string
  ) => {
    setEditingSubTagId({ tagId, subTagId });
    setEditingValue(value);
  };

  const cancelEditing = () => {
    setEditingTagId(null);
    setEditingSubTagId(null);
    setEditingValue("");
  };

  const toggleExpand = async (tagId: string) => {
    const selectedTag = tags.find((tag) => tag._id === tagId);
    if (!selectedTag) return;

    if (selectedTag.isExpanded) {
      setTags((prevTags) =>
        prevTags.map((tag) =>
          tag._id === tagId ? { ...tag, isExpanded: false } : tag
        )
      );
      return;
    }

    if (selectedTag.subTags.length > 0) {
      setTags((prevTags) =>
        prevTags.map((tag) =>
          tag._id === tagId ? { ...tag, isExpanded: true } : tag
        )
      );
      return;
    }

    setTags((prevTags) =>
      prevTags.map((tag) =>
        tag._id === tagId ? { ...tag, subtagsLoading: true } : tag
      )
    );

    try {
      const subTags = await listSubTags(tagId);
      setTags((prevTags) =>
        prevTags.map((tag) =>
          tag._id === tagId
            ? {
                ...tag,
                subTags,
                isExpanded: true,
                subtagsLoading: false,
              }
            : tag
        )
      );
    } catch (error) {
      logError(error, {
        scope: "TagsPage",
        action: "listSubTags",
        metadata: { tagId },
      });
      toast.error("Failed to load subtags", {
        description: extractErrorMessage(error, "Unable to load subtags."),
      });
      setTags((prevTags) =>
        prevTags.map((tag) =>
          tag._id === tagId ? { ...tag, subtagsLoading: false } : tag
        )
      );
    }
  };

  const getCreatedByLabelForTag = (tag: MainTag): string => {
    if (tag.created_by_label && tag.created_by_label.trim().length > 0) {
      return tag.created_by_label;
    }
    const vis = tag.visibility;
    if (vis === "global") return "System Created";
    if (vis === "organization")
      return `Created by ${tag.organization_name || "Organization"}`;
    if (vis === "project") return `Created by ${tag.project_name || "Project"}`;
    return "Created by";
  };

  const renderTagActions = (tag: TagWithFrontendState) => (
    <div className="flex items-center gap-1">
      <Button
        variant="ghost"
        size="sm"
        className="h-7 w-7"
        onClick={() => startEditing(tag._id, tag.name)}
        disabled={operationLoading.deleteTag || operationLoading.updateTag}
      >
        <Edit size={14} />
      </Button>
      <Button
        variant="ghost"
        size="sm"
        className="h-7 w-7"
        onClick={() => deleteTag(tag)}
        disabled={operationLoading.deleteTag}
      >
        {operationLoading.deleteTag ? (
          <Loader2 size={14} className="animate-spin" />
        ) : (
          <Trash size={14} />
        )}
      </Button>
    </div>
  );

  const renderSubTagActions = (tagId: string, subTag: SubTag) => (
    <div className="flex items-center gap-1">
      <Button
        variant="ghost"
        size="sm"
        className="h-7 w-7"
        onClick={() => startEditingSubTag(tagId, subTag._id, subTag.name)}
        disabled={
          isSubTagBusy(`update:${subTag._id}`) ||
          isSubTagBusy(`delete:${subTag._id}`)
        }
      >
        <Edit size={14} />
      </Button>
      <Button
        variant="ghost"
        size="sm"
        className="h-7 w-7"
        onClick={() => deleteSubTag(tagId, subTag)}
        disabled={isSubTagBusy(`delete:${subTag._id}`)}
      >
        {isSubTagBusy(`delete:${subTag._id}`) ? (
          <Loader2 size={14} className="animate-spin" />
        ) : (
          <Trash size={14} />
        )}
      </Button>
    </div>
  );

  const renderTagItem = (tag: TagWithFrontendState) => (
    <div key={tag._id} className="border-b last:border-b-0">
      <div className="flex items-center justify-between p-3 bg-gray-50">
        <div className="flex items-center">
          <Button
            variant="ghost"
            size="sm"
            className="p-1 h-7 w-7"
            onClick={() => toggleExpand(tag._id)}
            disabled={tag.subtagsLoading}
          >
            {tag.subtagsLoading ? (
              <Loader2 size={18} className="animate-spin" />
            ) : tag.isExpanded ? (
              <ChevronDown size={18} />
            ) : (
              <ChevronRight size={18} />
            )}
          </Button>

          {editingTagId === tag._id ? (
            <div className="flex items-center gap-2 ml-1">
              <Input
                value={editingValue}
                onChange={(e) => setEditingValue(e.target.value)}
                className="h-8 py-1"
                autoFocus
              />
              <Button
                size="sm"
                variant="outline"
                className="h-8"
                onClick={saveEditing}
                disabled={operationLoading.updateTag}
              >
                {operationLoading.updateTag ? (
                  <Loader2 size={14} className="mr-1 animate-spin" />
                ) : (
                  <Save size={14} className="mr-1" />
                )}
                Save
              </Button>
              <Button
                size="sm"
                variant="ghost"
                className="h-8"
                onClick={cancelEditing}
              >
                Cancel
              </Button>
            </div>
          ) : (
            <span className="font-medium flex items-center">
              {tag.name}
              <span className="text-xs text-gray-500 ml-2 font-normal">
                {getCreatedByLabelForTag(tag)}
              </span>
            </span>
          )}
        </div>

        {editingTagId !== tag._id && renderTagActions(tag)}
      </div>

      {tag.isExpanded && (
        <div className="p-3 space-y-2">
          {tag.subTags.map((subTag) => (
            <div
              key={subTag._id}
              className="flex items-center justify-between border-b last:border-b-0"
            >
              {editingSubTagId?.subTagId === subTag._id ? (
                <div className="flex items-center gap-2 ml-1">
                  <Input
                    value={editingValue}
                    onChange={(e) => setEditingValue(e.target.value)}
                    className="h-8 py-1"
                    autoFocus
                  />
                  <Button
                    size="sm"
                    variant="outline"
                    className="h-8"
                    onClick={saveEditing}
                    disabled={isSubTagBusy(`update:${subTag._id}`)}
                  >
                    {isSubTagBusy(`update:${subTag._id}`) ? (
                      <Loader2 size={14} className="mr-1 animate-spin" />
                    ) : (
                      <Save size={14} className="mr-1" />
                    )}
                    Save
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    className="h-8"
                    onClick={cancelEditing}
                  >
                    Cancel
                  </Button>
                </div>
              ) : (
                <span className="ml-6 flex items-center py-2">
                  <span>{subTag.name}</span>
                  <span className="text-xs text-gray-500 ml-2">
                    {subTag.created_by_label || getCreatedByLabelForTag(tag)}
                  </span>
                </span>
              )}

              {editingSubTagId?.subTagId !== subTag._id && (
                renderSubTagActions(tag._id, subTag)
              )}
            </div>
          ))}
          <div className="flex gap-2 ml-6">
            <Input
              placeholder="Enter subtag name"
              value={newSubTagNames[tag._id] ?? ""}
              disabled={isSubTagBusy(`add:${tag._id}`)}
              onChange={(e) =>
                setNewSubTagNames((prev) => ({
                  ...prev,
                  [tag._id]: e.target.value,
                }))
              }
              className="flex-1"
            />
            <Button
              onClick={() => addSubTag(tag._id)}
              disabled={isSubTagBusy(`add:${tag._id}`)}
            >
              {isSubTagBusy(`add:${tag._id}`) ? (
                <Loader2 size={16} className="mr-1 animate-spin" />
              ) : (
                <Plus size={16} className="mr-1" />
              )}
              Add Subtag
            </Button>
          </div>
        </div>
      )}
    </div>
  );

  return (
    <div className="space-y-6 animate-fade-in">
      <div className="flex justify-between items-center">
        <div>
          <h1 className="text-2xl font-semibold text-docsumo-text">
            Document Tags
          </h1>
          <p className="text-sm text-gray-500 mt-1">
            Manage document categorization with tags and sub-tags
          </p>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        <Card className="col-span-1 md:col-span-2">
          <CardHeader className="pb-3">
            <CardTitle className="text-lg flex items-center">
              <TagIcon className="mr-2" size={20} /> Tag Management
            </CardTitle>
            <CardDescription>
              Create, edit, and organize document tags
            </CardDescription>
          </CardHeader>
          <CardContent>
            <div className="mb-4 flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
              <div className="relative w-full md:max-w-sm">
                <Search
                  size={16}
                  className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400"
                />
                <Input
                  placeholder="Search tags"
                  value={tagSearchTerm}
                  onChange={(e) => {
                    setTagSearchTerm(e.target.value);
                    setCurrentPage(1);
                  }}
                  className="pl-9"
                />
              </div>
              <div className="text-sm text-gray-500">
                {totalTags > 0
                  ? `Showing ${firstResult}-${lastResult} of ${totalTags}`
                  : "No tags to show"}
              </div>
            </div>

            <div className="mb-6 bg-gray-50 p-4 rounded-lg">
              <h3 className="text-md font-medium mb-3">Add New Tag</h3>
              <div className="flex gap-2">
                <Input
                  placeholder="Enter tag name"
                  value={newTagName}
                  onChange={(e) => setNewTagName(e.target.value)}
                  className="flex-1"
                />
                <Button onClick={addNewTag} disabled={operationLoading.addTag}>
                  {operationLoading.addTag ? (
                    <Loader2 size={16} className="mr-1 animate-spin" />
                  ) : (
                    <Plus size={16} className="mr-1" />
                  )}
                  Add Tag
                </Button>
              </div>
            </div>
            {loading ? (
              <div className="flex justify-center items-center h-40">
                <Loader2 className="h-10 w-10 animate-spin" />
              </div>
            ) : (
              <div className="border rounded-md">
                {tags.length === 0 ? (
                  <div className="flex flex-col items-center justify-center px-4 py-12 text-center">
                    <TagIcon size={28} className="mb-3 text-gray-400" />
                    <h3 className="text-base font-medium">
                      {tagSearchTerm.trim() ? "No Matching Tags" : "No Tags Yet"}
                    </h3>
                    <p className="mt-1 max-w-md text-sm text-gray-500">
                      {tagSearchTerm.trim()
                        ? "No tags match your search. Try a different term or clear the search field."
                        : "Create a tag to start categorizing documents."}
                    </p>
                  </div>
                ) : (
                  tags.map((tag) => renderTagItem(tag))
                )}
              </div>
            )}

            {!loading && totalTags > tagsPerPage && (
              <div className="mt-4 flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
                <div className="text-sm text-gray-500">
                  Page {currentPage} of {totalPages}
                </div>
                <div className="flex items-center gap-2">
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    onClick={() => setCurrentPage((page) => Math.max(1, page - 1))}
                    disabled={currentPage === 1}
                  >
                    <ChevronLeft size={16} className="mr-1" />
                    Previous
                  </Button>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    onClick={() =>
                      setCurrentPage((page) => Math.min(totalPages, page + 1))
                    }
                    disabled={currentPage >= totalPages}
                  >
                    Next
                    <ChevronRight size={16} className="ml-1" />
                  </Button>
                </div>
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
};

export default TagsPage;
