import React from "react";
import {
  Card,
  CardHeader,
  CardTitle,
  CardDescription,
  CardContent,
} from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Button } from "@/components/ui/button";
import { AlertCircle } from "lucide-react";
import {
  PieChart,
  Pie,
  Cell,
  ResponsiveContainer,
  Tooltip,
  Legend,
} from "recharts";

export interface Organization {
  id: string;
  name: string;
  totalLetters: number;
  replyOverdue: number;
  underReview: number;
}

export interface Project {
  id: string;
  name: string;
  organization: string;
  totalLetters: number;
  inputRequired: number;
  closed: number;
}

interface StatusData {
  status: string;
  count: number;
  color: string;
}

interface OrganizationViewProps {
  organizations: Organization[];
  projects: Project[];
  letterStatuses: StatusData[];
  totalLettersCount: number;
  selectedOrg: string | null;
  setSelectedOrg: (id: string | null) => void;
  selectedOrgDetails: Organization | null;
  filteredProjects: Project[];
  setSelectedProject: (id: string | null) => void;
}

const OrganizationView: React.FC<OrganizationViewProps> = ({
  organizations,
  projects,
  letterStatuses,
  totalLettersCount,
  selectedOrg,
  setSelectedOrg,
  selectedOrgDetails,
  filteredProjects,
  setSelectedProject,
}) => {
  return (
    <div className="space-y-6">
      <Card className="glass-card">
        <CardHeader>
          <CardTitle className="text-lg font-semibold">
            Organization Document Status
          </CardTitle>
          <CardDescription>Document metrics by organization</CardDescription>
        </CardHeader>
        <CardContent>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Organization</TableHead>
                <TableHead>Total Documents</TableHead>
                <TableHead>Reply Overdue</TableHead>
                <TableHead>Under Review</TableHead>
                <TableHead className="text-right">Action</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {organizations.map((org) => (
                <TableRow
                  key={org.id}
                  className={selectedOrg === org.id ? "bg-blue-50" : ""}
                  onClick={() =>
                    setSelectedOrg(org.id === selectedOrg ? null : org.id)
                  }
                >
                  <TableCell className="font-medium">{org.name}</TableCell>
                  <TableCell>{org.totalLetters}</TableCell>
                  <TableCell>
                    <div className="flex items-center">
                      <span
                        className={
                          org.replyOverdue > 0 ? "text-red-600 font-medium" : ""
                        }
                      >
                        {org.replyOverdue}
                      </span>
                      {org.replyOverdue > 0 && (
                        <AlertCircle className="ml-1 text-red-600" size={16} />
                      )}
                    </div>
                  </TableCell>
                  <TableCell>{org.underReview}</TableCell>
                  <TableCell className="text-right">
                    <Button
                      size="sm"
                      variant={selectedOrg === org.id ? "default" : "outline"}
                      onClick={(e) => {
                        e.stopPropagation();
                        setSelectedOrg(org.id === selectedOrg ? null : org.id);
                      }}
                    >
                      {selectedOrg === org.id ? "Hide Details" : "View Details"}
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      {selectedOrgDetails && (
        <Card className="glass-card">
          <CardHeader>
            <CardTitle className="text-lg font-semibold">
              {selectedOrgDetails.name} - Document Distribution
            </CardTitle>
            <CardDescription>
              Status distribution for selected organization
            </CardDescription>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-6">
              <div className="h-72">
                <ResponsiveContainer width="100%" height="100%">
                  <PieChart>
                    <Pie
                      data={letterStatuses}
                      cx="50%"
                      cy="50%"
                      innerRadius={60}
                      outerRadius={100}
                      paddingAngle={2}
                      dataKey="count"
                      nameKey="status"
                      label={({ status, count }) => `${status}: ${count}`}
                    >
                      {letterStatuses.map((entry, index) => (
                        <Cell key={`cell-${index}`} fill={entry.color} />
                      ))}
                    </Pie>
                    <Tooltip
                      formatter={(value: number, name: string) => [
                        `${value} Documents`,
                        name,
                      ]}
                      contentStyle={{
                        borderRadius: "8px",
                        border: "1px solid #E5E7EB",
                        boxShadow: "0 2px 8px rgba(0,0,0,0.1)",
                      }}
                    />
                    <Legend />
                  </PieChart>
                </ResponsiveContainer>
              </div>

              <div>
                <h3 className="text-lg font-medium mb-3">
                  Projects in {selectedOrgDetails.name}
                </h3>
                <ul className="space-y-3">
                  {filteredProjects.map((project) => (
                    <li
                      key={project.id}
                      className="p-3 border rounded-md hover:bg-gray-50 cursor-pointer"
                      onClick={() => setSelectedProject(project.id)}
                    >
                      <div className="flex justify-between items-center">
                        <div>
                          <p className="font-medium">{project.name}</p>
                          <p className="text-sm text-gray-500">
                            {project.totalLetters} Documents •{" "}
                            {project.inputRequired} Input Required
                          </p>
                        </div>
                        <Button size="sm" variant="ghost">
                          View
                        </Button>
                      </div>
                    </li>
                  ))}
                </ul>
              </div>
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
};

export default OrganizationView;
