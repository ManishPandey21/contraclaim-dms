
import React, { useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import { Letter } from './types';
import { StatusBadge } from './StatusBadge';
import { PendencyIndicator } from './PendencyIndicator';

interface LettersTableProps {
  letters: Letter[];
  formatDate: (dateString: string) => string;
  onSelectLetter?: (letterId: string) => void;
  selectedLetterId?: string;
}

export const LettersTable: React.FC<LettersTableProps> = ({
  letters,
  formatDate,
  onSelectLetter,
  selectedLetterId,
}) => {
  const navigate = useNavigate();

  const rowClass = (letterId: string) =>
    letterId === selectedLetterId ? "bg-muted/50" : "";

  const getLetterRoute = useMemo(
    () =>
      (letter: Letter) => {
        switch (letter.status) {
          case 'Input':
            return `/letters/${letter.id}/input`;
          case 'Strategy':
            return `/letters/${letter.id}/strategy`;
          case 'Draft':
            return `/letters/${letter.id}/draft`;
          case 'Review':
            return `/letters/${letter.id}/review`;
          case 'Approval':
            return `/letters/${letter.id}/approval`;
          case 'Completed':
          case 'Rejected':
            return `/letters/${letter.id}/completed`;
          default:
            return `/letters/${letter.id}/draft`;
        }
      },
    []
  );

  const handleRowSelect = (letterId: string) => {
    if (onSelectLetter) {
      onSelectLetter(letterId);
    }
  };

  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Title</TableHead>
          <TableHead>Recipient</TableHead>
          <TableHead>Created By</TableHead>
          <TableHead>Assigned To</TableHead>
          <TableHead>Last Updated</TableHead>
          <TableHead>Status</TableHead>
          <TableHead>Pendency</TableHead>
          <TableHead className="text-right">Actions</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {letters.map(letter => (
          <TableRow
            key={letter.id}
            className={rowClass(letter.id)}
            onClick={() => handleRowSelect(letter.id)}
          >
            <TableCell className="font-medium">{letter.title}</TableCell>
            <TableCell>{letter.recipient}</TableCell>
            <TableCell>
              <div className="flex items-center gap-2">
                <Avatar className="h-6 w-6">
                  <AvatarImage src={letter.createdBy?.avatar} />
                  <AvatarFallback>
                    {letter.createdBy?.name?.charAt(0) ?? "?"}
                  </AvatarFallback>
                </Avatar>
                <span>{letter.createdBy?.name ?? "—"}</span>
              </div>
            </TableCell>
            <TableCell>
              <div className="flex items-center gap-2">
                <Avatar className="h-6 w-6">
                  <AvatarImage src={letter.assignedTo?.avatar} />
                  <AvatarFallback>
                    {letter.assignedTo?.name?.charAt(0) ?? "?"}
                  </AvatarFallback>
                </Avatar>
                <span>{letter.assignedTo?.name ?? "Unassigned"}</span>
              </div>
            </TableCell>
            <TableCell>{formatDate(letter.updatedAt)}</TableCell>
            <TableCell><StatusBadge status={letter.status} /></TableCell>
            <TableCell>
              {letter.statusStartDate && (
                <PendencyIndicator
                  date={letter.statusStartDate}
                  status={letter.status}
                />
              )}
            </TableCell>
            <TableCell className="text-right">
              <div className="flex items-center justify-end gap-2 whitespace-nowrap">
                <Button
                  variant="outline"
                  onClick={(event) => {
                    event.stopPropagation();
                    navigate(getLetterRoute(letter));
                  }}
                >
                  Manage
                </Button>
              </div>
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
};
