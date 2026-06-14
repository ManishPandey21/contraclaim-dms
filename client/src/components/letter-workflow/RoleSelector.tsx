import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type { StrategyRole } from "@/types/strategyPlan";

interface RoleSelectorProps {
  role: StrategyRole;
  onRoleChange: (role: StrategyRole) => void;
  engineerRecipient?: string;
  onEngineerRecipientChange?: (recipient: string) => void;
}

export const RoleSelector = ({
  role,
  onRoleChange,
  engineerRecipient,
  onEngineerRecipientChange,
}: RoleSelectorProps) => {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-lg">Select Strategic Perspective</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="space-y-2">
          <Label>Your Role</Label>
          <Select value={role} onValueChange={(value) => onRoleChange(value as StrategyRole)}>
            <SelectTrigger>
              <SelectValue placeholder="Choose role" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="contractor">
                Contractor (writing to Engineer)
              </SelectItem>
              <SelectItem value="engineer">
                Engineer Representative
              </SelectItem>
              <SelectItem value="employer">Engineer/Employer Context</SelectItem>
            </SelectContent>
          </Select>
        </div>

        {role === "engineer" && onEngineerRecipientChange && (
          <div className="space-y-2">
            <Label>Engineer Writing To</Label>
            <Select
              value={engineerRecipient ?? "Contractor"}
              onValueChange={onEngineerRecipientChange}
            >
              <SelectTrigger>
                <SelectValue placeholder="Select recipient" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="Contractor">Contractor</SelectItem>
                <SelectItem value="Employer">Employer</SelectItem>
              </SelectContent>
            </Select>
          </div>
        )}
      </CardContent>
    </Card>
  );
};

export default RoleSelector;
