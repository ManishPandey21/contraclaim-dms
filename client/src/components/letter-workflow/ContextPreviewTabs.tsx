import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { ClipboardCopy } from "lucide-react";
import { useToast } from "@/hooks/use-toast";

interface ContextPreviewTabsProps {
  contractor?: string | null;
  engineer?: string | null;
  employer?: string | null;
}

const tabConfig = [
  { value: "contractor", label: "Contractor" },
  { value: "engineer", label: "Engineer Representative" },
  { value: "employer", label: "Engineer/Employer Context" },
];

export const ContextPreviewTabs = ({
  contractor,
  engineer,
  employer,
}: ContextPreviewTabsProps) => {
  const { toast } = useToast();

  const contexts: Record<string, string | undefined | null> = {
    contractor,
    engineer,
    employer,
  };

  const handleCopy = async (text?: string | null) => {
    if (!text) return;
    await navigator.clipboard.writeText(text);
    toast({
      title: "Copied",
      description: "Context copied to clipboard",
    });
  };

  return (
    <Tabs defaultValue="contractor" className="w-full">
      <TabsList className="grid w-full grid-cols-3">
        {tabConfig.map((tab) => (
          <TabsTrigger key={tab.value} value={tab.value}>
            {tab.label}
          </TabsTrigger>
        ))}
      </TabsList>
      {tabConfig.map((tab) => {
        const value = contexts[tab.value];
        return (
          <TabsContent key={tab.value} value={tab.value}>
            <div className="flex items-center justify-between mb-2">
              <p className="text-sm font-semibold">{tab.label} Perspective</p>
              <Button
                variant="ghost"
                size="sm"
                className="gap-1"
                disabled={!value}
                onClick={() => handleCopy(value)}
              >
                <ClipboardCopy className="h-4 w-4" />
                Copy
              </Button>
            </div>
            <ScrollArea className="h-48 rounded-md border bg-muted/30 p-3 text-sm whitespace-pre-wrap">
              {value ? value : "Context not generated yet."}
            </ScrollArea>
          </TabsContent>
        );
      })}
    </Tabs>
  );
};

export default ContextPreviewTabs;
