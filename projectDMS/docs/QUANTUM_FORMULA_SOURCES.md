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

## 2. PMI: Zack, "Calculation and recovery of home office overhead" (2000)

Source read: the owner-supplied PDF of the PMI page (the web page itself answers
automated requests with HTTP 403). As printed: "Zack, J. G. (2000). Calculation and
recovery of home office overhead. Paper presented at Project Management Institute
Annual Seminars & Symposium, Houston, TX." Conference paper dated 7 September 2000.
The McDermott quotations in section 1 were also checked, word for word, against the
owner-supplied PDF of the judgment.

### The worked case and how it rounds

The paper works one hypothetical case through eight formulas (figures in US dollars):
contract billings in the actual period $76,866,128 (also "Total Contract Value (before
claim)"); total firm revenue, actual period $381,095,333; company overhead, actual
period $28,918,417; actual duration 655 calendar days; owner-caused delay 235 days;
planned duration 365 days; original contract value $68,500,000; bid overhead and
profit 7.0%; total overhead and profit, actual period $37,156,795.

Its printed Eichleay working: "($76,866,128 ÷ $381,095,333) x $28,918,417 =
$5,832,787", then "$8,905/cd", then "$8,905 x 235 cd's = $2,092,675".

**Rounding convention, recovered from its figures.** The ratio is rounded to six
decimal places (0.201698); each money step and the daily rate to whole dollars; the
amount is the rounded daily rate times the days. This reproduces every Eichleay
figure in the paper, including both Modified Eichleay variations, and its Hudson
($13,137/day, $3,087,195) and Emden ($18,298/day, $4,300,030) results. Without the
six-place ratio, step 1 is $5,832,784, not the printed $5,832,787.

**Misprint noted.** The Hudson line is printed "7.0% x ($68,500,000 x 365 cd's) =
$13,137/cd"; the paper's own formula divides by the original contract period, and
only division gives $13,137.

**The product's convention differs on purpose.** It does not round on the way and
states the amount to the paisa: the same Eichleay inputs give 2,092,678.21, against
the paper's 2,092,675. `quantum_formulas.steps` takes a `Rounding` so a worked
example can be reproduced exactly; tests/test_quantum_formulas.py does so for the
paper.

### What the paper says about entitlement (summarised, with short quotations)

- It presents the formulas as estimating, not accounting: what "is generally
  presented as an accounting technique is obviously an estimating approach that
  yields wildly different results, even when applied to the same case" (Eichleay
  $2,092,675 against Emden $4,300,030 on the same facts).
- Prerequisites it derives from US Federal decisions for unabsorbed overhead:
  compensable (owner-caused) delay proven; a "reduction in the stream of income";
  inability to take on new work during the delay (the unknown-duration rule); in
  its summary rules also standby, and delay not arising from directed changes; it
  notes "no recovery can be had when concurrent delay can be shown".
- These are US Federal rules; the paper says most State courts had not adopted them.
  They are not Indian law. McDermott (section 1) is the governing authority here.

### What follows for the product

- The Eichleay labels already follow the paper's terms: billings of this contract in
  the actual period (before the claim), total company billings and overhead for the
  same period, days of actual performance.
- The calculation note and per-formula cautions stand: a formula gives an estimate;
  entitlement (owner-caused delay, reduced income, inability to mitigate, no
  concurrency) is decided separately, and the delay assessment's compensable days
  are the days the line may use.
