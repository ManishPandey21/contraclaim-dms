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
  DialogTrigger,
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

const OrganizationsPage = () => {
  const [organizations, setOrganizations] = useState([]);
  const [loading, setLoading] = useState(false);
  const [editOrganization, setEditOrganization] = useState(null);
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [editedName, setEditedName] = useState("");
  const [editedPanNumber, setEditedPanNumber] = useState("");

  useEffect(() => {
    const fetchOrganizations = async () => {
      setLoading(true);
      try {
        const response = await fetch("/api/organizations", {
          headers: {
            Authorization: `Bearer ${localStorage.getItem("accessToken")}`,
          },
        });
        if (!response.ok) {
          throw new Error(`Failed to fetch organizations: ${response.status}`);
        }
        const data = await response.json();
        setOrganizations(data);
      } catch (error) {
        console.error("Error fetching organizations:", error);
        alert("Failed to fetch organizations. Please try again later.");
      } finally {
        setLoading(false);
      }
    };

    fetchOrganizations();
  }, []);

  const handleEditClick = (organization) => {
    setEditOrganization(organization);
    setEditedName(organization.name);
    setEditedPanNumber(organization.panNumber);
    setIsModalOpen(true);
  };

  const handleUpdateOrganization = async () => {
    try {
      const response = await fetch(
        `/api/organizations/${editOrganization._id}`,
        {
          method: "PUT",
          headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${localStorage.getItem("accessToken")}`,
          },
          body: JSON.stringify({
            name: editedName,
            panNumber: editedPanNumber,
          }),
        }
      );

      if (!response.ok) {
        throw new Error(`Failed to update organization: ${response.status}`);
      }

      const updatedOrganization = await response.json();

      // Update the organizations list with the updated organization
      setOrganizations((prevOrganizations) =>
        prevOrganizations.map((org) =>
          org._id === updatedOrganization._id ? updatedOrganization : org
        )
      );

      setIsModalOpen(false);
      setEditOrganization(null);
      setEditedName("");
      setEditedPanNumber("");
    } catch (error) {
      console.error("Error updating organization:", error);
      alert("Failed to update organization. Please try again later.");
    }
  };

  return (
    <div className="container mx-auto py-8">
      <h1 className="text-2xl font-bold mb-4">Organizations</h1>
      <Card>
        <CardHeader>
          <CardTitle>Organization List</CardTitle>
          <CardDescription>
            List of all registered organizations.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Name</TableHead>
                <TableHead>PAN Number</TableHead>
                <TableHead>Contact Person</TableHead>
                <TableHead>Email ID</TableHead>
                <TableHead>Contact Number</TableHead>
                <TableHead>Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {loading ? (
                <TableRow>
                  <TableCell colSpan={4} className="text-center">
                    Loading organizations...
                  </TableCell>
                </TableRow>
              ) : (
                organizations.map((org) => (
                  <TableRow key={org._id}>
                    <TableCell>{org.name}</TableCell>
                    <TableCell>{org.panNumber}</TableCell>
                    <TableCell>{org.adminName}</TableCell>
                    <TableCell>{org.adminEmail}</TableCell>
                    <TableCell>{org.adminContact}</TableCell>
                    <TableCell>
                      <Button
                        variant="outline"
                        size="sm"
                        className="mr-2"
                        onClick={() => handleEditClick(org)}
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

      {/* Edit Organization Modal */}
      <Dialog open={isModalOpen} onOpenChange={setIsModalOpen}>
        <DialogContent className="sm:max-w-[425px]">
          <DialogHeader>
            <DialogTitle>Edit Organization</DialogTitle>
            <DialogDescription>
              Make changes to the organization details.
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
              <Label htmlFor="panNumber" className="text-right">
                PAN Number
              </Label>
              <Input
                id="panNumber"
                value={editedPanNumber}
                onChange={(e) => setEditedPanNumber(e.target.value)}
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
            <Button type="submit" onClick={handleUpdateOrganization}>
              Save changes
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
};

export default OrganizationsPage;
