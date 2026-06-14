
import React, { useState } from 'react';
import { Button } from '@/components/ui/button';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Calendar } from '@/components/ui/calendar';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { CalendarIcon, MessageCircleQuestion, UserCheck } from 'lucide-react';
import { format } from 'date-fns';
import { toast } from 'sonner';
import { Avatar, AvatarFallback, AvatarImage } from '@/components/ui/avatar';

interface User {
  id: string;
  name: string;
  email: string;
  avatar?: string;
}

interface RequestInputFormProps {
  users: User[];
  letterId: string;
  onRequestSent: () => void;
  onCancel: () => void;
  onInputRequest: (letterId: string, requestDetails: string, requestedUserId: string, dueDate?: Date) => void;
}

const RequestInputForm: React.FC<RequestInputFormProps> = ({ 
  users, 
  letterId, 
  onRequestSent, 
  onCancel,
  onInputRequest
}) => {
  const [selectedUserId, setSelectedUserId] = useState<string>('');
  const [requestDetails, setRequestDetails] = useState<string>('');
  const [dueDate, setDueDate] = useState<Date | undefined>(undefined);
  
  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    
    if (!selectedUserId) {
      toast.error("Please select a user to request input from");
      return;
    }
    
    if (!requestDetails.trim()) {
      toast.error("Please provide details about what input you need");
      return;
    }
    
    // Call the input request handler
    onInputRequest(letterId, requestDetails, selectedUserId, dueDate);
    
    onRequestSent();
  };
  
  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      <div>
        <Label htmlFor="recipient">Request Input From</Label>
        <Select value={selectedUserId} onValueChange={setSelectedUserId}>
          <SelectTrigger id="recipient" className="w-full">
            <SelectValue placeholder="Select user" />
          </SelectTrigger>
          <SelectContent>
            {users.map(user => (
              <SelectItem key={user.id} value={user.id}>
                <div className="flex items-center gap-2">
                  <Avatar className="h-6 w-6">
                    <AvatarImage src={user.avatar} />
                    <AvatarFallback>{user.name.charAt(0)}</AvatarFallback>
                  </Avatar>
                  <span>{user.name}</span>
                </div>
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      
      <div>
        <Label htmlFor="request-details">What information do you need?</Label>
        <Textarea
          id="request-details"
          value={requestDetails}
          onChange={(e) => setRequestDetails(e.target.value)}
          rows={4}
          placeholder="Describe what information or clarification you need..."
          className="resize-none"
        />
      </div>
      
      <div>
        <Label htmlFor="due-date">Due Date (Optional)</Label>
        <Popover>
          <PopoverTrigger asChild>
            <Button
              variant="outline"
              className="w-full justify-start text-left font-normal"
              id="due-date"
            >
              <CalendarIcon className="mr-2 h-4 w-4" />
              {dueDate ? format(dueDate, 'PPP') : <span>Select a date</span>}
            </Button>
          </PopoverTrigger>
          <PopoverContent className="w-auto p-0">
            <Calendar
              mode="single"
              selected={dueDate}
              onSelect={setDueDate}
              initialFocus
            />
          </PopoverContent>
        </Popover>
        <p className="text-sm text-muted-foreground mt-1">
          If you need the input by a specific date, please select it here
        </p>
      </div>
      
      <div className="flex justify-end gap-2 pt-2">
        <Button type="button" variant="outline" onClick={onCancel}>
          Cancel
        </Button>
        <Button type="submit" className="gap-2">
          <MessageCircleQuestion className="h-4 w-4" />
          Send Input Request
        </Button>
      </div>
    </form>
  );
};

export default RequestInputForm;
