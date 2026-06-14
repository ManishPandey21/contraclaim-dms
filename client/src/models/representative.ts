export type RepresentativeLevel = "organization" | "project";

export interface Representative {
  _id: string;
  party_id?: string;
  organization_id?: string;
  project_id?: string;
  name: string;
  email: string;
  contact_number?: string;
  designation?: string;
  is_primary: boolean;
  level: RepresentativeLevel;
  use_head_office: boolean;
}

export interface RepresentativeCreate {
  party_id?: string;
  organization_id?: string;
  project_id?: string;
  name: string;
  email: string;
  contact_number?: string;
  designation?: string;
  is_primary: boolean;
  level: RepresentativeLevel;
  use_head_office: boolean;
}

export interface RepresentativeUpdate {
  name?: string;
  email?: string;
  contact_number?: string;
  designation?: string;
  is_primary?: boolean;
  level?: RepresentativeLevel;
  use_head_office?: boolean;
}
