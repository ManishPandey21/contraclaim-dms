# Integrated Letter Workflow System with Strategy Tab

## Overview

This document integrates the **consolidated requirements** with the **strategy plan implementation** by adding a new **"Strategy"** tab to the letter workflow after the "Input" stage. This creates a clear three-stage workflow: **Input → Strategy → Draft → Review → Approval → Completed**.

---

## 1. Workflow Architecture Update

### 1.1 Current Workflow Stages

**Current (6 stages):**
```
All Letters → Input → Draft → Review → Approval → Completed
```

**Updated (7 stages with Strategy):**
```
All Letters → Input → Strategy → Draft → Review → Approval → Completed
```

### 1.2 New Stage: Strategy

**Purpose:** Generate structured strategic plan before drafting letter

**Activities:**
- Select context documents (LinkedDocumentSelector)
- Generate background summary from previous letters
- Create structured strategic plan (TONE, CONTENT, RESPONSES, RISKS, OUTCOME)
- Review and approve plan before proceeding to draft
- Edit plan with human input

**Entry Condition:** User moves letter from Input → Strategy status

**Exit Condition:** User approves and saves strategic plan, transitions to Draft

---

## 2. Updated Letter Status Type & Database Schema

### 2.1 TypeScript Status Type

Update `client/src/components/letter-workflow/types.ts`:

```typescript
// types.ts
export type LetterStatus =
  | "Input"
  | "Strategy"
  | "Draft"
  | "Review"
  | "Approval"
  | "Completed"
  | "Rejected";
```

### 2.2 Updated MongoDB Letter Schema

Update the letter record in `backend/rbac_backend/models/letter.py`:

```python
# backend/rbac_backend/models/letter.py

from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from datetime import datetime
from enum import Enum

class LetterStatusEnum(str, Enum):
    INPUT = "Input"
    STRATEGY = "Strategy"        # ← NEW
    DRAFT = "Draft"
    REVIEW = "Review"
    APPROVAL = "Approval"
    COMPLETED = "Completed"
    REJECTED = "Rejected"

class LetterRecord(BaseModel):
    """MongoDB Letter Document"""
    _id: Optional[str] = None

    # Basic metadata
    letter_no: str
    title: str
    subject: str
    recipient: str
    status: LetterStatusEnum = LetterStatusEnum.INPUT

    # Organization & Project
    organization_id: str
    project_id: str

    # User tracking
    created_by: str
    assigned_to: str
    created_at: datetime
    updated_at: datetime
    status_start_date: datetime

    # Content fields
    content: str  # Initial requirements/context
    draft_body: Optional[str] = None
    comments: List[str] = []

    # ========== STRATEGY STAGE FIELDS ==========
    strategy_plan: Optional[str] = None           # ← NEW: Full plan text
    strategic_outline: Optional[Dict[str, Any]] = None  # ← NEW: Structured plan
    summary_points: List[str] = []                # ← NEW: Key points
    strategy_plan_approved_by: Optional[str] = None  # ← NEW: Approval user
    strategy_plan_approved_at: Optional[datetime] = None  # ← NEW: Approval time

    # Context documents for strategy
    context_document_ids: List[str] = []
    background_summary: List[Dict[str, Any]] = []
    background_annotations: Optional[Dict[str, Any]] = None

    # LangGraph strategy run
    strategy_run_id: Optional[str] = None         # ← NEW
    strategy_graph_status: Optional[str] = None   # ← NEW: "success", "needs_input", "failed"
    strategy_graph_trace: List[Dict[str, Any]] = []  # ← NEW

    # ========== EXISTING DRAFT STAGE FIELDS ==========
    draft_plan: Optional[str] = None
    draft_trace: List[Dict[str, Any]] = []
    graph_status: Optional[str] = None
    graph_started_at: Optional[datetime] = None
    graph_completed_at: Optional[datetime] = None
    graph_thread: List[Dict[str, Any]] = []

    # Time tracking
    draft_requested_at: Optional[datetime] = None
    input_received_at: Optional[datetime] = None
    strategy_started_at: Optional[datetime] = None       # ← NEW
    strategy_completed_at: Optional[datetime] = None     # ← NEW
    review_started_at: Optional[datetime] = None
    approved_at: Optional[datetime] = None
    finalized_at: Optional[datetime] = None

    # Duration tracking
    duration_input_received: Optional[float] = None
    duration_strategy: Optional[float] = None            # ← NEW (in hours)
    duration_draft: Optional[float] = None               # ← NEW (in hours)
    duration_review: Optional[float] = None
    duration_approval: Optional[float] = None
    duration_total: Optional[float] = None

    # File references
    s3_key_docx: Optional[str] = None
    s3_key_pdf: Optional[str] = None
    presigned_docx_url: Optional[str] = None
    presigned_pdf_url: Optional[str] = None
```

---

## 3. Updated Workflow Tabs Component

### 3.1 LetterWorkflowTabs.tsx with Strategy Tab

Update `client/src/components/letter-workflow/LetterWorkflowTabs.tsx`:

```typescript
import React, { useMemo } from 'react';
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  ListChecks,
  MessageCircleQuestion,
  Lightbulb,           // ← NEW for Strategy
  Mail,
  Clock,
  CheckCircle,
  XCircle
} from 'lucide-react';

import type { LetterStatus } from './types';

interface LetterWorkflowTabsProps {
  activeTab: LetterStatus | "All";
  setActiveTab: (tab: LetterStatus | "All") => void;
  children: React.ReactNode;
}

export const LetterWorkflowTabs: React.FC<LetterWorkflowTabsProps> = ({
  activeTab,
  setActiveTab,
  children
}) => {
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
      {
        value: "Review" as const,
        label: "Review",
        icon: <Clock className="w-4 h-4" />,
        description: "Letters awaiting reviewer feedback or approval",
      },
      {
        value: "Approval" as const,
        label: "Approval",
        icon: <CheckCircle className="w-4 h-4" />,
        description: "Letters pending final approval",
      },
      {
        value: "Completed" as const,
        label: "Completed",
        icon: <CheckCircle className="w-4 h-4" />,
        description: "Approved or closed-out correspondence",
      },
      {
        value: "Rejected" as const,
        label: "Rejected",
        icon: <XCircle className="w-4 h-4" />,
        description: "Letters that were rejected or closed",
      },
    ],
    []
  );

  const activeTabMeta = tabs.find((tab) => tab.value === activeTab);

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <Tabs
            value={activeTab}
            onValueChange={(value) => setActiveTab(value as LetterStatus | "All")}
            className="w-full"
          >
            <TabsList className="grid w-full grid-cols-8">
              {tabs.map((tab) => (
                <TabsTrigger key={tab.value} value={tab.value} className="text-xs sm:text-sm">
                  {tab.icon}
                  <span className="hidden sm:inline ml-2">{tab.label}</span>
                  <span className="sm:hidden">{tab.label.charAt(0)}</span>
                </TabsTrigger>
              ))}
            </TabsList>
          </Tabs>
        </CardHeader>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Letter Management</CardTitle>
          <CardDescription>
            {activeTabMeta?.description ?? 'View letters by workflow stage'}
          </CardDescription>
        </CardHeader>
        <CardContent>
          {children}
        </CardContent>
      </Card>
    </div>
  );
};
```

---

## 4. New Strategy Stage Page

### 4.1 LetterStrategyPage.tsx (New)

Create `client/src/pages/LetterStrategyPage.tsx`:

```typescript
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { ArrowLeft, Sparkles, CheckCircle, Edit3, RefreshCw, AlertCircle } from 'lucide-react';

import { useLetterWorkflow } from '@/hooks/useLetterWorkflow';
import { useLanggraphStrategyPlan } from '@/hooks/useLanggraphStrategyPlan';
import { useLetterGraphRuns } from '@/hooks/useLetterGraphRuns';

import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Textarea } from '@/components/ui/textarea';
import { useToast } from '@/hooks/use-toast';

import { LinkedDocumentSelector } from '@/components/letter-workflow/LinkedDocumentSelector';
import BackgroundSummary from '@/components/letter-workflow/BackgroundSummary';
import StrategyPlanDisplay from '@/components/langgraph/StrategyPlanDisplay';
import GraphStatusBadge from '@/components/langgraph/GraphStatusBadge';

import { mapLetterToUi, UILetter } from '@/utils/letterWorkflowMapping';

interface ContextDocumentSummary {
  id: string;
  subject: string;
  letterNo?: string;
  date?: string;
  summary?: string;
}

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

  const { generateStrategyPlan, loading: planLoading, error: planError } = useLanggraphStrategyPlan();
  const { data: graphRunData, loading: graphRunLoading } = useLetterGraphRuns();

  // State
  const [selectedDocIds, setSelectedDocIds] = useState<string[]>([]);
  const [strategyPlanData, setStrategyPlanData] = useState<any>(null);
  const [isEditing, setIsEditing] = useState(false);
  const [editedPlan, setEditedPlan] = useState('');

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
  }, [uiLetter]);

  // Generate strategic plan
  const handleGenerateStrategyPlan = useCallback(async () => {
    if (!id || !uiLetter) return;

    try {
      const response = await generateStrategyPlan({
        letterId: id,
        subject: uiLetter.subject,
        recipient: uiLetter.recipient,
        context: uiLetter.content,
        documentIds: selectedDocIds,
        requirements: uiLetter.content,
      });

      setStrategyPlanData(response);
      setEditedPlan(response.plan);

      // Store context documents
      if (response.context_documents) {
        await handleLetterUpdate(id, {
          backgroundSummary: response.background_summary,
          contextDocumentIds: response.context_document_ids,
        });
      }

      toast({
        title: 'Strategy Plan Generated',
        description: 'Review the plan and approve to proceed to drafting.',
      });
    } catch (error: any) {
      const errorMsg = error?.response?.data?.detail || error?.message || 'Failed to generate strategy plan';
      toast({
        title: 'Generation Failed',
        description: errorMsg,
        variant: 'destructive',
      });
    }
  }, [id, uiLetter, selectedDocIds, generateStrategyPlan, handleLetterUpdate, toast]);

  // Approve and save strategy plan
  const handleApprovePlan = useCallback(async () => {
    if (!id) return;

    try {
      await handleLetterUpdate(id, {
        strategicPlan: editedPlan,
        strategicOutline: strategyPlanData?.structured_plan,
        summaryPoints: strategyPlanData?.summary_points || [],
        strategyPlanApprovedAt: new Date().toISOString(),
        status: 'Draft', // Move to next stage
      });

      toast({
        title: 'Strategy Plan Approved',
        description: 'Moving to draft stage. You can now generate the letter draft.',
      });

      // Navigate to draft page
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
                    Select supporting documents and generate a structured strategic plan to guide the letter drafting process.
                  </AlertDescription>
                </Alert>

                <Button
                  onClick={handleGenerateStrategyPlan}
                  disabled={planLoading || selectedDocIds.length === 0}
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
                      className="min-h-80"
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

## 5. Route Integration

### 5.1 Updated Router Configuration

Update `client/src/App.tsx` or routing file:

```typescript
import LetterStrategyPage from '@/pages/LetterStrategyPage';

// Add new route after Input
<Route path="/letters/:id/strategy" element={<LetterStrategyPage />} />
```

### 5.2 Updated Navigation Routes

Update `client/src/components/letter-workflow/LettersTable.tsx`:

```typescript
const getLetterRoute = useMemo(
  () => (letter: Letter) => {
    switch (letter.status) {
      case 'Input':
        return `/letters/${letter.id}/input`;
      case 'Strategy':                    // ← NEW
        return `/letters/${letter.id}/strategy`;
      case 'Draft':
        return `/letters/${letter.id}/draft`;
      case 'Review':
        return `/letters/${letter.id}/review`;
      case 'Approval':
        return `/letters/${letter.id}/approval`;
      case 'Completed':
        return `/letters/${letter.id}/completed`;
      case 'Rejected':
        return `/letters/${letter.id}/completed`;
      default:
        return `/letters/${letter.id}/input`;
    }
  },
  []
);
```

---

## 6. State Transition API

### 6.1 New Endpoint: Move to Strategy

Create endpoint in `backend/rbac_backend/routers/letters.py`:

```python
@router.post("/letters/{letter_id}/move-to-strategy")
async def move_letter_to_strategy(
    letter_id: str,
    current_user: dict = Depends(verify_auth)
):
    """Move letter from Input to Strategy stage"""

    try:
        letter = await LetterService.get_letter(letter_id, current_user)

        if letter["status"] != "Input":
            raise HTTPException(
                status_code=400,
                detail=f"Cannot move to Strategy from {letter['status']} stage"
            )

        # Update status
        await LetterService.update_letter(
            letter_id,
            {
                "status": "Strategy",
                "status_start_date": datetime.utcnow(),
            },
            current_user
        )

        return {"status": "moved_to_strategy", "letter_id": letter_id}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
```

### 6.2 Updated Input Page Navigation

Update `client/src/pages/LetterInputPage.tsx` to transition to Strategy:

```typescript
const handleMoveToStrategy = useCallback(async () => {
  try {
    // First save input response
    await respondToInputRequest(
      id,
      inputResponse,
      currentUserId
    );

    // Then move to strategy
    await fetch(`/api/letters/${id}/move-to-strategy`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' }
    });

    toast({
      title: 'Moving to Strategy Stage',
      description: 'Letter moved successfully. Proceed to strategic planning.',
    });

    navigate(`/letters/${id}/strategy`);
  } catch (error) {
    toast({
      title: 'Error',
      description: error?.message || 'Failed to move to strategy stage',
      variant: 'destructive',
    });
  }
}, [/* dependencies */]);
```

---

## 7. Updated Workflow Transitions

### 7.1 Complete State Machine

```
┌──────────────────────────────────────────────────────────┐
│                   LETTER WORKFLOW STATES                  │
└──────────────────────────────────────────────────────────┘

    ┌─────────┐
    │ CREATED │
    └────┬────┘
         │
         ↓
    ┌─────────────┐
    │    INPUT    │ ← Requesting info/clarifications
    │   Status    │   • Response Input Request
    │ "Input"     │   • Transition → Strategy
    └────┬────────┘
         │
         ↓
    ┌──────────────┐
    │  STRATEGY    │ ← NEW: Strategic Planning
    │   Status     │   • Select Documents
    │  "Strategy"  │   • Generate Plan (AI)
    └────┬─────────┘   • Review/Approve Plan
         │             • Transition → Draft
         ↓
    ┌──────────────┐
    │    DRAFT     │ ← AI Draft Generation
    │   Status     │   • Generate Draft (AI)
    │   "Draft"    │   • Edit Content
    └────┬─────────┘   • Transition → Review
         │
         ↓
    ┌──────────────┐
    │   REVIEW     │ ← Reviewer Feedback
    │   Status     │   • Add Comments
    │  "Review"    │   • Send Back or Approve
    └────┬─────────┘   • Transition → Approval
         │
         ↓
    ┌──────────────┐
    │  APPROVAL    │ ← Final Sign-Off
    │   Status     │   • Final Review
    │ "Approval"   │   • Approve or Reject
    └────┬─────────┘   • Export (DOCX/PDF)
         │             • Transition → Completed
         ↓
    ┌──────────────┐
    │ COMPLETED    │ ← Archived
    │   Status     │   • View Only
    │ "Completed"  │   • Download
    └──────────────┘
         ↑
         │ (if rejected)
    ┌────┴─────────┐
    │   REJECTED   │
    │   Status     │
    │  "Rejected"  │
    └──────────────┘
```

### 7.2 Transition Validation

Add to `backend/rbac_backend/utils/workflow_engine.py`:

```python
class WorkflowEngine:
    """Validates letter status transitions"""

    VALID_TRANSITIONS = {
        "Input": ["Strategy", "Rejected"],           # ← NEW: Strategy option
        "Strategy": ["Draft", "Input", "Rejected"],  # ← NEW: Can go back to Input
        "Draft": ["Review", "Strategy", "Rejected"],
        "Review": ["Approval", "Draft", "Rejected"],
        "Approval": ["Completed", "Review", "Rejected"],
        "Completed": [],  # Terminal state
        "Rejected": [],   # Terminal state
    }

    @staticmethod
    def is_valid_transition(
        current_status: str,
        target_status: str
    ) -> bool:
        """Check if transition is allowed"""
        allowed = WorkflowEngine.VALID_TRANSITIONS.get(current_status, [])
        return target_status in allowed

    @staticmethod
    def validate_transition(
        current_status: str,
        target_status: str
    ) -> None:
        """Raise exception if transition invalid"""
        if not WorkflowEngine.is_valid_transition(current_status, target_status):
            raise ValueError(
                f"Cannot transition from {current_status} to {target_status}"
            )
```

---

## 8. Time Tracking Updates

### 8.1 Duration Calculation Service

Update `backend/rbac_backend/services/time_tracker.py`:

```python
def calculate_durations(letter: dict) -> dict:
    """Calculate time spent in each stage"""

    durations = {}

    # Input stage
    if letter.get("input_received_at") and letter.get("draft_requested_at"):
        durations["duration_input"] = compute_hours(
            letter["draft_requested_at"],
            letter["input_received_at"]
        )

    # NEW: Strategy stage
    if letter.get("strategy_completed_at") and letter.get("input_received_at"):
        durations["duration_strategy"] = compute_hours(
            letter["input_received_at"],
            letter["strategy_completed_at"]
        )

    # Draft stage
    if letter.get("review_started_at") and letter.get("strategy_completed_at"):
        durations["duration_draft"] = compute_hours(
            letter["strategy_completed_at"],
            letter["review_started_at"]
        )

    # Review stage
    if letter.get("approved_at") and letter.get("review_started_at"):
        durations["duration_review"] = compute_hours(
            letter["review_started_at"],
            letter["approved_at"]
        )

    # Approval stage
    if letter.get("finalized_at") and letter.get("approved_at"):
        durations["duration_approval"] = compute_hours(
            letter["approved_at"],
            letter["finalized_at"]
        )

    # Total
    if letter.get("finalized_at") and letter.get("draft_requested_at"):
        durations["duration_total"] = compute_hours(
            letter["draft_requested_at"],
            letter["finalized_at"]
        )

    return durations
```

---

## 9. Analytics Updates

### 9.1 Updated Analytics Metrics

Update analytics in `backend/rbac_backend/routers/analytics.py`:

```python
@router.get("/analytics/{project_id}")
async def get_project_analytics(project_id: str):
    """Get project-level analytics including new Strategy stage"""

    analytics = {
        "project_id": project_id,
        "letters": [],
        "stage_distribution": {
            "Input": 0,
            "Strategy": 0,      # ← NEW
            "Draft": 0,
            "Review": 0,
            "Approval": 0,
            "Completed": 0,
            "Rejected": 0,
        },
        "average_durations": {
            "input": 0,
            "strategy": 0,      # ← NEW
            "draft": 0,
            "review": 0,
            "approval": 0,
            "total": 0,
        },
        "pending_items": {
            "in_input": 0,
            "in_strategy": 0,   # ← NEW
            "in_draft": 0,
            "in_review": 0,
            "in_approval": 0,
        },
    }

    # ... calculate metrics
    return analytics
```

---

## 10. Integration Checklist

### Database
- [ ] Add new fields to Letter schema (strategy_plan, strategy_completed_at, duration_strategy, etc.)
- [ ] Create migration script for existing letters
- [ ] Add indexes on status field for faster queries

### Backend
- [ ] Add `strategy_plan_impl.py` router with new endpoints
- [ ] Implement Strategy stage transition validation
- [ ] Update WorkflowEngine with new transitions
- [ ] Update time tracking calculations
- [ ] Update analytics endpoint
- [ ] Add strategy plan generation tests

### Frontend
- [ ] Update LetterStatus type to include "Strategy"
- [ ] Update LetterWorkflowTabs with Strategy tab
- [ ] Create new LetterStrategyPage component
- [ ] Create StrategyPlanDisplay component
- [ ] Create useLanggraphStrategyPlan hook
- [ ] Update routing configuration
- [ ] Update LettersTable navigation
- [ ] Update LetterInputPage with transition to Strategy

### Components
- [ ] Ensure LinkedDocumentSelector works
- [ ] Ensure BackgroundSummary displays correctly
- [ ] Ensure StrategyPlanDisplay renders all sections
- [ ] Add loading states and error handling

### Testing
- [ ] Test workflow transitions (Input → Strategy → Draft)
- [ ] Test strategy plan generation
- [ ] Test document selection
- [ ] Test time tracking across stages
- [ ] Test analytics calculations
- [ ] E2E test complete workflow

---

## 11. Summary of Changes

### New Components
✅ `LetterStrategyPage.tsx` - Strategy stage page
✅ `StrategyPlanDisplay.tsx` - Displays structured plan
✅ `useLanggraphStrategyPlan.ts` - Hook for strategy generation

### Updated Components
✅ `LetterWorkflowTabs.tsx` - Added Strategy tab
✅ `LettersTable.tsx` - Updated navigation routes
✅ `LetterInputPage.tsx` - Transition to Strategy
✅ `types.ts` - Added "Strategy" status

### New Database Fields
✅ `strategy_plan` - Full strategic plan text
✅ `strategy_completed_at` - Completion timestamp
✅ `duration_strategy` - Time in strategy stage
✅ `strategy_graph_status` - AI pipeline status
✅ `strategy_run_id` - LangGraph run identifier

### New Workflow States
✅ **Input** → **Strategy** (new transition)
✅ **Strategy** → **Draft** (new transition)
✅ Allow backward transitions for iteration

### New API Endpoints
✅ `POST /api/ai-assistant/langgraph/strategy-plan` - Generate plan
✅ `POST /api/letters/{id}/move-to-strategy` - State transition

---

## 12. Next Steps

1. **Phase 1: Backend Scaffolding**
   - [ ] Add database fields
   - [ ] Implement strategy endpoints
   - [ ] Add workflow validation

2. **Phase 2: Frontend Structure**
   - [ ] Update tabs and routing
   - [ ] Create LetterStrategyPage
   - [ ] Wire up navigation

3. **Phase 3: AI Integration**
   - [ ] Implement strategy plan generation
   - [ ] Test LLM outputs
   - [ ] Fine-tune prompts

4. **Phase 4: Testing & Polish**
   - [ ] E2E workflow testing
   - [ ] Performance optimization
   - [ ] Error handling

---

## Conclusion

This integration adds a **structured strategic planning stage** to the letter workflow, positioned between **Input** and **Draft** stages. It provides:

✅ **Clear Strategic Phase** - Separate concerns between requirements gathering and drafting
✅ **AI-Powered Planning** - LangGraph generates structured plans with 5 key sections
✅ **Document Context** - Users select relevant documents to inform strategy
✅ **Time Tracking** - New duration metrics for strategy development
✅ **Flexible Workflows** - Can iterate back to Input or move forward to Draft

Ready to implement! 🚀
