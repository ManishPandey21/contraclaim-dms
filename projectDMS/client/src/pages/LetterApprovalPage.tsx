import React, { useCallback, useMemo } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { ArrowLeft } from 'lucide-react';
import { useLetterWorkflow } from '@/hooks/useLetterWorkflow';
import LetterApprovalComponent from '@/components/letter-workflow/LetterApprovalComponent';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { useToast } from '@/hooks/use-toast';
import {
  mapLetterToUi,
  UILetter,
} from '@/utils/letterWorkflowMapping';

const LetterApprovalPage = () => {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const {
    letters,
    users,
    handleLetterUpdate,
    completeLetter,
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

  const handleApproval = useCallback(
    async (updatedLetter: any) => {
      if (!id) return;

      const patch: Record<string, unknown> = {
        content: updatedLetter.content,
        comments: updatedLetter.comments,
      };

      if (updatedLetter.status === 'Review') {
        patch.status = 'Review';
      }
      if (updatedLetter.status === 'Rejected') {
        patch.status = 'Rejected';
      }

      await handleLetterUpdate(id, patch);
      await fetchLetters();

      if (updatedLetter.status === 'Completed') {
        try {
          await completeLetter(id);
          await fetchLetters();
          toast({
            title: 'Letter completed',
            description: 'Letter marked as completed.',
          });
          navigate(`/letters/${id}/completed`);
        } catch (error: any) {
          const description =
            error?.response?.data?.detail ??
            error?.message ??
            'Unable to complete letter.';
          toast({
            title: 'Completion failed',
            description,
            variant: 'destructive',
          });
        }
        return;
      }

      if (updatedLetter.status === 'Review') {
        toast({
          title: 'Sent back for review',
          description: 'Letter returned to reviewers.',
        });
        navigate(`/letters/${id}/review`);
        return;
      }

      if (updatedLetter.status === 'Rejected') {
        toast({
          title: 'Letter rejected',
          description: 'Letter marked as rejected.',
          variant: 'destructive',
        });
        navigate('/letters');
        return;
      }

      toast({
        title: 'Approval notes saved',
        description: 'Updates stored successfully.',
      });
    },
    [
      id,
      handleLetterUpdate,
      completeLetter,
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
      <h1 className="text-2xl font-bold">Letter Approval</h1>
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
          <LetterApprovalComponent
            letter={uiLetter}
            onApproval={handleApproval}
            onCancel={handleCancel}
          />
        </CardContent>
      </Card>
    </div>
  );
};

export default LetterApprovalPage;
