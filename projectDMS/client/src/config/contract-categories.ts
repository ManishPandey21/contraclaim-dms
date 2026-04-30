export type ContractCategoryKey =
  | "general"
  | "employer"
  | "engineer"
  | "contractor"
  | "nominated"
  | "staff"
  | "plant"
  | "commencement"
  | "tests"
  | "takingover"
  | "defects"
  | "measurement"
  | "variations"
  | "payment"
  | "termination"
  | "risk"
  | "insurance"
  | "forcemajeure"
  | "claims";

export interface ContractCategory {
  keywords: string[];
  description: string;
}

export const CONTRACT_CATEGORIES: Record<
  ContractCategoryKey,
  ContractCategory
> = {
  general: {
    keywords: [
      "general provisions",
      "definitions",
      "interpretation",
      "law and language",
      "priority of documents",
    ],
    description: "General provisions and definitions",
  },
  employer: {
    keywords: [
      "employer",
      "employer's personnel",
      "employer's claims",
      "employer's risks",
    ],
    description: "Employer responsibilities and rights",
  },
  engineer: {
    keywords: [
      "engineer",
      "engineer's duties",
      "engineer's authority",
      "engineer's determination",
    ],
    description: "Engineer role and authority",
  },
  contractor: {
    keywords: [
      "contractor",
      "contractor's general obligations",
      "performance security",
      "contractor's representatives",
    ],
    description: "Contractor responsibilities and obligations",
  },
  nominated: {
    keywords: [
      "nominated subcontractors",
      "objection to nomination",
      "payments to nominated subcontractors",
    ],
    description: "Nominated subcontractors",
  },
  staff: {
    keywords: [
      "staff and labour",
      "rates of wages",
      "labour conditions",
      "personnel and equipment records",
    ],
    description: "Staff and labor provisions",
  },
  plant: {
    keywords: [
      "plant materials and workmanship",
      "manner of execution",
      "inspections",
      "testing",
    ],
    description: "Plant, materials and workmanship",
  },
  commencement: {
    keywords: [
      "commencement of works",
      "time for completion",
      "programme",
      "rate of progress",
    ],
    description: "Commencement, delays and suspension",
  },
  tests: {
    keywords: [
      "tests on completion",
      "delayed tests",
      "retesting",
      "failure to pass tests on completion",
    ],
    description: "Tests on completion",
  },
  takingover: {
    keywords: [
      "employer's taking over",
      "taking-over certificate",
      "interference with tests on completion",
    ],
    description: "Employer's taking over",
  },
  defects: {
    keywords: [
      "defects liability",
      "completion of outstanding work",
      "removal of defective work",
    ],
    description: "Defects liability",
  },
  measurement: {
    keywords: [
      "measurement and evaluation",
      "works to be measured",
      "valuation",
      "omissions",
    ],
    description: "Measurement and evaluation",
  },
  variations: {
    keywords: [
      "variations and adjustments",
      "value engineering",
      "variation procedure",
      "adjustments for changes in cost",
    ],
    description: "Variations and adjustments",
  },
  payment: {
    keywords: [
      "contract price and payment",
      "advance payment",
      "payment certificates",
      "delayed payment",
    ],
    description: "Contract price and payment",
  },
  termination: {
    keywords: [
      "termination by employer",
      "termination by contractor",
      "payment after termination",
    ],
    description: "Termination provisions",
  },
  risk: {
    keywords: [
      "risk and responsibility",
      "indemnities",
      "limitation of liability",
      "intellectual property",
    ],
    description: "Risk and responsibility",
  },
  insurance: {
    keywords: [
      "insurance",
      "insurance for works and contractor's equipment",
      "insurance against injury to persons and damage to property",
    ],
    description: "Insurance provisions",
  },
  forcemajeure: {
    keywords: [
      "force majeure",
      "definition of force majeure",
      "notice of force majeure",
      "consequences of force majeure",
    ],
    description: "Force majeure provisions",
  },
  claims: {
    keywords: [
      "claims disputes and arbitration",
      "contractor's claims",
      "appointment of the dispute board",
      "arbitration",
    ],
    description: "Claims, disputes and arbitration",
  },
};

export const CATEGORY_ORDER: ContractCategoryKey[] = [
  "general",
  "employer",
  "engineer",
  "contractor",
  "nominated",
  "staff",
  "plant",
  "commencement",
  "tests",
  "takingover",
  "defects",
  "measurement",
  "variations",
  "payment",
  "termination",
  "risk",
  "insurance",
  "forcemajeure",
  "claims",
];
