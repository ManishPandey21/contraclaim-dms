import React from "react";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import { Letter, LetterStatus } from "./types";
import { StatusBadge } from "./StatusBadge";
import { PendencyIndicator } from "./PendencyIndicator";

interface LettersTableProps {
  letters: Letter[];
  formatDate: (dateString: string) => string;
  onManageLetter: (letter: Letter) => void;
  onSelectLetter?: (letter: Letter) => void;
}

export const LettersTable: React.FC<LettersTableProps> = ({
  letters,
  formatDate,
  onManageLetter,
  onSelectLetter,
}) => {
  const isLetterStatus = (status: string): status is LetterStatus => {
    return Object.values(LetterStatus).includes(status);
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
        {letters.map((letter) => (
          <TableRow
            key={letter.id}
            className={onSelectLetter ? "cursor-pointer hover:bg-muted/40" : ""}
            onClick={() => onSelectLetter && onSelectLetter(letter)}
          >
            <TableCell className="font-medium">
              {letter.title || "No title"}
            </TableCell>
            <TableCell>{letter.recipient || "No recipient"}</TableCell>
            <TableCell>
              <div className="flex items-center gap-2">
                <Avatar className="h-6 w-6">
                  <AvatarImage src={letter.createdBy?.avatar || ""} />
                  <AvatarFallback>
                    {letter.createdBy?.name?.charAt(0) || "U"}
                  </AvatarFallback>
                </Avatar>
                <span>{letter.createdBy?.name || "Unknown"}</span>
              </div>
            </TableCell>
            <TableCell>
              <div className="flex items-center gap-2">
                <Avatar className="h-6 w-6">
                  <AvatarImage src={letter.assignedTo?.avatar || ""} />
                  <AvatarFallback>
                    {letter.assignedTo?.name?.charAt(0) || "U"}
                  </AvatarFallback>
                </Avatar>
                <span>{letter.assignedTo?.name || "Unknown"}</span>
              </div>
            </TableCell>
            <TableCell>{formatDate(letter.updatedAt || "")}</TableCell>
            <TableCell>
              <StatusBadge
                status={
                  isLetterStatus(letter.status)
                    ? letter.status
                    : LetterStatus.Unknown
                }
              />
            </TableCell>
            <TableCell>
              {letter.statusStartDate && (
                <PendencyIndicator
                  date={letter.statusStartDate}
                  status={
                    isLetterStatus(letter.status)
                      ? letter.status
                      : LetterStatus.Unknown
                  }
                />
              )}
            </TableCell>
            <TableCell className="text-right">
              <Button
                variant="outline"
                onClick={(e) => {
                  e.stopPropagation();
                  onManageLetter(letter);
                }}
              >
                Manage
              </Button>
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
};
