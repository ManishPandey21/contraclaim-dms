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
    
    // Simulate AI generation delay
    await new Promise(resolve => setTimeout(resolve, 2000));
    
    // Mock AI-generated content based on context
    const contextInfo = letterContext?.inputInfo ? `\n\nBased on the input provided: ${letterContext.inputInfo}` : '';
    
    const generatedContent = `Dear ${letterContext?.recipient || '[Recipient]'},

${userPrompt.includes('formal') ? 'I am writing to formally address' : 'I hope this letter finds you well. I am writing to'} the matter regarding ${letterContext?.subject || '[Subject]'}.${contextInfo}

${userPrompt.includes('urgent') ? 'This matter requires immediate attention and' : 'I would like to'} ${userPrompt.toLowerCase().includes('request') ? 'request your consideration for' : 'inform you about'} the following:

[Please elaborate on the specific details and requirements based on your input and context]

${userPrompt.includes('meeting') ? 'I would welcome the opportunity to discuss this matter further in a meeting at your convenience.' : 'I look forward to your response and any guidance you may provide.'}

Thank you for your time and consideration.

Sincerely,
[Your Name]
[Your Title]`;

    setLastSuggestion(generatedContent);
    setIsGenerating(false);
    
    toast.success("AI draft generated successfully");
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

  const improveDraft = () => {
    if (!letterContent.trim()) {
      toast.error("Please add some content to improve");
      return;
    }

    setIsGenerating(true);
    
    // Simulate improvement delay
    setTimeout(() => {
      const improvedContent = letterContent + "\n\n[AI Enhancement: Added formal closing and improved clarity]";
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
              disabled={isGenerating}
              className="gap-2"
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