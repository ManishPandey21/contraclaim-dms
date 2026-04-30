import { useEffect, useMemo } from "react";
import { create } from "zustand";
import useWebSocket from "react-use-websocket";

import { API_BASE_URL } from "../config/api";
import { NotificationCategory, NotificationItem } from "../types/api";
import enhancedApi from "../services/enhanced-api";

interface NotificationState {
  notifications: NotificationItem[];
  unreadCount: number;
  loading: boolean;
  activeCategory: NotificationCategory | "all";
  isPanelOpen: boolean;
  fetchNotifications: (
    category?: NotificationCategory | "all"
  ) => Promise<void>;
  markRead: (id: string) => Promise<void>;
  markAllRead: () => Promise<void>;
  refreshUnreadCount: () => Promise<void>;
  setPanelOpen: (isOpen: boolean) => void;
}

function resolveWebSocketUrl(): string | null {
  if (typeof window === "undefined") return null;
  const token = window.localStorage.getItem("accessToken");
  if (!token) return null;

  let base: URL;
  if (API_BASE_URL.startsWith("http")) {
    base = new URL(API_BASE_URL);
  } else {
    base = new URL(window.location.origin);
  }
  const protocol = base.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${base.host}/ws/notifications?token=${encodeURIComponent(
    token
  )}`;
}

export const useNotificationStore = create<NotificationState>((set, get) => ({
  notifications: [],
  unreadCount: 0,
  loading: false,
  activeCategory: "all",
  isPanelOpen: false,
  async fetchNotifications(category = get().activeCategory) {
    set({ loading: true, activeCategory: category });
    try {
      const response = await enhancedApi.getNotifications({
        category: category === "all" ? undefined : category,
        limit: 20,
        skip: 0,
      });
      set({
        notifications: response.notifications,
        unreadCount: response.unread_count,
        loading: false,
      });
    } catch (error) {
      console.error("Fetch notifications error:", error);
      set({ loading: false });
    }
  },
  async markRead(id) {
    try {
      await enhancedApi.markNotificationRead(id);
      const state = get();
      set({
        notifications: state.notifications.map((notification) =>
          notification.id === id
            ? { ...notification, unread: false }
            : notification
        ),
        unreadCount: Math.max(0, state.unreadCount - 1),
      });
    } catch (error) {
      console.error("Mark notification read error:", error);
    }
  },
  async markAllRead() {
    try {
      await enhancedApi.markAllNotificationsRead();
      const state = get();
      set({
        notifications: state.notifications.map((notification) => ({
          ...notification,
          unread: false,
        })),
        unreadCount: 0,
      });
    } catch (error) {
      console.error("Mark all notifications read error:", error);
    }
  },
  async refreshUnreadCount() {
    try {
      const response = await enhancedApi.getUnreadNotificationCount();
      set({ unreadCount: response.unread_count });
    } catch (error) {
      console.error("Refresh unread count error:", error);
    }
  },
  setPanelOpen(isPanelOpen) {
    set({ isPanelOpen });
  },
}));

export const useWebSocketNotifications = () => {
  const socketUrl = useMemo(resolveWebSocketUrl, []);
  const { fetchNotifications, refreshUnreadCount } =
    useNotificationStore.getState();

  const { lastJsonMessage, readyState } = useWebSocket(socketUrl, {
    shouldReconnect: () => true,
    reconnectAttempts: 10,
    reconnectInterval: 3000,
  });

  useEffect(() => {
    if (lastJsonMessage && (lastJsonMessage as any).type === "notification") {
      const { activeCategory, isPanelOpen } = useNotificationStore.getState();
      refreshUnreadCount();
      if (isPanelOpen) {
        fetchNotifications(activeCategory);
      }
    }
  }, [lastJsonMessage]);

  return { readyState };
};
