import React, { useMemo, useState } from "react";
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
  BookMarked,
  BookOpen,
  Activity,
  CreditCard,
  CalendarClock,
  GitCompareArrows,
  Landmark,
  FileSignature,
  PackageOpen,
  Network,
  ShieldCheck,
  Eye,
  ListTree,
} from "lucide-react";
import { cn } from "@/lib/utils";
import useRBAC from "@/hooks/useRBAC";
import { useSidebarIdentity } from "@/hooks/useSidebarIdentity";
import { isRouteAllowedByPermission, labelForRole } from "@/config/rolePermissions";

const Sidebar = () => {
  const [collapsed, setCollapsed] = useState(false);
  const [arbitrationOpen, setArbitrationOpen] = useState(false);
  const location = useLocation();
  const { roles, can } = useRBAC();

  const { displayName, initials, profilePhotoUrl } = useSidebarIdentity();

  const roleLabel = useMemo(() => {
    const priority = [
      "superadmin",
      "orgadmin",
      "projectadmin",
      "contraclaim_billing_admin",
      "contraclaim_drafting_manager",
      "doccontroller",
      "reporter",
      "settings_manager",
      "orguser",
      "projectuser",
      "limited_user",
    ];
    const selected = priority.find((role) => roles.includes(role)) || roles[0];
    return selected ? labelForRole(selected) : "User";
  }, [roles]);

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
    { path: "/legal-words", icon: <BookOpen size={20} />, label: "Learn Legal Words" },
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
      path: "/contracts/clauses",
      icon: <ListTree size={20} />,
      label: "Clause Index",
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
      path: "/contracts/viewer",
      icon: <Eye size={20} />,
      label: "Contract Viewer",
    },
    {
      path: "/contracts/appraisal",
      icon: <Sparkles size={20} />,
      label: "Contract Appraisal",
    },
    {
      path: "/contracts/timeline",
      icon: <Network size={20} />,
      label: "Contract Timeline",
    },

    // ── Contract registers ──
    { path: "/contracts/master", icon: <FileSignature size={20} />, label: "Contract Master" },
    { path: "/key-dates", icon: <CalendarClock size={20} />, label: "Key Dates" },
    { path: "/bank-guarantees", icon: <Landmark size={20} />, label: "Bank Guarantee Register" },
    { path: "/insurance", icon: <ShieldCheck size={20} />, label: "Insurance Management" },
    { path: "/ipc-bills", icon: <FileSignature size={20} />, label: "IPC / Bill Register" },
    { path: "/variations", icon: <GitCompareArrows size={20} />, label: "Variation Register" },

    // ── Claims & arbitration ──
    { path: "/claims", icon: <Scale size={20} />, label: "Claims Register" },
    { path: "/sla", icon: <Clock size={20} />, label: "SLA Tracker" },
    {
      path: "/chronology",
      icon: <ClipboardList size={20} />,
      label: "Chronology Builder",
    },
    {
      path: "/arbitration",
      icon: <Landmark size={20} />,
      label: "Arbitration",
    },
    {
      path: "/arbitration/cases",
      icon: <Landmark size={20} />,
      label: "Case Workspaces",
      sub: true,
    },
    {
      path: "/arbitration/drafts",
      icon: <FileText size={20} />,
      label: "Saved Drafts",
      sub: true,
    },
    {
      path: "/arbitration/claim",
      icon: <FileText size={20} />,
      label: "Draft Statement of Claim",
      sub: true,
    },
    {
      path: "/arbitration/defence",
      icon: <FileSignature size={20} />,
      label: "Draft Statement of Defence",
      sub: true,
    },
    {
      path: "/arbitration/rejoinder",
      icon: <GitCompareArrows size={20} />,
      label: "Draft Rejoinder",
      sub: true,
    },
    {
      path: "/arbitration/counterclaim",
      icon: <Scale size={20} />,
      label: "Draft Counterclaim",
      sub: true,
    },

    // ── User tools ──
    { path: "/tasks", icon: <ClipboardList size={20} />, label: "Tasks" },
    { path: "/concerns", icon: <MessageSquare size={20} />, label: "Concerns" },
    { path: "/retrieval-console", icon: <Search size={20} />, label: "Retrieval Console" },
    { path: "/folders", icon: <FolderClosed size={20} />, label: "Folder Structure" },
    { path: "/reports", icon: <BarChart size={20} />, label: "Reports & Analytics" },
    { path: "/notifications", icon: <Bell size={20} />, label: "Notifications" },

    // ── Admin tools ──
    {
      path: "/observability",
      icon: <Activity size={20} />,
      label: "Observability",
      permission: "reports:view",
    },
    { path: "/health", icon: <HeartPulse size={20} />, label: "System Health" },
    { path: "/users", icon: <Users size={20} />, label: "Users" },
    { path: "/permissions", icon: <UserCog size={20} />, label: "Permissions" },
    {
      path: "/plan-settings",
      icon: <Settings size={20} />,
      label: "Plan Settings",
      permission: "billing.plan.view",
    },
    {
      path: "/admin/billing-catalog",
      icon: <PackageOpen size={20} />,
      label: "Billing Catalog",
      permission: "billing.plan.manage",
    },
    {
      path: "/admin/legal-words",
      icon: <BookMarked size={20} />,
      label: "Legal Words Admin",
      permission: "system:admin",
    },
    {
      path: "/subscription-management",
      icon: <CreditCard size={20} />,
      label: "Subscription",
      permission: "billing.plan.view",
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
              "Contract Timeline", // After Contracts group → Contract registers
              "Variation Register", // End of Contract registers → Claims & arbitration
              "Draft Counterclaim", // End of Claims & arbitration → User tools
              "Notifications", // End of User tools → Admin tools
            ]);

            const isSub = "sub" in link && link.sub;

            // Hide sub-items when the accordion is closed
            if (isSub && !arbitrationOpen) return null;

            // "Arbitration" renders as a toggle button instead of a NavLink
            if (link.label === "Arbitration") {
              const anySubActive = location.pathname.startsWith("/arbitration/");
              return (
                <React.Fragment key={link.path}>
                  <li>
                    <button
                      type="button"
                      onClick={() => setArbitrationOpen((o) => !o)}
                      className={cn(
                        "w-full flex items-center space-x-3 px-3 py-2 rounded-md text-sm font-medium transition-all duration-200",
                        anySubActive
                          ? "bg-docsumo-blue/10 text-docsumo-blue"
                          : "text-gray-600 hover:bg-docsumo-blue/5 hover:text-docsumo-blue",
                        collapsed && "justify-center",
                      )}
                    >
                      <span>{link.icon}</span>
                      {!collapsed && (
                        <>
                          <span className="flex-1 text-left">{link.label}</span>
                          <ChevronRight
                            size={14}
                            className={cn(
                              "shrink-0 transition-transform duration-200",
                              arbitrationOpen && "rotate-90",
                            )}
                          />
                        </>
                      )}
                    </button>
                  </li>
                  {/* When accordion is closed, emit the section-end divider here since
                      Draft Counterclaim never renders and its divider never fires */}
                  {!arbitrationOpen && (
                    <li aria-hidden="true"><div className="my-2 h-px bg-gray-200" /></li>
                  )}
                </React.Fragment>
              );
            }

            return (
              <React.Fragment key={link.path}>
                <li>
                  <NavLink
                    to={link.path}
                    className={({ isActive }) =>
                      cn(
                        "flex items-center space-x-3 rounded-md text-sm font-medium transition-all duration-200",
                        isSub ? "px-3 py-1.5 pl-8" : "px-3 py-2",
                        isSub && "text-xs",
                        isActive
                          ? "bg-docsumo-blue/10 text-docsumo-blue"
                          : "text-gray-600 hover:bg-docsumo-blue/5 hover:text-docsumo-blue",
                        collapsed && "justify-center",
                      )
                    }
                  >
                    <span>{isSub ? <span className="text-gray-400">›</span> : link.icon}</span>
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
