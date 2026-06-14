import React from "react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Letter } from "./types";
import LetterDraftEditor from "./LetterDraftEditor";
import LetterReviewComponent from "./LetterReviewComponent";
import LetterApprovalComponent from "./LetterApprovalComponent";
import LetterInputComponent from "./LetterInputComponent";

interface LetterDialogProps {
  selectedLetter: Letter | null;
  onOpenChange: (open: boolean) => void;
  onLetterUpdate: (updatedLetter: Letter) => void;
}

export const LetterDialog: React.FC<LetterDialogProps> = ({
  selectedLetter,
  onOpenChange,
  onLetterUpdate,
}) => {
  const handleCancel = () => onOpenChange(false);

  if (!selectedLetter) return null;

  return (
    <Dialog open={!!selectedLetter} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-5xl w-[95vw] max-w-[1400px]">
        <DialogHeader>
          <DialogTitle>{selectedLetter.title}</DialogTitle>
        </DialogHeader>

        {selectedLetter.status === "Input" && (
          <LetterInputComponent
            letter={selectedLetter}
            onInput={onLetterUpdate}
            onCancel={handleCancel}
          />
        )}

        {selectedLetter.status === "Draft" && (
          <LetterDraftEditor
            letter={selectedLetter}
            onSave={onLetterUpdate}
            onCancel={handleCancel}
          />
        )}

        {selectedLetter.status === "Review" && (
          <LetterReviewComponent
            letter={selectedLetter}
            onReview={onLetterUpdate}
            onCancel={handleCancel}
          />
        )}

        {selectedLetter.status === "Approval" && (
          <LetterApprovalComponent
            letter={selectedLetter}
            onApproval={onLetterUpdate}
            onCancel={handleCancel}
          />
        )}

        {(selectedLetter.status === "Completed" ||
          selectedLetter.status === "Rejected") && (
          <div className="space-y-4">
            <div className="rounded-md border p-4">
              <div className="font-semibold mb-2">
                Subject: {selectedLetter.subject}
              </div>
              <div className="mb-2">To: {selectedLetter.recipient}</div>
              <div
                dangerouslySetInnerHTML={{ __html: selectedLetter.content }}
              />
            </div>
            <div className="flex justify-end">
              <Button onClick={handleCancel}>Close</Button>
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
};
