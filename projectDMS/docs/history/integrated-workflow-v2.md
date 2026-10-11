# Integrated Letter Workflow System with Role-Based Strategy Planning

## Overview

This document integrates the **consolidated requirements**, **strategy plan implementation**, and **role-based three-way context system** by adding a new **"Strategy"** tab to the letter workflow after the "Input" stage. The system now supports role selection (Contractor, Engineer, Employer) and generates consolidated contexts from complete letter content for strategic planning.

**Updated Workflow:** `All → Input → Strategy → Draft → Review → Approval → Completed`

---

## 1. Workflow Architecture Update

### 1.1 New Strategy Stage with Role-Based Planning

**Purpose:** Generate structured strategic plan based on role perspective before drafting letter

**Key Features:**
1. **Role Selection**: User selects role (Contractor / Engineer's Rep / Employer)
2. **Three-Way Context Generation**: System consolidates complete letter content by party
3. **Role-Specific Prompts**: AI generates strategy using appropriate prompt template
4. **Structured Analysis**: 9-section strategic plan tailored to role
5. **Plan Review & Approval**: Human edits plan before proceeding to draft

**Entry Condition:** User moves letter from Input → Strategy status

**Exit Condition:** User approves and saves strategic plan, transitions to Draft

---

## 2. Updated Database Schema

### 2.1 Letter Status Type

```typescript
// client/src/components/letter-workflow/types.ts
export type LetterStatus =
  | "Input"
  | "Strategy"     // ← NEW
  | "Draft"
  | "Review"
  | "Approval"
  | "Completed"
  | "Rejected";
```

### 2.2 MongoDB Letter Schema (Role-Based Context)

```python
# backend/rbac_backend/models/letter.py

class LetterRecord(BaseModel):
    # ... existing fields ...

    # ========== STRATEGY STAGE FIELDS ==========
    strategy_plan: Optional[str] = None
    strategic_outline: Optional[Dict[str, Any]] = None
    summary_points: List[str] = []
    strategy_plan_approved_by: Optional[str] = None
    strategy_plan_approved_at: Optional[datetime] = None

    # Context documents for strategy
    context_document_ids: List[str] = []
    background_summary: List[Dict[str, Any]] = []

    # ========== NEW: ROLE-BASED CONSOLIDATED CONTEXTS ==========
    contractor_context: Optional[str] = None     # Full consolidated contractor perspective
    engineer_context: Optional[str] = None       # Full consolidated engineer perspective
    employer_context: Optional[str] = None       # Full consolidated employer perspective

    # Thread linking
    thread_id: Optional[str] = None              # Group related letters in correspondence
    thread_letters: List[str] = []               # All letter IDs in thread

    # Role selection for strategy generation
    strategy_role: Optional[str] = None          # "contractor", "engineer", "employer"
    strategy_recipient: Optional[str] = None     # "Contractor", "Engineer", "Employer" (if engineer role)

    # Three-way correspondence metadata
    correspondence_type: Optional[str] = None    # "three-way", "two-way", "standalone"
    parties_involved: List[str] = []             # ["Contractor", "Engineer", "Employer"]

    # LangGraph strategy run
    strategy_run_id: Optional[str] = None
    strategy_graph_status: Optional[str] = None  # "success", "needs_input", "failed"
    strategy_graph_trace: List[Dict[str, Any]] = []

    # Time tracking
    strategy_started_at: Optional[datetime] = None
    strategy_completed_at: Optional[datetime] = None
    duration_strategy: Optional[float] = None    # Hours in strategy stage
```

---

## 3. Updated Workflow Tabs Component

```typescript
// client/src/components/letter-workflow/LetterWorkflowTabs.tsx

import { Lightbulb } from 'lucide-react';  // ← NEW icon for Strategy

const tabs = useMemo(
  () => [
    {
      value: "All" as const,
      label: "All Letters",
      icon: <ListChecks className="w-4 h-4" />,
      description: "View every letter regardless of workflow stage",
    },
    {
      value: "Input" as const,
      label: "Input",
      icon: <MessageCircleQuestion className="w-4 h-4" />,
      description: "Letters that require additional information or clarification",
    },
    // ← NEW STRATEGY TAB
    {
      value: "Strategy" as const,
      label: "Strategy",
      icon: <Lightbulb className="w-4 h-4" />,
      description: "Letters with strategic plans being developed or approved",
    },
    {
      value: "Draft" as const,
      label: "Draft",
      icon: <Mail className="w-4 h-4" />,
      description: "Letters currently being drafted or revised",
    },
    // ... other tabs
  ],
  []
);
```

---

## 4. Enhanced Strategy Stage Page with Role Selection

### 4.1 LetterStrategyPage.tsx (Complete)

```typescript
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import {
  ArrowLeft,
  Sparkles,
  CheckCircle,
  Edit3,
  RefreshCw,
  AlertCircle,
  Users,
  FileText
} from 'lucide-react';

import { useLetterWorkflow } from '@/hooks/useLetterWorkflow';
import { useLanggraphStrategyPlan } from '@/hooks/useLanggraphStrategyPlan';

import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Textarea } from '@/components/ui/textarea';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { useToast } from '@/hooks/use-toast';

import { LinkedDocumentSelector } from '@/components/letter-workflow/LinkedDocumentSelector';
import BackgroundSummary from '@/components/letter-workflow/BackgroundSummary';
import StrategyPlanDisplay from '@/components/langgraph/StrategyPlanDisplay';

import { mapLetterToUi, UILetter } from '@/utils/letterWorkflowMapping';

const LetterStrategyPage = () => {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { toast } = useToast();

  const {
    letters,
    users,
    handleLetterUpdate,
    fetchLetters,
  } = useLetterWorkflow();

  const {
    generateStrategyPlan,
    loading: planLoading,
    error: planError
  } = useLanggraphStrategyPlan();

  // ========== STATE ==========
  const [selectedDocIds, setSelectedDocIds] = useState<string[]>([]);
  const [strategyPlanData, setStrategyPlanData] = useState<any>(null);
  const [isEditing, setIsEditing] = useState(false);
  const [editedPlan, setEditedPlan] = useState('');

  // ← NEW: Role-based context states
  const [selectedRole, setSelectedRole] = useState<'contractor' | 'engineer' | 'employer'>('engineer');
  const [recipientIfEngineer, setRecipientIfEngineer] = useState<'Contractor' | 'Employer'>('Contractor');

  const [contractorContext, setContractorContext] = useState<string>('');
  const [engineerContext, setEngineerContext] = useState<string>('');
  const [employerContext, setEmployerContext] = useState<string>('');
  const [contextsGenerated, setContextsGenerated] = useState(false);
  const [generatingContexts, setGeneratingContexts] = useState(false);

  // Get letter
  const letter = useMemo(
    () => letters.find((l) => l.id === id),
    [letters, id]
  );

  const uiLetter: UILetter | null = useMemo(
    () => letter ? mapLetterToUi(letter, users) : null,
    [letter, users]
  );

  // Initialize
  useEffect(() => {
    if (uiLetter?.contextDocumentIds) {
      setSelectedDocIds(uiLetter.contextDocumentIds);
    }
    if (uiLetter?.strategicPlan) {
      setEditedPlan(uiLetter.strategicPlan);
    }
    // Load existing contexts if available
    if (uiLetter?.contractorContext) {
      setContractorContext(uiLetter.contractorContext);
      setEngineerContext(uiLetter.engineerContext || '');
      setEmployerContext(uiLetter.employerContext || '');
      setContextsGenerated(true);
    }
  }, [uiLetter]);

  // ========== NEW: GENERATE THREE-WAY CONSOLIDATED CONTEXTS ==========
  const handleGenerateContexts = useCallback(async () => {
    if (!id) return;

    setGeneratingContexts(true);
    try {
      // Generate all three contexts in parallel using complete letter content
      const [contractorRes, engineerRes, employerRes] = await Promise.all([
        fetch(`/api/letters/${id}/generate-context`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ role: 'contractor' })
        }),
        fetch(`/api/letters/${id}/generate-context`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ role: 'engineer' })
        }),
        fetch(`/api/letters/${id}/generate-context`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ role: 'employer' })
        })
      ]);

      const contractor = await contractorRes.json();
      const engineer = await engineerRes.json();
      const employer = await employerRes.json();

      setContractorContext(contractor.context);
      setEngineerContext(engineer.context);
      setEmployerContext(employer.context);
      setContextsGenerated(true);

      // Save contexts to database
      await handleLetterUpdate(id, {
        contractorContext: contractor.context,
        engineerContext: engineer.context,
        employerContext: employer.context,
        correspondenceType: 'three-way',
        partiesInvolved: ['Contractor', 'Engineer', 'Employer']
      });

      toast({
        title: 'Contexts Generated',
        description: `Three-way consolidated contexts created successfully from ${contractor.letter_count} letters.`,
      });
    } catch (error: any) {
      toast({
        title: 'Context Generation Failed',
        description: error?.message || 'Failed to generate consolidated contexts',
        variant: 'destructive'
      });
    } finally {
      setGeneratingContexts(false);
    }
  }, [id, handleLetterUpdate, toast]);

  // ========== GENERATE ROLE-BASED STRATEGIC PLAN ==========
  const handleGenerateStrategyPlan = useCallback(async () => {
    if (!id || !uiLetter) return;

    // Validate contexts are generated
    if (!contextsGenerated) {
      toast({
        title: 'Contexts Required',
        description: 'Please generate three-way contexts before creating the strategic plan.',
        variant: 'destructive'
      });
      return;
    }

    try {
      const response = await generateStrategyPlan({
        letterId: id,
        role: selectedRole,
        recipient: selectedRole === 'engineer' ? recipientIfEngineer : undefined,
        subject: uiLetter.subject,
        context: uiLetter.content,
        documentIds: selectedDocIds,
        letterReference: uiLetter.letterNo || id,
        letterDate: uiLetter.createdAt,
        // Pass consolidated contexts
        contractorContext,
        engineerContext,
        employerContext
      });

      setStrategyPlanData(response);
      setEditedPlan(response.plan);

      // Store context documents and role selection
      await handleLetterUpdate(id, {
        backgroundSummary: response.background_summary,
        contextDocumentIds: response.context_document_ids,
        strategyRole: selectedRole,
        strategyRecipient: recipientIfEngineer
      });

      toast({
        title: 'Strategy Plan Generated',
        description: `${selectedRole.charAt(0).toUpperCase() + selectedRole.slice(1)} strategy plan created. Review and approve to proceed.`,
      });
    } catch (error: any) {
      const errorMsg = error?.response?.data?.detail || error?.message || 'Failed to generate strategy plan';
      toast({
        title: 'Generation Failed',
        description: errorMsg,
        variant: 'destructive',
      });
    }
  }, [
    id,
    uiLetter,
    selectedDocIds,
    selectedRole,
    recipientIfEngineer,
    contractorContext,
    engineerContext,
    employerContext,
    contextsGenerated,
    generateStrategyPlan,
    handleLetterUpdate,
    toast
  ]);

  // Approve and save strategy plan
  const handleApprovePlan = useCallback(async () => {
    if (!id) return;

    try {
      await handleLetterUpdate(id, {
        strategicPlan: editedPlan,
        strategicOutline: strategyPlanData?.structured_plan,
        summaryPoints: strategyPlanData?.summary_points || [],
        strategyPlanApprovedAt: new Date().toISOString(),
        strategyPlanApprovedBy: 'current_user_id', // Replace with actual user ID
        status: 'Draft', // Move to next stage
      });

      toast({
        title: 'Strategy Plan Approved',
        description: 'Moving to draft stage. You can now generate the letter draft.',
      });

      setTimeout(() => navigate(`/letters/${id}/draft`), 1500);
    } catch (error: any) {
      toast({
        title: 'Save Failed',
        description: error?.message || 'Failed to save strategy plan',
        variant: 'destructive',
      });
    }
  }, [id, editedPlan, strategyPlanData, handleLetterUpdate, navigate, toast]);

  // Save progress without moving forward
  const handleSaveProgress = useCallback(async () => {
    if (!id) return;

    try {
      await handleLetterUpdate(id, {
        strategicPlan: editedPlan,
        strategicOutline: strategyPlanData?.structured_plan,
        summaryPoints: strategyPlanData?.summary_points || [],
      });

      toast({
        title: 'Progress Saved',
        description: 'Your strategy plan has been saved.',
      });
    } catch (error: any) {
      toast({
        title: 'Save Failed',
        description: error?.message || 'Failed to save progress',
        variant: 'destructive',
      });
    }
  }, [id, editedPlan, strategyPlanData, handleLetterUpdate, toast]);

  if (!uiLetter) {
    return (
      <div className="flex items-center justify-center h-64">
        <p className="text-gray-500">Letter not found</p>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Button
            variant="ghost"
            size="sm"
            onClick={() => navigate('/letters')}
          >
            <ArrowLeft className="w-4 h-4 mr-2" />
            Back
          </Button>
          <div>
            <h1 className="text-3xl font-bold">{uiLetter.title}</h1>
            <p className="text-gray-600">{uiLetter.subject}</p>
          </div>
        </div>
        <Badge>{uiLetter.status}</Badge>
      </div>

      {/* Content */}
      <div className="grid gap-6 lg:grid-cols-3">
        {/* Main Content */}
        <div className="lg:col-span-2 space-y-6">
          {/* Letter Context */}
          <Card>
            <CardHeader>
              <CardTitle>Letter Context & Requirements</CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-sm text-gray-600 mb-4">{uiLetter.content}</p>
              <div className="grid gap-2 text-sm">
                <div>
                  <span className="font-semibold">Recipient:</span> {uiLetter.recipient}
                </div>
                <div>
                  <span className="font-semibold">Subject:</span> {uiLetter.subject}
                </div>
              </div>
            </CardContent>
          </Card>

          {/* ← NEW: ROLE SELECTION & CONTEXT GENERATION */}
          <Card className="border-2 border-blue-200 bg-blue-50">
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Users className="w-5 h-5" />
                Role Selection & Context Generation
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <Alert>
                <AlertCircle className="h-4 w-4" />
                <AlertDescription>
                  Select your role and generate consolidated contexts from all related correspondence before creating the strategic plan.
                </AlertDescription>
              </Alert>

              <div className="grid gap-4">
                <div>
                  <Label>Your Role</Label>
                  <Select value={selectedRole} onValueChange={setSelectedRole}>
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="contractor">
                        Contractor (Writing to Engineer)
                      </SelectItem>
                      <SelectItem value="engineer">
                        Engineer's Representative
                      </SelectItem>
                      <SelectItem value="employer">
                        Employer (Writing to Engineer)
                      </SelectItem>
                    </SelectContent>
                  </Select>
                </div>

                {selectedRole === 'engineer' && (
                  <div>
                    <Label>Recipient</Label>
                    <Select value={recipientIfEngineer} onValueChange={setRecipientIfEngineer}>
                      <SelectTrigger>
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="Contractor">
                          Writing to Contractor
                        </SelectItem>
                        <SelectItem value="Employer">
                          Writing to Employer
                        </SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                )}

                <Button
                  onClick={handleGenerateContexts}
                  variant={contextsGenerated ? "outline" : "default"}
                  disabled={generatingContexts}
                  className="w-full"
                >
                  {generatingContexts ? (
                    <>
                      <RefreshCw className="w-4 h-4 mr-2 animate-spin" />
                      Generating Contexts...
                    </>
                  ) : contextsGenerated ? (
                    <>
                      <CheckCircle className="w-4 h-4 mr-2" />
                      Contexts Generated (Click to Regenerate)
                    </>
                  ) : (
                    <>
                      <FileText className="w-4 h-4 mr-2" />
                      Generate Three-Way Contexts
                    </>
                  )}
                </Button>

                {contextsGenerated && (
                  <Tabs defaultValue="contractor" className="w-full">
                    <TabsList className="grid w-full grid-cols-3">
                      <TabsTrigger value="contractor">Contractor</TabsTrigger>
                      <TabsTrigger value="engineer">Engineer</TabsTrigger>
                      <TabsTrigger value="employer">Employer</TabsTrigger>
                    </TabsList>
                    <TabsContent value="contractor" className="mt-4">
                      <Card>
                        <CardContent className="pt-4">
                          <div className="bg-gray-50 p-4 rounded text-xs font-mono max-h-64 overflow-y-auto whitespace-pre-wrap">
                            {contractorContext || 'No context available'}
                          </div>
                        </CardContent>
                      </Card>
                    </TabsContent>
                    <TabsContent value="engineer" className="mt-4">
                      <Card>
                        <CardContent className="pt-4">
                          <div className="bg-gray-50 p-4 rounded text-xs font-mono max-h-64 overflow-y-auto whitespace-pre-wrap">
                            {engineerContext || 'No context available'}
                          </div>
                        </CardContent>
                      </Card>
                    </TabsContent>
                    <TabsContent value="employer" className="mt-4">
                      <Card>
                        <CardContent className="pt-4">
                          <div className="bg-gray-50 p-4 rounded text-xs font-mono max-h-64 overflow-y-auto whitespace-pre-wrap">
                            {employerContext || 'No context available'}
                          </div>
                        </CardContent>
                      </Card>
                    </TabsContent>
                  </Tabs>
                )}
              </div>
            </CardContent>
          </Card>

          {/* Strategy Plan Generation */}
          {!strategyPlanData ? (
            <Card className="border-dashed border-2">
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <Sparkles className="w-5 h-5" />
                  Generate Strategic Plan
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-4">
                <Alert>
                  <AlertCircle className="h-4 w-4" />
                  <AlertDescription>
                    Select supporting documents and generate a structured strategic plan tailored to your role perspective.
                  </AlertDescription>
                </Alert>

                <Button
                  onClick={handleGenerateStrategyPlan}
                  disabled={planLoading || !contextsGenerated || selectedDocIds.length === 0}
                  size="lg"
                  className="w-full"
                >
                  {planLoading ? (
                    <>
                      <RefreshCw className="w-4 h-4 mr-2 animate-spin" />
                      Generating Plan...
                    </>
                  ) : (
                    <>
                      <Sparkles className="w-4 h-4 mr-2" />
                      Generate Strategy Plan
                    </>
                  )}
                </Button>

                {planError && (
                  <Alert variant="destructive">
                    <AlertCircle className="h-4 w-4" />
                    <AlertDescription>{planError}</AlertDescription>
                  </Alert>
                )}
              </CardContent>
            </Card>
          ) : (
            <>
              {/* Strategy Plan Display */}
              <StrategyPlanDisplay
                data={strategyPlanData}
                onEdit={() => setIsEditing(!isEditing)}
                onApprove={handleApprovePlan}
                loading={planLoading}
              />

              {/* Edit Mode */}
              {isEditing && (
                <Card>
                  <CardHeader>
                    <CardTitle className="flex items-center gap-2">
                      <Edit3 className="w-4 h-4" />
                      Edit Strategic Plan
                    </CardTitle>
                  </CardHeader>
                  <CardContent className="space-y-4">
                    <Textarea
                      value={editedPlan}
                      onChange={(e) => setEditedPlan(e.target.value)}
                      placeholder="Edit the strategic plan..."
                      className="min-h-80 font-mono text-sm"
                    />
                    <div className="flex gap-2">
                      <Button onClick={handleSaveProgress} variant="outline">
                        Save Changes
                      </Button>
                      <Button onClick={() => setIsEditing(false)} variant="ghost">
                        Cancel
                      </Button>
                    </div>
                  </CardContent>
                </Card>
              )}

              {/* Approval Buttons */}
              <div className="flex gap-2 pt-4">
                <Button
                  onClick={handleApprovePlan}
                  disabled={planLoading}
                  size="lg"
                  className="flex-1"
                >
                  <CheckCircle className="w-4 h-4 mr-2" />
                  Approve & Proceed to Draft
                </Button>
                <Button
                  onClick={handleSaveProgress}
                  variant="outline"
                  disabled={planLoading}
                >
                  Save Progress
                </Button>
              </div>
            </>
          )}
        </div>

        {/* Sidebar */}
        <div className="space-y-6">
          {/* Document Selector */}
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Supporting Documents</CardTitle>
            </CardHeader>
            <CardContent>
              <LinkedDocumentSelector
                selectedDocIds={selectedDocIds}
                onSelectionChange={setSelectedDocIds}
              />
            </CardContent>
          </Card>

          {/* Background Summary */}
          {uiLetter.backgroundSummary && (
            <Card>
              <CardHeader>
                <CardTitle className="text-base">Background Context</CardTitle>
              </CardHeader>
              <CardContent>
                <BackgroundSummary
                  entries={uiLetter.backgroundSummary}
                />
              </CardContent>
            </Card>
          )}

          {/* Status Info */}
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Status & Progress</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2 text-sm">
              <div>
                <span className="font-semibold">Status:</span>
                <Badge className="ml-2">{uiLetter.status}</Badge>
              </div>
              <div>
                <span className="font-semibold">Created:</span>
                <p className="text-gray-600">{uiLetter.createdAt}</p>
              </div>
              <div>
                <span className="font-semibold">Assigned To:</span>
                <p className="text-gray-600">{uiLetter.assignedTo?.name}</p>
              </div>
              {selectedRole && (
                <div>
                  <span className="font-semibold">Current Role:</span>
                  <p className="text-gray-600 capitalize">{selectedRole}</p>
                </div>
              )}
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
};

export default LetterStrategyPage;
```

---

## 5. Backend API Endpoints

### 5.1 Generate Consolidated Context

```python
# backend/rbac_backend/routers/context.py

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel

router = APIRouter(prefix="/api/letters", tags=["context"])

class GenerateContextRequest(BaseModel):
    role: str  # "contractor", "engineer", "employer"

@router.post("/{letter_id}/generate-context")
async def generate_consolidated_context(
    letter_id: str,
    request: GenerateContextRequest,
    current_user: dict = Depends(verify_auth)
):
    """
    Generate consolidated context from all linked letters in thread.
    Uses complete letter content instead of summaries.
    """

    try:
        # Get all letters in thread (same subject/topic)
        thread_letters = await LetterService.get_thread_letters(letter_id)

        # Consolidate by role using full letter content
        context = await ContextService.consolidate_context_by_role(
            thread_letters=thread_letters,
            role=request.role
        )

        return {
            "role": request.role,
            "context": context,
            "letter_count": len(thread_letters),
            "parties": ["Contractor", "Engineer", "Employer"]
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
```

### 5.2 Updated Strategy Plan Generation

```python
# backend/rbac_backend/routers/strategy_plan.py

class StrategyPlanRequest(BaseModel):
    letter_id: str
    role: str  # "contractor", "engineer", "employer"
    recipient: Optional[str] = None  # "Contractor" or "Employer" (if engineer)
    subject: str
    context: str
    document_ids: List[str] = []
    letter_reference: str
    letter_date: str
    contractor_context: str
    engineer_context: str
    employer_context: str

@router.post("/api/ai-assistant/langgraph/strategy-plan")
async def generate_strategy_plan(
    request: StrategyPlanRequest,
    current_user: dict = Depends(verify_auth)
):
    """Generate role-based strategic plan"""

    try:
        # Select prompt template based on role
        if request.role == "contractor":
            prompt_template = CONTRACTOR_TO_ENGINEER_PROMPT
        elif request.role == "engineer":
            if request.recipient == "Contractor":
                prompt_template = ENGINEER_TO_CONTRACTOR_PROMPT
            else:
                prompt_template = ENGINEER_TO_EMPLOYER_PROMPT
        elif request.role == "employer":
            prompt_template = EMPLOYER_TO_ENGINEER_PROMPT

        # Inject contexts into prompt
        full_prompt = prompt_template.format(
            CONTRACTOR_FULL_CONTEXT=request.contractor_context,
            ENGINEER_FULL_CONTEXT=request.engineer_context,
            EMPLOYER_FULL_CONTEXT=request.employer_context,
            LETTER_REFERENCE=request.letter_reference,
            LETTER_DATE=request.letter_date
        )

        # Run LangGraph with role-specific prompt
        result = await AIService.generate_strategy_plan(
            letter_id=request.letter_id,
            prompt=full_prompt,
            role=request.role,
            recipient=request.recipient,
            document_ids=request.document_ids
        )

        return result

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
```

---

## 6. Complete Updated Workflow State Machine

```
┌──────────────────────────────────────────────────────────────┐
│                LETTER WORKFLOW WITH ROLE-BASED STRATEGY       │
└──────────────────────────────────────────────────────────────┘

    ┌─────────┐
    │ CREATED │
    └────┬────┘
         │
         ↓
    ┌─────────────┐
    │    INPUT    │ ← Requesting info/clarifications
    │   Status    │   • Respond to Input Requests
    │             │   • Transition → Strategy
    └────┬────────┘
         │
         ↓
    ┌──────────────────────────────────┐
    │         STRATEGY (NEW)           │ ← Strategic Planning with Role Selection
    │          Status                   │   • Select Role (Contractor/Engineer/Employer)
    │                                   │   • Generate Three-Way Contexts (Complete Content)
    │  1. Generate Contexts             │   • Select Supporting Documents
    │     - Contractor Context          │   • AI Generates 9-Section Plan
    │     - Engineer Context            │   • Review/Edit Plan
    │     - Employer Context            │   • Approve Plan
    │                                   │   • Transition → Draft
    │  2. Select Role & Generate Plan   │
    │     - Contractor → Engineer       │
    │     - Engineer → Contractor       │
    │     - Engineer → Employer         │
    │                                   │
    │  3. Review & Approve              │
    └────┬─────────────────────────────┘
         │
         ↓
    ┌──────────────┐
    │    DRAFT     │ ← AI Draft Generation (using approved plan)
    │   Status     │   • Generate Draft Based on Strategy
    │              │   • Edit Content
    └────┬─────────┘   • Transition → Review
         │
         ↓
    ┌──────────────┐
    │   REVIEW     │ ← Reviewer Feedback
    │   Status     │   • Add Comments
    │              │   • Send Back or Approve
    └────┬─────────┘   • Transition → Approval
         │
         ↓
    ┌──────────────┐
    │  APPROVAL    │ ← Final Sign-Off
    │   Status     │   • Final Review
    │              │   • Approve or Reject
    └────┬─────────┘   • Export (DOCX/PDF)
         │             • Transition → Completed
         ↓
    ┌──────────────┐
    │ COMPLETED    │ ← Archived
    │   Status     │   • View Only
    │              │   • Download
    └──────────────┘
```

---

## 7. Integration Summary

### 7.1 What Was Added

✅ **Role Selection Interface** - Dropdown for Contractor/Engineer/Employer
✅ **Three-Way Context Generation** - Consolidates complete letter content by party
✅ **Role-Specific Prompts** - 9-section strategic plan templates for each role
✅ **Context Preview Tabs** - View Contractor, Engineer, and Employer contexts
✅ **Database Schema Updates** - New fields for contexts and role metadata
✅ **Backend API Endpoints** - Context generation and role-based strategy
✅ **Time Tracking** - Duration in strategy stage
✅ **Workflow Validation** - Ensure contexts generated before plan creation

### 7.2 User Workflow

1. **Input Stage**: Gather requirements and clarifications
2. **Move to Strategy**: Transition letter to Strategy status
3. **Role Selection**: User selects their role perspective
4. **Context Generation**: System consolidates all related correspondence by party (uses complete letter content, not summaries)
5. **Document Selection**: User selects supporting documents
6. **Strategy Generation**: AI creates role-specific 9-section strategic plan
7. **Plan Review**: User reviews, edits, and approves plan
8. **Move to Draft**: Proceed to letter drafting with approved strategy

---

## 8. Key Benefits

✅ **Complete Context**: Uses full letter content instead of summaries for accuracy
✅ **Role Perspective**: Strategy tailored to Contractor, Engineer, or Employer viewpoint
✅ **Three-Way Analysis**: Understands all parties' positions simultaneously
✅ **Structured Output**: 9-section plan covering all strategic aspects
✅ **FIDIC Compliance**: Prompts aligned with FIDIC contract procedures
✅ **Dispute Preparedness**: Risk assessment and alternative options included
✅ **Audit Trail**: Tracks role selection and contexts used
✅ **Flexible Workflows**: Can iterate back to Input or forward to Draft

---

## 9. Next Steps

1. **Phase 1: Backend Implementation**
   - [ ] Add context generation endpoint
   - [ ] Implement role-based prompt templates
   - [ ] Add database fields
   - [ ] Create ContextService

2. **Phase 2: Frontend Implementation**
   - [ ] Update LetterStrategyPage with role selection
   - [ ] Add three-way context preview tabs
   - [ ] Wire up context generation
   - [ ] Integrate with strategy plan generation

3. **Phase 3: Testing**
   - [ ] Test context generation with real correspondence
   - [ ] Test all three role prompts
   - [ ] Verify complete letter content usage
   - [ ] E2E workflow testing

4. **Phase 4: Documentation & Training**
   - [ ] User guide for role selection
   - [ ] Prompt template documentation
   - [ ] Admin guide for context management

---

## Conclusion

This integrated workflow system adds comprehensive **role-based strategic planning** to the letter management process, positioned between **Input** and **Draft** stages. It provides:

✅ **Clear Strategic Phase** - Separate planning from execution
✅ **Role-Specific Intelligence** - AI adapts to user's role perspective
✅ **Complete Context** - Full letter content, not just summaries
✅ **Three-Way Understanding** - Simultaneous view of all parties' positions
✅ **Structured Analysis** - 9-section strategic framework
✅ **Time Tracking** - New duration metrics for strategy development
✅ **Flexible Workflows** - Iterate or progress as needed

Ready to implement! 🚀
