import React, { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Plus, Search, Edit, Trash2, Copy, Eye, FileText, MoreVertical } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { useToast } from '@/hooks/use-toast';
import {
  enhancedApi as api,
  type LetterTemplate,
  type LetterTemplateSection,
} from '@/services/enhanced-api';
import { DEFAULT_TEMPLATE_SECTIONS } from '@/components/letter-template/templateDefaults';

const categories = [
  'Contract Management',
  'Notices',
  'Payments',
  'Claims',
  'Variations',
  'Instructions',
  'General',
];

const LetterTemplatePage = () => {
  const navigate = useNavigate();
  const { toast } = useToast();
  const [templates, setTemplates] = useState<LetterTemplate[]>([]);
  const [searchTerm, setSearchTerm] = useState('');
  const [selectedCategory, setSelectedCategory] = useState<string>('all');
  const [isCreateDialogOpen, setIsCreateDialogOpen] = useState(false);
  const [isPreviewDialogOpen, setIsPreviewDialogOpen] = useState(false);
  const [selectedTemplate, setSelectedTemplate] = useState<LetterTemplate | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [newTemplate, setNewTemplate] = useState({
    name: '',
    code: '',
    category: '',
    description: '',
  });

  useEffect(() => {
    const loadTemplates = async () => {
      setIsLoading(true);
      try {
        const data = await api.getLetterTemplates();
        setTemplates(data);
      } catch (error) {
        const message =
          error instanceof Error ? error.message : 'Failed to load templates';
        toast({
          title: 'Unable to load templates',
          description: message,
          variant: 'destructive',
        });
      } finally {
        setIsLoading(false);
      }
    };

    loadTemplates();
  }, [toast]);

  const filteredTemplates = templates.filter((template) => {
    const matchesSearch =
      template.name.toLowerCase().includes(searchTerm.toLowerCase()) ||
      template.code.toLowerCase().includes(searchTerm.toLowerCase());
    const matchesCategory =
      selectedCategory === 'all' || template.category === selectedCategory;
    return matchesSearch && matchesCategory;
  });

  const formatDate = (value?: string) => {
    if (!value) {
      return '—';
    }
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) {
      return value;
    }
    return parsed.toLocaleDateString();
  };

  const buildPreviewContent = (sections: LetterTemplateSection[]) => {
    return [...sections]
      .sort((a, b) => (a.order ?? 0) - (b.order ?? 0))
      .filter((section) => section.enabled)
      .map((section) => section.content)
      .join('<hr class="my-4"/>');
  };

  const getStatusColor = (status: string) => {
    switch (status) {
      case 'active':
        return 'bg-green-100 text-green-800';
      case 'draft':
        return 'bg-yellow-100 text-yellow-800';
      case 'archived':
        return 'bg-gray-100 text-gray-800';
      default:
        return 'bg-gray-100 text-gray-800';
    }
  };

  const handleCreateTemplate = async () => {
    if (!newTemplate.name.trim() || !newTemplate.code.trim() || !newTemplate.category.trim()) {
      toast({
        title: 'Missing required fields',
        description: 'Name, code, and category are required.',
        variant: 'destructive',
      });
      return;
    }

    setIsSubmitting(true);
    try {
      const created = await api.createLetterTemplate({
        name: newTemplate.name.trim(),
        code: newTemplate.code.trim(),
        category: newTemplate.category.trim(),
        description: newTemplate.description.trim() || undefined,
        status: 'draft',
        sections: DEFAULT_TEMPLATE_SECTIONS.map((section) => ({ ...section })),
      });
      setTemplates((prev) => [created, ...prev]);
      setNewTemplate({ name: '', code: '', category: '', description: '' });
      setIsCreateDialogOpen(false);
      navigate(`/letter-templates/${created._id}/edit`);
    } catch (error) {
      const message =
        error instanceof Error ? error.message : 'Failed to create template';
      toast({
        title: 'Create failed',
        description: message,
        variant: 'destructive',
      });
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleDeleteTemplate = async (id: string) => {
    try {
      await api.deleteLetterTemplate(id);
      setTemplates((prev) => prev.filter((t) => t._id !== id));
      toast({
        title: 'Template deleted',
        description: 'The template was deleted successfully.',
      });
    } catch (error) {
      const message =
        error instanceof Error ? error.message : 'Failed to delete template';
      toast({
        title: 'Delete failed',
        description: message,
        variant: 'destructive',
      });
    }
  };

  const handleDuplicateTemplate = async (template: LetterTemplate) => {
    setIsSubmitting(true);
    try {
      const created = await api.createLetterTemplate({
        name: `${template.name} (Copy)`,
        code: `${template.code}-COPY`,
        category: template.category,
        description: template.description,
        status: 'draft',
        sections: (template.sections || []).map((section) => ({ ...section })),
      });
      setTemplates((prev) => [created, ...prev]);
      toast({
        title: 'Template duplicated',
        description: `"${template.name}" was duplicated successfully.`,
      });
    } catch (error) {
      const message =
        error instanceof Error ? error.message : 'Failed to duplicate template';
      toast({
        title: 'Duplicate failed',
        description: message,
        variant: 'destructive',
      });
    } finally {
      setIsSubmitting(false);
    }
  };

  if (isLoading) {
    return (
      <div className="p-6 text-sm text-muted-foreground">
        Loading templates...
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4">
        <div>
          <h1 className="text-2xl font-bold text-foreground">Letter Templates</h1>
          <p className="text-muted-foreground mt-1">
            Manage standardized letter templates for project correspondence
          </p>
        </div>
        <Dialog open={isCreateDialogOpen} onOpenChange={setIsCreateDialogOpen}>
          <DialogTrigger asChild>
            <Button 
              className="bg-docsumo-blue hover:bg-docsumo-blue/90"
            >
              <Plus className="mr-2 h-4 w-4" />
              Create Template
            </Button>
          </DialogTrigger>
          <DialogContent className="sm:max-w-[500px]">
            <DialogHeader>
              <DialogTitle>Create New Letter Template</DialogTitle>
              <DialogDescription>
                Define a new letter template with standard sections and formatting.
              </DialogDescription>
            </DialogHeader>
            <div className="space-y-4 py-4">
              <div className="space-y-2">
                <Label htmlFor="name">Template Name</Label>
                <Input
                  id="name"
                  placeholder="e.g., Contractor Request Response"
                  value={newTemplate.name}
                  onChange={(e) =>
                    setNewTemplate({ ...newTemplate, name: e.target.value })
                  }
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="code">Template Code</Label>
                <Input
                  id="code"
                  placeholder="e.g., LET-JVTI-CPM"
                  value={newTemplate.code}
                  onChange={(e) =>
                    setNewTemplate({ ...newTemplate, code: e.target.value })
                  }
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="category">Category</Label>
                <Select
                  value={newTemplate.category}
                  onValueChange={(value) =>
                    setNewTemplate({ ...newTemplate, category: value })
                  }
                >
                  <SelectTrigger>
                    <SelectValue placeholder="Select category" />
                  </SelectTrigger>
                  <SelectContent>
                    {categories.map((cat) => (
                      <SelectItem key={cat} value={cat}>
                        {cat}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-2">
                <Label htmlFor="description">Description</Label>
                <Textarea
                  id="description"
                  placeholder="Brief description of the template purpose"
                  value={newTemplate.description}
                  onChange={(e) =>
                    setNewTemplate({ ...newTemplate, description: e.target.value })
                  }
                />
              </div>
            </div>
            <div className="flex justify-end gap-2">
              <Button variant="outline" onClick={() => setIsCreateDialogOpen(false)}>
                Cancel
              </Button>
              <Button
                onClick={handleCreateTemplate}
                className="bg-docsumo-blue hover:bg-docsumo-blue/90"
                disabled={isSubmitting}
              >
                {isSubmitting ? 'Creating...' : 'Create Template'}
              </Button>
            </div>
          </DialogContent>
        </Dialog>
      </div>

      {/* Stats Cards */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium text-muted-foreground">
              Total Templates
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">{templates.length}</div>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium text-muted-foreground">
              Active Templates
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold text-green-600">
              {templates.filter((t) => t.status === 'active').length}
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium text-muted-foreground">
              Draft Templates
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold text-yellow-600">
              {templates.filter((t) => t.status === 'draft').length}
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium text-muted-foreground">
              Total Usage
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold text-docsumo-blue">
              {templates.reduce((sum, t) => sum + (t.usage_count ?? 0), 0)}
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Filters */}
      <div className="flex flex-col sm:flex-row gap-4">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
          <Input
            placeholder="Search templates by name or code..."
            className="pl-10"
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
          />
        </div>
        <Select value={selectedCategory} onValueChange={setSelectedCategory}>
          <SelectTrigger className="w-full sm:w-[200px]">
            <SelectValue placeholder="All Categories" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All Categories</SelectItem>
            {categories.map((cat) => (
              <SelectItem key={cat} value={cat}>
                {cat}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {/* Templates Table */}
      <Card>
        <CardContent className="p-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Template Name</TableHead>
                <TableHead>Code</TableHead>
                <TableHead>Category</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Usage</TableHead>
                <TableHead>Last Updated</TableHead>
                <TableHead className="text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {filteredTemplates.length === 0 ? (
                <TableRow>
                  <TableCell
                    colSpan={7}
                    className="text-center text-sm text-muted-foreground"
                  >
                    No templates found.
                  </TableCell>
                </TableRow>
              ) : (
                filteredTemplates.map((template) => (
                  <TableRow key={template._id}>
                    <TableCell>
                      <div className="flex items-center gap-2">
                        <FileText className="h-4 w-4 text-muted-foreground" />
                        <div>
                          <div className="font-medium">{template.name}</div>
                          <div className="text-xs text-muted-foreground line-clamp-1">
                            {template.description || '—'}
                          </div>
                        </div>
                      </div>
                    </TableCell>
                    <TableCell>
                      <code className="text-xs bg-muted px-2 py-1 rounded">
                        {template.code}
                      </code>
                    </TableCell>
                    <TableCell>{template.category}</TableCell>
                    <TableCell>
                      <Badge className={getStatusColor(template.status)}>
                        {template.status}
                      </Badge>
                    </TableCell>
                    <TableCell>{template.usage_count ?? 0} letters</TableCell>
                    <TableCell>{formatDate(template.updated_at)}</TableCell>
                    <TableCell className="text-right">
                      <DropdownMenu>
                        <DropdownMenuTrigger asChild>
                          <Button variant="ghost" size="icon">
                            <MoreVertical className="h-4 w-4" />
                          </Button>
                        </DropdownMenuTrigger>
                        <DropdownMenuContent align="end">
                          <DropdownMenuItem
                            onClick={() => {
                              setSelectedTemplate(template);
                              setIsPreviewDialogOpen(true);
                            }}
                          >
                            <Eye className="mr-2 h-4 w-4" />
                            Preview
                          </DropdownMenuItem>
                          <DropdownMenuItem
                            onClick={() =>
                              navigate(`/letter-templates/${template._id}/edit`)
                            }
                          >
                            <Edit className="mr-2 h-4 w-4" />
                            Edit
                          </DropdownMenuItem>
                          <DropdownMenuItem
                            onClick={() => handleDuplicateTemplate(template)}
                            disabled={isSubmitting}
                          >
                            <Copy className="mr-2 h-4 w-4" />
                            Duplicate
                          </DropdownMenuItem>
                          <DropdownMenuItem
                            className="text-red-600"
                            onClick={() => handleDeleteTemplate(template._id)}
                          >
                            <Trash2 className="mr-2 h-4 w-4" />
                            Delete
                          </DropdownMenuItem>
                        </DropdownMenuContent>
                      </DropdownMenu>
                    </TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      {/* Preview Dialog */}
      <Dialog open={isPreviewDialogOpen} onOpenChange={setIsPreviewDialogOpen}>
        <DialogContent className="sm:max-w-[700px] max-h-[80vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>Template Preview</DialogTitle>
            <DialogDescription>
              {selectedTemplate?.name} ({selectedTemplate?.code})
            </DialogDescription>
          </DialogHeader>
          {selectedTemplate && (
            <div className="border rounded-lg p-6 bg-white space-y-4">
              <div className="flex items-center justify-between">
                <Badge variant="outline">
                  {selectedTemplate.code || 'NO-CODE'}
                </Badge>
                <Badge>{selectedTemplate.category || 'Uncategorized'}</Badge>
              </div>
              {buildPreviewContent(selectedTemplate.sections || []) ? (
                <div
                  className="prose prose-sm max-w-none"
                  dangerouslySetInnerHTML={{
                    __html: buildPreviewContent(selectedTemplate.sections || []),
                  }}
                />
              ) : (
                <p className="text-sm text-muted-foreground">
                  No sections available for preview.
                </p>
              )}
            </div>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
};

export default LetterTemplatePage;
