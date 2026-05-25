import React, { useCallback, useState, useEffect } from "react";
import PageHeader from "@/components/ui/PageHeader";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import Badge from "@/components/ui/badge";
import { SkeletonCard } from "@/components/ui/skeleton";
import { getSearchAnalytics } from "@/services/search-api";
import { listDocuments } from "@/services/documents-api";
import { listOrganizations } from "@/services/organizations-api";
import { listProjects } from "@/services/projects-api";
import {
  BarChart3,
  FileText,
  Building2,
  FolderOpen,
  TrendingUp,
  Clock,
  Search,
  Users,
  Activity,
  Calendar,
  Download,
  Eye,
} from "lucide-react";

interface DashboardStats {
  totalDocuments: number;
  totalOrganizations: number;
  totalProjects: number;
  recentUploads: number;
  searchesThisWeek: number;
  popularSearches: Array<{ term: string; count: number }>;
  recentActivity: Array<{
    id: string;
    type: "upload" | "search" | "download" | "view";
    description: string;
    timestamp: string;
    user?: string;
  }>;
  documentsByCategory: Array<{ category: string; count: number }>;
  uploadTrend: Array<{ date: string; count: number }>;
}

const DashboardPage: React.FC = () => {
  const [stats, setStats] = useState<DashboardStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [timeRange, setTimeRange] = useState<"7d" | "30d" | "90d">("30d");

  const loadDashboardData = useCallback(async () => {
    setLoading(true);
    setError(null);

    try {
      // Calculate date range
      const endDate = new Date();
      const startDate = new Date();
      const days = timeRange === "7d" ? 7 : timeRange === "30d" ? 30 : 90;
      startDate.setDate(endDate.getDate() - days);

      // Fetch data in parallel
      const [
        documentsResponse,
        organizationsResponse,
        projectsResponse,
        analyticsResponse,
      ] = await Promise.all([
        listDocuments({ limit: 1000 }), // Get all documents for stats
        listOrganizations(),
        listProjects(),
        getSearchAnalytics({
          from: startDate.toISOString().split("T")[0],
          to: endDate.toISOString().split("T")[0],
        }).catch(() => null), // Analytics might not be available
      ]);

      // Process documents data
      const documents = documentsResponse.documents || [];
      const recentUploads = documents.filter((doc) => {
        if (!doc.createdAt) return false;
        const uploadDate = new Date(doc.createdAt);
        return uploadDate >= startDate;
      }).length;

      // Group documents by category
      const categoryMap = new Map<string, number>();
      documents.forEach((doc) => {
        if (doc.categories && doc.categories.length > 0) {
          doc.categories.forEach((category) => {
            categoryMap.set(category, (categoryMap.get(category) || 0) + 1);
          });
        } else {
          categoryMap.set(
            "Uncategorized",
            (categoryMap.get("Uncategorized") || 0) + 1
          );
        }
      });

      const documentsByCategory = Array.from(categoryMap.entries())
        .map(([category, count]) => ({ category, count }))
        .sort((a, b) => b.count - a.count)
        .slice(0, 10);

      // Generate upload trend data
      const uploadTrend = [];
      for (let i = days - 1; i >= 0; i--) {
        const date = new Date();
        date.setDate(date.getDate() - i);
        const dateStr = date.toISOString().split("T")[0];

        const count = documents.filter((doc) => {
          if (!doc.createdAt) return false;
          const uploadDate = new Date(doc.createdAt);
          return uploadDate.toISOString().split("T")[0] === dateStr;
        }).length;

        uploadTrend.push({
          date: dateStr,
          count,
        });
      }

      // Mock recent activity (in a real app, this would come from an activity log)
      const recentActivity = [
        {
          id: "1",
          type: "upload" as const,
          description: "Contract ABC-123.pdf uploaded",
          timestamp: new Date(Date.now() - 1000 * 60 * 30).toISOString(), // 30 minutes ago
          user: "John Doe",
        },
        {
          id: "2",
          type: "search" as const,
          description: 'Searched for "payment terms"',
          timestamp: new Date(Date.now() - 1000 * 60 * 60).toISOString(), // 1 hour ago
          user: "Jane Smith",
        },
        {
          id: "3",
          type: "download" as const,
          description: "Downloaded Project-Report.pdf",
          timestamp: new Date(Date.now() - 1000 * 60 * 60 * 2).toISOString(), // 2 hours ago
          user: "Mike Johnson",
        },
        {
          id: "4",
          type: "view" as const,
          description: "Viewed Agreement-XYZ.docx",
          timestamp: new Date(Date.now() - 1000 * 60 * 60 * 4).toISOString(), // 4 hours ago
          user: "Sarah Wilson",
        },
      ];

      const dashboardStats: DashboardStats = {
        totalDocuments: documents.length,
        totalOrganizations: organizationsResponse.length,
        totalProjects: projectsResponse.length,
        recentUploads,
        searchesThisWeek:
          analyticsResponse?.volume_over_time?.reduce(
            (sum: number, day: any) => sum + day.searches,
            0
          ) || 0,
        popularSearches: analyticsResponse?.top_searches?.slice(0, 5) || [
          { term: "contract", count: 45 },
          { term: "agreement", count: 32 },
          { term: "invoice", count: 28 },
          { term: "report", count: 21 },
          { term: "proposal", count: 18 },
        ],
        recentActivity,
        documentsByCategory,
        uploadTrend,
      };

      setStats(dashboardStats);
    } catch (err) {
      console.error("Failed to load dashboard data:", err);
      setError("Failed to load dashboard data");
    } finally {
      setLoading(false);
    }
  }, [timeRange]);

  useEffect(() => {
    loadDashboardData();
  }, [loadDashboardData]);

  const formatTimeAgo = (timestamp: string) => {
    const now = new Date();
    const time = new Date(timestamp);
    const diffInMinutes = Math.floor(
      (now.getTime() - time.getTime()) / (1000 * 60)
    );

    if (diffInMinutes < 60) {
      return `${diffInMinutes}m ago`;
    } else if (diffInMinutes < 1440) {
      return `${Math.floor(diffInMinutes / 60)}h ago`;
    } else {
      return `${Math.floor(diffInMinutes / 1440)}d ago`;
    }
  };

  const getActivityIcon = (type: string) => {
    switch (type) {
      case "upload":
        return <FileText className="h-4 w-4 text-blue-500" />;
      case "search":
        return <Search className="h-4 w-4 text-green-500" />;
      case "download":
        return <Download className="h-4 w-4 text-purple-500" />;
      case "view":
        return <Eye className="h-4 w-4 text-orange-500" />;
      default:
        return <Activity className="h-4 w-4 text-gray-500" />;
    }
  };

  if (loading) {
    return (
      <div className="max-w-7xl mx-auto p-6 space-y-6">
        <PageHeader
          icon={<BarChart3 className="h-6 w-6" />}
          title="Dashboard"
          description="Overview of your document management system"
        />
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
          {Array.from({ length: 4 }).map((_, i) => (
            <SkeletonCard key={i} />
          ))}
        </div>
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          {Array.from({ length: 4 }).map((_, i) => (
            <SkeletonCard key={i} />
          ))}
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="max-w-7xl mx-auto p-6">
        <PageHeader
          icon={<BarChart3 className="h-6 w-6" />}
          title="Dashboard"
          description="Overview of your document management system"
        />
        <Card>
          <CardContent className="p-6 text-center">
            <p className="text-red-600">{error}</p>
            <button
              onClick={loadDashboardData}
              className="mt-4 px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700"
            >
              Retry
            </button>
          </CardContent>
        </Card>
      </div>
    );
  }

  if (!stats) return null;

  return (
    <div className="max-w-7xl mx-auto p-6 space-y-6">
      <div className="flex items-center justify-between">
        <PageHeader
          icon={<BarChart3 className="h-6 w-6" />}
          title="Dashboard"
          description="Overview of your document management system"
        />
        <div className="flex gap-2">
          {(["7d", "30d", "90d"] as const).map((range) => (
            <button
              key={range}
              onClick={() => setTimeRange(range)}
              className={`px-3 py-1 text-sm rounded ${
                timeRange === range
                  ? "bg-blue-600 text-white"
                  : "bg-gray-100 text-gray-700 hover:bg-gray-200"
              }`}
            >
              {range === "7d"
                ? "7 days"
                : range === "30d"
                ? "30 days"
                : "90 days"}
            </button>
          ))}
        </div>
      </div>

      {/* Key Metrics */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
        <Card>
          <CardContent className="p-6">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm font-medium text-gray-600">
                  Total Documents
                </p>
                <p className="text-2xl font-bold text-gray-900">
                  {stats.totalDocuments}
                </p>
              </div>
              <FileText className="h-8 w-8 text-blue-500" />
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardContent className="p-6">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm font-medium text-gray-600">
                  Organizations
                </p>
                <p className="text-2xl font-bold text-gray-900">
                  {stats.totalOrganizations}
                </p>
              </div>
              <Building2 className="h-8 w-8 text-green-500" />
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardContent className="p-6">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm font-medium text-gray-600">Projects</p>
                <p className="text-2xl font-bold text-gray-900">
                  {stats.totalProjects}
                </p>
              </div>
              <FolderOpen className="h-8 w-8 text-purple-500" />
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardContent className="p-6">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm font-medium text-gray-600">
                  Recent Uploads
                </p>
                <p className="text-2xl font-bold text-gray-900">
                  {stats.recentUploads}
                </p>
                <p className="text-xs text-gray-500">Last {timeRange}</p>
              </div>
              <TrendingUp className="h-8 w-8 text-orange-500" />
            </div>
          </CardContent>
        </Card>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Popular Searches */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Search className="h-5 w-5" />
              Popular Searches
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="space-y-3">
              {stats.popularSearches.map((search, index) => (
                <div
                  key={search.term}
                  className="flex items-center justify-between"
                >
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium text-gray-600">
                      #{index + 1}
                    </span>
                    <span className="text-sm text-gray-900">{search.term}</span>
                  </div>
                  <Badge variant="outline">{search.count}</Badge>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>

        {/* Recent Activity */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Activity className="h-5 w-5" />
              Recent Activity
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="space-y-3">
              {stats.recentActivity.map((activity) => (
                <div key={activity.id} className="flex items-start gap-3">
                  {getActivityIcon(activity.type)}
                  <div className="flex-1 min-w-0">
                    <p className="text-sm text-gray-900">
                      {activity.description}
                    </p>
                    <div className="flex items-center gap-2 mt-1">
                      {activity.user && (
                        <span className="text-xs text-gray-500">
                          {activity.user}
                        </span>
                      )}
                      <span className="text-xs text-gray-400">
                        {formatTimeAgo(activity.timestamp)}
                      </span>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>

        {/* Documents by Category */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <BarChart3 className="h-5 w-5" />
              Documents by Category
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="space-y-3">
              {stats.documentsByCategory.map((category) => (
                <div
                  key={category.category}
                  className="flex items-center justify-between"
                >
                  <span className="text-sm text-gray-900">
                    {category.category}
                  </span>
                  <div className="flex items-center gap-2">
                    <div className="w-20 bg-gray-200 rounded-full h-2">
                      <div
                        className="bg-blue-600 h-2 rounded-full"
                        style={{
                          width: `${
                            (category.count / stats.totalDocuments) * 100
                          }%`,
                        }}
                      />
                    </div>
                    <span className="text-sm font-medium text-gray-600 w-8 text-right">
                      {category.count}
                    </span>
                  </div>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>

        {/* Upload Trend */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Calendar className="h-5 w-5" />
              Upload Trend
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="space-y-2">
              {stats.uploadTrend.slice(-7).map((day) => (
                <div
                  key={day.date}
                  className="flex items-center justify-between"
                >
                  <span className="text-sm text-gray-600">
                    {new Date(day.date).toLocaleDateString("en-US", {
                      month: "short",
                      day: "numeric",
                    })}
                  </span>
                  <div className="flex items-center gap-2">
                    <div className="w-16 bg-gray-200 rounded-full h-2">
                      <div
                        className="bg-green-600 h-2 rounded-full"
                        style={{
                          width: `${Math.max(
                            10,
                            (day.count /
                              Math.max(
                                ...stats.uploadTrend.map((d) => d.count)
                              )) *
                              100
                          )}%`,
                        }}
                      />
                    </div>
                    <span className="text-sm font-medium text-gray-600 w-6 text-right">
                      {day.count}
                    </span>
                  </div>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
};

export default DashboardPage;
