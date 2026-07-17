import React, { useState, useEffect } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Form,
  FormControl,
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form";
import { Checkbox } from "@/components/ui/checkbox";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import * as z from "zod";
import { Building, Users, ChevronRight, Sparkles, CheckCircle2, Clock, CreditCard } from "lucide-react";
import { useToast } from "@/hooks/use-toast";
import { enhancedApi } from "@/services/enhanced-api";
import { useStepUp } from "@/hooks/useStepUp";
import useHasPermission from "@/hooks/useHasPermission";
import { ENTITY_PERMISSIONS } from "@/constants/entityPermissions";
import {
  getPlanCatalog,
  getPlanSettings,
  updatePlanSettingsScope,
  type PlanCatalogResponse,
} from "@/services/plan-settings-api";
import {
  DEFAULT_BILLING_PERIOD,
  NO_SERVICE_PLAN_CODE,
  buildOrganizationPlanScopePayload,
  buildOrganizationProfilePayload,
  organizationSubscriptionChanged,
  organizationSubscriptionSnapshotFromEffective,
  organizationSubscriptionSnapshotFromForm,
  type OrganizationSubscriptionSnapshot,
} from "@/lib/organization-subscription";

// Formatting helper for plan pricing
const formatPriceMajor = (minor: number, currency = "INR") => {
  const major = minor / 100;
  return new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency,
    minimumFractionDigits: 0,
  }).format(major);
};

// Define validation schema for organization registration
const organizationSchema = z.object({
  name: z.string().min(2, "Organization name must be at least 2 characters"),
  panNumber: z.string().min(10, "PAN Number must be at least 10 characters"),
  gstNumber: z.string().optional(),
  address: z.string().min(5, "Address must be at least 5 characters"),
  city: z.string().min(2, "City must be at least 2 characters"),
  state: z.string().min(2, "State must be at least 2 characters"),
  pinCode: z.string().min(4, "PIN code must be at least 4 characters"),
  adminName: z.string().min(2, "Admin name must be at least 2 characters"),
  adminEmail: z.string().email("Please enter a valid email address"),
  adminContact: z
    .string()
    .min(6, "Contact number must be at least 6 characters"),
  billingEnabled: z.boolean().default(false),
  plan_code: z.string().optional(),
  billing_period: z.string().default("monthly"),
  trial_enabled: z.boolean().default(true),
});

type OrganizationFormValues = z.infer<typeof organizationSchema>;

const projectSchema = z.object({
  name: z.string().min(2, "Project name must be at least 2 characters"),
  projectCode: z.string().min(2, "Project code must be at least 2 characters"),
  panNumber: z.string().optional(),
  gstNumber: z.string().optional(),
  address: z.string().min(5, "Address must be at least 5 characters"),
  city: z.string().min(2, "City must be at least 2 characters"),
  state: z.string().min(2, "State must be at least 2 characters"),
  pinCode: z.string().min(4, "PIN code must be at least 4 characters"),
  adminName: z.string().min(2, "Admin name must be at least 2 characters"),
  adminEmail: z.string().email("Please enter a valid email address"),
  adminContact: z
    .string()
    .min(6, "Contact number must be at least 6 characters"),
  billingEnabled: z.boolean().default(false),
  plan_code: z.string().optional(),
  billing_period: z.string().default("monthly"),
  trial_enabled: z.boolean().default(true),
  subscription_mode: z.enum(["inherit", "override"]).default("inherit"),
  organization_id: z.string().min(1, "Please select an organization"),
});

const RegisterPage = () => {
  const [activeTab, setActiveTab] = useState("organization");
  const { toast } = useToast();
  const { requestToken, StepUpDialog } = useStepUp();
  const [loading, setLoading] = useState(false);
  const [organizations, setOrganizations] = useState([]);
  const [mode, setMode] = useState<"create" | "edit">("create");
  const [editingOrgId, setEditingOrgId] = useState<string | null>(null);
  const [editingProjectId, setEditingProjectId] = useState<string | null>(null);
  const [initialOrgSubscription, setInitialOrgSubscription] =
    useState<OrganizationSubscriptionSnapshot | null>(null);
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const canCreateOrg = useHasPermission(
    ENTITY_PERMISSIONS.organizations.create
  );
  const canUpdateOrg = useHasPermission(
    ENTITY_PERMISSIONS.organizations.update
  );
  const canCreateProject = useHasPermission(ENTITY_PERMISSIONS.projects.create);
  const canUpdateProject = useHasPermission(ENTITY_PERMISSIONS.projects.update);
  const DEFAULT_TEMP_PASSWORD = "TempPass123!";

  const sanitizeUsernameValue = (value: string) =>
    value
      .trim()
      .toLowerCase()
      .replace(/\s+/g, "")
      .replace(/[^a-z0-9_.-]/g, "");

  const splitName = (fullName: string) => {
    const parts = (fullName || "").trim().split(/\s+/).filter(Boolean);
    const first = parts.shift() || "Admin";
    const last = parts.join(" ") || first;
    return { first, last };
  };

  const deriveUsername = (email: string, name: string) => {
    const local = (email || "").split("@")[0] || "";
    const nameCandidate = sanitizeUsernameValue(name.replace(/\s+/g, "."));
    const emailCandidate = sanitizeUsernameValue(local);
    return emailCandidate || nameCandidate || "admin";
  };

  const createAdminUser = async ({
    role,
    email,
    name,
    organizationId,
    projectId,
  }: {
    role: "orgadmin" | "projectadmin";
    email: string;
    name: string;
    organizationId?: string;
    projectId?: string;
  }) => {
    const { first, last } = splitName(name);
    const username = deriveUsername(email, name);
    try {
      await enhancedApi.createUser({
        username,
        email,
        first_name: first,
        last_name: last,
        password: DEFAULT_TEMP_PASSWORD,
        roles: [role],
        organization_id: organizationId,
        organizations: organizationId ? [organizationId] : [],
        projects: projectId ? [projectId] : [],
        permissions: [],
        preferences: {
          emailNotifications: true,
          sharingAlerts: true,
          theme: "light",
          language: "en",
        },
      });
      toast({
        title: "Admin user created",
        description: `${role === "orgadmin" ? "Organization" : "Project"} admin account created for ${email}`,
      });
    } catch (error: any) {
      console.error("Admin user creation failed:", error);
      toast({
        title: "Admin user creation failed",
        description:
          error?.message ||
          "Organization/Project saved, but admin user could not be created.",
        variant: "destructive",
      });
    }
  };

  const fetchOrganizations = async () => {
    try {
      const list = await enhancedApi.getOrganizations();
      const normalizedMap = new Map<string, any>();
      (list || []).forEach((o: any) => {
        const _id = o?._id ? String(o._id) : o?.id ? String(o.id) : "";
        if (!_id) return;
        normalizedMap.set(_id, {
          ...o,
          _id,
          name: o?.name ?? o?.shortName ?? "Unnamed",
        });
      });
      setOrganizations(Array.from(normalizedMap.values()));
    } catch (error: any) {
      console.error("Error fetching organizations:", error);
      toast({
        title: "Error",
        description: error?.message || "Failed to fetch organizations.",
        variant: "destructive",
      });
    }
  };

  const [planCatalog, setPlanCatalog] = useState<PlanCatalogResponse | null>(
    null
  );

  useEffect(() => {
    fetchOrganizations();
    getPlanCatalog()
      .then(setPlanCatalog)
      .catch((err) => console.warn("Could not load plan catalog:", err));
    // Initial bootstrap intentionally runs once.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Organization form
  const organizationForm = useForm({
    resolver: zodResolver(organizationSchema),
    defaultValues: {
      name: "",
      panNumber: "",
      gstNumber: "",
      address: "",
      city: "",
      state: "",
      pinCode: "",
      adminName: "",
      adminEmail: "",
      adminContact: "",
      billingEnabled: false,
      plan_code: "",
      billing_period: "monthly",
      trial_enabled: true,
    },
  });

  // Project form
  const projectForm = useForm({
    resolver: zodResolver(projectSchema),
    defaultValues: {
      name: "",
      projectCode: "",
      panNumber: "",
      gstNumber: "",
      address: "",
      city: "",
      state: "",
      pinCode: "",
      adminName: "",
      adminEmail: "",
      adminContact: "",
      billingEnabled: false,
      plan_code: "",
      billing_period: "monthly",
      trial_enabled: true,
      subscription_mode: "inherit" as const,
      organization_id: "",
    },
  });

  const loadOrganizationForEdit = async (orgId: string) => {
    setLoading(true);
    try {
      const org = await enhancedApi.getOrganization(orgId);
      let subscriptionValues: Partial<OrganizationFormValues> = {
        plan_code: "",
        billing_period: DEFAULT_BILLING_PERIOD,
        trial_enabled: true,
      };
      let subscriptionSnapshot: OrganizationSubscriptionSnapshot | null = null;

      try {
        const planSettings = await getPlanSettings();
        const effective = planSettings.effective.organizations[String(orgId)];
        subscriptionSnapshot =
          organizationSubscriptionSnapshotFromEffective(effective);
        subscriptionValues = {
          plan_code: subscriptionSnapshot.plan_code,
          billing_period: subscriptionSnapshot.billing_period,
          trial_enabled: Boolean(effective?.trial ?? true),
          billingEnabled: subscriptionSnapshot.plan_code !== NO_SERVICE_PLAN_CODE,
        };
      } catch (planError) {
        console.warn("Could not load organization subscription:", planError);
      }

      organizationForm.reset({
        name: org.name || "",
        panNumber: org.panNumber || "",
        gstNumber: org.gstNumber || "",
        address: org.address || "",
        city: org.city || "",
        state: org.state || "",
        pinCode: org.pinCode || "",
        adminName: org.adminName || "",
        adminEmail: org.adminEmail || "",
        adminContact: org.adminContact || "",
        billingEnabled: Boolean(org.billingEnabled),
        ...subscriptionValues,
      });
      setInitialOrgSubscription(subscriptionSnapshot);
    } catch (error: any) {
      toast({
        title: "Error",
        description: error?.message || "Failed to load organization.",
        variant: "destructive",
      });
    } finally {
      setLoading(false);
    }
  };

  const loadProjectForEdit = async (projectId: string) => {
    setLoading(true);
    try {
      const project = await enhancedApi.getProjectById(projectId);
      projectForm.reset({
        name: project.name || "",
        projectCode: project.projectCode || "",
        panNumber: project.panNumber || "",
        gstNumber: project.gstNumber || "",
        address: project.address || "",
        city: project.city || "",
        state: project.state || "",
        pinCode: project.pinCode || "",
        adminName: project.adminName || "",
        adminEmail: project.adminEmail || "",
        adminContact: project.adminContact || "",
        billingEnabled: Boolean(project.billingEnabled),
        organization_id: project.organization_id || "",
      });
    } catch (error: any) {
      toast({
        title: "Error",
        description: error?.message || "Failed to load project.",
        variant: "destructive",
      });
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    const tabParam = searchParams.get("tab");
    const modeParam = searchParams.get("mode");
    const orgId = searchParams.get("orgId");
    const projectId = searchParams.get("projectId");
    const nextMode: "create" | "edit" =
      modeParam === "edit" && (orgId || projectId) ? "edit" : "create";

    setMode(nextMode);
    setEditingOrgId(nextMode === "edit" && orgId ? orgId : null);
    setEditingProjectId(
      nextMode === "edit" && !orgId && projectId ? projectId : null
    );

    if (tabParam === "project" || tabParam === "organization") {
      setActiveTab(tabParam);
    }

    if (nextMode === "edit" && orgId) {
      setActiveTab("organization");
      loadOrganizationForEdit(orgId);
    } else if (nextMode === "edit" && projectId) {
      setActiveTab("project");
      setInitialOrgSubscription(null);
      loadProjectForEdit(projectId);
    } else {
      setInitialOrgSubscription(null);
    }
    // URL-driven form bootstrap; loaders depend on form instances and are called intentionally here.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams]);

  // Handle organization form submission
  const onOrganizationSubmit = async (data) => {
    const isEditing = mode === "edit" && editingOrgId;
    const allowed = isEditing ? canUpdateOrg : canCreateOrg;
    if (!allowed) {
      toast({
        title: "Permission denied",
        description: "You do not have permission to perform this action.",
        variant: "destructive",
      });
      return;
    }
    setLoading(true);
    try {
      const subscriptionNeedsUpdate =
        isEditing && editingOrgId
          ? organizationSubscriptionChanged(initialOrgSubscription, data)
          : false;
      const stepUpToken = subscriptionNeedsUpdate
        ? await requestToken(
            "subscription.entitlement.manage",
            "Confirm subscription update",
            "Enter your password to change this organization's subscription."
          )
        : undefined;

      if (isEditing && editingOrgId) {
        await enhancedApi.updateOrganization(
          editingOrgId,
          buildOrganizationProfilePayload(data)
        );
        if (subscriptionNeedsUpdate) {
          await updatePlanSettingsScope(
            buildOrganizationPlanScopePayload(editingOrgId, data),
            { stepUpToken }
          );
          setInitialOrgSubscription(
            organizationSubscriptionSnapshotFromForm(data)
          );
        }
        toast({
          title: "Organization updated successfully",
          description: data.name,
        });
      } else {
        const newOrganization = await enhancedApi.createOrganization(data);
        const normalizedOrgId = newOrganization?._id
          ? String(newOrganization._id)
          : newOrganization?.id
          ? String(newOrganization.id)
          : "";
        const normalizedOrg = {
          ...newOrganization,
          _id: normalizedOrgId,
        };
        setOrganizations((prevOrganizations) => {
          const next = new Map<string, any>();
          prevOrganizations.forEach((o: any) =>
            next.set(String(o?._id ?? o?.id ?? ""), o)
          );
          if (normalizedOrgId) {
            next.set(normalizedOrgId, normalizedOrg);
          }
          return Array.from(next.values());
        });
        if (normalizedOrg._id) {
          projectForm.setValue("organization_id", normalizedOrg._id);
        }
        toast({
          title: "Organization Registered",
          description: `Successfully registered ${data.name}`,
        });
        if (normalizedOrg._id) {
          await createAdminUser({
            role: "orgadmin",
            email: data.adminEmail,
            name: data.adminName,
            organizationId: normalizedOrg._id,
          });
        }
        setActiveTab("project");
      }
    } catch (error: any) {
      console.error("Error saving organization:", error);
      toast({
        title: "Error",
        description: error?.message || "Failed to save organization.",
        variant: "destructive",
      });
    } finally {
      setLoading(false);
    }
  };

  // Handle project form submission
  const onProjectSubmit = async (data) => {
    const isEditing = mode === "edit" && editingProjectId;
    const allowed = isEditing ? canUpdateProject : canCreateProject;
    if (!allowed) {
      toast({
        title: "Permission denied",
        description: "You do not have permission to perform this action.",
        variant: "destructive",
      });
      return;
    }
    setLoading(true);
    try {
      const orgId = String(data.organization_id ?? "").trim();
      if (!orgId) {
        toast({
          title: "Organization required",
          description:
            "Please select an organization before creating a project.",
          variant: "destructive",
        });
        setLoading(false);
        return;
      }
      const selectedOrg = organizations.find(
        (org: any) => String(org?._id ?? org?.id ?? "") === orgId
      );
      const resolvedOrgId = selectedOrg
        ? String(selectedOrg._id ?? selectedOrg.id)
        : orgId;
      // Include both naming conventions for backend compatibility
      const payload = {
        ...data,
        organization_id: resolvedOrgId,
        //organizationId: data.organization_id,
      };
      if (isEditing && editingProjectId) {
        await enhancedApi.updateProject(editingProjectId, payload);
        toast({
          title: "Project updated successfully",
          description: data.name,
        });
      } else {
        const newProject = await enhancedApi.createProject(payload);
        const normalizedProjectId = newProject?._id
          ? String(newProject._id)
          : newProject?.id
          ? String(newProject.id)
          : "";
        toast({
          title: "Project Registered",
          description: `Successfully registered project: ${data.name}`,
        });
        if (normalizedProjectId) {
          await createAdminUser({
            role: "projectadmin",
            email: data.adminEmail,
            name: data.adminName,
            organizationId: resolvedOrgId,
            projectId: normalizedProjectId,
          });
        }
      }
    } catch (error: any) {
      console.error("Error saving project:", error);
      toast({
        title: "Error",
        description: error?.message || "Failed to save project.",
        variant: "destructive",
      });
    } finally {
      setLoading(false);
    }
  };
  // Sample data
  const states = [
    "Andhra Pradesh",
    "Andaman and Nicobar Islands",
    "Arunachal Pradesh",
    "Assam",
    "Bihar",
    "Chandigarh",
    "Chhattisgarh",
    "Dadra and Nagar Haveli and Daman and Diu",
    "Delhi",
    "Goa",
    "Gujarat",
    "Haryana",
    "Himachal Pradesh",
    "Jammu and Kashmir",
    "Jharkhand",
    "Karnataka",
    "Kerala",
    "Lakshadweep",
    "Madhya Pradesh",
    "Maharashtra",
    "Manipur",
    "Meghalaya",
    "Mizoram",
    "Nagaland",
    "Odisha",
    "Puducherry",
    "Punjab",
    "Rajasthan",
    "Sikkim",
    "Tamil Nadu",
    "Telangana",
    "Tripura",
    "Uttar Pradesh",
    "Uttarakhand",
    "West Bengal",
  ];

  const isEditingOrg = mode === "edit" && Boolean(editingOrgId);
  const isEditingProject = mode === "edit" && Boolean(editingProjectId);
  const organizationActionDisabled =
    loading || (isEditingOrg ? !canUpdateOrg : !canCreateOrg);
  const projectActionDisabled =
    loading || (isEditingProject ? !canUpdateProject : !canCreateProject);

  const handleCancelEdit = () => {
    if (activeTab === "project") {
      navigate("/projects");
    } else {
      navigate("/organizations");
    }
  };

  return (
    <div className="max-w-6xl mx-auto p-6 space-y-6">
      {StepUpDialog}
      <h1 className="text-2xl font-bold">Registration</h1>
      <Tabs
        value={activeTab}
        onValueChange={setActiveTab}
        className="max-w-6xl mx-auto"
      >
        <TabsList className="grid w-full grid-cols-2">
          <TabsTrigger value="organization" className="flex items-center gap-2">
            <Building className="h-4 w-4" />
            <span>Organization Registration</span>
          </TabsTrigger>
          <TabsTrigger value="project" className="flex items-center gap-2">
            <Users className="h-4 w-4" />
            <span>Project Registration</span>
          </TabsTrigger>
        </TabsList>

        {/* Organization Registration Tab - Restructured with 2 columns */}
        <TabsContent value="organization" className="mt-6">
          <Card>
            <CardHeader>
              <CardTitle>
                {isEditingOrg
                  ? "Update Organization"
                  : "Register a New Organization"}
              </CardTitle>
              <CardDescription>
                {isEditingOrg
                  ? "Edit organization details and save changes."
                  : "Add a new organization to manage its documents and projects"}
              </CardDescription>
            </CardHeader>
            <Form {...organizationForm}>
              <form
                onSubmit={organizationForm.handleSubmit(onOrganizationSubmit)}
              >
                <CardContent className="space-y-4">
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                    {/* Column 1 */}
                    <div className="space-y-4">
                      <FormField
                        control={organizationForm.control}
                        name="name"
                        render={({ field }) => (
                          <FormItem>
                            <FormLabel>Organization Name</FormLabel>
                            <FormControl>
                              <Input
                                placeholder="Enter organization name"
                                {...field}
                              />
                            </FormControl>
                            <FormMessage />
                          </FormItem>
                        )}
                      />

                      <FormField
                        control={organizationForm.control}
                        name="panNumber"
                        render={({ field }) => (
                          <FormItem>
                            <FormLabel>PAN Number (Unique)</FormLabel>
                            <FormControl>
                              <Input
                                placeholder="Enter PAN number"
                                {...field}
                              />
                            </FormControl>
                            <FormMessage />
                          </FormItem>
                        )}
                      />

                      <FormField
                        control={organizationForm.control}
                        name="gstNumber"
                        render={({ field }) => (
                          <FormItem>
                            <FormLabel>GST Number</FormLabel>
                            <FormControl>
                              <Input
                                placeholder="Enter GST number"
                                {...field}
                              />
                            </FormControl>
                            <FormMessage />
                          </FormItem>
                        )}
                      />

                      <FormField
                        control={organizationForm.control}
                        name="address"
                        render={({ field }) => (
                          <FormItem>
                            <FormLabel>Address</FormLabel>
                            <FormControl>
                              <Input
                                placeholder="Enter street address"
                                {...field}
                              />
                            </FormControl>
                            <FormMessage />
                          </FormItem>
                        )}
                      />

                      <FormField
                        control={organizationForm.control}
                        name="city"
                        render={({ field }) => (
                          <FormItem>
                            <FormLabel>City</FormLabel>
                            <FormControl>
                              <Input placeholder="Enter city" {...field} />
                            </FormControl>
                            <FormMessage />
                          </FormItem>
                        )}
                      />
                    </div>

                    {/* Column 2 */}
                    <div className="space-y-4">
                      <FormField
                        control={organizationForm.control}
                        name="state"
                        render={({ field }) => (
                          <FormItem>
                            <FormLabel>State</FormLabel>
                            <Select
                              onValueChange={field.onChange}
                              defaultValue={field.value}
                            >
                              <FormControl>
                                <SelectTrigger>
                                  <SelectValue placeholder="Select state" />
                                </SelectTrigger>
                              </FormControl>
                              <SelectContent>
                                {states.map((state) => (
                                  <SelectItem key={state} value={state}>
                                    {state}
                                  </SelectItem>
                                ))}
                              </SelectContent>
                            </Select>
                            <FormMessage />
                          </FormItem>
                        )}
                      />

                      <FormField
                        control={organizationForm.control}
                        name="pinCode"
                        render={({ field }) => (
                          <FormItem>
                            <FormLabel>PIN Code</FormLabel>
                            <FormControl>
                              <Input placeholder="Enter PIN code" {...field} />
                            </FormControl>
                            <FormMessage />
                          </FormItem>
                        )}
                      />

                      <FormField
                        control={organizationForm.control}
                        name="adminName"
                        render={({ field }) => (
                          <FormItem>
                            <FormLabel>Admin Name</FormLabel>
                            <FormControl>
                              <Input
                                placeholder="Enter admin name"
                                {...field}
                              />
                            </FormControl>
                            <FormMessage />
                          </FormItem>
                        )}
                      />

                      <FormField
                        control={organizationForm.control}
                        name="adminEmail"
                        render={({ field }) => (
                          <FormItem>
                            <FormLabel>Email ID</FormLabel>
                            <FormControl>
                              <Input
                                type="email"
                                placeholder="admin@example.com"
                                {...field}
                              />
                            </FormControl>
                            <FormMessage />
                          </FormItem>
                        )}
                      />

                      <FormField
                        control={organizationForm.control}
                        name="adminContact"
                        render={({ field }) => (
                          <FormItem>
                            <FormLabel>Contact Number</FormLabel>
                            <FormControl>
                              <Input
                                placeholder="Enter contact number"
                                {...field}
                              />
                            </FormControl>
                            <FormMessage />
                          </FormItem>
                        )}
                      />
                    </div>
                  </div>

                  {/* ──── Subscription Configuration ──── */}
                  <Separator className="my-2" />
                  <div className="space-y-4">
                    <div className="flex items-center gap-2">
                      <CreditCard className="h-4 w-4 text-primary" />
                      <h3 className="text-sm font-semibold">Subscription Configuration</h3>
                    </div>

                    {/* Plan Selection */}
                    <FormField
                      control={organizationForm.control}
                      name="plan_code"
                      render={({ field }) => (
                        <FormItem>
                          <FormLabel>Select Plan</FormLabel>
                          <Select onValueChange={(v) => {
                            field.onChange(v);
                            organizationForm.setValue("billingEnabled", v !== "" && v !== "no_service_override");
                          }} value={field.value || ""}>
                            <FormControl>
                              <SelectTrigger>
                                <SelectValue placeholder="Choose a subscription plan" />
                              </SelectTrigger>
                            </FormControl>
                            <SelectContent>
                              <SelectItem value="no_service_override">No Service (Free)</SelectItem>
                              {(planCatalog?.plans || []).filter(p => p.is_active && p.code !== "no_service_override").map(plan => (
                                <SelectItem key={plan.code} value={plan.code}>
                                  <span className="flex items-center gap-2">
                                    {plan.name}
                                    {plan.highlight && <Badge variant="secondary" className="text-[10px] py-0">Popular</Badge>}
                                    {plan.pricing_tiers?.monthly
                                      ? ` — ${formatPriceMajor(plan.pricing_tiers.monthly)}/mo`
                                      : " — Custom"}
                                  </span>
                                </SelectItem>
                              ))}
                            </SelectContent>
                          </Select>
                          <FormDescription>
                            Select the subscription plan for this organization
                          </FormDescription>
                        </FormItem>
                      )}
                    />

                    {/* Billing Period */}
                    {organizationForm.watch("plan_code") && organizationForm.watch("plan_code") !== "no_service_override" && (
                      <FormField
                        control={organizationForm.control}
                        name="billing_period"
                        render={({ field }) => (
                          <FormItem>
                            <FormLabel>Billing Period</FormLabel>
                            <Select onValueChange={field.onChange} value={field.value || "monthly"}>
                              <FormControl>
                                <SelectTrigger>
                                  <SelectValue />
                                </SelectTrigger>
                              </FormControl>
                              <SelectContent>
                                <SelectItem value="monthly">Monthly</SelectItem>
                                <SelectItem value="quarterly">Quarterly (Save ~7%)</SelectItem>
                                <SelectItem value="annual">Annual (Save ~17%)</SelectItem>
                              </SelectContent>
                            </Select>
                          </FormItem>
                        )}
                      />
                    )}

                    {/* Trial Toggle */}
                    {organizationForm.watch("plan_code") && organizationForm.watch("plan_code") !== "no_service_override" && (
                      <FormField
                        control={organizationForm.control}
                        name="trial_enabled"
                        render={({ field }) => {
                          const selPlan = (planCatalog?.plans || []).find(p => p.code === organizationForm.watch("plan_code"));
                          const trialDays = (selPlan?.trial_config as any)?.days || 14;
                          const trialAvailable = (selPlan?.trial_config as any)?.enabled;
                          if (!trialAvailable) return null;
                          return (
                            <FormItem className="flex flex-row items-start space-x-3 space-y-0 rounded-md border p-4 bg-amber-50/50 dark:bg-amber-950/20">
                              <FormControl>
                                <Checkbox checked={field.value} onCheckedChange={field.onChange} />
                              </FormControl>
                              <div className="space-y-1 leading-none">
                                <FormLabel className="flex items-center gap-1.5">
                                  <Clock className="h-3.5 w-3.5 text-amber-600" />
                                  Start with {trialDays}-day free trial
                                </FormLabel>
                                <FormDescription>
                                  Try all features before your first billing cycle
                                </FormDescription>
                              </div>
                            </FormItem>
                          );
                        }}
                      />
                    )}

                    {/* Plan Features Preview */}
                    {(() => {
                      const selCode = organizationForm.watch("plan_code");
                      const selPlan = (planCatalog?.plans || []).find(p => p.code === selCode);
                      if (!selPlan || selCode === "no_service_override") return null;
                      return (
                        <div className="rounded-lg border bg-muted/30 p-3">
                          <p className="text-xs font-medium text-muted-foreground mb-2">PLAN FEATURES</p>
                          <ul className="space-y-1">
                            {Object.entries(selPlan.features || {}).map(([k, v]) => v && (
                              <li key={k} className="text-xs flex items-center gap-1.5">
                                <CheckCircle2 className="h-3 w-3 text-emerald-500" />
                                {k.replace(/^feature\./, "").replace(/\./g, " ").replace(/\b\w/g, c => c.toUpperCase())}
                              </li>
                            ))}
                            {selPlan.max_users && (
                              <li className="text-xs flex items-center gap-1.5">
                                <CheckCircle2 className="h-3 w-3 text-emerald-500" />
                                Up to {selPlan.max_users} users
                              </li>
                            )}
                            {selPlan.max_storage_gb && (
                              <li className="text-xs flex items-center gap-1.5">
                                <CheckCircle2 className="h-3 w-3 text-emerald-500" />
                                {selPlan.max_storage_gb} GB storage
                              </li>
                            )}
                          </ul>
                        </div>
                      );
                    })()}
                  </div>
                </CardContent>
                <CardFooter className="flex justify-between">
                  {isEditingOrg ? (
                    <Button
                      variant="outline"
                      type="button"
                      onClick={handleCancelEdit}
                    >
                      Cancel
                    </Button>
                  ) : (
                    <div />
                  )}
                  <Button
                    type="submit"
                    className="flex items-center gap-1"
                    disabled={organizationActionDisabled}
                  >
                    {loading
                      ? isEditingOrg
                        ? "Saving..."
                        : "Registering..."
                      : isEditingOrg
                      ? "Save Changes"
                      : "Register Organization"}
                    <ChevronRight className="h-4 w-4" />
                  </Button>
                </CardFooter>
                {organizationActionDisabled &&
                  !loading &&
                  ((isEditingOrg && !canUpdateOrg) ||
                    (!isEditingOrg && !canCreateOrg)) && (
                    <p className="text-sm text-destructive px-6 pb-4">
                      You do not have permission to{" "}
                      {isEditingOrg ? "update" : "create"} organizations.
                    </p>
                  )}
              </form>
            </Form>
          </Card>
        </TabsContent>

        {/* Project Registration Tab - Restructured with 2 columns */}
        <TabsContent value="project" className="mt-6">
          <Card>
            <CardHeader>
              <CardTitle>
                {isEditingProject ? "Update Project" : "Register a New Project"}
              </CardTitle>
              <CardDescription>
                {isEditingProject
                  ? "Edit project details and save changes."
                  : "Create a new project to organize related documents"}
              </CardDescription>
            </CardHeader>
            <Form {...projectForm}>
              <form onSubmit={projectForm.handleSubmit(onProjectSubmit)}>
                <CardContent className="space-y-4">
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                    {/* Column 1 */}
                    <div className="space-y-4">
                      <FormField
                        control={projectForm.control}
                        name="name"
                        render={({ field }) => (
                          <FormItem>
                            <FormLabel>Project Name</FormLabel>
                            <FormControl>
                              <Input
                                placeholder="Enter project name"
                                {...field}
                              />
                            </FormControl>
                            <FormMessage />
                          </FormItem>
                        )}
                      />

                      <FormField
                        control={projectForm.control}
                        name="projectCode"
                        render={({ field }) => (
                          <FormItem>
                            <FormLabel>Project Code (Unique)</FormLabel>
                            <FormControl>
                              <Input
                                placeholder="Enter project code"
                                {...field}
                              />
                            </FormControl>
                            <FormMessage />
                          </FormItem>
                        )}
                      />

                      <FormField
                        control={projectForm.control}
                        name="panNumber"
                        render={({ field }) => (
                          <FormItem>
                            <FormLabel>PAN Number</FormLabel>
                            <FormControl>
                              <Input
                                placeholder="Enter PAN number"
                                {...field}
                              />
                            </FormControl>
                            <FormMessage />
                          </FormItem>
                        )}
                      />

                      <FormField
                        control={projectForm.control}
                        name="gstNumber"
                        render={({ field }) => (
                          <FormItem>
                            <FormLabel>GST Number</FormLabel>
                            <FormControl>
                              <Input
                                placeholder="Enter GST number"
                                {...field}
                              />
                            </FormControl>
                            <FormMessage />
                          </FormItem>
                        )}
                      />

                      <FormField
                        control={projectForm.control}
                        name="address"
                        render={({ field }) => (
                          <FormItem>
                            <FormLabel>Address</FormLabel>
                            <FormControl>
                              <Input
                                placeholder="Enter project address"
                                {...field}
                              />
                            </FormControl>
                            <FormMessage />
                          </FormItem>
                        )}
                      />

                      <FormField
                        control={projectForm.control}
                        name="organization_id"
                        render={({ field }) => (
                          <FormItem>
                            <FormLabel>Organization</FormLabel>
                            <Select
                              onValueChange={field.onChange}
                              value={field.value}
                            >
                              <FormControl>
                                <SelectTrigger>
                                  <SelectValue placeholder="Select organization" />
                                </SelectTrigger>
                              </FormControl>
                              <SelectContent>
                                {organizations.map((org) => (
                                  <SelectItem key={org._id} value={org._id}>
                                    {org.name}
                                  </SelectItem>
                                ))}
                              </SelectContent>
                            </Select>
                            <FormMessage />
                          </FormItem>
                        )}
                      />
                    </div>

                    {/* Column 2 */}
                    <div className="space-y-4">
                      <FormField
                        control={projectForm.control}
                        name="city"
                        render={({ field }) => (
                          <FormItem>
                            <FormLabel>City</FormLabel>
                            <FormControl>
                              <Input placeholder="Enter city" {...field} />
                            </FormControl>
                            <FormMessage />
                          </FormItem>
                        )}
                      />

                      <FormField
                        control={projectForm.control}
                        name="state"
                        render={({ field }) => (
                          <FormItem>
                            <FormLabel>State</FormLabel>
                            <Select
                              onValueChange={field.onChange}
                              defaultValue={field.value}
                            >
                              <FormControl>
                                <SelectTrigger>
                                  <SelectValue placeholder="Select state" />
                                </SelectTrigger>
                              </FormControl>
                              <SelectContent>
                                {states.map((state) => (
                                  <SelectItem key={state} value={state}>
                                    {state}
                                  </SelectItem>
                                ))}
                              </SelectContent>
                            </Select>
                            <FormMessage />
                          </FormItem>
                        )}
                      />

                      <FormField
                        control={projectForm.control}
                        name="pinCode"
                        render={({ field }) => (
                          <FormItem>
                            <FormLabel>PIN Code</FormLabel>
                            <FormControl>
                              <Input placeholder="Enter PIN code" {...field} />
                            </FormControl>
                            <FormMessage />
                          </FormItem>
                        )}
                      />

                      <FormField
                        control={projectForm.control}
                        name="adminName"
                        render={({ field }) => (
                          <FormItem>
                            <FormLabel>Admin Name</FormLabel>
                            <FormControl>
                              <Input
                                placeholder="Enter admin name"
                                {...field}
                              />
                            </FormControl>
                            <FormMessage />
                          </FormItem>
                        )}
                      />

                      <FormField
                        control={projectForm.control}
                        name="adminEmail"
                        render={({ field }) => (
                          <FormItem>
                            <FormLabel>Email ID</FormLabel>
                            <FormControl>
                              <Input
                                type="email"
                                placeholder="admin@example.com"
                                {...field}
                              />
                            </FormControl>
                            <FormMessage />
                          </FormItem>
                        )}
                      />

                      <FormField
                        control={projectForm.control}
                        name="adminContact"
                        render={({ field }) => (
                          <FormItem>
                            <FormLabel>Contact Number</FormLabel>
                            <FormControl>
                              <Input
                                placeholder="Enter contact number"
                                {...field}
                              />
                            </FormControl>
                            <FormMessage />
                          </FormItem>
                        )}
                      />
                    </div>
                  </div>

                  {/* ──── Subscription Configuration ──── */}
                  <Separator className="my-2" />
                  <div className="space-y-4">
                    <div className="flex items-center gap-2">
                      <CreditCard className="h-4 w-4 text-primary" />
                      <h3 className="text-sm font-semibold">Subscription Configuration</h3>
                    </div>

                    {/* Inherit vs Override */}
                    <FormField
                      control={projectForm.control}
                      name="subscription_mode"
                      render={({ field }) => (
                        <FormItem>
                          <FormLabel>Subscription Mode</FormLabel>
                          <Select onValueChange={(v) => {
                            field.onChange(v);
                            if (v === "inherit") {
                              projectForm.setValue("plan_code", "");
                              projectForm.setValue("billingEnabled", false);
                            }
                          }} value={field.value || "inherit"}>
                            <FormControl>
                              <SelectTrigger>
                                <SelectValue />
                              </SelectTrigger>
                            </FormControl>
                            <SelectContent>
                              <SelectItem value="inherit">Inherit from Organization</SelectItem>
                              <SelectItem value="override">Assign Specific Plan</SelectItem>
                            </SelectContent>
                          </Select>
                          <FormDescription>
                            Choose whether this project uses the organization's plan or has its own
                          </FormDescription>
                        </FormItem>
                      )}
                    />

                    {/* Plan Selection (only if override) */}
                    {projectForm.watch("subscription_mode") === "override" && (
                      <>
                        <FormField
                          control={projectForm.control}
                          name="plan_code"
                          render={({ field }) => (
                            <FormItem>
                              <FormLabel>Select Plan</FormLabel>
                              <Select onValueChange={(v) => {
                                field.onChange(v);
                                projectForm.setValue("billingEnabled", v !== "" && v !== "no_service_override");
                              }} value={field.value || ""}>
                                <FormControl>
                                  <SelectTrigger>
                                    <SelectValue placeholder="Choose a subscription plan" />
                                  </SelectTrigger>
                                </FormControl>
                                <SelectContent>
                                  <SelectItem value="no_service_override">No Service (Free)</SelectItem>
                                  {(planCatalog?.plans || []).filter(p => p.is_active && p.code !== "no_service_override").map(plan => (
                                    <SelectItem key={plan.code} value={plan.code}>
                                      {plan.name}
                                      {plan.pricing_tiers?.monthly
                                        ? ` — ${formatPriceMajor(plan.pricing_tiers.monthly)}/mo`
                                        : " — Custom"}
                                    </SelectItem>
                                  ))}
                                </SelectContent>
                              </Select>
                            </FormItem>
                          )}
                        />

                        {/* Billing Period */}
                        {projectForm.watch("plan_code") && projectForm.watch("plan_code") !== "no_service_override" && (
                          <FormField
                            control={projectForm.control}
                            name="billing_period"
                            render={({ field }) => (
                              <FormItem>
                                <FormLabel>Billing Period</FormLabel>
                                <Select onValueChange={field.onChange} value={field.value || "monthly"}>
                                  <FormControl>
                                    <SelectTrigger>
                                      <SelectValue />
                                    </SelectTrigger>
                                  </FormControl>
                                  <SelectContent>
                                    <SelectItem value="monthly">Monthly</SelectItem>
                                    <SelectItem value="quarterly">Quarterly (Save ~7%)</SelectItem>
                                    <SelectItem value="annual">Annual (Save ~17%)</SelectItem>
                                  </SelectContent>
                                </Select>
                              </FormItem>
                            )}
                          />
                        )}

                        {/* Trial Toggle */}
                        {projectForm.watch("plan_code") && projectForm.watch("plan_code") !== "no_service_override" && (
                          <FormField
                            control={projectForm.control}
                            name="trial_enabled"
                            render={({ field }) => {
                              const selPlan = (planCatalog?.plans || []).find(p => p.code === projectForm.watch("plan_code"));
                              const trialDays = (selPlan?.trial_config as any)?.days || 14;
                              const trialAvailable = (selPlan?.trial_config as any)?.enabled;
                              if (!trialAvailable) return null;
                              return (
                                <FormItem className="flex flex-row items-start space-x-3 space-y-0 rounded-md border p-4 bg-amber-50/50 dark:bg-amber-950/20">
                                  <FormControl>
                                    <Checkbox checked={field.value} onCheckedChange={field.onChange} />
                                  </FormControl>
                                  <div className="space-y-1 leading-none">
                                    <FormLabel className="flex items-center gap-1.5">
                                      <Clock className="h-3.5 w-3.5 text-amber-600" />
                                      Start with {trialDays}-day free trial
                                    </FormLabel>
                                    <FormDescription>
                                      Try all features before your first billing cycle
                                    </FormDescription>
                                  </div>
                                </FormItem>
                              );
                            }}
                          />
                        )}
                      </>
                    )}
                  </div>
                </CardContent>
                <CardFooter className="flex justify-between">
                  {isEditingProject ? (
                    <Button
                      variant="outline"
                      type="button"
                      onClick={handleCancelEdit}
                    >
                      Cancel
                    </Button>
                  ) : (
                    <Button
                      variant="outline"
                      type="button"
                      onClick={() => setActiveTab("organization")}
                    >
                      Back
                    </Button>
                  )}
                  <Button type="submit" disabled={projectActionDisabled}>
                    {loading
                      ? isEditingProject
                        ? "Saving..."
                        : "Registering..."
                      : isEditingProject
                      ? "Save Changes"
                      : "Register Project"}
                  </Button>
                </CardFooter>
                {projectActionDisabled &&
                  !loading &&
                  ((isEditingProject && !canUpdateProject) ||
                    (!isEditingProject && !canCreateProject)) && (
                    <p className="text-sm text-destructive px-6 pb-4">
                      You do not have permission to{" "}
                      {isEditingProject ? "update" : "create"} projects.
                    </p>
                  )}
              </form>
            </Form>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  );
};

export default RegisterPage;
