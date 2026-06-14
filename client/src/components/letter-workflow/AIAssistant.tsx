import React, { useState } from 'react';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Label } from '@/components/ui/label';
import { Wand2, Search, RefreshCw } from 'lucide-react';
import { toast } from 'sonner';

interface AIAssistantProps {
  letterContent: string;
  onContentSuggestion: (suggestion: string) => void;
  onGenerateDraft?: (instructions: string) => Promise<string | null | undefined>;
  disabled?: boolean;
  disabledReason?: string;
  letterContext?: {
    title: string;
    recipient: string;
    subject: string;
    inputInfo?: string;
  };
}

const AIAssistant: React.FC<AIAssistantProps> = ({ 
  letterContent, 
  onContentSuggestion, 
  onGenerateDraft,
  disabled = false,
  disabledReason,
  letterContext 
}) => {
  const [userPrompt, setUserPrompt] = useState('');
  const [isGenerating, setIsGenerating] = useState(false);
  const [lastSuggestion, setLastSuggestion] = useState('');

  const handleGenerateDraft = async () => {
    if (!userPrompt.trim()) {
      toast.error("Please provide instructions for the AI assistant");
      return;
    }

    setIsGenerating(true);
    try {
      if (onGenerateDraft) {
        const generatedContent = await onGenerateDraft(userPrompt.trim());
        if (generatedContent) {
          setLastSuggestion(generatedContent);
          toast.success("AI draft generated successfully");
        }
        return;
      }

      toast.error("AI drafting workflow is not available for this letter");
    } catch (error: any) {
      toast.error("AI draft generation failed", {
        description: error?.message ?? "Unable to generate a draft from the approved strategy.",
      });
    } finally {
      setIsGenerating(false);
    }
  };

  const handleSearchSimilar = () => {
    // Mock similar letter search
    toast.info("Searching for similar letters...", {
      description: "This would search your letter database for similar content"
    });
  };

  const applySuggestion = () => {
    if (lastSuggestion) {
      onContentSuggestion(lastSuggestion);
      toast.success("Suggestion applied to letter content");
    }
  };

  const improveDraft = async () => {
    if (!letterContent.trim()) {
      toast.error("Please add some content to improve");
      return;
    }
    if (!onGenerateDraft) {
      toast.error("AI drafting workflow is not available for this letter");
      return;
    }

    setIsGenerating(true);
    const instruction =
      "Improve the current draft using the approved strategy while preserving the contractual position and source support.";
    try {
      const improvedContent = await onGenerateDraft(instruction);
      if (improvedContent) {
        setLastSuggestion(improvedContent);
        toast.success("Draft improvements generated");
      }
    } catch (error: any) {
      toast.error("Draft improvement failed", {
        description: error?.message ?? "Unable to improve this draft.",
      });
    } finally {
      setIsGenerating(false);
    }
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
            Get AI-powered help with drafting your letter using context and input information
          </p>
        </CardHeader>
        <CardContent className="space-y-4">
          <div>
            <Label htmlFor="ai-prompt">Describe what you want the letter to achieve</Label>
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
              disabled={isGenerating || disabled}
              className="gap-2"
              title={disabled ? disabledReason : undefined}
            >
              {isGenerating ? <RefreshCw className="h-4 w-4 animate-spin" /> : <Wand2 className="h-4 w-4" />}
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
                disabled={isGenerating || disabled || !onGenerateDraft}
                className="gap-2"
                title={disabled ? disabledReason : undefined}
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
            <CardTitle className="text-lg text-green-800">AI Suggestion</CardTitle>
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
              <Button onClick={applySuggestion} className="bg-green-600 hover:bg-green-700">
                Apply to Letter
              </Button>
              <Button 
                variant="outline" 
                onClick={() => setLastSuggestion('')}
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
