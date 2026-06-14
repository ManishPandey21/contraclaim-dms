import React, { useState, useEffect } from 'react';
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { 
  Select, 
  SelectContent, 
  SelectItem, 
  SelectTrigger, 
  SelectValue 
} from "@/components/ui/select";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import { 
  ResizableHandle, 
  ResizablePanel, 
  ResizablePanelGroup 
} from "@/components/ui/resizable";
import { ScrollArea } from "@/components/ui/scroll-area";
import { useParams, Link as RouterLink } from 'react-router-dom';
import { 
  ArrowLeft,
  ChevronLeft, 
  ChevronRight, 
  Download, 
  FileText, 
  Share, 
  Edit, 
  MessageSquare, 
  Tag, 
  Clock, 
  Search,
  PlusCircle,
  Save,
  Paperclip,
  Trash,
  Mail,
  Loader2,
  Eye,
  Info,
  Link,
  LinkIcon,
  ExternalLink
} from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Form, FormControl, FormField, FormItem, FormLabel, FormMessage } from "@/components/ui/form";
import { useForm } from "react-hook-form";
import { 
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
  DialogClose
} from "@/components/ui/dialog";
import { Checkbox } from "@/components/ui/checkbox";
import { toast } from "sonner";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";

// Define interfaces and types
interface DocumentReference {
  id: string;
  name: string;
  date: string;
  type: string;
  direction: 'Incoming' | 'Outgoing';
  letterNo: string;
  subject: string;
  linkType?: 'direct' | 'indirect';
}

type MetadataFieldName = 'type' | 'date' | 'letterNo' | 'subject' | 'from' | 'to' | 'tag' | 'subTag' | 'status';

interface MetadataField {
  id: MetadataFieldName;
  label: string;
  value: string;
  type: 'select' | 'text' | 'textarea' | 'date' | 'radio';
  options?: string[];
}

type FormValues = {
  [key in MetadataFieldName]?: string;
};

type ShareFormValues = {
  recipientEmail: string;
  subject: string;
  message: string;
  includeLinkedDocs: boolean;
};

type LinkReferenceFormValues = {
  selectedDocuments: string[];
};

// Document Viewer Page Component
const DocumentViewerPage: React.FC = () => {
  const { id } = useParams();
  const [currentPage, setCurrentPage] = useState(1);
  const [zoom, setZoom] = useState(100);
  const [showMetadata, setShowMetadata] = useState(true);
  const [activeTab, setActiveTab] = useState("metadata");
  const [isShareDialogOpen, setIsShareDialogOpen] = useState(false);
  const [isLinkReferenceDialogOpen, setIsLinkReferenceDialogOpen] = useState(false);
  const [isSending, setIsSending] = useState(false);
  const [isLinking, setIsLinking] = useState(false);
  const [linkedReferences, setLinkedReferences] = useState<DocumentReference[]>([]);
  const [availableDocuments, setAvailableDocuments] = useState<DocumentReference[]>([]);
  const [searchQuery, setSearchQuery] = useState("");
  const [searchResults, setSearchResults] = useState<DocumentReference[]>([]);
  const [isSearching, setIsSearching] = useState(false);
  
  // Mocked document data
  const document = {
    id,
    title: 'Financial Report Q4 2023.pdf',
    type: 'PDF',
    direction: 'Incoming' as const,
    letterNo: 'FR-2023-Q4-001',
    date: '2023-12-15',
    pages: 12,
    status: 'Approved',
    createdAt: '2023-12-15T10:30:00Z',
    modifiedAt: '2023-12-20T14:45:00Z',
    createdBy: 'John Doe',
    size: '2.4 MB',
    version: '1.2',
    tags: ['Financial', 'Report', 'Q4', '2023'],
    from: 'Finance Department',
    to: 'Board of Directors',
    subject: 'Financial Report for Q4 2023',
  };

  // Mock available documents for linking
  useEffect(() => {
    // In a real app, this would be an API call filtered by the letter direction
    const oppositeDirection = document.direction === 'Incoming' ? 'Outgoing' : 'Incoming';
    const mockAvailableDocs: DocumentReference[] = [
      { 
        id: '001', 
        name: 'Q3 Financial Report Request.pdf', 
        date: '2023-09-15', 
        type: 'PDF',
        direction: oppositeDirection,
        letterNo: 'FR-2023-Q3-001',
        subject: 'Request for Q3 Financial Report'
      },
      { 
        id: '002', 
        name: 'Budget Approval 2023.pdf', 
        date: '2023-01-10', 
        type: 'PDF',
        direction: oppositeDirection,
        letterNo: 'BA-2023-001',
        subject: 'Budget Approval for 2023'
      },
      { 
        id: '003', 
        name: 'Financial Projections Request.pdf', 
        date: '2023-11-22', 
        type: 'PDF',
        direction: oppositeDirection,
        letterNo: 'FP-2023-001',
        subject: 'Request for Financial Projections 2024'
      },
      { 
        id: '004', 
        name: 'Quarterly Reporting Schedule.pdf', 
        date: '2023-01-05', 
        type: 'PDF',
        direction: oppositeDirection,
        letterNo: 'QRS-2023-001',
        subject: 'Quarterly Reporting Schedule for 2023'
      },
      { 
        id: '005', 
        name: 'Financial Audit Notification.pdf', 
        date: '2023-10-15', 
        type: 'PDF',
        direction: oppositeDirection,
        letterNo: 'FA-2023-001',
        subject: 'Notification of Annual Financial Audit'
      },
    ];
    
    setAvailableDocuments(mockAvailableDocs);
    setSearchResults(mockAvailableDocs);
    
    // Mock already linked references
    const mockLinkedDocs: DocumentReference[] = [
      { 
        id: '006', 
        name: 'Q3 Financial Report.pdf', 
        date: '2023-09-20', 
        type: 'PDF',
        direction: oppositeDirection,
        letterNo: 'FR-2023-Q3-002',
        subject: 'Financial Report for Q3 2023',
        linkType: 'direct'
      },
      { 
        id: '007', 
        name: 'Annual Budget Meeting Minutes.pdf', 
        date: '2023-01-15', 
        type: 'PDF',
        direction: document.direction,
        letterNo: 'MM-2023-001',
        subject: 'Minutes from Annual Budget Meeting',
        linkType: 'indirect'
      },
    ];
    
    setLinkedReferences(mockLinkedDocs);
  }, [document.direction, id]);

  const metadataFields: MetadataField[] = [
    { id: 'type', label: 'Type', value: 'Incoming', type: 'radio', options: ['Incoming', 'Outgoing'] },
    { id: 'date', label: 'Date', value: '2023-12-15', type: 'date' },
    { id: 'letterNo', label: 'Letter No.', value: 'FR-2023-Q4-001', type: 'text' },
    { id: 'subject', label: 'Subject', value: 'Financial Report for Q4 2023', type: 'text' },
    { id: 'from', label: 'From', value: 'Finance Department', type: 'text' },
    { id: 'to', label: 'To', value: 'Board of Directors', type: 'text' },
    { id: 'tag', label: 'Tag', value: 'Financial', type: 'select', options: ['Financial', 'HR', 'Legal', 'Marketing', 'Operations'] },
    { id: 'subTag', label: 'Sub-Tag', value: 'Report', type: 'select', options: ['Report', 'Invoice', 'Contract', 'Letter', 'Memo'] },
    { id: 'status', label: 'Status', value: 'Approved', type: 'select', options: ['Draft', 'Pending', 'Approved', 'Rejected'] },
  ];

  const relatedDocuments = [
    { id: '001', name: 'Q3 Financial Report.pdf', date: '2023-09-15', type: 'PDF' },
    { id: '002', name: 'Annual Budget 2023.xlsx', date: '2023-01-10', type: 'XLSX' },
    { id: '003', name: 'Financial Projections 2024.pdf', date: '2023-12-22', type: 'PDF' },
  ];

  const handleMetadataChange = (id: MetadataFieldName, value: string) => {
    console.log(`Updating metadata field ${id} to ${value}`);
  };

  const form = useForm<FormValues>({
    defaultValues: {
      type: 'Incoming',
      date: '2023-12-15',
      letterNo: 'FR-2023-Q4-001',
      subject: 'Financial Report for Q4 2023',
      from: 'Finance Department',
      to: 'Board of Directors',
      tag: 'Financial',
      subTag: 'Report',
      status: 'Approved'
    }
  });

  const shareForm = useForm<ShareFormValues>({
    defaultValues: {
      recipientEmail: '',
      subject: `Sharing document: ${document.title}`,
      message: `Hello,\n\nI'm sharing the document "${document.title}" with you.\n\nPlease review and let me know if you have any questions.\n\nRegards,\n${document.createdBy}`,
      includeLinkedDocs: false
    }
  });

  const linkReferenceForm = useForm<LinkReferenceFormValues>({
    defaultValues: {
      selectedDocuments: []
    }
  });

  const handleShareSubmit = async (values: ShareFormValues) => {
    try {
      setIsSending(true);
      
      const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
      if (!emailRegex.test(values.recipientEmail)) {
        toast.error("Please enter a valid email address");
        setIsSending(false);
        return;
      }
      
      console.log('Sharing document with:', values);
      
      await new Promise(resolve => setTimeout(resolve, 2000));
      
      const linkedDocIds = values.includeLinkedDocs 
        ? relatedDocuments.map(doc => doc.id) 
        : [];
      
      const payload = {
        recipientEmail: values.recipientEmail,
        subject: values.subject,
        message: values.message,
        documentId: document.id,
        linkedDocumentIds: linkedDocIds
      };
      
      console.log('API payload:', payload);
      
      toast.success("Document shared successfully", {
        description: `Email sent to ${values.recipientEmail}`
      });
      
      setIsShareDialogOpen(false);
      setIsSending(false);
    } catch (error) {
      console.error('Error sharing document:', error);
      toast.error("Failed to share document", {
        description: "Please try again later"
      });
      setIsSending(false);
    }
  };

  const handleLinkReferenceSubmit = async (values: LinkReferenceFormValues) => {
    try {
      setIsLinking(true);
      
      console.log('Linking documents:', values.selectedDocuments);
      
      // Simulate API call with delay
      await new Promise(resolve => setTimeout(resolve, 1500));
      
      // In a real app, this would be done by the backend
      // For now, we'll just add the selected documents to our linkedReferences state
      const newLinks = availableDocuments
        .filter(doc => values.selectedDocuments.includes(doc.id))
        .map(doc => ({
          ...doc,
          linkType: 'direct' as const
        }));
      
      // Add new links to the linkedReferences state
      setLinkedReferences(prev => [...prev, ...newLinks]);
      
      toast.success("References linked successfully", {
        description: `${newLinks.length} documents linked to this document`
      });
      
      setIsLinkReferenceDialogOpen(false);
      setIsLinking(false);
    } catch (error) {
      console.error('Error linking references:', error);
      toast.error("Failed to link references", {
        description: "Please try again later"
      });
      setIsLinking(false);
    }
  };

  const handleRemoveReference = (referenceId: string) => {
    setLinkedReferences(prev => prev.filter(ref => ref.id !== referenceId));
    
    toast.success("Reference removed", {
      description: "The document reference has been removed"
    });
  };

  const formatDate = (dateString: string) => {
    const date = new Date(dateString);
    return date.toLocaleDateString('en-US', { 
      year: 'numeric', 
      month: 'short', 
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit'
    });
  };

  const getLinkTypeLabel = (linkType: string | undefined) => {
    if (linkType === 'direct') return 'Direct Link';
    if (linkType === 'indirect') return 'Indirect Link';
    return 'Link';
  };

  const handleSearch = () => {
    if (!searchQuery.trim()) {
      setSearchResults(availableDocuments);
      return;
    }
    
    setIsSearching(true);
    
    // Simulate searching with a delay
    setTimeout(() => {
      const query = searchQuery.toLowerCase();
      const results = availableDocuments.filter(doc => 
        doc.name.toLowerCase().includes(query) || 
        doc.letterNo.toLowerCase().includes(query) || 
        doc.subject.toLowerCase().includes(query)
      );
      
      setSearchResults(results);
      setIsSearching(false);
    }, 500);
  };

  const handleAddReference = (docId: string) => {
    const documentToAdd = availableDocuments.find(doc => doc.id === docId);
    
    if (!documentToAdd) return;
    
    if (linkedReferences.some(ref => ref.id === docId)) {
      toast.info("This document is already linked");
      return;
    }
    
    const newReference = {
      ...documentToAdd,
      linkType: 'direct' as const
    };
    
    setLinkedReferences(prev => [...prev, newReference]);
    
    toast.success("Reference added successfully", {
      description: `"${documentToAdd.name}" added to linked references`
    });
  };

  return (
    <div className="h-screen flex flex-col bg-gray-100">
      <div className="bg-white border-b px-4 py-2 flex items-center justify-between">
        <div className="flex items-center space-x-4">
          <Button variant="ghost" size="icon" asChild>
            <RouterLink to="/documents">
              <ArrowLeft className="h-5 w-5" />
            </RouterLink>
          </Button>
          <div>
            <h1 className="text-lg font-medium">{document.title}</h1>
            <div className="flex items-center space-x-2">
              <Badge variant="outline" className="text-xs">
                {document.type}
              </Badge>
              <Badge variant={
                document.status === 'Approved' ? 'default' : 
                document.status === 'Draft' ? 'secondary' : 
                document.status === 'Rejected' ? 'destructive' : 'outline'
              } className="text-xs">
                {document.status}
              </Badge>
              <span className="text-xs text-muted-foreground">
                {document.pages} pages • {document.size}
              </span>
            </div>
          </div>
        </div>
        
        <div className="flex items-center space-x-2">
          <Button variant="ghost" size="sm" className="flex items-center gap-1">
            <Download className="h-4 w-4" />
            <span className="hidden sm:inline">Download</span>
          </Button>
          
          <Dialog open={isShareDialogOpen} onOpenChange={setIsShareDialogOpen}>
            <DialogTrigger asChild>
              <Button variant="ghost" size="sm" className="flex items-center gap-1">
                <Share className="h-4 w-4" />
                <span className="hidden sm:inline">Share</span>
              </Button>
            </DialogTrigger>
            <DialogContent className="sm:max-w-md">
              <DialogHeader>
                <DialogTitle>Share Document</DialogTitle>
                <DialogDescription>
                  Send this document via email to a recipient
                </DialogDescription>
              </DialogHeader>
              
              <Form {...shareForm}>
                <form onSubmit={shareForm.handleSubmit(handleShareSubmit)} className="space-y-4">
                  <FormField
                    control={shareForm.control}
                    name="recipientEmail"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>Recipient Email</FormLabel>
                        <FormControl>
                          <Input 
                            placeholder="recipient@example.com" 
                            {...field} 
                            type="email"
                            required
                          />
                        </FormControl>
                        <FormMessage />
                      </FormItem>
                    )}
                  />
                  
                  <FormField
                    control={shareForm.control}
                    name="subject"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>Subject</FormLabel>
                        <FormControl>
                          <Input 
                            placeholder="Email subject" 
                            {...field} 
                            required
                          />
                        </FormControl>
                        <FormMessage />
                      </FormItem>
                    )}
                  />
                  
                  <FormField
                    control={shareForm.control}
                    name="message"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>Message</FormLabel>
                        <FormControl>
                          <Textarea 
                            placeholder="Enter your message" 
                            {...field} 
                            rows={5}
                            required
                          />
                        </FormControl>
                        <FormMessage />
                      </FormItem>
                    )}
                  />
                  
                  <FormField
                    control={shareForm.control}
                    name="includeLinkedDocs"
                    render={({ field }) => (
                      <FormItem className="flex flex-row items-start space-x-3 space-y-0 rounded-md border p-4">
                        <FormControl>
                          <Checkbox
                            checked={field.value}
                            onCheckedChange={field.onChange}
                          />
                        </FormControl>
                        <div className="space-y-1 leading-none">
                          <FormLabel>Include linked documents</FormLabel>
                          <p className="text-sm text-muted-foreground">
                            Attach all linked documents to the email ({relatedDocuments.length} documents)
                          </p>
                        </div>
                      </FormItem>
                    )}
                  />
                  
                  <DialogFooter>
                    <Button
                      type="button"
                      variant="outline"
                      onClick={() => setIsShareDialogOpen(false)}
                      disabled={isSending}
                    >
                      Cancel
                    </Button>
                    <Button type="submit" disabled={isSending}>
                      {isSending ? (
                        <>
                          <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                          Sending...
                        </>
                      ) : (
                        <>
                          <Mail className="mr-2 h-4 w-4" />
                          Send Email
                        </>
                      )}
                    </Button>
                  </DialogFooter>
                </form>
              </Form>
            </DialogContent>
          </Dialog>
          
          <Dialog open={isLinkReferenceDialogOpen} onOpenChange={setIsLinkReferenceDialogOpen}>
            <DialogTrigger asChild>
              <Button variant="ghost" size="sm" className="flex items-center gap-1">
                <LinkIcon className="h-4 w-4" />
                <span className="hidden sm:inline">Link Reference</span>
              </Button>
            </DialogTrigger>
            <DialogContent className="sm:max-w-md">
              <DialogHeader>
                <DialogTitle>Link References</DialogTitle>
                <DialogDescription>
                  Connect this {document.direction.toLowerCase()} letter to {document.direction === "Incoming" ? "outgoing" : "incoming"} letters
                </DialogDescription>
              </DialogHeader>
              
              <Form {...linkReferenceForm}>
                <form onSubmit={linkReferenceForm.handleSubmit(handleLinkReferenceSubmit)} className="space-y-4">
                  <FormField
                    control={linkReferenceForm.control}
                    name="selectedDocuments"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>Select documents to link</FormLabel>
                        <FormControl>
                          <div className="border rounded-md overflow-hidden">
                            <Table>
                              <TableHeader>
                                <TableRow>
                                  <TableHead className="w-12"></TableHead>
                                  <TableHead>Document</TableHead>
                                  <TableHead>Letter No.</TableHead>
                                  <TableHead>Date</TableHead>
                                </TableRow>
                              </TableHeader>
                              <TableBody>
                                {availableDocuments.length === 0 ? (
                                  <TableRow>
                                    <TableCell colSpan={4} className="text-center py-4 text-muted-foreground">
                                      No {document.direction === "Incoming" ? "outgoing" : "incoming"} letters available to link
                                    </TableCell>
                                  </TableRow>
                                ) : (
                                  availableDocuments.map((doc) => (
                                    <TableRow key={doc.id}>
                                      <TableCell>
                                        <Checkbox 
                                          checked={field.value.includes(doc.id)}
                                          onCheckedChange={(checked) => {
                                            if (checked) {
                                              field.onChange([...field.value, doc.id]);
                                            } else {
                                              field.onChange(field.value.filter(id => id !== doc.id));
                                            }
                                          }}
                                        />
                                      </TableCell>
                                      <TableCell className="font-medium">{doc.name}</TableCell>
                                      <TableCell>{doc.letterNo}</TableCell>
                                      <TableCell>{doc.date}</TableCell>
                                    </TableRow>
                                  ))
                                )}
                              </TableBody>
                            </Table>
                          </div>
                        </FormControl>
                        <FormMessage />
                      </FormItem>
                    )}
                  />
                  
                  <DialogFooter>
                    <Button
                      type="button"
                      variant="outline"
                      onClick={() => setIsLinkReferenceDialogOpen(false)}
                      disabled={isLinking}
                    >
                      Cancel
                    </Button>
                    <Button 
                      type="submit" 
                      disabled={isLinking || linkReferenceForm.watch('selectedDocuments').length === 0}
                    >
                      {isLinking ? (
                        <>
                          <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                          Linking...
                        </>
                      ) : (
                        <>
                          <LinkIcon className="mr-2 h-4 w-4" />
                          Link Documents
                        </>
                      )}
                    </Button>
                  </DialogFooter>
                </form>
              </Form>
            </DialogContent>
          </Dialog>
          
          <Button variant="ghost" size="sm" className="flex items-center gap-1">
            <Edit className="h-4 w-4" />
            <span className="hidden sm:inline">Edit</span>
          </Button>
          <Button size="sm" className="flex items-center gap-1" onClick={() => setShowMetadata(!showMetadata)}>
            <Tag className="h-4 w-4" />
            <span className="hidden sm:inline">Metadata</span>
          </Button>
        </div>
      </div>
      
      <div className="flex-1 overflow-hidden">
        <ResizablePanelGroup direction="horizontal">
          <ResizablePanel defaultSize={75} minSize={50}>
            <div className="h-full flex flex-col">
              <div className="bg-white border-b flex justify-between items-center py-1 px-4">
                <div className="flex items-center space-x-4">
                  <div className="flex items-center space-x-1">
                    <Button
                      variant="ghost"
                      size="icon"
                      disabled={currentPage <= 1}
                      onClick={() => setCurrentPage(prev => Math.max(1, prev - 1))}
                    >
                      <ChevronLeft className="h-4 w-4" />
                    </Button>
                    <span className="text-sm">
                      Page {currentPage} of {document.pages}
                    </span>
                    <Button
                      variant="ghost"
                      size="icon"
                      disabled={currentPage >= document.pages}
                      onClick={() => setCurrentPage(prev => Math.min(document.pages, prev + 1))}
                    >
                      <ChevronRight className="h-4 w-4" />
                    </Button>
                  </div>
                  
                  <div className="flex items-center space-x-1">
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => setZoom(prev => Math.max(50, prev - 10))}
                      disabled={zoom <= 50}
                    >
                      -
                    </Button>
                    <Select value={zoom.toString()} onValueChange={(value) => setZoom(parseInt(value))}>
                      <SelectTrigger className="w-[100px]">
                        <SelectValue placeholder="Zoom" />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="50">50%</SelectItem>
                        <SelectItem value="75">75%</SelectItem>
                        <SelectItem value="100">100%</SelectItem>
                        <SelectItem value="125">125%</SelectItem>
                        <SelectItem value="150">150%</SelectItem>
                        <SelectItem value="200">200%</SelectItem>
                      </SelectContent>
                    </Select>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => setZoom(prev => Math.min(200, prev + 10))}
                      disabled={zoom >= 200}
                    >
                      +
                    </Button>
                  </div>
                </div>
                
                <div className="relative w-64">
                  <Search className="absolute left-2 top-1/2 transform -translate-y-1/2 h-4 w-4 text-muted-foreground" />
                  <Input 
                    placeholder="Search in document..." 
                    className="pl-8 h-8 text-sm"
                  />
                </div>
              </div>
              
              <div className="flex-1 overflow-auto bg-gray-900 flex items-center justify-center">
                <div 
                  className="bg-white shadow-lg mx-auto my-8"
                  style={{ 
                    width: `${8.5 * zoom / 100}in`, 
                    height: `${11 * zoom / 100}in`,
                    position: 'relative'
                  }}
                >
                  <div className="absolute inset-0 flex items-center justify-center">
                    <div className="text-center">
                      <FileText className="h-20 w-20 text-gray-300 mx-auto mb-4" />
                      <p className="text-gray-500">Preview not available</p>
                      <p className="text-xs text-gray-400 mt-1">Page {currentPage} of {document.pages}</p>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </ResizablePanel>
          
          {showMetadata && <ResizableHandle withHandle />}
          
          {showMetadata && (
            <ResizablePanel defaultSize={25} minSize={20}>
              <div className="h-full flex flex-col">
                <div className="bg-white border-b p-3">
                  <Tabs value={activeTab} onValueChange={setActiveTab} className="w-full">
                    <TabsList className="grid w-full grid-cols-4">
                      <TabsTrigger value="metadata">Metadata</TabsTrigger>
                      <TabsTrigger value="enclosure">Enclosures</TabsTrigger>
                      <TabsTrigger value="references">References</TabsTrigger>
                      <TabsTrigger value="details">Details</TabsTrigger>
                    </TabsList>

                    <TabsContent value="metadata" className="mt-0 space-y-5">
                      <Form {...form}>
                        <div className="space-y-4">
                          {metadataFields.map((field) => (
                            <FormField
                              key={field.id}
                              control={form.control}
                              name={field.id}
                              render={({ field: formField }) => (
                                <FormItem className="space-y-1">
                                  <FormLabel>{field.label}</FormLabel>
                                  <FormControl>
                                    {field.type === 'select' ? (
                                      <Select 
                                        value={formField.value} 
                                        onValueChange={formField.onChange}
                                      >
                                        <SelectTrigger id={field.id}>
                                          <SelectValue placeholder={`Select ${field.label}`} />
                                        </SelectTrigger>
                                        <SelectContent>
                                          {field.options?.map(option => (
                                            <SelectItem key={option} value={option}>{option}</SelectItem>
                                          ))}
                                        </SelectContent>
                                      </Select>
                                    ) : field.type === 'textarea' ? (
                                      <Textarea 
                                        id={field.id}
                                        {...formField}
                                        placeholder={`Enter ${field.label.toLowerCase()}`}
                                        className="resize-none"
                                        rows={3}
                                      />
                                    ) : field.type === 'date' ? (
                                      <Input 
                                        id={field.id}
                                        type="date"
                                        {...formField}
                                      />
                                    ) : field.type === 'radio' ? (
                                      <RadioGroup 
                                        value={formField.value}
                                        onValueChange={formField.onChange}
                                        className="flex gap-4"
                                      >
                                        {field.options?.map(option => (
                                          <div key={option} className="flex items-center space-x-2">
                                            <RadioGroupItem value={option} id={`${field.id}-${option}`} />
                                            <Label htmlFor={`${field.id}-${option}`}>{option}</Label>
                                          </div>
                                        ))}
                                      </RadioGroup>
                                    ) : (
                                      <Input 
                                        id={field.id}
                                        {...formField}
                                        placeholder={`Enter ${field.label.toLowerCase()}`}
                                      />
                                    )}
                                  </FormControl>
                                </FormItem>
                              )}
                            />
                          ))}
                          
                          <div className="flex gap-2 pt-3">
                            <Button className="flex-1" variant="outline">
                              <PlusCircle className="h-4 w-4 mr-2" />
                              Add Field
                            </Button>
                            <Button className="flex-1">
                              <Save className="h-4 w-4 mr-2" />
                              Save
                            </Button>
                          </div>
                        </div>
                      </Form>
                    </TabsContent>
                    
                    <TabsContent value="references" className="mt-0 space-y-5">
                      <Card>
                        <CardHeader className="py-3 px-4 flex flex-row items-center justify-between">
                          <CardTitle className="text-sm font-medium flex items-center gap-2">
                            <LinkIcon className="h-4 w-4 text-muted-foreground" />
                            Document References ({linkedReferences.length})
                          </CardTitle>
                          
                          {/* Add Reference Dialog with Search Bar */}
                          <Dialog>
                            <DialogTrigger asChild>
                              <Button variant="outline" size="sm" className="h-8">
                                <PlusCircle className="h-3.5 w-3.5 mr-1" />
                                Add Reference
                              </Button>
                            </DialogTrigger>
                            <DialogContent className="sm:max-w-md">
                              <DialogHeader>
                                <DialogTitle>Add Reference</DialogTitle>
                                <DialogDescription>
                                  Search and link documents to this {document.direction.toLowerCase()} letter
                                </DialogDescription>
                              </DialogHeader>
                              
                              <div className="space-y-4 py-2">
                                <div className="flex gap-2">
                                  <div className="relative flex-1">
                                    <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 h-4 w-4 text-muted-foreground" />
                                    <Input 
                                      placeholder="Search by name, letter number, or subject..." 
                                      className="pl-9"
                                      value={searchQuery}
                                      onChange={(e) => setSearchQuery(e.target.value)}
                                      onKeyDown={(e) => e.key === 'Enter' && handleSearch()}
                                    />
                                  </div>
                                  <Button 
                                    onClick={handleSearch} 
                                    variant="secondary"
                                    disabled={isSearching}
                                  >
                                    {isSearching ? (
                                      <Loader2 className="h-4 w-4 animate-spin" />
                                    ) : (
                                      "Search"
                                    )}
                                  </Button>
                                </div>
                                
                                <div className="border rounded-md overflow-hidden">
                                  <Table>
                                    <TableHeader>
                                      <TableRow>
                                        <TableHead>Document</TableHead>
                                        <TableHead className="hidden sm:table-cell">Letter No.</TableHead>
                                        <TableHead className="w-20"></TableHead>
                                      </TableRow>
                                    </TableHeader>
                                    <TableBody>
                                      {searchResults.length === 0 ? (
                                        <TableRow>
                                          <TableCell colSpan={3} className="text-center py-4 text-muted-foreground">
                                            {searchQuery ? "No matching documents found" : "No documents available"}
                                          </TableCell>
                                        </TableRow>
                                      ) : (
                                        searchResults.map((doc) => (
                                          <TableRow key={doc.id}>
                                            <TableCell>
                                              <div className="flex flex-col">
                                                <div className="flex items-center gap-1.5">
                                                  <FileText className="h-4 w-4 text-slate-500 shrink-0" />
                                                  <span className="font-medium text-sm truncate">{doc.name}</span>
                                                </div>
                                                <span className="text-xs text-muted-foreground pl-5">{doc.subject}</span>
                                              </div>
                                            </TableCell>
                                            <TableCell className="hidden sm:table-cell">{doc.letterNo}</TableCell>
                                            <TableCell>
                                              <Button
                                                variant="ghost"
                                                size="sm"
                                                className="h-8 w-8 p-0"
                                                onClick={() => handleAddReference(doc.id)}
                                                disabled={linkedReferences.some(ref => ref.id === doc.id)}
                                              >
                                                <PlusCircle className="h-4 w-4" />
                                              </Button>
                                            </TableCell>
                                          </TableRow>
                                        ))
                                      )}
                                    </TableBody>
                                  </Table>
                                </div>
                              </div>
                              
                              <DialogFooter>
                                <DialogClose asChild>
                                  <Button variant="outline">Done</Button>
                                </DialogClose>
                              </DialogFooter>
                            </DialogContent>
                          </Dialog>
                        </CardHeader>
                        <CardContent className="p-0">
                          {linkedReferences.length === 0 ? (
                            <div className="flex flex-col items-center justify-center py-8 px-4 text-center">
                              <div className="h-12 w-12 rounded-full bg-slate-100 flex items-center justify-center mb-3">
                                <LinkIcon className="h-6 w-6 text-slate-400" />
                              </div>
                              <h3 className="font-medium text-slate-900">No References Linked</h3>
                              <p className="text-sm text-slate-500 mt-1 max-w-xs">
                                This document is not linked to any {document.direction === 'Incoming' ? 'outgoing' : 'incoming'} letters.
                              </p>
                            </div>
                          ) : (
                            <div className="divide-y">
                              {linkedReferences.map((ref) => (
                                <div key={ref.id} className="p-3 hover:bg-slate-50">
                                  <div className="flex justify-between items-start">
                                    <div>
                                      <div className="flex items-center gap-1.5">
                                        <FileText className="h-4 w-4 text-slate-500" />
                                        <span className="font-medium text-sm">{ref.name}</span>
                                      </div>
                                      <p className="text-xs text-slate-500 mt-1">{ref.subject}</p>
                                      <div className="flex items-center gap-2 mt-2">
                                        <Badge variant="outline" className={ref.direction === "Incoming" ? "bg-blue-50 text-blue-700" : "bg-green-50 text-green-700"}>
                                          {ref.direction}
                                        </Badge>
                                        <Badge variant="outline" className={ref.linkType === "direct" ? "bg-indigo-50 text-indigo-700" : "bg-purple-50 text-purple-700"}>
                                          {getLinkTypeLabel(ref.linkType)}
                                        </Badge>
                                        <span className="text-xs text-slate-500">{ref.date}</span>
                                      </div>
                                    </div>
                                    <div className="flex gap-1">
                                      <Button variant="ghost" size="icon" className="h-7 w-7" asChild>
                                        <RouterLink to={`/document/${ref.id}`}>
                                          <ExternalLink className="h-3.5 w-3.5" />
                                        </RouterLink>
                                      </Button>
                                      <Button 
                                        variant="ghost" 
                                        size="icon" 
                                        className="h-7 w-7 text-red-500 hover:text-red-700 hover:bg-red-50"
                                        onClick={() => handleRemoveReference(ref.id)}
                                      >
                                        <Trash className="h-3.5 w-3.5" />
                                      </Button>
                                    </div>
                                  </div>
                                </div>
                              ))}
                            </div>
                          )}
                        </CardContent>
                      </Card>
                    </TabsContent>
                    
                    <TabsContent value="details" className="mt-0 space-y-5">
                      <Card>
                        <CardContent className="p-4 space-y-4">
                          <div>
                            <h3 className="font-medium text-sm flex items-center gap-2">
                              <Clock className="h-4 w-4 text-muted-foreground" />
                              Document History
                            </h3>
                            <div className="mt-2 space-y-2 text-sm">
                              <div className="flex justify-between">
                                <span className="text-muted-foreground">Created</span>
                                <span>{formatDate(document.createdAt)}</span>
                              </div>
                              <div className="flex justify-between">
                                <span className="text-muted-foreground">Modified</span>
                                <span>{formatDate(document.modifiedAt)}</span>
                              </div>
                              <div className="flex justify-between">
                                <span className="text-muted-foreground">Created By</span>
                                <span>{document.createdBy}</span>
                              </div>
                              <div className="flex justify-between">
                                <span className="text-muted-foreground">Version</span>
                                <span>{document.version}</span>
                              </div>
                            </div>
                          </div>
                          
                          <div className="pt-2">
                            <h3 className="font-medium text-sm flex items-center gap-2">
                              <Tag className="h-4 w-4 text-muted-foreground" />
                              Tags
                            </h3>
                            <div className="flex flex-wrap gap-2 mt-2">
                              {document.tags.map(tag => (
                                <Badge key={tag} variant="secondary" className="text-xs">
                                  {tag}
                                </Badge>
                              ))}
                              <Button variant="outline" size="sm" className="h-6 text-xs rounded-full">
                                + Add Tag
                              </Button>
                            </div>
                          </div>
                        </CardContent>
                      </Card>
                      
                      <Card>
                        <CardContent className="p-4">
                          <h3 className="font-medium text-sm flex items-center gap-2 mb-2">
                            <MessageSquare className="h-4 w-4 text-muted-foreground" />
                            Activity
                          </h3>
                          <div className="space-y-3 text-sm">
                            <div className="border-l-2 border-primary pl-3 py-1">
                              <p className="font-medium">John Doe added a comment</p>
                              <p className="text-muted-foreground text-xs">2 hours ago</p>
                            </div>
                            <div className="border-l-2 border-muted pl-3 py-1">
                              <p className="font-medium">Sarah Miller updated metadata</p>
                              <p className="text-muted-foreground text-xs">Yesterday at 4:30 PM</p>
                            </div>
                            <div className="border-l-2 border-muted pl-3 py-1">
                              <p className="font-medium">Document status changed to Approved</p>
                              <p className="text-muted-foreground text-xs">Dec 20, 2023</p>
                            </div>
                          </div>
                        </CardContent>
                      </Card>
                    </TabsContent>
                    
                    <TabsContent value="enclosure" className="mt-0 space-y-5">
                      <div className="flex justify-between items-center mb-3">
                        <h3 className="font-medium text-sm">Related Enclosures</h3>
                        <Button variant="ghost" size="sm" className="h-7 text-xs">
                          <PlusCircle className="h-3.5 w-3.5 mr-1" />
                          Link Document
                        </Button>
                      </div>
                      
                      <Table>
                        <TableHeader>
                          <TableRow>
                            <TableHead>Document</TableHead>
                            <TableHead>Date</TableHead>
                            <TableHead className="w-10"></TableHead>
                          </TableRow>
                        </TableHeader>
                        <TableBody>
                          {relatedDocuments.map((doc) => (
                            <TableRow key={doc.id}>
                              <TableCell className="flex items-center gap-2">
                                <FileText className="h-4 w-4 text-muted-foreground" />
                                <span>{doc.name}</span>
                              </TableCell>
                              <TableCell className="text-xs text-muted-foreground">
                                {new Date(doc.date).toLocaleDateString()}
                              </TableCell>
                              <TableCell>
                                <Button variant="ghost" size="icon" className="h-7 w-7">
                                  <Trash className="h-3.5 w-3.5" />
                                </Button>
                              </TableCell>
                            </TableRow>
                          ))}
                        </TableBody>
                      </Table>
                      
                      <div className="pt-2">
                        <div className="p-4 border rounded-lg flex flex-col items-center justify-center text-center space-y-2">
                          <Paperclip className="h-10 w-10 text-muted-foreground mb-2" />
                          <h4 className="font-medium">Drag & Drop</h4>
                          <p className="text-sm text-muted-foreground">
                            Drag and drop files here to attach them to this document
                          </p>
                          <Button size="sm" className="mt-2">Browse Files</Button>
                        </div>
                      </div>
                    </TabsContent>
                  </Tabs>
                </div>
                
                <ScrollArea className="flex-1">
                  <div className="p-4">
                    {/* Content will be displayed via TabsContent above */}
                  </div>
                </ScrollArea>
              </div>
            </ResizablePanel>
          )}
        </ResizablePanelGroup>
      </div>
    </div>
  );
};

export default DocumentViewerPage;
