import React, { useCallback, useState } from "react";
import { LockKeyhole } from "lucide-react";
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
import {
  clearCachedStepUpToken,
  getCachedStepUpToken,
  requestStepUpToken,
} from "@/services/step-up";

type StepUpPrompt = {
  action: string;
  title: string;
  description: string;
  resolve: (token: string | null) => void;
};

export const useStepUp = () => {
  const [prompt, setPrompt] = useState<StepUpPrompt | null>(null);
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const requestToken = useCallback(
    async (
      action: string,
      title = "Confirm sensitive action",
      description = "Enter your password to continue."
    ) => {
      const cachedToken = getCachedStepUpToken(action);
      if (cachedToken) return cachedToken;

      return await new Promise<string>((resolve, reject) => {
        setPassword("");
        setError("");
        setPrompt({
          action,
          title,
          description,
          resolve: (token) => {
            if (token) resolve(token);
            else reject(new Error("Step-up verification was cancelled."));
          },
        });
      });
    },
    []
  );

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!prompt || submitting) return;
    if (!password.trim()) {
      setError("Password is required.");
      return;
    }

    try {
      setSubmitting(true);
      const token = await requestStepUpToken(prompt.action, password);
      prompt.resolve(token);
      setPrompt(null);
      setPassword("");
    } catch (err: any) {
      clearCachedStepUpToken(prompt.action);
      setError(
        err?.response?.data?.detail ||
          err?.message ||
          "Unable to verify your password."
      );
    } finally {
      setSubmitting(false);
    }
  };

  const cancel = () => {
    prompt?.resolve(null);
    setPrompt(null);
    setPassword("");
    setError("");
    setSubmitting(false);
  };

  const StepUpDialog = (
    <Dialog open={Boolean(prompt)} onOpenChange={(open) => !open && cancel()}>
      <DialogContent className="sm:max-w-md">
        <form onSubmit={submit} className="space-y-4">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <LockKeyhole className="h-5 w-5" />
              {prompt?.title || "Confirm sensitive action"}
            </DialogTitle>
            <DialogDescription>
              {prompt?.description || "Enter your password to continue."}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-2">
            <Label htmlFor="step-up-password">Password</Label>
            <Input
              id="step-up-password"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(event) => {
                setPassword(event.target.value);
                if (error) setError("");
              }}
              disabled={submitting}
              autoFocus
            />
            {error ? <p className="text-sm text-destructive">{error}</p> : null}
          </div>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={cancel}>
              Cancel
            </Button>
            <Button type="submit" disabled={submitting}>
              {submitting ? "Verifying..." : "Verify"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );

  return { requestToken, StepUpDialog };
};
