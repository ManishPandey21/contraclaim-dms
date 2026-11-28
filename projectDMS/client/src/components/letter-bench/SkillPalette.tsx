import React from "react";
import { Lock, PenLine, RefreshCw, Sparkles } from "lucide-react";

import {
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandSeparator,
} from "@/components/ui/command";
import type { BenchGate, DraftRole, EffectiveSkill } from "@/types/letterBench";
import { paletteSkills } from "./benchLogic";

interface SkillPaletteProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  skills: EffectiveSkill[];
  role: DraftRole;
  /** The assertion a chosen skill will be applied to; null means the whole letter. */
  targetAssertionId: string | null;
  gate: BenchGate;
  onApplySkill: (skill: EffectiveSkill, assertionId: string | null) => void;
  onReanalyse: () => void;
  onLock: () => void;
  onCompose: () => void;
}

/** Command palette on "/" or Ctrl/Cmd+K: apply a skill, or drive the Bench without the mouse. */
export const SkillPalette: React.FC<SkillPaletteProps> = ({
  open,
  onOpenChange,
  skills,
  role,
  targetAssertionId,
  gate,
  onApplySkill,
  onReanalyse,
  onLock,
  onCompose,
}) => {
  const options = paletteSkills(skills, role);
  const close = () => onOpenChange(false);
  const scope = targetAssertionId ? `to ${targetAssertionId}` : "to the whole letter";

  return (
    <CommandDialog open={open} onOpenChange={onOpenChange}>
      <CommandInput placeholder={`Apply a skill ${scope}, or run a command…`} />
      <CommandList>
        <CommandEmpty>No skill or command matches.</CommandEmpty>
        <CommandGroup heading={`Apply a skill ${scope}`}>
          {options.map((skill) => (
            <CommandItem
              key={skill.skill_key}
              value={`${skill.name} ${skill.skill_key} ${skill.use_when ?? ""}`}
              disabled={!skill.selectable}
              onSelect={() => {
                if (!skill.selectable) return;
                onApplySkill(skill, targetAssertionId);
                close();
              }}
            >
              <Sparkles className="mr-2 shrink-0" aria-hidden="true" />
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <span className="font-medium">{skill.name}</span>
                  {skill.version && <span className="text-xs text-muted-foreground">v{skill.version}</span>}
                  {skill.pinned && <span className="rounded bg-muted px-1 text-[10px] uppercase">pinned</span>}
                </div>
                <p className="truncate text-xs text-muted-foreground">
                  {skill.selectable ? (skill.use_when ?? skill.description) : `Unavailable: ${skill.reason}`}
                </p>
              </div>
            </CommandItem>
          ))}
        </CommandGroup>
        <CommandSeparator />
        <CommandGroup heading="Commands">
          <CommandItem
            value="re-run analysis dissect letter again"
            onSelect={() => {
              onReanalyse();
              close();
            }}
          >
            <RefreshCw className="mr-2" aria-hidden="true" /> Re-run the analysis
          </CommandItem>
          <CommandItem
            value="lock positions approve strategy"
            disabled={!gate.can_lock}
            onSelect={() => {
              onLock();
              close();
            }}
          >
            <Lock className="mr-2" aria-hidden="true" /> Lock positions
          </CommandItem>
          <CommandItem
            value="compose reply draft letter"
            disabled={!gate.can_compose}
            onSelect={() => {
              onCompose();
              close();
            }}
          >
            <PenLine className="mr-2" aria-hidden="true" /> Compose the reply
          </CommandItem>
        </CommandGroup>
      </CommandList>
    </CommandDialog>
  );
};

export default SkillPalette;
