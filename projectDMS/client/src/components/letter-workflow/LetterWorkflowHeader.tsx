import React, { useCallback } from "react";
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
import type { CreateLetterInput } from "@/hooks/useLetterWorkflow";
import { useToast } from "@/hooks/use-toast";

interface PrefillDataShape {
  document_id?: string;
  letter_no?: string;
  subject?: string;
  recipient?: string;
  organization_id?: string;
  organization_name?: string;
  project_id?: string;
  project_name?: string;
  draft_reserved?: boolean;
}

interface LetterWorkflowHeaderProps {
  isInitiateDialogOpen: boolean;
  setIsInitiateDialogOpen: (open: boolean) => void;
  isRequestInputDialogOpen: boolean;
  setIsRequestInputDialogOpen: (open: boolean) => void;
  onLetterInitiation: (payload: CreateLetterInput) => Promise<void> | void;
  onInputRequest: (
    letterId: string,
    requestDetails: string,
    requestedUserId: string,
    dueDate?: Date
  ) => Promise<void> | void;
  users: User[];
  organizations: Organization[];
  projects: Project[];
  selectedLetterId?: string;
  documentPrefill?: PrefillDataShape;
  onInitiationDialogClose?: () => void;
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
  documentPrefill,
  onInitiationDialogClose,
}) => {
  const { toast } = useToast();

  const handleInitiateDialogChange = useCallback(
    (open: boolean) => {
      setIsInitiateDialogOpen(open);
      if (!open && onInitiationDialogClose) {
        onInitiationDialogClose();
      }
    },
    [setIsInitiateDialogOpen, onInitiationDialogClose]
  );

  const handleLetterInitiation = useCallback(
    async (payload: CreateLetterInput) => {
      try {
        await onLetterInitiation(payload);
        handleInitiateDialogChange(false);
        toast({
          title: "Letter initiated",
          description: "The letter has been created successfully.",
        });
      } catch (error: any) {
        const description =
          error?.response?.data?.detail ??
          error?.message ??
          "Unable to create letter.";
        toast({
          title: "Failed to create letter",
          description,
          variant: "destructive",
        });
        throw error;
      }
    },
    [onLetterInitiation, handleInitiateDialogChange, toast]
  );

  const handleInputRequest = useCallback(
    async (
      letterId: string,
      requestDetails: string,
      requestedUserId: string,
      dueDate?: Date
    ) => {
      if (!letterId) {
        toast({
          title: "Select a letter first",
          description:
            "Please choose a letter from the table before sending a request.",
          variant: "destructive",
        });
        return;
      }
      try {
        await onInputRequest(
          letterId,
          requestDetails,
          requestedUserId,
          dueDate
        );
        setIsRequestInputDialogOpen(false);
        toast({
          title: "Input request sent",
          description: "Team member notified successfully.",
        });
      } catch (error: any) {
        const description =
          error?.response?.data?.detail ??
          error?.message ??
          "Unable to send input request.";
        toast({
          title: "Failed to send request",
          description,
          variant: "destructive",
        });
        throw error;
      }
    },
    [onInputRequest, setIsRequestInputDialogOpen, toast]
  );

  const requestDisabled = !selectedLetterId;

  return (
    <div className="flex justify-between items-center mb-6">
      <h1 className="text-2xl font-bold">Letter Workflow Management</h1>

      <div className="flex gap-2">
        <Dialog
          open={isInitiateDialogOpen}
          onOpenChange={handleInitiateDialogChange}
        >
          <DialogTrigger asChild>
            <Button>
              <PlusCircle className="mr-2 h-4 w-4" />
              Initiate New Letter
            </Button>
          </DialogTrigger>
          <DialogContent className="sm:max-w-2xl">
            <DialogHeader>
              <DialogTitle>Initiate New Letter</DialogTitle>
            </DialogHeader>
            <LetterInitiationForm
              users={users}
              onSubmit={handleLetterInitiation}
              onCancel={() => handleInitiateDialogChange(false)}
              organizations={organizations}
              projects={projects}
              prefillData={documentPrefill}
            />
          </DialogContent>
        </Dialog>

        <Dialog
          open={isRequestInputDialogOpen}
          onOpenChange={setIsRequestInputDialogOpen}
        >
          <DialogTrigger asChild>
            <Button variant="outline" disabled={requestDisabled}>
              <MessageCircleQuestion className="mr-2 h-4 w-4" />
              Request Input
            </Button>
          </DialogTrigger>
          <DialogContent className="sm:max-w-2xl">
            <DialogHeader>
              <DialogTitle>Request Information or Clarification</DialogTitle>
            </DialogHeader>
            <RequestInputForm
              users={users}
              letterId={selectedLetterId || ""}
              onRequestSent={() => setIsRequestInputDialogOpen(false)}
              onCancel={() => setIsRequestInputDialogOpen(false)}
              onInputRequest={handleInputRequest}
            />
          </DialogContent>
        </Dialog>
      </div>
    </div>
  );
};
