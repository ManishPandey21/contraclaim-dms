
import React, { useEffect, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { 
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
  DialogFooter
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { 
  Table, 
  TableHeader, 
  TableBody, 
  TableRow, 
  TableHead, 
  TableCell 
} from '@/components/ui/table';
import { Search, X, Plus, Sparkles } from 'lucide-react';
import { format } from 'date-fns';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import AIAssistant from './AIAssistant';

interface LetterReference {
  id: string;
  title: string;
  subject: string;
  date: string;
  referenceNumber: string;
}

interface LetterDraftEditorProps {
  letter: Letter;
  referenceLetters?: ReferenceLetterOption[];
  onGenerateAiDraft?: (instructions: string) => Promise<string | null | undefined>;
  aiDraftDisabled?: boolean;
  aiDraftDisabledReason?: string;
  onSave: (updatedLetter: Letter) => void;
  onCancel: () => void;
}

interface User {
  id: string;
  name: string;
  email: string;
  avatar?: string;
}

interface InputRequest {
  id: string;
  requestedBy: User;
  requestDetails: string;
  dueDate?: string;
  createdAt: string;
  response?: string;
  respondedAt?: string;
}

interface Letter {
  id: string;
  title: string;
  recipient: string;
  subject: string;
  content: string;
  status: string;
  createdBy: User;
  assignedTo: User;
  createdAt: string;
  updatedAt: string;
  comments?: string[];
  inputRequests?: InputRequest[];
  reference?: LetterReference;
}

interface ReferenceLetterOption {
  id: string;
  title: string;
  subject: string;
  referenceNumber?: string;
  recipient?: string;
  createdAt?: string;
  status?: string;
}

const LetterDraftEditor: React.FC<LetterDraftEditorProps> = ({
  letter,
  referenceLetters = [],
  onGenerateAiDraft,
  aiDraftDisabled,
  aiDraftDisabledReason,
  onSave,
  onCancel,
}) => {
  const [content, setContent] = useState(letter.content);
  const [reference, setReference] = useState<LetterReference | undefined>(letter.reference);
  const [isReferenceDialogOpen, setIsReferenceDialogOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");

  // Draft generation and scoped section revision replace the parent letter
  // content asynchronously. Keep the editor aligned with that authoritative
  // run artifact instead of retaining the first render's stale local value.
  useEffect(() => {
    setContent(letter.content);
  }, [letter.content]);
  
  // Filter letters based on search query
  const filteredLetters = referenceLetters.filter((l) => {
    const refNo = l.referenceNumber ?? "";
    const recipient = l.recipient ?? "";
    return (
      l.title.toLowerCase().includes(searchQuery.toLowerCase()) ||
      l.subject.toLowerCase().includes(searchQuery.toLowerCase()) ||
      refNo.toLowerCase().includes(searchQuery.toLowerCase()) ||
      recipient.toLowerCase().includes(searchQuery.toLowerCase())
    );
  });
  
  const handleSubmitForReview = () => {
    const now = new Date().toISOString();
    
    onSave({
      ...letter,
      content,
      reference,
      status: 'Review',
      updatedAt: now
    });
  };
  
  const handleSaveDraft = () => {
    const now = new Date().toISOString();
    
    onSave({
      ...letter,
      content,
      reference,
      updatedAt: now
    });
  };
  
  const selectLetterReference = (selectedLetter: ReferenceLetterOption) => {
    setReference({
      id: selectedLetter.id,
      title: selectedLetter.title,
      subject: selectedLetter.subject,
      date: selectedLetter.createdAt ?? new Date().toISOString(),
      referenceNumber: selectedLetter.referenceNumber ?? selectedLetter.id
    });
    setIsReferenceDialogOpen(false);
  };
  
  const removeReference = () => {
    setReference(undefined);
  };
  
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
        
        {/* Reference Section */}
        <div className="mb-4">
          <div className="flex items-center justify-between mb-2">
            <Label className="text-muted-foreground text-sm">Reference</Label>
            <Dialog open={isReferenceDialogOpen} onOpenChange={setIsReferenceDialogOpen}>
              <DialogTrigger asChild>
                <Button variant="outline" size="sm" className="gap-1">
                  <Plus className="h-4 w-4" />
                  Add Reference
                </Button>
              </DialogTrigger>
              <DialogContent className="max-w-3xl">
                <DialogHeader>
                  <DialogTitle>Select a Letter Reference</DialogTitle>
                </DialogHeader>
                
                <div className="relative mb-4">
                  <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
                  <Input
                    placeholder="Search by title, subject, reference number, or recipient..."
                    className="pl-9"
                    value={searchQuery}
                    onChange={(e) => setSearchQuery(e.target.value)}
                  />
                </div>
                
                <div className="overflow-y-auto max-h-[300px]">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>Reference #</TableHead>
                        <TableHead>Title</TableHead>
                        <TableHead>Date</TableHead>
                        <TableHead>Recipient</TableHead>
                        <TableHead>Action</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {filteredLetters.length > 0 ? (
                        filteredLetters.map((prevLetter) => (
                          <TableRow key={prevLetter.id}>
                          <TableCell className="font-medium">
                            {prevLetter.referenceNumber ?? "-"}
                          </TableCell>
                          <TableCell>{prevLetter.title}</TableCell>
                          <TableCell>
                            {prevLetter.createdAt
                              ? format(new Date(prevLetter.createdAt), 'MMM d, yyyy')
                              : "-"}
                          </TableCell>
                          <TableCell>{prevLetter.recipient ?? "-"}</TableCell>
                            <TableCell>
                              <Button 
                                size="sm" 
                                variant="outline"
                                onClick={() => selectLetterReference(prevLetter)}
                              >
                                Select
                              </Button>
                            </TableCell>
                          </TableRow>
                        ))
                      ) : (
                        <TableRow>
                          <TableCell colSpan={5} className="text-center py-4 text-muted-foreground">
                            No letters found matching your search
                          </TableCell>
                        </TableRow>
                      )}
                    </TableBody>
                  </Table>
                </div>
                
                <DialogFooter>
                  <Button variant="outline" onClick={() => setIsReferenceDialogOpen(false)}>
                    Cancel
                  </Button>
                </DialogFooter>
              </DialogContent>
            </Dialog>
          </div>
          
          {reference ? (
            <div className="p-3 bg-muted rounded-md border relative">
              <Button 
                variant="ghost" 
                size="icon" 
                className="absolute top-2 right-2 h-6 w-6" 
                onClick={removeReference}
              >
                <X className="h-4 w-4" />
              </Button>
              <div className="grid grid-cols-2 gap-2 text-sm">
                <div>
                  <span className="text-muted-foreground">Reference Number:</span>
                  <div className="font-medium">{reference.referenceNumber}</div>
                </div>
                <div>
                  <span className="text-muted-foreground">Date:</span>
                  <div className="font-medium">{format(new Date(reference.date), 'MMM d, yyyy')}</div>
                </div>
                <div className="col-span-2">
                  <span className="text-muted-foreground">Title:</span>
                  <div className="font-medium">{reference.title}</div>
                </div>
                <div className="col-span-2">
                  <span className="text-muted-foreground">Subject:</span>
                  <div className="font-medium">{reference.subject}</div>
                </div>
              </div>
            </div>
          ) : (
            <div className="text-sm text-muted-foreground italic">
              No reference selected
            </div>
          )}
        </div>
        
        {letter.inputRequests && letter.inputRequests.length > 0 && (
          <div className="mb-4 p-3 bg-blue-50 rounded-md border border-blue-200">
            <Label className="text-muted-foreground text-sm block mb-2">Input Information</Label>
            {letter.inputRequests.map((request, index) => (
              <div key={index} className="mb-3 last:mb-0">
                <div className="text-sm font-medium text-blue-800 mb-1">
                  Request from {request.requestedBy.name}:
                </div>
                <div className="text-sm text-blue-700 mb-2">{request.requestDetails}</div>
                {request.response && (
                  <div className="bg-blue-100 p-2 rounded text-sm">
                    <div className="font-medium text-blue-800 mb-1">Response:</div>
                    <div className="text-blue-700">{request.response}</div>
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
        
        {letter.comments && letter.comments.length > 0 && (
          <div className="mb-4 p-3 bg-amber-50 rounded-md border border-amber-200">
            <Label className="text-muted-foreground text-sm block mb-1">Reviewer Comments</Label>
            <ul className="list-disc pl-5 space-y-1">
              {letter.comments.map((comment, index) => (
                <li key={index} className="text-sm">{comment}</li>
              ))}
            </ul>
          </div>
        )}
        
        <Tabs defaultValue="editor" className="w-full">
          <TabsList className="grid w-full grid-cols-2">
            <TabsTrigger value="editor">Letter Editor</TabsTrigger>
            <TabsTrigger value="ai-assistant" className="gap-2">
              <Sparkles className="h-4 w-4" />
              AI Assistant
            </TabsTrigger>
          </TabsList>
          
          <TabsContent value="editor" className="mt-4">
            <div className="mb-4">
              <Label htmlFor="content" className="block mb-2">Letter Content</Label>
              <Textarea
                id="content"
                value={content}
                onChange={(e) => setContent(e.target.value)}
                rows={15}
                placeholder="Compose your letter here..."
                className="font-mono"
              />
              <div className="text-xs text-muted-foreground mt-1">
                Use plain text for now. In a real application, this would be a rich text editor.
              </div>
            </div>
          </TabsContent>
          
          <TabsContent value="ai-assistant" className="mt-4">
            <AIAssistant
              letterContent={content}
              onContentSuggestion={setContent}
              onGenerateDraft={onGenerateAiDraft}
              disabled={aiDraftDisabled}
              disabledReason={aiDraftDisabledReason}
              letterContext={{
                title: letter.title,
                recipient: letter.recipient,
                subject: letter.subject,
                inputInfo: letter.inputRequests?.find(req => req.response)?.response
              }}
            />
          </TabsContent>
        </Tabs>
      </div>
      
      <div className="flex justify-end gap-2">
        <Button variant="outline" onClick={onCancel}>
          Cancel
        </Button>
        <Button variant="outline" onClick={handleSaveDraft}>
          Save Draft
        </Button>
        <Button onClick={handleSubmitForReview}>
          Submit for Review
        </Button>
      </div>
    </div>
  );
};

export default LetterDraftEditor;
