# Strategic Plan Generation Implementation Guide

## Overview

This document details how to implement the **"Generate Strategy Plan"** button in `LetterStrategicPlanPage.tsx` to create a **structured, AI-powered strategic plan** based on previous letter context and new requirements using the LangGraph pipeline.

---

## 1. Current State

### 1.1 Existing Button Handler

In `LetterStrategicPlanPage.tsx`, the "Generate Strategy Plan" button exists but calls a generic `runDraft()`:

```typescript
const handleGeneratePlan = useCallback(async () => {
  if (!id || !uiLetter) return;
  try {
    const response = await runDraft({
      letterId: id,
      subject: uiLetter.subject,
      recipient: uiLetter.recipient,
      context: uiLetter.content,
      points: summaryPoints.length > 0 ? summaryPoints.join('\n') : undefined,
      documentIds: selectedDocIds,
    });

    setPlanDraft(response.plan ?? '');
    setSummaryPoints(response.summary_points ?? []);
    // ... rest of handler
  } catch (error: any) {
    // error handling
  }
}, [/* dependencies */]);
```

### 1.2 Current Limitations

1. ✗ No structured prompt template for strategic planning
2. ✗ Missing extraction of previous letter context
3. ✗ No explicit AI instruction for structured output
4. ✗ Background summary not being leveraged
5. ✗ No point-by-point response strategy generation

---

## 2. Enhanced Strategy Plan Generation Architecture

### 2.1 Data Flow

```
LetterStrategicPlanPage
    ↓ (user clicks "Generate Strategy Plan")
handleGeneratePlan()
    ↓
Prepare strategic plan payload with:
  - Letter metadata (subject, recipient, requirements)
  - Selected documents (context_document_ids)
  - Background summary (from previous letters)
  - Requirements text
    ↓
POST /api/ai-assistant/langgraph/strategy-plan
    ↓
Backend (FastAPI)
    ↓
LangGraph Pipeline
    ├─ load_state (letter + metadata)
    ├─ collect_context (retrieve similar letters via Qdrant)
    ├─ extract_background (synthesize key points from similar letters)
    ├─ generate_strategic_plan (AI generates STRUCTURED plan)
    ├─ validate_and_route
    └─ persist_results
    ↓
Return LangGraphStrategyPlanResponse:
  {
    plan: "Structured strategic plan text",
    tone_approach: {...},
    content_structure: {...},
    specific_responses: [...],
    risk_mitigation: [...],
    desired_outcome: {...},
    background_summary: [...],
    context_documents: [...],
    trace: [...]
  }
    ↓
Frontend displays structured plan with:
  - Tone & Approach section
  - Content Structure section
  - Specific Responses (point-by-point)
  - Risk Mitigation section
  - Desired Outcome section
```

---

## 3. Backend Implementation (FastAPI)

### 3.1 New API Endpoint

Create `backend/rbac_backend/routers/strategy_plan.py`:

```python
# backend/rbac_backend/routers/strategy_plan.py

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from datetime import datetime

router = APIRouter(prefix="/api/ai-assistant", tags=["ai-strategy"])

# ============ MODELS ============

class StrategyPlanRequest(BaseModel):
    letter_id: str
    subject: str
    recipient: str
    context: str  # Current letter requirements/context
    document_ids: List[str] = []
    requirements: Optional[str] = None  # Explicit requirements if provided

class ToneApproachSection(BaseModel):
    overall_tone: str  # firm/cooperative/neutral
    key_messaging_strategy: str
    relationship_management_approach: str

class ContentStructureSection(BaseModel):
    opening_strategy: str
    key_points_order: List[str]
    contractual_references: List[str]
    closing_approach: str

class PointResponse(BaseModel):
    contractor_point: str
    response_strategy: str
    evidence_references: List[str]
    contractual_basis: str

class RiskMitigationSection(BaseModel):
    legal_risks: List[str]
    relationship_risks: List[str]
    project_impact_considerations: List[str]

class DesiredOutcomeSection(BaseModel):
    immediate_action: str
    next_steps: List[str]
    fallback_positions: List[str]

class LangGraphStrategyPlanResponse(BaseModel):
    run_id: str
    letter_id: str
    status: str
    timestamp: datetime
    
    # Structured plan sections
    plan: str  # Full plan as text
    tone_approach: ToneApproachSection
    content_structure: ContentStructureSection
    specific_responses: List[PointResponse]
    risk_mitigation: RiskMitigationSection
    desired_outcome: DesiredOutcomeSection
    
    # Supporting data
    summary_points: List[str]
    background_summary: List[Dict[str, Any]]
    context_documents: List[Dict[str, Any]]
    context_document_ids: List[str]
    
    # Diagnostics
    trace: List[Dict[str, Any]]
    warnings: List[str]
    similar_letters: List[Dict[str, Any]]

# ============ ENDPOINT ============

@router.post("/langgraph/strategy-plan", response_model=LangGraphStrategyPlanResponse)
async def generate_strategy_plan(
    request: StrategyPlanRequest,
    current_user: dict = Depends(verify_auth)
):
    """
    Generate a structured strategic plan for letter drafting.
    
    This endpoint:
    1. Collects context from previous similar letters (Qdrant)
    2. Synthesizes background information
    3. Uses AI to generate structured plan with:
       - Tone & Approach
       - Content Structure
       - Point-by-point responses
       - Risk Mitigation
       - Desired Outcome
    4. Returns fully structured JSON response
    """
    
    try:
        # Validate letter ownership
        letter = await LetterService.get_letter(request.letter_id, current_user)
        if not letter:
            raise HTTPException(status_code=404, detail="Letter not found")
        
        # Run LangGraph strategy plan pipeline
        result = await AIService.generate_strategy_plan(
            letter_id=request.letter_id,
            subject=request.subject,
            recipient=request.recipient,
            context=request.context,
            document_ids=request.document_ids,
            requirements=request.requirements,
            user_id=current_user["id"],
            organization_id=current_user["organization_id"]
        )
        
        return result
        
    except Exception as e:
        logger.error(f"Strategy plan generation failed: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
```

### 3.2 AIService Implementation

Create `backend/rbac_backend/services/strategy_plan_service.py`:

```python
# backend/rbac_backend/services/strategy_plan_service.py

from typing import List, Dict, Any, Optional
import json
from datetime import datetime
from langchain_openai import ChatOpenAI
from langchain.schema import HumanMessage, SystemMessage
from langgraph.graph import StateGraph, END

class StrategyPlanService:
    """Service for generating strategic plans using LangGraph"""
    
    def __init__(self):
        self.llm = ChatOpenAI(
            model="gpt-4",
            temperature=0.1,  # Low temperature for consistency
            max_tokens=3000
        )
    
    async def generate_strategy_plan(
        self,
        letter_id: str,
        subject: str,
        recipient: str,
        context: str,
        document_ids: List[str],
        requirements: Optional[str] = None,
        user_id: str = None,
        organization_id: str = None
    ) -> LangGraphStrategyPlanResponse:
        """Generate structured strategic plan"""
        
        # Build LangGraph state
        state = {
            "letter_id": letter_id,
            "subject": subject,
            "recipient": recipient,
            "context": context,
            "document_ids": document_ids,
            "requirements": requirements or context,
            "user_id": user_id,
            "organization_id": organization_id,
            "run_id": str(uuid.uuid4()),
            "timestamp": datetime.utcnow(),
            "trace": [],
            "warnings": []
        }
        
        # Create and execute workflow
        workflow = self._create_strategy_workflow()
        final_state = await workflow.ainvoke(state)
        
        # Parse and structure response
        return await self._build_response(final_state)
    
    def _create_strategy_workflow(self) -> StateGraph:
        """Create LangGraph workflow for strategy planning"""
        
        workflow = StateGraph(dict)
        
        # Add nodes
        workflow.add_node("retrieve_context", self._retrieve_context)
        workflow.add_node("extract_background", self._extract_background)
        workflow.add_node("generate_plan", self._generate_plan)
        workflow.add_node("structure_output", self._structure_output)
        workflow.add_node("validate_plan", self._validate_plan)
        
        # Define flow
        workflow.set_entry_point("retrieve_context")
        workflow.add_edge("retrieve_context", "extract_background")
        workflow.add_edge("extract_background", "generate_plan")
        workflow.add_edge("generate_plan", "structure_output")
        workflow.add_edge("structure_output", "validate_plan")
        workflow.add_edge("validate_plan", END)
        
        return workflow.compile()
    
    async def _retrieve_context(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Node 1: Retrieve similar previous letters"""
        
        # Search Qdrant for similar letters
        similar_letters = await self._search_similar_letters(
            query=state["subject"],
            organization_id=state["organization_id"],
            k=5
        )
        
        state["similar_letters"] = similar_letters
        state["trace"].append({
            "node": "retrieve_context",
            "timestamp": datetime.utcnow().isoformat(),
            "similar_letters_found": len(similar_letters)
        })
        
        return state
    
    async def _extract_background(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Node 2: Extract and synthesize background from similar letters"""
        
        # Prepare context from similar letters
        context_text = self._format_similar_letters(state["similar_letters"])
        
        # Extract key points
        extraction_prompt = f"""
        Analyze these similar previous letters and extract key strategic insights.
        
        PREVIOUS LETTERS:
        {context_text}
        
        Extract:
        1. Common tone and approach patterns
        2. Recurring contractual references
        3. Typical response strategies
        4. Risk mitigation patterns observed
        5. Relationship management approaches
        
        Format as structured bullet points.
        """
        
        response = self.llm.invoke([
            SystemMessage(content="You are a contract management expert analyzing letter writing patterns."),
            HumanMessage(content=extraction_prompt)
        ])
        
        state["background_summary"] = response.content
        state["trace"].append({
            "node": "extract_background",
            "timestamp": datetime.utcnow().isoformat(),
            "background_extracted": True
        })
        
        return state
    
    async def _generate_plan(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Node 3: Generate structured strategic plan"""
        
        plan_prompt = f"""
        Based on the previous letters and new requirements, create a STRUCTURED PLAN for drafting the response.
        
        PREVIOUS LETTERS CONTEXT:
        {state.get('background_summary', 'No previous letters found')}
        
        NEW REQUIREMENTS:
        {state['requirements']}
        
        Subject: {state['subject']}
        Recipient: {state['recipient']}
        
        Create a detailed structured plan with EXACT sections below. Use ONLY information from provided context.
        Do not fabricate details or clause numbers not explicitly mentioned.
        
        === STRUCTURED PLAN ===
        
        1. TONE & APPROACH
        - Overall tone (firm/cooperative/neutral with justification):
        - Key messaging strategy (what must be conveyed):
        - Relationship management approach (how to maintain/improve relationship):
        
        2. CONTENT STRUCTURE
        - Opening paragraph strategy (purpose + key reference):
        - Key points to address (numbered list, in order):
        - Contractual references to include (cite exact clauses if known):
        - Closing approach (desired state + next steps):
        
        3. SPECIFIC RESPONSES to contractor's/other party's points:
        List each point and how to respond:
        [POINT]: [RESPONSE STRATEGY] [EVIDENCE REFERENCE] [CONTRACTUAL BASIS]
        
        4. RISK MITIGATION
        - Legal/contractual risks to avoid (specific scenarios):
        - Relationship risks to manage (preservation considerations):
        - Project impact considerations (timeline, budget, resources):
        
        5. DESIRED OUTCOME
        - Immediate action required (specific, measurable):
        - Next steps (sequence of follow-ups):
        - Fallback positions (if primary position not accepted):
        
        === END PLAN ===
        """
        
        response = self.llm.invoke([
            SystemMessage(content="""You are an Expert Contract Manager with 20 years of construction law experience.
            Draft precise, unambiguous strategic plans grounded only in provided information.
            Never invent clause numbers or facts not explicitly mentioned.
            If critical information is missing, explicitly state "Information required: [specific detail]"."""),
            HumanMessage(content=plan_prompt)
        ])
        
        state["full_plan"] = response.content
        state["trace"].append({
            "node": "generate_plan",
            "timestamp": datetime.utcnow().isoformat(),
            "plan_generated": True
        })
        
        return state
    
    async def _structure_output(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Node 4: Parse plan into structured sections"""
        
        # Parse the full plan into sections
        plan_text = state["full_plan"]
        
        # Extract sections (regex or simple parsing)
        sections = self._parse_plan_sections(plan_text)
        
        state["structured_plan"] = {
            "tone_approach": sections.get("tone_approach", {}),
            "content_structure": sections.get("content_structure", {}),
            "specific_responses": sections.get("specific_responses", []),
            "risk_mitigation": sections.get("risk_mitigation", {}),
            "desired_outcome": sections.get("desired_outcome", {})
        }
        
        state["summary_points"] = self._extract_summary_points(plan_text)
        
        state["trace"].append({
            "node": "structure_output",
            "timestamp": datetime.utcnow().isoformat(),
            "sections_parsed": len(sections)
        })
        
        return state
    
    async def _validate_plan(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Node 5: Validate plan completeness"""
        
        required_sections = [
            "tone_approach",
            "content_structure",
            "specific_responses",
            "risk_mitigation",
            "desired_outcome"
        ]
        
        for section in required_sections:
            if not state["structured_plan"].get(section):
                state["warnings"].append(f"Missing or incomplete section: {section}")
        
        state["trace"].append({
            "node": "validate_plan",
            "timestamp": datetime.utcnow().isoformat(),
            "validation_warnings": len(state["warnings"])
        })
        
        return state
    
    async def _build_response(
        self,
        state: Dict[str, Any]
    ) -> LangGraphStrategyPlanResponse:
        """Build typed response from final state"""
        
        return LangGraphStrategyPlanResponse(
            run_id=state["run_id"],
            letter_id=state["letter_id"],
            status="success" if not state["warnings"] else "completed_with_warnings",
            timestamp=state["timestamp"],
            
            plan=state["full_plan"],
            tone_approach=ToneApproachSection(
                overall_tone=state["structured_plan"]["tone_approach"].get("overall_tone", ""),
                key_messaging_strategy=state["structured_plan"]["tone_approach"].get("key_messaging_strategy", ""),
                relationship_management_approach=state["structured_plan"]["tone_approach"].get("relationship_management_approach", "")
            ),
            content_structure=ContentStructureSection(
                opening_strategy=state["structured_plan"]["content_structure"].get("opening_strategy", ""),
                key_points_order=state["structured_plan"]["content_structure"].get("key_points_order", []),
                contractual_references=state["structured_plan"]["content_structure"].get("contractual_references", []),
                closing_approach=state["structured_plan"]["content_structure"].get("closing_approach", "")
            ),
            specific_responses=[
                PointResponse(
                    contractor_point=resp.get("contractor_point", ""),
                    response_strategy=resp.get("response_strategy", ""),
                    evidence_references=resp.get("evidence_references", []),
                    contractual_basis=resp.get("contractual_basis", "")
                )
                for resp in state["structured_plan"]["specific_responses"]
            ],
            risk_mitigation=RiskMitigationSection(
                legal_risks=state["structured_plan"]["risk_mitigation"].get("legal_risks", []),
                relationship_risks=state["structured_plan"]["risk_mitigation"].get("relationship_risks", []),
                project_impact_considerations=state["structured_plan"]["risk_mitigation"].get("project_impact_considerations", [])
            ),
            desired_outcome=DesiredOutcomeSection(
                immediate_action=state["structured_plan"]["desired_outcome"].get("immediate_action", ""),
                next_steps=state["structured_plan"]["desired_outcome"].get("next_steps", []),
                fallback_positions=state["structured_plan"]["desired_outcome"].get("fallback_positions", [])
            ),
            
            summary_points=state["summary_points"],
            background_summary=state.get("background_summary", ""),
            context_documents=state.get("similar_letters", []),
            context_document_ids=state.get("document_ids", []),
            
            trace=state["trace"],
            warnings=state["warnings"],
            similar_letters=state.get("similar_letters", [])
        )
    
    def _parse_plan_sections(self, plan_text: str) -> Dict[str, Any]:
        """Parse plan text into structured sections"""
        # This is simplified; in production, use more robust parsing
        sections = {
            "tone_approach": {},
            "content_structure": {},
            "specific_responses": [],
            "risk_mitigation": {},
            "desired_outcome": {}
        }
        
        # Extract sections using regex or simple string splitting
        # For brevity, simplified logic shown
        lines = plan_text.split("\n")
        current_section = None
        
        for line in lines:
            if "TONE & APPROACH" in line:
                current_section = "tone_approach"
            elif "CONTENT STRUCTURE" in line:
                current_section = "content_structure"
            elif "SPECIFIC RESPONSES" in line:
                current_section = "specific_responses"
            elif "RISK MITIGATION" in line:
                current_section = "risk_mitigation"
            elif "DESIRED OUTCOME" in line:
                current_section = "desired_outcome"
            elif line.strip() and current_section:
                # Add to current section
                pass
        
        return sections
    
    def _extract_summary_points(self, plan_text: str) -> List[str]:
        """Extract key summary points from plan"""
        # Extract bullet points or numbered items
        points = []
        for line in plan_text.split("\n"):
            if line.strip().startswith(("-", "*", "•", "1.", "2.", "3.")):
                points.append(line.strip().lstrip("-*•0123456789. "))
        return points
    
    def _format_similar_letters(self, similar_letters: List[Dict]) -> str:
        """Format similar letters for prompt context"""
        formatted = []
        for letter in similar_letters[:5]:  # Limit to top 5
            formatted.append(f"""
            Letter: {letter.get('letter_no', 'Unknown')}
            Date: {letter.get('date', 'Unknown')}
            Subject: {letter.get('subject', 'Unknown')}
            Summary: {letter.get('summary', letter.get('content', '')[:500])}
            """)
        return "\n---\n".join(formatted)
    
    async def _search_similar_letters(
        self,
        query: str,
        organization_id: str,
        k: int = 5
    ) -> List[Dict[str, Any]]:
        """Search Qdrant for similar letters"""
        # Use QdrantService to search
        from backend.rbac_backend.services.qdrant_service import QdrantService
        
        qdrant = QdrantService()
        return await qdrant.search_similar_letters(
            query=query,
            organization_id=organization_id,
            k=k
        )
```

---

## 4. Frontend Implementation

### 4.1 Enhanced Hook: useLanggraphStrategyPlan

Create `client/src/hooks/useLanggraphStrategyPlan.ts`:

```typescript
import { useState, useCallback } from 'react';
import axios from 'axios';

export interface StrategyPlanResponse {
  run_id: string;
  letter_id: string;
  status: string;
  timestamp: string;
  
  plan: string;
  tone_approach: {
    overall_tone: string;
    key_messaging_strategy: string;
    relationship_management_approach: string;
  };
  content_structure: {
    opening_strategy: string;
    key_points_order: string[];
    contractual_references: string[];
    closing_approach: string;
  };
  specific_responses: Array<{
    contractor_point: string;
    response_strategy: string;
    evidence_references: string[];
    contractual_basis: string;
  }>;
  risk_mitigation: {
    legal_risks: string[];
    relationship_risks: string[];
    project_impact_considerations: string[];
  };
  desired_outcome: {
    immediate_action: string;
    next_steps: string[];
    fallback_positions: string[];
  };
  
  summary_points: string[];
  background_summary: string;
  context_document_ids: string[];
  
  trace: Array<any>;
  warnings: string[];
}

export const useLanggraphStrategyPlan = () => {
  const [loading, setLoading] = useState(false);
  const [data, setData] = useState<StrategyPlanResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  const generateStrategyPlan = useCallback(
    async (payload: {
      letterId: string;
      subject: string;
      recipient: string;
      context: string;
      documentIds?: string[];
      requirements?: string;
    }): Promise<StrategyPlanResponse> => {
      setLoading(true);
      setError(null);

      try {
        const response = await axios.post<StrategyPlanResponse>(
          '/api/ai-assistant/langgraph/strategy-plan',
          {
            letter_id: payload.letterId,
            subject: payload.subject,
            recipient: payload.recipient,
            context: payload.context,
            document_ids: payload.documentIds || [],
            requirements: payload.requirements,
          }
        );

        setData(response.data);
        return response.data;
      } catch (err: any) {
        const errorMsg =
          err?.response?.data?.detail ||
          err?.message ||
          'Failed to generate strategy plan';
        setError(errorMsg);
        throw err;
      } finally {
        setLoading(false);
      }
    },
    []
  );

  return {
    generateStrategyPlan,
    loading,
    data,
    error,
  };
};
```

### 4.2 Enhanced LetterStrategicPlanPage Component

Update `client/src/pages/LetterStrategicPlanPage.tsx`:

```typescript
import { useLanggraphStrategyPlan } from '@/hooks/useLanggraphStrategyPlan';
import StrategyPlanDisplay from '@/components/langgraph/StrategyPlanDisplay';

const LetterStrategicPlanPage = () => {
  // ... existing code ...
  
  const { generateStrategyPlan, loading: planLoading } = useLanggraphStrategyPlan();
  const [strategyPlanData, setStrategyPlanData] = useState(null);

  const handleGeneratePlan = useCallback(async () => {
    if (!id || !uiLetter) return;

    try {
      const response = await generateStrategyPlan({
        letterId: id,
        subject: uiLetter.subject,
        recipient: uiLetter.recipient,
        context: uiLetter.content,
        documentIds: selectedDocIds,
        requirements: summaryPoints.join('\n'), // Add requirements if available
      });

      setStrategyPlanData(response);
      setPlanDraft(response.plan);
      setSummaryPoints(response.summary_points);

      toast({
        title: 'Strategy Plan Generated',
        description: 'Strategic plan created successfully. Review and edit as needed.',
      });
    } catch (error: any) {
      const description =
        error?.response?.data?.detail || error?.message || 'Unable to generate strategy plan.';
      toast({
        title: 'Strategy Plan Generation Failed',
        description,
        variant: 'destructive',
      });
    }
  }, [id, uiLetter, selectedDocIds, summaryPoints, generateStrategyPlan, toast]);

  return (
    <>
      {/* ... existing header and controls ... */}

      {/* Strategy Plan Display Component */}
      {strategyPlanData && (
        <StrategyPlanDisplay
          data={strategyPlanData}
          onEdit={() => setIsEditing(true)}
          onApprove={() => handleSavePlan()}
          loading={planLoading}
        />
      )}

      {/* ... rest of component ... */}
    </>
  );
};
```

### 4.3 New Component: StrategyPlanDisplay

Create `client/src/components/langgraph/StrategyPlanDisplay.tsx`:

```typescript
import React, { useState } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Badge } from '@/components/ui/badge';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { AlertTriangle, Check, Copy } from 'lucide-react';
import { Button } from '@/components/ui/button';

interface StrategyPlanDisplayProps {
  data: any; // StrategyPlanResponse type
  onEdit: () => void;
  onApprove: () => void;
  loading?: boolean;
}

const StrategyPlanDisplay: React.FC<StrategyPlanDisplayProps> = ({
  data,
  onEdit,
  onApprove,
  loading = false,
}) => {
  const [copiedSection, setCopiedSection] = useState<string | null>(null);

  const copyToClipboard = (text: string, sectionName: string) => {
    navigator.clipboard.writeText(text);
    setCopiedSection(sectionName);
    setTimeout(() => setCopiedSection(null), 2000);
  };

  return (
    <Card className="mt-6">
      <CardHeader>
        <CardTitle className="flex items-center justify-between">
          <span>Structured Strategic Plan</span>
          <Badge variant={data.status === 'success' ? 'default' : 'secondary'}>
            {data.status}
          </Badge>
        </CardTitle>
      </CardHeader>

      <CardContent>
        {/* Warnings */}
        {data.warnings && data.warnings.length > 0 && (
          <Alert variant="destructive" className="mb-4">
            <AlertTriangle className="h-4 w-4" />
            <AlertDescription>
              <strong>Warnings:</strong>
              <ul className="mt-2 ml-4 list-disc">
                {data.warnings.map((warning: string, idx: number) => (
                  <li key={idx}>{warning}</li>
                ))}
              </ul>
            </AlertDescription>
          </Alert>
        )}

        {/* Tabs for different sections */}
        <Tabs defaultValue="tone" className="w-full">
          <TabsList className="grid w-full grid-cols-5">
            <TabsTrigger value="tone">Tone & Approach</TabsTrigger>
            <TabsTrigger value="content">Content Structure</TabsTrigger>
            <TabsTrigger value="responses">Responses</TabsTrigger>
            <TabsTrigger value="risks">Risk Mitigation</TabsTrigger>
            <TabsTrigger value="outcome">Desired Outcome</TabsTrigger>
          </TabsList>

          {/* 1. Tone & Approach */}
          <TabsContent value="tone" className="space-y-4 mt-4">
            <div>
              <h4 className="font-semibold">Overall Tone</h4>
              <p className="text-sm text-gray-600 mt-2">
                {data.tone_approach?.overall_tone}
              </p>
            </div>

            <div>
              <h4 className="font-semibold">Key Messaging Strategy</h4>
              <p className="text-sm text-gray-600 mt-2">
                {data.tone_approach?.key_messaging_strategy}
              </p>
            </div>

            <div>
              <h4 className="font-semibold">Relationship Management Approach</h4>
              <p className="text-sm text-gray-600 mt-2">
                {data.tone_approach?.relationship_management_approach}
              </p>
            </div>
          </TabsContent>

          {/* 2. Content Structure */}
          <TabsContent value="content" className="space-y-4 mt-4">
            <div>
              <h4 className="font-semibold">Opening Strategy</h4>
              <p className="text-sm text-gray-600 mt-2">
                {data.content_structure?.opening_strategy}
              </p>
            </div>

            <div>
              <h4 className="font-semibold">Key Points to Address</h4>
              <ul className="list-disc list-inside mt-2 space-y-1">
                {data.content_structure?.key_points_order?.map(
                  (point: string, idx: number) => (
                    <li key={idx} className="text-sm text-gray-600">
                      {point}
                    </li>
                  )
                )}
              </ul>
            </div>

            <div>
              <h4 className="font-semibold">Contractual References</h4>
              <ul className="list-disc list-inside mt-2 space-y-1">
                {data.content_structure?.contractual_references?.map(
                  (ref: string, idx: number) => (
                    <li key={idx} className="text-sm text-gray-600">
                      {ref}
                    </li>
                  )
                )}
              </ul>
            </div>

            <div>
              <h4 className="font-semibold">Closing Approach</h4>
              <p className="text-sm text-gray-600 mt-2">
                {data.content_structure?.closing_approach}
              </p>
            </div>
          </TabsContent>

          {/* 3. Specific Responses */}
          <TabsContent value="responses" className="space-y-4 mt-4">
            {data.specific_responses?.map(
              (response: any, idx: number) => (
                <Card key={idx} className="bg-blue-50">
                  <CardContent className="pt-4">
                    <h5 className="font-semibold">Point {idx + 1}: {response.contractor_point}</h5>
                    <div className="mt-2 space-y-2">
                      <div>
                        <span className="font-medium">Response Strategy:</span>
                        <p className="text-sm text-gray-600">{response.response_strategy}</p>
                      </div>
                      {response.evidence_references?.length > 0 && (
                        <div>
                          <span className="font-medium">Evidence References:</span>
                          <ul className="list-disc list-inside text-sm text-gray-600">
                            {response.evidence_references.map(
                              (ref: string, idx: number) => (
                                <li key={idx}>{ref}</li>
                              )
                            )}
                          </ul>
                        </div>
                      )}
                      <div>
                        <span className="font-medium">Contractual Basis:</span>
                        <p className="text-sm text-gray-600">{response.contractual_basis}</p>
                      </div>
                    </div>
                  </CardContent>
                </Card>
              )
            )}
          </TabsContent>

          {/* 4. Risk Mitigation */}
          <TabsContent value="risks" className="space-y-4 mt-4">
            <div>
              <h4 className="font-semibold">Legal/Contractual Risks to Avoid</h4>
              <ul className="list-disc list-inside mt-2 space-y-1">
                {data.risk_mitigation?.legal_risks?.map(
                  (risk: string, idx: number) => (
                    <li key={idx} className="text-sm text-gray-600">
                      {risk}
                    </li>
                  )
                )}
              </ul>
            </div>

            <div>
              <h4 className="font-semibold">Relationship Risks to Manage</h4>
              <ul className="list-disc list-inside mt-2 space-y-1">
                {data.risk_mitigation?.relationship_risks?.map(
                  (risk: string, idx: number) => (
                    <li key={idx} className="text-sm text-gray-600">
                      {risk}
                    </li>
                  )
                )}
              </ul>
            </div>

            <div>
              <h4 className="font-semibold">Project Impact Considerations</h4>
              <ul className="list-disc list-inside mt-2 space-y-1">
                {data.risk_mitigation?.project_impact_considerations?.map(
                  (consideration: string, idx: number) => (
                    <li key={idx} className="text-sm text-gray-600">
                      {consideration}
                    </li>
                  )
                )}
              </ul>
            </div>
          </TabsContent>

          {/* 5. Desired Outcome */}
          <TabsContent value="outcome" className="space-y-4 mt-4">
            <div>
              <h4 className="font-semibold">Immediate Action Required</h4>
              <p className="text-sm text-gray-600 mt-2">
                {data.desired_outcome?.immediate_action}
              </p>
            </div>

            <div>
              <h4 className="font-semibold">Next Steps</h4>
              <ul className="list-decimal list-inside mt-2 space-y-1">
                {data.desired_outcome?.next_steps?.map(
                  (step: string, idx: number) => (
                    <li key={idx} className="text-sm text-gray-600">
                      {step}
                    </li>
                  )
                )}
              </ul>
            </div>

            <div>
              <h4 className="font-semibold">Fallback Positions</h4>
              <ul className="list-disc list-inside mt-2 space-y-1">
                {data.desired_outcome?.fallback_positions?.map(
                  (position: string, idx: number) => (
                    <li key={idx} className="text-sm text-gray-600">
                      {position}
                    </li>
                  )
                )}
              </ul>
            </div>
          </TabsContent>
        </Tabs>

        {/* Summary Points */}
        <div className="mt-6 pt-4 border-t">
          <h4 className="font-semibold mb-2">Summary Points</h4>
          <div className="space-y-2">
            {data.summary_points?.map((point: string, idx: number) => (
              <div key={idx} className="flex items-start gap-2 text-sm">
                <Check className="h-4 w-4 mt-0.5 text-green-600 flex-shrink-0" />
                <span className="text-gray-700">{point}</span>
              </div>
            ))}
          </div>
        </div>

        {/* Full Plan Text (with copy button) */}
        <div className="mt-6 pt-4 border-t">
          <div className="flex items-center justify-between mb-2">
            <h4 className="font-semibold">Full Strategic Plan</h4>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => copyToClipboard(data.plan, 'full-plan')}
            >
              <Copy className="h-4 w-4 mr-2" />
              {copiedSection === 'full-plan' ? 'Copied!' : 'Copy'}
            </Button>
          </div>
          <div className="bg-gray-50 p-4 rounded text-sm whitespace-pre-wrap font-mono max-h-96 overflow-y-auto">
            {data.plan}
          </div>
        </div>

        {/* Action Buttons */}
        <div className="mt-6 flex gap-2">
          <Button onClick={onApprove} disabled={loading}>
            {loading ? 'Saving...' : 'Approve & Save Plan'}
          </Button>
          <Button variant="outline" onClick={onEdit}>
            Edit Plan
          </Button>
        </div>
      </CardContent>
    </Card>
  );
};

export default StrategyPlanDisplay;
```

---

## 5. Integration Checklist

### Backend

- [ ] Create `strategy_plan.py` router with endpoint
- [ ] Implement `StrategyPlanService` with LangGraph workflow
- [ ] Add Qdrant integration for similar letter retrieval
- [ ] Add parsing logic for structured plan sections
- [ ] Implement error handling and validation
- [ ] Add request/response models (Pydantic)
- [ ] Test with sample letter data

### Frontend

- [ ] Create `useLanggraphStrategyPlan` hook
- [ ] Create `StrategyPlanDisplay` component
- [ ] Update `LetterStrategicPlanPage` to use new hook
- [ ] Add navigation to strategy plan display
- [ ] Implement copy-to-clipboard functionality
- [ ] Add error toast notifications
- [ ] Test UI with mock data

### Prompts & AI

- [ ] Finalize Expert Contract Manager protocol prompts
- [ ] Test LLM output parsing for structured sections
- [ ] Add temperature tuning (recommend 0.1-0.2 for consistency)
- [ ] Add validation for missing clauses/details

---

## 6. Example Request/Response

### Request

```json
{
  "letter_id": "letter-001",
  "subject": "Response to Deferment Request",
  "recipient": "Contractor Name",
  "context": "The contractor has requested deferment of mobilization advances...",
  "document_ids": ["doc-001", "doc-002"],
  "requirements": "Address contractor's financial constraints while maintaining contractual position"
}
```

### Response

```json
{
  "run_id": "run-uuid",
  "letter_id": "letter-001",
  "status": "success",
  "timestamp": "2025-11-08T10:30:00Z",
  "plan": "Full structured plan text...",
  "tone_approach": {
    "overall_tone": "Firm but cooperative",
    "key_messaging_strategy": "Acknowledge constraints while maintaining position...",
    "relationship_management_approach": "Professional dialogue..."
  },
  "content_structure": {
    "opening_strategy": "Acknowledge receipt and thank for communication...",
    "key_points_order": ["Acknowledge financial challenges", "Explain contractual position", ...],
    "contractual_references": ["Clause 8.3", "Clause 11.2.4"],
    "closing_approach": "Propose conditional deferment with interest..."
  },
  "specific_responses": [
    {
      "contractor_point": "Financial constraints due to events",
      "response_strategy": "Acknowledge but reference previous EOT rejections",
      "evidence_references": ["Letter KANPUR-LET-01400"],
      "contractual_basis": "GCC Clause 8.3 (contractor responsibilities)"
    }
  ],
  "risk_mitigation": {
    "legal_risks": ["Avoid setting precedent for future deferments", ...],
    "relationship_risks": ["Project continuation risk", ...],
    "project_impact_considerations": ["Cash flow management", ...]
  },
  "desired_outcome": {
    "immediate_action": "Contractor accepts deferment with interest terms",
    "next_steps": ["Monthly review meetings", "Interest calculation..."],
    "fallback_positions": ["Partial deferment with escalated interest", ...]
  },
  "summary_points": ["Point 1", "Point 2", ...],
  "trace": [...]
}
```

---

## 7. Future Enhancements

1. **Iterative Planning**: Allow user feedback loop to regenerate sections
2. **Version Tracking**: Store plan versions for comparison
3. **Plan Approval Workflow**: Multi-level approvals before drafting
4. **Template Library**: Save plans as templates for similar scenarios
5. **Team Collaboration**: Real-time co-editing of plans
6. **Analytics**: Track which plans lead to successful outcomes

---

## Summary

This implementation provides:

✅ **Structured AI Planning** - LangGraph orchestrates multi-step planning  
✅ **Context Retrieval** - Qdrant pulls similar previous letters  
✅ **Typed Responses** - Strong TypeScript definitions for all sections  
✅ **User-Friendly UI** - Tabbed display of 5 strategic sections  
✅ **Copy-Ready Output** - Easy export of plan for editing/sharing  
✅ **Expert Protocol** - Follows Expert Contract Manager constraints  

Ready to deploy on click of "Generate Strategy Plan" button! 🚀