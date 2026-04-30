import React from "react";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  ListChecks,
  MessageCircleQuestion,
  Mail,
  Clock,
  CheckCircle,
} from "lucide-react";

interface LetterWorkflowTabsProps {
  activeTab: string;
  setActiveTab: (tab: string) => void;
  children: React.ReactNode;
}

export const LetterWorkflowTabs: React.FC<LetterWorkflowTabsProps> = ({
  activeTab,
  setActiveTab,
  children,
}) => {
  const description =
    activeTab === "all"
      ? "View all letters in the system"
      : activeTab === "input"
      ? "View letters requiring input or clarification"
      : `View letters in ${activeTab} stage`;

  return (
    <Tabs value={activeTab} onValueChange={setActiveTab} className="w-full">
      <TabsList className="grid w-full grid-cols-6">
        <TabsTrigger value="all">
          <ListChecks className="mr-2 h-4 w-4" />
          All Letters
        </TabsTrigger>
        <TabsTrigger value="input">
          <MessageCircleQuestion className="mr-2 h-4 w-4" />
          Input
        </TabsTrigger>
        <TabsTrigger value="draft">
          <Mail className="mr-2 h-4 w-4" />
          Draft
        </TabsTrigger>
        <TabsTrigger value="review">
          <Clock className="mr-2 h-4 w-4" />
          Review
        </TabsTrigger>
        <TabsTrigger value="approval">
          <CheckCircle className="mr-2 h-4 w-4" />
          Approval
        </TabsTrigger>
        <TabsTrigger value="completed">
          <CheckCircle className="mr-2 h-4 w-4" />
          Completed
        </TabsTrigger>
      </TabsList>

      {["all", "input", "draft", "review", "approval", "completed"].map(
        (val) => (
          <TabsContent key={val} value={val} className="space-y-4">
            <Card>
              <CardHeader>
                <CardTitle className="text-lg">Letter Management</CardTitle>
                <CardDescription>{description}</CardDescription>
              </CardHeader>
              <CardContent>{children}</CardContent>
            </Card>
          </TabsContent>
        )
      )}
    </Tabs>
  );
};
