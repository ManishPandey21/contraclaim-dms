import React from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useLetterWorkflow } from '@/hooks/useLetterWorkflow';
import { Button } from '@/components/ui/button';
import { ArrowLeft } from 'lucide-react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Label } from '@/components/ui/label';

const LetterCompletedPage = () => {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { letters, formatDate } = useLetterWorkflow();

  const letter = letters.find(l => l.id === id);

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

  return (
    <div className="container mx-auto p-6">
      <div className="mb-4">
        <Button variant="ghost" onClick={() => navigate('/letter-workflow')}>
          <ArrowLeft className="mr-2 h-4 w-4" />
          Back to Letters
        </Button>
      </div>

      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <CardTitle>{letter.title}</CardTitle>
            <Badge 
              className={
                letter.status === 'Completed' 
                  ? 'bg-green-500' 
                  : 'bg-red-500'
              }
            >
              {letter.status}
            </Badge>
          </div>
        </CardHeader>
        <CardContent>
          <div className="space-y-4">
            <div className="rounded-md border p-4">
              <div className="grid grid-cols-2 gap-4 mb-4">
                <div>
                  <Label className="text-muted-foreground text-sm">Recipient</Label>
                  <div className="font-medium">{letter.recipient}</div>
                </div>
                <div>
                  <Label className="text-muted-foreground text-sm">Created By</Label>
                  <div className="font-medium">{letter.createdBy.name}</div>
                </div>
              </div>

              <div className="grid grid-cols-2 gap-4 mb-4">
                <div>
                  <Label className="text-muted-foreground text-sm">Created Date</Label>
                  <div className="font-medium">{formatDate(letter.createdAt)}</div>
                </div>
                <div>
                  <Label className="text-muted-foreground text-sm">{letter.status === 'Completed' ? 'Completed' : 'Rejected'} Date</Label>
                  <div className="font-medium">{formatDate(letter.updatedAt)}</div>
                </div>
              </div>

              <div className="mb-4">
                <Label className="text-muted-foreground text-sm">Subject</Label>
                <div className="font-medium">{letter.subject}</div>
              </div>

              <div>
                <Label className="text-muted-foreground text-sm">Letter Content</Label>
                <div className="mt-2 p-4 bg-muted rounded-md">
                  <div dangerouslySetInnerHTML={{ __html: letter.content }} />
                </div>
              </div>

              {letter.comments && letter.comments.length > 0 && (
                <div className="mt-4">
                  <Label className="text-muted-foreground text-sm">Comments</Label>
                  <div className="mt-2 p-4 bg-muted rounded-md">
                    <ul className="list-disc pl-5 space-y-1">
                      {letter.comments.map((comment, index) => (
                        <li key={index} className="text-sm">{comment}</li>
                      ))}
                    </ul>
                  </div>
                </div>
              )}
            </div>

            <div className="flex justify-end">
              <Button onClick={() => navigate('/letter-workflow')}>
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
