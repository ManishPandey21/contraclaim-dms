import React from "react";
import { Button } from "@/components/ui/button";
import { Letter } from "./types";
import LetterDraftEditor from "./LetterDraftEditor";
import LetterReviewComponent from "./LetterReviewComponent";
import LetterApprovalComponent from "./LetterApprovalComponent";
import LetterInputComponent from "./LetterInputComponent";

/**
 * LetterWorkflowPanel
 * Renders the same body previously shown inside the modal dialog, but as an inline panel for a full page.
 */
interface LetterWorkflowPanelProps {
  selectedLetter: Letter;
  onLetterUpdate: (updatedLetter: Letter) => void;
  onCancel?: () => void; // Optional, allows parent to clear selection or navigate away
}

const LetterWorkflowPanel: React.FC<LetterWorkflowPanelProps> = ({
  selectedLetter,
  onLetterUpdate,
  onCancel,
}) => {
  if (!selectedLetter) return null;

  const handleCancel = () => {
    if (onCancel) onCancel();
  };

  return (
    <div className="w-full">
      <div className="mb-4">
        <h2 className="text-xl font-semibold">{selectedLetter.title}</h2>
      </div>

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
            <div dangerouslySetInnerHTML={{ __html: selectedLetter.content }} />
          </div>
          <div className="flex justify-end">
            {onCancel && <Button onClick={handleCancel}>Close</Button>}
          </div>
        </div>
      )}
    </div>
  );
};

export default LetterWorkflowPanel;
