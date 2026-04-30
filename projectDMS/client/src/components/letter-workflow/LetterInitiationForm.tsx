import React, { useEffect } from "react";
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
import type { CreateLetterInput } from "@/hooks/useLetterWorkflow";

interface User {
  id: string;
  name: string;
  email: string;
  avatar?: string;
}

type LetterInitiationPayload = CreateLetterInput & {
  instructions?: string;
  createdBy?: User;
  assignedTo?: User;
  assignedUserId?: string;
};

interface LetterInitiationFormProps {
  onSubmit: (data: LetterInitiationPayload) => void;
  onCancel: () => void;
  organizations: Organization[];
  projects: Project[];
  users?: User[];
  prefillData?: {
    document_id?: string;
    letter_no?: string;
    subject?: string;
    recipient?: string;
    organization_id?: string;
    organization_name?: string;
    project_id?: string;
    project_name?: string;
    draft_reserved?: boolean;
  };
}

const LetterInitiationForm: React.FC<LetterInitiationFormProps> = ({
  onSubmit,
  onCancel,
  organizations,
  projects,
  users,
  prefillData,
}) => {
  const fallbackUsers: User[] = [
    { id: "demo", name: "Demo User", email: "demo@example.com" },
    {
      id: "drafter",
      name: "Drafting Specialist",
      email: "drafter@example.com",
    },
  ];

  const normalizedUsers: User[] = (
    Array.isArray(users) && users.length > 0 ? users : fallbackUsers
  ).map((user) => ({
    ...user,
    id: String(user.id ?? user.email ?? user.name ?? ""),
  }));

  const normalizeString = (value?: string | null) =>
    typeof value === "string" ? value.toLowerCase().trim() : undefined;

  const storedUserId =
    typeof window !== "undefined"
      ? window.localStorage.getItem("user_id") ?? undefined
      : undefined;

  const currentUser =
    normalizedUsers.find((user) => user.id === storedUserId) ??
    normalizedUsers[0] ??
    undefined;

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
      title: prefillData?.letter_no ? `Re: ${prefillData.letter_no}` : "",
      recipient: prefillData?.recipient || "",
      subject: prefillData?.subject || "",
      assignedUserId: currentUser?.id ?? "",
      instructions: "",
      organizationId: prefillData?.organization_id || "",
      projectId: prefillData?.project_id || "",
    },
  });

  // Update form values when prefillData changes
  useEffect(() => {
    if (!prefillData) return;

    if (prefillData.letter_no) {
      form.setValue("title", `Re: ${prefillData.letter_no}`);
    }
    if (prefillData.recipient) {
      form.setValue("recipient", prefillData.recipient);
    }
    if (prefillData.subject) {
      form.setValue("subject", prefillData.subject);
    }

    const targetOrgName = normalizeString(prefillData.organization_name);
    const resolvedOrgId =
      (prefillData.organization_id &&
        String(prefillData.organization_id)) ||
      (targetOrgName
        ? organizations.find(
            (org) => normalizeString(org.name) === targetOrgName
          )?.id
        : undefined);

    if (resolvedOrgId) {
      form.setValue("organizationId", resolvedOrgId);
    }

    const targetProjectName = normalizeString(prefillData.project_name);
    const resolvedProjectId =
      (prefillData.project_id && String(prefillData.project_id)) ||
      (targetProjectName
        ? projects.find((project) => {
            const sameOrg = resolvedOrgId
              ? project.organizationId === resolvedOrgId
              : true;
            return (
              sameOrg && normalizeString(project.name) === targetProjectName
            );
          })?.id
        : undefined);

    if (resolvedProjectId) {
      form.setValue("projectId", resolvedProjectId);
    }
  }, [prefillData, form, organizations, projects]);

  const selectedOrganizationId = form.watch("organizationId");
  const filteredProjects = projects.filter((project) =>
    selectedOrganizationId
      ? project.organizationId === selectedOrganizationId
      : true
  );

  useEffect(() => {
    if (organizations.length === 0) return;
    const currentOrgId = form.getValues("organizationId");
    const exists = organizations.some((org) => org.id === currentOrgId);
    if (!currentOrgId || !exists) {
      const preferredId =
        (prefillData?.organization_id &&
          String(prefillData.organization_id)) ||
        (prefillData?.organization_name
          ? organizations.find(
              (org) =>
                normalizeString(org.name) ===
                normalizeString(prefillData?.organization_name)
            )?.id
          : undefined);
      form.setValue("organizationId", preferredId || organizations[0].id);
    }
  }, [organizations, form, prefillData]);

  useEffect(() => {
    const currentProjectId = form.getValues("projectId");
    const projectStillAvailable = filteredProjects.some(
      (project) => project.id === currentProjectId
    );
    if (!projectStillAvailable) {
      const preferredProject =
        (prefillData?.project_id && String(prefillData.project_id)) ||
        (prefillData?.project_name
          ? filteredProjects.find(
              (project) =>
                normalizeString(project.name) ===
                normalizeString(prefillData?.project_name)
            )?.id
          : undefined);
      const fallbackProjectId =
        preferredProject ?? filteredProjects[0]?.id ?? "";
      form.setValue("projectId", fallbackProjectId);
    }
  }, [filteredProjects, form, prefillData]);

  useEffect(() => {
    if (!currentUser) return;
    const currentValue = form.getValues("assignedUserId");
    const existsInOptions = normalizedUsers.some(
      (user) => user.id === currentValue
    );
    if (!currentValue || !existsInOptions) {
      form.setValue("assignedUserId", currentUser.id);
    }
  }, [currentUser, normalizedUsers, form]);

  const handleSubmit = (values: FormValues) => {
    const assignedUser =
      normalizedUsers.find((user) => user.id === values.assignedUserId) ??
      currentUser;
    const assignedId = assignedUser?.id ?? normalizedUsers[0]?.id ?? "";

    onSubmit({
      title: values.title,
      recipient: values.recipient,
      subject: values.subject,
      instructions: values.instructions?.trim()
        ? values.instructions.trim()
        : undefined,
      createdBy: currentUser,
      assignedTo: assignedUser,
      assignedUserId: assignedId || undefined,
      assigned_to: assignedId,
      organization_id: values.organizationId || undefined,
      project_id: values.projectId || undefined,
    });
  };

  return (
    <div className="px-6 py-4">
      <Form {...form}>
        <form
          onSubmit={form.handleSubmit(handleSubmit)}
          className="space-y-4 w-full"
        >
          <div className="grid grid-cols-2 gap-4">
            <FormField
              control={form.control}
              name="title"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Letter Title</FormLabel>
                  <FormControl>
                    <Input
                      placeholder="Enter letter title"
                      {...field}
                      required
                    />
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
                  <Select onValueChange={field.onChange} value={field.value}>
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
                  <Input
                    placeholder="Enter letter subject"
                    {...field}
                    required
                  />
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
                <Select
                  onValueChange={field.onChange}
                  value={field.value}
                  disabled={normalizedUsers.length === 0}
                >
                  <FormControl>
                    <SelectTrigger>
                      <SelectValue
                        placeholder={
                          normalizedUsers.length === 0
                            ? "No users available"
                            : "Select a user"
                        }
                      />
                    </SelectTrigger>
                  </FormControl>
                  <SelectContent>
                    {normalizedUsers.map((user) => (
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
    </div>
  );
};

export default LetterInitiationForm;
