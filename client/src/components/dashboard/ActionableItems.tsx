
import React from 'react';
import { Link } from 'react-router-dom';
import { 
  Card, CardHeader, CardTitle, CardDescription, CardContent 
} from "@/components/ui/card";
import { 
  Tabs, TabsContent, TabsList, TabsTrigger 
} from "@/components/ui/tabs";
import { 
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow 
} from "@/components/ui/table";
import { Button } from "@/components/ui/button";
import { 
  MessageCircleQuestion, AlertCircle, Clock, ArrowUpRight 
} from 'lucide-react';

export interface Letter {
  id: string;
  title: string;
  organization: string;
  project: string;
  status: string;
  updatedAt: string;
  updatedBy: string;
  assignedTo: string;
}

interface ActionableItemsProps {
  letters: Letter[];
  formatDate: (dateString: string) => string;
  getStatusIcon: (status: string) => React.ReactNode;
}

const ActionableItems: React.FC<ActionableItemsProps> = ({ 
  letters, 
  formatDate, 
  getStatusIcon 
}) => {
  return (
    <Card className="glass-card">
      <CardHeader>
        <CardTitle className="text-lg font-semibold">Actionable Items</CardTitle>
        <CardDescription>Documents that require your attention</CardDescription>
      </CardHeader>
      <CardContent>
        <Tabs defaultValue="input-required" className="w-full">
          <TabsList className="grid grid-cols-3 w-full">
            <TabsTrigger value="input-required">
              <MessageCircleQuestion className="mr-2 h-4 w-4" />
              Input Required
            </TabsTrigger>
            <TabsTrigger value="reply-overdue">
              <AlertCircle className="mr-2 h-4 w-4" />
              Reply Overdue
            </TabsTrigger>
            <TabsTrigger value="under-review">
              <Clock className="mr-2 h-4 w-4" />
              Under Review
            </TabsTrigger>
          </TabsList>
          
          <TabsContent value="input-required" className="pt-4">
            <LetterTable 
              letters={letters.filter(letter => letter.status === 'Input Required')} 
              formatDate={formatDate}
              getStatusIcon={getStatusIcon}
            />
          </TabsContent>
          
          <TabsContent value="reply-overdue" className="pt-4">
            <LetterTable 
              letters={letters.filter(letter => letter.status === 'Reply Overdue')} 
              formatDate={formatDate}
              getStatusIcon={getStatusIcon}
            />
          </TabsContent>
          
          <TabsContent value="under-review" className="pt-4">
            <LetterTable 
              letters={letters.filter(letter => letter.status === 'Under Review')} 
              formatDate={formatDate}
              getStatusIcon={getStatusIcon}
            />
          </TabsContent>
        </Tabs>
      </CardContent>
    </Card>
  );
};

interface LetterTableProps {
  letters: Letter[];
  formatDate: (dateString: string) => string;
  getStatusIcon: (status: string) => React.ReactNode;
}

const LetterTable: React.FC<LetterTableProps> = ({ letters, formatDate, getStatusIcon }) => {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Document</TableHead>
          <TableHead>Organization</TableHead>
          <TableHead>Project</TableHead>
          <TableHead>Last Updated</TableHead>
          <TableHead>Updated By</TableHead>
          <TableHead className="text-right">Action</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {letters.map((letter) => (
          <TableRow key={letter.id}>
            <TableCell className="font-medium">
              <div className="flex items-center">
                {getStatusIcon(letter.status)}
                <span className="ml-2">{letter.title}</span>
              </div>
            </TableCell>
            <TableCell>{letter.organization}</TableCell>
            <TableCell>{letter.project}</TableCell>
            <TableCell>{formatDate(letter.updatedAt)}</TableCell>
            <TableCell>{letter.updatedBy || "N/A"}</TableCell>
            <TableCell className="text-right">
              <Button size="sm" asChild>
                <Link to={`/documentviewer/${letter.id}`}>
                  View <ArrowUpRight className="ml-1 h-3 w-3" />
                </Link>
              </Button>
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
};

export default ActionableItems;
