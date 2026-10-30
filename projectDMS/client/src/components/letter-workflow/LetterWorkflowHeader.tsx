import React, { useCallback, useState } from "react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import {
  Loader2,
  MessageCircleQuestion,
  PlusCircle,
  UserPlus,
} from "lucide-react";
import LetterInitiationForm from "./LetterInitiationForm";
import RequestInputForm from "./RequestInputForm";
import { Letter, User, Organization, Project } from "./types";
import type { CreateLetterInput } from "@/hooks/useLetterWorkflow";
import { useToast } from "@/hooks/use-toast";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Label } from "@/components/ui/label";

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
  selectedLetter?: Letter;
  selectedLetterId?: string;
  canAssignDrafter?: boolean;
  onAssignDrafter?: (
    letterId: string,
    payload: {
      user_id: string;
      drafting_profile:
        | "contractor"
        | "engineer_representation"
        | "employer_contract_review";
    }
  ) => Promise<void> | void;
  onAssignReviewer?: (
    letterId: string,
    runId: string,
    payload: {
      reviewer_user_id: string;
      due_at?: string;
      note?: string;
    }
  ) => Promise<void> | void;
  documentPrefill?: PrefillDataShape;
  onInitiationDialogClose?: () => void;
}

const DRAFTING_PROFILE_OPTIONS = [
  {
    value: "contractor",
    label: "Contractor",
  },
  {
    value: "engineer_representation",
    label: "Engineer Representation",
  },
  {
    value: "employer_contract_review",
    label: "Employer Contract Review",
  },
] as const;

type DraftingProfile = (typeof DRAFTING_PROFILE_OPTIONS)[number]["value"];

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
  selectedLetter,
  selectedLetterId,
  canAssignDrafter = false,
  onAssignDrafter,
  onAssignReviewer,
  documentPrefill,
  onInitiationDialogClose,
}) => {
  const { toast } = useToast();
  const [isAssignDrafterDialogOpen, setIsAssignDrafterDialogOpen] =
    useState(false);
  const [selectedDrafterId, setSelectedDrafterId] = useState("");
  const [selectedProfile, setSelectedProfile] =
    useState<DraftingProfile>("contractor");
  const [isAssigningDrafter, setIsAssigningDrafter] = useState(false);

  // General Work Assignment states
  const [assignmentType, setAssignmentType] = useState<"drafter" | "reviewer">("drafter");
  const [selectedReviewerId, setSelectedReviewerId] = useState("");
  const [reviewerNote, setReviewerNote] = useState("");
  const [reviewerDueDate, setReviewerDueDate] = useState("");

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

  const openAssignDrafterDialog = useCallback(() => {
    if (!selectedLetterId || !selectedLetter) {
      toast({
        title: "Select a letter first",
        description: "Please choose a letter from the table before assigning a drafter.",
        variant: "destructive",
      });
      return;
    }

    setSelectedDrafterId(
      selectedLetter.assignedTo?.id &&
        selectedLetter.assignedTo.id !== "unknown"
        ? selectedLetter.assignedTo.id
        : ""
    );
    setSelectedProfile(
      DRAFTING_PROFILE_OPTIONS.some(
        (option) => option.value === selectedLetter.draftingProfile
      )
        ? (selectedLetter.draftingProfile as DraftingProfile)
        : "contractor"
    );
    setIsAssignDrafterDialogOpen(true);
  }, [selectedLetter, selectedLetterId, toast]);

  const handleAssignWork = useCallback(async () => {
    if (!selectedLetterId) return;

    setIsAssigningDrafter(true);
    try {
      if (assignmentType === "drafter") {
        if (!selectedDrafterId || !onAssignDrafter) {
          setIsAssigningDrafter(false);
          return;
        }
        await onAssignDrafter(selectedLetterId, {
          user_id: selectedDrafterId,
          drafting_profile: selectedProfile,
        });
        toast({
          title: "Drafter assigned",
          description: "The selected drafter has been assigned to this letter.",
        });
      } else {
        const runId = selectedLetter?.graphRunId;
        if (!runId || !selectedReviewerId || !onAssignReviewer) {
          setIsAssigningDrafter(false);
          return;
        }
        await onAssignReviewer(selectedLetterId, runId, {
          reviewer_user_id: selectedReviewerId,
          due_at: reviewerDueDate ? new Date(reviewerDueDate).toISOString() : undefined,
          note: reviewerNote || undefined,
        });
        toast({
          title: "Reviewer assigned",
          description: "The selected reviewer has been assigned to this draft run.",
        });
      }
      setIsAssignDrafterDialogOpen(false);
    } catch (error: any) {
      const description =
        error?.response?.data?.detail ??
        error?.message ??
        `Unable to assign ${assignmentType}.`;
      toast({
        title: `Failed to assign ${assignmentType}`,
        description,
        variant: "destructive",
      });
      throw error;
    } finally {
      setIsAssigningDrafter(false);
    }
  }, [
    assignmentType,
    onAssignDrafter,
    onAssignReviewer,
    selectedDrafterId,
    selectedLetter,
    selectedLetterId,
    selectedProfile,
    selectedReviewerId,
    reviewerDueDate,
    reviewerNote,
    toast,
  ]);

  const requestDisabled = !selectedLetterId;
  const assignDisabled = !selectedLetterId;

  return (
    <div className="flex justify-between items-center mb-6">
      <h1 className="text-2xl font-bold">Letter Workflow Management</h1>

      <div className="flex items-center gap-2">
        <Dialog
          open={isInitiateDialogOpen}
          onOpenChange={handleInitiateDialogChange}
        >
          <DialogTrigger asChild>
            <Button className="bg-docsumo-blue hover:bg-docsumo-blue/90 text-white">
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
            <Button
              variant="outline"
              className="flex items-center"
              disabled={requestDisabled}
              title={
                requestDisabled
                  ? "Select a letter before requesting input"
                  : "Request input for the selected letter"
              }
            >
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

        {canAssignDrafter && (
          <Dialog
            open={isAssignDrafterDialogOpen}
            onOpenChange={setIsAssignDrafterDialogOpen}
          >
            <DialogTrigger asChild>
              <Button
                variant="outline"
                className="flex items-center"
                disabled={assignDisabled}
                title={
                  assignDisabled
                    ? "Select a letter before assigning work"
                    : "Assign a drafter or reviewer to the selected letter"
                }
                onClick={(event) => {
                  event.preventDefault();
                  openAssignDrafterDialog();
                }}
              >
                <UserPlus className="mr-2 h-4 w-4" />
                Assign Work
              </Button>
            </DialogTrigger>
            <DialogContent className="sm:max-w-2xl bg-card border border-border rounded-lg shadow-xl animate-in fade-in zoom-in duration-200">
              <DialogHeader>
                <DialogTitle className="text-xl font-bold flex items-center gap-2 text-foreground">
                  <UserPlus className="h-5 w-5 text-primary" />
                  Assign Work
                </DialogTitle>
                <DialogDescription className="text-sm text-muted-foreground">
                  Choose the work assignment type and designate the appropriate team member.
                </DialogDescription>
              </DialogHeader>

              {/* Assignment Type Selector Button Group */}
              <div className="flex gap-2 p-1 bg-muted/40 rounded-lg border border-border mb-2">
                <Button
                  type="button"
                  variant={assignmentType === "drafter" ? "default" : "ghost"}
                  className="flex-1 text-sm font-medium h-9 rounded-md transition-all duration-150"
                  onClick={() => setAssignmentType("drafter")}
                >
                  Drafter
                </Button>
                <Button
                  type="button"
                  variant={assignmentType === "reviewer" ? "default" : "ghost"}
                  className="flex-1 text-sm font-medium h-9 rounded-md transition-all duration-150"
                  disabled={!selectedLetter?.graphRunId}
                  title={!selectedLetter?.graphRunId ? "DMS review assignments require an active Draft Run" : ""}
                  onClick={() => setAssignmentType("reviewer")}
                >
                  Reviewer
                </Button>
              </div>

              {assignmentType === "drafter" ? (
                <div className="space-y-4 py-2">
                  <div className="space-y-2">
                    <Label className="text-sm font-semibold text-foreground">Select Drafter</Label>
                    <Select
                      value={selectedDrafterId}
                      onValueChange={setSelectedDrafterId}
                    >
                      <SelectTrigger className="w-full bg-background border border-input rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-primary">
                        <SelectValue placeholder="Choose a drafting professional" />
                      </SelectTrigger>
                      <SelectContent className="bg-popover border border-border shadow-md rounded-md max-h-60 overflow-y-auto">
                        {users.map((user) => (
                          <SelectItem key={user.id} value={user.id} className="text-sm cursor-pointer py-1.5 px-2 hover:bg-muted rounded">
                            {user.name}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                  <div className="space-y-2">
                    <Label className="text-sm font-semibold text-foreground">Role / Profile of Drafter</Label>
                    <Select
                      value={selectedProfile}
                      onValueChange={(value) =>
                        setSelectedProfile(value as DraftingProfile)
                      }
                    >
                      <SelectTrigger className="w-full bg-background border border-input rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-primary">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent className="bg-popover border border-border shadow-md rounded-md">
                        {DRAFTING_PROFILE_OPTIONS.map((option) => (
                          <SelectItem key={option.value} value={option.value} className="text-sm cursor-pointer py-1.5 px-2 hover:bg-muted rounded">
                            {option.label}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                </div>
              ) : (
                <div className="space-y-4 py-2">
                  <div className="space-y-2">
                    <Label className="text-sm font-semibold text-foreground">Select Reviewer</Label>
                    <Select
                      value={selectedReviewerId}
                      onValueChange={setSelectedReviewerId}
                    >
                      <SelectTrigger className="w-full bg-background border border-input rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-primary">
                        <SelectValue placeholder="Choose a review professional" />
                      </SelectTrigger>
                      <SelectContent className="bg-popover border border-border shadow-md rounded-md max-h-60 overflow-y-auto">
                        {users.map((user) => (
                          <SelectItem key={user.id} value={user.id} className="text-sm cursor-pointer py-1.5 px-2 hover:bg-muted rounded">
                            {user.name}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>

                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                    <div className="space-y-2">
                      <Label className="text-sm font-semibold text-foreground">Due Date (Optional)</Label>
                      <input
                        type="date"
                        value={reviewerDueDate}
                        onChange={(e) => setReviewerDueDate(e.target.value)}
                        className="w-full bg-background text-foreground border border-input rounded-md px-3 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-primary font-sans h-[38px]"
                      />
                    </div>
                  </div>

                  <div className="space-y-2">
                    <Label className="text-sm font-semibold text-foreground">Instructions / Note (Optional)</Label>
                    <textarea
                      value={reviewerNote}
                      onChange={(e) => setReviewerNote(e.target.value)}
                      placeholder="Enter review notes or verification instructions..."
                      rows={3}
                      className="w-full bg-background text-foreground border border-input rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-primary font-sans resize-none"
                    />
                  </div>
                </div>
              )}

              <DialogFooter className="mt-4 pt-2 border-t border-border flex items-center justify-end gap-2">
                <Button
                  variant="outline"
                  onClick={() => setIsAssignDrafterDialogOpen(false)}
                  className="px-4 py-2 text-sm font-medium hover:bg-muted rounded-md"
                >
                  Cancel
                </Button>
                <Button
                  onClick={handleAssignWork}
                  disabled={
                    isAssigningDrafter ||
                    (assignmentType === "drafter" ? !selectedDrafterId : !selectedReviewerId)
                  }
                  className="px-4 py-2 text-sm font-medium bg-primary text-primary-foreground hover:bg-primary/90 rounded-md"
                >
                  {isAssigningDrafter ? (
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  ) : (
                    <UserPlus className="mr-2 h-4 w-4" />
                  )}
                  Assign
                </Button>
              </DialogFooter>
            </DialogContent>
          </Dialog>
        )}
      </div>
    </div>
  );
};
