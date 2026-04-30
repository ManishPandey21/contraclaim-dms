import React, { useEffect, useRef, useState } from "react";
import { BellIcon, CheckIcon } from "@heroicons/react/24/outline";
import { useNavigate } from "react-router-dom";

import {
  useNotificationStore,
  useWebSocketNotifications,
} from "../contexts/NotificationContext";
import { NotificationCategory } from "../types/api";
import { cn } from "../lib/utils";

const categories: Array<{ label: string; value: NotificationCategory | "all" }> = [
  { label: "All", value: "all" },
  { label: "Drafting", value: "drafting" },
  { label: "Approvals", value: "approvals" },
  { label: "Uploads", value: "uploads" },
  { label: "Comments", value: "comments" },
];

const formatTimestamp = (value: string) => {
  try {
    return new Date(value).toLocaleString();
  } catch {
    return value;
  }
};

const NotificationCenter: React.FC = () => {
  const {
    notifications,
    unreadCount,
    loading,
    activeCategory,
    fetchNotifications,
    markRead,
    markAllRead,
    refreshUnreadCount,
    setPanelOpen,
  } = useNotificationStore();
  const { readyState } = useWebSocketNotifications();
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const navigate = useNavigate();

  useEffect(() => {
    const node = containerRef.current;
    if (!node) return;

    const handleEvent = (event: MouseEvent | TouchEvent) => {
      if (!node.contains(event.target as Node)) {
        setOpen(false);
      }
    };

    document.addEventListener("mousedown", handleEvent);
    document.addEventListener("touchstart", handleEvent);
    return () => {
      document.removeEventListener("mousedown", handleEvent);
      document.removeEventListener("touchstart", handleEvent);
    };
  }, []);

  useEffect(() => {
    refreshUnreadCount();
  }, [refreshUnreadCount]);

  useEffect(() => {
    setPanelOpen(open);
    return () => setPanelOpen(false);
  }, [open, setPanelOpen]);

  useEffect(() => {
    if (open) {
      fetchNotifications(activeCategory);
    }
  }, [fetchNotifications, open]);

  useEffect(() => {
    const intervalId = window.setInterval(() => {
      refreshUnreadCount();
      if (open) {
        fetchNotifications(activeCategory);
      }
    }, 30000);

    return () => window.clearInterval(intervalId);
  }, [activeCategory, fetchNotifications, open, refreshUnreadCount]);

  const handleCategoryChange = (category: NotificationCategory | "all") => {
    if (category !== activeCategory) {
      fetchNotifications(category);
    }
  };

  const handleMarkAll = async () => {
    await markAllRead();
  };

  const handleNotificationClick = async (notification: {
    id: string;
    type: string;
    unread: boolean;
    resource_type?: string;
    resource_id?: string;
    data?: {
      document_id?: string;
    };
  }) => {
    try {
      if (notification.unread) {
        await markRead(notification.id);
      }
    } finally {
      if (notification.type === "bulk_upload_completed") {
        navigate("/documents");
      } else {
        const documentId =
          notification.data?.document_id ||
          (notification.resource_type === "document"
            ? notification.resource_id
            : undefined);
        if (documentId) {
          navigate(`/documentviewer/${documentId}`);
        }
      }
      setOpen(false);
    }
  };

  return (
    <div className="relative" ref={containerRef}>
      <button
        type="button"
        className={cn(
          "flex items-center p-2 text-gray-400 hover:text-gray-500 rounded-full",
          "focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-indigo-500"
        )}
        onClick={() => setOpen((prev) => !prev)}
      >
        <BellIcon className="h-6 w-6" />
        {unreadCount > 0 && (
          <span className="absolute top-0 right-0 inline-flex items-center justify-center px-1.5 py-0.5 text-xs font-bold leading-none text-white transform translate-x-1/2 -translate-y-1/2 bg-red-600 rounded-full">
            {unreadCount}
          </span>
        )}
      </button>

      {open && (
        <div className="absolute top-12 right-0 z-50 w-96 bg-white rounded-md shadow-lg ring-1 ring-black ring-opacity-5 py-4 divide-y divide-gray-200 overflow-hidden">
          <div className="px-4 py-3 space-y-1">
            <h3 className="text-lg font-medium text-gray-900">Notifications</h3>
            <p className="text-sm text-gray-500">
              {readyState === WebSocket.OPEN ? "Connected" : "Reconnecting"}
            </p>
            {loading && <p className="text-sm text-gray-500">Loading...</p>}
          </div>

          <div className="px-4 pb-2 flex space-x-1 bg-gray-50">
            {categories.map(({ label, value }) => {
              const selected = value === activeCategory;
              return (
                <button
                  key={value}
                  type="button"
                  onClick={() => handleCategoryChange(value)}
                  className={cn(
                    "flex-1 py-2 text-sm font-medium rounded-lg transition-colors",
                    selected
                      ? "bg-white text-indigo-700 shadow"
                      : "text-gray-600 hover:text-gray-800 hover:bg-white"
                  )}
                >
                  {label}
                </button>
              );
            })}
          </div>

          <div className="p-4 max-h-80 overflow-y-auto">
            {notifications.length === 0 ? (
              <p className="py-8 text-center text-gray-500">No notifications</p>
            ) : (
              <ul className="-my-4 divide-y divide-gray-200">
                {notifications.map((notification) => (
                  <li key={notification.id} className="py-4">
                    <div className="flex items-start gap-3">
                      <div
                        className="mt-1 h-2 w-2 rounded-full bg-indigo-500"
                        hidden={!notification.unread}
                      />
                      <button
                        type="button"
                        className="flex-1 text-left"
                        onClick={() => handleNotificationClick(notification)}
                      >
                        <p className="text-sm font-medium text-gray-900">
                          {notification.data?.title ||
                            notification.type.replace(/_/g, " ").toUpperCase()}
                        </p>
                        <p className="text-sm text-gray-600">
                          {notification.data?.message || notification.resource_type}
                        </p>
                        <p className="text-xs text-gray-400 mt-1">
                          {formatTimestamp(notification.created_at)}
                        </p>
                      </button>
                      <div className="flex flex-col items-end gap-2">
                        {notification.unread && (
                          <button
                            type="button"
                            onClick={(event) => {
                              event.stopPropagation();
                              void markRead(notification.id);
                            }}
                            className="inline-flex items-center px-2 py-1 rounded-full text-xs font-medium bg-green-100 text-green-800"
                          >
                            <CheckIcon className="w-3 h-3 mr-1" /> Mark read
                          </button>
                        )}
                      </div>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </div>

          <div className="px-4 py-3 border-t border-gray-200">
            <button
              onClick={handleMarkAll}
              className="w-full flex justify-center items-center text-sm font-medium text-indigo-600 hover:text-indigo-500"
              disabled={unreadCount === 0}
            >
              Mark all as read ({unreadCount})
            </button>
          </div>
        </div>
      )}
    </div>
  );
};

export default NotificationCenter;
