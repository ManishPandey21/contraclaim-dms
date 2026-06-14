import React, { useState, useEffect } from "react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Wand2, FileText, Search, RefreshCw, FileSearch } from "lucide-react";
import { toast } from "sonner";
import { enhancedApi } from "@/services/enhanced-api";

interface DeepPlanningAssistantProps {
  letterContent: string;
  onContentSuggestion: (suggestion: string) => void;
  letterContext?: {
    title: string;
    recipient: string;
    subject: string;
    inputInfo?: string;
  };
  documentId?: string;
  letterNumber?: string;
  selectedReferenceNumber?: string;
}

interface DeepPlanningRequest {
  document_ids: string[];
  subject: string;
  recipient: string;
  user_id: string;
  context?: string;
  points?: string;
  target_letter_id?: string;
}

const DeepPlanningAssistant = ({
  letterContent,
  onContentSuggestion,
  letterContext,
  documentId,
  letterNumber: initialLetterNumber,
  selectedReferenceNumber,
}: DeepPlanningAssistantProps): JSX.Element => {
  const [isGenerating, setIsGenerating] = useState(false);
  const [lastSuggestion, setLastSuggestion] = useState("");
  const [documentIds, setDocumentIds] = useState<string[]>(
    documentId ? [documentId] : []
  );
  const [customDocumentId, setCustomDocumentId] = useState("");
  const [letterNumber, setLetterNumber] = useState("");
  const [reference, setReference] = useState("");
  const [additionalContext, setAdditionalContext] = useState("");

  // Prefill from props when available
  useEffect(() => {
    if (initialLetterNumber && !letterNumber) {
      setLetterNumber(initialLetterNumber);
    }
  }, [initialLetterNumber]);
  useEffect(() => {
    if (selectedReferenceNumber && !reference) {
      setReference(selectedReferenceNumber);
    }
  }, [selectedReferenceNumber]);

  const handleAddDocumentId = () => {
    if (
      customDocumentId.trim() &&
      !documentIds.includes(customDocumentId.trim())
    ) {
      setDocumentIds([...documentIds, customDocumentId.trim()]);
      setCustomDocumentId("");
    }
  };

  const handleRemoveDocumentId = (idToRemove: string) => {
    setDocumentIds(documentIds.filter((id) => id !== idToRemove));
  };

  const handleDeepPlanning = async () => {
    if (documentIds.length === 0) {
      toast.error("Please add at least one document ID");
      return;
    }

    if (!letterContext?.subject) {
      toast.error("Please provide a subject for the letter");
      return;
    }

    const userId = window.localStorage.getItem("user_id");
    if (!userId) {
      toast.error("User ID not found. Please log in again.");
      return;
    }

    setIsGenerating(true);

    try {
      const request: DeepPlanningRequest = {
        document_ids: documentIds,
        subject: letterContext.subject,
        recipient: letterContext.recipient || "",
        user_id: userId,
        context: additionalContext || letterContext.inputInfo,
        points: undefined,
        target_letter_id: undefined,
      };

      const response = await enhancedApi.deepPlanning(request);

      // Compose structured content per required format
      const numEffective = initialLetterNumber || letterNumber || "New Draft";
      const refEffective = selectedReferenceNumber || reference || "-";
      const keyPointsText = (response.extracted_key_points || "").trim() || "-";

      const clausesLines =
        response.quoted_clauses && response.quoted_clauses.length > 0
          ? response.quoted_clauses
              .map((c) => {
                const meta: string[] = [];
                if (c.page_number) meta.push(`Page ${c.page_number}`);
                if (c.line_numbers) meta.push(`Lines ${c.line_numbers}`);
                const notes = meta.length ? ` (${meta.join(", ")})` : "";
                return `- Clause ${c.clause_number}: ${c.content}${notes}`;
              })
              .join("\n")
          : "- None";

      const structured = `Letter Number: ${numEffective}
Reference: ${refEffective}
Subject/Title: ${letterContext?.subject || ""}
Summary:
${keyPointsText}

Document IDs: ${documentIds.join(", ")}

Extracted Key Points:
${keyPointsText}

Quoted Clauses:
${clausesLines}

Draft Letter:
${response.draft_letter}
`;

      setLastSuggestion(structured);
      toast.success("Deep planning draft generated successfully");

      // Show extracted information in toast
      if (response.extracted_key_points) {
        toast.info("Key points extracted", {
          description: response.extracted_key_points.slice(0, 200) + "...",
          duration: 8000,
        });
      }

      if (response.quoted_clauses && response.quoted_clauses.length > 0) {
        toast.info("Clauses referenced", {
          description: `${response.quoted_clauses.length} clauses extracted with page/line numbers`,
          duration: 6000,
        });
      }
    } catch (error) {
      console.error("Error in deep planning:", error);
      toast.error("Failed to generate deep planning draft. Please try again.");

      // Fallback to a basic template on error
      const fallbackContent = `Deep Planning Draft - ${letterContext.subject}

Based on analysis of document(s): ${documentIds.join(", ")}

${letterNumber ? `Letter Number: ${letterNumber}\n` : ""}
${reference ? `Reference: ${reference}\n` : ""}

Dear ${letterContext.recipient || "[Recipient]"},

I hope this letter finds you well. I am writing regarding ${
        letterContext.subject
      }.

[Deep planning analysis would appear here with extracted key points and quoted clauses]

I look forward to your response.

Sincerely,
[Your Name]
[Your Title]`;

      setLastSuggestion(fallbackContent);
      toast.info("Using fallback template due to API error");
    } finally {
      setIsGenerating(false);
    }
  };

  const applySuggestion = () => {
    if (lastSuggestion) {
      onContentSuggestion(lastSuggestion);
      toast.success("Deep planning suggestion applied to letter content");
    }
  };

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-lg">
            <FileSearch className="h-5 w-5 text-purple-600" />
            Deep Planning Assistant
          </CardTitle>
          <p className="text-sm text-muted-foreground">
            Generate comprehensive letter drafts using document analysis, key
            point extraction, and clause referencing
          </p>
        </CardHeader>
        <CardContent className="space-y-4">
          {/* Document IDs */}
          <div>
            <Label htmlFor="document-ids">Document IDs for Analysis</Label>
            <div className="flex gap-2 mb-2">
              <Input
                id="document-ids"
                value={customDocumentId}
                onChange={(e) => setCustomDocumentId(e.target.value)}
                placeholder="Enter document ID..."
                className="flex-1"
              />
              <Button onClick={handleAddDocumentId} variant="outline">
                Add
              </Button>
            </div>

            {documentIds.length > 0 && (
              <div className="space-y-1">
                {documentIds.map((id) => (
                  <div
                    key={id}
                    className="flex items-center justify-between p-2 bg-muted rounded text-sm"
                  >
                    <span className="font-mono">{id}</span>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => handleRemoveDocumentId(id)}
                      className="h-6 w-6 p-0 text-red-500"
                    >
                      ×
                    </Button>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Letter Metadata */}
          <div className="grid grid-cols-2 gap-4">
            <div>
              <Label htmlFor="letter-number">Letter Number (Optional)</Label>
              <Input
                id="letter-number"
                value={letterNumber}
                onChange={(e) => setLetterNumber(e.target.value)}
                placeholder="e.g., ABC-2023-001"
              />
            </div>
            <div>
              <Label htmlFor="reference">Reference (Optional)</Label>
              <Input
                id="reference"
                value={reference}
                onChange={(e) => setReference(e.target.value)}
                placeholder="Reference number or code"
              />
            </div>
          </div>

          {/* Additional Context */}
          <div>
            <Label htmlFor="additional-context">Additional Context</Label>
            <Textarea
              id="additional-context"
              value={additionalContext}
              onChange={(e) => setAdditionalContext(e.target.value)}
              rows={3}
              placeholder="Additional instructions or context for the AI..."
              className="resize-none"
            />
          </div>

          <Button
            onClick={handleDeepPlanning}
            disabled={isGenerating || documentIds.length === 0}
            className="gap-2 w-full bg-purple-600 hover:bg-purple-700"
          >
            {isGenerating ? (
              <RefreshCw className="h-4 w-4 animate-spin" />
            ) : (
              <FileSearch className="h-4 w-4" />
            )}
            Generate Deep Planning Draft
          </Button>
        </CardContent>
      </Card>

      {lastSuggestion && (
        <Card className="border-purple-200 bg-purple-50">
          <CardHeader>
            <CardTitle className="text-lg text-purple-800">
              Deep Planning Result
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="mb-4">
              <Textarea
                value={lastSuggestion}
                readOnly
                rows={12}
                className="font-mono text-sm bg-white border-purple-200"
              />
            </div>
            <div className="flex gap-2">
              <Button
                onClick={applySuggestion}
                className="bg-purple-600 hover:bg-purple-700"
              >
                Apply to Letter
              </Button>
              <Button
                variant="outline"
                onClick={() => setLastSuggestion("")}
                className="border-purple-200 text-purple-700 hover:bg-purple-100"
              >
                Dismiss
              </Button>
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
};

export default DeepPlanningAssistant;
