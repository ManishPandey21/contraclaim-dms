import React, { useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useLetterWorkflow } from '@/hooks/useLetterWorkflow';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Textarea } from '@/components/ui/textarea';
import { ArrowLeft, Sparkles, CheckCircle, Edit3 } from 'lucide-react';
import { useToast } from '@/hooks/use-toast';
import { Badge } from '@/components/ui/badge';

const LetterStrategicPlanPage = () => {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { letters, handleLetterUpdate } = useLetterWorkflow();
  const { toast } = useToast();
  
  const letter = letters.find(l => l.id === id);
  const [strategicPlan, setStrategicPlan] = useState(letter?.strategicPlan || '');
  const [isGenerating, setIsGenerating] = useState(false);
  const [isEditing, setIsEditing] = useState(false);

  if (!letter) {
    return (
      <div className="container mx-auto p-6">
        <Card>
          <CardHeader>
            <CardTitle>Letter Not Found</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-muted-foreground mb-4">The requested letter could not be found.</p>
            <Button onClick={() => navigate('/letter-workflow')}>
              <ArrowLeft className="mr-2 h-4 w-4" />
              Back to Letters
            </Button>
          </CardContent>
        </Card>
      </div>
    );
  }

  const handleGeneratePlan = async () => {
    setIsGenerating(true);
    
    // Simulate AI generation
    await new Promise(resolve => setTimeout(resolve, 2000));
    
    const mockPlan = `Strategic Plan for: ${letter.subject}

1. OBJECTIVE
   - Primary goal: ${letter.subject}
   - Target audience: ${letter.recipient}
   - Desired outcome: Achieve clear communication and actionable response

2. KEY MESSAGES
   - Opening: Establish context and purpose
   - Main points:
     * Address primary concern or request
     * Provide supporting evidence or rationale
     * Outline specific actions or expectations
   - Closing: Clear call-to-action and next steps

3. TONE & STYLE
   - Professional yet approachable
   - Clear and concise language
   - Respectful and collaborative tone
   - Action-oriented

4. STRUCTURE
   - Introduction: Context and purpose (1 paragraph)
   - Body: Main content with clear sections (2-3 paragraphs)
   - Conclusion: Summary and call-to-action (1 paragraph)

5. KEY CONSIDERATIONS
   - Maintain clarity throughout
   - Ensure all points are relevant to the objective
   - Use appropriate formatting for readability
   - Include specific dates, references, or requirements as needed

6. REVIEW POINTS
   - Does it address all necessary points?
   - Is the tone appropriate for the recipient?
   - Are action items clear and specific?
   - Is the timeline realistic?`;

    setStrategicPlan(mockPlan);
    setIsGenerating(false);
    
    toast({
      title: "Strategic Plan Generated",
      description: "Review and edit the plan before proceeding to drafting.",
    });
  };

  const handleSavePlan = () => {
    const updatedLetter = {
      ...letter,
      strategicPlan: strategicPlan
    };
    handleLetterUpdate(updatedLetter);
    setIsEditing(false);
    
    toast({
      title: "Plan Saved",
      description: "Strategic plan has been saved successfully.",
    });
  };

  const handleProceedToDraft = () => {
    if (!strategicPlan) {
      toast({
        title: "Plan Required",
        description: "Please generate and approve a strategic plan first.",
        variant: "destructive",
      });
      return;
    }
    
    const updatedLetter = {
      ...letter,
      strategicPlan: strategicPlan
    };
    handleLetterUpdate(updatedLetter);
    
    navigate(`/letter-workflow/draft/${id}`);
  };

  return (
    <div className="container mx-auto p-6 max-w-5xl">
      <div className="mb-4">
        <Button variant="ghost" onClick={() => navigate('/letter-workflow')}>
          <ArrowLeft className="mr-2 h-4 w-4" />
          Back to Letters
        </Button>
      </div>

      <Card className="mb-6">
        <CardHeader>
          <div className="flex items-start justify-between">
            <div>
              <CardTitle className="text-2xl">{letter.title}</CardTitle>
              <div className="mt-2 space-y-1 text-sm text-muted-foreground">
                <p><strong>To:</strong> {letter.recipient}</p>
                <p><strong>Subject:</strong> {letter.subject}</p>
              </div>
            </div>
            <Badge variant="secondary">Strategic Planning</Badge>
          </div>
        </CardHeader>
      </Card>

      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <CardTitle className="flex items-center gap-2">
              <Sparkles className="h-5 w-5 text-primary" />
              Strategic Plan
            </CardTitle>
            {!strategicPlan && (
              <Button onClick={handleGeneratePlan} disabled={isGenerating}>
                {isGenerating ? (
                  <>
                    <Sparkles className="mr-2 h-4 w-4 animate-spin" />
                    Generating...
                  </>
                ) : (
                  <>
                    <Sparkles className="mr-2 h-4 w-4" />
                    Generate Strategic Plan
                  </>
                )}
              </Button>
            )}
          </div>
        </CardHeader>
        <CardContent>
          {!strategicPlan ? (
            <div className="text-center py-12">
              <Sparkles className="h-16 w-16 mx-auto text-muted-foreground mb-4" />
              <h3 className="text-lg font-semibold mb-2">No Strategic Plan Yet</h3>
              <p className="text-muted-foreground mb-4">
                Generate an AI-powered strategic plan to guide your letter drafting process.
              </p>
            </div>
          ) : (
            <div className="space-y-4">
              {isEditing ? (
                <>
                  <Textarea
                    value={strategicPlan}
                    onChange={(e) => setStrategicPlan(e.target.value)}
                    className="min-h-[500px] font-mono text-sm"
                  />
                  <div className="flex gap-2">
                    <Button onClick={handleSavePlan}>
                      <CheckCircle className="mr-2 h-4 w-4" />
                      Save Changes
                    </Button>
                    <Button variant="outline" onClick={() => {
                      setStrategicPlan(letter.strategicPlan || '');
                      setIsEditing(false);
                    }}>
                      Cancel
                    </Button>
                  </div>
                </>
              ) : (
                <>
                  <div className="bg-muted/50 rounded-lg p-6">
                    <pre className="whitespace-pre-wrap font-sans text-sm">{strategicPlan}</pre>
                  </div>
                  <div className="flex gap-2 justify-between">
                    <Button variant="outline" onClick={() => setIsEditing(true)}>
                      <Edit3 className="mr-2 h-4 w-4" />
                      Edit Plan
                    </Button>
                    <div className="flex gap-2">
                      <Button variant="outline" onClick={handleGeneratePlan} disabled={isGenerating}>
                        <Sparkles className="mr-2 h-4 w-4" />
                        Regenerate
                      </Button>
                      <Button onClick={handleProceedToDraft}>
                        <CheckCircle className="mr-2 h-4 w-4" />
                        Approve & Proceed to Draft
                      </Button>
                    </div>
                  </div>
                </>
              )}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
};

export default LetterStrategicPlanPage;
