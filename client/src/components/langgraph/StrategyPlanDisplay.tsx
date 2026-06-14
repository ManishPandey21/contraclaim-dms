import React, { useState } from "react";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Badge } from "@/components/ui/badge";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { AlertTriangle, Check, Copy, Edit3 } from "lucide-react";
import type { StrategyPlanResponse } from "@/types/strategyPlan";

interface StrategyPlanDisplayProps {
  data: StrategyPlanResponse;
  onEdit?: () => void;
  onApprove?: () => void;
  loading?: boolean;
}

const StrategyPlanDisplay: React.FC<StrategyPlanDisplayProps> = ({
  data,
  onEdit,
  onApprove,
  loading = false,
}) => {
  const [copiedSection, setCopiedSection] = useState<string | null>(null);

  const copyText = async (text: string, label: string) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopiedSection(label);
      setTimeout(() => setCopiedSection(null), 2000);
    } catch (error) {
      console.error("Failed to copy plan section", error);
    }
  };

  const renderList = (items?: string[]) =>
    items && items.length > 0 ? (
      <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-muted-foreground">
        {items.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
    ) : (
      <p className="text-sm text-muted-foreground">No entries provided.</p>
    );

  const actionDisabled = loading;

  return (
    <Card className="mt-6">
      <CardHeader className="flex flex-col md:flex-row md:items-center md:justify-between gap-4">
        <div>
          <CardTitle className="flex items-center gap-2">
            Structured Strategy Plan
            <Badge
              variant={data.status === "ready_for_review" ? "default" : "secondary"}
            >
              {data.status}
            </Badge>
          </CardTitle>
          <p className="text-sm text-muted-foreground">
            Generated at {new Date(data.generated_at).toLocaleString()}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          {onEdit && (
            <Button variant="outline" size="sm" onClick={onEdit} disabled={loading}>
              <Edit3 className="mr-2 h-4 w-4" />
              Edit Plan
            </Button>
          )}
          {onApprove && (
            <Button size="sm" onClick={onApprove} disabled={actionDisabled}>
              <Check className="mr-2 h-4 w-4" />
              Approve &amp; Proceed
            </Button>
          )}
        </div>
      </CardHeader>
      <CardContent>
        {data.warnings.length > 0 && (
          <Alert variant="destructive" className="mb-4">
            <AlertTriangle className="h-4 w-4" />
            <AlertDescription>
              <strong>Warnings:</strong>
              <ul className="mt-2 ml-4 list-disc space-y-1 text-sm">
                {data.warnings.map((warning) => (
                  <li key={warning}>{warning}</li>
                ))}
              </ul>
            </AlertDescription>
          </Alert>
        )}

        <Tabs defaultValue="tone" className="w-full">
          <TabsList className="grid w-full grid-cols-5">
            <TabsTrigger value="tone">Tone &amp; Approach</TabsTrigger>
            <TabsTrigger value="content">Content Structure</TabsTrigger>
            <TabsTrigger value="responses">Responses</TabsTrigger>
            <TabsTrigger value="risks">Risk Mitigation</TabsTrigger>
            <TabsTrigger value="outcome">Desired Outcome</TabsTrigger>
          </TabsList>

          <TabsContent value="tone" className="mt-4 space-y-4">
            <section>
              <div className="flex items-center justify-between">
                <h4 className="font-semibold">Overall Tone</h4>
                {data.tone_approach.overall_tone && (
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-8 px-2"
                    onClick={() =>
                      copyText(data.tone_approach.overall_tone ?? "", "tone")
                    }
                  >
                    <Copy className="mr-2 h-4 w-4" />
                    {copiedSection === "tone" ? "Copied" : "Copy"}
                  </Button>
                )}
              </div>
              <p className="text-sm text-muted-foreground mt-2">
                {data.tone_approach.overall_tone ?? "Not specified."}
              </p>
            </section>

            <section>
              <h4 className="font-semibold">Key Messaging Strategy</h4>
              <p className="text-sm text-muted-foreground mt-2">
                {data.tone_approach.key_messaging_strategy ?? "Not specified."}
              </p>
            </section>

            <section>
              <h4 className="font-semibold">Relationship Management Approach</h4>
              <p className="text-sm text-muted-foreground mt-2">
                {data.tone_approach.relationship_management_approach ??
                  "Not specified."}
              </p>
            </section>
          </TabsContent>

          <TabsContent value="content" className="mt-4 space-y-4">
            <div>
              <h4 className="font-semibold">Opening Strategy</h4>
              <p className="text-sm text-muted-foreground mt-2">
                {data.content_structure.opening_strategy ?? "Not specified."}
              </p>
            </div>
            <div>
              <h4 className="font-semibold">Key Points Order</h4>
              {renderList(data.content_structure.key_points_order)}
            </div>
            <div>
              <h4 className="font-semibold">Contractual References</h4>
              {renderList(data.content_structure.contractual_references)}
            </div>
            <div>
              <h4 className="font-semibold">Closing Approach</h4>
              <p className="text-sm text-muted-foreground mt-2">
                {data.content_structure.closing_approach ?? "Not specified."}
              </p>
            </div>
          </TabsContent>

          <TabsContent value="responses" className="mt-4 space-y-4">
            {data.specific_responses.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                No point-by-point responses provided.
              </p>
            ) : (
              data.specific_responses.map((response, index) => (
                <div
                  key={`${response.contractor_point}-${index}`}
                  className="rounded-md border p-4 space-y-2"
                >
                  <h4 className="font-semibold">
                    Contractor Point: {response.contractor_point ?? "N/A"}
                  </h4>
                  <p className="text-sm text-muted-foreground">
                    Strategy: {response.response_strategy ?? "N/A"}
                  </p>
                  {response.contractual_basis && (
                    <p className="text-sm text-muted-foreground">
                      Contractual Basis: {response.contractual_basis}
                    </p>
                  )}
                  {response.evidence_references?.length ? (
                    <div>
                      <p className="text-xs uppercase tracking-wide text-muted-foreground">
                        Evidence
                      </p>
                      {renderList(response.evidence_references)}
                    </div>
                  ) : null}
                </div>
              ))
            )}
          </TabsContent>

          <TabsContent value="risks" className="mt-4 space-y-4">
            <div>
              <h4 className="font-semibold">Legal / Contractual Risks</h4>
              {renderList(data.risk_mitigation.legal_risks)}
            </div>
            <div>
              <h4 className="font-semibold">Relationship Risks</h4>
              {renderList(data.risk_mitigation.relationship_risks)}
            </div>
            <div>
              <h4 className="font-semibold">Project Impact</h4>
              {renderList(data.risk_mitigation.project_impact_considerations)}
            </div>
          </TabsContent>

          <TabsContent value="outcome" className="mt-4 space-y-4">
            <div>
              <h4 className="font-semibold">Immediate Action</h4>
              <p className="text-sm text-muted-foreground mt-2">
                {data.desired_outcome.immediate_action ?? "Not specified."}
              </p>
            </div>

            <div>
              <h4 className="font-semibold">Next Steps</h4>
              {renderList(data.desired_outcome.next_steps)}
            </div>

            <div>
              <h4 className="font-semibold">Fallback Positions</h4>
              {renderList(data.desired_outcome.fallback_positions)}
            </div>
          </TabsContent>
        </Tabs>
      </CardContent>
    </Card>
  );
};

export default StrategyPlanDisplay;
