
import React, { useState, useEffect, useCallback } from 'react';
import { Link } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
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
  ExternalLink,
  FileText,
  ListChecks,
  Loader2,
  MessageSquare,
  PlusCircle,
  RefreshCw,
  Search,
  Trash2,
  UserCheck,
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
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { format } from "date-fns";
import { Calendar as CalendarComponent } from "@/components/ui/calendar";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import {
  getTasks,
  getTaskBoard,
  createTask,
  updateTask,
  deleteTask,
  addTaskComment,
  type TaskDTO,
  type TaskBoard,
} from "@/services/tasks-api";
import { getCurrentUserProfile } from "@/services/session-api";
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

// Map backend task status (open/in_progress/done) + due date to the UI status.
const deriveStatus = (status?: string | null, dueDate?: string | null): TaskStatus => {
  if (status === "done") return "Completed";
  if (status === "in_progress") return "In Progress";
  if (dueDate && new Date(dueDate).getTime() < Date.now()) return "Overdue";
  return "To Do";
};

// Assignment board columns, in lifecycle order.
const BOARD_COLUMNS = [
  { key: "draft", label: "Draft" },
  { key: "review", label: "Review" },
  { key: "approve", label: "Approve" },
] as const;

const TasksPage = () => {
  const [activeTab, setActiveTab] = useState<string>("tasks");
  const [isNewTaskDialogOpen, setIsNewTaskDialogOpen] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);

  // Assignment board (GET /api/tasks/board), grouped by lifecycle stage.
  const [board, setBoard] = useState<TaskBoard | null>(null);
  const [boardMine, setBoardMine] = useState(false);

  // Real tasks loaded from the backend (GET /api/tasks, tenant-scoped).
  const [taskDtos, setTaskDtos] = useState<TaskDTO[]>([]);
  const [taskFilter, setTaskFilter] = useState<"all" | "mine" | "overdue">("all");
  const [myUserId, setMyUserId] = useState<string>("");

  useEffect(() => {
    getCurrentUserProfile()
      .then((p) => setMyUserId(p?.id || ""))
      .catch(() => {});
  }, []);

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

  const reloadBoard = useCallback(async () => {
    try {
      setBoard(await getTaskBoard(boardMine && myUserId ? { assigned_to: myUserId } : undefined));
    } catch {
      /* board stays as-is on error */
    }
  }, [boardMine, myUserId]);

  useEffect(() => {
    if (activeTab === "board") void reloadBoard();
  }, [activeTab, reloadBoard]);

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

  const isOverdue = (t: Task) =>
    !!t.dueDate && new Date(t.dueDate) < new Date() && t.status !== "Completed";
  const visibleTasks = tasks.filter((t) => {
    if (taskFilter === "mine") return myUserId && t.assignedTo.id === myUserId;
    if (taskFilter === "overdue") return isOverdue(t);
    return true;
  });

  // ---- Board helpers ----
  const assigneeName = (id?: string | null): string => {
    if (!id) return "Unassigned";
    return orgUsers.find((u) => u.id === id)?.name || id;
  };

  const artifactBadge = (t: TaskDTO) => {
    if (t.resource_type === "letter")
      return <Badge variant="outline" className="bg-blue-50 text-blue-700">Letter</Badge>;
    if (t.resource_type === "arbitration_draft")
      return <Badge variant="outline" className="bg-purple-50 text-purple-700">Arbitration</Badge>;
    return null;
  };

  const taskLink = (t: TaskDTO): string | null => {
    if (t.resource_type === "letter" && t.resource_id) {
      const stage = (t.workflow_stage || "").toLowerCase();
      if (stage === "review") return `/letters/${t.resource_id}/review`;
      if (stage === "approval") return `/letters/${t.resource_id}/approval`;
      return `/letters/${t.resource_id}/draft`;
    }
    return null;
  };

  const reassignTask = async (taskId: string, userId: string) => {
    try {
      await updateTask(taskId, { assigned_to: userId });
      await reloadBoard();
      toast.success("Task reassigned");
    } catch {
      toast.error("Failed to reassign task");
    }
  };

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

  const getStatusBadgeColor = (status: TaskStatus) => {
    switch (status) {
      case 'To Do': return 'bg-gray-500';
      case 'In Progress': return 'bg-blue-500';
      case 'Completed': return 'bg-green-500';
      case 'Overdue': return 'bg-red-500';
      default: return 'bg-gray-500';
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
        <h1 className="text-2xl font-bold">Task Management &amp; Workflow</h1>

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
          <TabsTrigger value="board">
            <ClipboardList className="mr-2 h-4 w-4" />
            Assignment Board
          </TabsTrigger>
        </TabsList>

        <TabsContent value="tasks" className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle className="text-lg">Assigned Tasks</CardTitle>
              <CardDescription>View and manage assigned tasks</CardDescription>
              <div className="flex gap-2 pt-2">
                {([
                  { key: "all", label: "All" },
                  { key: "mine", label: "My Tasks" },
                  { key: "overdue", label: "Overdue" },
                ] as const).map((f) => (
                  <Button
                    key={f.key}
                    size="sm"
                    variant={taskFilter === f.key ? "default" : "outline"}
                    onClick={() => setTaskFilter(f.key)}
                  >
                    {f.label}
                  </Button>
                ))}
              </div>
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
                  {visibleTasks.map(task => {
                    const dto = taskDtos.find((d) => d.id === task.id);
                    return (
                    <TableRow key={task.id}>
                      <TableCell className="font-medium">
                        <div className="flex items-center gap-2">
                          <span>{task.title}</span>
                          {dto?.linked_claim_id && (
                            <Link
                              to={`/claims/${dto.linked_claim_id}`}
                              className="text-xs text-blue-600 hover:underline"
                              title="Linked claim"
                            >
                              claim
                            </Link>
                          )}
                          {dto?.resource_type === "letter" && dto?.resource_id && (
                            <Link
                              to={`/letters/${dto.resource_id}/draft`}
                              className="text-xs text-blue-600 hover:underline"
                              title="Linked letter"
                            >
                              letter
                            </Link>
                          )}
                        </div>
                      </TableCell>
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
                    );
                  })}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="board" className="space-y-4">
          <Card>
            <CardHeader>
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <CardTitle className="text-lg">Assignment Board</CardTitle>
                  <CardDescription>
                    Who is drafting, reviewing, and approving — across letters and arbitration pleadings.
                  </CardDescription>
                </div>
                <div className="flex gap-2">
                  <Button
                    size="sm"
                    variant={boardMine ? "default" : "outline"}
                    onClick={() => setBoardMine((v) => !v)}
                  >
                    <UserCheck className="mr-2 h-4 w-4" />
                    My assignments
                  </Button>
                  <Button size="sm" variant="outline" onClick={reloadBoard}>
                    <RefreshCw className="mr-2 h-4 w-4" />
                    Refresh
                  </Button>
                </div>
              </div>
            </CardHeader>
            <CardContent>
              <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
                {BOARD_COLUMNS.map((col) => {
                  const items = board?.columns?.[col.key] ?? [];
                  return (
                    <div key={col.key} className="rounded-lg border bg-muted/30">
                      <div className="flex items-center justify-between border-b px-3 py-2">
                        <span className="font-medium">{col.label}</span>
                        <Badge variant="secondary">{items.length}</Badge>
                      </div>
                      <div className="min-h-[140px] space-y-2 p-2">
                        {items.length === 0 ? (
                          <p className="px-1 py-6 text-center text-xs text-muted-foreground">
                            No {col.label.toLowerCase()} tasks.
                          </p>
                        ) : (
                          items.map((t) => {
                            const link = taskLink(t);
                            const uiStatus = deriveStatus(t.status, t.due_date);
                            return (
                              <div key={t.id} className="rounded-md border bg-background p-3 shadow-sm">
                                <div className="flex items-start justify-between gap-2">
                                  <div className="text-sm font-medium leading-tight">{t.title}</div>
                                  {artifactBadge(t)}
                                </div>
                                <div className="mt-2 flex items-center gap-2 text-xs text-muted-foreground">
                                  <Avatar className="h-5 w-5">
                                    <AvatarFallback className="text-[10px]">
                                      {assigneeName(t.assigned_to).charAt(0).toUpperCase()}
                                    </AvatarFallback>
                                  </Avatar>
                                  <span>{assigneeName(t.assigned_to)}</span>
                                </div>
                                <div className="mt-2 flex items-center justify-between">
                                  <Badge className={getStatusBadgeColor(uiStatus)}>{uiStatus}</Badge>
                                  {link && (
                                    <Link
                                      to={link}
                                      className="inline-flex items-center gap-1 text-xs text-blue-600 hover:underline"
                                    >
                                      Open <ExternalLink className="h-3 w-3" />
                                    </Link>
                                  )}
                                </div>
                                {t.due_date && (
                                  <div className="mt-1 text-xs text-muted-foreground">
                                    Due {formatDate(t.due_date)}
                                  </div>
                                )}
                                <div className="mt-2">
                                  <Select
                                    value={t.assigned_to || ""}
                                    onValueChange={(uid) => reassignTask(t.id, uid)}
                                  >
                                    <SelectTrigger className="h-7 text-xs">
                                      <SelectValue placeholder="Assign…" />
                                    </SelectTrigger>
                                    <SelectContent>
                                      {orgUsers.map((u) => (
                                        <SelectItem key={u.id} value={u.id}>
                                          {u.name}
                                        </SelectItem>
                                      ))}
                                    </SelectContent>
                                  </Select>
                                </div>
                              </div>
                            );
                          })
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            </CardContent>
          </Card>
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
