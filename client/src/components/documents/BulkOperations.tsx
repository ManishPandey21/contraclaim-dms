import React, { useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import Badge from "@/components/ui/badge";
import {
  Trash2,
  Download,
  Tag,
  Move,
  Archive,
  Share2,
  CheckSquare,
  Square,
  MoreHorizontal,
  X,
  AlertTriangle,
  Loader2,
} from "lucide-react";

export interface BulkOperationItem {
  _id: string;
  name: string;
  filename: string;
  size?: number;
  categories?: string[];
  organization_id?: string;
  project_id?: string;
}

interface BulkOperationsProps {
  selectedItems: BulkOperationItem[];
  onSelectionChange: (items: BulkOperationItem[]) => void;
  onBulkDelete: (items: BulkOperationItem[]) => Promise<void>;
  onBulkDownload: (items: BulkOperationItem[]) => Promise<void>;
  onBulkCategorize: (
    items: BulkOperationItem[],
    categories: string[]
  ) => Promise<void>;
  onBulkMove: (
    items: BulkOperationItem[],
    organizationId: string,
    projectId?: string
  ) => Promise<void>;
  availableCategories: string[];
  availableOrganizations: Array<{ _id: string; name: string }>;
  availableProjects: Array<{
    _id: string;
    name: string;
    organization_id: string;
  }>;
}

type BulkAction =
  | "delete"
  | "download"
  | "categorize"
  | "move"
  | "archive"
  | "share";

export const BulkOperations: React.FC<BulkOperationsProps> = ({
  selectedItems,
  onSelectionChange,
  onBulkDelete,
  onBulkDownload,
  onBulkCategorize,
  onBulkMove,
  availableCategories,
  availableOrganizations,
  availableProjects,
}) => {
  const [showActions, setShowActions] = useState(false);
  const [currentAction, setCurrentAction] = useState<BulkAction | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Categorize action state
  const [selectedCategories, setSelectedCategories] = useState<string[]>([]);

  // Move action state
  const [selectedOrganization, setSelectedOrganization] = useState("");
  const [selectedProject, setSelectedProject] = useState("");

  const handleSelectAll = () => {
    // This would typically be called from parent with all available items
    // For now, we'll just clear selection as an example
    onSelectionChange([]);
  };

  const handleDeselectAll = () => {
    onSelectionChange([]);
  };

  const handleRemoveItem = (itemId: string) => {
    const updatedItems = selectedItems.filter((item) => item._id !== itemId);
    onSelectionChange(updatedItems);
  };

  const handleAction = async (action: BulkAction) => {
    if (selectedItems.length === 0) return;

    setCurrentAction(action);
    setError(null);

    if (action === "delete" || action === "download") {
      setShowActions(true);
    } else {
      setShowActions(true);
    }
  };

  const executeAction = async () => {
    if (!currentAction || selectedItems.length === 0) return;

    setLoading(true);
    setError(null);

    try {
      switch (currentAction) {
        case "delete":
          await onBulkDelete(selectedItems);
          break;
        case "download":
          await onBulkDownload(selectedItems);
          break;
        case "categorize":
          if (selectedCategories.length === 0) {
            setError("Please select at least one category");
            return;
          }
          await onBulkCategorize(selectedItems, selectedCategories);
          break;
        case "move":
          if (!selectedOrganization) {
            setError("Please select an organization");
            return;
          }
          await onBulkMove(
            selectedItems,
            selectedOrganization,
            selectedProject || undefined
          );
          break;
        default:
          throw new Error(`Action ${currentAction} not implemented`);
      }

      // Reset state after successful action
      setCurrentAction(null);
      setShowActions(false);
      setSelectedCategories([]);
      setSelectedOrganization("");
      setSelectedProject("");
      onSelectionChange([]);
    } catch (err) {
      console.error(`Bulk ${currentAction} failed:`, err);
      setError(`Failed to ${currentAction} documents. Please try again.`);
    } finally {
      setLoading(false);
    }
  };

  const cancelAction = () => {
    setCurrentAction(null);
    setShowActions(false);
    setSelectedCategories([]);
    setSelectedOrganization("");
    setSelectedProject("");
    setError(null);
  };

  const formatFileSize = (bytes?: number) => {
    if (!bytes) return "Unknown";
    const sizes = ["B", "KB", "MB", "GB"];
    const i = Math.floor(Math.log(bytes) / Math.log(1024));
    return `${(bytes / Math.pow(1024, i)).toFixed(1)} ${sizes[i]}`;
  };

  const getTotalSize = () => {
    return selectedItems.reduce((total, item) => total + (item.size || 0), 0);
  };

  const filteredProjects = availableProjects.filter(
    (project) => project.organization_id === selectedOrganization
  );

  if (selectedItems.length === 0) {
    return null;
  }

  return (
    <Card className="border-blue-200 bg-blue-50">
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between">
          <CardTitle className="text-lg flex items-center gap-2">
            <CheckSquare className="h-5 w-5 text-blue-600" />
            {selectedItems.length} document{selectedItems.length > 1 ? "s" : ""}{" "}
            selected
          </CardTitle>
          <div className="flex items-center gap-2">
            <button
              onClick={handleDeselectAll}
              className="text-sm text-gray-600 hover:text-gray-800"
            >
              Clear selection
            </button>
            <button
              onClick={() => setShowActions(!showActions)}
              className="p-1 hover:bg-blue-100 rounded"
            >
              <MoreHorizontal className="h-4 w-4" />
            </button>
          </div>
        </div>
        {getTotalSize() > 0 && (
          <p className="text-sm text-gray-600">
            Total size: {formatFileSize(getTotalSize())}
          </p>
        )}
      </CardHeader>

      <CardContent className="space-y-4">
        {/* Selected Items List */}
        <div className="max-h-32 overflow-y-auto space-y-2">
          {selectedItems.map((item) => (
            <div
              key={item._id}
              className="flex items-center justify-between p-2 bg-white rounded border"
            >
              <div className="flex-1 min-w-0">
                <p className="text-sm font-medium text-gray-900 truncate">
                  {item.name}
                </p>
                <div className="flex items-center gap-2 mt-1">
                  <p className="text-xs text-gray-500">{item.filename}</p>
                  {item.size && (
                    <span className="text-xs text-gray-400">
                      {formatFileSize(item.size)}
                    </span>
                  )}
                </div>
                {item.categories && item.categories.length > 0 && (
                  <div className="flex flex-wrap gap-1 mt-1">
                    {item.categories.slice(0, 3).map((category) => (
                      <Badge
                        key={category}
                        variant="outline"
                        className="text-xs"
                      >
                        {category}
                      </Badge>
                    ))}
                    {item.categories.length > 3 && (
                      <Badge variant="outline" className="text-xs">
                        +{item.categories.length - 3}
                      </Badge>
                    )}
                  </div>
                )}
              </div>
              <button
                onClick={() => handleRemoveItem(item._id)}
                className="p-1 text-gray-400 hover:text-red-500 ml-2"
              >
                <X className="h-3 w-3" />
              </button>
            </div>
          ))}
        </div>

        {/* Quick Actions */}
        <div className="flex flex-wrap gap-2">
          <button
            onClick={() => handleAction("download")}
            disabled={loading}
            className="flex items-center gap-1 px-3 py-1.5 bg-blue-600 text-white text-sm rounded hover:bg-blue-700 disabled:opacity-50"
          >
            <Download className="h-3 w-3" />
            Download All
          </button>
          <button
            onClick={() => handleAction("categorize")}
            disabled={loading}
            className="flex items-center gap-1 px-3 py-1.5 bg-green-600 text-white text-sm rounded hover:bg-green-700 disabled:opacity-50"
          >
            <Tag className="h-3 w-3" />
            Categorize
          </button>
          <button
            onClick={() => handleAction("move")}
            disabled={loading}
            className="flex items-center gap-1 px-3 py-1.5 bg-purple-600 text-white text-sm rounded hover:bg-purple-700 disabled:opacity-50"
          >
            <Move className="h-3 w-3" />
            Move
          </button>
          <button
            onClick={() => handleAction("delete")}
            disabled={loading}
            className="flex items-center gap-1 px-3 py-1.5 bg-red-600 text-white text-sm rounded hover:bg-red-700 disabled:opacity-50"
          >
            <Trash2 className="h-3 w-3" />
            Delete
          </button>
        </div>

        {/* Action Dialogs */}
        {showActions && currentAction && (
          <div className="border-t pt-4 space-y-4">
            {currentAction === "delete" && (
              <div className="bg-red-50 border border-red-200 rounded p-4">
                <div className="flex items-start gap-3">
                  <AlertTriangle className="h-5 w-5 text-red-500 mt-0.5" />
                  <div className="flex-1">
                    <h4 className="font-medium text-red-800">
                      Confirm Deletion
                    </h4>
                    <p className="text-sm text-red-700 mt-1">
                      Are you sure you want to delete {selectedItems.length}{" "}
                      document{selectedItems.length > 1 ? "s" : ""}? This action
                      cannot be undone.
                    </p>
                  </div>
                </div>
              </div>
            )}

            {currentAction === "categorize" && (
              <div className="space-y-3">
                <h4 className="font-medium text-gray-900">Add Categories</h4>
                <div className="flex flex-wrap gap-2">
                  {availableCategories.map((category) => (
                    <button
                      key={category}
                      onClick={() => {
                        if (selectedCategories.includes(category)) {
                          setSelectedCategories((prev) =>
                            prev.filter((c) => c !== category)
                          );
                        } else {
                          setSelectedCategories((prev) => [...prev, category]);
                        }
                      }}
                      className={`px-3 py-1 text-sm rounded transition-colors ${
                        selectedCategories.includes(category)
                          ? "bg-blue-100 text-blue-700 border border-blue-200"
                          : "bg-gray-100 text-gray-700 border border-gray-200 hover:bg-gray-200"
                      }`}
                    >
                      {category}
                    </button>
                  ))}
                </div>
                {selectedCategories.length > 0 && (
                  <p className="text-sm text-gray-600">
                    Selected: {selectedCategories.join(", ")}
                  </p>
                )}
              </div>
            )}

            {currentAction === "move" && (
              <div className="space-y-3">
                <h4 className="font-medium text-gray-900">Move Documents</h4>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">
                      Organization *
                    </label>
                    <select
                      value={selectedOrganization}
                      onChange={(e) => {
                        setSelectedOrganization(e.target.value);
                        setSelectedProject(""); // Reset project when org changes
                      }}
                      className="w-full px-3 py-2 border border-gray-300 rounded text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                    >
                      <option value="">Select organization</option>
                      {availableOrganizations.map((org) => (
                        <option key={org._id} value={org._id}>
                          {org.name}
                        </option>
                      ))}
                    </select>
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">
                      Project (optional)
                    </label>
                    <select
                      value={selectedProject}
                      onChange={(e) => setSelectedProject(e.target.value)}
                      disabled={!selectedOrganization}
                      className="w-full px-3 py-2 border border-gray-300 rounded text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:bg-gray-50"
                    >
                      <option value="">Select project</option>
                      {filteredProjects.map((project) => (
                        <option key={project._id} value={project._id}>
                          {project.name}
                        </option>
                      ))}
                    </select>
                  </div>
                </div>
              </div>
            )}

            {error && (
              <div className="bg-red-50 border border-red-200 rounded p-3">
                <p className="text-sm text-red-700">{error}</p>
              </div>
            )}

            <div className="flex justify-end gap-2">
              <button
                onClick={cancelAction}
                disabled={loading}
                className="px-4 py-2 text-sm border border-gray-300 rounded hover:bg-gray-50 disabled:opacity-50"
              >
                Cancel
              </button>
              <button
                onClick={executeAction}
                disabled={loading}
                className={`px-4 py-2 text-sm rounded text-white disabled:opacity-50 flex items-center gap-2 ${
                  currentAction === "delete"
                    ? "bg-red-600 hover:bg-red-700"
                    : "bg-blue-600 hover:bg-blue-700"
                }`}
              >
                {loading && <Loader2 className="h-3 w-3 animate-spin" />}
                {currentAction === "delete" && "Delete Documents"}
                {currentAction === "download" && "Download All"}
                {currentAction === "categorize" && "Apply Categories"}
                {currentAction === "move" && "Move Documents"}
              </button>
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
};

export default BulkOperations;
