import React from "react";
import { Search, HelpCircle, LogOut } from "lucide-react";
import { Button } from "@/components/ui/button";
import NotificationCenter from "@/components/NotificationCenter";
import { useAuth } from "@/hooks/use-auth";
import TenantScopeBar from "./TenantScopeBar";

const Navbar = () => {
  const { logout } = useAuth();

  const handleLogout = () => {
    logout();
  };

  return (
    <header className="bg-white border-b border-gray-200 py-3 px-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <TenantScopeBar />

        <div className="ml-auto flex flex-wrap items-center gap-2 sm:gap-4">
          <div className="relative min-w-56 flex-1 sm:flex-none">
            <div className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none">
              <Search size={18} className="text-gray-400" />
            </div>
            <input
              type="search"
              placeholder="Search..."
              aria-label="Global search"
              className="w-full py-2 pl-10 pr-4 bg-gray-100 rounded-md border border-gray-200 focus:outline-none focus:ring-2 focus:ring-docsumo-blue/40 sm:w-64 text-sm"
            />
          </div>
          <NotificationCenter />

          <Button variant="ghost" size="icon" aria-label="Help">
            <HelpCircle size={20} />
          </Button>
          <Button
            variant="ghost"
            size="icon"
            aria-label="Log out"
            onClick={handleLogout}
          >
            <LogOut size={20} />
          </Button>
        </div>
      </div>
    </header>
  );
};

export default Navbar;
