import React from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useLetterWorkflow } from '@/hooks/useLetterWorkflow';
import LetterApprovalComponent from '@/components/letter-workflow/LetterApprovalComponent';
import { Button } from '@/components/ui/button';
import { ArrowLeft } from 'lucide-react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';

const LetterApprovalPage = () => {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { letters, handleLetterUpdate } = useLetterWorkflow();

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

  const handleApproval = (updatedLetter: any) => {
    handleLetterUpdate(updatedLetter);
    navigate('/letter-workflow');
  };

  const handleCancel = () => {
    navigate('/letter-workflow');
  };

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
          <CardTitle>{letter.title}</CardTitle>
        </CardHeader>
        <CardContent>
          <LetterApprovalComponent
            letter={letter}
            onApproval={handleApproval}
            onCancel={handleCancel}
          />
        </CardContent>
      </Card>
    </div>
  );
};

export default LetterApprovalPage;
