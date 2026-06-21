import React, { useEffect, useMemo, useState } from "react";
import { NavLink, useLocation } from "react-router-dom";
import {
  LayoutDashboard,
  FileText,
  Upload,
  Users,
  UserCog,
  Settings,
  ChevronLeft,
  ChevronRight,
  Building,
  FolderClosed,
  FileSearch,
  Mail,
  ClipboardList,
  BarChart,
  Home,
  HeartPulse,
  Microscope,
  Search,
  Text,
  TextSearch,
  Files,
  MessageSquare,
  Scale,
  Clock,
  Sparkles,
  Bell,
  CreditCard,
  CalendarClock,
  GitCompareArrows,
  Landmark,
  FileSignature,
  PackageOpen,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { enhancedApi as api } from "@/services/enhanced-api";
import { getCurrentUserProfile } from "@/services/session-api";
import useRBAC from "@/hooks/useRBAC";
import { isRouteAllowedByPermission } from "@/config/rolePermissions";

const Sidebar = () => {
  const [collapsed, setCollapsed] = useState(false);
  const location = useLocation();
  const { roles, can } = useRBAC();

  // Dynamic user identity shown in the sidebar footer
  const [displayName, setDisplayName] = useState<string>("User Name");
  const [initials, setInitials] = useState<string>("US");
  const [profilePhotoUrl, setProfilePhotoUrl] = useState<string>("");

  const roleLabel = useMemo(() => {
    const norm = roles.map((r) => String(r).toLowerCase());
    return norm.includes("superadmin")
      ? "Super Admin"
      : norm.includes("orgadmin")
        ? "Organization Admin"
        : norm.includes("orguser")
          ? "Organization User"
          : norm.includes("projectadmin")
            ? "Project Admin"
            : norm.includes("projectuser")
              ? "Project User"
              : norm[0]
                ? norm[0].replace(/\b\w/g, (c) => c.toUpperCase())
                : "User";
  }, [roles]);

  useEffect(() => {
    const load = async () => {
      try {
        let name = "User Name";
        let photo = "";

        // Prefer profile names if available
        try {
          const profile = await api.getProfile().catch(() => null as any);
          if (profile) {
            const first = (profile as any).first_name || "";
            const last = (profile as any).last_name || "";
            const email = (profile as any).email || "";
            const candidate = `${first} ${last}`.trim() || email;
            if (candidate) name = candidate;
            photo = (profile as any).profile_photo_url || "";
          }
        } catch {
          // ignore
        }

        // Fallback to /me when full profile data is unavailable.
        if (!name || name === "User Name") {
          try {
            const me = await getCurrentUserProfile();
            const candidate = me?.email || me?.id;
            if (candidate) name = candidate;
          } catch {
            // ignore
          }
        }

        // Local cache override from profile save flow
        try {
          const cacheRaw =
            typeof window !== "undefined"
              ? window.localStorage.getItem("profile_cache")
              : null;
          if (cacheRaw) {
            const cache = JSON.parse(cacheRaw);
            if (cache?.full_name) name = cache.full_name;
            if (cache?.profile_photo_url) photo = cache.profile_photo_url;
          }
        } catch {
          // ignore
        }

        // Compute initials from name
        const parts = String(name).trim().split(/\s+/).filter(Boolean);
        const init =
          (parts[0]?.[0] || "U").toUpperCase() +
          (parts[1]?.[0] || parts[0]?.[1] || "S").toUpperCase();

        setDisplayName(name);
        setInitials(init);
        setProfilePhotoUrl(photo || "");
      } catch {
        // ignore
      }
    };
    load();
  }, [location.pathname]);

  const toggleSidebar = () => {
    setCollapsed(!collapsed);
  };

  const sidebarLinks = useMemo(() => [
    { path: "/overview", icon: <Home size={20} />, label: "Overview" },
    {
      path: "/dashboard",
      icon: <LayoutDashboard size={20} />,
      label: "Dashboard",
    },
    { path: "/register", icon: <Upload size={20} />, label: "Regisration" },

    {
      path: "/organizations",
      icon: <Building size={20} />,
      label: "Organizations",
    },
    { path: "/projects", icon: <FolderClosed size={20} />, label: "Projects" },
    { path: "/parties", icon: <Users size={20} />, label: "Add Stakeholders" },
    {
      path: "/email-groups",
      icon: <Users size={20} />,
      label: "Email Groups",
    },

    { path: "/upload", icon: <Upload size={20} />, label: "Upload Letters" },
    { path: "/documents", icon: <Files size={20} />, label: "Letters Library" },
    {
      path: "/documentsearch",
      icon: <Search size={20} />,
      label: "Search Letters",
    },
    //{ path: "/reference/663c9a72a3b039f4cd364bcf", icon: <FileSearch size={20} />, label: "Reference Page" },

    //{
    //  path: "/documentviewer",
    //  icon: <FileSearch size={20} />,
    //  label: "Doc Viewer",
    // },
    { path: "/letters", icon: <Mail size={20} />, label: "Letter Drafting" },
    {
      path: "/letter-quality",
      icon: <BarChart size={20} />,
      label: "Letter Quality",
    },
    {
      path: "/letter-templates",
      icon: <Text size={20} />,
      label: "Letter Templates",
    },
    {
      path: "/letter-templates/new/edit",
      icon: <Text size={20} />,
      label: "Create Template",
    },
    {
      path: "/contracts/upload",
      icon: <Upload size={20} />,
      label: "Upload Contract",
    },
    {
      path: "/contracts/search",
      icon: <FileSearch size={20} />,
      label: "Search Clauses",
    },
    {
      path: "/contracts/qa",
      icon: <MessageSquare size={20} />,
      label: "Contract Q&A",
    },
    {
      path: "/contracts/appraisal",
      icon: <Sparkles size={20} />,
      label: "Contract Appraisal",
    },

    { path: "/claims", icon: <Scale size={20} />, label: "Claims Register" },
    { path: "/sla", icon: <Clock size={20} />, label: "SLA Tracker" },
    { path: "/key-dates", icon: <CalendarClock size={20} />, label: "Key Dates" },
    { path: "/contracts/master", icon: <FileSignature size={20} />, label: "Contract Master" },
    { path: "/variations", icon: <GitCompareArrows size={20} />, label: "Variation Register" },
    { path: "/bank-guarantees", icon: <Landmark size={20} />, label: "Bank Guarantee Register" },
    { path: "/ipc-bills", icon: <FileSignature size={20} />, label: "IPC / Bill Register" },
    { path: "/concerns", icon: <MessageSquare size={20} />, label: "Concerns" },
    { path: "/retrieval-console", icon: <Search size={20} />, label: "Retrieval Console" },
    {
      path: "/observability",
      icon: <Activity size={20} />,
      label: "Observability",
      permission: "reports:view",
    },
    {
      path: "/tasks",
      icon: <ClipboardList size={20} />,
      label: "Tasks",
    },
    {
      path: "/folders",
      icon: <FolderClosed size={20} />,
      label: "Folder Structure",
    },
    {
      path: "/reports",
      icon: <BarChart size={20} />,
      label: "Reports & Analytics",
    },
    {
      path: "/notifications",
      icon: <Bell size={20} />,
      label: "Notifications",
    },
    {
      path: "/health",
      icon: <HeartPulse size={20} />,
      label: "System Health",
    },
    { path: "/users", icon: <Users size={20} />, label: "Users" },
    { path: "/permissions", icon: <UserCog size={20} />, label: "Permissions" },
    {
      path: "/plan-settings",
      icon: <Settings size={20} />,
      label: "Plan Settings",
      permission: "subscription.entitlement.manage",
    },
    {
      path: "/admin/billing-catalog",
      icon: <PackageOpen size={20} />,
      label: "Billing Catalog",
      permission: "billing.plan.manage",
    },
    {
      path: "/subscription-management",
      icon: <CreditCard size={20} />,
      label: "Subscription",
      permission: "subscription.entitlement.manage",
    },
    { path: "/settings", icon: <Settings size={20} />, label: "Settings" },
  ], []);

  const visibleLinks = useMemo(
    () =>
      sidebarLinks.filter((link) => {
        if (!isRouteAllowedByPermission(can, link.path)) return false;
        if ("permission" in link && link.permission) {
          return roles.includes("superadmin") || can(link.permission);
        }
        return true;
      }),
    [roles, sidebarLinks, can],
  );

  return (
    <aside
      className={cn(
        "bg-white border-r border-gray-200 h-screen flex flex-col transition-all duration-300 ease-in-out relative",
        collapsed ? "w-20" : "w-64",
      )}
    >
      <div className="flex items-center p-4 border-b border-gray-200">
        {!collapsed && (
          <div className="w-60 flex justify-center">
            <img
              src="/contraclaim2.png"
              alt="ContraClaim DMS"
              className="h-8 w-auto"
            />
          </div>
        )}
        {collapsed && (
          <img
            src="/page.png"
            alt="ContraClaim DMS"
            className="h-8 w-auto mx-auto"
          />
        )}
        <button
          onClick={toggleSidebar}
          className="absolute right-[-12px] top-12 bg-white rounded-full p-1 border border-gray-200 text-gray-500 hover:text-docsumo-blue transition-colors"
        >
          {collapsed ? <ChevronRight size={16} /> : <ChevronLeft size={16} />}
        </button>
      </div>

      <nav className="flex-1 py-4 overflow-y-auto">
        <ul className="space-y-1 px-3">
          {visibleLinks.map((link) => {
            const dividerAfter = new Set<string>([
              "Dashboard", // After Overview & Dashboard
              "Email Groups", // After Add Stakeholder
              "Search Letters", // After Search letter
              "Create Template", // After Letter Templates
              "Contract Appraisal", // After the Contracts group
              "Tasks", // After the Claims / SLA / Tasks group
            ]);

            return (
              <React.Fragment>
                <li>
                  <NavLink
                    to={link.path}
                    className={({ isActive }) =>
                      cn(
                        "flex items-center space-x-3 px-3 py-2 rounded-md text-sm font-medium transition-all duration-200",
                        isActive
                          ? "bg-docsumo-blue/10 text-docsumo-blue"
                          : "text-gray-600 hover:bg-docsumo-blue/5 hover:text-docsumo-blue",
                        collapsed && "justify-center",
                      )
                    }
                  >
                    <span>{link.icon}</span>
                    {!collapsed && <span>{link.label}</span>}
                  </NavLink>
                </li>
                {dividerAfter.has(link.label) && (
                  <li aria-hidden="true">
                    <div className="my-2 h-px bg-gray-200" />
                  </li>
                )}
              </React.Fragment>
            );
          })}
        </ul>
      </nav>

      <div className="p-4 border-t border-gray-200">
        <NavLink
          to="/profile"
          className={({ isActive }) =>
            cn(
              "flex items-center space-x-3 px-3 py-2 rounded-md text-sm font-medium transition-all duration-200",
              isActive
                ? "bg-docsumo-blue/10 text-docsumo-blue"
                : "text-gray-600 hover:bg-docsumo-blue/5 hover:text-docsumo-blue",
              collapsed && "justify-center",
            )
          }
        >
          <div className="w-8 h-8 rounded-full bg-docsumo-blue/20 flex items-center justify-center text-docsumo-blue overflow-hidden">
            {profilePhotoUrl ? (
              <img
                src={profilePhotoUrl}
                alt={displayName}
                className="w-full h-full object-cover"
              />
            ) : (
              <span className="text-xs font-medium">{initials}</span>
            )}
          </div>
          {!collapsed && (
            <div className="flex flex-col">
              <span className="text-sm font-medium">{displayName}</span>
              <span className="text-xs text-gray-500">{roleLabel}</span>
            </div>
          )}
        </NavLink>
      </div>
    </aside>
  );
};

export default Sidebar;
