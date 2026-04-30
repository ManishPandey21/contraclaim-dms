import React, { useState, useEffect } from "react";
import {
  Plus,
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
  const [tags, setTags] = useState<TagWithFrontendState[]>([]);
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

  const fetchTags = async () => {
    setLoading(true);
    try {
      const list = await listTags();
      const withSubtags = list.map((tag) => ({
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
  };

  useEffect(() => {
    fetchTags();
  }, []);

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
      fetchTags().catch(() => {});
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
    const input = (newSubTagNames[tagId] || "").trim();
    if (!input) {
      toast.error("Subtag name cannot be empty");
      return;
    }
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
    }
  };

  const saveTag = async (tagId: string, newName: string) => {
    try {
      await updateTagRequest(tagId, { name: newName });
      setTags((prev) =>
        prev.map((t) => (t._id === tagId ? { ...t, name: newName } : t))
      );

      toast.success("Tag updated");
    } catch (error) {
      logError(error, {
        scope: "TagsPage",
        action: "updateTag",
        metadata: { tagId },
      });
      toast.error("Error updating tag", {
        description: extractErrorMessage(error, "Unable to update tag."),
      });
    }
  };

  const saveSubTag = async (
    tagId: string,
    subTagId: string,
    newName: string
  ) => {
    try {
      await updateSubTagRequest(subTagId, { name: newName });
      setTags((prev) =>
        prev.map((t) =>
          t._id === tagId
            ? {
                ...t,
                subTags: t.subTags.map((st) =>
                  st._id === subTagId ? { ...st, name: newName } : st
                ),
              }
            : t
        )
      );

      toast.success("Subtag updated");
    } catch (error) {
      logError(error, {
        scope: "TagsPage",
        action: "updateSubTag",
        metadata: { tagId, subTagId },
      });
      toast.error("Error updating subtag", {
        description: extractErrorMessage(error, "Unable to update subtag."),
      });
    }
  };

  const saveEditing = async () => {
    if (editingTagId) {
      await saveTag(editingTagId, editingValue);
    } else if (editingSubTagId) {
      await saveSubTag(
        editingSubTagId.tagId,
        editingSubTagId.subTagId,
        editingValue
      );
    }
    cancelEditing();
  };

  const deleteTag = async (tagId: string) => {
    try {
      await deleteTagRequest(tagId);
      setTags((prev) => prev.filter((t) => t._id !== tagId));

      toast.success("Tag deleted");
    } catch (error) {
      logError(error, {
        scope: "TagsPage",
        action: "deleteTag",
        metadata: { tagId },
      });
      toast.error("Error deleting tag", {
        description: extractErrorMessage(error, "Unable to delete tag."),
      });
    }
  };

  const deleteSubTag = async (tagId: string, subTagId: string) => {
    try {
      await deleteSubTagRequest(subTagId);
      setTags((prev) =>
        prev.map((t) =>
          t._id === tagId
            ? { ...t, subTags: t.subTags.filter((st) => st._id !== subTagId) }
            : t
        )
      );

      toast.success("Subtag deleted");
    } catch (error) {
      logError(error, {
        scope: "TagsPage",
        action: "deleteSubTag",
        metadata: { tagId, subTagId },
      });
      toast.error("Error deleting subtag", {
        description: extractErrorMessage(error, "Unable to delete subtag."),
      });
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

  // Helper Components
  const TagActions = ({ tag }: { tag: TagWithFrontendState }) => (
    <div className="flex items-center gap-1">
      <Button
        variant="ghost"
        size="sm"
        className="h-7 w-7"
        onClick={() => startEditing(tag._id, tag.name)}
      >
        <Edit size={14} />
      </Button>
      <Button
        variant="ghost"
        size="sm"
        className="h-7 w-7"
        onClick={() => deleteTag(tag._id)}
      >
        <Trash size={14} />
      </Button>
    </div>
  );

  const SubTagActions = ({
    tagId,
    subTag,
  }: {
    tagId: string;
    subTag: SubTag;
  }) => (
    <div className="flex items-center gap-1">
      <Button
        variant="ghost"
        size="sm"
        className="h-7 w-7"
        onClick={() => startEditingSubTag(tagId, subTag._id, subTag.name)}
      >
        <Edit size={14} />
      </Button>
      <Button
        variant="ghost"
        size="sm"
        className="h-7 w-7"
        onClick={() => deleteSubTag(tagId, subTag._id)}
      >
        <Trash size={14} />
      </Button>
    </div>
  );

  const TagItem = ({ tag }: { tag: TagWithFrontendState }) => (
    <div key={tag._id} className="border-b last:border-b-0">
      <div className="flex items-center justify-between p-3 bg-gray-50">
        <div className="flex items-center">
          <Button
            variant="ghost"
            size="sm"
            className="p-1 h-7 w-7"
            onClick={() => toggleExpand(tag._id)}
          >
            {tag.isExpanded ? (
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
              >
                <Save size={14} className="mr-1" /> Save
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

        {editingTagId !== tag._id && <TagActions tag={tag} />}
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
                  >
                    <Save size={14} className="mr-1" /> Save
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
                <SubTagActions tagId={tag._id} subTag={subTag} />
              )}
            </div>
          ))}
          <div className="flex gap-2 ml-6">
            <Input
              placeholder="Enter subtag name"
              value={newSubTagNames[tag._id] ?? ""}
              onChange={(e) =>
                setNewSubTagNames((prev) => ({
                  ...prev,
                  [tag._id]: e.target.value,
                }))
              }
              className="flex-1"
            />
            <Button onClick={() => addSubTag(tag._id)}>
              <Plus size={16} className="mr-1" /> Add Subtag
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
                  <Plus size={16} className="mr-1" /> Add Tag
                </Button>
              </div>
            </div>
            {loading ? (
              <div className="flex justify-center items-center h-40">
                <Loader2 className="h-10 w-10 animate-spin" />
              </div>
            ) : (
              <div className="border rounded-md">
                {tags.map((tag) => (
                  <TagItem key={tag._id} tag={tag} />
                ))}
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
};

export default TagsPage;
