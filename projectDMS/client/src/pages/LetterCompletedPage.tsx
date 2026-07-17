import React, { useMemo } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { ArrowLeft } from 'lucide-react';
import { useLetterWorkflow } from '@/hooks/useLetterWorkflow';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Label } from '@/components/ui/label';
import {
  mapLetterToUi,
  UILetter,
} from '@/utils/letterWorkflowMapping';

const LetterCompletedPage = () => {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { letters, users, formatDate } = useLetterWorkflow();

  const letter = useMemo(
    () => letters.find((entry) => entry.id === id),
    [letters, id]
  );
  const uiLetter: UILetter | null = useMemo(
    () => (letter ? mapLetterToUi(letter, users) : null),
    [letter, users]
  );

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

  const statusBadgeClass =
    uiLetter.status === 'Completed'
      ? 'bg-green-500'
      : uiLetter.status === 'Rejected'
      ? 'bg-red-500'
      : 'bg-secondary';

  return (
    <div className="container mx-auto p-6 space-y-4">
      <h1 className="text-2xl font-bold">Completed Letter</h1>
      <div>
        <Button variant="ghost" onClick={() => navigate('/letters')}>
          <ArrowLeft className="mr-2 h-4 w-4" />
          Back to Letters
        </Button>
      </div>

      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <CardTitle>{uiLetter.title}</CardTitle>
            <Badge className={statusBadgeClass}>
              {uiLetter.status}
            </Badge>
          </div>
        </CardHeader>
        <CardContent>
          <div className="space-y-4">
            <div className="rounded-md border p-4 space-y-4">
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <Label className="text-muted-foreground text-sm">Recipient</Label>
                  <div className="font-medium">{uiLetter.recipient}</div>
                </div>
                <div>
                  <Label className="text-muted-foreground text-sm">Created By</Label>
                  <div className="font-medium">{uiLetter.createdBy.name}</div>
                </div>
              </div>

              <div className="grid grid-cols-2 gap-4">
                <div>
                  <Label className="text-muted-foreground text-sm">Created Date</Label>
                  <div className="font-medium">{formatDate(uiLetter.createdAt)}</div>
                </div>
                <div>
                  <Label className="text-muted-foreground text-sm">
                    {uiLetter.status === 'Completed' ? 'Completed' : 'Updated'} Date
                  </Label>
                  <div className="font-medium">{formatDate(uiLetter.updatedAt)}</div>
                </div>
              </div>

              <div>
                <Label className="text-muted-foreground text-sm">Subject</Label>
                <div className="font-medium">{uiLetter.subject}</div>
              </div>

              <div>
                <Label className="text-muted-foreground text-sm">Letter Content</Label>
                <div className="mt-2 p-4 bg-muted rounded-md">
                  <div className="whitespace-pre-wrap font-mono text-sm">
                    {uiLetter.content}
                  </div>
                </div>
              </div>

              {uiLetter.comments && uiLetter.comments.length > 0 && (
                <div>
                  <Label className="text-muted-foreground text-sm">Comments</Label>
                  <div className="mt-2 p-4 bg-muted rounded-md">
                    <ul className="list-disc pl-5 space-y-1 text-sm">
                      {uiLetter.comments.map((comment, index) => (
                        <li key={index}>{comment}</li>
                      ))}
                    </ul>
                  </div>
                </div>
              )}
            </div>

            <div className="flex justify-end">
              <Button onClick={() => navigate('/letters')}>
                Close
              </Button>
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  );
};

export default LetterCompletedPage;
