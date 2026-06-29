import React, { useEffect, useMemo, useState } from "react";
import { AlertCircle, CheckCircle2, Download, Loader2, Upload } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

export interface CSVImportRow {
  row_number: number;
  data: Record<string, any>;
  errors: string[];
  warnings: string[];
  duplicate: boolean;
}

export interface CSVImportPreview {
  total_rows: number;
  valid_rows: number;
  invalid_rows: number;
  can_import: boolean;
  rows: CSVImportRow[];
  required_headers: string[];
  template_headers: string[];
}

export interface CSVImportResult extends CSVImportPreview {
  imported_count: number;
  created_ids: string[];
}

interface CsvImportDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description: string;
  sampleFileName: string;
  onDownloadTemplate: () => Promise<Blob>;
  onPreview: (file: File) => Promise<CSVImportPreview>;
  onImport: (file: File) => Promise<CSVImportResult>;
  onImported: () => Promise<void> | void;
  rowLabel: (row: CSVImportRow) => string;
}

function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

export const CsvImportDialog: React.FC<CsvImportDialogProps> = ({
  open,
  onOpenChange,
  title,
  description,
  sampleFileName,
  onDownloadTemplate,
  onPreview,
  onImport,
  onImported,
  rowLabel,
}) => {
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<CSVImportPreview | null>(null);
  const [busy, setBusy] = useState<"template" | "preview" | "import" | null>(null);

  useEffect(() => {
    if (!open) {
      setFile(null);
      setPreview(null);
      setBusy(null);
    }
  }, [open]);

  const status = useMemo(() => {
    if (!preview) return null;
    if (preview.invalid_rows > 0) return "invalid";
    if (preview.valid_rows > 0) return "valid";
    return "empty";
  }, [preview]);

  const template = async () => {
    setBusy("template");
    try {
      downloadBlob(await onDownloadTemplate(), sampleFileName);
    } catch (error: any) {
      toast.error(error?.response?.data?.detail || "Template download failed");
    } finally {
      setBusy(null);
    }
  };

  const previewFile = async () => {
    if (!file) {
      toast.error("Select a CSV file first");
      return;
    }
    setBusy("preview");
    try {
      const result = await onPreview(file);
      setPreview(result);
      if (result.invalid_rows > 0) toast.error(`${result.invalid_rows} row(s) need correction`);
      else toast.success(`${result.valid_rows} row(s) ready to import`);
    } catch (error: any) {
      toast.error(error?.response?.data?.detail || "CSV preview failed");
      setPreview(null);
    } finally {
      setBusy(null);
    }
  };

  const importFile = async () => {
    if (!file || !preview?.can_import) return;
    setBusy("import");
    try {
      const result = await onImport(file);
      setPreview(result);
      if (!result.can_import || result.invalid_rows > 0) {
        toast.error(`${result.invalid_rows} row(s) need correction`);
        return;
      }
      toast.success(`Imported ${result.imported_count} record(s)`);
      await onImported();
      onOpenChange(false);
    } catch (error: any) {
      toast.error(error?.response?.data?.detail || "CSV import failed");
    } finally {
      setBusy(null);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[88vh] overflow-hidden sm:max-w-4xl">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>

        <div className="space-y-4 overflow-y-auto pr-1">
          <div className="grid gap-3 md:grid-cols-[1fr_auto_auto] md:items-end">
            <div>
              <Label htmlFor="csv-import-file">CSV file</Label>
              <Input
                id="csv-import-file"
                type="file"
                accept=".csv,text/csv"
                onChange={(event) => {
                  setFile(event.target.files?.[0] || null);
                  setPreview(null);
                }}
              />
            </div>
            <Button variant="outline" onClick={template} disabled={busy !== null}>
              {busy === "template" ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Download className="mr-2 h-4 w-4" />}
              Sample CSV
            </Button>
            <Button variant="outline" onClick={previewFile} disabled={!file || busy !== null}>
              {busy === "preview" ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Upload className="mr-2 h-4 w-4" />}
              Preview
            </Button>
          </div>

          {preview && (
            <div className="space-y-3">
              <div className="flex flex-wrap items-center gap-2">
                <Badge variant="outline">{preview.total_rows} rows</Badge>
                <Badge className="bg-green-100 text-green-800">{preview.valid_rows} valid</Badge>
                <Badge className={preview.invalid_rows ? "bg-red-100 text-red-800" : "bg-slate-100 text-slate-700"}>
                  {preview.invalid_rows} with errors
                </Badge>
                {status === "valid" && <span className="flex items-center gap-1 text-sm text-green-700"><CheckCircle2 className="h-4 w-4" />Ready</span>}
                {status === "invalid" && <span className="flex items-center gap-1 text-sm text-red-700"><AlertCircle className="h-4 w-4" />Fix errors before import</span>}
              </div>

              <div className="rounded-md border">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead className="w-20">Row</TableHead>
                      <TableHead>Record</TableHead>
                      <TableHead>Status</TableHead>
                      <TableHead>Messages</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {preview.rows.map((row) => (
                      <TableRow key={row.row_number}>
                        <TableCell>{row.row_number}</TableCell>
                        <TableCell className="max-w-[260px] truncate" title={rowLabel(row)}>
                          {rowLabel(row)}
                        </TableCell>
                        <TableCell>
                          {row.errors.length ? (
                            <Badge className="bg-red-100 text-red-800">Invalid</Badge>
                          ) : (
                            <Badge className="bg-green-100 text-green-800">Valid</Badge>
                          )}
                        </TableCell>
                        <TableCell className="text-sm">
                          {row.errors.length > 0 ? (
                            <ul className="space-y-1 text-red-700">
                              {row.errors.map((msg, index) => <li key={`${row.row_number}-e-${index}`}>{msg}</li>)}
                            </ul>
                          ) : row.warnings.length > 0 ? (
                            <ul className="space-y-1 text-amber-700">
                              {row.warnings.map((msg, index) => <li key={`${row.row_number}-w-${index}`}>{msg}</li>)}
                            </ul>
                          ) : (
                            <span className="text-muted-foreground">No issues</span>
                          )}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            </div>
          )}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={busy !== null}>Cancel</Button>
          <Button onClick={importFile} disabled={!file || !preview?.can_import || busy !== null}>
            {busy === "import" ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Upload className="mr-2 h-4 w-4" />}
            Import
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};

export default CsvImportDialog;
