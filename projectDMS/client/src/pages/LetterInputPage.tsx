import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useLetterWorkflow } from '@/hooks/useLetterWorkflow';
import LetterInputComponent from '@/components/letter-workflow/LetterInputComponent';
import { Button } from '@/components/ui/button';
import { ArrowLeft, Loader2 } from 'lucide-react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { useToast } from '@/hooks/use-toast';
import {
  mapInputRequestsToUi,
  mapLetterToUi,
  UILetter,
  UILetterInputRequest,
} from '@/utils/letterWorkflowMapping';

const LetterInputPage = () => {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const {
    letters,
    users,
    listInputRequests,
    respondToInputRequest,
    handleLetterUpdate,
    fetchLetters,
    moveLetterToStrategy,
  } = useLetterWorkflow();
  const { toast } = useToast();

  const letter = useMemo(
    () => letters.find((l) => l.id === id),
    [letters, id]
  );
  const uiLetter: UILetter | null = useMemo(
    () => (letter ? mapLetterToUi(letter, users) : null),
    [letter, users]
  );

  const [requests, setRequests] = useState<UILetterInputRequest[]>([]);
  const [loadingRequests, setLoadingRequests] = useState(false);
  const [processing, setProcessing] = useState(false);

  const loadRequests = useCallback(async () => {
    if (!id) return;
    setLoadingRequests(true);
    try {
      const data = await listInputRequests(id);
      setRequests(mapInputRequestsToUi(data ?? [], users));
    } catch (error: any) {
      const description =
        error?.response?.data?.detail ?? error?.message ?? 'Unable to load input requests';
      toast({
        title: 'Request fetch failed',
        description,
        variant: 'destructive',
      });
    } finally {
      setLoadingRequests(false);
    }
  }, [id, listInputRequests, users, toast]);

  useEffect(() => {
    loadRequests();
  }, [loadRequests]);

  const handleProvideInput = useCallback(
    async (responseText: string) => {
      if (!id) {
        throw new Error('Letter identifier missing');
      }
      setProcessing(true);
      try {
        const pending = requests.filter((request) => !request.response);
        for (const request of pending) {
          await respondToInputRequest(request.id, responseText);
        }

        await handleLetterUpdate(id, {
          content: responseText,
        });
        await moveLetterToStrategy(id);
        await fetchLetters();
        await loadRequests();

        toast({
          title: 'Moving to Strategy Stage',
          description: 'Inputs captured. Continue with strategic planning.',
        });
        navigate(`/letters/${id}/strategy`);
      } finally {
        setProcessing(false);
      }
    },
    [
      id,
      requests,
      respondToInputRequest,
      handleLetterUpdate,
      fetchLetters,
      loadRequests,
      navigate,
      moveLetterToStrategy,
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
            <p className="text-muted-foreground mb-4">The requested letter could not be found.</p>
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
    <div className="container mx-auto space-y-4 p-6">
      <h1 className="text-2xl font-bold">Letter Input</h1>
      <div className="mb-4">
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
          {loadingRequests && (
            <div className="mb-4 flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" />
              Loading input requests...
            </div>
          )}
          <LetterInputComponent
            letter={uiLetter}
            inputRequests={requests}
            onProvideInput={handleProvideInput}
            onCancel={handleCancel}
            isProcessing={processing}
          />
        </CardContent>
      </Card>
    </div>
  );
};

export default LetterInputPage;
