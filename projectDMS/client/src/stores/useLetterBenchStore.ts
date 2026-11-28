import { create } from "zustand";

// Interaction state for the Letter Bench. Server state (board, steps, skills)
// lives in React Query; this store holds only what the user is pointing at.

export interface PendingSelection {
  start: number;
  end: number;
  text: string;
  /** Set when the selection starts inside an existing assertion: offers a split. */
  insideAssertionId?: string;
  splitAt?: number;
}

export type BenchPane = "letter" | "positions" | "reply";

interface LetterBenchState {
  selectedAssertionId: string | null;
  /** Assertion lit up by hovering or focusing its highlight, card or reply paragraph. */
  activeAssertionId: string | null;
  openStepId: string | null;
  paletteOpen: boolean;
  paletteAssertionId: string | null;
  pendingSelection: PendingSelection | null;
  pane: BenchPane;
  selectAssertion: (id: string | null) => void;
  setActiveAssertion: (id: string | null) => void;
  openStep: (stepId: string | null) => void;
  openPalette: (assertionId?: string | null) => void;
  closePalette: () => void;
  setPendingSelection: (selection: PendingSelection | null) => void;
  setPane: (pane: BenchPane) => void;
  reset: () => void;
}

const initial = {
  selectedAssertionId: null,
  activeAssertionId: null,
  openStepId: null,
  paletteOpen: false,
  paletteAssertionId: null,
  pendingSelection: null,
  pane: "positions" as BenchPane,
};

export const useLetterBenchStore = create<LetterBenchState>((set) => ({
  ...initial,
  selectAssertion: (id) => set({ selectedAssertionId: id, activeAssertionId: id }),
  setActiveAssertion: (id) => set({ activeAssertionId: id }),
  openStep: (stepId) => set({ openStepId: stepId }),
  openPalette: (assertionId) =>
    set((state) => ({
      paletteOpen: true,
      paletteAssertionId: assertionId === undefined ? state.selectedAssertionId : assertionId,
    })),
  closePalette: () => set({ paletteOpen: false }),
  setPendingSelection: (selection) => set({ pendingSelection: selection }),
  setPane: (pane) => set({ pane }),
  reset: () => set({ ...initial }),
}));
