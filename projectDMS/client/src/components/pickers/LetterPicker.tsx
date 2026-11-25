import React from "react";
import { isLetter, letterLabel } from "@/lib/picker-labels";
import DocumentPicker from "./DocumentPicker";

type Props = Omit<React.ComponentProps<typeof DocumentPicker>, "filter" | "describe" | "placeholder">;

/** Choose letters (incoming or outgoing correspondence) on one project (plan 4B-11). */
const LetterPicker: React.FC<Props> = (props) => (
  <DocumentPicker {...props} filter={isLetter} describe={letterLabel} placeholder="Search by reference or subject" />
);

export default LetterPicker;
