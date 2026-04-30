import React, { useState, useEffect } from "react";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  Edit,
  Loader2,
  Mail,
  Phone,
  PlusCircle,
  Trash,
  Users,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form";
import { useForm } from "react-hook-form";
import { z } from "zod";
import { zodResolver } from "@hookform/resolvers/zod";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { toast } from "sonner";
import { enhancedApi as api } from "@/services/enhanced-api";
import { Party, Representative, Project, Organization } from "@/types/api";

// --- Zod Schemas for Validation ---
const emailSchema = z
  .string()
  .email("Invalid email address")
  .min(1, "Email is required");

const partyFormSchema = z.object({
  name: z.string().min(2, "Name must be at least 2 characters").max(50),
  type: z.enum(["Organization", "Individual"]),
  contactEmail: z
    .string()
    .email("Invalid email address")
    .optional()
    .or(z.literal("")),
  contactPhone: z.string().optional(),
  projects: z.array(z.string()).optional(),
});

type PartyFormValues = z.infer<typeof partyFormSchema>;

const representativeFormSchema = z.object({
  name: z.string().min(2, "Name must be at least 2 characters"),
  email: emailSchema,
  contact_number: z.string().optional().or(z.literal("")),
  designation: z.string().optional().or(z.literal("")),
  is_primary: z.boolean().default(false),
});

type RepresentativeFormValues = z.infer<typeof representativeFormSchema>;

const PartiesInvolvedPage: React.FC = () => {
  const [activeTab, setActiveTab] = useState("projects");
  const [projects, setProjects] = useState<Project[]>([]);
  const [organizations, setOrganizations] = useState<Organization[]>([]);
  const [otherStakeholders, setOtherStakeholders] = useState<Party[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState("");
  const [selectedEntity, setSelectedEntity] = useState<
    Party | Project | Organization | null
  >(null);
  const [isNewPartyDialogOpen, setIsNewPartyDialogOpen] = useState(false);
  const [isNewRepresentativeDialogOpen, setIsNewRepresentativeDialogOpen] =
    useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [partyToEdit, setPartyToEdit] = useState<Party | null>(null);
  const [representativeToEdit, setRepresentativeToEdit] =
    useState<Representative | null>(null);

  const partyForm = useForm<PartyFormValues>({
    resolver: zodResolver(partyFormSchema),
    defaultValues: {
      name: "",
      type: "Individual",
      contactEmail: "",
      contactPhone: "",
      projects: [],
    },
  });

  const representativeForm = useForm<RepresentativeFormValues>({
    resolver: zodResolver(representativeFormSchema),
    defaultValues: {
      name: "",
      email: "",
      contact_number: "",
      designation: "",
      is_primary: false,
    },
  });

  const fetchData = async () => {
    try {
      setIsLoading(true);
      const [projectsData, organizationsData, partiesData] = await Promise.all([
        api.getProjects(),
        api.getOrganizations(),
        api.getParties(),
      ]);

      const projectsWithReps = await Promise.all(
        projectsData.map(async (p) => ({
          ...p,
          representatives: await api.getProjectRepresentatives(p._id),
        }))
      );

      const orgsWithReps = await Promise.all(
        organizationsData.map(async (o) => ({
          ...o,
          representatives: await api.getOrganizationRepresentatives(o._id),
        }))
      );

      setProjects(projectsWithReps);
      setOrganizations(orgsWithReps);
      setOtherStakeholders(partiesData.filter((p) => p.type === "Individual"));
    } catch (error) {
      toast.error("Failed to fetch data.");
      console.error("Error fetching data:", error);
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    fetchData();
  }, []);

  const onPartySubmit = async (values: PartyFormValues) => {
    try {
      setIsSubmitting(true);
      if (partyToEdit) {
        const updatedParty = await api.updateParty(partyToEdit._id, values);
        setOtherStakeholders((prev) =>
          prev.map((p) => (p._id === partyToEdit._id ? updatedParty : p))
        );
        toast.success("Stakeholder updated successfully");
      } else {
        const newParty = await api.createParty({
          name: values.name,
          type: values.type,
          contactEmail: values.contactEmail || null,
          contactPhone: values.contactPhone || null,
          projects: values.projects || [],
          organizationId: null,
          address: null,
          city: null,
          state: null,
          pinCode: null,
          country: null,
        });
        setOtherStakeholders((prev) => [...prev, newParty]);
        toast.success("Stakeholder created successfully");
      }
      partyForm.reset();
      setPartyToEdit(null);
      setIsNewPartyDialogOpen(false);
    } catch (error) {
      toast.error(`Failed to ${partyToEdit ? "update" : "create"} stakeholder`);
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleDeleteParty = async (partyId: string) => {
    try {
      await api.deleteParty(partyId);
      setOtherStakeholders((prev) => prev.filter((p) => p._id !== partyId));
      toast.success("Stakeholder deleted successfully");
    } catch (error) {
      toast.error("Failed to delete stakeholder");
    }
  };

  const handleDeleteRepresentative = async (repId: string) => {
    try {
      await api.deleteRepresentativeById(repId);
      toast.success("Representative deleted successfully");
      fetchData(); // Refetch all data to ensure consistency
    } catch (error) {
      toast.error("Failed to delete representative");
    }
  };

  const onRepresentativeSubmit = async (values: RepresentativeFormValues) => {
    if (!selectedEntity) return;

    try {
      setIsSubmitting(true);
      const repData = {
        name: values.name,
        email: values.email,
        contact_number: values.contact_number || null,
        designation: values.designation || null,
        is_primary: values.is_primary,
      };

      if (representativeToEdit) {
        await api.updateRepresentativeById(representativeToEdit._id, repData);
        toast.success("Representative updated successfully");
      } else {
        if (activeTab === "projects") {
          await api.addProjectRepresentative(selectedEntity._id, {
            ...repData,
            level: "project",
            use_head_office: false,
          });
        } else if (activeTab === "organisations") {
          await api.addOrganizationRepresentative(selectedEntity._id, {
            ...repData,
            level: "organization",
            use_head_office: false,
          });
        } else {
          await api.addRepresentative((selectedEntity as Party)._id, {
            ...repData,
            level: "organization",
            use_head_office: false,
          });
        }
        toast.success("Representative added successfully");
      }

      fetchData(); // Refetch all data to ensure consistency
      representativeForm.reset();
      setRepresentativeToEdit(null);
      setIsNewRepresentativeDialogOpen(false);
    } catch (error) {
      toast.error(
        `Failed to ${representativeToEdit ? "update" : "add"} representative`
      );
    } finally {
      setIsSubmitting(false);
    }
  };

  const renderRepresentatives = (entity: Party | Project | Organization) => (
    <div className="mt-4">
      <div className="flex justify-between items-center mb-2">
        <h4 className="font-semibold">Authorised Representatives</h4>
        <Button
          size="sm"
          onClick={() => {
            setSelectedEntity(entity);
            setIsNewRepresentativeDialogOpen(true);
          }}
        >
          <PlusCircle className="mr-2 h-4 w-4" /> Add Representative
        </Button>
      </div>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Name</TableHead>
            <TableHead>Designation</TableHead>
            <TableHead>Contact No.</TableHead>
            <TableHead>Email</TableHead>
            <TableHead>Primary</TableHead>
            <TableHead>Actions</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {entity.representatives && entity.representatives.length > 0 ? (
            entity.representatives.map((rep) => (
              <TableRow key={rep._id}>
                <TableCell>{rep.name}</TableCell>
                <TableCell>{rep.designation || "N/A"}</TableCell>
                <TableCell>{rep.contact_number || "N/A"}</TableCell>
                <TableCell>{rep.email}</TableCell>
                <TableCell>
                  {rep.is_primary ? (
                    <Badge>Yes</Badge>
                  ) : (
                    <Badge variant="secondary">No</Badge>
                  )}
                </TableCell>
                <TableCell>
                  <Button
                    variant="ghost"
                    size="icon"
                    onClick={() => {
                      setSelectedEntity(entity);
                      setRepresentativeToEdit(rep);
                      representativeForm.reset(rep);
                      setIsNewRepresentativeDialogOpen(true);
                    }}
                  >
                    <Edit className="h-4 w-4" />
                  </Button>
                  <Button
                    variant="ghost"
                    size="icon"
                    onClick={() => handleDeleteRepresentative(rep._id)}
                  >
                    <Trash className="h-4 w-4" />
                  </Button>
                </TableCell>
              </TableRow>
            ))
          ) : (
            <TableRow>
              <TableCell
                colSpan={6}
                className="text-center text-muted-foreground"
              >
                No representatives found. Click "Add Representative" to add one.
              </TableCell>
            </TableRow>
          )}
        </TableBody>
      </Table>
    </div>
  );

  if (isLoading) {
    return (
      <div className="flex justify-center items-center h-64">
        <Loader2 className="h-8 w-8 animate-spin" />
      </div>
    );
  }

  return (
    <div className="container mx-auto p-4">
      <h1 className="text-3xl font-bold mb-6">Stakeholder Management</h1>

      <Tabs value={activeTab} onValueChange={setActiveTab} className="w-full">
        <TabsList className="grid w-full grid-cols-3">
          <TabsTrigger value="projects">Projects</TabsTrigger>
          <TabsTrigger value="organisations">Organisations</TabsTrigger>
          <TabsTrigger value="other_stakeholders">
            Other Stakeholders
          </TabsTrigger>
        </TabsList>

        <TabsContent value="projects">
          <Card>
            <CardHeader>
              <CardTitle>Projects</CardTitle>
              <CardDescription>
                View and manage representatives for each project.
              </CardDescription>
            </CardHeader>
            <CardContent>
              {projects.map((project) => (
                <Card key={project._id} className="mb-4">
                  <CardHeader>
                    <CardTitle>{project.name}</CardTitle>
                  </CardHeader>
                  <CardContent>{renderRepresentatives(project)}</CardContent>
                </Card>
              ))}
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="organisations">
          <Card>
            <CardHeader>
              <CardTitle>Organisations</CardTitle>
              <CardDescription>
                Manage representatives for each organisation.
              </CardDescription>
            </CardHeader>
            <CardContent>
              {organizations.map((org) => (
                <Card key={org._id} className="mb-4">
                  <CardHeader>
                    <CardTitle>{org.name}</CardTitle>
                  </CardHeader>
                  <CardContent>{renderRepresentatives(org)}</CardContent>
                </Card>
              ))}
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="other_stakeholders">
          <Card>
            <CardHeader>
              <div className="flex justify-between items-center">
                <div>
                  <CardTitle>Other Stakeholders</CardTitle>
                  <CardDescription>
                    Manage other stakeholders and their representatives.
                  </CardDescription>
                </div>
                <div className="flex items-center gap-2">
                  <Input
                    placeholder="Search stakeholders..."
                    value={searchQuery}
                    onChange={(e) => setSearchQuery(e.target.value)}
                    className="max-w-sm"
                  />
                  <Button onClick={() => setIsNewPartyDialogOpen(true)}>
                    <PlusCircle className="mr-2 h-4 w-4" />
                    Register New Stakeholder
                  </Button>
                </div>
              </div>
            </CardHeader>
            <CardContent>
              {otherStakeholders
                .filter(
                  (party) =>
                    party.name
                      .toLowerCase()
                      .includes(searchQuery.toLowerCase()) ||
                    (party.contactEmail &&
                      party.contactEmail
                        .toLowerCase()
                        .includes(searchQuery.toLowerCase())) ||
                    (party.contactPhone &&
                      party.contactPhone.includes(searchQuery))
                )
                .map((party) => (
                  <Card key={party._id} className="mb-4">
                    <CardHeader>
                      <CardTitle className="flex items-center justify-between">
                        <span>{party.name}</span>
                        <div>
                          <Button
                            variant="ghost"
                            size="icon"
                            onClick={() => {
                              setPartyToEdit(party);
                              partyForm.reset(party);
                              setIsNewPartyDialogOpen(true);
                            }}
                          >
                            <Edit className="h-4 w-4" />
                          </Button>
                          <Button
                            variant="ghost"
                            size="icon"
                            onClick={() => handleDeleteParty(party._id)}
                          >
                            <Trash className="h-4 w-4" />
                          </Button>
                          <Badge variant="outline">{party.type}</Badge>
                        </div>
                      </CardTitle>
                      <CardDescription>
                        <div className="flex items-center gap-4">
                          {party.contactEmail && (
                            <span className="flex items-center">
                              <Mail className="h-3 w-3 mr-1" />
                              {party.contactEmail}
                            </span>
                          )}
                          {party.contactPhone && (
                            <span className="flex items-center">
                              <Phone className="h-3 w-3 mr-1" />
                              {party.contactPhone}
                            </span>
                          )}
                        </div>
                      </CardDescription>
                    </CardHeader>
                    <CardContent>{renderRepresentatives(party)}</CardContent>
                  </Card>
                ))}
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>

      {/* Dialog for New Stakeholder */}
      <Dialog
        open={isNewPartyDialogOpen}
        onOpenChange={(isOpen) => {
          if (!isOpen) {
            setPartyToEdit(null);
            partyForm.reset();
          }
          setIsNewPartyDialogOpen(isOpen);
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {partyToEdit ? "Edit Stakeholder" : "Register New Stakeholder"}
            </DialogTitle>
          </DialogHeader>
          <Form {...partyForm}>
            <form
              onSubmit={partyForm.handleSubmit(onPartySubmit)}
              className="space-y-4"
            >
              <FormField
                control={partyForm.control}
                name="name"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Name</FormLabel>
                    <FormControl>
                      <Input placeholder="Full Name" {...field} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={partyForm.control}
                name="contactEmail"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Email</FormLabel>
                    <FormControl>
                      <Input type="email" placeholder="Email" {...field} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={partyForm.control}
                name="contactPhone"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Phone</FormLabel>
                    <FormControl>
                      <Input placeholder="Phone Number" {...field} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <DialogFooter>
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => setIsNewPartyDialogOpen(false)}
                >
                  Cancel
                </Button>
                <Button type="submit" disabled={isSubmitting}>
                  {isSubmitting && (
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  )}
                  Submit
                </Button>
              </DialogFooter>
            </form>
          </Form>
        </DialogContent>
      </Dialog>

      {/* Dialog for New Representative */}
      <Dialog
        open={isNewRepresentativeDialogOpen}
        onOpenChange={(isOpen) => {
          if (!isOpen) {
            setRepresentativeToEdit(null);
            representativeForm.reset();
          }
          setIsNewRepresentativeDialogOpen(isOpen);
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {representativeToEdit
                ? "Edit Representative"
                : "Add New Representative"}
            </DialogTitle>
            <DialogDescription>For {selectedEntity?.name}</DialogDescription>
          </DialogHeader>
          <Form {...representativeForm}>
            <form
              onSubmit={representativeForm.handleSubmit(onRepresentativeSubmit)}
              className="space-y-4"
            >
              <FormField
                control={representativeForm.control}
                name="name"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Name</FormLabel>
                    <FormControl>
                      <Input placeholder="Full Name" {...field} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={representativeForm.control}
                name="designation"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Designation</FormLabel>
                    <FormControl>
                      <Input placeholder="Designation" {...field} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={representativeForm.control}
                name="contact_number"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Contact Number</FormLabel>
                    <FormControl>
                      <Input placeholder="Contact Number" {...field} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={representativeForm.control}
                name="email"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Email</FormLabel>
                    <FormControl>
                      <Input type="email" placeholder="Email" {...field} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={representativeForm.control}
                name="is_primary"
                render={({ field }) => (
                  <FormItem className="flex flex-row items-start space-x-3 space-y-0 rounded-md border p-4">
                    <FormControl>
                      <input
                        type="checkbox"
                        checked={field.value}
                        onChange={field.onChange}
                      />
                    </FormControl>
                    <div className="space-y-1 leading-none">
                      <FormLabel>Set as primary contact</FormLabel>
                    </div>
                  </FormItem>
                )}
              />
              <DialogFooter>
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => setIsNewRepresentativeDialogOpen(false)}
                >
                  Cancel
                </Button>
                <Button type="submit" disabled={isSubmitting}>
                  {isSubmitting && (
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  )}
                  Submit
                </Button>
              </DialogFooter>
            </form>
          </Form>
        </DialogContent>
      </Dialog>
    </div>
  );
};

export default PartiesInvolvedPage;
