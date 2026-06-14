import React from "react";
import { useForm } from "react-hook-form";
import { Button } from "@/components/ui/button";
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { DialogFooter } from "@/components/ui/dialog";
import { Organization, Project } from "./types";

interface User {
  id: string;
  name: string;
  email: string;
  avatar?: string;
}

interface LetterInitiationFormProps {
  onSubmit: (data: {
    title: string;
    recipient: string;
    subject: string;
    createdBy: User;
    assignedTo: User;
    organizationId?: string;
    projectId?: string;
    instructions?: string;
  }) => void;
  onCancel: () => void;
  organizations: Organization[];
  projects: Project[];
  users?: User[];
  isLoading?: boolean;
}

const LetterInitiationForm: React.FC<LetterInitiationFormProps> = ({
  onSubmit,
  onCancel,
  organizations,
  projects,
  users: propUsers,
  isLoading = false,
}) => {
  // Use users from props if available, otherwise fallback to mock data
  const users = propUsers || [
    { id: "1", name: "John Doe", email: "john@example.com" },
    { id: "2", name: "Jane Smith", email: "jane@example.com" },
    { id: "3", name: "Alice Johnson", email: "alice@example.com" },
  ];

  const currentUser = users[0]; // Mock current user

  type FormValues = {
    title: string;
    recipient: string;
    subject: string;
    assignedUserId: string;
    instructions: string;
    organizationId: string;
    projectId: string;
  };

  const form = useForm<FormValues>({
    defaultValues: {
      title: "",
      recipient: "",
      subject: "",
      assignedUserId: "",
      instructions: "",
      organizationId: "",
      projectId: "",
    },
  });

  const selectedOrganizationId = form.watch("organizationId");
  const filteredProjects = projects.filter((project) =>
    selectedOrganizationId
      ? project.organizationId === selectedOrganizationId
      : true
  );

  const handleSubmit = (values: FormValues) => {
    const assignedUser =
      users.find((user) => user.id === values.assignedUserId) || currentUser;

    // Persist org/project context to localStorage so downstream API calls include headers
    try {
      if (values.organizationId) {
        const org = organizations.find((o) => o.id === values.organizationId);
        window.localStorage.setItem("org_id", values.organizationId);
        if (org?.name) {
          window.localStorage.setItem("org_name", org.name);
        }
      } else {
        // Clear if not provided
        window.localStorage.removeItem("org_id");
        window.localStorage.removeItem("org_name");
      }
      if (values.projectId) {
        const proj = projects.find((p) => p.id === values.projectId);
        window.localStorage.setItem("proj_id", values.projectId);
        if (proj?.name) {
          window.localStorage.setItem("proj_name", proj.name);
        }
      } else {
        // Clear if not provided
        window.localStorage.removeItem("proj_id");
        window.localStorage.removeItem("proj_name");
      }
    } catch (e) {
      // no-op: localStorage might be unavailable in some environments
    }

    onSubmit({
      title: values.title,
      recipient: values.recipient,
      subject: values.subject,
      createdBy: currentUser,
      assignedTo: assignedUser,
      organizationId: values.organizationId || undefined,
      projectId: values.projectId || undefined,
      instructions: values.instructions || undefined,
    });
  };

  return (
    <Form {...form}>
      <form onSubmit={form.handleSubmit(handleSubmit)} className="space-y-4">
        <div className="grid grid-cols-2 gap-4">
          <FormField
            control={form.control}
            name="title"
            render={({ field }) => (
              <FormItem>
                <FormLabel>Letter Title</FormLabel>
                <FormControl>
                  <Input placeholder="Enter letter title" {...field} required />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />

          <FormField
            control={form.control}
            name="recipient"
            render={({ field }) => (
              <FormItem>
                <FormLabel>Recipient</FormLabel>
                <FormControl>
                  <Input
                    placeholder="Enter recipient name/organization"
                    {...field}
                    required
                  />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
        </div>

        <div className="grid grid-cols-2 gap-4">
          <FormField
            control={form.control}
            name="organizationId"
            render={({ field }) => (
              <FormItem>
                <FormLabel>Organization</FormLabel>
                <Select
                  onValueChange={(val) => {
                    field.onChange(val);
                    form.setValue("projectId", "");
                  }}
                  value={field.value}
                >
                  <FormControl>
                    <SelectTrigger>
                      <SelectValue placeholder="Select organization" />
                    </SelectTrigger>
                  </FormControl>
                  <SelectContent>
                    {organizations.map((org) => (
                      <SelectItem key={org.id} value={org.id}>
                        {org.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <FormMessage />
              </FormItem>
            )}
          />

          <FormField
            control={form.control}
            name="projectId"
            render={({ field }) => (
              <FormItem>
                <FormLabel>Project</FormLabel>
                <Select onValueChange={field.onChange} value={field.value}>
                  <FormControl>
                    <SelectTrigger>
                      <SelectValue placeholder="Select project" />
                    </SelectTrigger>
                  </FormControl>
                  <SelectContent>
                    {filteredProjects.map((project) => (
                      <SelectItem key={project.id} value={project.id}>
                        {project.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <FormMessage />
              </FormItem>
            )}
          />
        </div>

        <FormField
          control={form.control}
          name="subject"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Subject</FormLabel>
              <FormControl>
                <Input placeholder="Enter letter subject" {...field} required />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />

        <FormField
          control={form.control}
          name="assignedUserId"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Assigned Drafter</FormLabel>
              <Select onValueChange={field.onChange} value={field.value}>
                <FormControl>
                  <SelectTrigger>
                    <SelectValue placeholder="Select a user" />
                  </SelectTrigger>
                </FormControl>
                <SelectContent>
                  {users.map((user) => (
                    <SelectItem key={user.id} value={user.id}>
                      {user.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <FormMessage />
            </FormItem>
          )}
        />

        <FormField
          control={form.control}
          name="instructions"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Initial Instructions</FormLabel>
              <FormControl>
                <Textarea
                  placeholder="Enter instructions for the drafter"
                  {...field}
                  rows={3}
                />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />

        <DialogFooter>
          <Button type="button" variant="outline" onClick={onCancel}>
            Cancel
          </Button>
          <Button type="submit">Initialize Letter</Button>
        </DialogFooter>
      </form>
    </Form>
  );
};

export default LetterInitiationForm;
