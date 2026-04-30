import * as z from "zod";

export const partySchema = z.object({
  name: z
    .string()
    .min(2, "Name must be at least 2 characters")
    .max(100, "Name cannot exceed 100 characters"),
  type: z.enum(["Organization", "Individual"]),
  contactEmail: z
    .string()
    .email("Invalid email address")
    .optional()
    .or(z.literal("")),
  contactPhone: z
    .string()
    .min(10, "Phone number must be at least 10 digits")
    .max(20, "Phone number cannot exceed 20 digits")
    .optional()
    .or(z.literal("")),
  projects: z.array(z.string()).optional().default([]),
  address: z.string().optional(),
  city: z.string().optional(),
  state: z.string().optional(),
  pinCode: z.string().optional(),
  country: z.string().optional(),
});

export const representativeSchema = z.object({
  name: z
    .string()
    .min(2, "Name must be at least 2 characters")
    .max(100, "Name cannot exceed 100 characters"),
  email: z.string().email("Invalid email address"),
  phone: z
    .string()
    .min(10, "Phone number must be at least 10 digits")
    .max(20, "Phone number cannot exceed 20 digits")
    .optional()
    .or(z.literal("")),
  position: z
    .string()
    .max(100, "Position cannot exceed 100 characters")
    .optional()
    .or(z.literal("")),
  isPrimary: z.boolean().default(false),
});

export const concernSchema = z.object({
  name: z
    .string()
    .min(2, "Name must be at least 2 characters")
    .max(100, "Name cannot exceed 100 characters"),
  partyId: z.string().min(1, "Party must be selected"),
  description: z
    .string()
    .max(500, "Description cannot exceed 500 characters")
    .optional()
    .or(z.literal("")),
  emails: z
    .array(z.string().email("Invalid email address"))
    .min(1, "At least one email address is required"),
});

// Form types derived from schemas
export type PartyFormData = z.infer<typeof partySchema>;
export type RepresentativeFormData = z.infer<typeof representativeSchema>;
export type ConcernFormData = z.infer<typeof concernSchema>;

// Validation functions
export const validatePartyForm = (data: unknown) => {
  return partySchema.safeParse(data);
};

export const validateRepresentativeForm = (data: unknown) => {
  return representativeSchema.safeParse(data);
};

export const validateConcernForm = (data: unknown) => {
  return concernSchema.safeParse(data);
};

// Default values
export const defaultPartyFormValues: PartyFormData = {
  name: "",
  type: "Organization",
  contactEmail: "",
  contactPhone: "",
  projects: [],
  address: "",
  city: "",
  state: "",
  pinCode: "",
  country: "",
};

export const defaultRepresentativeFormValues: RepresentativeFormData = {
  name: "",
  email: "",
  phone: "",
  position: "",
  isPrimary: false,
};

export const defaultConcernFormValues: ConcernFormData = {
  name: "",
  partyId: "",
  description: "",
  emails: [],
};
