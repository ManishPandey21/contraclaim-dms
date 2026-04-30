import React, { useState, useEffect } from "react";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

const ProjectsPage = () => {
  const [projects, setProjects] = useState([]);
  const [loading, setLoading] = useState(false);
  const [editProject, setEditProject] = useState(null);
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [editedName, setEditedName] = useState("");
  const [editedGstNumber, setEditedGstNumber] = useState("");
  const [editedOrganizationId, setEditedOrganizationId] = useState("");

  useEffect(() => {
    const fetchProjects = async () => {
      setLoading(true);
      try {
        const response = await fetch("/api/projects", {
          headers: {
            Authorization: `Bearer ${localStorage.getItem("accessToken")}`,
          },
        });
        if (!response.ok) {
          throw new Error(`Failed to fetch projects: ${response.status}`);
        }
        const data = await response.json();
        setProjects(data);
      } catch (error) {
        console.error("Error fetching projects:", error);
        alert("Failed to fetch projects. Please try again later.");
      } finally {
        setLoading(false);
      }
    };

    fetchProjects();
  }, []);

  const handleEditClick = (project) => {
    setEditProject(project);
    setEditedName(project.name);
    setEditedGstNumber(project.gstNumber);
    setEditedOrganizationId(project.organization_id);
    setIsModalOpen(true);
  };

  const handleUpdateProject = async () => {
    try {
      const response = await fetch(`/api/projects/${editProject._id}`, {
        method: "PUT",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${localStorage.getItem("accessToken")}`,
        },
        body: JSON.stringify({
          name: editedName,
          gstNumber: editedGstNumber,
          organization_id: editedOrganizationId,
        }),
      });

      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(
          `Failed to update project: ${response.status} - ${errorData.detail}`
        );
      }

      const updatedProject = await response.json();

      setProjects((prevProjects) =>
        prevProjects.map((proj) =>
          proj._id === updatedProject._id ? updatedProject : proj
        )
      );

      setIsModalOpen(false);
      setEditProject(null);
      setEditedName("");
      setEditedGstNumber("");
      setEditedOrganizationId("");
    } catch (error) {
      console.error("Error updating project:", error);
      alert(
        error.message || "Failed to update project. Please try again later."
      );
    }
  };

  return (
    <div className="container mx-auto py-8">
      <h1 className="text-2xl font-bold mb-4">Projects</h1>
      <Card>
        <CardHeader>
          <CardTitle>Project List</CardTitle>
          <CardDescription>List of all registered projects.</CardDescription>
        </CardHeader>
        <CardContent>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Name</TableHead>
                <TableHead>GST Number</TableHead>
                <TableHead>Organization Name</TableHead>
                <TableHead>Contact Person</TableHead>
                <TableHead>Email ID</TableHead>
                <TableHead>Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {loading ? (
                <TableRow>
                  <TableCell colSpan={5} className="text-center">
                    Loading projects...
                  </TableCell>
                </TableRow>
              ) : (
                projects.map((proj) => (
                  <TableRow key={proj._id}>
                    <TableCell>{proj.name}</TableCell>
                    <TableCell>{proj.gstNumber}</TableCell>
                    <TableCell>{proj.organizationId}</TableCell>
                    <TableCell>{proj.adminName}</TableCell>
                    <TableCell>{proj.adminEmail}</TableCell>
                    <TableCell>
                      <Button
                        variant="outline"
                        size="sm"
                        className="mr-2"
                        onClick={() => handleEditClick(proj)}
                      >
                        Edit
                      </Button>
                      <Button variant="destructive" size="sm">
                        Delete
                      </Button>
                    </TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      {/* Edit Project Modal */}
      <Dialog open={isModalOpen} onOpenChange={setIsModalOpen}>
        <DialogContent className="sm:max-w-[425px]">
          <DialogHeader>
            <DialogTitle>Edit Project</DialogTitle>
            <DialogDescription>
              Make changes to the project details.
            </DialogDescription>
          </DialogHeader>
          <div className="grid gap-4 py-4">
            <div className="grid grid-cols-4 items-center gap-4">
              <Label htmlFor="name" className="text-right">
                Name
              </Label>
              <Input
                id="name"
                value={editedName}
                onChange={(e) => setEditedName(e.target.value)}
                className="col-span-3"
              />
            </div>
            <div className="grid grid-cols-4 items-center gap-4">
              <Label htmlFor="gstNumber" className="text-right">
                GST Number
              </Label>
              <Input
                id="gstNumber"
                value={editedGstNumber}
                onChange={(e) => setEditedGstNumber(e.target.value)}
                className="col-span-3"
              />
            </div>
            <div className="grid grid-cols-4 items-center gap-4">
              <Label htmlFor="organizationId" className="text-right">
                Organization ID
              </Label>
              <Input
                id="organization_Id"
                value={editedOrganizationId}
                onChange={(e) => setEditedOrganizationId(e.target.value)}
                className="col-span-3"
              />
            </div>
          </div>
          <DialogFooter>
            <Button
              type="button"
              variant="secondary"
              onClick={() => setIsModalOpen(false)}
            >
              Cancel
            </Button>
            <Button type="submit" onClick={handleUpdateProject}>
              Save Changes
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
};

export default ProjectsPage;
