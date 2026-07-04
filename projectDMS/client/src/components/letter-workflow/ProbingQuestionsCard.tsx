import { useMemo, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import { HelpCircle, Send } from "lucide-react";
import type {
  ProbingQuestion,
  UserDirectionAnswer,
} from "@/types/letterDrafting";

const CATEGORY_LABEL: Record<string, string> = {
  position: "Position",
  deadline: "Deadline",
  clause: "Clause",
  amount: "Amount",
  missing_input: "Missing input",
  scope: "Scope",
};

interface Props {
  questions: ProbingQuestion[];
  existingDirections?: UserDirectionAnswer[];
  loading?: boolean;
  onSubmit: (answers: UserDirectionAnswer[], directions?: string) => Promise<void> | void;
}

/**
 * User Direction Agent card: probing questions the drafter should answer
 * before strategy/drafting. Answers merge into subsequent runs' inputs.
 */
const ProbingQuestionsCard = ({ questions, existingDirections, loading, onSubmit }: Props) => {
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [freeText, setFreeText] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const answered = useMemo(
    () => new Set((existingDirections ?? []).map((a) => a.question_id).filter(Boolean)),
    [existingDirections],
  );

  if (!questions.length && !(existingDirections ?? []).length) return null;

  const handleSubmit = async () => {
    const payload: UserDirectionAnswer[] = Object.entries(answers)
      .map(([question_id, answer]) => ({ question_id, answer: answer.trim() }))
      .filter((a) => a.answer);
    if (!payload.length && !freeText.trim()) return;
    setSubmitting(true);
    try {
      await onSubmit(payload, freeText.trim() || undefined);
      setAnswers({});
      setFreeText("");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="flex items-center gap-2 text-base">
          <HelpCircle className="h-4 w-4" />
          Line of action — probing questions
        </CardTitle>
        <p className="text-xs text-muted-foreground">
          Answer before drafting; your direction is carried into the strategy and draft.
        </p>
      </CardHeader>
      <CardContent className="space-y-3">
        {questions.map((q) => (
          <div key={q.question_id} className="space-y-1">
            <p className="text-sm font-medium">
              <span className="mr-2 rounded bg-blue-50 px-1.5 py-0.5 text-[10px] uppercase text-blue-700">
                {CATEGORY_LABEL[q.category ?? "scope"] ?? q.category}
              </span>
              {q.question}
              {answered.has(q.question_id) && (
                <span className="ml-2 text-[10px] text-emerald-600">answered</span>
              )}
            </p>
            {q.why && <p className="text-xs text-muted-foreground">{q.why}</p>}
            <Textarea
              rows={2}
              value={answers[q.question_id] ?? ""}
              onChange={(e) =>
                setAnswers((prev) => ({ ...prev, [q.question_id]: e.target.value }))
              }
              placeholder="Your direction..."
              className="text-sm"
            />
          </div>
        ))}
        <div className="space-y-1">
          <p className="text-sm font-medium">Additional direction (optional)</p>
          <Textarea
            rows={2}
            value={freeText}
            onChange={(e) => setFreeText(e.target.value)}
            placeholder="Any other line of action for this reply..."
            className="text-sm"
          />
        </div>
        {(existingDirections ?? []).length > 0 && (
          <div className="rounded-md border bg-muted/40 p-2 text-xs text-muted-foreground">
            <p className="mb-1 font-medium text-foreground">Recorded direction</p>
            {(existingDirections ?? []).map((d, i) => (
              <p key={i}>
                {d.question_id && d.question_id !== "free_text" ? `[${d.question_id}] ` : ""}
                {d.answer}
              </p>
            ))}
          </div>
        )}
        <div className="flex justify-end">
          <Button size="sm" className="gap-2" onClick={handleSubmit} disabled={submitting || loading}>
            <Send className="h-4 w-4" />
            Record direction
          </Button>
        </div>
      </CardContent>
    </Card>
  );
};

export default ProbingQuestionsCard;
