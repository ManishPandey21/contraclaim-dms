import React, { useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import Badge from "@/components/ui/badge";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Calendar,
  Filter,
  X,
  ChevronDown,
  ChevronUp,
  Search,
  FileText,
  Building2,
  FolderOpen,
} from "lucide-react";

export interface SearchFilters {
  query: string;
  dateRange: {
    from?: string;
    to?: string;
  };
  fileTypes: string[];
  organizations: string[];
  projects: string[];
  categories: string[];
  sortBy: "relevance" | "date" | "name" | "size";
  sortOrder: "asc" | "desc";

  // Single-select helpers for dropdown UX
  selectedOrganization?: string;
  selectedProject?: string;

  // Additional filters
  tag?: string;
  direction?: "incoming" | "outgoing";
}

interface AdvancedSearchFiltersProps {
  filters: SearchFilters;
  onFiltersChange: (filters: SearchFilters) => void;
  availableOrganizations: Array<{ _id: string; name: string }>;
  availableProjects: Array<{ _id: string; name: string }>;
  availableCategories: string[];
  availableTags?: Array<{ _id: string; name: string }>;
  isExpanded?: boolean;
  onToggleExpanded?: () => void;
}

const FILE_TYPES = [
  { value: "pdf", label: "PDF", icon: "📄" },
  { value: "doc", label: "DOC", icon: "📝" },
  { value: "docx", label: "DOCX", icon: "📝" },
  { value: "txt", label: "TXT", icon: "📄" },
  { value: "xlsx", label: "XLSX", icon: "📊" },
  { value: "pptx", label: "PPTX", icon: "📊" },
];

const SORT_OPTIONS = [
  { value: "relevance", label: "Relevance" },
  { value: "date", label: "Date Modified" },
  { value: "name", label: "Name" },
  { value: "size", label: "File Size" },
];

export const AdvancedSearchFilters: React.FC<AdvancedSearchFiltersProps> = ({
  filters,
  onFiltersChange,
  availableOrganizations,
  availableProjects,
  availableCategories,
  availableTags,
  isExpanded = false,
  onToggleExpanded,
}) => {
  const [localFilters, setLocalFilters] = useState<SearchFilters>(filters);

  const updateFilters = (updates: Partial<SearchFilters>) => {
    const newFilters = { ...localFilters, ...updates };
    setLocalFilters(newFilters);
    onFiltersChange(newFilters);
  };

  const toggleArrayFilter = (
    key: keyof Pick<
      SearchFilters,
      "fileTypes" | "organizations" | "projects" | "categories"
    >,
    value: string
  ) => {
    const currentArray = localFilters[key] as string[];
    const newArray = currentArray.includes(value)
      ? currentArray.filter((item) => item !== value)
      : [...currentArray, value];
    updateFilters({ [key]: newArray });
  };

  const clearAllFilters = () => {
    const clearedFilters: SearchFilters = {
      query: localFilters.query, // Keep the search query
      dateRange: {},
      fileTypes: [],
      organizations: [],
      projects: [],
      categories: [],
      sortBy: "relevance",
      sortOrder: "desc",
      selectedOrganization: undefined,
      selectedProject: undefined,
      tag: undefined,
      direction: undefined,
    };
    setLocalFilters(clearedFilters);
    onFiltersChange(clearedFilters);
  };

  const hasActiveFilters =
    localFilters.dateRange.from ||
    localFilters.dateRange.to ||
    localFilters.fileTypes.length > 0 ||
    localFilters.organizations.length > 0 ||
    localFilters.projects.length > 0 ||
    localFilters.categories.length > 0 ||
    !!localFilters.tag ||
    !!localFilters.direction;

  return (
    <Card>
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between">
          <CardTitle className="flex items-center gap-2 text-lg">
            <Filter className="h-5 w-5" />
            Advanced Filters
            {hasActiveFilters && (
              <Badge variant="primary" className="ml-2">
                {[
                  localFilters.fileTypes.length,
                  localFilters.organizations.length,
                  localFilters.projects.length,
                  localFilters.categories.length,
                ].reduce((a, b) => a + b, 0)}
              </Badge>
            )}
          </CardTitle>
          <div className="flex items-center gap-2">
            {hasActiveFilters && (
              <button
                onClick={clearAllFilters}
                className="text-sm text-red-600 hover:text-red-700 flex items-center gap-1"
              >
                <X className="h-3 w-3" />
                Clear All
              </button>
            )}
            {onToggleExpanded && (
              <button
                onClick={onToggleExpanded}
                className="p-1 hover:bg-gray-100 rounded"
              >
                {isExpanded ? (
                  <ChevronUp className="h-4 w-4" />
                ) : (
                  <ChevronDown className="h-4 w-4" />
                )}
              </button>
            )}
          </div>
        </div>
      </CardHeader>

      {isExpanded && (
        <CardContent className="space-y-6">
          {/* Date Range */}
          <div className="space-y-2">
            <label className="text-sm font-medium text-gray-700 flex items-center gap-2">
              <Calendar className="h-4 w-4" />
              Date Range
            </label>
            <div className="grid grid-cols-2 gap-2">
              <div>
                <label className="text-xs text-gray-500">From</label>
                <input
                  type="date"
                  value={localFilters.dateRange.from || ""}
                  onChange={(e) =>
                    updateFilters({
                      dateRange: {
                        ...localFilters.dateRange,
                        from: e.target.value,
                      },
                    })
                  }
                  className="w-full px-3 py-2 border border-gray-300 rounded-md text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                />
              </div>
              <div>
                <label className="text-xs text-gray-500">To</label>
                <input
                  type="date"
                  value={localFilters.dateRange.to || ""}
                  onChange={(e) =>
                    updateFilters({
                      dateRange: {
                        ...localFilters.dateRange,
                        to: e.target.value,
                      },
                    })
                  }
                  className="w-full px-3 py-2 border border-gray-300 rounded-md text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                />
              </div>
            </div>
          </div>

          {/* File Types */}
          <div className="space-y-2">
            <label className="text-sm font-medium text-gray-700 flex items-center gap-2">
              <FileText className="h-4 w-4" />
              File Types
            </label>
            <div className="flex flex-wrap gap-2">
              {FILE_TYPES.map((type) => (
                <button
                  key={type.value}
                  onClick={() => toggleArrayFilter("fileTypes", type.value)}
                  className={`
                    px-3 py-1 rounded-full text-sm font-medium transition-colors flex items-center gap-1
                    ${
                      localFilters.fileTypes.includes(type.value)
                        ? "bg-blue-100 text-blue-700 border border-blue-200"
                        : "bg-gray-100 text-gray-700 border border-gray-200 hover:bg-gray-200"
                    }
                  `}
                >
                  <span>{type.icon}</span>
                  {type.label}
                </button>
              ))}
            </div>
          </div>

          {/* Organization & Project (same row) */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div className="space-y-2">
              <label className="text-sm font-medium text-gray-700 flex items-center gap-2">
                <Building2 className="h-4 w-4" />
                Organization *
              </label>
              <select
                value={localFilters.selectedOrganization || ""}
                onChange={(e) =>
                  updateFilters({
                    selectedOrganization: e.target.value || undefined,
                    organizations: e.target.value ? [e.target.value] : [],
                    // reset project when organization changes
                    selectedProject: undefined,
                    projects: [],
                  })
                }
                className="w-full px-3 py-2 border border-gray-300 rounded-md text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              >
                <option value="">Select organization</option>
                {availableOrganizations.map((org) => (
                  <option key={org._id} value={org._id}>
                    {org.name}
                  </option>
                ))}
              </select>
            </div>

            <div className="space-y-2">
              <label className="text-sm font-medium text-gray-700 flex items-center gap-2">
                <FolderOpen className="h-4 w-4" />
                Project *
              </label>
              <select
                value={localFilters.selectedProject || ""}
                onChange={(e) =>
                  updateFilters({
                    selectedProject: e.target.value || undefined,
                    projects: e.target.value ? [e.target.value] : [],
                  })
                }
                className="w-full px-3 py-2 border border-gray-300 rounded-md text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              >
                <option value="">Select project</option>
                {availableProjects.map((project) => (
                  <option key={project._id} value={project._id}>
                    {project.name}
                  </option>
                ))}
              </select>
            </div>
          </div>

          {/* Tag & Directions (same row) */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div className="space-y-2">
              <label className="text-sm font-medium text-gray-700">Tag</label>
              <select
                value={localFilters.tag || ""}
                onChange={(e) =>
                  updateFilters({
                    tag: e.target.value || undefined,
                  })
                }
                className="w-full px-3 py-2 border border-gray-300 rounded-md text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              >
                <option value="">All Tags</option>
                {(availableTags || []).map((t) => (
                  <option key={t._id} value={t._id}>
                    {t.name}
                  </option>
                ))}
              </select>
            </div>

            <div className="space-y-2">
              <label className="text-sm font-medium text-gray-700">
                Directions
              </label>
              <select
                value={localFilters.direction || ""}
                onChange={(e) =>
                  updateFilters({
                    direction: (e.target.value ||
                      undefined) as SearchFilters["direction"],
                  })
                }
                className="w-full px-3 py-2 border border-gray-300 rounded-md text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              >
                <option value="">Any</option>
                <option value="incoming">Incoming</option>
                <option value="outgoing">Outgoing</option>
              </select>
            </div>
          </div>

          {/* Sort Options */}
          <div className="space-y-2">
            <label className="text-sm font-medium text-gray-700">Sort By</label>
            <div className="flex gap-2">
              <select
                value={localFilters.sortBy}
                onChange={(e) =>
                  updateFilters({
                    sortBy: e.target.value as SearchFilters["sortBy"],
                  })
                }
                className="flex-1 px-3 py-2 border border-gray-300 rounded-md text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              >
                {SORT_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
              <select
                value={localFilters.sortOrder}
                onChange={(e) =>
                  updateFilters({
                    sortOrder: e.target.value as SearchFilters["sortOrder"],
                  })
                }
                className="px-3 py-2 border border-gray-300 rounded-md text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              >
                <option value="desc">Descending</option>
                <option value="asc">Ascending</option>
              </select>
            </div>
          </div>
        </CardContent>
      )}
    </Card>
  );
};

export default AdvancedSearchFilters;
