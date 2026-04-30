import React from "react";
import { Button } from "@/components/ui/button";
import { PlusCircle } from "lucide-react";
import {
  Dialog,
  DialogTrigger,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

interface FieldOption {
  label: string;
  value: string;
}

interface AddEntityButtonProps {
  entityName: string;
  icon?: React.ReactNode;
  onAdd?: (data: any) => void;
  onOpen?: () => void;
  fields?: Array<{
    id: string;
    label: string;
    type?: string;
    placeholder?: string;
    required?: boolean;
    column?: 1 | 2;
    options?: FieldOption[];
  }>;
  twoColumnLayout?: boolean;
}

const AddEntityButton = ({
  entityName,
  icon = <PlusCircle size={16} />,
  onAdd,
  onOpen,
  fields = [],
  twoColumnLayout = false,
}: AddEntityButtonProps) => {
  const [open, setOpen] = React.useState(false);
  const [formData, setFormData] = React.useState<Record<string, string>>({});

  const handleInputChange = (id: string, value: string) => {
    setFormData((prev) => ({ ...prev, [id]: value }));
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (onAdd) {
      onAdd(formData);
    }
    setFormData({});
    setOpen(false);
  };

  // Split fields by column if twoColumnLayout is true
  const column1Fields = twoColumnLayout
    ? fields.filter((field) => !field.column || field.column === 1)
    : fields;
  const column2Fields = twoColumnLayout
    ? fields.filter((field) => field.column === 2)
    : [];

  const renderField = (field: (typeof fields)[0]) => {
    // Render a radio group
    if (field.type === "radio" && field.options) {
      return (
        <div className="grid grid-cols-4 items-center gap-4" key={field.id}>
          <Label htmlFor={field.id} className="text-right">
            {field.label}{" "}
            {field.required && <span className="text-destructive">*</span>}
          </Label>
          <div className="col-span-3">
            <RadioGroup
              defaultValue={
                formData[field.id] || (field.options[0]?.value ?? "")
              }
              onValueChange={(value) => handleInputChange(field.id, value)}
              className="flex flex-wrap gap-4"
            >
              {field.options.map((option) => (
                <div key={option.value} className="flex items-center space-x-2">
                  <RadioGroupItem
                    value={option.value}
                    id={`${field.id}-${option.value}`}
                  />
                  <Label htmlFor={`${field.id}-${option.value}`}>
                    {option.label}
                  </Label>
                </div>
              ))}
            </RadioGroup>
          </div>
        </div>
      );
    }

    // Render a select dropdown
    if (field.type === "select" && field.options) {
      return (
        <div className="grid grid-cols-4 items-center gap-4" key={field.id}>
          <Label htmlFor={field.id} className="text-right">
            {field.label}{" "}
            {field.required && <span className="text-destructive">*</span>}
          </Label>
          <div className="col-span-3">
            <Select
              value={formData[field.id] || ""}
              onValueChange={(value) => handleInputChange(field.id, value)}
            >
              <SelectTrigger id={field.id}>
                <SelectValue
                  placeholder={`Select ${field.label.toLowerCase()}`}
                />
              </SelectTrigger>
              <SelectContent>
                {field.options.map((opt) => (
                  <SelectItem key={opt.value} value={opt.value}>
                    {opt.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>
      );
    }

    // Default: text/number/date inputs
    return (
      <div className="grid grid-cols-4 items-center gap-4" key={field.id}>
        <Label htmlFor={field.id} className="text-right">
          {field.label}{" "}
          {field.required && <span className="text-destructive">*</span>}
        </Label>
        <Input
          id={field.id}
          type={field.type || "text"}
          placeholder={
            field.placeholder || `Enter ${field.label.toLowerCase()}`
          }
          className="col-span-3"
          required={field.required}
          value={formData[field.id] || ""}
          onChange={(e) => handleInputChange(field.id, e.target.value)}
        />
      </div>
    );
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (next && onOpen) {
          try {
            onOpen();
          } catch {
            // no-op
          }
        }
      }}
    >
      <DialogTrigger asChild>
        <Button className="flex items-center gap-2">
          {icon}
          <span>Add {entityName}</span>
        </Button>
      </DialogTrigger>
      <DialogContent
        className={`${
          twoColumnLayout ? "sm:max-w-[800px]" : "sm:max-w-[425px]"
        }`}
      >
        <form onSubmit={handleSubmit}>
          <DialogHeader>
            <DialogTitle>Add New {entityName}</DialogTitle>
            <DialogDescription>
              Fill in the details to create a new {entityName.toLowerCase()}.
            </DialogDescription>
          </DialogHeader>

          {twoColumnLayout ? (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6 py-4">
              <div className="space-y-4">{column1Fields.map(renderField)}</div>
              <div className="space-y-4">{column2Fields.map(renderField)}</div>
            </div>
          ) : (
            <div className="grid gap-4 py-4">{fields.map(renderField)}</div>
          )}

          <DialogFooter>
            <Button type="submit">Create {entityName}</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
};

export default AddEntityButton;
export { AddEntityButton };
