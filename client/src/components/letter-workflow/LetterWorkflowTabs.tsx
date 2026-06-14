
import React, { useMemo } from 'react';
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { ListChecks, MessageCircleQuestion, Mail, Clock, CheckCircle, Lightbulb } from 'lucide-react';
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
        icon: <ListChecks className="mr-2 h-4 w-4" />,
        description: "View every letter regardless of workflow stage",
      },
      {
        value: "Input" as const,
        label: "Input",
        icon: <MessageCircleQuestion className="mr-2 h-4 w-4" />,
        description: "Letters that require additional information or clarification",
      },
      {
        value: "Strategy" as const,
        label: "Strategy",
        icon: <Lightbulb className="mr-2 h-4 w-4" />,
        description: "Strategic planning and drafting engine analysis",
      },
      {
        value: "Draft" as const,
        label: "Draft",
        icon: <Mail className="mr-2 h-4 w-4" />,
        description: "Letters currently being drafted or revised",
      },
      {
        value: "Review" as const,
        label: "Review",
        icon: <Clock className="mr-2 h-4 w-4" />,
        description: "Letters awaiting reviewer feedback or approval",
      },
      {
        value: "Approval" as const,
        label: "Approval",
        icon: <CheckCircle className="mr-2 h-4 w-4" />,
        description: "Letters pending final approval",
      },
      {
        value: "Completed" as const,
        label: "Completed",
        icon: <CheckCircle className="mr-2 h-4 w-4" />,
        description: "Approved or closed-out correspondence",
      },
    ],
    []
  );

  const activeTabMeta = tabs.find((tab) => tab.value === activeTab);

  return (
    <Tabs value={activeTab} onValueChange={setActiveTab} className="w-full">
      <TabsList className="grid w-full grid-cols-7">
        {tabs.map((tab) => (
          <TabsTrigger value={tab.value} key={tab.value}>
            {tab.icon}
            {tab.label}
          </TabsTrigger>
        ))}
      </TabsList>
      
      <TabsContent value={activeTab} className="space-y-4">
        <Card>
          <CardHeader>
            <CardTitle className="text-lg">Letter Management</CardTitle>
            <CardDescription>
              {activeTabMeta?.description ?? 'View letters by workflow stage'}
            </CardDescription>
          </CardHeader>
          <CardContent>
            {children}
          </CardContent>
        </Card>
      </TabsContent>
    </Tabs>
  );
};
