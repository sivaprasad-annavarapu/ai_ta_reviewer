"""A synthetic client, five years deep, with defects we know about.

Nothing in TFA is measured against a real client's data during development, so
this generator writes filings in the shape the e-filing utility produces —
ITR-6 and Form 3CA-3CD — with a manifest of exactly what is wrong with them.
The manifest is what the scorecard scores against: a rule set that finds four
of eleven planted defects is not a rule set anyone should ship, and the only
way to know that is to plant them deliberately.

    python3 -m generator.generate                 # writes filings/ACME-PVT-LTD
    python3 -m generator.generate --client DEMO --clean
"""
import argparse
import json
import random
from pathlib import Path
from typing import Dict, List

ROOT = Path(__file__).resolve().parents[1]

PAN = "AABCA1234A"
NAME = ("Acme Industrial Pumps", "Private", "Limited")
YEARS = ["2021-22", "2022-23", "2023-24", "2024-25", "2025-26"]   # assessment years

DEFECTS: List[Dict] = []


def defect(defect_id: str, ay: str, where: str, description: str,
           expect: str) -> None:
    DEFECTS.append({"defect_id": defect_id, "ay": f"AY {ay}", "where": where,
                    "description": " ".join(description.split()),
                    "expected_rule": expect})


def rupees(x) -> int:
    return int(round(x))


# ---------------------------------------------------------------- the story
def story() -> Dict[str, Dict]:
    """One dictionary per assessment year holding the underlying reality, from
    which both documents are then written. Defects are introduced by making a
    document disagree with this reality, or with the year before it."""
    base = {}
    turnover = 214_000_000
    wdv = 86_000_000
    stock = 41_000_000
    bf_loss = 0

    for i, ay in enumerate(YEARS):
        turnover = rupees(turnover * (1.09 + 0.05 * random.Random(i).random()))
        gp_rate = [22.4, 22.1, 21.8, 14.2, 15.1][i]        # a step down in year four
        np_rate = [6.2, 6.0, 5.8, 1.9, 2.4][i]
        additions = [7_400_000, 9_100_000, 6_200_000, 24_800_000, 8_300_000][i]
        depreciation = rupees((wdv + additions * 0.6) * 0.135)
        closing_wdv = wdv + additions - depreciation
        opening_stock = stock
        stock = rupees(turnover * [0.19, 0.19, 0.20, 0.28, 0.27][i])

        base[ay] = {
            "ay": ay,
            "turnover": turnover,
            "gp_rate": gp_rate,
            "np_rate": np_rate,
            "gross_profit": rupees(turnover * gp_rate / 100),
            "net_profit": rupees(turnover * np_rate / 100),
            "expenditure": rupees(turnover * (1 - np_rate / 100) * 0.97),
            "opening_wdv": wdv,
            "additions": additions,
            "depreciation": depreciation,
            "closing_wdv": closing_wdv,
            "opening_stock": opening_stock,
            "closing_stock": stock,
            "purchases": rupees(turnover * 0.66),
            "unpaid_43b": [1_240_000, 1_610_000, 2_050_000, 8_900_000, 9_400_000][i],
            "opening_43b": None,           # filled below from the year before
            "tds_payments": rupees(turnover * 0.21),
            "tds_deducted": rupees(turnover * 0.21 * 0.031),
            "tds_not_deposited": [0, 0, 0, 1_180_000, 640_000][i],
            "cash_disallowance": [0, 0, 42_000, 690_000, 1_340_000][i],
            "related_party": [4_100_000, 4_400_000, 4_900_000, 18_600_000, 19_100_000][i],
            "unregistered_expenditure": [
                rupees(turnover * 0.028), rupees(turnover * 0.031),
                rupees(turnover * 0.034), rupees(turnover * 0.212),
                rupees(turnover * 0.198)][i],
            "loans_269ss": [0, 0, 0, 0, 2_600_000][i],
            "pf_received": [3_100_000, 3_300_000, 3_500_000, 3_900_000, 4_200_000][i],
            "pf_paid": [3_100_000, 3_300_000, 3_500_000, 2_700_000, 4_200_000][i],
            "bf_loss": bf_loss,
        }
        wdv = closing_wdv
    return base


# ------------------------------------------------------------------ 3CA-3CD
def build_3cd(year: Dict, prior: Dict, index: int) -> Dict:
    ay = year["ay"]
    start = f"{int(ay[:4]) - 1}-04-01"
    end = f"{ay[:4]}-03-31"

    # ---- planted: the closing written down value of AY 2023-24 is not the
    # opening written down value reported for AY 2024-25 -------------------
    opening_wdv = year["opening_wdv"]
    if ay == "2024-25":
        opening_wdv = year["opening_wdv"] + 5_600_000
        defect("D-01", ay, "clause 18 opening WDV",
               "The opening written down value reported does not equal the closing "
               "written down value reported for the preceding year; the block is "
               "overstated by 56,00,000.",
               "DEP-001")

    # ---- planted: closing stock of AY 2022-23 is not the opening stock of
    # AY 2023-24 ------------------------------------------------------------
    opening_stock = year["opening_stock"]
    if ay == "2023-24":
        opening_stock = year["opening_stock"] - 3_400_000
        defect("D-02", ay, "clause 35 opening stock",
               "The opening stock in the quantitative details does not equal the "
               "closing stock reported for the preceding year.",
               "STK-001")

    # ---- planted: the preceding-year turnover restated ---------------------
    preceding_turnover = prior["turnover"] if prior else 0
    if ay == "2023-24" and prior:
        preceding_turnover = rupees(prior["turnover"] * 0.92)
        defect("D-03", ay, "clause 40 preceding year turnover",
               "The preceding year's turnover stated in this report differs from the "
               "turnover this firm reported for that year in the report for that year.",
               "RAT-001")

    # ---- planted: 43B closing liability does not carry into the next year --
    opening_43b = prior["unpaid_43b"] if prior else 0
    if ay == "2023-24" and prior:
        opening_43b = 0
        defect("D-04", ay, "clause 26 pre-existing liability",
               "No pre-existing 43B liability is reported although the preceding "
               "year's report showed a liability unpaid at the year end.",
               "STA-001")

    # ---- planted: tax deducted but not deposited, with the default flag N --
    tds_flag = "N" if ay == "2024-25" else ("Y" if year["tds_not_deposited"] else "N")
    if ay == "2024-25":
        defect("D-05", ay, "clause 34 flag vs amount",
               "An amount of tax deducted and not paid to the credit of the Central "
               "Government is reported, while the particulars state that there was no "
               "default under Chapter XVII-B.",
               "PAR-002")

    # ---- planted: 40A(3) amount reported with the flag answered no ---------
    cash_flag = "N" if ay == "2025-26" else ("Y" if year["cash_disallowance"] else "N")
    if ay == "2025-26":
        defect("D-06", ay, "clause 21(d) flag vs amount",
               "An amount inadmissible under section 40A(3) is reported while the "
               "particulars state that no payment was covered by that section.",
               "PAR-001")

    # ---- planted: the GST break-up does not add up ------------------------
    total_expenditure = year["expenditure"]
    unregistered = year["unregistered_expenditure"]
    registered = total_expenditure - unregistered
    if ay == "2022-23":
        registered = rupees(registered * 0.86)
        defect("D-07", ay, "clause 44 break-up",
               "The components of the expenditure break-up do not add up to the total "
               "expenditure stated in the same clause.",
               "GST-001")

    # ---- planted: brought forward loss appears from nowhere ---------------
    bfl = []
    if ay in ("2024-25", "2025-26"):
        amount = 40_000_000 if ay == "2024-25" else 55_000_000
        bfl = [{"AssessmentYear": "2020-21", "NatureOfLossAllw": "1",
                "AmtReturned": amount, "AmtAssessed": amount,
                "amtAdjusted": 0, "allnotSection115baa": "N",
                "OrderNo": "", "DateOfOrder": "", "Remarks": ""}]
        if ay == "2025-26":
            defect("D-08", ay, "clause 32(a) brought forward loss",
                   "The loss brought forward for assessment year 2020-21 is larger "
                   "than the amount carried forward for the same year in the "
                   "preceding year's report, with no order of assessment cited.",
                   "LOS-001")

    # Clause 35 is quantitative: units, not rupees. Opening plus purchases less
    # sales and closing stock is the shortage, and it has to reconcile.
    unit_price = 2600
    qty = {
        "opening": rupees(year["opening_stock"] / unit_price),
        "purchases": rupees(year["purchases"] / unit_price),
        "sales": rupees(year["turnover"] / unit_price * 0.62),
        "closing": rupees(year["closing_stock"] / unit_price),
    }
    if ay == "2023-24":
        qty["opening"] = qty["opening"] - 1300
    qty["shortage"] = (qty["opening"] + qty["purchases"] - qty["sales"]
                       - qty["closing"])

    doc = {
        "FORM3CA": {"F3CA": {
            "CreationInfo": {
                "SWVersionNo": "1.0", "SWCreatedBy": "SW10000000",
                "JSONCreatedBy": "SW10000000",
                "JSONCreationDate": f"{ay[:4]}-09-{12 + index:02d}",
                "IntermediaryCity": "Pune",
            },
            "Form_Details": {
                "FormName": "FORM3CA-3CD",
                "Description": "Audit report under section 44AB",
                "AssessmentYear": ay, "SchemaVer": "Ver1.0", "FormVer": "Ver1.0",
            },
            "PartA": {
                "AssesseeName": {"FirstName": NAME[0], "MiddleName": NAME[1],
                                 "LastName": NAME[2]},
                "AddressDetail": {"AddrDetail1": "Plot 14, MIDC Bhosari",
                                  "CityOrTownOrDistrict": "Pune", "StateCode": "27",
                                  "PinCode": 411026},
                "PAN": PAN, "Status": 8,
                "IndirectTaxFlag": "Y",
                "Form3cdIndirectTax": [
                    {"IndirectTaxType": "GST", "StateCode": "27",
                     "RegNo": "27AABCA1234A1ZQ"}],
                "PartAStartDate": start, "PartAEndDate": end,
                "AssessmentYear": ay,
                "Clause": [{"ClauseNo": "44ABa"}],
            },
            "Form3cdBooksOfAccLst": [
                {"Form3cdBooksOfAccLstPK": {"BooksMaintained": b}}
                for b in ("Cash book", "Ledger", "Journal", "Sales register",
                          "Purchase register")],
            "Form3cdChngMethAccVal": {"MethodOfAcc": "Mercantile"},
            "Form3cdDeprAllw": _blocks(opening_wdv, year["additions"],
                                       year["depreciation"]),
            "Form3cdDebPLExpnditure": [
                {"ParticularType": "Capital expenditure", "Particulars": "Plant",
                 "Amount": rupees(year["additions"] * 0.02)}],
            "Form3cdEmpPfSuperann": [
                {"NatureOfFund": "Provident fund", "Amount": year["pf_received"],
                 "DueDate": f"{ay[:4]}-04-15",
                 "ActualAmount": year["pf_paid"], "ActualDate": f"{ay[:4]}-05-22"}],
            "Form3cdIncomeUnderSec40A_3": {
                "PaymntSec40A_3A": year["cash_disallowance"]},
            "Form3cdPymtSec40a2bDetail": [
                {"NameOfParty": "Acme Holdings LLP", "PAN": "AAEFA9911K",
                 "Relation": "Group entity", "Amount": year["related_party"]}],
            "Form3cdProfGainsTaxSec41": [],
            "Form3cdIncExpPriorPlAcc": [],
            "Form3cdUnpaidStrySec43b": [
                {"LiabilityType": "Tax", "Section": "43B(a)",
                 "Description": "Goods and services tax",
                 "Amount": year["unpaid_43b"]}],
            "Form3cdUnpaidStrySec43b1": ([
                {"LiabilityType": "Tax", "Section": "43B(a)",
                 "Description": "Pre-existing liability",
                 "Amount": opening_43b}] if opening_43b else []),
            "Form3cdChapXVII": [
                {"TAN": "PNEA12345B", "Sec": "194C",
                 "NatureOfPaymnt": "Payments to contractors",
                 "AmtOfNatureSpec": year["tds_payments"],
                 "TotTaxDedCollectd": year["tds_deducted"],
                 "TotLessThanSpecRate": 0,
                 "AmtNotDepToGovt": year["tds_not_deposited"]}],
            "Form3cdSec2011A206C7": ([
                {"TAN": "PNEA12345B",
                 "AmtOfIntrest": rupees(year["tds_not_deposited"] * 0.09),
                 "AmtPaid": 0, "DueDate": f"{ay[:4]}-05-31"}]
                if year["tds_not_deposited"] else []),
            "Form3cdSec269SSDtls": ([
                {"ReceiverName": "Director current account",
                 "ReceiverPanNo": "AAAPS7788L",
                 "AmtAccepted": year["loans_269ss"], "OsAccPy": 0,
                 "NatureAmtCdeBType": "Loan",
                 "LoanDepChqDrftType": "Cash"}] if year["loans_269ss"] else []),
            "Form3cdBflDa": bfl,
            "Form3cdTradeRawProdDet": [
                {"ItemName": "Pump assemblies", "Unit": "Nos",
                 "OpeningStock": qty["opening"],
                 "PurchasePy": qty["purchases"],
                 "SalesDurPy": qty["sales"],
                 "ClosingStock": qty["closing"],
                 "ShortageExcess": qty["shortage"],
                 "ConsumptionManufacPy": qty["purchases"]}],
            "Form3cdBreakUpGST": {
                "TotAmtExp": total_expenditure,
                "GoodOrSerGST": rupees(registered * 0.04),
                "CompSchme": rupees(registered * 0.01),
                "OthRegEntities": rupees(registered * 0.95),
                "RegEntities": registered,
                "ExpNonRegGST": unregistered,
            },
            "Form3cdAccountingRatioCalculations": {
                "PrevYrProfTurnOvr": {
                    "TotalTurnover": year["turnover"],
                    "GrossprofitTurnover": year["gp_rate"],
                    "NetprofitTurnover1": year["np_rate"],
                    "StockInTrade": round(year["closing_stock"] /
                                          year["turnover"] * 100, 2),
                    "MaterialconsumedFinishedgoods": round(
                        year["purchases"] / year["turnover"] * 100, 2),
                    "GrosProfNumertr": year["gross_profit"],
                    "GrosProfDmntr": year["turnover"],
                    "NetProfNumertr": year["net_profit"],
                    "NetProfDmntr": year["turnover"],
                    "StockTradeNmrtr": year["closing_stock"],
                    "StockTradeDmntr": year["turnover"],
                },
                "PrecPrevYrProfTurnOvr": ({
                    "TotalTurnover": preceding_turnover,
                    "GrossprofitTurnover": prior["gp_rate"],
                    "NetprofitTurnover1": prior["np_rate"],
                } if prior else {}),
            },
            "Form3cdFlags": {
                "ChngMethodOfAcc": "N", "ChngShareSec79": "N",
                "ChangeInPartner": "N", "ChangeNaturBuisnes": "N",
                "BookOfAcnt": "Y", "Sec145": "Y", "Sec145A": "N",
                "Sec40A3": cash_flag, "Sec40A3A": "N",
                "ChapXVIIFlag": tds_flag,
                "Sec201_1A_206_C7": "Y" if year["tds_not_deposited"] else "N",
                "ImpermissibleSec96": "N", "TransferPriceSection92CE": "N",
                "IncurredExpenditure": "N",
            },
            "OtherInformation1": {
                "FirstName": "Rohan", "LastName": "Bhandari",
                "Place": "Pune",
                "Date": f"{ay[:4]}-09-{12 + index:02d}",
                "MembershipNo": "104556" if index < 3 else "118902",
                "FirmRegNum": "104233W",
            },
        }}
    }
    if index == 3:
        defect("D-09", ay, "signing accountant",
               "The membership number of the accountant signing the report changes "
               "from this year onwards without the firm registration number changing.",
               "IDN-004")
    return doc


def _blocks(opening: int, additions: int, depreciation: int) -> List[Dict]:
    """Three blocks, split so the totals across them are what the story says."""
    split = [(15, 0.52), (10, 0.31), (40, 0.17)]
    out = []
    for rate, share in split:
        o = rupees(opening * share)
        a = rupees(additions * share)
        d = rupees(depreciation * share)
        out.append({
            "DescBlockAssets": f"Block at {rate}%", "RateOfDep": rate,
            "OpeningWDV": o, "TotalPurchase": a, "TotalPurchaseValue": a,
            "Puttousefor180daysormore": a, "Puttouseforlessthan180days": 0,
            "TotalDeduction": 0, "Deductions": 0,
            "DepAllowable": d, "WrittenDownVal": o + a - d,
        })
    return out


# ---------------------------------------------------------------------- ITR
def build_itr(year: Dict, index: int) -> Dict:
    ay = year["ay"]

    turnover = year["turnover"]
    if ay == "2024-25":
        turnover = year["turnover"] - 12_400_000
        defect("D-10", ay, "turnover: return against audit report",
               "The turnover reported in the return is lower than the turnover "
               "reported in the audit report for the same year by 1,24,00,000.",
               "TIE-001")

    total_income = rupees(year["net_profit"] * 1.06)
    book_profit = rupees(year["net_profit"] * 1.02)

    doc = {
        "ITR": {"ITR6": {
            "CreationInfo": {
                "SWVersionNo": "1.0", "SWCreatedBy": "SW10000000",
                "JSONCreatedBy": "SW10000000",
                "JSONCreationDate": f"{ay[:4]}-{'09' if index != 3 else '08'}-"
                                    f"{25 if index != 3 else 30}",
            },
            "Form_ITR6": {"FormName": "ITR-6", "AssessmentYear": ay[:4],
                          "SchemaVer": "Ver1.0", "FormVer": "Ver1.0"},
            "PartA_GEN1": {
                "PersonalInfo": {
                    "AssesseeName": {"FirstName": NAME[0], "MiddleName": NAME[1],
                                     "SurNameOrOrgName": NAME[2]},
                    "PAN": PAN,
                    "Address": {"ResidenceNo": "Plot 14", "CityOrTownOrDistrict": "Pune",
                                "StateCode": "27", "PinCode": 411026},
                },
                "FilingStatus": {"ReturnFileSec": 11,
                                 "SeventhProvisio139": "N"},
                "AuditInfo": {
                    "LiableToAudit": "Y",
                    "AuditorName": "Bhandari & Rao",
                    "MembershipNo": "104556" if index < 3 else "118902",
                    "AuditReportDate": f"{ay[:4]}-09-{12 + index:02d}",
                    "RegNoAuditFirm": "104233W",
                },
                "NatureOfBusiness": {"NatureOfBusiness": [
                    {"Code": "04012" if index < 4 else "09027",
                     "TradeName1": "Manufacture of pumps"}]},
            },
            "PartA_BS": {
                "SourcesOfFunds": {
                    "ShareholderFund": {
                        "TotalShareCapital": 25_000_000,
                        "TotalReservesAndSurplus": rupees(
                            42_000_000 + index * year["net_profit"] * 0.6),
                    },
                    "LoanFunds": {
                        "TotalLongTermBorrowings": rupees(turnover * 0.11),
                        "TotalShortTermBorrowings": rupees(
                            turnover * (0.07 if index < 3 else 0.19)),
                    },
                },
                "ApplicationOfFunds": {
                    "FixedAsset": {"TotalFixedAssets": year["closing_wdv"]},
                    "CurrentAssets": {
                        "TotalInventories": year["closing_stock"],
                        "TotalTradeReceivable": rupees(
                            turnover * (0.17 if index < 3 else 0.29)),
                        "TotalCashAndCashEquivalents": rupees(turnover * 0.03),
                    },
                    "CurrentLiabilities": {
                        "TotalTradePayable": rupees(turnover * 0.14)},
                },
            },
            "TradingAccount": {
                "SaleOfGoods": turnover,
                "SalesGrossReceiptsTotal": turnover,
                "TotRevenueFrmOperations": turnover,
                "OpngStckOfFinishedStcks": year["opening_stock"],
                "ClsngStckOfFinishedStcks": year["closing_stock"],
                "Purchases": year["purchases"],
                "GrossProfitFrmBusProf": year["gross_profit"],
            },
            "PartA_PL": {
                "CreditsToPL": {
                    "TotRevenueFromOperations": turnover,
                    "TotOtherIncome": rupees(turnover * 0.006),
                    "TotCreditsToPL": rupees(turnover * 1.006),
                },
                "DebitsToPL": {"TotalExpenditure": year["expenditure"]},
                "ProfitBeforeTax": year["net_profit"],
                "NetProfitOrLoss": year["net_profit"],
            },
            "PARTA_OI": {
                "MethodOfAcct": "MERC",
                "ChangeInAcctMethFlg": "N",
                "MethodOfValClgStk": {"ChngStockValMetFlg": "N"},
                "AmtDisallUs40A": {
                    "AmtGT20kCash": year["cash_disallowance"],
                    "AmtPaidUs40A2b": year["related_party"],
                },
                "AmtDisallUs36": {"EmpContributionCredits":
                                  year["pf_received"] - year["pf_paid"]},
                "AmtDisall43B": {"AmtUs43B": {"TotAmtUs43b": year["unpaid_43b"]}},
                "ProfTaxAmtUs41": 0,
                "PriorAmtIncCrDrPL": 0,
            },
            "PARTA_QD": {"TradingConcern": {"QuantitDet": [
                {"ItemName": "Pump assemblies", "UnitOfMeasure": "107",
                 "OpeningStock": rupees(year["opening_stock"] / 2600),
                 "PurchaseQty": rupees(year["purchases"] / 2600),
                 "SaleQty": rupees(year["turnover"] / 2600 * 0.62),
                 "ClgStock": rupees(year["closing_stock"] / 2600),
                 "AnyShortExces": 0}]}},
            "ScheduleGST": {"TurnoverGrsRcptForGSTIN": [
                {"GSTINNo": "27AABCA1234A1ZQ",
                 "AmtTurnGrossRcptGSTIN": rupees(
                     turnover * (1.0 if index < 3 else 1.11))}]},
            "ScheduleDPM": {"TotDepreciationAllowable": year["depreciation"]},
            "ScheduleBP": {"ProfBusGain": total_income},
            "PartB_TI": {
                "ProfBusGain": {"TotProfBusGain": total_income},
                "GrossTotalIncome": total_income,
                "DeductionsUnderScheduleVIA": 0,
                "TotalIncome": total_income,
            },
            "PartB_TTI": {
                "NetTaxLiability": rupees(total_income * 0.2517),
                "BookProfitUs115JB": book_profit,
                "TaxPaid": {"TaxesPaid": {
                    "AdvanceTax": rupees(total_income * 0.19),
                    "TDS": rupees(turnover * 0.004),
                    "SelfAssessmentTax": rupees(total_income * 0.05),
                }},
                "Refund": {"RefundDue": 0},
            },
            "Verification": {"Place": "Pune",
                             "Date": f"{ay[:4]}-09-25", "Capacity": "Director"},
        }}
    }
    if index == 3:
        defect("D-14", ay, "turnover against GST turnover",
               "The turnover reported against the GST registration in Schedule GST is "
               "higher than the turnover credited in the profit and loss account, and "
               "the gap opens in this year.",
               "TIE-006")
    if index == 4:
        defect("D-11", ay, "nature of business",
               "The business code reported in the return changes from the code "
               "reported in earlier years, while the audit report states that there "
               "was no change in the nature of the business.",
               "IDN-005")
    if index == 3:
        defect("D-12", ay, "sequence of the return and the report",
               "The return was created before the audit report it relies on was "
               "signed.",
               "IDN-003")
    return doc


# --------------------------------------------------------------------- write
def main() -> int:
    ap = argparse.ArgumentParser(prog="generator.generate")
    ap.add_argument("--client", default="ACME-PVT-LTD")
    ap.add_argument("--clean", action="store_true",
                    help="delete anything already in the client folder")
    args = ap.parse_args()

    root = ROOT / "filings" / args.client
    if args.clean and root.exists():
        for p in sorted(root.rglob("*"), reverse=True):
            p.unlink() if p.is_file() else p.rmdir()
    root.mkdir(parents=True, exist_ok=True)

    DEFECTS.clear()
    years = story()
    prior = None
    for index, ay in enumerate(YEARS):
        year = years[ay]
        folder = root / f"AY{ay}"
        folder.mkdir(exist_ok=True)

        threecd = build_3cd(year, prior, index)
        (folder / f"3CA-3CD_{ay}.json").write_text(json.dumps(threecd, indent=1))

        # ---- planted: no return at all for AY 2022-23 --------------------
        if ay == "2022-23":
            defect("D-13", ay, "attendance",
                   "No return of income is present for this year, although an audit "
                   "report for the year is.", "ATT-002")
        else:
            itr = build_itr(year, index)
            (folder / f"ITR6_{ay}.json").write_text(json.dumps(itr, indent=1))

        # ---- a revised return, to exercise supersession -------------------
        if ay == "2023-24":
            revised = build_itr(year, index)
            revised["ITR"]["ITR6"]["CreationInfo"]["JSONCreationDate"] = \
                f"{ay[:4]}-12-18"
            revised["ITR"]["ITR6"]["PartA_GEN1"]["FilingStatus"]["ReturnFileSec"] = 17
            revised["ITR"]["ITR6"]["PartB_TI"]["TotalIncome"] = rupees(
                years[ay]["net_profit"] * 1.14)
            (folder / f"ITR6_{ay}_revised.json").write_text(json.dumps(revised, indent=1))

        prior = year

    # ---- a file that is not readable, to exercise the coverage ledger ----
    (root / "notes to the client.txt").write_text(
        "Placed here deliberately: TFA must report a file it cannot read rather "
        "than pass over it in silence.\n")

    manifest = {
        "client": args.client, "pan": PAN,
        "name": " ".join(NAME),
        "years": [f"AY {y}" for y in YEARS],
        "planted": DEFECTS,
    }
    out = ROOT / "generator" / "ground_truth"
    out.mkdir(exist_ok=True)
    (out / f"{args.client}.json").write_text(json.dumps(manifest, indent=2))

    print(f"wrote {sum(1 for _ in root.rglob('*.json'))} filings to {root}")
    print(f"planted {len(DEFECTS)} defects → generator/ground_truth/{args.client}.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
