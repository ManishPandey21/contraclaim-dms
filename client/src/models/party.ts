import { Representative } from "./representative";

export interface Party {
  _id: string;
  name: string;
  type: "Organization" | "Individual";
  organization_id?: string;
  contactEmail?: string;
  contactPhone?: string;
  createdAt: string;
  representatives: Representative[];
  projects: string[];
  address?: string;
  city?: string;
  state?: string;
  pinCode?: string;
  country?: string;
}

export interface PartyUpdate {
  name?: string;
  type?: "Organization" | "Individual";
  organization_id?: string;
  contactEmail?: string;
  contactPhone?: string;
  representatives?: Representative[];
  projects?: string[];
  address?: string;
  city?: string;
  state?: string;
  pinCode?: string;
  country?: string;
}
