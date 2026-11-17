# Head-office overhead formulas: sources and what they mean for the product

Plan 4B-24. Recorded 2026-11-17 from the owner-supplied sources. Quotations are
copied from the source text as retrieved; nothing here is stated from memory.

## 1. McDermott International Inc. v. Burn Standard Co. Ltd. & Ors (12 May 2006)

Source read: https://indiankanoon.org/doc/996348/?type=print (owner-supplied), page
header "Mcdermott International Inc vs Burn Standard Co. Ltd. & Ors on 12 May, 2006";
equivalent citation printed on the page includes 2006 (11) SCC 181. Bench: B.P. Singh
and S.B. Sinha JJ. (judgment by S.B. Sinha J.). The Court's own paragraphs are not
numbered in this text, so passages are located by the judgment's headings
("METHOD FOR COMPUTATION OF DAMAGES", "ACTUAL LOSS : DETERMINATION OF", "CLAUSE 37").

### What the Court said (verbatim)

Nature of the claim:

> "A claim for overhead costs resulting in decrease in profit or additional
> management costs is a claim for damages."

> "While claiming damages, the amount therefor was not required to be quantified.
> Quantification of a claim is merely a matter of proof."

The formulas, as the Court set them out:

> Hudson: "In the Hudson formula, the head office overhead percentage is taken from
> the contract. Although the Hudson formula has received judicial support in many
> cases, it has been criticized principally because it adopts the head office
> overhead percentage from the contract as the factor for calculating the costs, and
> this may bear little or no relation to the actual head office costs of the
> contractor."

> Emden: "Using the Emden formula, the head office overhead percentage is arrived at
> by dividing the total overhead cost and profit of the contractor's organization as
> a whole by the total turnover. This formula has the advantage of using the
> contractors actual head office and profit percentage rather than those contained in
> the contract."

> Eichleay (three steps): "Contract Billings / Total Billings for contract period x
> Total overhead for contract period = Overhead allocable to the contract";
> "Allocable overhead / Total days of contract = Daily Overhead rate"; "Daily
> Contract Overhead Rate x Number of Days of delay = Amount of Unabsorbed overhead".

> "This formula is used where it is not possible to prove loss of opportunity and the
> claim is based on actual cost."

Choice of formula:

> "We do not intend to delve deep into the matter as it is an accepted position that
> different formulas can be applied in different circumstances and the question as
> to whether damages should be computed by taking recourse to one or the other
> formula, having regard to the facts and circumstances of a particular case, would
> eminently fall within the domain of the Arbitrator."

Proof of actual loss (Sections 55 and 73, Indian Contract Act):

> "The learned Arbitrator did insist that sufferance of actual damages must be proved
> by bringing on record books of account and other relevant documents."

> "Sections 55 and 73 of the Indian Contract Act do not lay down the mode and manner
> as to how and in what manner the computation of damages or compensation has to be
> made. There is nothing in Indian law to show that any of the formulae adopted in
> other countries is prohibited in law or the same would be inconsistent with the law
> prevailing in India."

> "A court of law or an arbitrator may insist on some proof of actual damages, and
> may not allow the parties to take recourse to one formula or the other. In a given
> case, the court of law or an arbitrator may even prefer one formula as against
> another. But, only because the learned arbitrator in the facts and circumstances of
> the case has allowed MII to prove its claim relying on or on the basis of Emden
> Formula, the same by itself, in our opinion, would not lead to the conclusion that
> it was in breach of Sections 55 or Section 73 of the Indian Contract Act."

### Points to note in the text

- The Court's Eichleay description divides by "Total days of contract" in the
  formula, then says the allocated amount "is then expressed as a weekly amount by
  dividing it by the contract period". The product divides by days of actual
  performance, as the formula's step 2 states; the "weekly" wording is the Court's
  narrative, not a different method.
- The Court describes Eichleay's billings ratio in terms of the "value of work
  carried out in the contract period". The product's labels (billings on this
  contract during actual performance, total company billings for the same period)
  follow the formula's own terms.
- The Court treats the Emden formula as described in Emden's text with profit
  included ("Head office overhead & profit"). The product's Emden inputs are
  "overhead and profit" figures from the same accounts, matching.

### What follows for the product (calculation is not entitlement)

1. **A formula amount is a quantification, not proof of loss.** The Court calls the
   claim one for damages, says quantification "is merely a matter of proof", and
   says a tribunal "may insist on some proof of actual damages". The product
   therefore marks every formula line as a calculation (`calculated`,
   `calculation_note`) and never treats it as a determined or recoverable amount.
2. **The choice of formula is for the tribunal**, "having regard to the facts and
   circumstances". The product computes the formula a person chooses and does not
   recommend one.
3. **Each formula carries its own caution** from the judgment: Hudson's tender
   percentage "may bear little or no relation to the actual head office costs";
   Emden uses the actual percentage from the accounts; Eichleay is used "where it is
   not possible to prove loss of opportunity and the claim is based on actual cost".
   These are shown beside the line (`FORMULA_CAUTIONS` in
   `services/quantum_formulas.py`).
4. **Evidence:** every formula input is tied to a record linked to the claim; the
   line locks if that record is unlinked (4B-24). Whether the record proves actual
   loss is a person's decision, not the calculation's.

## 2. PMI, "Calculation and Recovery of Home Office Overhead"

Owner-supplied URL: https://www.pmi.org/learning/library/calculation-recovery-home-office-overhead-8913

**Not read.** On 2026-11-17 the page returned HTTP 403 with PMI's own error page to
both the fetch tool and a direct request from this environment (the site refuses
non-browser clients). Fetching copies from other hosts was refused by this session's
permission controls earlier the same day. Nothing from the paper is reproduced or
relied on, and **the PMI Eichleay fixture is not added.** To close the gap: attach or
paste the paper (or its worked-example page); the fixture will then reproduce its
inputs, each intermediate value and its rounding exactly as printed.
