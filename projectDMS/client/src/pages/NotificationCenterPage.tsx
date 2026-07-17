import React, { useEffect, useMemo, useState } from "react";
import { Check, ChevronLeft, ChevronRight, Search } from "lucide-react";
import { useNavigate } from "react-router-dom";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import enhancedApi from "@/services/enhanced-api";
import {
  NotificationCategory,
  NotificationItem,
  NotificationSeverity,
} from "@/types/api";
import { useNotificationStore } from "@/contexts/NotificationContext";
import { cn } from "@/lib/utils";

const PAGE_SIZE = 25;

const categories: Array<{ label: string; value: NotificationCategory | "all" }> = [
  { label: "All", value: "all" },
  { label: "Drafting", value: "drafting" },
  { label: "Approvals", value: "approvals" },
  { label: "Uploads", value: "uploads" },
  { label: "Comments", value: "comments" },
  { label: "Reminders", value: "reminders" },
  { label: "System", value: "system" },
  { label: "Security", value: "security" },
];

const severityClass: Record<NotificationSeverity, string> = {
  info: "bg-slate-100 text-slate-700",
  warning: "bg-amber-100 text-amber-800",
  error: "bg-red-100 text-red-800",
  critical: "bg-red-600 text-white",
};

function formatTimestamp(value: string) {
  try {
    return new Date(value).toLocaleString();
  } catch {
    return value;
  }
}

function resolveNotificationPath(notification: NotificationItem) {
  if (notification.resource_link) return notification.resource_link;
  const navigateAction = notification.actions?.find(
    (action) => action.method === "navigate" && action.href,
  );
  if (navigateAction?.href) return navigateAction.href;
  if (notification.type === "bulk_upload_completed") return "/documents";
  const documentId =
    notification.data?.document_id ||
    (notification.resource_type === "document" ? notification.resource_id : undefined);
  if (documentId) return `/documentviewer/${documentId}`;
  if (notification.resource_type === "letter" && notification.resource_id) {
    return `/letters/${notification.resource_id}/input`;
  }
  return null;
}

const NotificationCenterPage: React.FC = () => {
  const navigate = useNavigate();
  const { refreshUnreadCount } = useNotificationStore();
  const [notifications, setNotifications] = useState<NotificationItem[]>([]);
  const [total, setTotal] = useState(0);
  const [unreadCount, setUnreadCount] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [category, setCategory] = useState<NotificationCategory | "all">("all");
  const [eventType, setEventType] = useState("");
  const [projectId, setProjectId] = useState("");
  const [search, setSearch] = useState("");
  const [unreadOnly, setUnreadOnly] = useState(false);
  const [page, setPage] = useState(0);

  const skip = page * PAGE_SIZE;
  const hasPrevious = page > 0;
  const hasNext = skip + notifications.length < total;

  const filters = useMemo(
    () => ({
      unread_only: unreadOnly || undefined,
      category: category === "all" ? undefined : category,
      event_type: eventType.trim() || undefined,
      project_id: projectId.trim() || undefined,
      search: search.trim() || undefined,
      limit: PAGE_SIZE,
      skip,
    }),
    [category, eventType, projectId, search, skip, unreadOnly],
  );

  useEffect(() => {
    let cancelled = false;

    async function loadNotifications() {
      setLoading(true);
      setError(null);
      try {
        const response = await enhancedApi.getNotifications(filters);
        if (cancelled) return;
        setNotifications(response.notifications);
        setTotal(response.total);
        setUnreadCount(response.unread_count);
      } catch (err) {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : "Failed to load notifications");
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    void loadNotifications();
    return () => {
      cancelled = true;
    };
  }, [filters]);

  const resetPage = () => setPage(0);

  const markRead = async (notification: NotificationItem) => {
    if (!notification.unread) return;
    await enhancedApi.markNotificationRead(notification.id);
    setNotifications((items) =>
      items.map((item) =>
        item.id === notification.id ? { ...item, unread: false } : item,
      ),
    );
    setUnreadCount((value) => Math.max(0, value - 1));
    void refreshUnreadCount();
  };

  const markAllRead = async () => {
    const response = await enhancedApi.markAllNotificationsRead({
      category: category === "all" ? undefined : category,
      event_type: eventType.trim() || undefined,
      project_id: projectId.trim() || undefined,
    });
    if (response.updated > 0) {
      setNotifications((items) => items.map((item) => ({ ...item, unread: false })));
      setUnreadCount(0);
      void refreshUnreadCount();
    }
  };

  const openNotification = async (notification: NotificationItem) => {
    await markRead(notification);
    const path = resolveNotificationPath(notification);
    if (path) navigate(path);
  };

  const executeAction = async (
    notification: NotificationItem,
    actionKey: string,
  ) => {
    await enhancedApi.executeNotificationAction(notification.id, actionKey);
    const response = await enhancedApi.getNotifications(filters);
    setNotifications(response.notifications);
    setTotal(response.total);
    setUnreadCount(response.unread_count);
    void refreshUnreadCount();
  };

  return (
    <div className="space-y-5">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-gray-900">Notifications</h1>
          <p className="text-sm text-gray-500">
            {total} total, {unreadCount} unread in the current view
          </p>
        </div>
        <Button
          type="button"
          variant="outline"
          onClick={markAllRead}
          disabled={unreadCount === 0}
          className="w-full sm:w-auto"
        >
          <Check className="mr-2 h-4 w-4" />
          Mark filtered read
        </Button>
      </div>

      <div className="rounded-md border border-gray-200 bg-white p-4">
        <div className="grid gap-3 lg:grid-cols-[1.4fr_0.8fr_0.8fr_auto]">
          <div className="relative">
            <Search className="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-gray-400" />
            <Input
              value={search}
              onChange={(event) => {
                setSearch(event.target.value);
                resetPage();
              }}
              placeholder="Search title, message, subject, filename"
              className="pl-9"
            />
          </div>
          <Input
            value={eventType}
            onChange={(event) => {
              setEventType(event.target.value);
              resetPage();
            }}
            placeholder="Event type"
          />
          <Input
            value={projectId}
            onChange={(event) => {
              setProjectId(event.target.value);
              resetPage();
            }}
            placeholder="Project ID"
          />
          <label className="flex h-10 items-center gap-2 rounded-md border border-gray-200 px-3 text-sm text-gray-700">
            <input
              type="checkbox"
              checked={unreadOnly}
              onChange={(event) => {
                setUnreadOnly(event.target.checked);
                resetPage();
              }}
            />
            Unread only
          </label>
        </div>

        <div className="mt-4 flex flex-wrap gap-2">
          {categories.map((item) => (
            <button
              key={item.value}
              type="button"
              onClick={() => {
                setCategory(item.value);
                resetPage();
              }}
              className={cn(
                "rounded-md border px-3 py-1.5 text-sm font-medium",
                category === item.value
                  ? "border-blue-600 bg-blue-50 text-blue-700"
                  : "border-gray-200 text-gray-600 hover:bg-gray-50",
              )}
            >
              {item.label}
            </button>
          ))}
        </div>
      </div>

      <div className="overflow-hidden rounded-md border border-gray-200 bg-white">
        {error ? (
          <div className="p-6 text-sm text-red-700">{error}</div>
        ) : notifications.length === 0 ? (
          <div className="p-10 text-center text-sm text-gray-500">
            {loading ? "Loading notifications..." : "No notifications match these filters"}
          </div>
        ) : (
          <ul className="divide-y divide-gray-200">
            {notifications.map((notification) => (
              <li key={notification.id}>
                <div className="grid w-full gap-3 px-4 py-4 hover:bg-gray-50 md:grid-cols-[1fr_auto]">
                  <button
                    type="button"
                    onClick={() => void openNotification(notification)}
                    className="flex min-w-0 gap-3 text-left"
                  >
                    <span
                      className="mt-2 h-2 w-2 shrink-0 rounded-full bg-blue-600"
                      hidden={!notification.unread}
                    />
                    <span className="min-w-0">
                      <span className="block truncate text-sm font-semibold text-gray-900">
                        {notification.title ||
                          notification.data?.title ||
                          notification.type.replace(/_/g, " ").toUpperCase()}
                      </span>
                      <span className="mt-1 block text-sm text-gray-600">
                        {notification.message ||
                          notification.data?.message ||
                          notification.resource_type}
                      </span>
                      <span className="mt-2 flex flex-wrap gap-2 text-xs text-gray-500">
                        <span>{formatTimestamp(notification.created_at)}</span>
                        {notification.category && <span>{notification.category}</span>}
                        {notification.resource_type && <span>{notification.resource_type}</span>}
                      </span>
                    </span>
                  </button>
                  <div className="flex items-center gap-2 md:justify-end">
                    {notification.actions
                      ?.filter((action) => action.method === "post")
                      .slice(0, 3)
                      .map((action) => (
                        <button
                          type="button"
                          key={action.key}
                          onClick={() => void executeAction(notification, action.key)}
                          disabled={
                            notification.action_state?.[action.key]?.status ===
                            "completed"
                          }
                          className={cn(
                            "rounded-md border border-gray-200 px-2 py-1 text-xs font-medium text-gray-700 hover:bg-gray-100 disabled:opacity-50",
                            notification.action_state?.[action.key]?.status ===
                              "completed" && "pointer-events-none",
                          )}
                        >
                          {action.label}
                        </button>
                      ))}
                    <span
                      className={cn(
                        "rounded-full px-2 py-1 text-xs font-medium",
                        severityClass[notification.severity || "info"],
                      )}
                    >
                      {notification.severity || "info"}
                    </span>
                    {notification.unread && (
                      <span className="rounded-full bg-blue-100 px-2 py-1 text-xs font-medium text-blue-800">
                        Unread
                      </span>
                    )}
                  </div>
                </div>
              </li>
            ))}
          </ul>
        )}

        <div className="flex items-center justify-between border-t border-gray-200 px-4 py-3 text-sm text-gray-600">
          <span>
            Page {page + 1}
            {total > 0 ? ` of ${Math.max(1, Math.ceil(total / PAGE_SIZE))}` : ""}
          </span>
          <div className="flex gap-2">
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={!hasPrevious || loading}
              onClick={() => setPage((value) => Math.max(0, value - 1))}
            >
              <ChevronLeft className="mr-1 h-4 w-4" />
              Previous
            </Button>
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={!hasNext || loading}
              onClick={() => setPage((value) => value + 1)}
            >
              Next
              <ChevronRight className="ml-1 h-4 w-4" />
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
};

export default NotificationCenterPage;
