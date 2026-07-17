import React, { useCallback, useMemo } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { ArrowLeft } from 'lucide-react';
import { useLetterWorkflow } from '@/hooks/useLetterWorkflow';
import LetterReviewComponent from '@/components/letter-workflow/LetterReviewComponent';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { useToast } from '@/hooks/use-toast';
import {
  mapLetterToUi,
  UILetter,
} from '@/utils/letterWorkflowMapping';

const LetterReviewPage = () => {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const {
    letters,
    users,
    handleLetterUpdate,
    approveLetter,
    fetchLetters,
  } = useLetterWorkflow();
  const { toast } = useToast();

  const letter = useMemo(
    () => letters.find((entry) => entry.id === id),
    [letters, id]
  );
  const uiLetter: UILetter | null = useMemo(
    () => (letter ? mapLetterToUi(letter, users) : null),
    [letter, users]
  );

  const handleReview = useCallback(
    async (updatedLetter: any) => {
      if (!id) return;

      const patch: Record<string, unknown> = {
        content: updatedLetter.content,
        comments: updatedLetter.comments,
      };

      if (updatedLetter.status === 'Draft') {
        patch.status = 'Draft';
      }

      await handleLetterUpdate(id, patch);
      await fetchLetters();

      if (updatedLetter.status === 'Approval') {
        try {
          await approveLetter(id);
          await fetchLetters();
          toast({
            title: 'Letter approved',
            description: 'Letter progressed to the approval stage.',
          });
          navigate(`/letters/${id}/approval`);
        } catch (error: any) {
          const description =
            error?.response?.data?.detail ??
            error?.message ??
            'Unable to move letter to approval.';
          toast({
            title: 'Approval failed',
            description,
            variant: 'destructive',
          });
        }
        return;
      }

      if (updatedLetter.status === 'Draft') {
        toast({
          title: 'Sent back to draft',
          description: 'Letter returned to drafter with your comments.',
        });
        navigate(`/letters/${id}/draft`);
        return;
      }

      toast({
        title: 'Review notes saved',
        description: 'Changes stored successfully.',
      });
    },
    [
      id,
      handleLetterUpdate,
      approveLetter,
      fetchLetters,
      navigate,
      toast,
    ]
  );

  const handleCancel = () => {
    navigate('/letters');
  };

  if (!uiLetter) {
    return (
      <div className="container mx-auto p-6">
        <Card>
          <CardHeader>
            <CardTitle>Letter Not Found</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-muted-foreground mb-4">
              The requested letter could not be found.
            </p>
            <Button onClick={() => navigate('/letters')}>
              <ArrowLeft className="mr-2 h-4 w-4" />
              Back to Letters
            </Button>
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div className="container mx-auto p-6 space-y-4">
      <h1 className="text-2xl font-bold">Letter Review</h1>
      <div>
        <Button variant="ghost" onClick={() => navigate('/letters')}>
          <ArrowLeft className="mr-2 h-4 w-4" />
          Back to Letters
        </Button>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>{uiLetter.title}</CardTitle>
        </CardHeader>
        <CardContent>
          <LetterReviewComponent
            letter={uiLetter}
            onReview={handleReview}
            onCancel={handleCancel}
          />
        </CardContent>
      </Card>
    </div>
  );
};

export default LetterReviewPage;
