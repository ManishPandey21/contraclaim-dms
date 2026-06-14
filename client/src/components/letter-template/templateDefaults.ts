import type { LetterTemplateSection } from "@/services/enhanced-api";

export const DEFAULT_TEMPLATE_SECTIONS: LetterTemplateSection[] = [
  {
    id: "header",
    name: "Letter Header",
    type: "header",
    enabled: true,
    content:
      "<p><strong>CONSULTING ENGINEERS & ARCHITECTS</strong></p><p>Project Management Consultants</p>",
    order: 0,
  },
  {
    id: "addressee",
    name: "Addressee Details",
    type: "addressee",
    enabled: true,
    content:
      "<p><strong>Att.:</strong> {{addressee_name}}</p><p><strong>Designation:</strong> {{addressee_designation}}</p><p><strong>Organization:</strong> {{addressee_organization}}</p><p><strong>Address:</strong> {{addressee_address}}</p>",
    order: 1,
  },
  {
    id: "contract",
    name: "Contract Details",
    type: "contract",
    enabled: true,
    content: "<p><strong>Contract:</strong> {{contract_title}}</p>",
    order: 2,
  },
  {
    id: "subject",
    name: "Subject Line",
    type: "subject",
    enabled: true,
    content: "<p><strong>Subject:</strong> {{letter_subject}}</p>",
    order: 3,
  },
  {
    id: "references",
    name: "References",
    type: "references",
    enabled: true,
    content:
      "<p><strong>Ref.:</strong></p><ol><li>{{reference_1}}</li><li>{{reference_2}}</li><li>{{reference_3}}</li></ol>",
    order: 4,
  },
  {
    id: "body",
    name: "Letter Body",
    type: "body",
    enabled: true,
    content:
      "<p>Dear Sir,</p><p>{{letter_body_paragraph_1}}</p><p>{{letter_body_paragraph_2}}</p><p>This is for your information and necessary action.</p><p>Thanking you,</p>",
    order: 5,
  },
  {
    id: "signature",
    name: "Signature Block",
    type: "signature",
    enabled: true,
    content:
      "<p>for {{organization_name}}</p><br/><br/><p>{{signatory_name}}</p><p><em>{{signatory_designation}}</em></p>",
    order: 6,
  },
  {
    id: "copyTo",
    name: "Copy To",
    type: "copyTo",
    enabled: true,
    content:
      "<p><strong>Copy to:</strong></p><p>1. {{copy_to_1}}</p><p>2. {{copy_to_2}}</p>",
    order: 7,
  },
];
