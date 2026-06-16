
import React, { useState, useEffect, useCallback } from 'react';
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
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
import { ScrollArea } from "@/components/ui/scroll-area";
import { 
  Calendar,
  ClipboardList,
  Edit,
  File,
  FileText,
  Filter,
  ListChecks,
  Loader2,
  MessageSquare,
  PlusCircle,
  Search,
  Trash2,
  ThumbsDown,
  ThumbsUp,
  UserCheck,
  X
} from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Form, FormControl, FormDescription, FormField, FormItem, FormLabel, FormMessage } from "@/components/ui/form";
import { useForm } from "react-hook-form";
import { 
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { toast } from "sonner";
import { Separator } from "@/components/ui/separator";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { format } from "date-fns";
import { Calendar as CalendarComponent } from "@/components/ui/calendar";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { getTasks, createTask, updateTask, deleteTask, addTaskComment, type TaskDTO } from "@/services/tasks-api";
import { enhancedApi } from "@/services/enhanced-api";
import { listDocuments } from "@/services/documents-api";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

// Types
type TaskStatus = 'To Do' | 'In Progress' | 'Completed' | 'Overdue';
type WorkflowStage = 'Input' | 'Draft' | 'Review' | 'Comment' | 'Approval';

interface User {
  id: string;
  name: string;
  email: string;
  avatar?: string;
}

interface Document {
  id: string;
  title: string;
  type: string;
  createdAt: string;
}

interface Task {
  id: string;
  title: string;
  description: string;
  assignedTo: User;
  dueDate: string;
  status: TaskStatus;
  document?: Document;
  createdAt: string;
}

interface WorkflowItem {
  id: string;
  documentId: string;
  documentTitle: string;
  currentStage: WorkflowStage;
  history: WorkflowHistory[];
  assignedTo: User;
  createdAt: string;
}

interface WorkflowHistory {
  stage: WorkflowStage;
  timestamp: string;
  user: User;
  comment?: string;
}

interface Comment {
  id: string;
  text: string;
  user: User;
  timestamp: string;
}

// Map backend task status (open/in_progress/done) + due date to the UI status.
const deriveStatus = (status?: string | null, dueDate?: string | null): TaskStatus => {
  if (status === "done") return "Completed";
  if (status === "in_progress") return "In Progress";
  if (dueDate && new Date(dueDate).getTime() < Date.now()) return "Overdue";
  return "To Do";
};

const TasksPage = () => {
  const [activeTab, setActiveTab] = useState<string>("tasks");
  const [isNewTaskDialogOpen, setIsNewTaskDialogOpen] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [selectedWorkflow, setSelectedWorkflow] = useState<WorkflowItem | null>(null);
  const [newComment, setNewComment] = useState("");
  const [isSubmittingComment, setIsSubmittingComment] = useState(false);
  
  // Mock data
  const users: User[] = [
    { id: '1', name: 'John Doe', email: 'john@example.com', avatar: '/avatar1.png' },
    { id: '2', name: 'Jane Smith', email: 'jane@example.com', avatar: '/avatar2.png' },
    { id: '3', name: 'Alice Johnson', email: 'alice@example.com', avatar: '/avatar3.png' },
  ];
  
  const documents: Document[] = [
    { id: '1', title: 'Financial Report Q4 2023', type: 'PDF', createdAt: '2023-12-15T10:30:00Z' },
    { id: '2', title: 'Marketing Strategy 2024', type: 'DOCX', createdAt: '2023-12-16T14:20:00Z' },
    { id: '3', title: 'Product Launch Plan', type: 'PDF', createdAt: '2023-12-18T09:15:00Z' },
  ];
  
  // Real tasks loaded from the backend (GET /api/tasks, tenant-scoped).
  const [taskDtos, setTaskDtos] = useState<TaskDTO[]>([]);

  const reloadTasks = useCallback(async () => {
    try {
      setTaskDtos(await getTasks());
    } catch {
      /* leave the list empty on error; a toast is surfaced on actions */
    }
  }, []);

  useEffect(() => {
    void reloadTasks();
  }, [reloadTasks]);

  // Real org members + documents for the create dialog and list resolution.
  const [orgUsers, setOrgUsers] = useState<User[]>([]);
  const [orgDocuments, setOrgDocuments] = useState<Document[]>([]);

  useEffect(() => {
    let active = true;
    (async () => {
      try {
        const raw = await enhancedApi.getUsers();
        if (active) {
          setOrgUsers(
            (raw || []).map((u) => ({
              id: String(u.id || u._id || ""),
              name:
                [u.first_name, u.last_name].filter(Boolean).join(" ") ||
                u.username ||
                u.email ||
                "User",
              email: u.email || "",
            }))
          );
        }
      } catch {
        /* dropdown falls back to empty */
      }
      try {
        const res = await listDocuments({ limit: 100 });
        if (active) {
          setOrgDocuments(
            (res?.documents || []).map((d) => ({
              id: String(d._id || ""),
              title: d.name || d.filename || "Untitled",
              type: d.uploadType || "",
              createdAt: d.createdAt || "",
            }))
          );
        }
      } catch {
        /* dropdown falls back to empty */
      }
    })();
    return () => {
      active = false;
    };
  }, []);

  // Per-task comments view
  const [commentsTask, setCommentsTask] = useState<TaskDTO | null>(null);
  const [taskComment, setTaskComment] = useState("");
  const [isAddingComment, setIsAddingComment] = useState(false);
  const [editingTask, setEditingTask] = useState<TaskDTO | null>(null);

  const submitTaskComment = async () => {
    if (!commentsTask || !taskComment.trim()) return;
    try {
      setIsAddingComment(true);
      const updated = await addTaskComment(commentsTask.id, taskComment.trim());
      setCommentsTask(updated);
      setTaskComment("");
      await reloadTasks();
      toast.success("Comment added");
    } catch {
      toast.error("Failed to add comment");
    } finally {
      setIsAddingComment(false);
    }
  };

  // Derive the UI Task shape (nested assignee/document) from the flat DTOs.
  const tasks: Task[] = taskDtos.map((dto) => ({
    id: dto.id,
    title: dto.title,
    description: dto.description || "",
    assignedTo:
      orgUsers.find((u) => u.id === dto.assigned_to) || {
        id: dto.assigned_to || "",
        name: dto.assigned_to || "Unassigned",
        email: "",
      },
    dueDate: dto.due_date || "",
    status: deriveStatus(dto.status, dto.due_date),
    document: dto.document_id
      ? orgDocuments.find((d) => d.id === dto.document_id) || {
          id: dto.document_id,
          title: dto.document_id,
          type: "",
          createdAt: "",
        }
      : undefined,
    createdAt: dto.created_at,
  }));
  
  const workflows: WorkflowItem[] = [
    {
      id: '1',
      documentId: '1',
      documentTitle: 'Financial Report Q4 2023',
      currentStage: 'Review',
      history: [
        {
          stage: 'Input',
          timestamp: '2023-12-15T10:30:00Z',
          user: users[2],
        },
        {
          stage: 'Draft',
          timestamp: '2023-12-16T11:15:00Z',
          user: users[2],
          comment: 'First draft completed'
        },
        {
          stage: 'Review',
          timestamp: '2023-12-17T09:00:00Z',
          user: users[0],
          comment: 'Ready for review'
        }
      ],
      assignedTo: users[0],
      createdAt: '2023-12-15T10:30:00Z'
    },
    {
      id: '2',
      documentId: '2',
      documentTitle: 'Marketing Strategy 2024',
      currentStage: 'Draft',
      history: [
        {
          stage: 'Input',
          timestamp: '2023-12-16T14:20:00Z',
          user: users[1],
        },
        {
          stage: 'Draft',
          timestamp: '2023-12-17T16:00:00Z',
          user: users[1],
          comment: 'Starting the draft'
        }
      ],
      assignedTo: users[1],
      createdAt: '2023-12-16T14:20:00Z'
    },
    {
      id: '3',
      documentId: '3',
      documentTitle: 'Product Launch Plan',
      currentStage: 'Approval',
      history: [
        {
          stage: 'Input',
          timestamp: '2023-12-18T09:15:00Z',
          user: users[2],
        },
        {
          stage: 'Draft',
          timestamp: '2023-12-18T11:30:00Z',
          user: users[2],
        },
        {
          stage: 'Review',
          timestamp: '2023-12-19T10:00:00Z',
          user: users[0],
          comment: 'Please review the launch timeline'
        },
        {
          stage: 'Comment',
          timestamp: '2023-12-19T14:30:00Z',
          user: users[1],
          comment: 'Timeline looks good, ready for approval'
        },
        {
          stage: 'Approval',
          timestamp: '2023-12-20T09:45:00Z',
          user: users[0],
          comment: 'Final review before approval'
        }
      ],
      assignedTo: users[0],
      createdAt: '2023-12-18T09:15:00Z'
    }
  ];
  
  const comments: Comment[] = [
    {
      id: '1',
      text: 'The financial projections look accurate, but we should clarify the Q3 variance.',
      user: users[0],
      timestamp: '2023-12-18T10:15:00Z'
    },
    {
      id: '2',
      text: "I've adjusted the Q3 variance explanation. Please review the updated section.",
      user: users[2],
      timestamp: '2023-12-18T14:30:00Z'
    },
    {
      id: '3',
      text: 'The executive summary should highlight the cost-saving measures more prominently.',
      user: users[1],
      timestamp: '2023-12-19T09:20:00Z'
    }
  ];
  
  // Task Creation Form
  type TaskFormValues = {
    title: string;
    assignedUserId: string;
    dueDate: Date;
    description: string;
    documentId: string;
  };
  
  const taskForm = useForm<TaskFormValues>({
    defaultValues: {
      title: '',
      assignedUserId: '',
      description: '',
      documentId: ''
    }
  });
  
  const onTaskSubmit = async (values: TaskFormValues) => {
    try {
      setIsSubmitting(true);

      const payload = {
        title: values.title,
        description: values.description || undefined,
        assigned_to: values.assignedUserId || undefined,
        due_date: values.dueDate ? values.dueDate.toISOString() : undefined,
        document_id: values.documentId || undefined,
      };
      if (editingTask) {
        await updateTask(editingTask.id, payload);
      } else {
        await createTask(payload);
      }
      await reloadTasks();

      toast.success(editingTask ? "Task updated successfully" : "Task created successfully");
      taskForm.reset();
      setEditingTask(null);
      setIsNewTaskDialogOpen(false);
    } catch (error) {
      toast.error(editingTask ? "Failed to update task" : "Failed to create task", {
        description: "Please try again later"
      });
    } finally {
      setIsSubmitting(false);
    }
  };
  
  const handleCommentSubmit = async () => {
    if (!newComment.trim() || !selectedWorkflow) return;
    
    try {
      setIsSubmittingComment(true);
      
      // Simulate API call
      await new Promise(resolve => setTimeout(resolve, 1000));
      
      // Mock comment creation
      const comment = {
        id: `comment-${Date.now()}`,
        text: newComment,
        user: users[0], // Current user
        timestamp: new Date().toISOString()
      };
      
      toast.success("Comment added successfully");
      setNewComment("");
      setIsSubmittingComment(false);
    } catch (error) {
      console.error('Error adding comment:', error);
      toast.error("Failed to add comment", {
        description: "Please try again later"
      });
      setIsSubmittingComment(false);
    }
  };
  
  const handleWorkflowAction = async (action: 'approve' | 'reject') => {
    if (!selectedWorkflow) return;
    
    try {
      // Simulate API call
      await new Promise(resolve => setTimeout(resolve, 1000));
      
      // Mock workflow update
      const message = action === 'approve' 
        ? "Document approved successfully"
        : "Document rejected";
      
      toast.success(message);
    } catch (error) {
      toast.error(`Failed to ${action} document`, {
        description: "Please try again later"
      });
    }
  };
  
  const getStatusBadgeColor = (status: TaskStatus) => {
    switch (status) {
      case 'To Do': return 'bg-gray-500';
      case 'In Progress': return 'bg-blue-500';
      case 'Completed': return 'bg-green-500';
      case 'Overdue': return 'bg-red-500';
      default: return 'bg-gray-500';
    }
  };
  
  const getWorkflowStageBadge = (stage: WorkflowStage) => {
    switch (stage) {
      case 'Input': 
        return <Badge variant="outline" className="bg-gray-100">Input</Badge>;
      case 'Draft': 
        return <Badge variant="outline" className="bg-blue-100">Draft</Badge>;
      case 'Review': 
        return <Badge variant="outline" className="bg-yellow-100">Review</Badge>;
      case 'Comment': 
        return <Badge variant="outline" className="bg-purple-100">Comment</Badge>;
      case 'Approval': 
        return <Badge variant="outline" className="bg-green-100">Approval</Badge>;
      default: 
        return <Badge variant="outline">Unknown</Badge>;
    }
  };
  
  const formatDate = (dateString: string) => {
    if (!dateString) return "—";
    const date = new Date(dateString);
    if (isNaN(date.getTime())) return "—";
    return format(date, 'MMM dd, yyyy');
  };
  
  const formatDateTime = (dateString: string) => {
    const date = new Date(dateString);
    return format(date, 'MMM dd, yyyy h:mm a');
  };
  
  const openEditTask = (task: Task) => {
    const dto = taskDtos.find((d) => d.id === task.id) || null;
    setEditingTask(dto);
    taskForm.reset({
      title: task.title,
      assignedUserId: dto?.assigned_to || "",
      description: task.description || "",
      documentId: dto?.document_id || "",
      dueDate: dto?.due_date ? new Date(dto.due_date) : undefined,
    });
    setIsNewTaskDialogOpen(true);
  };

  const handleStatusChange = async (task: Task, backendStatus: string) => {
    try {
      await updateTask(task.id, { status: backendStatus });
      await reloadTasks();
    } catch {
      toast.error("Failed to update status");
    }
  };

  const handleDeleteTask = async (task: Task) => {
    try {
      await deleteTask(task.id);
      await reloadTasks();
      toast.success("Task deleted");
    } catch {
      toast.error("Failed to delete task");
    }
  };

  return (
    <div className="container mx-auto p-6">
      <div className="flex justify-between items-center mb-6">
        <h1 className="text-2xl font-bold">Task Management & Workflow</h1>
        
        <div className="flex gap-2">
          <Dialog
            open={isNewTaskDialogOpen}
            onOpenChange={(open) => {
              setIsNewTaskDialogOpen(open);
              if (!open) setEditingTask(null);
            }}
          >
            <DialogTrigger asChild>
              <Button onClick={() => setEditingTask(null)}>
                <PlusCircle className="mr-2 h-4 w-4" />
                New Task
              </Button>
            </DialogTrigger>
            <DialogContent className="sm:max-w-lg">
              <DialogHeader>
                <DialogTitle>{editingTask ? "Edit Task" : "Create New Task"}</DialogTitle>
                <DialogDescription>
                  {editingTask ? "Update the task details." : "Assign a new task to a team member."}
                </DialogDescription>
              </DialogHeader>
              
              <Form {...taskForm}>
                <form onSubmit={taskForm.handleSubmit(onTaskSubmit)} className="space-y-4">
                  <FormField
                    control={taskForm.control}
                    name="title"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>Task Title</FormLabel>
                        <FormControl>
                          <Input placeholder="Enter task title" {...field} required />
                        </FormControl>
                        <FormMessage />
                      </FormItem>
                    )}
                  />
                  
                  <FormField
                    control={taskForm.control}
                    name="assignedUserId"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>Assigned User</FormLabel>
                        <Select 
                          onValueChange={field.onChange} 
                          value={field.value}
                        >
                          <FormControl>
                            <SelectTrigger>
                              <SelectValue placeholder="Select a user" />
                            </SelectTrigger>
                          </FormControl>
                          <SelectContent>
                            {orgUsers.map(user => (
                              <SelectItem key={user.id} value={user.id}>
                                {user.name}
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                        <FormMessage />
                      </FormItem>
                    )}
                  />
                  
                  <FormField
                    control={taskForm.control}
                    name="dueDate"
                    render={({ field }) => (
                      <FormItem className="flex flex-col">
                        <FormLabel>Due Date</FormLabel>
                        <Popover>
                          <PopoverTrigger asChild>
                            <FormControl>
                              <Button
                                variant="outline"
                                className="w-full pl-3 text-left font-normal flex justify-between"
                              >
                                {field.value ? (
                                  format(field.value, "PPP")
                                ) : (
                                  <span>Pick a date</span>
                                )}
                                <Calendar className="h-4 w-4 opacity-50" />
                              </Button>
                            </FormControl>
                          </PopoverTrigger>
                          <PopoverContent className="w-auto p-0" align="start">
                            <CalendarComponent
                              mode="single"
                              selected={field.value}
                              onSelect={field.onChange}
                              initialFocus
                            />
                          </PopoverContent>
                        </Popover>
                        <FormMessage />
                      </FormItem>
                    )}
                  />
                  
                  <FormField
                    control={taskForm.control}
                    name="description"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>Description/Instructions</FormLabel>
                        <FormControl>
                          <Textarea 
                            placeholder="Enter task description and instructions" 
                            {...field} 
                            rows={3}
                          />
                        </FormControl>
                        <FormMessage />
                      </FormItem>
                    )}
                  />
                  
                  <FormField
                    control={taskForm.control}
                    name="documentId"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>Associated Document</FormLabel>
                        <Select 
                          onValueChange={field.onChange} 
                          value={field.value}
                        >
                          <FormControl>
                            <SelectTrigger>
                              <SelectValue placeholder="Select a document (optional)" />
                            </SelectTrigger>
                          </FormControl>
                          <SelectContent>
                            {orgDocuments.map(doc => (
                              <SelectItem key={doc.id} value={doc.id}>
                                {doc.title}
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                        <FormDescription>
                          Optionally link this task to a document
                        </FormDescription>
                        <FormMessage />
                      </FormItem>
                    )}
                  />
                  
                  <DialogFooter>
                    <Button
                      type="button"
                      variant="outline"
                      onClick={() => setIsNewTaskDialogOpen(false)}
                      disabled={isSubmitting}
                    >
                      Cancel
                    </Button>
                    <Button type="submit" disabled={isSubmitting}>
                      {isSubmitting ? (
                        <>
                          <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                          {editingTask ? "Saving..." : "Creating..."}
                        </>
                      ) : (
                        <>
                          <PlusCircle className="mr-2 h-4 w-4" />
                          {editingTask ? "Save changes" : "Create Task"}
                        </>
                      )}
                    </Button>
                  </DialogFooter>
                </form>
              </Form>
            </DialogContent>
          </Dialog>
          
          <div className="relative">
            <Search className="absolute left-2 top-1/2 transform -translate-y-1/2 h-4 w-4 text-muted-foreground" />
            <Input 
              placeholder="Search tasks..." 
              className="pl-8 w-[250px]"
            />
          </div>
        </div>
      </div>
      
      <Tabs value={activeTab} onValueChange={setActiveTab} className="w-full">
        <TabsList className="grid w-full grid-cols-2">
          <TabsTrigger value="tasks">
            <ListChecks className="mr-2 h-4 w-4" />
            Tasks
          </TabsTrigger>
          <TabsTrigger value="workflows">
            <ClipboardList className="mr-2 h-4 w-4" />
            Workflows
          </TabsTrigger>
        </TabsList>
        
        <TabsContent value="tasks" className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle className="text-lg">Assigned Tasks</CardTitle>
              <CardDescription>View and manage assigned tasks</CardDescription>
            </CardHeader>
            <CardContent>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Task</TableHead>
                    <TableHead>Assigned To</TableHead>
                    <TableHead>Due Date</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead>Document</TableHead>
                    <TableHead className="text-right">Actions</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {tasks.map(task => (
                    <TableRow key={task.id}>
                      <TableCell className="font-medium">{task.title}</TableCell>
                      <TableCell>
                        <div className="flex items-center gap-2">
                          <Avatar className="h-6 w-6">
                            <AvatarImage src={task.assignedTo.avatar} />
                            <AvatarFallback>{task.assignedTo.name.charAt(0)}</AvatarFallback>
                          </Avatar>
                          <span>{task.assignedTo.name}</span>
                        </div>
                      </TableCell>
                      <TableCell>{formatDate(task.dueDate)}</TableCell>
                      <TableCell>
                        <DropdownMenu>
                          <DropdownMenuTrigger asChild>
                            <button type="button" className="cursor-pointer">
                              <Badge className={getStatusBadgeColor(task.status)}>
                                {task.status}
                              </Badge>
                            </button>
                          </DropdownMenuTrigger>
                          <DropdownMenuContent align="start">
                            <DropdownMenuItem onClick={() => handleStatusChange(task, "open")}>
                              To Do
                            </DropdownMenuItem>
                            <DropdownMenuItem onClick={() => handleStatusChange(task, "in_progress")}>
                              In Progress
                            </DropdownMenuItem>
                            <DropdownMenuItem onClick={() => handleStatusChange(task, "done")}>
                              Completed
                            </DropdownMenuItem>
                          </DropdownMenuContent>
                        </DropdownMenu>
                      </TableCell>
                      <TableCell>
                        {task.document ? (
                          <div className="flex items-center gap-1">
                            <FileText className="h-4 w-4 text-muted-foreground" />
                            <span className="text-sm truncate max-w-[150px]">
                              {task.document.title}
                            </span>
                          </div>
                        ) : (
                          <span className="text-sm text-muted-foreground">None</span>
                        )}
                      </TableCell>
                      <TableCell className="text-right">
                        <div className="flex justify-end gap-1">
                          <Button
                            variant="ghost"
                            size="icon"
                            className="h-8 w-8"
                            title="Comments"
                            onClick={() =>
                              setCommentsTask(taskDtos.find((d) => d.id === task.id) || null)
                            }
                          >
                            <MessageSquare className="h-4 w-4" />
                          </Button>
                          <Button
                            variant="ghost"
                            size="icon"
                            className="h-8 w-8"
                            title="Edit"
                            onClick={() => openEditTask(task)}
                          >
                            <Edit className="h-4 w-4" />
                          </Button>
                          <AlertDialog>
                            <AlertDialogTrigger asChild>
                              <Button variant="ghost" size="icon" className="h-8 w-8" title="Delete">
                                <Trash2 className="h-4 w-4 text-destructive" />
                              </Button>
                            </AlertDialogTrigger>
                            <AlertDialogContent>
                              <AlertDialogHeader>
                                <AlertDialogTitle>Delete task?</AlertDialogTitle>
                                <AlertDialogDescription>
                                  This permanently deletes &quot;{task.title}&quot;. This cannot be undone.
                                </AlertDialogDescription>
                              </AlertDialogHeader>
                              <AlertDialogFooter>
                                <AlertDialogCancel>Cancel</AlertDialogCancel>
                                <AlertDialogAction onClick={() => handleDeleteTask(task)}>
                                  Delete
                                </AlertDialogAction>
                              </AlertDialogFooter>
                            </AlertDialogContent>
                          </AlertDialog>
                        </div>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
        </TabsContent>
        
        <TabsContent value="workflows" className="space-y-4">
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <Card className="md:col-span-1">
              <CardHeader>
                <CardTitle className="text-lg">Workflow Items</CardTitle>
                <CardDescription>Documents in approval workflow</CardDescription>
              </CardHeader>
              <CardContent>
                <div className="space-y-2">
                  {workflows.map(workflow => (
                    <div 
                      key={workflow.id} 
                      className={`p-4 border rounded-lg cursor-pointer ${
                        selectedWorkflow?.id === workflow.id ? 'border-primary bg-accent' : ''
                      }`}
                      onClick={() => setSelectedWorkflow(workflow)}
                    >
                      <div className="flex justify-between items-start">
                        <div>
                          <div className="font-medium">{workflow.documentTitle}</div>
                          <div className="text-sm text-muted-foreground">
                            Created {formatDate(workflow.createdAt)}
                          </div>
                        </div>
                        {getWorkflowStageBadge(workflow.currentStage)}
                      </div>
                      
                      <div className="mt-2 text-sm">
                        Assigned to: {workflow.assignedTo.name}
                      </div>
                    </div>
                  ))}
                </div>
              </CardContent>
            </Card>
            
            <Card className="md:col-span-2">
              <CardHeader>
                <CardTitle className="text-lg">
                  {selectedWorkflow ? (
                    <div className="flex justify-between items-center">
                      <div>Workflow Details: {selectedWorkflow.documentTitle}</div>
                      <div>{getWorkflowStageBadge(selectedWorkflow.currentStage)}</div>
                    </div>
                  ) : (
                    'Select a workflow'
                  )}
                </CardTitle>
              </CardHeader>
              
              {selectedWorkflow ? (
                <CardContent>
                  <div className="space-y-6">
                    <div>
                      <h3 className="font-medium mb-2">Workflow Progress</h3>
                      <div className="relative">
                        <div className="flex justify-between mb-2">
                          {['Input', 'Draft', 'Review', 'Comment', 'Approval'].map((stage, index) => (
                            <div 
                              key={stage} 
                              className={`flex flex-col items-center w-1/5 z-10 ${
                                selectedWorkflow.history.some(h => h.stage === stage)
                                  ? 'text-primary'
                                  : 'text-muted-foreground'
                              }`}
                            >
                              <div className={`rounded-full w-4 h-4 ${
                                selectedWorkflow.history.some(h => h.stage === stage)
                                  ? 'bg-primary'
                                  : 'bg-muted'
                              }`} />
                              <span className="text-xs mt-1">{stage}</span>
                            </div>
                          ))}
                        </div>
                        
                        {/* Progress bar */}
                        <div className="absolute top-2 left-0 right-0 h-[2px] bg-muted -z-0">
                          <div 
                            className="h-full bg-primary"
                            style={{ 
                              width: `${
                                ['Input', 'Draft', 'Review', 'Comment', 'Approval']
                                  .indexOf(selectedWorkflow.currentStage) * 25
                              }%` 
                            }}
                          />
                        </div>
                      </div>
                    </div>
                    
                    <div>
                      <h3 className="font-medium mb-2">Workflow History</h3>
                      <div className="space-y-3">
                        {selectedWorkflow.history.map((item, index) => (
                          <div key={index} className="border-l-2 border-primary pl-4 py-2">
                            <div className="flex justify-between">
                              <span className="font-medium">{item.stage}</span>
                              <span className="text-sm text-muted-foreground">
                                {formatDateTime(item.timestamp)}
                              </span>
                            </div>
                            <div className="text-sm">
                              <span className="text-muted-foreground">By: </span>
                              {item.user.name}
                            </div>
                            {item.comment && (
                              <div className="mt-1 text-sm">{item.comment}</div>
                            )}
                          </div>
                        ))}
                      </div>
                    </div>
                    
                    <Separator />
                    
                    <div>
                      <h3 className="font-medium mb-2">Comments</h3>
                      <ScrollArea className="h-[200px]">
                        <div className="space-y-4 pr-4">
                          {comments.map(comment => (
                            <div key={comment.id} className="flex gap-3">
                              <Avatar className="h-8 w-8">
                                <AvatarImage src={comment.user.avatar} />
                                <AvatarFallback>{comment.user.name.charAt(0)}</AvatarFallback>
                              </Avatar>
                              <div className="flex-1">
                                <div className="flex justify-between">
                                  <span className="font-medium">{comment.user.name}</span>
                                  <span className="text-xs text-muted-foreground">
                                    {formatDateTime(comment.timestamp)}
                                  </span>
                                </div>
                                <div className="text-sm mt-1">{comment.text}</div>
                              </div>
                            </div>
                          ))}
                        </div>
                      </ScrollArea>
                      
                      <div className="mt-4 flex gap-2">
                        <Textarea 
                          placeholder="Add a comment..." 
                          value={newComment}
                          onChange={e => setNewComment(e.target.value)}
                          className="min-h-[80px]"
                        />
                        <Button 
                          className="self-end"
                          onClick={handleCommentSubmit}
                          disabled={!newComment.trim() || isSubmittingComment}
                        >
                          {isSubmittingComment ? (
                            <Loader2 className="h-4 w-4 animate-spin" />
                          ) : (
                            <MessageSquare className="h-4 w-4" />
                          )}
                        </Button>
                      </div>
                    </div>
                    
                    {selectedWorkflow.currentStage === 'Approval' && (
                      <div className="flex justify-end gap-2 mt-4">
                        <Button 
                          variant="outline" 
                          onClick={() => handleWorkflowAction('reject')}
                        >
                          <ThumbsDown className="mr-2 h-4 w-4" />
                          Reject
                        </Button>
                        <Button
                          onClick={() => handleWorkflowAction('approve')}
                        >
                          <ThumbsUp className="mr-2 h-4 w-4" />
                          Approve
                        </Button>
                      </div>
                    )}
                  </div>
                </CardContent>
              ) : (
                <CardContent>
                  <div className="flex flex-col items-center justify-center h-[400px] text-muted-foreground">
                    <File className="h-16 w-16 mb-4" />
                    <p>Select a workflow item to view details</p>
                  </div>
                </CardContent>
              )}
            </Card>
          </div>
        </TabsContent>
      </Tabs>

      {/* Per-task comments */}
      <Dialog open={!!commentsTask} onOpenChange={(open) => !open && setCommentsTask(null)}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>Comments</DialogTitle>
            <DialogDescription>{commentsTask?.title}</DialogDescription>
          </DialogHeader>

          <ScrollArea className="max-h-72 pr-3">
            {(commentsTask?.comments?.length ?? 0) === 0 ? (
              <p className="text-sm text-muted-foreground py-4">No comments yet.</p>
            ) : (
              <div className="space-y-3 py-2">
                {commentsTask?.comments?.map((c) => (
                  <div key={c.id} className="rounded-md border p-3">
                    <div className="flex items-center justify-between mb-1">
                      <span className="text-sm font-medium">{c.author_name || "User"}</span>
                      <span className="text-xs text-muted-foreground">
                        {formatDateTime(c.created_at)}
                      </span>
                    </div>
                    <p className="text-sm whitespace-pre-wrap">{c.text}</p>
                  </div>
                ))}
              </div>
            )}
          </ScrollArea>

          <div className="space-y-2">
            <Textarea
              placeholder="Add a comment..."
              value={taskComment}
              onChange={(e) => setTaskComment(e.target.value)}
              rows={3}
            />
            <DialogFooter>
              <Button
                onClick={submitTaskComment}
                disabled={isAddingComment || !taskComment.trim()}
              >
                {isAddingComment ? (
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                ) : (
                  <MessageSquare className="mr-2 h-4 w-4" />
                )}
                Add comment
              </Button>
            </DialogFooter>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
};

export default TasksPage;
