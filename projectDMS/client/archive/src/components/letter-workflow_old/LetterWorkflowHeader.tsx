import React from "react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { PlusCircle, MessageCircleQuestion } from "lucide-react";
import LetterInitiationForm from "./LetterInitiationForm";
import RequestInputForm from "./RequestInputForm";
import { User, Organization, Project } from "./types";

interface LetterWorkflowHeaderProps {
  isInitiateDialogOpen: boolean;
  setIsInitiateDialogOpen: (open: boolean) => void;
  isRequestInputDialogOpen: boolean;
  setIsRequestInputDialogOpen: (open: boolean) => void;
  onLetterInitiation: (newLetter: any) => void;
  onInputRequest: (
    letterId: string,
    requestDetails: string,
    requestedUserId: string,
    dueDate?: Date,
    keyPoints?: string,
    referenceLetterId?: string
  ) => void;
  users: User[];
  organizations: Organization[];
  projects: Project[];
  selectedLetterId?: string;
  // Optional initiating document context to auto-populate keypoints/summary
  documentId?: string;
}

export const LetterWorkflowHeader: React.FC<LetterWorkflowHeaderProps> = ({
  isInitiateDialogOpen,
  setIsInitiateDialogOpen,
  isRequestInputDialogOpen,
  setIsRequestInputDialogOpen,
  onLetterInitiation,
  onInputRequest,
  users,
  organizations,
  projects,
  selectedLetterId,
  documentId,
}) => {
  return (
    <div className="flex justify-between items-center mb-6">
      <h1 className="text-2xl font-bold">Letter Workflow Management</h1>

      <div className="flex gap-2">
        <Dialog
          open={isInitiateDialogOpen}
          onOpenChange={setIsInitiateDialogOpen}
        >
          <DialogTrigger asChild>
            <Button>
              <PlusCircle className="mr-2 h-4 w-4" />
              Initiate New Letter
            </Button>
          </DialogTrigger>
          <DialogContent className="sm:max-w-4xl w-[95vw] max-w-[1200px]">
            <DialogHeader>
              <DialogTitle>Initiate New Letter</DialogTitle>
            </DialogHeader>
            <LetterInitiationForm
              onSubmit={onLetterInitiation}
              onCancel={() => setIsInitiateDialogOpen(false)}
              organizations={organizations}
              projects={projects}
              users={users}
            />
          </DialogContent>
        </Dialog>

        <Dialog
          open={isRequestInputDialogOpen}
          onOpenChange={setIsRequestInputDialogOpen}
        >
          <DialogTrigger asChild>
            <Button variant="outline">
              <MessageCircleQuestion className="mr-2 h-4 w-4" />
              Request Input
            </Button>
          </DialogTrigger>
          <DialogContent className="sm:max-w-4xl w-[95vw] max-w-[1200px]">
            <DialogHeader>
              <DialogTitle>Request Information or Clarification</DialogTitle>
            </DialogHeader>
            <RequestInputForm
              users={users}
              letterId={selectedLetterId || ""}
              documentId={documentId}
              onRequestSent={() => setIsRequestInputDialogOpen(false)}
              onCancel={() => setIsRequestInputDialogOpen(false)}
              onInputRequest={onInputRequest}
            />
          </DialogContent>
        </Dialog>
      </div>
    </div>
  );
};
