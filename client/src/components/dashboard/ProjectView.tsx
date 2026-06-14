import React from "react";
import { Link } from "react-router-dom";
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
import { Progress } from "@/components/ui/progress";
import { MessageCircleQuestion } from "lucide-react";
import { Organization } from "./OrganizationView";

export interface Project {
  id: string;
  name: string;
  organization: string;
  totalLetters: number;
  inputRequired: number;
  closed: number;
}

export interface Letter {
  id: string;
  title: string;
  organization: string;
  project: string;
  status: string;
  updatedAt: string;
  updatedBy: string;
  assignedTo: string;
}

interface StatusData {
  status: string;
  count: number;
  color: string;
}

interface ProjectViewProps {
  projects: Project[];
  organizations: Organization[];
  letterStatuses: StatusData[];
  recentLetters: Letter[];
  totalLettersCount: number;
  selectedProject: string | null;
  setSelectedProject: (id: string | null) => void;
  selectedProjectDetails: Project | null;
  formatDate: (dateString: string) => string;
  getStatusBadge: (status: string) => React.ReactNode;
  getStatusIcon: (status: string) => React.ReactNode;
}

const ProjectView: React.FC<ProjectViewProps> = ({
  projects,
  organizations,
  letterStatuses,
  recentLetters,
  totalLettersCount,
  selectedProject,
  setSelectedProject,
  selectedProjectDetails,
  formatDate,
  getStatusBadge,
  getStatusIcon,
}) => {
  return (
    <div className="space-y-6">
      <Card className="glass-card">
        <CardHeader>
          <CardTitle className="text-lg font-semibold">
            Project Document Status
          </CardTitle>
          <CardDescription>Document metrics by project</CardDescription>
        </CardHeader>
        <CardContent>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Project</TableHead>
                <TableHead>Organization</TableHead>
                <TableHead>Total Documents</TableHead>
                <TableHead>Input Required</TableHead>
                <TableHead>Closed</TableHead>
                <TableHead className="text-right">Action</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {projects.map((project) => (
                <TableRow
                  key={project.id}
                  className={selectedProject === project.id ? "bg-blue-50" : ""}
                  onClick={() =>
                    setSelectedProject(
                      project.id === selectedProject ? null : project.id,
                    )
                  }
                >
                  <TableCell className="font-medium">{project.name}</TableCell>
                  <TableCell>
                    {
                      organizations.find(
                        (org) => org.id === project.organization,
                      )?.name
                    }
                  </TableCell>
                  <TableCell>{project.totalLetters}</TableCell>
                  <TableCell>
                    <div className="flex items-center">
                      <span
                        className={
                          project.inputRequired > 0
                            ? "text-amber-600 font-medium"
                            : ""
                        }
                      >
                        {project.inputRequired}
                      </span>
                      {project.inputRequired > 0 && (
                        <MessageCircleQuestion
                          className="ml-1 text-amber-600"
                          size={16}
                        />
                      )}
                    </div>
                  </TableCell>
                  <TableCell>{project.closed}</TableCell>
                  <TableCell className="text-right">
                    <Button
                      size="sm"
                      variant={
                        selectedProject === project.id ? "default" : "outline"
                      }
                      onClick={(e) => {
                        e.stopPropagation();
                        setSelectedProject(
                          project.id === selectedProject ? null : project.id,
                        );
                      }}
                    >
                      {selectedProject === project.id
                        ? "Hide Details"
                        : "View Details"}
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      {selectedProjectDetails && (
        <Card className="glass-card">
          <CardHeader>
            <CardTitle className="text-lg font-semibold">
              {selectedProjectDetails.name} - Status Breakdown
            </CardTitle>
            <CardDescription>
              Project in{" "}
              {
                organizations.find(
                  (org) => org.id === selectedProjectDetails.organization,
                )?.name
              }
            </CardDescription>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
              <div>
                <h3 className="text-lg font-medium mb-4">Status Breakdown</h3>
                <ul className="space-y-3">
                  {letterStatuses.slice(0, 6).map((status) => {
                    const percentage = Math.floor(
                      (status.count / totalLettersCount) * 100,
                    );
                    const count = Math.floor(
                      (percentage / 100) * selectedProjectDetails.totalLetters,
                    );

                    return (
                      <li key={status.status} className="space-y-1">
                        <div className="flex justify-between text-sm">
                          <span>{status.status}</span>
                          <span className="font-medium">{count}</span>
                        </div>
                        <Progress
                          value={percentage}
                          className="h-2 bg-gradient-to-r from-blue-500 to-blue-700"
                        />
                      </li>
                    );
                  })}
                </ul>
              </div>

              <div>
                <h3 className="text-lg font-medium mb-4">Recent Documents</h3>
                <ul className="space-y-3">
                  {recentLetters
                    .filter(
                      (letter) =>
                        letter.project === selectedProjectDetails.name,
                    )
                    .map((letter) => (
                      <li
                        key={letter.id}
                        className="flex items-center justify-between p-3 border rounded-md"
                      >
                        <div className="flex items-center gap-2">
                          {getStatusIcon(letter.status)}
                          <div>
                            <p className="font-medium">{letter.title}</p>
                            <div className="flex items-center gap-2 text-sm text-gray-500">
                              <span>{getStatusBadge(letter.status)}</span>
                              <span>&bull;</span>
                              <span>{formatDate(letter.updatedAt)}</span>
                            </div>
                          </div>
                        </div>
                        <Button size="sm" asChild>
                          <Link to={`/documentviewer/${letter.id}`}>View</Link>
                        </Button>
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

export default ProjectView;
