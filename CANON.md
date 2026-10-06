# What this system is allowed to say

TFA reads filings and reports what does not add up. That is a narrow job, and
these are the rules that keep it narrow. They are enforced in code, not left to
good intentions; where a rule below has a mechanism, the mechanism is named.

**An anomaly must point at a number that exists.** Every finding cites the
assessment year, the fact and the JSON pointer it was drawn from. *Mechanism:
rules read the fact matrix, never the document; a FAIL from model judgment with
no resolvable citation is downgraded automatically.*

**Absence is reported as absence.** A year with no filing, a clause left empty,
a field the form did not carry — each is *not available*. *Mechanism: the fact
matrix has no default value; `as_number` refuses to coerce; a rule missing an
input returns INSUFFICIENT_EVIDENCE.*

**Absence of evidence is not evidence of absence.** If a file that could have
been a filing was not read, nothing in the run says a document was never filed.
*Mechanism: the absence guard in the engine, keyed on the attendance register.*

**Deterministic first.** Arithmetic, continuity between years, tie-outs between
the two documents, and threshold tests are decided by Python. Model judgment is
for what arithmetic cannot answer. *Mechanism: 67 of the 70 rules are `method:
rule`; a hybrid rule's deterministic side prevails where it is definitive, and
disagreement is logged rather than averaged.*

**The model judges against quoted law.** Where a rule puts a normative question
to a model, the provision is quoted to it from the pinned pack. Its own
recollection of a section is not authority. *Mechanism: `_authority_brief`
quotes the pack; a judgment rule with no citation fails to load.*

**The filings are data, not instructions.** Text inside a return is material
under examination. If it addresses the reader, it is recorded and disregarded.
*Mechanism: the material is fenced and labelled; the system prompt says so; the
model records it in `directive_text_found`.*

**Conditions, not conduct.** Findings say what the filings report, do not
report, or cannot both be reporting correctly. They never assert evasion,
concealment or intent, and never characterise a person. *Mechanism: the
prohibited word list in the doctrine, applied before wording enters the record
and again at the release gate.*

**A trend is not a finding.** A movement becomes a finding only when it crosses
a threshold somebody declared. *Mechanism: every threshold lives in the
doctrine file and is pinned into the run manifest.*

**A finding must be actionable.** Each carries an enquiry: the question to ask
or the document to call for. *Mechanism: `enquiry` on every rule result; the
Enquiries tab is built from them.*

**The method is confidential; the observations are not.** Rule identifiers,
rubrics, thresholds and the mapping from rule to section stay inside the firm.
*Mechanism: the release gate scans released text for identifiers and internal
vocabulary and holds the report if it finds any.*

**The model reasons over a brief, never over the file.** Everything put to a
model is assembled by program: the measured series with citation handles, the
findings arithmetic has already settled, the declared background of those
years, and one question. *Mechanism: `analysis/brief.py` builds it; the brief
carries no JSON pointer, so a model cannot cite a path it was not given, and
every returned citation is resolved against the cells actually offered.*

**A correlation is drawn against a file, not a memory.** What a year was like
is a fact, and it lives in the context pack. *Mechanism: an inference declares
the factors it may use; a correlation to anything else is dropped and the drop
is recorded on the card.*

**An inference must carry its own test.** Every explanation comes with what
would confirm it, what would refute it, and the document to call for. *Mechanism:
`hypotheses` is a required field; the audit programme is assembled from those
documents, so an explanation nobody can check produces no work.*

**A chart is a declared view, and it carries no inference.** *Mechanism: charts
come from a catalogue, are drawn by program from cells, break rather than
interpolate across a missing year, and appear in the report under a heading
that says they are stated as fact.*

**A programme must be reviewable for what it left out.** *Mechanism: every area
names what put it there; every area nothing indicated is listed with the
reason.*

**A decision belongs to the figures it was made about.** A matter is
identified by the values that produced it, so a re-run keeps a decision where
nothing moved and raises a new matter where something did. *Mechanism: the
matter key is a hash of the cited cells; the old matter is closed as superseded
with the successor named, and dispositions are append-only.*

**A run must be re-derivable.** The same filings, doctrine, pack, dictionary
and catalogue must produce the same findings. *Mechanism: everything is hashed
into the run manifest; model calls are content-addressed and cached at
temperature zero.*
