// MetadataEditor.tsx
import React, { useState, useEffect } from "react";
import { useForm } from "react-hook-form";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
} from "@/components/ui/form";
import { PlusCircle, Save } from "lucide-react";
import { toast } from "sonner";
import { enhancedApi, type DocumentUpdateData } from "@/services/enhanced-api";

type MetadataFieldName =
  | "uploadType"
  | "date"
  | "letterNo"
  | "subject"
  | "from_"
  | "to"
  | "tag"
  | "subTag"
  | "status";

interface MetadataField {
  id: MetadataFieldName;
  label: string;
  value: string;
  type: "select" | "text" | "textarea" | "date" | "radio";
  options?: string[];
}

type FormValues = {
  [key in MetadataFieldName]?: string;
};

interface MetadataEditorProps {
  metadataFields: MetadataField[];
  onMetadataChange?: (id: MetadataFieldName, value: string) => void;
  documentId: string;
  setMetadataFields: React.Dispatch<React.SetStateAction<MetadataField[]>>;
  availableTags?: { value: string; label: string }[];
  availableSubtags?: { value: string; label: string; tagId: string }[];
  isLoadingSubtags?: boolean;
}

const MetadataEditor: React.FC<MetadataEditorProps> = ({
  metadataFields,
  onMetadataChange = () => {},
  documentId,
  setMetadataFields,
  availableTags = [],
  availableSubtags = [],
  isLoadingSubtags = false,
}) => {
  const form = useForm<FormValues>({
    defaultValues: metadataFields.reduce((values, field) => {
      values[field.id] = field.value;
      return values;
    }, {} as FormValues),
  });

  // Effect to reset form values when metadataFields change
  useEffect(() => {
    const newDefaultValues = metadataFields.reduce((values, field) => {
      values[field.id] = field.value;
      return values;
    }, {} as FormValues);
    form.reset(newDefaultValues);
  }, [metadataFields, form]);

  const [isSaving, setIsSaving] = useState(false);
  const selectedTagValue = form.watch("tag");
  const selectedTagId =
    availableTags.find(
      (tag) => tag.value === selectedTagValue || tag.label === selectedTagValue
    )?.value || selectedTagValue || "";
  const filteredSubtags = selectedTagId
    ? availableSubtags.filter((subtag) => subtag.tagId === selectedTagId)
    : [];

  const handleSave = async () => {
    try {
      setIsSaving(true);
      const formValues = form.getValues();

      let tagIds: string[] = [];
      let subTagIds: string[] = [];

      if (formValues.tag) {
        // Try to find by value first (if it's already an ID), then by label
        const selectedTag = availableTags.find(
          (tag) => tag.value === formValues.tag || tag.label === formValues.tag
        );
        if (selectedTag) {
          tagIds = [selectedTag.value];
        } else {
          // If not found in availableTags, assume it's already an ID
          tagIds = [formValues.tag];
        }
      }

      if (formValues.subTag) {
        // Try to find by value first (if it's already an ID), then by label
        const selectedSubTag = availableSubtags.find(
          (subtag) =>
            subtag.value === formValues.subTag ||
            subtag.label === formValues.subTag
        );
        if (selectedSubTag) {
          subTagIds = [selectedSubTag.value];
        } else {
          // If not found in availableSubtags, assume it's already an ID
          subTagIds = [formValues.subTag];
        }
      }

      const updateData: DocumentUpdateData = {
        uploadType: formValues.uploadType?.toLowerCase(),
        letterNo: formValues.letterNo,
        date: formValues.date,
        subject: formValues.subject,
        // Use alias key expected by backend schema (Pydantic Field(alias="from"))
        from: formValues.from_,
        to: formValues.to,
        status: formValues.status,
        ...(tagIds.length > 0 && { tags: tagIds }),
        ...(subTagIds.length > 0 && { subTags: subTagIds }),
      };

      const cleanedData = Object.fromEntries(
        Object.entries(updateData).filter(
          ([_, value]) =>
            value !== undefined &&
            value !== "" &&
            !(Array.isArray(value) && value.length === 0)
        )
      );
      await enhancedApi.updateDocument(documentId, cleanedData);

      toast.success("Document metadata updated successfully");

      // No need to manually update setMetadataFields here if parent handles it
      // via onMetadataChange and subsequent document state update.
      // The useEffect in DocumentViewerPage will re-render this component with new props.
    } catch (error) {
      console.error("Error updating metadata:", error);
      toast.error("Error updating metadata", {
        description:
          error instanceof Error ? error.message : "Unknown error occurred",
      });
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <Form {...form}>
      <div className="space-y-4">
        {metadataFields.map((field) => (
          <FormField
            key={field.id}
            control={form.control}
            name={field.id}
            render={({ field: formField }) => (
              <FormItem className="space-y-1">
                <FormLabel>{field.label}</FormLabel>
                <FormControl>
                  {field.type === "select" ? (
                    <Select
                      value={formField.value}
                      onValueChange={(value) => {
                        formField.onChange(value); // Update react-hook-form state
                        if (field.id === "tag") {
                          form.setValue("subTag", "");
                          onMetadataChange("subTag", "");
                        }
                        onMetadataChange(field.id, value); // Notify parent
                      }}
                      disabled={
                        field.id === "subTag" &&
                        (isLoadingSubtags || !selectedTagId)
                      }
                    >
                      <SelectTrigger id={field.id}>
                        <SelectValue
                          placeholder={
                            field.id === "subTag" && isLoadingSubtags
                              ? "Loading subtags..."
                              : `Select ${field.label}`
                          }
                        />
                      </SelectTrigger>
                      <SelectContent>
                        {field.id === "tag"
                          ? availableTags.length > 0
                            ? availableTags.map((tag) => (
                                <SelectItem key={tag.value} value={tag.value}>
                                  {tag.label}
                                </SelectItem>
                              ))
                            : (
                                <SelectItem value="__no-tags" disabled>
                                  No tags available
                                </SelectItem>
                              )
                          : field.id === "subTag"
                          ? filteredSubtags.map((subtag) => (
                              <SelectItem
                                key={subtag.value}
                                value={subtag.value}
                              >
                                {subtag.label}
                              </SelectItem>
                            ))
                          : field.options?.map((option) => {
                              const isGroupLabel =
                                field.id === "status" &&
                                option.trim().startsWith("---");
                              return (
                                <SelectItem
                                  key={option}
                                  value={option}
                                  disabled={isGroupLabel}
                                >
                                  {option}
                                </SelectItem>
                              );
                            })}
                        {field.id === "subTag" &&
                          selectedTagId &&
                          !isLoadingSubtags &&
                          filteredSubtags.length === 0 && (
                            <SelectItem
                              value="__no-subtags"
                              disabled
                            >
                              No subtags available
                            </SelectItem>
                          )}
                      </SelectContent>
                    </Select>
                  ) : field.type === "textarea" ? (
                    <Textarea
                      id={field.id}
                      {...formField}
                      placeholder={`Enter ${field.label.toLowerCase()}`}
                      className="resize-none"
                      rows={3}
                      onChange={(e) => {
                        formField.onChange(e.target.value); // Update react-hook-form state
                        onMetadataChange(field.id, e.target.value); // Notify parent
                      }}
                    />
                  ) : field.type === "date" ? (
                    <Input
                      id={field.id}
                      type="date"
                      {...formField}
                      onChange={(e) => {
                        formField.onChange(e.target.value); // Update react-hook-form state
                        onMetadataChange(field.id, e.target.value); // Notify parent
                      }}
                    />
                  ) : field.type === "radio" ? (
                    <RadioGroup
                      value={formField.value}
                      onValueChange={(value) => {
                        formField.onChange(value); // Update react-hook-form state
                        onMetadataChange(field.id, value); // Notify parent
                      }}
                      className="flex gap-4"
                    >
                      {field.options?.map((option) => (
                        <div
                          key={option}
                          className="flex items-center space-x-2"
                        >
                          <RadioGroupItem
                            value={option}
                            id={`${field.id}-${option}`}
                          />
                          <Label htmlFor={`${field.id}-${option}`}>
                            {option}
                          </Label>
                        </div>
                      ))}
                    </RadioGroup>
                  ) : (
                    <Input
                      id={field.id}
                      {...formField}
                      placeholder={`Enter ${field.label.toLowerCase()}`}
                      onChange={(e) => {
                        formField.onChange(e.target.value); // Update react-hook-form state
                        onMetadataChange(field.id, e.target.value); // Notify parent
                      }}
                    />
                  )}
                </FormControl>
              </FormItem>
            )}
          />
        ))}

        <div className="flex gap-2 pt-3">
          <Button className="flex-1" variant="outline">
            <PlusCircle className="h-4 w-4 mr-2" />
            Add Field
          </Button>
          <Button className="flex-1" onClick={handleSave} disabled={isSaving}>
            <Save className="h-4 w-4 mr-2" />
            Save
          </Button>
        </div>
      </div>
    </Form>
  );
};

export default MetadataEditor;
