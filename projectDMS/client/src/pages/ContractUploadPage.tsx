import React, { useState } from 'react';
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import { useForm } from "react-hook-form";
import { Loader2, Upload, FileText, Building, FolderOpen } from 'lucide-react';
import { toast } from "sonner";
import { Organization, Project } from '@/components/letter-workflow/types';

type ContractUploadValues = {
  title: string;
  description: string;
  contractType: string;
  organizationId: string;
  projectId: string;
  file: FileList;
};

// Mock data for organizations and projects
const mockOrganizations: Organization[] = [
  { id: '1', name: 'Tech Corp', description: 'Technology company' },
  { id: '2', name: 'Healthcare Inc', description: 'Healthcare organization' },
  { id: '3', name: 'Finance Ltd', description: 'Financial services' },
];

const mockProjects: Project[] = [
  { id: '1', name: 'Project Alpha', organizationId: '1' },
  { id: '2', name: 'Project Beta', organizationId: '1' },
  { id: '3', name: 'Medical System', organizationId: '2' },
  { id: '4', name: 'Payment Gateway', organizationId: '3' },
];

const contractTypes = [
  'Service Agreement',
  'Non-Disclosure Agreement',
  'Employment Contract',
  'Vendor Agreement',
  'Partnership Agreement',
  'Licensing Agreement',
  'Lease Agreement',
  'Other'
];

const ContractUploadPage: React.FC = () => {
  const [uploading, setUploading] = useState(false);
  const [selectedOrgId, setSelectedOrgId] = useState<string>('');
  const { register, handleSubmit, reset, setValue, watch } = useForm<ContractUploadValues>();
  
  const selectedOrganizationId = watch('organizationId');
  const filteredProjects = mockProjects.filter(
    project => project.organizationId === selectedOrganizationId
  );

  const handleUploadContract = async (data: ContractUploadValues) => {
    try {
      if (!data.file || data.file.length === 0) {
        toast.error("Please select a contract file to upload");
        return;
      }
      
      if (!data.organizationId || !data.projectId) {
        toast.error("Please select both organization and project");
        return;
      }
      
      setUploading(true);
      
      // Simulate API call
      await new Promise(resolve => setTimeout(resolve, 2000));
      
      const files = Array.from(data.file);
      const selectedOrg = mockOrganizations.find(org => org.id === data.organizationId);
      const selectedProject = mockProjects.find(proj => proj.id === data.projectId);
      
      toast.success("Contracts uploaded successfully", {
        description: `${files.length} file(s) uploaded to ${selectedProject?.name}`
      });
      
      // Reset form
      reset();
      setSelectedOrgId('');
    } catch (error) {
      console.error('Error uploading contract:', error);
      toast.error("Failed to upload contract", {
        description: "Please try again later"
      });
    } finally {
      setUploading(false);
    }
  };

  const handleOrganizationChange = (orgId: string) => {
    setSelectedOrgId(orgId);
    setValue('organizationId', orgId);
    setValue('projectId', ''); // Reset project selection
  };

  return (
    <div className="container mx-auto py-6 px-4">
      <div className="max-w-2xl mx-auto">
        <div className="mb-6">
          <h1 className="text-2xl font-bold text-foreground flex items-center gap-2">
            <FileText className="h-6 w-6" />
            Upload Contract Document
          </h1>
          <p className="text-muted-foreground mt-2">
            Upload and organize contract documents by organization and project
          </p>
        </div>

        <Card>
          <CardHeader>
            <CardTitle>Contract Details</CardTitle>
            <CardDescription>
              Please fill in the contract information and select the associated organization and project
            </CardDescription>
          </CardHeader>
          <CardContent>
            <form onSubmit={handleSubmit(handleUploadContract)} className="space-y-6">
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div className="space-y-2">
                  <Label htmlFor="title">Contract Title *</Label>
                  <Input 
                    id="title" 
                    placeholder="e.g., Software License Agreement" 
                    {...register('title', { required: true })}
                  />
                </div>
                
                <div className="space-y-2">
                  <Label htmlFor="contractType">Contract Type *</Label>
                  <Select onValueChange={(value) => setValue('contractType', value)}>
                    <SelectTrigger>
                      <SelectValue placeholder="Select contract type" />
                    </SelectTrigger>
                    <SelectContent>
                      {contractTypes.map((type) => (
                        <SelectItem key={type} value={type}>
                          {type}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              </div>

              <div className="space-y-2">
                <Label htmlFor="description">Description</Label>
                <Textarea 
                  id="description" 
                  placeholder="Brief description of the contract"
                  rows={3}
                  {...register('description')}
                />
              </div>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div className="space-y-2">
                  <Label htmlFor="organization">Organization *</Label>
                  <Select onValueChange={handleOrganizationChange}>
                    <SelectTrigger>
                      <SelectValue placeholder="Select organization">
                        <div className="flex items-center gap-2">
                          <Building className="h-4 w-4" />
                          <span>Select organization</span>
                        </div>
                      </SelectValue>
                    </SelectTrigger>
                    <SelectContent>
                      {mockOrganizations.map((org) => (
                        <SelectItem key={org.id} value={org.id}>
                          <div className="flex items-center gap-2">
                            <Building className="h-4 w-4" />
                            <span>{org.name}</span>
                          </div>
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>

                <div className="space-y-2">
                  <Label htmlFor="project">Project *</Label>
                  <Select 
                    onValueChange={(value) => setValue('projectId', value)}
                    disabled={!selectedOrganizationId}
                  >
                    <SelectTrigger>
                      <SelectValue placeholder="Select project">
                        <div className="flex items-center gap-2">
                          <FolderOpen className="h-4 w-4" />
                          <span>Select project</span>
                        </div>
                      </SelectValue>
                    </SelectTrigger>
                    <SelectContent>
                      {filteredProjects.map((project) => (
                        <SelectItem key={project.id} value={project.id}>
                          <div className="flex items-center gap-2">
                            <FolderOpen className="h-4 w-4" />
                            <span>{project.name}</span>
                          </div>
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  {!selectedOrganizationId && (
                    <p className="text-xs text-muted-foreground">
                      Please select an organization first
                    </p>
                  )}
                </div>
              </div>
              
              <div className="space-y-2">
                <Label htmlFor="file">Contract Files *</Label>
                <Input 
                  id="file" 
                  type="file" 
                  accept=".pdf,.doc,.docx" 
                  multiple
                  {...register('file', { required: true })}
                />
                <p className="text-xs text-muted-foreground">
                  Supported formats: PDF, DOC, DOCX (Max 10MB each). You can select multiple files.
                </p>
                {watch('file') && watch('file').length > 0 && (
                  <div className="text-sm text-muted-foreground">
                    Selected {watch('file').length} file(s)
                  </div>
                )}
              </div>
              
              <Button type="submit" className="w-full" disabled={uploading}>
                {uploading ? (
                  <>
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                    Uploading Contract...
                  </>
                ) : (
                  <>
                    <Upload className="mr-2 h-4 w-4" />
                    Upload Contract
                  </>
                )}
              </Button>
            </form>
          </CardContent>
        </Card>
      </div>
    </div>
  );
};

export default ContractUploadPage;