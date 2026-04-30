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

// Define interfaces to match the backend
interface SubTag {
  _id: string;
  name: string;
  tag_id: string;
  created_by: string;
  created_at: string;
}

interface MainTag {
  _id: string;
  name: string;
  organization_id: string;
  created_by: string;
  created_at: string;
}

interface TagWithFrontendState extends MainTag {
  subTags: SubTag[];
  isExpanded?: boolean;
  isEditing?: boolean;
  subtagsLoading?: boolean;
}

const TagsPage = () => {
  const [tags, setTags] = useState<TagWithFrontendState[]>([]);
  const [newTagName, setNewTagName] = useState("");
  const [newSubTagName, setNewSubTagName] = useState("");
  const [editingTagId, setEditingTagId] = useState<string | null>(null);
  const [editingSubTagId, setEditingSubTagId] = useState<{
    tagId: string;
    subTagId: string;
  } | null>(null);
  const [editingValue, setEditingValue] = useState("");
  const [loading, setLoading] = useState(false);

  const fetchTags = async () => {
    setLoading(true);
    try {
      const res = await fetch("/tags", {
        // Corrected endpoint
        headers: {
          Authorization: `Bearer ${localStorage.getItem("accessToken")}`,
        },
      });
      if (!res.ok) throw new Error("Failed to load tags");
      const data: MainTag[] = await res.json();
      const withSubtags = data.map((tag) => ({
        ...tag,
        subTags: [],
        isExpanded: false,
        isEditing: false,
        subtagsLoading: false,
      }));
      setTags(withSubtags);
    } catch (e: any) {
      toast.error("Failed to load tags", { description: e.message });
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchTags();
  }, []);

  const addNewTag = async () => {
    try {
      const res = await fetch("/api/tags", {
        method: "POST",
        headers: {
          "content-type": "application/json",
          Authorization: `Bearer ${localStorage.getItem("accessToken")}`,
        },
        body: JSON.stringify({ name: newTagName }),
      });
      if (!res.ok) throw new Error("Failed to create tag");
      toast.success("Tag created");
      setNewTagName("");
      fetchTags();
    } catch (e: any) {
      toast.error("Error creating tag", { description: e.message });
    }
  };

  const addSubTag = async (tagId: string) => {
    try {
      const res = await fetch(`/api/tags/${tagId}/subtags`, {
        // Corrected endpoint
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${localStorage.getItem("accessToken")}`,
        },
        body: JSON.stringify({ name: newSubTagName }),
      });
      if (!res.ok) throw new Error("Failed to add subtag");
      toast.success("Subtag added");
      setNewSubTagName("");
      fetchTags();
    } catch (e: any) {
      toast.error("Error adding subtag", { description: e.message });
    }
  };

  const saveTag = async (tagId: string, newName: string) => {
    try {
      const res = await fetch(`/api/tags/${tagId}`, {
        // Corrected endpoint
        method: "PUT",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${localStorage.getItem("accessToken")}`,
        },
        body: JSON.stringify({ name: newName }),
      });
      if (!res.ok) throw new Error("Failed to update tag");
      toast.success("Tag updated");
      fetchTags();
    } catch (e: any) {
      toast.error("Error updating tag", { description: e.message });
    }
  };

  const saveSubTag = async (
    tagId: string,
    subTagId: string,
    newName: string
  ) => {
    try {
      const res = await fetch(`/api/subtags/${subTagId}`, {
        // Corrected endpoint
        method: "PUT",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${localStorage.getItem("accessToken")}`,
        },
        body: JSON.stringify({ name: newName }),
      });
      if (!res.ok) throw new Error("Failed to update subtag");
      toast.success("Subtag updated");
      fetchTags();
    } catch (e: any) {
      toast.error("Error updating subtag", { description: e.message });
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
      const res = await fetch(`/api/tags/${tagId}`, {
        // Corrected endpoint
        method: "DELETE",
        headers: {
          Authorization: `Bearer ${localStorage.getItem("accessToken")}`,
        },
      });
      if (!res.ok) throw new Error("Failed to delete tag");
      toast.success("Tag deleted");
      fetchTags();
    } catch (e: any) {
      toast.error("Error deleting tag", { description: e.message });
    }
  };

  const deleteSubTag = async (tagId: string, subTagId: string) => {
    try {
      const res = await fetch(`/api/subtags/${subTagId}`, {
        // Corrected endpoint
        method: "DELETE",
        headers: {
          Authorization: `Bearer ${localStorage.getItem("accessToken")}`,
        },
      });
      if (!res.ok) throw new Error("Failed to delete subtag");
      toast.success("Subtag deleted");
      fetchTags();
    } catch (e: any) {
      toast.error("Error deleting subtag", { description: e.message });
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
    setTags(
      tags.map((tag) => {
        if (tag._id === tagId) {
          if (!tag.isExpanded && !tag.subtagsLoading) {
            // Fetch subtags only when expanding and not already loading
            const fetchSubtags = async () => {
              setTags((prevTags) =>
                prevTags.map((prevTag) =>
                  prevTag._id === tagId
                    ? { ...prevTag, subtagsLoading: true }
                    : prevTag
                )
              );
              try {
                const subRes = await fetch(`/api/tags/${tagId}/subtags`, {
                  headers: {
                    Authorization: `Bearer ${localStorage.getItem(
                      "accessToken"
                    )}`,
                  },
                });
                if (!subRes.ok) throw new Error("Failed to load subtags");
                const subTags: SubTag[] = await subRes.json();
                setTags((prevTags) =>
                  prevTags.map((prevTag) =>
                    prevTag._id === tagId
                      ? {
                          ...prevTag,
                          subTags,
                          isExpanded: true,
                          subtagsLoading: false,
                        }
                      : prevTag
                  )
                );
              } catch (e: any) {
                toast.error("Failed to load subtags", {
                  description: e.message,
                });
                setTags((prevTags) =>
                  prevTags.map((prevTag) =>
                    prevTag._id === tagId
                      ? { ...prevTag, subtagsLoading: false }
                      : prevTag
                  )
                );
              }
            };
            fetchSubtags();
          } else {
            // Toggle isExpanded if already fetched or loading
            return { ...tag, isExpanded: !tag.isExpanded };
          }
        }
        return tag;
      })
    );
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
            <span className="font-medium">{tag.name}</span>
          )}
        </div>

        {editingTagId !== tag._id && <TagActions tag={tag} />}
      </div>

      {tag.isExpanded && (
        <div className="p-3 space-y-2">
          {tag.subTags.map((subTag) => (
            <div key={subTag._id} className="flex items-center justify-between">
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
                <span className="ml-6">{subTag.name}</span>
              )}

              {editingSubTagId?.subTagId !== subTag._id && (
                <SubTagActions tagId={tag._id} subTag={subTag} />
              )}
            </div>
          ))}
          <div className="flex gap-2 ml-6">
            <Input
              placeholder="Enter subtag name"
              value={newSubTagName}
              onChange={(e) => setNewSubTagName(e.target.value)}
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
                <Button onClick={addNewTag}>
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
                  <TagItem tag={tag} />
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
