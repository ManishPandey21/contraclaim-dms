import React, { useState, useEffect } from "react";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import { ThumbsUp, ArrowLeft, Edit, Save } from "lucide-react";
import { toast } from "sonner";
import { Input } from "@/components/ui/input";
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

interface LetterReviewComponentProps {
  letter: Letter;
  onReview: (updatedLetter: Letter) => void;
  onCancel: () => void;
}

const LetterReviewComponent: React.FC<LetterReviewComponentProps> = ({
  letter,
  onReview,
  onCancel,
}) => {
  const [comment, setComment] = useState("");
  const [isSendingBack, setIsSendingBack] = useState(false);
  const [isEditing, setIsEditing] = useState(false);
  const [editedContent, setEditedContent] = useState(letter.content);
  const [letterComment, setLetterComment] = useState("");

  // Keep local edited content in sync if the letter prop updates (e.g., after submit/fetch)
  useEffect(() => {
    setEditedContent(letter.content || "");
  }, [letter.id, letter.content]);

  const handleApprove = async () => {
    try {
      const hdrs = {
        headers: {
          "X-Org-Id": (letter as any).organizationId ?? "",
          "X-Proj-Id": (letter as any).projectId ?? "",
        },
      };
      await api.post(`/letters/${letter.id}/approve`, {}, hdrs);
      const now = new Date().toISOString();

      const updatedComments = letterComment.trim()
        ? [...(letter.comments || []), letterComment]
        : letter.comments;

      onReview({
        ...letter,
        content: editedContent,
        status: "Approval",
        comments: updatedComments,
        updatedAt: now,
      });

      toast.success("Letter approved and forwarded for final review", {
        description: "The letter has been approved and sent for final review.",
      });
    } catch (e) {
      console.error("Approve letter failed", e);
      const anyErr: any = e as any;
      const status = anyErr?.response?.status;
      const detail =
        anyErr?.response?.data?.detail ||
        anyErr?.response?.data?.message ||
        anyErr?.message ||
        "Unknown error";
      toast.error("Failed to approve letter", {
        description: status ? `${status}: ${String(detail)}` : String(detail),
      });
    }
  };

  const handleSendBack = async () => {
    if (!comment.trim()) {
      toast.error("Comments required", {
        description: "Please provide comments for the drafter",
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
          content: editedContent,
          status: "Draft",
          comments: updatedComments,
        },
        hdrs
      );
      onReview({
        ...letter,
        content: editedContent,
        status: "Draft",
        comments: updatedComments,
        updatedAt: now,
      });
      toast.info("Letter sent back for revision", {
        description:
          "The letter has been returned to draft with your comments.",
      });
    } catch (e) {
      console.error("Send back to draft failed", e);
      const anyErr: any = e as any;
      const status = anyErr?.response?.status;
      const detail =
        anyErr?.response?.data?.detail ||
        anyErr?.response?.data?.message ||
        anyErr?.message ||
        "Unknown error";
      toast.error("Failed to send back to draft", {
        description: status ? `${status}: ${String(detail)}` : String(detail),
      });
    }
  };

  const handleSaveEdit = async () => {
    try {
      const hdrs = {
        headers: {
          "X-Org-Id": (letter as any).organizationId ?? "",
          "X-Proj-Id": (letter as any).projectId ?? "",
        },
      };
      // Persist edited content while staying in Review
      await api.put(
        `/letters/${letter.id}`,
        {
          content: editedContent,
        },
        hdrs
      );
      const now = new Date().toISOString();
      onReview({
        ...letter,
        content: editedContent,
        updatedAt: now,
      });
      setIsEditing(false);
      toast.success("Edits saved", {
        description: "Your changes to the letter have been saved.",
      });
    } catch (e) {
      console.error("Save edits failed", e);
      const anyErr: any = e as any;
      const status = anyErr?.response?.status;
      const detail =
        anyErr?.response?.data?.detail ||
        anyErr?.response?.data?.message ||
        anyErr?.message ||
        "Unknown error";
      toast.error("Failed to save edits", {
        description: status ? `${status}: ${String(detail)}` : String(detail),
      });
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center space-x-2">
          <Badge className="bg-yellow-500">Review Stage</Badge>
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

        <div className="p-4 border rounded-md bg-gray-50 mb-4 relative">
          <div className="flex items-center justify-between mb-2">
            <Label className="text-muted-foreground text-sm">
              Letter Content
            </Label>
            {!isEditing ? (
              <Button
                variant="outline"
                size="sm"
                className="gap-1"
                onClick={() => setIsEditing(true)}
              >
                <Edit className="h-4 w-4" />
                Edit
              </Button>
            ) : (
              <Button
                variant="outline"
                size="sm"
                className="gap-1"
                onClick={handleSaveEdit}
              >
                <Save className="h-4 w-4" />
                Save Edits
              </Button>
            )}
          </div>

          {isEditing ? (
            <Textarea
              value={editedContent}
              onChange={(e) => setEditedContent(e.target.value)}
              rows={10}
              className="font-mono mb-2"
            />
          ) : (
            <div className="whitespace-pre-wrap font-mono">{editedContent}</div>
          )}
        </div>

        <div className="mb-4">
          <Label htmlFor="letter-comment" className="block mb-2">
            Add a Comment to the Letter
          </Label>
          <Textarea
            id="letter-comment"
            value={letterComment}
            onChange={(e) => setLetterComment(e.target.value)}
            rows={3}
            placeholder="Add a general comment about this letter..."
            className="mb-2"
          />
        </div>

        {isSendingBack ? (
          <div className="mb-4">
            <Label htmlFor="review-comment" className="block mb-2">
              Comments for Drafter
            </Label>
            <Textarea
              id="review-comment"
              value={comment}
              onChange={(e) => setComment(e.target.value)}
              rows={4}
              placeholder="Enter your comments or suggestions for the drafter..."
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
              Back to Review
            </Button>
            <Button variant="destructive" onClick={handleSendBack}>
              Send Back to Draft
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
            <Button onClick={handleApprove} className="gap-2">
              <ThumbsUp className="h-4 w-4" />
              Approve for Final Review
            </Button>
          </>
        )}
      </div>
    </div>
  );
};

export default LetterReviewComponent;
