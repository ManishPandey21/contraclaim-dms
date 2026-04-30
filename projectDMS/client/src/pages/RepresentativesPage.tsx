import React, { useState, useEffect, useCallback } from "react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form";
import { Input } from "@/components/ui/input";
import { Checkbox } from "@/components/ui/checkbox";
import { useForm } from "react-hook-form";
import { toast } from "sonner";
import { enhancedApi } from "@/services/enhanced-api";
import {
  Representative,
  RepresentativeCreateInput,
  RepresentativeUpdateInput,
} from "@/types/api";

const RepresentativesPage: React.FC = () => {
  const [representatives, setRepresentatives] = useState<Representative[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isError, setIsError] = useState(false);
  const [isDialogOpen, setIsDialogOpen] = useState(false);
  const [editingRepresentative, setEditingRepresentative] =
    useState<Representative | null>(null);

  const form = useForm<RepresentativeCreateInput | RepresentativeUpdateInput>();

  const fetchRepresentatives = useCallback(async () => {
    try {
      setIsLoading(true);
      const data = await enhancedApi.getRepresentatives();
      setRepresentatives(data);
    } catch (error) {
      console.error("Error fetching representatives:", error);
      setIsError(true);
      toast.error("Failed to fetch representatives");
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchRepresentatives();
  }, [fetchRepresentatives]);

  const handleFormSubmit = async (
    values: RepresentativeCreateInput | RepresentativeUpdateInput
  ) => {
    try {
      if (editingRepresentative) {
        await enhancedApi.updateRepresentative(
          editingRepresentative._id,
          values as RepresentativeUpdateInput
        );
        toast.success("Representative updated successfully");
      } else {
        await enhancedApi.createRepresentative(
          values as RepresentativeCreateInput
        );
        toast.success("Representative created successfully");
      }
      setIsDialogOpen(false);
      setEditingRepresentative(null);
      form.reset();
      fetchRepresentatives();
    } catch (error) {
      console.error("Error saving representative:", error);
      toast.error("Failed to save representative");
    }
  };

  const openDialog = (rep?: Representative) => {
    if (rep) {
      setEditingRepresentative(rep);
      form.reset(rep);
    } else {
      setEditingRepresentative(null);
      form.reset({});
    }
    setIsDialogOpen(true);
  };

  const handleDelete = async (rep: Representative) => {
    try {
      await enhancedApi.deleteRepresentative(rep._id);
      toast.success("Representative deleted successfully");
      fetchRepresentatives();
    } catch (error) {
      console.error("Error deleting representative:", error);
      toast.error("Failed to delete representative");
    }
  };

  if (isLoading) return <div>Loading...</div>;
  if (isError) return <div>Error loading representatives.</div>;

  return (
    <div className="p-4">
      <h1 className="text-2xl font-bold mb-4">Representatives</h1>
      <Button onClick={() => openDialog()}>Add Representative</Button>

      <div className="mt-4">
        {representatives.map((rep) => (
          <div key={rep._id} className="border p-4 my-2 rounded">
            <p>Name: {rep.name}</p>
            <p>Email: {rep.email}</p>
            <p>Designation: {rep.designation}</p>
            <p>Primary: {rep.is_primary ? "Yes" : "No"}</p>
            <Button onClick={() => openDialog(rep)}>Edit</Button>
            <Button onClick={() => handleDelete(rep)}>Delete</Button>
          </div>
        ))}
      </div>

      <Dialog open={isDialogOpen} onOpenChange={setIsDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {editingRepresentative ? "Edit" : "Add"} Representative
            </DialogTitle>
            <DialogDescription>
              {editingRepresentative
                ? "Update the representative's details."
                : "Add a new representative."}
            </DialogDescription>
          </DialogHeader>
          <Form {...form}>
            <form
              onSubmit={form.handleSubmit(handleFormSubmit)}
              className="space-y-4"
            >
              <FormField
                control={form.control}
                name="name"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Name</FormLabel>
                    <FormControl>
                      <Input {...field} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name="email"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Email</FormLabel>
                    <FormControl>
                      <Input {...field} type="email" />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name="designation"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Designation</FormLabel>
                    <FormControl>
                      <Input {...field} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name="is_primary"
                render={({ field }) => (
                  <FormItem className="flex flex-row items-start space-x-3 space-y-0 rounded-md border p-4">
                    <FormControl>
                      <Checkbox
                        checked={!!field.value}
                        onCheckedChange={(checked) => field.onChange(!!checked)}
                      />
                    </FormControl>
                    <div className="space-y-1 leading-none">
                      <FormLabel>Set as primary contact</FormLabel>
                    </div>
                  </FormItem>
                )}
              />
              <DialogFooter>
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => setIsDialogOpen(false)}
                >
                  Cancel
                </Button>
                <Button type="submit">Save</Button>
              </DialogFooter>
            </form>
          </Form>
        </DialogContent>
      </Dialog>
    </div>
  );
};

export default RepresentativesPage;
