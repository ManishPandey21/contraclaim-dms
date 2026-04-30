import React, { useState } from "react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Wand2, Search, RefreshCw } from "lucide-react";
import { toast } from "sonner";
import { enhancedApi } from "@/services/enhanced-api";

interface AIAssistantProps {
  letterContent: string;
  onContentSuggestion: (suggestion: string) => void;
  letterContext?: {
    title: string;
    recipient: string;
    subject: string;
    inputInfo?: string;
  };
}

const AIAssistant = ({
  letterContent,
  onContentSuggestion,
  letterContext,
}: AIAssistantProps): JSX.Element => {
  const [userPrompt, setUserPrompt] = useState("");
  const [isGenerating, setIsGenerating] = useState(false);
  const [lastSuggestion, setLastSuggestion] = useState("");

  const handleGenerateDraft = async () => {
    if (!userPrompt.trim()) {
      toast.error("Please provide instructions for the AI assistant");
      return;
    }

    setIsGenerating(true);

    try {
      // Get current user ID from localStorage or use a default
      const userId = localStorage.getItem("user_id") || "current_user";

      const response = await enhancedApi.generateLetterDraft({
        subject: letterContext?.subject || "",
        recipient: letterContext?.recipient || "",
        user_id: userId,
        context: letterContext?.inputInfo,
        points: userPrompt,
      });

      setLastSuggestion(response.draft_letter);
      toast.success("AI draft generated successfully");
    } catch (error) {
      console.error("Error generating draft:", error);
      toast.error("Failed to generate AI draft. Please try again.");

      // Fallback to a basic template on error
      const fallbackContent = `Dear ${
        letterContext?.recipient || "[Recipient]"
      },

I hope this letter finds you well. I am writing regarding ${
        letterContext?.subject || "[Subject]"
      }.

${
  letterContext?.inputInfo
    ? `Based on the context provided: ${letterContext.inputInfo}\n\n`
    : ""
}

${userPrompt}

I look forward to your response and any guidance you may provide.

Thank you for your time and consideration.

Sincerely,
[Your Name]
[Your Title]`;

      setLastSuggestion(fallbackContent);
      toast.info("Using fallback template due to API error");
    } finally {
      setIsGenerating(false);
    }
  };

  const handleSearchSimilar = async () => {
    if (!letterContext?.subject && !userPrompt.trim()) {
      toast.error(
        "Please provide a subject or description to search for similar letters"
      );
      return;
    }

    setIsGenerating(true);

    try {
      const searchQuery = letterContext?.subject || userPrompt;
      const response = await enhancedApi.searchSimilarLetters(searchQuery, 5);

      if (response.similar_letters.length > 0) {
        toast.success(
          `Found ${response.similar_letters.length} similar letters`
        );

        // Show similar letters in a formatted way
        const similarLettersText = response.similar_letters
          .map(
            (letter, index) =>
              `${index + 1}. ${letter.subject} (To: ${
                letter.recipient
              }) - Similarity: ${(letter.similarity_score * 100).toFixed(1)}%`
          )
          .join("\n");

        toast.info("Similar letters found", {
          description: similarLettersText,
          duration: 10000,
        });
      } else {
        toast.info("No similar letters found in your database");
      }
    } catch (error) {
      console.error("Error searching similar letters:", error);
      toast.error("Failed to search for similar letters");
    } finally {
      setIsGenerating(false);
    }
  };

  const applySuggestion = () => {
    if (lastSuggestion) {
      onContentSuggestion(lastSuggestion);
      toast.success("Suggestion applied to letter content");
    }
  };

  const improveDraft = () => {
    if (!letterContent.trim()) {
      toast.error("Please add some content to improve");
      return;
    }

    setIsGenerating(true);

    // Simulate improvement delay
    setTimeout(() => {
      const improvedContent =
        letterContent +
        "\n\n[AI Enhancement: Added formal closing and improved clarity]";
      setLastSuggestion(improvedContent);
      setIsGenerating(false);
      toast.success("Draft improvements generated");
    }, 1500);
  };

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-lg">
            <Wand2 className="h-5 w-5 text-blue-600" />
            AI Letter Assistant
          </CardTitle>
          <p className="text-sm text-muted-foreground">
            Get AI-powered help with drafting your letter using context and
            input information
          </p>
        </CardHeader>
        <CardContent className="space-y-4">
          <div>
            <Label htmlFor="ai-prompt">
              Describe what you want the letter to achieve
            </Label>
            <Textarea
              id="ai-prompt"
              value={userPrompt}
              onChange={(e) => setUserPrompt(e.target.value)}
              rows={3}
              placeholder="e.g., 'Write a formal request for budget approval' or 'Draft an urgent response to their concerns'"
              className="resize-none"
            />
          </div>

          <div className="flex flex-wrap gap-2">
            <Button
              onClick={handleGenerateDraft}
              disabled={isGenerating}
              className="gap-2"
            >
              {isGenerating ? (
                <RefreshCw className="h-4 w-4 animate-spin" />
              ) : (
                <Wand2 className="h-4 w-4" />
              )}
              Generate AI Draft
            </Button>

            <Button
              variant="outline"
              onClick={handleSearchSimilar}
              className="gap-2"
            >
              <Search className="h-4 w-4" />
              Search Similar Letters
            </Button>

            {letterContent && (
              <Button
                variant="outline"
                onClick={improveDraft}
                disabled={isGenerating}
                className="gap-2"
              >
                <Wand2 className="h-4 w-4" />
                Improve Current Draft
              </Button>
            )}
          </div>
        </CardContent>
      </Card>

      {lastSuggestion && (
        <Card className="border-green-200 bg-green-50">
          <CardHeader>
            <CardTitle className="text-lg text-green-800">
              AI Suggestion
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="mb-4">
              <Textarea
                value={lastSuggestion}
                readOnly
                rows={12}
                className="font-mono text-sm bg-white border-green-200"
              />
            </div>
            <div className="flex gap-2">
              <Button
                onClick={applySuggestion}
                className="bg-green-600 hover:bg-green-700"
              >
                Apply to Letter
              </Button>
              <Button
                variant="outline"
                onClick={() => setLastSuggestion("")}
                className="border-green-200 text-green-700 hover:bg-green-100"
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

export default AIAssistant;
