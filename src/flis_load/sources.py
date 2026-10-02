"""Registry of PUB LOG source files and their record layouts.

Single source of truth for the pipeline: fetch.py reads it to know what to
download, load.py reads it to know what to extract and how to type it.

Column lists are transcribed from the record-layout PDFs published alongside
each zip in the FLIS Data Electronic Reading Room, NOT inferred from the CSV
header rows. The layouts are the contract; headers are a convenience.

Adding a dataset means adding one Source entry here. No other file changes.
"""

from dataclasses import dataclass
from typing import Dict, List

READING_ROOM = (
    "https://www.dla.mil/Information-Operations/FLIS-Data-Electronic-Reading-Room/"
)
BASE_URL = (
    "https://www.dla.mil/Portals/104/Documents/InformationOperations"
    "/LogisticsInformationServices/FOIA/PUBLOG"
)


@dataclass(frozen=True)
class Member:
    """One CSV inside a zip, and the DuckDB table it becomes."""

    csv_name: str
    table: str
    columns: List[str]
    approx_rows: str = ""


@dataclass(frozen=True)
class Source:
    """One downloadable zip from the Reading Room."""

    zip_name: str
    description: str
    members: List[Member]
    enabled: bool = True
    layout_pdf: str = ""

    @property
    def url(self) -> str:
        return f"{BASE_URL}/{self.zip_name}"


SOURCES: Dict[str, Source] = {
    # ------------------------------------------------------------------
    # The supplier <-> part link. This is the reason the project exists.
    # ------------------------------------------------------------------
    "REFERENCE.zip": Source(
        zip_name="REFERENCE.zip",
        description=(
            "Unique CAGE code / part number combinations and their "
            "relationship to the item of supply"
        ),
        layout_pdf="Reference%20FOIA%20Layout.pdf",
        members=[
            Member(
                csv_name="V_FLIS_PART.CSV",
                table="v_flis_part",
                approx_rows="16M+",
                columns=[
                    "NIIN",          # 9   NCB_CD_4130 + I_I_NBR_4131
                    "PART_NUMBER",   # 32V REF_NBR_3570
                    "CAGE_CODE",     # 5   CAGE_CD_9250
                    "CAGE_STATUS",   # 1   CAGE_STAT_CD_2694
                    "RNCC",          # 1   reference number category code
                    "RNVC",          # 1   reference number variation code
                    "DAC",           # 1
                    "RNAAC",         # 2
                    "RNFC",          # 1
                    "RNSC",          # 1
                    "RNJC",          # 1
                    "SADC",          # 2
                    "HCC",           # 2
                    "MSDS",          # 5
                    "MEDALS",        # 1
                ],
            ),
        ],
    ),
    # ------------------------------------------------------------------
    # Supplier identity.
    # ------------------------------------------------------------------
    "CAGE.zip": Source(
        zip_name="CAGE.zip",
        description=(
            "Commercial and Government Entity codes: company name, address, "
            "business classification"
        ),
        layout_pdf="CAGE%20FOIA%20Layout.pdf",
        members=[
            Member(
                csv_name="P_CAGE.CSV",
                table="p_cage",
                approx_rows="3M+",
                columns=[
                    "CAGE_CODE",
                    "CAGE_STATUS",
                    "COMPANY",
                    "CITY",
                    "STATE_PROVINCE",
                    "ZIP_POSTAL_ZONE",
                    "COUNTRY",
                ],
            ),
            Member(
                csv_name="V_CAGE_ADDRESS.CSV",
                table="v_cage_address",
                approx_rows="3M+",
                columns=[
                    "CAGE_CODE",
                    "COMPANY_NAME",
                    "STREET_ADDRESS_1",
                    "STREET_ADDRESS_2",
                    "STREET_ADDRESS_3",
                    "CITY",
                    "STATE",
                    "ZIP",
                    "COUNTRY",
                ],
            ),
        ],
    ),
    # ------------------------------------------------------------------
    # Item identity, plus the decode table for every coded column above.
    # ------------------------------------------------------------------
    "IDENTIFICATION.zip": Source(
        zip_name="IDENTIFICATION.zip",
        description="Data elements common to the item identification function",
        layout_pdf="Identification%20FOIA%20Layout.pdf",
        members=[
            Member(
                csv_name="P_FLIS_NSN.CSV",
                table="p_flis_nsn",
                approx_rows="16M+",
                columns=[
                    "FSC",
                    "NIIN",
                    "INC",
                    "ITEM_NAME",
                    "SOS",
                    "END_ITEM_NAME",
                    "CANCELLED_NIIN",
                ],
            ),
            Member(
                csv_name="V_FLIS_IDENTIFICATION.CSV",
                table="v_flis_identification",
                approx_rows="16M+",
                columns=[
                    "NIIN",
                    "INC",
                    "PINC",
                    "CRIT_CD",
                    "II",
                    "DMIL",
                    "DMIL_INT_CD",
                    "NIIN_ASGMT",
                    "PMIC",
                    "ADP",
                    "HMIC",
                    "ESD_EMI",
                    "HCC",
                    "SCHEDULE_B",
                    "ENAC",
                    "IUID_INDICATOR",
                    "LST_KWN_SOS",
                    "FEDMALL",
                ],
            ),
            Member(
                csv_name="V_FLIS_CANCELLED_NIIN.CSV",
                table="v_flis_cancelled_niin",
                approx_rows="8M+",
                columns=[
                    "NIIN",
                    "CANCELLED_NIIN_FSC",
                    "CANCELLED_NIIN",
                    "NIIN_STAT_CD",
                    "EFF_DATE",
                    "DEMIL",
                ],
            ),
            Member(
                csv_name="V_FLIS_STANDARDIZATION.CSV",
                table="v_flis_standardization",
                approx_rows="16M+",
                columns=[
                    "NIIN",
                    "RELATED_NSN",
                    "ISC",
                    "ORIG_STDZN_DEC",
                    "DT_STDZN_DEC",
                    "NIIN_STAT_CD",
                ],
            ),
            Member(
                csv_name="V_COLLOQUIAL_NAME.CSV",
                table="v_colloquial_name",
                approx_rows="33K+",
                columns=["INC", "COLLOQUIAL_NAME"],
            ),
            Member(
                csv_name="P_HELP.CSV",
                table="p_help",
                approx_rows="18K+",
                # The layout PDF (dated 9/23/2026) lists only CODE and
                # LITERAL. The shipped file has four columns. Verified
                # against the 10/01/2026 drop; trust the file.
                #
                # CODE is either a field name ("AAC") or a field:value pair
                # ("AAC:A"), which is what makes this a usable decode table.
                columns=["CODE", "LITERAL", "DESCRIPTION", "REGS"],
            ),
        ],
    ),
    # ------------------------------------------------------------------
    # Deferred. Flip enabled=True to pull these in; layouts must be
    # transcribed from the matching PDF first.
    # ------------------------------------------------------------------
    "MANAGEMENT.zip": Source(
        zip_name="MANAGEMENT.zip",
        description="Unit price, acquisition advice code, source of supply, per-service data",
        layout_pdf="Management%20FOIA%20Layout.pdf",
        enabled=False,
        members=[
            Member(
                csv_name="V_FLIS_MANAGEMENT.CSV",
                table="v_flis_management",
                approx_rows="15M+",
                columns=[
                    "NIIN",
                    "EFFECTIVE_DATE",
                    "MOE",
                    "AAC",
                    "SOS",
                    "SOSM",
                    "UI",
                    "UI_CONV_FAC",
                    "UOM",
                    "UOM_QTY",
                    "UNIT_PRICE",
                    "QUP",
                    "CIIC",
                    "SLC",
                    "REP_REC_CODE",
                    "MGMT_CTL",
                    "NET_PRICE",
                    "USC",
                    "ROW_OBS_DT",
                ],
            ),
        ],
    ),
    "HISTORY.zip": Source(
        zip_name="HISTORY.zip",
        description="Identification, management and part data for inactive NSNs",
        layout_pdf="History%20FOIA%20Layout.pdf",
        enabled=False,
        members=[],
    ),
    "MOE_RULE.zip": Source(
        zip_name="MOE_RULE.zip",
        description="Which service, agency or allied nation uses the item",
        layout_pdf="MOE%20Rule%20FOIA%20Layout.pdf",
        enabled=False,
        members=[],
    ),
}


def enabled_sources() -> List[Source]:
    """Sources in scope for the current load, in registry order."""
    return [s for s in SOURCES.values() if s.enabled and s.members]
