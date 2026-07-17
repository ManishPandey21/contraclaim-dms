import React from "react";
import { Search, HelpCircle, LogOut } from "lucide-react";
import { useLocation, useNavigate } from "react-router-dom";
import { Button } from "@/components/ui/button";
import NotificationCenter from "@/components/NotificationCenter";
import { useAuth } from "@/hooks/use-auth";
import TenantScopeBar from "./TenantScopeBar";

const Navbar = () => {
  const location = useLocation();
  const navigate = useNavigate();
  const { logout } = useAuth();

  // Function to get the page title based on the current route
  const getPageTitle = () => {
    const path = location.pathname;

    if (path === "/") return "Dashboard";
    if (path === "/organizations") return "Organizations";
    if (path === "/projects") return "Projects";
    if (path === "/documents") return "Documents";
    if (path === "/upload") return "Upload Documents";
    if (path === "/users") return "Users Management";
    if (path === "/permissions") return "Permissions";
    if (path === "/settings") return "Settings";
    if (path === "/notifications") return "Notifications";
    if (path === "/profile") return "My Profile";
    if (path === "/letter-quality") return "Letter Quality Dashboard";
    if (path.includes("/documentviewer/") || path.includes("/document/")) {
      return "Document Viewer";
    }

    return "Document Management System";
  };

  const handleLogout = () => {
    logout();
  };

  return (
    <header className="bg-white border-b border-gray-200 py-3 px-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-xl font-semibold text-docsumo-text">
          {getPageTitle()}
        </h1>

        <TenantScopeBar />

        <div className="flex items-center space-x-4">
          <div className="relative hidden 2xl:block">
            <div className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none">
              <Search size={18} className="text-gray-400" />
            </div>
            <input
              type="text"
              placeholder="Search..."
              className="py-2 pl-10 pr-4 bg-gray-100 rounded-md border border-gray-200 focus:outline-none focus:ring-2 focus:ring-docsumo-blue/40 w-64 text-sm"
            />
          </div>
          <NotificationCenter />

          <Button variant="ghost" size="icon">
            <HelpCircle size={20} />
          </Button>
          <Button variant="ghost" size="icon" onClick={handleLogout}>
            <LogOut size={20} />
          </Button>
        </div>
      </div>
    </header>
  );
};

export default Navbar;
