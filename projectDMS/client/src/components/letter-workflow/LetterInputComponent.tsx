import React, { useMemo, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { CheckCircle, Send, ArrowRight } from 'lucide-react';
import { toast } from 'sonner';
import { PendencyIndicator } from './PendencyIndicator';
import { format } from 'date-fns';
import { InputRequest, Letter } from './types';

interface LetterInputComponentProps {
  letter: Letter;
  inputRequests: InputRequest[];
  onProvideInput: (response: string) => Promise<void> | void;
  onCancel: () => void;
  isProcessing?: boolean;
}

const LetterInputComponent: React.FC<LetterInputComponentProps> = ({ 
  letter, 
  inputRequests,
  onProvideInput, 
  onCancel,
  isProcessing = false,
}) => {
  const [inputResponse, setInputResponse] = useState('');
  const [submitting, setSubmitting] = useState(false);

  const handleProvideInput = async () => {
    if (!inputResponse.trim()) {
      toast.error("Please provide your input response");
      return;
    }

    setSubmitting(true);
    try {
      await onProvideInput(inputResponse);
      toast.success("Input provided successfully", {
        description: "Letter moved to Draft stage with your input",
      });
      setInputResponse('');
    } catch (error: any) {
      const description =
        error?.response?.data?.detail ?? error?.message ?? "Failed to send input response";
      toast.error(description);
    } finally {
      setSubmitting(false);
    }
  };

  const { pendingRequests, completedRequests } = useMemo(() => {
    const pending = inputRequests.filter((req) => !req.response);
    const completed = inputRequests.filter((req) => !!req.response);
    return { pendingRequests: pending, completedRequests: completed };
  }, [inputRequests]);

  return (
    <div className="space-y-4">
      <div className="rounded-md border p-4">
        <div className="grid grid-cols-2 gap-4 mb-4">
          <div>
            <Label className="text-muted-foreground text-sm">Letter Title</Label>
            <div className="font-medium">{letter.title}</div>
          </div>
          <div>
            <Label className="text-muted-foreground text-sm">Recipient</Label>
            <div className="font-medium">{letter.recipient}</div>
          </div>
        </div>
        
        <div className="mb-4">
          <Label className="text-muted-foreground text-sm">Subject</Label>
          <div className="font-medium">{letter.subject}</div>
        </div>

        <PendencyIndicator
          status={letter.status}
          date={letter.statusStartDate || letter.createdAt}
        />
      </div>

      {/* Pending Input Requests */}
      {pendingRequests.length > 0 && (
        <div className="space-y-3">
          <h3 className="text-lg font-semibold flex items-center gap-2">
            <Send className="h-5 w-5" />
            Input Requests
          </h3>
          {pendingRequests.map(request => (
            <Card key={request.id} className="border-amber-200 bg-amber-50">
              <CardHeader className="pb-3">
                <div className="flex items-center justify-between">
                  <CardTitle className="text-sm font-medium">
                    Input requested by {request.requestedBy.name}
                  </CardTitle>
                  <div className="flex items-center gap-2">
                    <Badge variant="secondary" className="bg-amber-100 text-amber-800">
                      Pending
                    </Badge>
                    {request.dueDate && (
                      <span className="text-xs text-muted-foreground">
                        Due: {format(new Date(request.dueDate), 'MMM d, yyyy')}
                      </span>
                    )}
                  </div>
                </div>
              </CardHeader>
              <CardContent className="pt-0">
                <div className="mb-3">
                  <Label className="text-sm font-medium">Request Details:</Label>
                  <p className="text-sm text-muted-foreground mt-1">
                    {request.requestDetails}
                  </p>
                </div>
                
                <div className="space-y-3">
                  <div>
                    <Label htmlFor="input-response">Your Response</Label>
                    <Textarea
                      id="input-response"
                      value={inputResponse}
                      onChange={(e) => setInputResponse(e.target.value)}
                      rows={6}
                      placeholder="Provide the requested information or clarification..."
                      className="resize-none"
                    />
                  </div>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      {/* Completed Input Requests */}
      {completedRequests.length > 0 && (
        <div className="space-y-3">
          <h3 className="text-lg font-semibold flex items-center gap-2">
            <CheckCircle className="h-5 w-5 text-green-600" />
            Completed Inputs
          </h3>
          {completedRequests.map(request => (
            <Card key={request.id} className="border-green-200 bg-green-50">
              <CardHeader className="pb-3">
                <div className="flex items-center justify-between">
                  <CardTitle className="text-sm font-medium">
                    Input requested by {request.requestedBy.name}
                  </CardTitle>
                  <Badge variant="secondary" className="bg-green-100 text-green-800">
                    Completed
                  </Badge>
                </div>
              </CardHeader>
              <CardContent className="pt-0">
                <div className="mb-3">
                  <Label className="text-sm font-medium">Request:</Label>
                  <p className="text-sm text-muted-foreground mt-1">
                    {request.requestDetails}
                  </p>
                </div>
                <div>
                  <Label className="text-sm font-medium">Your Response:</Label>
                  <p className="text-sm mt-1">
                    {request.response}
                  </p>
                  <p className="text-xs text-muted-foreground mt-1">
                    Responded on {format(new Date(request.respondedAt!), 'MMM d, yyyy h:mm a')}
                  </p>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
      
      <div className="flex justify-end gap-2">
        <Button variant="outline" onClick={onCancel}>
          Cancel
        </Button>
        {pendingRequests.length > 0 && (
          <Button 
            onClick={handleProvideInput} 
            className="gap-2" 
            disabled={submitting || isProcessing}
          >
            <ArrowRight className="h-4 w-4" />
            {submitting || isProcessing ? "Submitting..." : "Provide Input & Continue to Draft"}
          </Button>
        )}
      </div>
    </div>
  );
};

export default LetterInputComponent;
