import React, { useState, useEffect } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Card, CardContent } from "../components/ui/card";
import { Button } from "../components/ui/button";
import { Badge } from "../components/ui/badge";
import { SkeletonCard } from "../components/ui/skeleton";
import {
  Building,
  MapPin,
  Phone,
  Mail,
  FileText,
  Users,
  ArrowUpRight,
} from "lucide-react";
import {
  listOrganizations,
  type Organization,
} from "@/services/organizations-api";
import useHasPermission from "@/hooks/useHasPermission";
import { ENTITY_PERMISSIONS } from "@/constants/entityPermissions";

const OrganizationsPage = () => {
  const [organizations, setOrganizations] = useState<Organization[]>([]);
  const [loading, setLoading] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const navigate = useNavigate();
  const canCreateOrganization = useHasPermission(
    ENTITY_PERMISSIONS.organizations.create
  );
  const canUpdateOrganization = useHasPermission(
    ENTITY_PERMISSIONS.organizations.update
  );

  useEffect(() => {
    const fetchOrganizations = async () => {
      setLoading(true);
      try {
        // Management page: bypass the shared dropdown cache so CRUD always
        // starts from the server's current list.
        const data = await listOrganizations({ forceRefresh: true });
        setOrganizations(data);
      } catch (error: unknown) {
        console.error(
          "Error fetching organizations:",
          error instanceof Error ? error.message : "Unknown error"
        );
        alert("Failed to fetch organizations. Please try again later.");
      } finally {
        setLoading(false);
      }
    };

    fetchOrganizations();
  }, []);

  const filteredOrganizations: Organization[] = organizations.filter((org) => {
    const q = searchQuery.toLowerCase();
    const name = (org.name || "").toLowerCase();
    const pan = (org.panNumber || "").toLowerCase();
    return name.includes(q) || pan.includes(q);
  });

  const buildOrganizationProjectsLink = (organization: Organization) => {
    const searchParams = new URLSearchParams({
      org: organization._id,
      orgName: organization.name,
      source: "organization",
    });

    return {
      pathname: "/projects",
      search: `?${searchParams.toString()}`,
    };
  };

  if (loading) {
    return (
      <div className="container mx-auto py-8">
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {Array.from({ length: 6 }).map((_, i) => (
            <SkeletonCard key={i} />
          ))}
        </div>
      </div>
    );
  }

  return (
    <div className="container mx-auto py-8 animate-fade-in">
      <div className="flex justify-between items-center mb-6">
        <h1 className="text-2xl font-bold">Organizations</h1>
        {canCreateOrganization && (
          <Button
            onClick={() =>
              navigate("/register?tab=organization&mode=create")
            }
            className="flex items-center gap-2"
          >
            <Building size={16} />
            Add Organization
          </Button>
        )}
      </div>

      {/* Search input */}
      <div className="mb-6">
        <div className="relative">
          <input
            type="text"
            placeholder="Search organizations..."
            className="w-full px-4 py-2 border rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
          />
          <div className="absolute right-3 top-2.5">
            <svg
              xmlns="http://www.w3.org/2000/svg"
              className="h-5 w-5 text-gray-400"
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"
              />
            </svg>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
        {filteredOrganizations.length > 0 ? (
          filteredOrganizations.map((org) => (
            <Card
              key={org._id}
              className="overflow-hidden hover:shadow-lg transition-shadow duration-300"
            >
              <CardContent className="p-0">
                <div className="bg-gradient-to-r from-blue-500 to-blue-600 p-4">
                  <h2 className="text-xl font-bold text-white">{org.name}</h2>
                  <Badge className="mt-2 bg-blue-700">{org.panNumber}</Badge>
                </div>
                <div className="p-4 space-y-3">
                  <div className="flex items-start gap-2">
                    <MapPin size={16} className="mt-1 text-gray-500" />
                    <div>
                      <p className="text-sm">{org.address}</p>
                      <p className="text-sm">
                        {org.city}, {org.state} - {org.pinCode}
                      </p>
                    </div>
                  </div>
                  <div className="flex items-center gap-2">
                    <Phone size={16} className="text-gray-500" />
                    <p className="text-sm">{org.adminContact}</p>
                  </div>
                  <div className="flex items-center gap-2">
                    <Mail size={16} className="text-gray-500" />
                    <p className="text-sm">{org.adminEmail}</p>
                  </div>

                  <div className="grid grid-cols-3 gap-2 mt-4 border-t pt-3">
                    <div className="text-center">
                      <div className="flex items-center justify-center gap-1">
                        <FileText size={14} className="text-blue-500" />
                        <span className="font-bold">{org.projectsCount}</span>
                      </div>
                      <p className="text-xs text-gray-500">Projects</p>
                    </div>
                    <div className="text-center">
                      <div className="flex items-center justify-center gap-1">
                        <Users size={14} className="text-blue-500" />
                        <span className="font-bold">{org.employeesCount}</span>
                      </div>
                      <p className="text-xs text-gray-500">Employees</p>
                    </div>
                    <div className="text-center">
                      <div className="flex items-center justify-center gap-1">
                        <Mail size={14} className="text-blue-500" />
                        <span className="font-bold">{org.lettersCount}</span>
                      </div>
                      <p className="text-xs text-gray-500">Letters</p>
                    </div>
                  </div>

                  <div className="mt-3 flex justify-end gap-2">
                    {canUpdateOrganization && (
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() =>
                          navigate(
                            `/register?tab=organization&mode=edit&orgId=${org._id}`
                          )
                        }
                      >
                        Edit
                      </Button>
                    )}
                    <Button size="sm" asChild>
                      <Link
                        to={buildOrganizationProjectsLink(org)}
                        state={{
                          source: "organization",
                          organizationId: org._id,
                          organizationName: org.name,
                        }}
                      >
                        View Projects <ArrowUpRight className="ml-1 h-3 w-3" />
                      </Link>
                    </Button>
                  </div>
                </div>
              </CardContent>
            </Card>
          ))
        ) : (
          <div className="col-span-3 flex items-center justify-center h-64 bg-gray-100 rounded-lg">
            <p className="text-gray-500">
              No organizations found matching your search
            </p>
          </div>
        )}
      </div>
    </div>
  );
};

export default OrganizationsPage;
