import React, { useEffect, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { ArrowLeft, Save, Eye, Settings2, FileText, Plus, GripVertical } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Switch } from '@/components/ui/switch';
import { Textarea } from '@/components/ui/textarea';
import { Badge } from '@/components/ui/badge';
import { Separator } from '@/components/ui/separator';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { useToast } from '@/hooks/use-toast';
import RichTextEditor from '@/components/letter-template/RichTextEditor';
import { DEFAULT_TEMPLATE_SECTIONS } from '@/components/letter-template/templateDefaults';
import {
  enhancedApi as api,
  type LetterTemplateSection,
  type LetterTemplateStatus,
} from '@/services/enhanced-api';
import { sanitizeHtml } from '@/utils/sanitizeHtml';

interface PlaceholderVariable {
  key: string;
  label: string;
  description: string;
}

const placeholderVariables: PlaceholderVariable[] = [
  { key: '{{addressee_name}}', label: 'Addressee Name', description: 'Name of the recipient' },
  { key: '{{addressee_designation}}', label: 'Addressee Designation', description: 'Job title of the recipient' },
  { key: '{{addressee_organization}}', label: 'Addressee Organization', description: 'Company name of the recipient' },
  { key: '{{addressee_address}}', label: 'Addressee Address', description: 'Full address of the recipient' },
  { key: '{{contract_title}}', label: 'Contract Title', description: 'Full contract name and description' },
  { key: '{{letter_code}}', label: 'Letter Code', description: 'Unique letter reference code' },
  { key: '{{letter_date}}', label: 'Letter Date', description: 'Date of the letter' },
  { key: '{{letter_subject}}', label: 'Letter Subject', description: 'Subject line of the letter' },
  { key: '{{organization_name}}', label: 'Organization Name', description: 'Sending organization name' },
  { key: '{{signatory_name}}', label: 'Signatory Name', description: 'Name of the person signing' },
  { key: '{{signatory_designation}}', label: 'Signatory Designation', description: 'Title of the person signing' },
];

const categories = [
  'Contract Management',
  'Notices',
  'Payments',
  'Claims',
  'Variations',
  'Instructions',
  'General',
];

const LetterTemplateEditorPage = () => {
  const { id } = useParams();
  const navigate = useNavigate();
  const { toast } = useToast();
  const isNew = id === 'new';

  const [templateName, setTemplateName] = useState('');
  const [templateCode, setTemplateCode] = useState('');
  const [templateCategory, setTemplateCategory] = useState('');
  const [templateDescription, setTemplateDescription] = useState('');
  const [templateStatus, setTemplateStatus] =
    useState<LetterTemplateStatus>('draft');
  const [sections, setSections] = useState<LetterTemplateSection[]>(
    DEFAULT_TEMPLATE_SECTIONS
  );
  const [activeSection, setActiveSection] = useState<string>('body');
  const [isPreviewOpen, setIsPreviewOpen] = useState(false);
  const [isLoading, setIsLoading] = useState(!isNew);
  const [isSaving, setIsSaving] = useState(false);

  const normalizeSections = (value?: LetterTemplateSection[]) => {
    if (!value || value.length === 0) {
      return DEFAULT_TEMPLATE_SECTIONS.map((section) => ({ ...section }));
    }
    return [...value]
      .map((section, index) => ({
        ...section,
        order:
          typeof section.order === 'number' && section.order >= 0
            ? section.order
            : index,
      }))
      .sort((a, b) => a.order - b.order);
  };

  useEffect(() => {
    if (!id) {
      return;
    }

    if (isNew) {
      setSections(normalizeSections());
      setActiveSection('body');
      setTemplateName('');
      setTemplateCode('');
      setTemplateCategory('');
      setTemplateDescription('');
      setTemplateStatus('draft');
      setIsLoading(false);
      return;
    }

    const loadTemplate = async () => {
      setIsLoading(true);
      try {
        const template = await api.getLetterTemplate(id);
        const normalizedSections = normalizeSections(template.sections);
        setTemplateName(template.name || '');
        setTemplateCode(template.code || '');
        setTemplateCategory(template.category || '');
        setTemplateDescription(template.description || '');
        setTemplateStatus(template.status || 'draft');
        setSections(normalizedSections);
        const firstEnabled =
          normalizedSections.find((section) => section.enabled)?.id ||
          normalizedSections[0]?.id ||
          'body';
        setActiveSection(firstEnabled);
      } catch (error) {
        const message =
          error instanceof Error ? error.message : 'Failed to load template';
        toast({
          title: 'Unable to load template',
          description: message,
          variant: 'destructive',
        });
      } finally {
        setIsLoading(false);
      }
    };

    loadTemplate();
  }, [id, isNew, toast]);

  const handleSectionToggle = (sectionId: string) => {
    setSections(
      sections.map((s) =>
        s.id === sectionId ? { ...s, enabled: !s.enabled } : s
      )
    );
  };

  const handleSectionContentChange = (sectionId: string, content: string) => {
    setSections(
      sections.map((s) =>
        s.id === sectionId ? { ...s, content } : s
      )
    );
  };

  const insertPlaceholder = (placeholder: string) => {
    const activeEditor = sections.find((s) => s.id === activeSection);
    if (activeEditor) {
      const newContent = activeEditor.content + placeholder;
      handleSectionContentChange(activeSection, newContent);
    }
  };

  const handleSave = async () => {
    if (!templateName.trim() || !templateCode.trim() || !templateCategory.trim()) {
      toast({
        title: 'Missing required fields',
        description: 'Name, code, and category are required.',
        variant: 'destructive',
      });
      return;
    }

    const payload = {
      name: templateName.trim(),
      code: templateCode.trim(),
      category: templateCategory.trim(),
      description: templateDescription.trim() || undefined,
      status: templateStatus,
      sections: normalizeSections(sections),
    };

    setIsSaving(true);
    try {
      if (isNew) {
        await api.createLetterTemplate(payload);
      } else if (id) {
        await api.updateLetterTemplate(id, payload);
      }
      toast({
        title: 'Template Saved',
        description: `"${templateName}" has been saved successfully.`,
      });
      navigate('/letter-templates');
    } catch (error) {
      const message =
        error instanceof Error ? error.message : 'Failed to save template';
      toast({
        title: 'Save failed',
        description: message,
        variant: 'destructive',
      });
    } finally {
      setIsSaving(false);
    }
  };

  const getPreviewContent = () => {
    const html = normalizeSections(sections)
      .filter((s) => s.enabled)
      .map((s) => s.content)
      .join('<hr class="my-4"/>');
    return sanitizeHtml(html);
  };

  const orderedSections = normalizeSections(sections);

  if (isLoading) {
    return (
      <div className="p-6 text-sm text-muted-foreground">
        Loading template...
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-4">
          <Button variant="ghost" size="icon" onClick={() => navigate('/letter-templates')}>
            <ArrowLeft className="h-5 w-5" />
          </Button>
          <div>
            <h1 className="text-2xl font-bold">
              {isNew ? 'Create Letter Template' : 'Edit Letter Template'}
            </h1>
            <p className="text-muted-foreground">
              Customize template sections and formatting
            </p>
          </div>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" onClick={() => setIsPreviewOpen(true)}>
            <Eye className="mr-2 h-4 w-4" />
            Preview
          </Button>
          <Button
            onClick={handleSave}
            className="bg-docsumo-blue hover:bg-docsumo-blue/90"
            disabled={isSaving}
          >
            <Save className="mr-2 h-4 w-4" />
            {isSaving ? 'Saving...' : 'Save Template'}
          </Button>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-4 gap-6">
        {/* Left Panel - Settings & Variables */}
        <div className="space-y-6">
          {/* Template Settings */}
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-base flex items-center gap-2">
                <Settings2 className="h-4 w-4" />
                Template Settings
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="space-y-2">
                <Label htmlFor="name">Template Name</Label>
                <Input
                  id="name"
                  value={templateName}
                  onChange={(e) => setTemplateName(e.target.value)}
                  placeholder="Enter template name"
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="code">Template Code</Label>
                <Input
                  id="code"
                  value={templateCode}
                  onChange={(e) => setTemplateCode(e.target.value)}
                  placeholder="e.g., LET-JVTI-CPM"
                />
              </div>
              <div className="space-y-2">
                <Label>Category</Label>
                <Select value={templateCategory} onValueChange={setTemplateCategory}>
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
                  value={templateDescription}
                  onChange={(e) => setTemplateDescription(e.target.value)}
                  placeholder="Brief description"
                  rows={3}
                />
              </div>
            </CardContent>
          </Card>

          {/* Placeholder Variables */}
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-base flex items-center gap-2">
                <Plus className="h-4 w-4" />
                Insert Placeholders
              </CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-xs text-muted-foreground mb-3">
                Click to insert dynamic placeholders into the active section
              </p>
              <div className="space-y-2 max-h-[300px] overflow-y-auto">
                {placeholderVariables.map((variable) => (
                  <button
                    key={variable.key}
                    onClick={() => insertPlaceholder(variable.key)}
                    className="w-full text-left p-2 rounded border hover:bg-muted/50 transition-colors"
                  >
                    <div className="font-mono text-xs text-docsumo-blue">
                      {variable.key}
                    </div>
                    <div className="text-xs text-muted-foreground">
                      {variable.label}
                    </div>
                  </button>
                ))}
              </div>
            </CardContent>
          </Card>
        </div>

        {/* Center Panel - Editor */}
        <div className="lg:col-span-2">
          <Card className="h-full">
            <CardHeader className="pb-3">
              <CardTitle className="text-base flex items-center gap-2">
                <FileText className="h-4 w-4" />
                Template Content Editor
              </CardTitle>
            </CardHeader>
            <CardContent>
              <Tabs value={activeSection} onValueChange={setActiveSection}>
                <TabsList className="w-full flex-wrap h-auto gap-1 mb-4">
                  {orderedSections.map((section) => (
                    <TabsTrigger
                      key={section.id}
                      value={section.id}
                      disabled={!section.enabled}
                      className="text-xs"
                    >
                      {section.name}
                    </TabsTrigger>
                  ))}
                </TabsList>

                {orderedSections.map((section) => (
                  <TabsContent key={section.id} value={section.id}>
                    <RichTextEditor
                      content={section.content}
                      onChange={(content) => handleSectionContentChange(section.id, content)}
                      placeholder={`Enter ${section.name.toLowerCase()} content...`}
                    />
                  </TabsContent>
                ))}
              </Tabs>
            </CardContent>
          </Card>
        </div>

        {/* Right Panel - Sections */}
        <div>
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-base">Template Sections</CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-xs text-muted-foreground mb-4">
                Enable or disable sections in the template
              </p>
              <div className="space-y-3">
                {orderedSections.map((section) => (
                  <div
                    key={section.id}
                    className="flex items-center justify-between p-2 rounded border"
                  >
                    <div className="flex items-center gap-2">
                      <GripVertical className="h-4 w-4 text-muted-foreground cursor-grab" />
                      <span className="text-sm">{section.name}</span>
                    </div>
                    <Switch
                      checked={section.enabled}
                      onCheckedChange={() => handleSectionToggle(section.id)}
                    />
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>
        </div>
      </div>

      {/* Preview Dialog */}
      <Dialog open={isPreviewOpen} onOpenChange={setIsPreviewOpen}>
        <DialogContent className="max-w-3xl max-h-[80vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>Template Preview</DialogTitle>
          </DialogHeader>
          <div className="border rounded-lg p-8 bg-white">
            <div className="flex justify-between items-start mb-4">
              <Badge variant="outline">{templateCode || 'NO-CODE'}</Badge>
              <Badge>{templateCategory || 'Uncategorized'}</Badge>
            </div>
            <Separator className="my-4" />
            <div
              className="prose prose-sm max-w-none"
              dangerouslySetInnerHTML={{ __html: getPreviewContent() }}
            />
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
};

export default LetterTemplateEditorPage;
