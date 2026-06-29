import React, { useCallback, useEffect, useState } from "react";
import { Sparkles, Save, RotateCcw, AlertCircle, CheckCircle, Info } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import { useToast } from "@/hooks/use-toast";
import { joinApiUrl } from "@/config/api";
import { authenticatedFetch } from "@/services/http";

interface PromptConfig {
  prompt_key: string;
  label: string;
  description: string;
  version: number;
  supported_payload_schema: string[];
  template: string;
}

const STRATEGY_PROMPT_KEY = "letter_drafting.v2.strategy";
const DRAFT_PROMPT_KEY = "letter_drafting.v2.draft";

const DEFAULT_STRATEGY_TEMPLATE = `Prepare a strategy plan for a contractual letter.

Active workspace: {active_workspace}
Role: {role}
Recipient: {recipient}
Subject: {subject}
Recipient focus: {recipient_focus}

Current materials:
{current_materials}

Sources:
{sources}

You must first analyze the incoming letter and available sources fully. Do not proceed directly to drafting advice until the roadmap below is complete.

Return a structured roadmap with exactly these sections:
1. Incoming letter summary
2. Sender and subject verification
3. Letter reference number and date
4. Main issue classification
5. Requested action
6. Stated deadline
7. Contractual response deadline
8. Cited clauses
9. Clause correctness check
10. Clause applicability analysis
11. Counter-position or counter-clauses
12. Missing information
13. Recommended response strategy
14. Points the drafter must verify manually
15. Suggested structure for the reply letter

Rules:
- Identify whether the issue is a claim, delay, variation, payment, approval, dispute, notice, contractual compliance issue, request for information, or other issue type.
- For cited clauses, state whether each clause appears in the available contract sources, whether quoted wording is supported, whether the clause is applicable, whether the sender is relying on it correctly, whether counter-clauses exist, and whether legal/commercial review is required.
- If source material is insufficient for any verification, write "Not verified from available sources" and list it under manual drafter verification.
- Do not invent missing sender details, dates, references, deadlines, clauses, or contract wording.`;

const DEFAULT_DRAFT_TEMPLATE = `You are a Contract Correspondence AI Agent drafting strictly as {role}.

Active workspace: {active_workspace}
Recipient: {recipient}
Subject: {subject}
Recipient focus: {recipient_focus}

Current materials:
{current_materials}

Plan:
{plan}

Sources:
{sources}

Profile pattern:
{profile_pattern}

Rules:
- Use only the current materials and listed sources.
- Do not invent facts, dates, clause references, meetings, attachments, or legal conclusions.
- Cite clauses only when a clause source is listed.
- Prior correspondence is for history/style continuity only unless listed as fact evidence.
- Return exactly these headings in this order:
Draft Letter
Source Integrity Notes
Learning Update
- Omit Learning Update content unless finalized is true; if not finalized, write "N/A".`;

const REQUIRED_VARS = {
  [STRATEGY_PROMPT_KEY]: ["active_workspace", "role", "recipient", "subject", "recipient_focus", "current_materials", "sources"],
  [DRAFT_PROMPT_KEY]: ["role", "active_workspace", "recipient", "subject", "recipient_focus", "current_materials", "plan", "sources", "profile_pattern"]
};

export const PromptSettingsPanel: React.FC = () => {
  const [prompts, setPrompts] = useState<PromptConfig[]>([]);
  const [selectedKey, setSelectedKey] = useState<string>(STRATEGY_PROMPT_KEY);
  const [templateText, setTemplateText] = useState<string>("");
  const [loading, setLoading] = useState<boolean>(true);
  const [saving, setSaving] = useState<boolean>(false);
  
  const { toast } = useToast();
  
  const selectedPrompt = prompts.find(p => p.prompt_key === selectedKey);
  const requiredVariables = REQUIRED_VARS[selectedKey] || [];
  
  const fetchPrompts = useCallback(async (activePromptKey: string = STRATEGY_PROMPT_KEY) => {
    try {
      setLoading(true);
      const res = await authenticatedFetch(joinApiUrl("/ai-assistant/prompts"), {
        method: "GET"
      });
      if (!res.ok) throw new Error("Failed to load prompt templates");
      const data = await res.json();
      setPrompts(data);
      
      const active = data.find((p: PromptConfig) => p.prompt_key === activePromptKey);
      if (active) {
        setTemplateText(active.template);
      }
    } catch (err: any) {
      toast({
        title: "Error",
        description: err.message || "Failed to load prompts settings.",
        variant: "destructive"
      });
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => {
    void fetchPrompts(STRATEGY_PROMPT_KEY);
  }, [fetchPrompts]);

  const handleKeyChange = (key: string) => {
    setSelectedKey(key);
    const active = prompts.find(p => p.prompt_key === key);
    if (active) {
      setTemplateText(active.template);
    } else {
      setTemplateText("");
    }
  };

  const getMissingVariables = (text: string): string[] => {
    const missing: string[] = [];
    requiredVariables.forEach(variable => {
      const regex = new RegExp(`\\{\\s*${variable}\\s*\\}`);
      if (!regex.test(text)) {
        missing.push(variable);
      }
    });
    return missing;
  };

  const missingVariables = getMissingVariables(templateText);

  const handleSave = async () => {
    if (missingVariables.length > 0) {
      toast({
        title: "Validation Error",
        description: `Missing required placeholder tags: ${missingVariables.map(v => `{${v}}`).join(", ")}`,
        variant: "destructive"
      });
      return;
    }
    
    try {
      setSaving(true);
      const res = await authenticatedFetch(joinApiUrl(`/ai-assistant/prompts/${selectedKey}`), {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ template: templateText })
      });
      
      if (!res.ok) {
        const errData = await res.json();
        throw new Error(errData?.detail || "Failed to update prompt template");
      }
      
      toast({
        title: "Success",
        description: "Prompt template updated successfully.",
      });
      
      await fetchPrompts(selectedKey);
    } catch (err: any) {
      toast({
        title: "Error",
        description: err.message || "Failed to save template.",
        variant: "destructive"
      });
    } finally {
      setSaving(false);
    }
  };

  const handleRestoreDefault = () => {
    const defaultTemplate = selectedKey === STRATEGY_PROMPT_KEY ? DEFAULT_STRATEGY_TEMPLATE : DEFAULT_DRAFT_TEMPLATE;
    setTemplateText(defaultTemplate);
    toast({
      title: "Restored",
      description: "Restored default template values in editor. Hit Save to persist."
    });
  };

  if (loading) {
    return <div className="text-sm text-muted-foreground p-4">Loading letter drafting prompts...</div>;
  }

  return (
    <div className="space-y-4">
      <Card className="border border-border">
        <CardHeader className="pb-4">
          <CardTitle className="flex items-center gap-2 text-foreground">
            <Sparkles className="h-5 w-5 text-primary" />
            Letter Drafting Prompts
          </CardTitle>
          <CardDescription>
            Configure raw prompts used by AI Agents to plan and draft correspondence reply templates.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-6">
          <div className="flex flex-col gap-2">
            <Label className="text-foreground">Select Prompt Blueprint</Label>
            <div className="flex gap-2">
              <Button
                variant={selectedKey === STRATEGY_PROMPT_KEY ? "default" : "outline"}
                onClick={() => handleKeyChange(STRATEGY_PROMPT_KEY)}
                className="flex-1 sm:flex-initial"
              >
                Strategy Plan Prompt
              </Button>
              <Button
                variant={selectedKey === DRAFT_PROMPT_KEY ? "default" : "outline"}
                onClick={() => handleKeyChange(DRAFT_PROMPT_KEY)}
                className="flex-1 sm:flex-initial"
              >
                AI Drafting Prompt
              </Button>
            </div>
          </div>

          {selectedPrompt && (
            <div className="space-y-4">
              <div className="bg-muted/40 p-3 rounded-lg border text-sm text-muted-foreground space-y-1">
                <p className="font-semibold text-foreground">{selectedPrompt.label}</p>
                <p>{selectedPrompt.description}</p>
                <p className="text-xs pt-1">
                  Active Database Version: <Badge variant="secondary" className="px-1.5 py-0.5 text-xs font-mono">v{selectedPrompt.version}</Badge>
                </p>
              </div>

              <div className="flex flex-col gap-2">
                <div className="flex items-center justify-between">
                  <Label className="text-foreground">Prompt Template Editor</Label>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="text-xs text-muted-foreground hover:text-foreground"
                    onClick={handleRestoreDefault}
                  >
                    <RotateCcw className="h-3 w-3 mr-1.5" />
                    Restore Default
                  </Button>
                </div>
                
                <Textarea
                  value={templateText}
                  onChange={(e) => setTemplateText(e.target.value)}
                  className="font-mono text-sm min-h-[360px] bg-slate-950 text-slate-100 border-slate-800 p-4 focus:ring-1 focus:ring-primary rounded-md leading-relaxed resize-y"
                  placeholder="Enter prompt template..."
                />
              </div>

              <div className="space-y-3">
                <Label className="text-foreground">Template Tag Helpers</Label>
                <div className="flex flex-wrap gap-1.5">
                  {requiredVariables.map(variable => {
                    const isPresent = templateText.includes(`{${variable}}`);
                    return (
                      <Badge
                        key={variable}
                        variant={isPresent ? "outline" : "destructive"}
                        className={`text-xs px-2 py-0.5 font-mono ${isPresent ? "border-emerald-500/30 bg-emerald-500/5 text-emerald-500 hover:bg-emerald-500/10" : "bg-red-500/10 text-red-500 border-red-500/20"}`}
                      >
                        {isPresent ? (
                          <CheckCircle className="h-3 w-3 mr-1 inline-block" />
                        ) : (
                          <AlertCircle className="h-3 w-3 mr-1 inline-block" />
                        )}
                        {`{${variable}}`}
                      </Badge>
                    );
                  })}
                </div>
                
                {missingVariables.length > 0 ? (
                  <div className="flex gap-2 items-start text-xs border border-red-500/20 bg-red-500/5 p-3 rounded-lg text-red-500 leading-normal">
                    <AlertCircle className="h-4 w-4 shrink-0 mt-0.5" />
                    <div>
                      <p className="font-semibold">Validation Guard Triggered</p>
                      <p>You cannot save. The template must include all required variable place-holders: {missingVariables.map(v => `{${v}}`).join(", ")}.</p>
                    </div>
                  </div>
                ) : (
                  <div className="flex gap-2 items-start text-xs border border-emerald-500/20 bg-emerald-500/5 p-3 rounded-lg text-emerald-500 leading-normal animate-fade-in">
                    <CheckCircle className="h-4 w-4 shrink-0 mt-0.5" />
                    <div>
                      <p className="font-semibold">Template Verified</p>
                      <p>All required dynamic variable place-holders are present and correctly formatted.</p>
                    </div>
                  </div>
                )}
                
                {selectedKey === STRATEGY_PROMPT_KEY && (
                  <div className="flex gap-2 items-start text-xs border border-blue-500/20 bg-blue-500/5 p-3 rounded-lg text-blue-500 leading-normal">
                    <Info className="h-4 w-4 shrink-0 mt-0.5" />
                    <div>
                      <p className="font-semibold">Roadmap Rules Note</p>
                      <p>Strategic Plan templates automatically undergo strict enforcement of the 15 required strategic roadmap sections before validation check updates.</p>
                    </div>
                  </div>
                )}
              </div>

              <div className="flex justify-end pt-2">
                <Button
                  onClick={handleSave}
                  disabled={saving || missingVariables.length > 0}
                  className="px-6 flex items-center gap-2"
                >
                  {saving ? (
                    <span className="animate-spin rounded-full h-4 w-4 border-2 border-current border-t-transparent" />
                  ) : (
                    <Save className="h-4 w-4" />
                  )}
                  Save Prompt
                </Button>
              </div>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
};

export default PromptSettingsPanel;
