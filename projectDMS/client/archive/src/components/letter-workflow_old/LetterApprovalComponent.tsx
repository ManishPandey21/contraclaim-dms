import React, { useState } from "react";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import { ThumbsUp, ThumbsDown, ArrowLeft } from "lucide-react";
import { toast } from "sonner";
import { PendencyIndicator } from "./PendencyIndicator";
import { api } from "@/services/api";

interface User {
  id: string;
  name: string;
  email: string;
  avatar?: string;
}

interface Letter {
  id: string;
  title: string;
  recipient: string;
  subject: string;
  content: string;
  status: string;
  createdBy: User;
  assignedTo: User;
  createdAt: string;
  updatedAt: string;
  statusStartDate?: string;
  comments?: string[];
}

interface LetterApprovalComponentProps {
  letter: Letter;
  onApproval: (updatedLetter: Letter) => void;
  onCancel: () => void;
}

const LetterApprovalComponent: React.FC<LetterApprovalComponentProps> = ({
  letter,
  onApproval,
  onCancel,
}) => {
  const [comment, setComment] = useState("");
  const [isSendingBack, setIsSendingBack] = useState(false);

  const handleApprove = async () => {
    try {
      const hdrs = {
        headers: {
          "X-Org-Id": (letter as any).organizationId ?? "",
          "X-Proj-Id": (letter as any).projectId ?? "",
        },
      };
      await api.post(`/letters/${letter.id}/complete`, {}, hdrs);
      const now = new Date().toISOString();

      onApproval({
        ...letter,
        status: "Completed",
        updatedAt: now,
      });

      toast.success("Letter has been approved", {
        description: "The letter has been approved and marked as completed.",
      });
    } catch (e) {
      console.error("Complete letter failed", e);
      const anyErr: any = e as any;
      const status = anyErr?.response?.status;
      const detail =
        anyErr?.response?.data?.detail ||
        anyErr?.response?.data?.message ||
        anyErr?.message ||
        "Unknown error";
      toast.error("Failed to mark letter as completed", {
        description: status ? `${status}: ${String(detail)}` : String(detail),
      });
    }
  };

  const handleReject = async () => {
    try {
      const now = new Date().toISOString();
      const hdrs = {
        headers: {
          "X-Org-Id": (letter as any).organizationId ?? "",
          "X-Proj-Id": (letter as any).projectId ?? "",
        },
      };
      await api.put(`/letters/${letter.id}`, { status: "Rejected" }, hdrs);
      onApproval({
        ...letter,
        status: "Rejected",
        updatedAt: now,
      });
      toast.error("Letter has been rejected", {
        description: "The letter has been rejected and marked accordingly.",
      });
    } catch (e) {
      console.error("Reject letter failed", e);
      const anyErr: any = e as any;
      const status = anyErr?.response?.status;
      const detail =
        anyErr?.response?.data?.detail ||
        anyErr?.response?.data?.message ||
        anyErr?.message ||
        "Unknown error";
      toast.error("Failed to reject letter", {
        description: status ? `${status}: ${String(detail)}` : String(detail),
      });
    }
  };

  const handleSendBack = async () => {
    if (!comment.trim()) {
      toast.error("Comments required", {
        description: "Please provide comments for further review",
      });
      return;
    }
    try {
      const now = new Date().toISOString();
      const updatedComments = [...(letter.comments || []), comment];
      const hdrs = {
        headers: {
          "X-Org-Id": (letter as any).organizationId ?? "",
          "X-Proj-Id": (letter as any).projectId ?? "",
        },
      };
      await api.put(
        `/letters/${letter.id}`,
        {
          status: "Review",
          comments: updatedComments,
        },
        hdrs
      );
      onApproval({
        ...letter,
        status: "Review",
        comments: updatedComments,
        updatedAt: now,
      });
      toast.info("Letter sent back for review", {
        description:
          "The letter has been returned to review stage with your comments.",
      });
    } catch (e) {
      console.error("Send back for review failed", e);
      const anyErr: any = e as any;
      const status = anyErr?.response?.status;
      const detail =
        anyErr?.response?.data?.detail ||
        anyErr?.response?.data?.message ||
        anyErr?.message ||
        "Unknown error";
      toast.error("Failed to send back for review", {
        description: status ? `${status}: ${String(detail)}` : String(detail),
      });
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center space-x-2">
          <Badge className="bg-purple-500">Final Approval Stage</Badge>
          {letter.statusStartDate && (
            <div className="ml-4">
              <PendencyIndicator
                date={letter.statusStartDate}
                status={letter.status}
              />
            </div>
          )}
        </div>
      </div>

      <div className="rounded-md border p-4">
        <div className="grid grid-cols-2 gap-4 mb-4">
          <div>
            <Label className="text-muted-foreground text-sm">
              Letter Title
            </Label>
            <div className="font-medium">{letter.title}</div>
          </div>
          <div>
            <Label className="text-muted-foreground text-sm">Recipient</Label>
            <div className="font-medium">{letter.recipient}</div>
          </div>
        </div>

        <div className="mb-4">
          <Label className="text-muted-foreground text-sm">Subject</Label>
          <div className="font-medium">{letter.subject}</div>
        </div>

        <div className="p-4 border rounded-md bg-gray-50 mb-4">
          <Label className="text-muted-foreground text-sm block mb-2">
            Letter Content
          </Label>
          <div className="whitespace-pre-wrap font-mono">{letter.content}</div>
        </div>

        {isSendingBack ? (
          <div className="mb-4">
            <Label htmlFor="approval-comment" className="block mb-2">
              Comments for Further Review
            </Label>
            <Textarea
              id="approval-comment"
              value={comment}
              onChange={(e) => setComment(e.target.value)}
              rows={4}
              placeholder="Enter your comments for further review..."
              className="mb-2"
            />
          </div>
        ) : null}
      </div>

      <div className="flex justify-end gap-2">
        <Button variant="outline" onClick={onCancel}>
          Cancel
        </Button>

        {isSendingBack ? (
          <>
            <Button variant="outline" onClick={() => setIsSendingBack(false)}>
              Back to Approval
            </Button>
            <Button variant="destructive" onClick={handleSendBack}>
              Send Back for Review
            </Button>
          </>
        ) : (
          <>
            <Button
              variant="outline"
              onClick={() => setIsSendingBack(true)}
              className="gap-2"
            >
              <ArrowLeft className="h-4 w-4" />
              Send Back
            </Button>
            <Button
              variant="destructive"
              onClick={handleReject}
              className="gap-2"
            >
              <ThumbsDown className="h-4 w-4" />
              Reject
            </Button>
            <Button onClick={handleApprove} className="gap-2">
              <ThumbsUp className="h-4 w-4" />
              Approve
            </Button>
          </>
        )}
      </div>
    </div>
  );
};

export default LetterApprovalComponent;
