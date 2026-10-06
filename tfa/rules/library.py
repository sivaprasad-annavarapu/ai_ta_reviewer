"""The deterministic rules.

Each function here answers one question from the canonical catalogue, from the
fact matrix alone, and returns a verdict with the cells it relied on. They are
the half of this system that does not need a model and must not use one:
arithmetic, continuity between years, tie-outs between the two documents, and
threshold tests are decided here, every time, the same way.

Three habits run through all of them.

  * A missing input is INSUFFICIENT_EVIDENCE, never a pass and never a zero.
  * A difference below the declared materiality floor is not reported, however
    large it looks as a percentage.
  * Every FAIL carries an enquiry: the question to put to the client, or the
    document to call for. A finding a reviewer cannot act on has not finished.
"""
from typing import Callable, Dict, List, Optional

from ..foundation import years as Y
from .context import (
    FAIL, INSUFFICIENT_EVIDENCE, NOT_APPLICABLE, PASS, RuleContext, RuleResult,
    bad, money, not_applicable, ok, pct, unknown,
)

REGISTRY: Dict[str, Callable[[RuleContext], RuleResult]] = {}


def rule(name: str):
    def register(fn):
        if name in REGISTRY:
            raise ValueError(f"two rules registered as {name!r}")
        REGISTRY[name] = fn
        return fn
    return register


# =========================================================== attendance =====

@rule("all_files_readable")
def all_files_readable(ctx: RuleContext) -> RuleResult:
    att = ctx.filings.attendance
    problems = att.unreadable + att.unrecognised
    if not problems:
        return ok(f"Every file in the folder was read and identified "
                  f"({len(ctx.filings.filings)} files).")
    listing = "; ".join(f"{p['file']} — {p['reason']}" for p in problems[:8])
    return bad(
        f"{len(problems)} file(s) in the folder could not be read or could not be "
        f"identified as a return or an audit report: {listing}.",
        observation=f"{len(problems)} file(s) in the folder were not readable as an "
                    f"income-tax return or a tax audit report.",
        enquiry="Confirm whether these files were meant to form part of the record. "
                "Until they are read, any statement in this analysis that something "
                "was never reported is qualified by them.",
        confidence=1.0)


@rule("return_present_for_year")
def return_present_for_year(ctx: RuleContext) -> RuleResult:
    if ctx.matrix.has_source(ctx.ay, "itr"):
        return ok(f"A return of income is on file for {ctx.ay} "
                  f"({ctx.source('itr')}).", ctx.cell("pan_itr"))
    return bad(
        f"No return of income is present for {ctx.ay}.",
        observation=f"No return of income for {ctx.ay} was provided with these papers.",
        enquiry=f"Obtain the return filed for {ctx.ay}, or the acknowledgement showing "
                f"that none was due. Nothing in this analysis compares the audit "
                f"report for {ctx.ay} against a return.",
        confidence=1.0)


@rule("audit_report_present_for_year")
def audit_report_present_for_year(ctx: RuleContext) -> RuleResult:
    if ctx.matrix.has_source(ctx.ay, "3cd"):
        return ok(f"A tax audit report is on file for {ctx.ay} "
                  f"({ctx.source('3cd')}).", ctx.cell("pan_3cd"))
    audit_flag = ctx.flag("audit_flag_itr")
    if audit_flag:
        return bad(
            f"The return for {ctx.ay} states that the accounts are liable to audit, "
            f"and no audit report for the year is on file.",
            ctx.cell("audit_flag_itr"),
            observation=f"The return for {ctx.ay} states that the accounts were liable "
                        f"to audit under section 44AB, but no audit report for that "
                        f"year was provided.",
            enquiry=f"Obtain the Form 3CA-3CD or 3CB-3CD filed for {ctx.ay} together "
                    f"with its acknowledgement, and check the date it was furnished "
                    f"against the specified date.",
            confidence=0.95)
    return unknown(
        f"No audit report is present for {ctx.ay}, and the return does not say "
        f"whether one was required.",
        enquiry=f"Establish whether the turnover for {ctx.ay} crossed the section 44AB "
                f"limit. If it did, the report is missing from this record.")


@rule("no_missing_year_in_span")
def no_missing_year_in_span(ctx: RuleContext) -> RuleResult:
    missing = ctx.filings.attendance.missing_years
    if not missing:
        return ok(f"Filings are continuous across the span "
                  f"{ctx.filings.attendance.years[0]} to "
                  f"{ctx.filings.attendance.years[-1]}."
                  if ctx.filings.attendance.years else "No span to test.")
    return bad(
        f"No filing of either kind is present for {', '.join(missing)}, although "
        f"years on both sides of the gap are present.",
        observation=f"The records provided skip {', '.join(missing)}.",
        enquiry="Establish whether the assessee filed for the missing year(s). A "
                "continuity break in the middle of a span makes every carried-forward "
                "figure across it unverifiable.",
        confidence=1.0)


@rule("one_return_per_year")
def one_return_per_year(ctx: RuleContext) -> RuleResult:
    dupes = [d for d in ctx.filings.attendance.duplicates]
    if not dupes:
        return ok("Each year is represented by a single return and a single audit "
                  "report.")
    listing = "; ".join(f"{d['ay']} {d['kind'].upper()} — {d['file']} superseded by "
                        f"{d['superseded_by']}" for d in dupes)
    return unknown(
        f"More than one filing was provided for the same year and kind: {listing}. "
        f"The later one was used and the earlier excluded from the fact matrix.",
        observation="More than one filing was provided for the same year; the later "
                    "one has been used.",
        enquiry="Confirm which filing is the operative one — a revised return under "
                "section 139(5) supersedes the original, and the reasons for revision "
                "are themselves worth reading.",
        confidence=0.8)


@rule("assessment_year_readable")
def assessment_year_readable(ctx: RuleContext) -> RuleResult:
    orphans = ctx.filings.attendance.year_unknown
    if not orphans:
        return ok("Every filing states an assessment year that could be read.")
    listing = "; ".join(f"{o['file']}" for o in orphans)
    return bad(
        f"{len(orphans)} filing(s) state no assessment year that could be read, so "
        f"they take no part in the analysis: {listing}.",
        observation=f"{len(orphans)} file(s) could not be attributed to an assessment "
                    f"year and were left out.",
        enquiry="Check whether these are drafts or partial exports from the utility.",
        confidence=1.0)


# ============================================================= identity =====

@rule("pan_consistent")
def pan_consistent(ctx: RuleContext) -> RuleResult:
    seen: Dict[str, List] = {}
    for ay in ctx.matrix.years:
        for fact in ("pan_3cd", "pan_itr"):
            cell = ctx.cell(fact, ay)
            if cell and cell.value:
                seen.setdefault(str(cell.value).upper(), []).append(cell)
    if not seen:
        return unknown("No PAN could be read from any filing.")
    declared = (ctx.foundation.client.pan or "").upper()
    if declared and declared not in seen:
        cells = [c for group in seen.values() for c in group][:4]
        return bad(
            f"The PAN in the filings ({', '.join(seen)}) is not the PAN declared for "
            f"this client ({declared}).", *cells,
            observation="The permanent account number in the filings provided is not "
                        "the one recorded for this client.",
            enquiry="Confirm that the folder holds the filings of the intended "
                    "assessee before anything else in this analysis is relied on.",
            confidence=1.0)
    if len(seen) > 1:
        cells = [group[0] for group in seen.values()]
        return bad(
            f"More than one PAN appears across the filings: {', '.join(seen)}.", *cells,
            observation="The filings provided do not all carry the same permanent "
                        "account number.",
            enquiry="Establish whether filings of two assessees have been placed in "
                    "one folder.",
            confidence=1.0)
    pan = next(iter(seen))
    return ok(f"One PAN, {pan}, appears throughout"
              + (" and matches the declared context." if declared else
                 " (there is no declared context to compare it against)."),
              seen[pan][0], confidence=1.0 if declared else 0.85)


@rule("name_consistent")
def name_consistent(ctx: RuleContext) -> RuleResult:
    def norm(text: str) -> str:
        return " ".join(str(text).lower().replace(".", " ").replace(",", " ").split())

    seen: Dict[str, List] = {}
    for ay in ctx.matrix.years:
        for fact in ("name_3cd", "name_itr"):
            cell = ctx.cell(fact, ay)
            if cell and cell.value:
                seen.setdefault(norm(cell.value), []).append(cell)
    if not seen:
        return unknown("No name of the assessee could be read from any filing.")
    if len(seen) > 1:
        cells = [group[0] for group in seen.values()]
        return bad(
            f"The name of the assessee is stated differently across the filings: "
            f"{'; '.join(str(c.value) for c in cells)}.", *cells,
            observation="The name of the assessee is not stated identically across "
                        "the filings provided.",
            enquiry="A change of name is ordinary; confirm the date it took effect and "
                    "that the change is reflected consistently from that year on.",
            confidence=0.75)
    return ok(f"The assessee is named consistently across the filings as "
              f"{next(iter(seen.values()))[0].value}.",
              next(iter(seen.values()))[0])


@rule("report_signed_before_return")
def report_signed_before_return(ctx: RuleContext) -> RuleResult:
    signed = ctx.cell("signed_on_3cd")
    filed = ctx.cell("return_filed_on_itr")
    if not signed or not filed:
        return unknown(f"Either the date the report was signed or the date the return "
                       f"was prepared is not available for {ctx.ay}.", signed, filed)
    a, b = str(signed.value)[:10], str(filed.value)[:10]
    if a <= b:
        return ok(f"The audit report for {ctx.ay} was signed on {a}, before the return "
                  f"was prepared on {b}.", signed, filed)
    return bad(
        f"The return for {ctx.ay} was prepared on {b}, before the audit report it "
        f"relies on was signed on {a}.", signed, filed,
        observation=f"For {ctx.ay} the return carries a date earlier than the date on "
                    f"the audit report.",
        enquiry="Ask for the acknowledgement of both filings. The dates inside the JSON "
                "are the dates the files were prepared, and the filing sequence should "
                "be confirmed from the acknowledgements before this is put to anyone.",
        confidence=0.7)


@rule("signing_accountant_consistent")
def signing_accountant_consistent(ctx: RuleContext) -> RuleResult:
    numbers: Dict[str, List] = {}
    for ay in ctx.matrix.years:
        cell = ctx.cell("membership_no_3cd", ay)
        if cell and cell.value:
            numbers.setdefault(str(cell.value).strip(), []).append(cell)
    if len(numbers) <= 1:
        return ok("The same accountant's membership number appears on every audit "
                  "report on file." if numbers else "No membership number was read.",
                  *(next(iter(numbers.values()))[:1] if numbers else []),
                  confidence=0.9 if numbers else 0.4)
    changes = {mno: [c.ay for c in cells] for mno, cells in numbers.items()}
    firms = {str(ctx.text("firm_reg_no_3cd", ay)) for ay in ctx.matrix.years
             if ctx.text("firm_reg_no_3cd", ay)}
    same_firm = len(firms) <= 1
    return unknown(
        f"The membership number of the signing accountant changes across the years: "
        f"{changes}. The firm registration number "
        + ("does not change." if same_firm else f"also changes ({', '.join(firms)})."),
        *[cells[0] for cells in numbers.values()],
        observation="The audit reports on file were not all signed by the same "
                    "accountant.",
        enquiry="Within a firm this is routine. Confirm the partner change, and where "
                "the firm itself changed, that the incoming auditor's communication "
                "with the outgoing one is on record.",
        confidence=0.8)


@rule("business_code_stable")
def business_code_stable(ctx: RuleContext) -> RuleResult:
    codes: Dict[str, List] = {}
    for ay in ctx.matrix.years:
        cell = ctx.cell("nature_of_business_itr", ay)
        if cell and cell.value:
            key = ", ".join(sorted(str(v) for v in
                                   (cell.value if isinstance(cell.value, list)
                                    else [cell.value])))
            codes.setdefault(key, []).append(cell)
    if len(codes) <= 1:
        return ok("The business code reported in the return does not change over the "
                  "years." if codes else "No business code was read from the returns.",
                  *(next(iter(codes.values()))[:1] if codes else []),
                  confidence=0.9 if codes else 0.4)

    changed_years = [cells[0].ay for cells in list(codes.values())[1:]]
    flagged = [ay for ay in changed_years if ctx.flag("flag_business_change_3cd", ay)]
    if flagged == changed_years:
        return ok("The business code changes, and the audit report for the same year "
                  "reports a change in the nature of the business.",
                  *[cells[0] for cells in codes.values()])
    return bad(
        f"The business code reported in the return changes ({' → '.join(codes)}) while "
        f"the audit report for {', '.join(y for y in changed_years if y not in flagged)} "
        f"states that there was no change in the nature of the business.",
        *[cells[0] for cells in codes.values()],
        observation="The business code in the return changes in a year in which the "
                    "audit report reports no change in the nature of the business.",
        enquiry="Establish which is right. A changed code with no change in the "
                "business is a filing error; a changed business with the particulars "
                "answered 'no' is a defect in the particulars.",
        confidence=0.85)


@rule("registration_numbers_stable")
def registration_numbers_stable(ctx: RuleContext) -> RuleResult:
    seen = {}
    for ay in ctx.matrix.years:
        cell = ctx.cell("gstin_3cd", ay)
        if cell and cell.value:
            seen[ay] = (set(cell.value if isinstance(cell.value, list)
                            else [cell.value]), cell)
    if len(seen) < 2:
        return unknown("Fewer than two years report an indirect tax registration.")
    years = list(seen)
    dropped = []
    for a, b in zip(years, years[1:]):
        gone = seen[a][0] - seen[b][0]
        if gone:
            dropped.append((b, gone))
    if not dropped:
        return ok("The indirect tax registrations reported carry forward from year to "
                  "year without any disappearing.", seen[years[0]][1])
    listing = "; ".join(f"{ay}: {', '.join(sorted(gone))}" for ay, gone in dropped)
    return unknown(
        f"A registration reported in one year is not reported in the next — {listing}.",
        *[seen[ay][1] for ay, _ in dropped],
        observation="An indirect tax registration reported in one year is absent from "
                    "the next.",
        enquiry="Ask whether the registration was surrendered or the place of business "
                "closed, and reconcile the year of surrender against the turnover "
                "reported for that state.",
        confidence=0.7)


@rule("previous_year_matches_assessment_year")
def previous_year_matches_assessment_year(ctx: RuleContext) -> RuleResult:
    start, end = ctx.cell("py_start_3cd"), ctx.cell("py_end_3cd")
    if not start or not end:
        return unknown(f"The previous year is not stated in the audit report for "
                       f"{ctx.ay}.")
    expected_start, expected_end = Y.previous_year(ctx.ay)
    got_start, got_end = str(start.value)[:10], str(end.value)[:10]
    if got_start == expected_start.isoformat() and got_end == expected_end.isoformat():
        return ok(f"The previous year stated ({got_start} to {got_end}) is the year "
                  f"assessed by {ctx.ay}.", start, end)
    if got_end == expected_end.isoformat():
        return unknown(
            f"The previous year runs from {got_start} rather than "
            f"{expected_start.isoformat()}, ending as expected on {got_end}.",
            start, end,
            observation="The period covered by the audit report is shorter or longer "
                        "than a full year.",
            enquiry="A part year is ordinary in the first year of a business or after "
                    "a change of accounting date. Confirm which applies.",
            confidence=0.8)
    return bad(
        f"The audit report for {ctx.ay} states a previous year of {got_start} to "
        f"{got_end}; the year assessed by {ctx.ay} is {expected_start.isoformat()} to "
        f"{expected_end.isoformat()}.", start, end,
        observation=f"The period stated in the audit report for {ctx.ay} is not the "
                    f"year that assessment year assesses.",
        enquiry="Check whether the report has been filed against the wrong assessment "
                "year in the utility.",
        confidence=0.9)


# ============================================================== tie-outs ====
# Both documents describe the same year. Where they describe the same thing
# twice, the two statements must agree; where they do not, the difference is
# the finding and neither side is assumed to be the right one.

def _side(ctx: RuleContext, fact_id: str) -> str:
    """Where a figure came from, in words. Two facts can both be in the return —
    turnover in the profit and loss account against turnover in Schedule GST —
    and saying "in the audit report" of one of them would be wrong."""
    spec = ctx.matrix.spec(fact_id)
    if spec is None:
        return "in the filings"
    if spec.scope == "3cd":
        clause = f" (clause {spec.clause})" if spec.clause else ""
        return f"in the audit report{clause}"
    if spec.scope == "itr":
        return "in the return"
    return "as computed from the filings"


@rule("tie_out")
def tie_out(ctx: RuleContext) -> RuleResult:
    left_id = ctx.params["left"]
    right_id = ctx.params["right"]
    left, right = ctx.cell(left_id), ctx.cell(right_id)
    if not left or not right:
        missing = left_id if not left else right_id
        if not ctx.has_both_documents():
            return not_applicable(
                f"only one of the two documents is on file for {ctx.ay}, so there is "
                f"nothing to tie {ctx.label(left_id).lower()} against")
        return unknown(
            f"{ctx.label(missing)} is not available for {ctx.ay}, so the two documents "
            f"could not be compared on this point.", left, right,
            enquiry=ctx.params.get("enquiry_missing", "")
                    or f"Establish why {ctx.label(missing).lower()} is not reported "
                       f"for {ctx.ay}.")
    a, b = left.number, right.number
    if a is None or b is None:
        return unknown(f"One of the two figures for {ctx.ay} is not a number.",
                       left, right)
    if ctx.agrees(a, b):
        return ok(f"{ctx.label(left_id)} ({money(a)}) agrees with "
                  f"{ctx.label(right_id).lower()} for {ctx.ay}.", left, right)
    gap = a - b
    if not ctx.significant(left_id, gap, max(abs(a), abs(b))):
        return ok(f"{ctx.label(left_id)} and {ctx.label(right_id).lower()} differ by "
                  f"{money(gap)} for {ctx.ay}, which is below the reporting floor of "
                  f"{money(ctx.floor)}.", left, right, confidence=0.85)
    subject = ctx.params.get("subject", "figure")
    left_side = ctx.params.get("left_label") or _side(ctx, left_id)
    right_side = ctx.params.get("right_label") or _side(ctx, right_id)
    return bad(
        f"For {ctx.ay} the {subject} is reported as {money(a)} {left_side} and "
        f"{money(b)} {right_side}, a difference of {money(gap)}.", left, right,
        amount=abs(gap),
        observation=f"For {ctx.ay} the {subject} reported {right_side} does not agree "
                    f"with the {subject} reported {left_side}; the difference is "
                    f"{money(gap)}.",
        enquiry=ctx.params.get("enquiry") or
                f"Reconcile the {subject} between the two documents and obtain the "
                f"working that explains the difference. Where the return is the lower "
                f"of the two, establish what was excluded and on what basis.",
        confidence=0.95)


@rule("dates_agree")
def dates_agree(ctx: RuleContext) -> RuleResult:
    left_id, right_id = ctx.params["left"], ctx.params["right"]
    left, right = ctx.cell(left_id), ctx.cell(right_id)
    if not left or not right:
        return unknown(f"One of the two dates is not stated for {ctx.ay}.", left, right)
    a, b = str(left.value)[:10], str(right.value)[:10]
    if a == b:
        return ok(f"{ctx.label(left_id)} and {ctx.label(right_id).lower()} agree "
                  f"for {ctx.ay} ({a}).", left, right)
    return bad(
        f"For {ctx.ay}, {ctx.label(left_id).lower()} is {a} while "
        f"{ctx.label(right_id).lower()} is {b}.", left, right,
        observation=f"For {ctx.ay} the date of the audit report stated in the return "
                    f"is not the date on the report itself.",
        enquiry=ctx.params.get("enquiry")
                or "Confirm the date the report was actually signed and furnished, "
                   "from the acknowledgement.",
        confidence=0.85)


@rule("values_agree_across_documents")
def values_agree_across_documents(ctx: RuleContext) -> RuleResult:
    """Text, not money: the same particular stated in both documents."""
    left_id, right_id = ctx.params["left"], ctx.params["right"]
    left, right = ctx.cell(left_id), ctx.cell(right_id)
    if not left or not right:
        return unknown(f"The particular is not stated in both documents for {ctx.ay}.",
                       left, right)
    a, b = str(left.value).strip().lower(), str(right.value).strip().lower()
    if a == b:
        return ok(f"{ctx.label(left_id)} agrees across the two documents for {ctx.ay}.",
                  left, right)
    return bad(
        f"For {ctx.ay}, {ctx.label(left_id).lower()} is stated as '{left.value}' in one "
        f"document and '{right.value}' in the other.", left, right,
        observation=f"For {ctx.ay} the same particular is stated differently in the "
                    f"return and in the audit report.",
        enquiry=ctx.params.get("enquiry") or "Establish which statement is correct.",
        confidence=0.85)


# ============================================================ continuity ====
# The closing position of one year is the opening position of the next. This is
# the single most productive family of rules in a multi-year file, because a
# break in it cannot be explained by anything except a restatement, an
# assessment order, or an error — and each of those is worth knowing about.

@rule("continuity")
def continuity(ctx: RuleContext) -> RuleResult:
    closing_id = ctx.params["closing"]
    opening_id = ctx.params["opening"]
    if not ctx.prior_ay:
        return not_applicable(f"{ctx.ay} is the earliest year on file, so there is no "
                              f"preceding year to carry forward from")
    closing = ctx.cell(closing_id, ctx.prior_ay)
    opening = ctx.cell(opening_id, ctx.ay)
    if not closing or not opening:
        which = closing_id if not closing else opening_id
        return unknown(
            f"{ctx.label(which)} is not available for "
            f"{ctx.prior_ay if not closing else ctx.ay}, so continuity between the two "
            f"years could not be tested.", closing, opening)
    a, b = closing.number, opening.number
    if a is None or b is None:
        return unknown("One of the two figures is not a number.", closing, opening)
    if ctx.agrees(a, b):
        return ok(f"The {ctx.params.get('subject', 'balance')} carried from "
                  f"{ctx.prior_ay} ({money(a)}) is the amount brought into {ctx.ay}.",
                  closing, opening)
    gap = b - a
    if not ctx.significant(opening_id, gap, max(abs(a), abs(b))):
        return ok(f"The carried and brought forward amounts differ by {money(gap)}, "
                  f"below the reporting floor.", closing, opening, confidence=0.85)
    subject = ctx.params.get("subject", "balance")
    return bad(
        f"The {subject} carried out of {ctx.prior_ay} is {money(a)}; the amount brought "
        f"into {ctx.ay} is {money(b)}, {money(abs(gap))} "
        f"{'higher' if gap > 0 else 'lower'}.", closing, opening, amount=abs(gap),
        observation=f"The {subject} brought forward into {ctx.ay} is not the amount "
                    f"carried forward from {ctx.prior_ay}; the difference is "
                    f"{money(abs(gap))}.",
        enquiry=ctx.params.get("enquiry") or
                f"Ask for the reconciliation between the two years. A genuine break "
                f"has a cause on record — a revised return, an order of assessment, or "
                f"a correction — and the cause should be produced, not inferred.",
        confidence=0.95)


@rule("restated_comparative")
def restated_comparative(ctx: RuleContext) -> RuleResult:
    """This year's report states last year's figure. Last year's report also
    stated it. They should be the same number."""
    comparative_id = ctx.params["comparative"]
    original_id = ctx.params["original"]
    if not ctx.prior_ay:
        return not_applicable(f"{ctx.ay} is the earliest year on file")
    stated = ctx.cell(comparative_id, ctx.ay)
    original = ctx.cell(original_id, ctx.prior_ay)
    if not stated or not original:
        return unknown("The comparative figure or the original is not available.",
                       stated, original)
    a, b = stated.number, original.number
    if a is None or b is None:
        return unknown("One of the two figures is not a number.", stated, original)
    if ctx.agrees(a, b):
        return ok(f"The {ctx.params.get('subject', 'comparative')} for {ctx.prior_ay} "
                  f"stated in the {ctx.ay} report ({money(a)}) is what was reported for "
                  f"{ctx.prior_ay} at the time.", stated, original)
    gap = a - b
    if not ctx.material(gap):
        return ok(f"The comparative differs by {money(gap)}, below the reporting floor.",
                  stated, original, confidence=0.85)
    subject = ctx.params.get("subject", "comparative figure")
    return bad(
        f"The {subject} for {ctx.prior_ay} stated in the {ctx.ay} report is {money(a)}; "
        f"the report for {ctx.prior_ay} itself reported {money(b)} — a restatement of "
        f"{money(gap)} that is not explained in either document.",
        stated, original, amount=abs(gap),
        observation=f"The figure shown for {ctx.prior_ay} as a comparative in the "
                    f"{ctx.ay} report differs from the figure reported for "
                    f"{ctx.prior_ay} in that year's own report.",
        enquiry=ctx.params.get("enquiry") or
                "Ask which of the two is right and what was restated. A comparative "
                "restated without disclosure is the trace a revision leaves behind.",
        confidence=0.95)


@rule("depreciation_arithmetic")
def depreciation_arithmetic(ctx: RuleContext) -> RuleResult:
    opening = ctx.cell("dep_opening_wdv_3cd")
    additions = ctx.cell("dep_additions_3cd")
    deductions = ctx.cell("dep_deductions_3cd")
    allowable = ctx.cell("dep_allowable_3cd")
    closing = ctx.cell("dep_closing_wdv_3cd")
    if not all((opening, allowable, closing)):
        return unknown(f"The depreciation particulars for {ctx.ay} are incomplete.",
                       opening, allowable, closing)
    expected = (opening.number + (additions.number if additions else 0)
                - (deductions.number if deductions else 0) - allowable.number)
    gap = closing.number - expected
    if ctx.agrees(closing.number, expected) or not ctx.material(gap):
        return ok(f"The blocks cast for {ctx.ay}: opening {money(opening.number)} plus "
                  f"additions less deductions and depreciation gives the closing "
                  f"written down value reported.",
                  opening, additions, allowable, closing)
    return bad(
        f"The depreciation particulars for {ctx.ay} do not cast: opening "
        f"{money(opening.number)} + additions "
        f"{money(additions.number if additions else 0)} − deductions "
        f"{money(deductions.number if deductions else 0)} − depreciation "
        f"{money(allowable.number)} = {money(expected)}, against a closing written down "
        f"value of {money(closing.number)}, a difference of {money(gap)}.",
        opening, additions, deductions, allowable, closing, amount=abs(gap),
        observation=f"The block-wise depreciation particulars for {ctx.ay} do not add "
                    f"up; the closing written down value differs from the computed "
                    f"figure by {money(abs(gap))}.",
        enquiry="Obtain the block-wise depreciation working. A difference here changes "
                "the written down value carried into every later year.",
        confidence=0.95)


@rule("quantitative_arithmetic")
def quantitative_arithmetic(ctx: RuleContext) -> RuleResult:
    opening = ctx.cell("qty_opening_stock_3cd")
    purchases = ctx.cell("qty_purchases_3cd")
    sales = ctx.cell("qty_sales_3cd")
    closing = ctx.cell("qty_closing_stock_3cd")
    shortage = ctx.cell("qty_shortage_3cd")
    if not all((opening, purchases, sales, closing)):
        return unknown(f"The quantitative details for {ctx.ay} are incomplete.",
                       opening, purchases, sales, closing)
    residual = (opening.number + purchases.number - sales.number - closing.number)
    reported = shortage.number if shortage else 0
    gap = residual - reported
    if not ctx.significant("qty_closing_stock_3cd", gap, abs(sales.number or 0)):
        return ok(f"The quantitative details for {ctx.ay} reconcile: opening plus "
                  f"purchases less sales and closing stock leaves {money(residual)}, "
                  f"against a reported shortage of {money(reported)}.",
                  opening, purchases, sales, closing, shortage)
    return bad(
        f"The quantitative details for {ctx.ay} leave {money(residual)} unaccounted "
        f"for against a reported shortage or excess of {money(reported)}.",
        opening, purchases, sales, closing, shortage, amount=abs(gap),
        observation=f"The quantitative details for {ctx.ay} do not reconcile; "
                    f"{money(abs(gap))} is unaccounted for.",
        enquiry="Obtain the stock reconciliation. Where the residual is a shortage, ask "
                "how it was accounted for and whether it is of a size that needed to be "
                "reported as such.",
        confidence=0.9)


@rule("ratio_agrees_with_its_inputs")
def ratio_agrees_with_its_inputs(ctx: RuleContext) -> RuleResult:
    ratio = ctx.cell(ctx.params["ratio"])
    numerator = ctx.cell(ctx.params["numerator"])
    denominator = ctx.cell(ctx.params["denominator"])
    if not all((ratio, numerator, denominator)) or not denominator.number:
        return unknown(f"The ratio or its inputs are not available for {ctx.ay}.",
                       ratio, numerator, denominator)
    computed = numerator.number / denominator.number * 100
    if abs(computed - ratio.number) <= float(ctx.param("ratio_arithmetic_tolerance", 0.6)):
        return ok(f"The {ctx.params.get('subject', 'ratio')} reported for {ctx.ay} "
                  f"({ratio.number:.2f}%) is what its own numerator and denominator "
                  f"give ({computed:.2f}%).", ratio, numerator, denominator)
    return bad(
        f"The {ctx.params.get('subject', 'ratio')} reported for {ctx.ay} is "
        f"{ratio.number:.2f}%, while the numerator and denominator reported in the same "
        f"clause give {computed:.2f}%.", ratio, numerator, denominator,
        observation=f"A ratio reported for {ctx.ay} does not follow from the figures "
                    f"reported alongside it.",
        enquiry="Check whether the ratio was computed on a different base — turnover "
                "including or excluding indirect taxes is the usual explanation — and "
                "whether the same base was used in the other years.",
        confidence=0.9)


# ================================================= flags against amounts ====
# A particular answered "no" while the amount beside it is not nil is one of
# the few things in a return that cannot be a matter of judgment.

@rule("flag_versus_amount")
def flag_versus_amount(ctx: RuleContext) -> RuleResult:
    flag_id, amount_id = ctx.params["flag"], ctx.params["amount"]
    flag, amount = ctx.cell(flag_id), ctx.cell(amount_id)
    if flag is None or amount is None:
        return unknown(f"Either the particular or the amount is not reported for "
                       f"{ctx.ay}.", flag, amount)
    if not isinstance(flag.value, bool):
        # Real filings answer some particulars with "X" — not applicable — and a
        # particular that was not answered yes or no cannot contradict anything.
        return unknown(
            f"For {ctx.ay} the particular is answered {str(flag.value)!r} rather than "
            f"yes or no, so it cannot be compared with the amount reported.",
            flag, amount,
            enquiry="Establish what the answer was intended to be. A particular left "
                    "at 'not applicable' beside a figure is worth confirming against "
                    "the report as filed.")
    answered_yes = flag.value is True
    reported = amount.number or 0
    if answered_yes or reported == 0:
        return ok(f"The particular and the amount reported for {ctx.ay} are consistent "
                  f"(answered {'yes' if answered_yes else 'no'}, amount "
                  f"{money(reported)}).", flag, amount)
    subject = ctx.params.get("subject", "the matter")
    return bad(
        f"For {ctx.ay} the particulars answer 'no' to {subject} while an amount of "
        f"{money(reported)} is reported against it.", flag, amount, amount=reported,
        observation=f"For {ctx.ay} the audit report answers 'no' to {subject} while "
                    f"reporting an amount of {money(reported)} against the same matter.",
        enquiry=ctx.params.get("enquiry") or
                "One of the two entries is wrong. Establish which, and whether the "
                "report as filed carries the same contradiction.",
        confidence=0.95)


# ================================================ movements between years ==
# A movement is only a finding once it crosses a threshold that somebody
# declared in the doctrine. These rules do the crossing test and nothing else:
# they say the series moved, by how much, and what to ask. Whether the movement
# is explicable is a question for judgment, not for arithmetic.

def _trend_or_unknown(ctx: RuleContext, fact_id: str):
    trend = ctx.trend(fact_id)
    if trend is None or trend.observations < 2:
        return None, unknown(
            f"{ctx.label(fact_id)} is available for fewer than two years, so no "
            f"movement can be measured.",
            enquiry="Obtain the earlier filings if a multi-year view is wanted.")
    return trend, None


@rule("step_change")
def step_change(ctx: RuleContext) -> RuleResult:
    fact_id = ctx.params["fact"]
    trend, failure = _trend_or_unknown(ctx, fact_id)
    if failure:
        return failure
    threshold = float(ctx.param(ctx.params.get("threshold_param", "yoy_step_change"),
                                0.75))
    biggest = trend.largest_step
    if biggest is None or abs(biggest.pct or 0) < threshold:
        return ok(f"{ctx.label(fact_id)} moves by no more than "
                  f"{pct(abs(biggest.pct) if biggest and biggest.pct else 0)} in any "
                  f"year, against a step-change threshold of {pct(threshold)}.",
                  ctx.cell(fact_id, trend.latest_year))
    if not ctx.material(biggest.change) and ctx.matrix.spec(fact_id).kind == "money":
        return ok(f"{ctx.label(fact_id)} moves sharply in percentage terms but only by "
                  f"{money(biggest.change)}, below the reporting floor.",
                  ctx.cell(fact_id, biggest.ay), confidence=0.85)
    cells = [ctx.cell(fact_id, biggest.previous_ay), ctx.cell(fact_id, biggest.ay)]
    unit = "" if ctx.matrix.spec(fact_id).kind != "money" else ""
    return bad(
        f"{ctx.label(fact_id)} moves from {money(biggest.previous)} in "
        f"{biggest.previous_ay} to {money(biggest.value)} in {biggest.ay}, "
        f"{pct(biggest.pct)} — a step change against the declared threshold of "
        f"{pct(threshold)}. Across the whole span the series reads: "
        f"{_series_text(trend)}.", *cells, amount=abs(biggest.change),
        observation=f"{ctx.label(fact_id)} changes by {pct(biggest.pct)} between "
                    f"{biggest.previous_ay} and {biggest.ay}.",
        enquiry=ctx.params.get("enquiry") or
                f"Ask what happened in {biggest.ay}. A step change of this size in a "
                f"single year is either an event in the business that can be evidenced "
                f"or a change in how the figure was compiled.",
        confidence=0.9)


@rule("ratio_shift")
def ratio_shift(ctx: RuleContext) -> RuleResult:
    fact_id = ctx.params["fact"]
    trend, failure = _trend_or_unknown(ctx, fact_id)
    if failure:
        return failure
    points = float(ctx.param(ctx.params.get("threshold_param",
                                            "ratio_material_shift_pct_points"), 5.0))
    worst = max(trend.steps, key=lambda s: abs(s.change), default=None)
    if worst is None or abs(worst.change) < points:
        return ok(f"{ctx.label(fact_id)} moves by no more than "
                  f"{abs(worst.change) if worst else 0:.2f} percentage points in any "
                  f"year, against a threshold of {points:.1f}. The series reads: "
                  f"{_series_text(trend)}.", ctx.cell(fact_id, trend.latest_year))
    cells = [ctx.cell(fact_id, worst.previous_ay), ctx.cell(fact_id, worst.ay)]
    direction = "falls" if worst.change < 0 else "rises"
    return bad(
        f"{ctx.label(fact_id)} {direction} from {worst.previous:.2f}% in "
        f"{worst.previous_ay} to {worst.value:.2f}% in {worst.ay}, a movement of "
        f"{worst.change:+.2f} percentage points against a threshold of {points:.1f}. "
        f"The series reads: {_series_text(trend)}.", *cells,
        observation=f"{ctx.label(fact_id)} moves by {worst.change:+.2f} percentage "
                    f"points between {worst.previous_ay} and {worst.ay}.",
        enquiry=ctx.params.get("enquiry") or
                f"Ask for the explanation of the movement in {worst.ay}, and test it "
                f"against the other ratios for the same year — a real change in margin "
                f"usually shows up in more than one of them.",
        confidence=0.9)


@rule("emergence")
def emergence(ctx: RuleContext) -> RuleResult:
    """A head that was nil for years and is not nil now."""
    fact_id = ctx.params["fact"]
    series = ctx.series(fact_id)
    if len(series) < 2:
        return unknown(f"{ctx.label(fact_id)} is reported for fewer than two years.")
    years = list(series)
    latest, earlier = years[-1], years[:-1]
    if not all(series[y] == 0 for y in earlier):
        return ok(f"{ctx.label(fact_id)} is not new: it is reported in earlier years "
                  f"as well ({_series_text_from(series)}).",
                  ctx.cell(fact_id, latest))
    if series[latest] == 0:
        return ok(f"{ctx.label(fact_id)} is nil in every year on file.",
                  ctx.cell(fact_id, latest))
    if not ctx.material(series[latest]):
        return ok(f"{ctx.label(fact_id)} appears for the first time in {latest} but at "
                  f"{money(series[latest])}, below the reporting floor.",
                  ctx.cell(fact_id, latest), confidence=0.85)
    return bad(
        f"{ctx.label(fact_id)} is nil in {', '.join(earlier)} and {money(series[latest])} "
        f"in {latest}.", ctx.cell(fact_id, latest), amount=series[latest],
        observation=f"{ctx.label(fact_id)} is reported for the first time in {latest}, "
                    f"at {money(series[latest])}.",
        enquiry=ctx.params.get("enquiry") or
                f"Ask what changed in {latest}. A head that appears for the first time "
                f"after several nil years is either a new kind of transaction or a "
                f"change in what was being reported before.",
        confidence=0.9)


@rule("divergence")
def divergence(ctx: RuleContext) -> RuleResult:
    """Two series that ordinarily move together, moving apart."""
    a_id, b_id = ctx.params["fact"], ctx.params["against"]
    a_trend, failure = _trend_or_unknown(ctx, a_id)
    if failure:
        return failure
    b_trend, failure = _trend_or_unknown(ctx, b_id)
    if failure:
        return failure
    threshold = float(ctx.param(ctx.params.get("threshold_param",
                                               "yoy_material_change"), 0.30))
    worst = None
    for step_a in a_trend.steps:
        step_b = next((s for s in b_trend.steps if s.ay == step_a.ay), None)
        if not step_b or step_a.pct is None or step_b.pct is None:
            continue
        gap = step_a.pct - step_b.pct
        if worst is None or abs(gap) > abs(worst[0]):
            worst = (gap, step_a, step_b)
    if worst is None:
        return unknown("The two series do not share a year in which both moved.")
    gap, step_a, step_b = worst
    if abs(gap) < threshold:
        return ok(f"{ctx.label(a_id)} and {ctx.label(b_id).lower()} move together: the "
                  f"widest divergence in any year is {pct(abs(gap))}, against a "
                  f"threshold of {pct(threshold)}.",
                  ctx.cell(a_id, step_a.ay), ctx.cell(b_id, step_b.ay))
    return bad(
        f"In {step_a.ay}, {ctx.label(a_id).lower()} moves {pct(step_a.pct)} while "
        f"{ctx.label(b_id).lower()} moves {pct(step_b.pct)} — the two diverge by "
        f"{pct(abs(gap))}.",
        ctx.cell(a_id, step_a.previous_ay), ctx.cell(a_id, step_a.ay),
        ctx.cell(b_id, step_b.previous_ay), ctx.cell(b_id, step_b.ay),
        observation=f"In {step_a.ay}, {ctx.label(a_id).lower()} and "
                    f"{ctx.label(b_id).lower()} move in ways that do not correspond.",
        enquiry=ctx.params.get("enquiry") or
                f"Ask what accounts for the divergence in {step_a.ay}.",
        confidence=0.85)


@rule("outlier_year")
def outlier_year(ctx: RuleContext) -> RuleResult:
    fact_id = ctx.params["fact"]
    trend, failure = _trend_or_unknown(ctx, fact_id)
    if failure:
        return failure
    minimum = int(ctx.param("min_years_for_outlier", 3))
    if trend.observations < minimum:
        return unknown(f"{ctx.label(fact_id)} is available for {trend.observations} "
                       f"year(s); at least {minimum} are needed before an outlier "
                       f"means anything.")
    sigma = float(ctx.param("outlier_sigma", 2.0))
    z = trend.z_of_latest()
    if z is None:
        return unknown(f"The earlier years of {ctx.label(fact_id).lower()} do not vary, "
                       f"so no outlier test is possible.")
    if abs(z) < sigma:
        return ok(f"The latest year of {ctx.label(fact_id).lower()} sits {z:+.2f} "
                  f"standard deviations from the mean of the earlier years, inside the "
                  f"declared band of {sigma:.1f}.", ctx.cell(fact_id, trend.latest_year))
    return bad(
        f"{ctx.label(fact_id)} in {trend.latest_year} is {money(trend.latest)}, "
        f"{z:+.2f} standard deviations from the mean of {money(trend.mean)} for the "
        f"earlier years. The series reads: {_series_text(trend)}.",
        ctx.cell(fact_id, trend.latest_year),
        observation=f"{ctx.label(fact_id)} in {trend.latest_year} sits well outside the "
                    f"range of the earlier years.",
        enquiry=ctx.params.get("enquiry") or
                f"Ask for the explanation of {trend.latest_year} specifically, and test "
                f"it against the underlying records rather than against the trend.",
        confidence=0.8)


def _series_text(trend) -> str:
    return _series_text_from(trend.values)


def _series_text_from(values) -> str:
    return "; ".join(f"{ay} {money(v)}" for ay, v in values.items())


# ============================================== exposures on the record ====
# Amounts the filings themselves report, which carry a consequence under the
# Act. Nothing here is inferred: the number is in the return or the report, and
# the rule's work is to notice it, size it, and say what follows.

@rule("amount_reported")
def amount_reported(ctx: RuleContext) -> RuleResult:
    fact_id = ctx.params["fact"]
    cell = ctx.cell(fact_id)
    if cell is None:
        return unknown(f"{ctx.label(fact_id)} is not reported for {ctx.ay}.",
                       enquiry=ctx.params.get("enquiry_missing", ""))
    amount = cell.number or 0
    subject = ctx.params.get("subject", ctx.label(fact_id).lower())
    if amount == 0:
        return ok(f"Nil is reported for {subject} in {ctx.ay}.", cell)
    if not ctx.material(amount):
        return ok(f"{money(amount)} is reported for {subject} in {ctx.ay}, below the "
                  f"reporting floor of {money(ctx.floor)}.", cell, confidence=0.85)
    series = ctx.series(fact_id)
    history = (f" Across the years on file: {_series_text_from(series)}."
               if len(series) > 1 else "")
    return bad(
        f"{money(amount)} is reported for {subject} in {ctx.ay}.{history}", cell,
        amount=amount,
        observation=ctx.params.get("observation", "").format(
            amount=money(amount), ay=ctx.ay) or
            f"The audit report for {ctx.ay} reports {money(amount)} in respect of "
            f"{subject}.",
        enquiry=ctx.params.get("enquiry") or
                f"Confirm how {subject} was dealt with in the computation for {ctx.ay}, "
                f"and whether the consequence under the Act has been given effect to.",
        confidence=0.9)


@rule("sums_to_total")
def sums_to_total(ctx: RuleContext) -> RuleResult:
    total = ctx.cell(ctx.params["total"])
    parts = ctx.cell(ctx.params["parts"])
    if not total or not parts:
        return unknown(f"Either the total or the components are not reported for "
                       f"{ctx.ay}.", total, parts)
    gap = (parts.number or 0) - (total.number or 0)
    if ctx.agrees(parts.number, total.number) or not ctx.material(gap):
        return ok(f"The components reported for {ctx.ay} add up to the total stated "
                  f"({money(total.number)}).", total, parts)
    return bad(
        f"For {ctx.ay} the components add to {money(parts.number)} against a stated "
        f"total of {money(total.number)}, a difference of {money(gap)}.",
        total, parts, amount=abs(gap),
        observation=f"For {ctx.ay} the parts of this clause do not add up to the total "
                    f"stated in the same clause; the difference is {money(abs(gap))}.",
        enquiry=ctx.params.get("enquiry") or
                "Obtain the working behind the break-up. Where the components fall "
                "short of the total, establish what the unallocated balance represents.",
        confidence=0.95)


@rule("turnover_within_audit_threshold")
def turnover_within_audit_threshold(ctx: RuleContext) -> RuleResult:
    """Turnover over the limit, with no audit report for the year on file."""
    if ctx.matrix.has_source(ctx.ay, "3cd"):
        return ok(f"An audit report is on file for {ctx.ay}, so the question of the "
                  f"limit does not arise on this record.")
    turnover = ctx.cell("turnover_itr")
    if not turnover or turnover.number is None:
        return unknown(f"No turnover is available for {ctx.ay}, so the limit could not "
                       f"be tested.")
    provision = ctx.foundation.pack.resolve("ITA-44AB")
    limit = float(provision.key_parameters.get("business_limit_inr", 10_000_000))
    if turnover.number <= limit:
        return ok(f"Turnover for {ctx.ay} is {money(turnover.number)}, within the "
                  f"limit of {money(limit)} recorded in the authority pack.", turnover)
    return bad(
        f"Turnover for {ctx.ay} is {money(turnover.number)}, above the limit of "
        f"{money(limit)} recorded in the authority pack, and no audit report for the "
        f"year is on file.", turnover, amount=turnover.number,
        observation=f"The turnover returned for {ctx.ay} is above the threshold at "
                    f"which an audit under section 44AB is required, and no audit "
                    f"report for that year was provided.",
        enquiry="Establish whether the report was obtained and furnished. The limit "
                "recorded here is the general one; check the proviso on cash receipts "
                "and payments before treating this as a default.",
        confidence=0.7)


@rule("carry_forward_within_time")
def carry_forward_within_time(ctx: RuleContext) -> RuleResult:
    """A loss carried beyond the years the Act allows."""
    cell = ctx.cell("bf_loss_years_3cd")
    if not cell or not cell.value:
        return ok(f"No brought forward loss is reported for {ctx.ay}.")
    provision = ctx.foundation.pack.resolve("ITA-72")
    allowed = int(provision.key_parameters.get("years", 8))
    current = Y.start_year(ctx.ay)
    stale = []
    for raw in (cell.value if isinstance(cell.value, list) else [cell.value]):
        label = Y.normalise(raw)
        if not label:
            continue
        age = current - Y.start_year(label)
        if age > allowed:
            stale.append((label, age))
    if not stale:
        return ok(f"Every loss brought forward in {ctx.ay} arises within the "
                  f"{allowed} assessment years the Act allows.", cell)
    listing = "; ".join(f"{label} ({age} years back)" for label, age in stale)
    return bad(
        f"A loss is brought forward in {ctx.ay} from {listing}, beyond the "
        f"{allowed} assessment years permitted.", cell,
        observation=f"A loss brought forward in {ctx.ay} arises from a year more than "
                    f"{allowed} assessment years earlier.",
        enquiry="Check the nature of the loss: unabsorbed depreciation carries forward "
                "without limit of time and is often reported in the same table as a "
                "business loss, which is the usual explanation for this.",
        confidence=0.6)


@rule("presumptive_consistent")
def presumptive_consistent(ctx: RuleContext) -> RuleResult:
    """Presumptive income and a full tax audit in the same year, or presumptive
    claimed in one year and dropped in the next."""
    claimed = {ay: ctx.text("presumptive_section_3cd", ay) for ay in ctx.matrix.years}
    claimed = {ay: v for ay, v in claimed.items() if v}
    if not claimed:
        return ok("No presumptive section is reported in any year on file.")
    years = list(claimed)
    gaps = [ay for ay in ctx.matrix.years
            if ay not in claimed and ctx.matrix.has_source(ay, "3cd")
            and Y.start_year(ay) > Y.start_year(years[0])]
    if not gaps:
        return ok(f"Presumptive income is reported consistently in {', '.join(years)}.",
                  ctx.cell("presumptive_section_3cd", years[0]))
    return unknown(
        f"Presumptive income is reported for {', '.join(years)} but not for "
        f"{', '.join(gaps)}.",
        *[ctx.cell("presumptive_section_3cd", ay) for ay in years],
        observation="Income is reported on a presumptive basis in some years on file "
                    "and not in others.",
        enquiry="Section 44AD carries a consequence where the assessee declares under "
                "it and then does not in a later year. Establish which section was "
                "claimed and whether the five-year condition is engaged.",
        confidence=0.7)


@rule("book_profit_reported_for_company")
def book_profit_reported_for_company(ctx: RuleContext) -> RuleResult:
    if not ctx.foundation.predicates.get("mat_applicable"):
        return not_applicable("minimum alternate tax does not apply to this assessee "
                              "on the declared context")
    cell = ctx.cell("book_profit_115jb_itr")
    if cell and cell.number:
        return ok(f"Book profit under section 115JB is reported for {ctx.ay} "
                  f"({money(cell.number)}).", cell)
    if not ctx.matrix.has_source(ctx.ay, "itr"):
        return not_applicable(f"no return is on file for {ctx.ay}")
    return unknown(
        f"No book profit under section 115JB is reported in the return for {ctx.ay}.",
        observation=f"The return for {ctx.ay} does not report a book profit under "
                    f"section 115JB.",
        enquiry="Where the company has opted for a concessional regime, section 115JB "
                "does not apply and this is expected. Confirm which regime was opted "
                "for and from which year.",
        confidence=0.6)


@rule("tax_rate_plausible")
def tax_rate_plausible(ctx: RuleContext) -> RuleResult:
    """Tax against total income, tested for a year that sits apart from the rest."""
    rate = ctx.cell("effective_tax_rate")
    if not rate:
        return unknown(f"The effective rate could not be computed for {ctx.ay}.")
    series = ctx.series("effective_tax_rate")
    if len(series) < 3:
        return unknown("Fewer than three years carry an effective rate, so no "
                       "comparison across years is possible.", rate)
    others = [v for ay, v in series.items() if ay != ctx.ay]
    from statistics import mean as _mean
    average = _mean(others)
    gap = rate.number - average
    if abs(gap) <= float(ctx.param("effective_rate_band_pct_points", 6.0)):
        return ok(f"The effective rate for {ctx.ay} is {rate.number:.2f}%, against an "
                  f"average of {average:.2f}% for the other years.", rate)
    return bad(
        f"The effective rate of tax for {ctx.ay} is {rate.number:.2f}% against "
        f"{average:.2f}% for the other years on file, a difference of "
        f"{gap:+.2f} percentage points. The series reads: "
        f"{'; '.join(f'{ay} {v:.2f}%' for ay, v in series.items())}.", rate,
        observation=f"The tax borne on the income returned for {ctx.ay} is out of line "
                    f"with the other years on file.",
        enquiry="Reconcile the year's tax computation: a change of regime, a "
                "brought-forward set-off, a deduction claimed for the first time, or "
                "book profit taxation each move this rate, and each is checkable.",
        confidence=0.75)


@rule("carry_forward_not_increased")
def carry_forward_not_increased(ctx: RuleContext) -> RuleResult:
    """A loss brought forward for a given year of origin should not grow.

    It can fall — set off, or lapsed — and it can be corrected by an order of
    assessment. What it cannot do is quietly increase, because the amount was
    fixed when the year of origin was returned.
    """
    if not ctx.prior_ay:
        return not_applicable(f"{ctx.ay} is the earliest year on file")
    now = ctx.cell("bf_loss_returned_3cd", ctx.ay)
    before = ctx.cell("bf_loss_returned_3cd", ctx.prior_ay)
    if not now or now.number is None:
        return ok(f"No brought forward loss is reported for {ctx.ay}.")
    if not before or before.number is None:
        if not ctx.material(now.number):
            return ok(f"The brought forward loss reported in {ctx.ay} is below the "
                      f"reporting floor.", now)
        return unknown(
            f"A brought forward loss of {money(now.number)} is reported for {ctx.ay} "
            f"and none was reported for {ctx.prior_ay}.", now, before,
            observation=f"A brought forward loss appears in {ctx.ay} that the "
                        f"preceding year's report does not carry.",
            enquiry="Ask for the return of the year in which the loss was determined, "
                    "and for the reason it was not reported in the preceding year.",
            confidence=0.7)
    gap = now.number - before.number
    if gap <= 0 or not ctx.material(gap):
        return ok(f"The loss brought forward in {ctx.ay} ({money(now.number)}) is no "
                  f"greater than the amount reported in {ctx.prior_ay} "
                  f"({money(before.number)}).", now, before)
    return bad(
        f"The loss brought forward in {ctx.ay} is {money(now.number)}, against "
        f"{money(before.number)} reported in {ctx.prior_ay} — an increase of "
        f"{money(gap)} in a figure that was fixed when the year of origin was "
        f"returned.", now, before, amount=gap,
        observation=f"The loss brought forward reported for {ctx.ay} is larger than "
                    f"the amount reported for the same losses in {ctx.prior_ay}.",
        enquiry="Ask for the order of assessment or the revised return that increased "
                "it. Absent one, the earlier figure is the one on the department's "
                "own record.",
        confidence=0.9)
