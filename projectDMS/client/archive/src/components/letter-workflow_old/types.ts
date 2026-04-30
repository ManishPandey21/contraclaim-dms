export enum LetterStatus {
  Input = "Input",
  Draft = "Draft",
  Review = "Review",
  Approval = "Approval",
  Completed = "Completed",
  Rejected = "Rejected",
  Unknown = "Unknown",
}

export interface User {
  id: string;
  name: string;
  email: string;
  avatar?: string;
}

export interface Organization {
  id: string;
  name: string;
  description?: string;
}

export interface Project {
  id: string;
  name: string;
  description?: string;
  organizationId: string;
}

export interface InputRequest {
  id: string;
  requestedBy: User;
  requestDetails: string;
  dueDate?: string;
  createdAt: string;
  response?: string;
  respondedAt?: string;
}

export interface Letter {
  id: string;
  title: string;
  recipient: string;
  subject: string;
  content: string;
  status: LetterStatus;
  createdBy: User;
  assignedTo: User;
  organizationId?: string;
  projectId?: string;
  createdAt: string;
  updatedAt: string;
  statusStartDate?: string; // Date when the letter entered its current status
  comments?: string[];
  inputRequests?: InputRequest[];
  reference?: {
    id: string;
    title: string;
    subject: string;
    date: string;
    referenceNumber: string;
  };
}
