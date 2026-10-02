-- Analysis surface over the raw PUB LOG tables.
--
-- Applied by load.py after every load. Views only, no materialisation: the
-- base tables are the snapshot, and a monthly reload should not leave stale
-- derived copies behind.
--
-- The central caveat, encoded here so it cannot be forgotten downstream:
-- a NIIN/CAGE row in V_FLIS_PART is NOT a claim that the company makes the
-- part. RNCC (reference number category) and RNVC (reference number
-- variation) qualify the relationship. RNCC='3' with RNVC='2' is the design
-- control reference -- the number controlled by the organisation that
-- controls the item's design and production. Everything else is a vendor
-- item, a secondary reference, or a superseded number. Use
-- is_design_control for supplier attribution; use the full set for
-- cross-reference lookup.

-- CAGE codes that hold design control but do not manufacture anything.
--
-- FLIS has no field for this: CAGE_STATUS records lifecycle (active,
-- obsolete, debarred), never whether an entity is a company. So the
-- classification is keyword matching on COMPANY, kept in this one view so it
-- can be audited and corrected in a single place:
--
--   SELECT category, count(*) FROM non_manufacturer_cage GROUP BY 1;
--   SELECT * FROM non_manufacturer_cage WHERE category = 'placeholder';
--
-- Three distinct things get caught, all of which break supplier attribution:
--
--   placeholder     DLA states the disclaimer in the name itself -- CAGE
--                   10001 is literally "NAVAIR AND NAVSEA MANAGED ORIGINAL
--                   DESIGN ACTIVITY DWG NOT MATERIAL/PARTS SUPPLIER".
--   standards_body  SAE, the National Aerospace Standards Committee. The
--                   part conforms to their specification; they make nothing.
--   government      Design activities that hold the drawing and buy the
--                   part -- NAVSEA, TACOM, AMCOM, NGA.
--
-- This is a lower bound, not an authoritative list. It misses government
-- entities with non-obvious names and '%ARSENAL%' can catch a commercial
-- one. Treat it as a strong default, not ground truth.
CREATE OR REPLACE VIEW non_manufacturer_cage AS
SELECT
    CAGE_CODE,
    COMPANY,
    CASE
        WHEN COMPANY LIKE '%ORIGINAL DESIGN ACTIVITY%'
          OR COMPANY LIKE '%NOT A MFR%'
          OR COMPANY LIKE '%NOT MATERIAL/PARTS SUPPLIER%'
          OR COMPANY LIKE '%NOT FOR RFQ%'
          -- '%CODE ASSIGNED%' catches the national codification conventions:
          -- CAGE 99999 is "DEPARTMENT OF DEFENSE AMMUNITION CODE ASSIGNED AND
          -- PROMULGATED BY..." (13,362 references), meaning the part number is
          -- a DoD-assigned ammunition code, not a manufacturer's. Also picks up
          -- the Canadian DND and UK RNCA equivalents. All 6 matches on the
          -- 10/01/2026 snapshot are genuine; no commercial false positives.
          OR COMPANY LIKE '%CODE ASSIGNED%'                    THEN 'placeholder'
        WHEN COMPANY LIKE '%STANDARD%COMMITTEE%'
          OR COMPANY LIKE 'SAE INTERNATIONAL%'
          OR COMPANY LIKE 'AMERICAN SOCIETY%'
          OR COMPANY LIKE '%ASTM%'
          OR COMPANY LIKE 'INTERNATIONAL ORGANIZATION FOR STANDARD%'
          OR COMPANY LIKE 'AEROSPACE INDUSTRIES ASSOC%'
          OR COMPANY LIKE 'UNDERWRITERS LAB%'                  THEN 'standards_body'
        ELSE 'government'
    END AS category
FROM p_cage
WHERE COMPANY LIKE '%ORIGINAL DESIGN ACTIVITY%'
   OR COMPANY LIKE '%NOT A MFR%'
   OR COMPANY LIKE '%NOT MATERIAL/PARTS SUPPLIER%'
   OR COMPANY LIKE '%NOT FOR RFQ%'
   OR COMPANY LIKE '%CODE ASSIGNED%'
   OR COMPANY LIKE '%STANDARD%COMMITTEE%'
   OR COMPANY LIKE 'SAE INTERNATIONAL%'
   OR COMPANY LIKE 'AMERICAN SOCIETY%'
   OR COMPANY LIKE '%ASTM%'
   OR COMPANY LIKE 'INTERNATIONAL ORGANIZATION FOR STANDARD%'
   OR COMPANY LIKE 'AEROSPACE INDUSTRIES ASSOC%'
   OR COMPANY LIKE 'UNDERWRITERS LAB%'
   OR COMPANY LIKE 'U S ARMY%'
   OR COMPANY LIKE 'US ARMY%'
   OR COMPANY LIKE 'NAVAL %'
   OR COMPANY LIKE 'NAVAIR%'
   OR COMPANY LIKE 'NAVSEA%'
   OR COMPANY LIKE 'US NAVY%'
   OR COMPANY LIKE 'DEPARTMENT OF %'
   OR COMPANY LIKE 'DEFENSE LOGISTICS AGENCY%'
   OR COMPANY LIKE 'NATIONAL GEOSPATIAL%'
   OR COMPANY LIKE '%AIR FORCE%'
   OR COMPANY LIKE 'MARINE CORPS%'
   OR COMPANY LIKE 'US COAST GUARD%'
   OR COMPANY LIKE 'COMBAT CAPABILITIES%'
   OR COMPANY LIKE 'NASA%'
   OR COMPANY LIKE '%SYSTEMS COMMAND%'
   OR COMPANY LIKE '%ARSENAL%'
   OR COMPANY LIKE 'GENERAL SERVICES ADMINISTRATION%';


CREATE OR REPLACE VIEW supplier_part AS
SELECT
    p.NIIN,
    n.FSC,
    n.FSC || p.NIIN                             AS NSN,
    n.ITEM_NAME,
    p.PART_NUMBER,
    p.CAGE_CODE,
    c.COMPANY,
    c.CITY,
    c.STATE_PROVINCE,
    c.COUNTRY,
    p.CAGE_STATUS,
    p.RNCC,
    p.RNVC,
    (p.RNCC = '3' AND p.RNVC = '2')             AS is_design_control,
    (c.COUNTRY IS NOT NULL
     AND c.COUNTRY <> ''
     AND c.COUNTRY <> 'UNITED STATES')          AS is_foreign,
    (nm.CAGE_CODE IS NOT NULL)                  AS is_non_manufacturer,
    nm.category                                 AS non_manufacturer_category
FROM v_flis_part p
LEFT JOIN p_cage                c  ON c.CAGE_CODE  = p.CAGE_CODE
LEFT JOIN p_flis_nsn            n  ON n.NIIN       = p.NIIN
LEFT JOIN non_manufacturer_cage nm ON nm.CAGE_CODE = p.CAGE_CODE;


-- The supplier-risk working set: design-controlling references that point at
-- an actual company. This is what the risk views below are built on.
CREATE OR REPLACE VIEW commercial_supplier_part AS
SELECT *
FROM supplier_part
WHERE is_design_control
  AND NOT is_non_manufacturer;


-- Sole-source exposure: how many distinct COMMERCIAL design-controlling
-- suppliers exist per item. supplier_count = 1 is the single point of
-- failure.
--
-- supplier_count deliberately excludes non-manufacturers, because an item
-- whose only "supplier" is NAVSEA or SAE is not a supply chain risk -- it is
-- a gap in attribution. Those are not dropped, only separated:
-- non_mfr_supplier_count exposes them, and rows where supplier_count = 0 but
-- non_mfr_supplier_count > 0 are items with NO commercial supplier recorded
-- at all. Filter them explicitly rather than reading them as sole-source.
CREATE OR REPLACE VIEW niin_supplier_count AS
SELECT
    NIIN,
    any_value(FSC)                       AS FSC,
    any_value(ITEM_NAME)                 AS ITEM_NAME,
    count(DISTINCT CAGE_CODE)
        FILTER (WHERE NOT is_non_manufacturer)            AS supplier_count,
    count(DISTINCT CAGE_CODE)
        FILTER (WHERE is_non_manufacturer)                AS non_mfr_supplier_count,
    count(DISTINCT CAGE_CODE)
        FILTER (WHERE is_foreign AND NOT is_non_manufacturer)
                                                          AS foreign_supplier_count,
    string_agg(DISTINCT CASE WHEN NOT is_non_manufacturer THEN COMPANY END, ' | ')
                                                          AS suppliers
FROM supplier_part
WHERE is_design_control
GROUP BY NIIN;


-- Concentration: suppliers carrying the most design-controlled items are the
-- chokepoints whose disruption propagates widest. Commercial only -- without
-- that filter this view is topped by NGA, TACOM and NAVSEA, which are
-- cataloguing artefacts rather than chokepoints.
CREATE OR REPLACE VIEW cage_part_count AS
SELECT
    CAGE_CODE,
    any_value(COMPANY)          AS COMPANY,
    any_value(COUNTRY)          AS COUNTRY,
    any_value(is_foreign)       AS is_foreign,
    count(DISTINCT NIIN)        AS niin_count,
    count(*)                    AS part_number_count
FROM commercial_supplier_part
GROUP BY CAGE_CODE;


-- Items whose design-controlling supplier sits outside the United States.
CREATE OR REPLACE VIEW foreign_sourced_parts AS
SELECT *
FROM commercial_supplier_part
WHERE is_foreign;


-- Decode table for the coded columns. P_HELP.CODE is either a bare field
-- name ("AAC") or a field:value pair ("AAC:A"), so splitting on ':' yields a
-- clean (field, value) -> meaning lookup:
--
--   SELECT * FROM code_decode WHERE field = 'RNVC';
CREATE OR REPLACE VIEW code_decode AS
SELECT
    split_part(CODE, ':', 1)  AS field,
    split_part(CODE, ':', 2)  AS value,
    LITERAL                   AS meaning,
    DESCRIPTION,
    REGS                      AS reference_url
FROM p_help
WHERE CODE LIKE '%:%';


-- The sharpest single risk signal available from this data: one supplier,
-- and that supplier is foreign.
CREATE OR REPLACE VIEW sole_source_foreign AS
SELECT s.*
FROM commercial_supplier_part s
JOIN niin_supplier_count n ON n.NIIN = s.NIIN
WHERE s.is_foreign
  AND n.supplier_count = 1;


-- Items where design control rests only with a government activity, a
-- standards body or a placeholder CAGE. These are NOT sole-source risks --
-- they are items whose actual manufacturer FLIS does not record. Separated
-- out so they stop contaminating the sole-source count, and because the list
-- is itself useful: it is the backlog for sourcing attribution elsewhere
-- (DIBBS award history, USAspending).
CREATE OR REPLACE VIEW unattributed_parts AS
SELECT *
FROM niin_supplier_count
WHERE supplier_count = 0
  AND non_mfr_supplier_count > 0;
