import React, { useState, useEffect, useCallback, useMemo } from "react";
import { Link } from "react-router-dom";
import PageHeader from "@/components/ui/PageHeader";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import Badge from "@/components/ui/badge";
import { SkeletonCard, SkeletonTable } from "@/components/ui/skeleton";
import AdvancedSearchFilters, {
  type SearchFilters,
} from "@/components/search/AdvancedSearchFilters";
import {
  searchDocuments,
  getSearchSuggestions,
  trackSearch,
  type SearchResult,
  type SearchResponse,
} from "@/services/search-api";
import {
  listOrganizations,
  type Organization,
} from "@/services/organizations-api";
import { listProjects, type Project } from "@/services/projects-api";
import { downloadDocumentFile } from "@/services/documents-api";
import enhancedApi, { type Tag } from "@/services/enhanced-api";
import { toast } from "sonner";
import {
  Search,
  FileText,
  Download,
  Eye,
  Calendar,
  Building2,
  FolderOpen,
  Filter,
  Grid,
  List,
  ChevronLeft,
  ChevronRight,
  Loader2,
  AlertCircle,
  Clock,
} from "lucide-react";

const ITEMS_PER_PAGE = 20;

const EnhancedDocumentsPage: React.FC = () => {
  // Search state
  const [searchQuery, setSearchQuery] = useState("");
  const [searchSuggestions, setSearchSuggestions] = useState<string[]>([]);
  const [showSuggestions, setShowSuggestions] = useState(false);
  const [filters, setFilters] = useState<SearchFilters>({
    query: "",
    dateRange: {},
    fileTypes: [],
    organizations: [],
    projects: [],
    categories: [],
    sortBy: "relevance",
    sortOrder: "desc",
  });

  // Data state
  const [searchResults, setSearchResults] = useState<SearchResponse | null>(
    null
  );
  const [organizations, setOrganizations] = useState<Organization[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [availableCategories] = useState<string[]>([
    "contract",
    "letter",
    "report",
    "invoice",
    "agreement",
    "proposal",
  ]);

  // UI state
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [currentPage, setCurrentPage] = useState(1);
  const [viewMode, setViewMode] = useState<"grid" | "list">("grid");
  const [filtersExpanded, setFiltersExpanded] = useState(true);
  const [tags, setTags] = useState<Tag[]>([]);
  const [validationError, setValidationError] = useState<string | null>(null);

  // Load organizations
  useEffect(() => {
    const loadOrgs = async () => {
      try {
        const orgsData = await listOrganizations();
        setOrganizations(orgsData);
      } catch (err) {
        console.error("Failed to load organizations:", err);
      }
    };
    loadOrgs();
  }, []);

  // Load projects after organization selection
  useEffect(() => {
    const loadProjectsForOrg = async () => {
      try {
        if (filters.selectedOrganization) {
          const projectsData = await listProjects({
            organization_id: filters.selectedOrganization,
          });
          setProjects(projectsData);
        } else {
          setProjects([]);
        }
      } catch (err) {
        console.error("Failed to load projects:", err);
      }
    };
    loadProjectsForOrg();
  }, [filters.selectedOrganization]);

  // Load tags for Tag select
  useEffect(() => {
    const loadTags = async () => {
      try {
        const t = await enhancedApi.getTags();
        const normalized = Array.isArray(t)
          ? t
          : Array.isArray((t as any)?.tags)
          ? (t as any).tags
          : [];
        setTags(normalized);
      } catch (err) {
        console.error("Failed to load tags:", err);
      }
    };
    loadTags();
  }, []);

  // Search suggestions
  useEffect(() => {
    const getSuggestions = async () => {
      if (searchQuery.length > 2) {
        try {
          const suggestions = await getSearchSuggestions(searchQuery);
          setSearchSuggestions(suggestions);
        } catch (err) {
          console.error("Failed to get suggestions:", err);
        }
      } else {
        setSearchSuggestions([]);
      }
    };

    const timeoutId = setTimeout(getSuggestions, 300);
    return () => clearTimeout(timeoutId);
  }, [searchQuery]);

  // Perform search
  const performSearch = useCallback(
    async (page = 1) => {
      // Require Organization and Project selection
      if (!filters.selectedOrganization || !filters.selectedProject) {
        setValidationError("Please select both Organization and Project.");
        setFiltersExpanded(true);
        setSearchResults(null);
        return;
      }
      setValidationError(null);

      if (
        !filters.query.trim() &&
        filters.organizations.length === 0 &&
        filters.projects.length === 0
      ) {
        setSearchResults(null);
        return;
      }

      setLoading(true);
      setError(null);

      try {
        const response = await searchDocuments({
          ...filters,
          page,
          limit: ITEMS_PER_PAGE,
          includeFacets: true,
          includeContent: false,
        });

        setSearchResults(response);
        setCurrentPage(page);

        // Track search for analytics
        await trackSearch(filters.query, filters, response.total);
      } catch (err) {
        console.error("Search failed:", err);
        setError("Search failed. Please try again.");
      } finally {
        setLoading(false);
      }
    },
    [filters]
  );

  // Handle search input
  const handleSearchSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const updatedFilters = { ...filters, query: searchQuery };
    setFilters(updatedFilters);
    setValidationError(null);
    performSearch(1);
    setShowSuggestions(false);
  };

  // Handle filter changes
  const handleFiltersChange = (newFilters: SearchFilters) => {
    setFilters(newFilters);
    setSearchQuery(newFilters.query);
    performSearch(1);
  };

  // Handle pagination
  const handlePageChange = (page: number) => {
    performSearch(page);
  };

  // Handle document download
  const handleDownload = async (document: SearchResult) => {
    try {
      await downloadDocumentFile({
        upload_id: document._id,
        filenameFallback: document.filename,
      });
    } catch (err) {
      console.error("Download failed:", err);
      toast.error("Download failed. Please try again.");
    }
  };

  // Format file size
  const formatFileSize = (bytes?: number) => {
    if (!bytes) return "Unknown";
    const sizes = ["B", "KB", "MB", "GB"];
    const i = Math.floor(Math.log(bytes) / Math.log(1024));
    return `${(bytes / Math.pow(1024, i)).toFixed(1)} ${sizes[i]}`;
  };

  // Format date
  const formatDate = (dateString?: string) => {
    if (!dateString) return "Unknown";
    return new Date(dateString).toLocaleDateString();
  };

  // Get organization name
  const getOrganizationName = (orgId?: string) => {
    if (!orgId) return "Unknown";
    const org = organizations.find((o) => o._id === orgId);
    return org?.name || "Unknown";
  };

  // Get project name
  const getProjectName = (projectId?: string) => {
    if (!projectId) return "Unknown";
    const project = projects.find((p) => p._id === projectId);
    return project?.name || "Unknown";
  };

  const totalPages = searchResults
    ? Math.ceil(searchResults.total / ITEMS_PER_PAGE)
    : 0;

  return (
    <div className="max-w-7xl mx-auto p-6 space-y-6">
      <PageHeader
        icon={<Search className="h-6 w-6" />}
        title="Document Search"
        description="Search and filter through all your documents with advanced options"
      />

      {/* Search Bar */}
      <Card>
        <CardContent className="p-4">
          <form onSubmit={handleSearchSubmit} className="relative">
            <div className="relative">
              <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 h-4 w-4 text-gray-400" />
              <input
                type="text"
                value={searchQuery}
                onChange={(e) => {
                  setSearchQuery(e.target.value);
                  setShowSuggestions(true);
                }}
                onFocus={() => setShowSuggestions(true)}
                onBlur={() => setTimeout(() => setShowSuggestions(false), 200)}
                placeholder="Search documents by name, content, or metadata..."
                className="w-full pl-10 pr-4 py-3 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
              />
              <button
                type="submit"
                disabled={loading}
                className="absolute right-2 top-1/2 transform -translate-y-1/2 px-4 py-1.5 bg-blue-600 text-white rounded-md text-sm hover:bg-blue-700 disabled:opacity-50 flex items-center gap-1"
              >
                {loading ? (
                  <Loader2 className="h-3 w-3 animate-spin" />
                ) : (
                  <Search className="h-3 w-3" />
                )}
                Search
              </button>
            </div>

            {/* Search Suggestions */}
            {showSuggestions && searchSuggestions.length > 0 && (
              <div className="absolute top-full left-0 right-0 mt-1 bg-white border border-gray-200 rounded-lg shadow-lg z-10">
                {searchSuggestions.map((suggestion, index) => (
                  <button
                    key={index}
                    type="button"
                    onClick={() => {
                      setSearchQuery(suggestion);
                      setShowSuggestions(false);
                      const updatedFilters = { ...filters, query: suggestion };
                      setFilters(updatedFilters);
                      performSearch(1);
                    }}
                    className="w-full text-left px-4 py-2 hover:bg-gray-50 text-sm border-b border-gray-100 last:border-b-0"
                  >
                    <Search className="inline h-3 w-3 mr-2 text-gray-400" />
                    {suggestion}
                  </button>
                ))}
              </div>
            )}
          </form>
        </CardContent>
      </Card>

      {validationError && (
        <div className="text-sm text-red-600">{validationError}</div>
      )}

      {/* Advanced Filters */}
      <AdvancedSearchFilters
        filters={filters}
        onFiltersChange={handleFiltersChange}
        availableOrganizations={organizations}
        availableProjects={projects}
        availableCategories={availableCategories}
        availableTags={tags}
        isExpanded={filtersExpanded}
        onToggleExpanded={() => setFiltersExpanded(!filtersExpanded)}
      />

      {/* Results Header */}
      {searchResults && (
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-4">
            <p className="text-sm text-gray-600">
              {searchResults.total} results found
              {searchResults.searchTime && (
                <span className="ml-2 text-gray-400">
                  ({searchResults.searchTime}ms)
                </span>
              )}
            </p>
            {searchResults.total > 0 && (
              <p className="text-sm text-gray-500">
                Page {currentPage} of {totalPages}
              </p>
            )}
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={() => setViewMode("grid")}
              className={`p-2 rounded ${
                viewMode === "grid"
                  ? "bg-blue-100 text-blue-600"
                  : "text-gray-400 hover:text-gray-600"
              }`}
            >
              <Grid className="h-4 w-4" />
            </button>
            <button
              onClick={() => setViewMode("list")}
              className={`p-2 rounded ${
                viewMode === "list"
                  ? "bg-blue-100 text-blue-600"
                  : "text-gray-400 hover:text-gray-600"
              }`}
            >
              <List className="h-4 w-4" />
            </button>
          </div>
        </div>
      )}

      {/* Error State */}
      {error && (
        <Card>
          <CardContent className="p-6 text-center">
            <AlertCircle className="h-12 w-12 text-red-500 mx-auto mb-4" />
            <h3 className="text-lg font-medium text-gray-900 mb-2">
              Search Error
            </h3>
            <p className="text-gray-600">{error}</p>
          </CardContent>
        </Card>
      )}

      {/* Loading State */}
      {loading && (
        <div
          className={
            viewMode === "grid"
              ? "grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6"
              : "space-y-4"
          }
        >
          {Array.from({ length: 6 }).map((_, i) =>
            viewMode === "grid" ? (
              <SkeletonCard key={i} />
            ) : (
              <SkeletonTable key={i} rows={1} cols={4} />
            )
          )}
        </div>
      )}

      {/* Search Results */}
      {searchResults && !loading && (
        <>
          {searchResults.results.length === 0 ? (
            <Card>
              <CardContent className="p-12 text-center">
                <FileText className="h-12 w-12 text-gray-400 mx-auto mb-4" />
                <h3 className="text-lg font-medium text-gray-900 mb-2">
                  No documents found
                </h3>
                <p className="text-gray-600">
                  Try adjusting your search query or filters
                </p>
              </CardContent>
            </Card>
          ) : (
            <>
              {viewMode === "grid" ? (
                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
                  {searchResults.results.map((document) => (
                    <Card
                      key={document._id}
                      className="hover:shadow-lg transition-shadow"
                    >
                      <CardHeader className="pb-3">
                        <div className="flex items-start justify-between">
                          <div className="flex-1 min-w-0">
                            <CardTitle
                              className="text-base truncate"
                              title={document.name}
                            >
                              {document.name}
                            </CardTitle>
                            <p className="text-sm text-gray-500 mt-1">
                              {document.filename}
                            </p>
                          </div>
                          <FileText className="h-5 w-5 text-gray-400 ml-2 flex-shrink-0" />
                        </div>
                      </CardHeader>
                      <CardContent className="space-y-3">
                        {document.excerpt && (
                          <p className="text-sm text-gray-600 line-clamp-3">
                            {document.excerpt}
                          </p>
                        )}

                        <div className="flex flex-wrap gap-1">
                          {document.categories?.map((category) => (
                            <Badge
                              key={category}
                              variant="outline"
                              className="text-xs"
                            >
                              {category}
                            </Badge>
                          ))}
                        </div>

                        <div className="space-y-2 text-xs text-gray-500">
                          <div className="flex items-center gap-1">
                            <Building2 className="h-3 w-3" />
                            {getOrganizationName(document.organization_id)}
                          </div>
                          {document.project_id && (
                            <div className="flex items-center gap-1">
                              <FolderOpen className="h-3 w-3" />
                              {getProjectName(document.project_id)}
                            </div>
                          )}
                          <div className="flex items-center gap-1">
                            <Calendar className="h-3 w-3" />
                            {formatDate(document.createdAt)}
                          </div>
                          {document.size && (
                            <div className="flex items-center gap-1">
                              <FileText className="h-3 w-3" />
                              {formatFileSize(document.size)}
                            </div>
                          )}
                        </div>

                        <div className="flex gap-2 pt-2">
                          <Link
                            to={`/documentviewer/${document._id}`}
                            className="flex-1 px-3 py-1.5 bg-blue-600 text-white text-sm rounded hover:bg-blue-700 flex items-center justify-center gap-1"
                          >
                            <Eye className="h-3 w-3" />
                            View
                          </Link>
                          <button
                            onClick={() => handleDownload(document)}
                            className="px-3 py-1.5 border border-gray-300 text-gray-700 text-sm rounded hover:bg-gray-50 flex items-center gap-1"
                          >
                            <Download className="h-3 w-3" />
                            Download
                          </button>
                        </div>
                      </CardContent>
                    </Card>
                  ))}
                </div>
              ) : (
                <Card>
                  <CardContent className="p-0">
                    <div className="overflow-x-auto">
                      <table className="w-full">
                        <thead className="bg-gray-50 border-b">
                          <tr>
                            <th className="text-left py-3 px-4 font-medium text-gray-900">
                              Name
                            </th>
                            <th className="text-left py-3 px-4 font-medium text-gray-900">
                              Organization
                            </th>
                            <th className="text-left py-3 px-4 font-medium text-gray-900">
                              Project
                            </th>
                            <th className="text-left py-3 px-4 font-medium text-gray-900">
                              Size
                            </th>
                            <th className="text-left py-3 px-4 font-medium text-gray-900">
                              Modified
                            </th>
                            <th className="text-left py-3 px-4 font-medium text-gray-900">
                              Actions
                            </th>
                          </tr>
                        </thead>
                        <tbody>
                          {searchResults.results.map((document, index) => (
                            <tr
                              key={document._id}
                              className={
                                index % 2 === 0 ? "bg-white" : "bg-gray-50"
                              }
                            >
                              <td className="py-3 px-4">
                                <div className="flex items-center gap-2">
                                  <FileText className="h-4 w-4 text-gray-400" />
                                  <div>
                                    <p
                                      className="font-medium text-gray-900 truncate max-w-xs"
                                      title={document.name}
                                    >
                                      {document.name}
                                    </p>
                                    <p className="text-sm text-gray-500">
                                      {document.filename}
                                    </p>
                                  </div>
                                </div>
                              </td>
                              <td className="py-3 px-4 text-sm text-gray-600">
                                {getOrganizationName(document.organization_id)}
                              </td>
                              <td className="py-3 px-4 text-sm text-gray-600">
                                {getProjectName(document.project_id)}
                              </td>
                              <td className="py-3 px-4 text-sm text-gray-600">
                                {formatFileSize(document.size)}
                              </td>
                              <td className="py-3 px-4 text-sm text-gray-600">
                                {formatDate(document.createdAt)}
                              </td>
                              <td className="py-3 px-4">
                                <div className="flex gap-2">
                                  <Link
                                    to={`/documentviewer/${document._id}`}
                                    className="text-blue-600 hover:text-blue-700 text-sm"
                                  >
                                    View
                                  </Link>
                                  <button
                                    onClick={() => handleDownload(document)}
                                    className="text-gray-600 hover:text-gray-700 text-sm"
                                  >
                                    Download
                                  </button>
                                </div>
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </CardContent>
                </Card>
              )}

              {/* Pagination */}
              {totalPages > 1 && (
                <div className="flex items-center justify-center gap-2">
                  <button
                    onClick={() => handlePageChange(currentPage - 1)}
                    disabled={currentPage === 1}
                    className="p-2 border border-gray-300 rounded hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    <ChevronLeft className="h-4 w-4" />
                  </button>

                  {Array.from({ length: Math.min(5, totalPages) }, (_, i) => {
                    const page =
                      Math.max(1, Math.min(totalPages - 4, currentPage - 2)) +
                      i;
                    return (
                      <button
                        key={page}
                        onClick={() => handlePageChange(page)}
                        className={`px-3 py-2 border rounded text-sm ${
                          page === currentPage
                            ? "bg-blue-600 text-white border-blue-600"
                            : "border-gray-300 hover:bg-gray-50"
                        }`}
                      >
                        {page}
                      </button>
                    );
                  })}

                  <button
                    onClick={() => handlePageChange(currentPage + 1)}
                    disabled={currentPage === totalPages}
                    className="p-2 border border-gray-300 rounded hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    <ChevronRight className="h-4 w-4" />
                  </button>
                </div>
              )}
            </>
          )}
        </>
      )}
    </div>
  );
};

export default EnhancedDocumentsPage;
