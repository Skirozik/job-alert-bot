"""Title -> role family and location -> U.S.?, for the ATS and jobright passes.

Pure functions, no I/O. Heuristic regexes, tuned on the 2026-09-29/30 sweeps
of company ATS boards, where all 1,960 titles they placed in a family were
hand-reviewed (not scored against labels). title_gate.source_gate() is the
only caller: a row is kept when classify_family() names an allowed family (or
the row is an early-career program title) and is_us() does not say "outside
the U.S.".

Families:

  SALES_SOLUTIONS  sales / solutions engineer, pre-sales, Cloudflare's
                   "Customer Engineer", solutions architect, solution(s)
                   consultant
  SUPPORT_ENG      cloud / technical / customer / product support engineer,
                   technical services engineer, technical support analyst
  DATA_CENTER      data-center IT roles (facilities, electrical, mechanical and
                   construction roles are EXCLUDED_DC_FACILITIES)
  NETWORK_INFRA    network engineer/admin/analyst/operations, NOC, IT/cloud
                   infrastructure engineer, cloud engineer
  SECURITY         SOC / security analyst / security engineer / IR / threat /
                   vulnerability management / IAM / GRC (physical security and
                   finance "securities" are excluded)
  ENDPOINT_ITSUP   endpoint / desktop / client-platform / M365 / Intune / Jamf
                   engineering, plus IT support / help desk / service desk as
                   subfamily 'it_support_helpdesk'
  SYSTEMS_IT       systems administrator, IT systems engineer, IT engineer, IT
                   operations engineer, and a plain "Systems Engineer" outside
                   the aerospace/hardware companies and Cloudflare

A title is assigned ONE family: the first rule in classify_family() that
matches (the rules interleave families; read the code for the order). Titles
whose role noun is software/firmware/hardware/data-science/research are kept
out of the IT families (SWE-style roles that merely mention networking or
security).
"""
import re

I = re.I

# ── global exclusions: role nouns that are never one of his families ──────────
# Recruiters/sourcers for an SE org, counsel, account executives, marketing,
# finance "securities", etc. Checked before any family regex. The sales clauses
# never fire on an engineer title: Samsara's pre-sales "Associate Specialist
# Sales Engineer" is a sales engineer, not a sales specialist.
NON_TECH_ROLE = re.compile(
    r"\brecruit|\bsourc(er|ing)\b|\btalent\s+(acq|partner)|\benablement\b|\bcounsel\b|\battorney\b|\bparalegal\b"
    r"|\baccount\s+executive\b|\bspecialist\s+sales\b(?!\s+engineer)|\bsales\s+executive\b"
    r"|\bsales\s+(specialist|representative|development|manager)\b(?!\s+engineer)|\bbdr\b|\bsdr\b"
    r"|\bmarketing\s+manager\b|\baccountant\b|\bsecurities\b|\bbuyer\b|\bsourcing\s+manager\b"
    r"|\bchief\s+of\s+staff\b|\bprogram\s+manager\b|\bproduct\s+manager\b|\bproject\s+manager\b|\bprogram\s+analyst\b|\bprogram\s+coordinator\b"
    r"|\bstrategy\s*&\s*operations\b|\bbusiness\s+partner\b", I)

# SWE / hardware / science role nouns. Used to keep "Software Engineer,
# Networking", "Security Software Engineer", "Network Hardware Engineer",
# "Data Scientist, Infrastructure", "HBM SoC Design Engineer" etc. OUT of the
# IT families. (Not applied to SALES_SOLUTIONS: "Solutions Engineer" is the
# role noun there.)
SWE_HW_SCI = re.compile(
    r"\bsoftware\b|\bsw\s+engineer|\bswe\b|\bdeveloper\b(?!\s+support)|\bfull[\s-]?stack\b|\bback[\s-]?end\b|\bfront[\s-]?end\b"
    r"|\bfirmware\b|\bhardware\b|\brtl\b|\basic\b|\bdft\b|\bphysical\s+design\b|\bsoc\s+design\b|\bembedded\b"
    r"|\bdata\s+(scientist|engineer|analyst)\b|\bscientist\b|\bresearch(er)?\b|\bmachine\s+learning\b|\bml\b|\bapplied\s+ai\b"
    r"|\bai\s+(engineer|systems\s+engineer)\b|\bllm\b|\bkernel\b|\boperating\s+systems\s+engineer\b|(?<!client\s)\bplatform\s+engineer\b|\bsdet\b|\bbi\b"
    r"|\bsite\s+reliability\b|\bsre\b|\bdevops\b|\bdevsecops\b|\bkubernetes\b|\bcompiler\b|\bgpu\b|\bhpc\b(?!\s+systems)"
    r"|\bproduct\s+engineer\b|\bintegration\s+engineer\s+[45]\b|\bdevrel\b", I)

# ── SALES_SOLUTIONS (primary) ────────────────────────────────────────────────
# se:  "Sales Engineer", "Solutions/Solution Engineer", "Presales/Pre-Sales ...",
#      Cloudflare's presales title "Customer Engineer", Pure Storage's presales
#      "Systems Engineer" when qualified (Territory / SE Excellence Center /
#      Pre-Sales), Databricks' "Scale Solution Engineer", Palo Alto Networks'
#      pre-sales specialist "Domain Consultant", HPE's "Proof of Concept
#      Consultant".
#      NOT: Micron "Test Solutions Engineer" (semiconductor test), "IT Solutions
#      Engineer" (corporate IT -> SYSTEMS_IT), "Forward Deployed Solution Engineer"
#      (reported separately as ADJACENT_FDE), Cisco's "Quality Engineer – Solution
#      Engineering".
# sa:  "Solution(s) Architect" incl. Partner/Delivery/Specialist SA; NOT internal
#      enterprise-apps architects (SAP/PLM/Total Rewards/People Tech/Procurement/
#      Supply Chain/Business Process).
# sc:  "Solution(s) Consultant" incl. "Technical Solutions Consultant".
SE_RE = re.compile(
    r"\bsales\s+engineer(ing)?\b|(?<!\bIT\s)(?<!\btest\s)\bsolutions?\s+engineer(ing)?\b|\bpre[\s-]?sales\b"
    r"|\bcustomer\s+engineer(ing)?\b|\bsales\s+architect\b|\bterritory\s+systems?\s+engineer\b"
    r"|\bse\s+excellence\s+center\b|\bexcellence\s+center\s+systems\s+engineer\b"
    r"|(?<!global\s)\bfield\s+engineering\b(?!\s+operations)(?!.*broadcast)"
    r"|\btechnical\s+architect\s*\(pre|\bdomain\s+consultant\b|\bproof[\s-]+of[\s-]+concept\s+(consultant|engineer)\b", I)
SA_RE = re.compile(r"\bsolutions?\s+architect(ure)?\b", I)
SA_INTERNAL = re.compile(
    r"\bsap\b|s/4hana|\bplm\b|total\s+rewards|people\s+tech|procurement|supply\s+chain|business\s+process|\bsku\b|\bworkday\b"
    r"|servicenow\s+solution\s+architect|master\s+data|strategy\s*&\s*operations", I)
SC_RE = re.compile(r"\bsolutions?\s+consultant\b", I)
FDE_RE = re.compile(r"forward[\s-]+deployed", I)  # adjacent, reported separately

# ── DATA_CENTER ──────────────────────────────────────────────────────────────
DC_RE = re.compile(r"\bdata\s*cent(er|re)s?\b|\bdatacent(er|re)s?\b|\bdcim\b", I)
# Facilities / construction / product ("Data Center SSD") roles: excluded.
DC_EXCLUDE = re.compile(
    r"electrical|mechanical|\bmep\b|controls\s+engineer|safety|design\s+engineer|construction|site\s+selection|capacity\s+planner"
    r"|quality|manufacturing|\bssd\b|firmware|validation|buyer|counsel|power|cooling|facilities|architect|compliance|financ"
    r"|program\s+manager|real\s+estate|energy|hvac|developer|community|engagement|supply|supplier|capacity|logistic|business|campaign|field\s+applications|commissioning|selection|reporting|planning"
    r"|acquisition|\brma\b|\bbuilds\b", I)   # site acquisition; a logistics firm's RMA and rack-build associates

# ── NETWORK_INFRA (primary) ──────────────────────────────────────────────────
# net: "network(ing)" followed within two words by an IT role noun; or a role
#      noun followed by ", ... Networking" (e.g. Anduril "Entry Level Systems
#      Engineer, C2 Networking"); plus NOC.
# infra: IT/cloud infrastructure engineer/admin/specialist/technician/ops,
#      "Cloud Engineer", "Cloud Operations Engineer", "Infrastructure Engineer"
#      (the latter is often SWE-infra at software companies; kept, flagged).
NET_RE = re.compile(
    r"\bnetwork(?:ing|s)?\s+(?:[\w/&-]+\s+){0,2}?(engineer|engineering|administrator|admin|analyst|technician|specialist"
    r"|operations|ops|architect|infrastructure|deployment|reliability)\b"
    r"|\b(engineer|administrator|technician|analyst|specialist)\b[^|]{0,40}?[,\-–(]\s*(?:[\w/&-]+\s+){0,2}?network(?:ing|s)?\b"
    r"|\bnoc\b|\bnetwork\s+operations\s+cent(er|re)\b", I)
NET_EXCLUDE = re.compile(
    r"payment\s+network|payments\s+network|card\s+network|network\s+contracting|provider\s+network|talent\s+network"
    r"|network\s+strategy|network\s+value|network\s+brand|network\s+product|network\s+partner|network\s+supply|network\s*&\s*supply"
    r"|power\s+delivery\s+network|partner\s+network|integration\s+network|network\s+analytics|network\s+participant"
    r"|network\s+compliance|network\s+risk|network\s+audit|network\s+recruit|fulfillment\s+network|dram\s+network"
    r"|networked\s+battlefield|vehicle|network\s+delivery\s+program|test\s*&\s*evaluation"
    r"|networking\s+event|out[\s-]+of[\s-]+network", I)   # a hiring event; a health plan's out-of-network claims
INFRA_RE = re.compile(
    r"\b(it|corporate|enterprise|cloud|hybrid|systems?|network)\s+infrastructure\s+(engineer|administrator|admin|specialist|technician|analyst|operations|coordinator)\b"
    r"|\binfrastructure\s+(engineer|administrator|specialist|technician|analyst)\b"
    r"|\bcloud\s+(engineer|engg|operations\s+engineer|infrastructure|administrator|technician|specialist)\b"
    r"|\b(azure|aws|gcp)\s+(engineer|administrator|admin)\b"
    r"|\b(virtualization|storage|backup)\s+engineer\b|\bstorage\s+administrator\b", I)
INFRA_EXCLUDE = re.compile(
    r"civil|structural|mechanical|electrical|construction|starbase|starship|launch\s+infrastructure|utility|land\s+development"
    r"|actuator|dynamometer|capacity\s+planner|demand\s+planning|financ|capital\s+markets|pre-training|safeguards"
    r"|data\s+infrastructure|accounting|supply|ai\s+infrastructure\s+engineer|platform\s+infrastructure|energy|instrumentation|range", I)

# ── SECURITY ─────────────────────────────────────────────────────────────────
# A cyber term AND an analyst/engineer-type role noun. "SOC" only counts in
# security context (it is also "System-on-Chip" at Micron/Astera/SpaceX).
# Managed detection and response counts too: Palo Alto's "MDR Analyst", CrowdStrike's
# "Analyst I, Falcon Complete" (its MDR service; the title names no cyber word),
# "Incident Handler", "Junior SOC DCO" (defensive cyber operations).
SEC_TERM = re.compile(
    r"secur|\bcyber|\binfosec\b|threat|vulnerab|\bvuln\b|incident\s+respon|detection|\bpentest|penetration\s+test|red\s+team"
    r"|blue\s+team|offensive|\bgrc\b|\biam\b|identity\s+(and|&)\s+access|data\s+loss\s+prevention|\bdlp\b|\bsiem\b|\bisso\b"
    r"|\bsoc\s+(analyst|engineer|l[123]|tier)|\(soc\)|security\s+operations\s+cent"
    r"|\bmdr\b|falcon\s+complete|\bintrusion\b|incident\s+handl|\bsoc\s+dco\b|defensive\s+cyber", I)
# "Engr" (Bechtel), "Asc" (Lockheed's associate), "DCO" and apprenticeships are role nouns here too.
SEC_ROLE = re.compile(
    r"engineer|analyst|specialist|investigator|hunter|responder|consultant|associate|information\s+systems?\s+security\s+officer"
    r"|\bisso\b|administrator|architect|tester|\bprogram\b|\bengr\b|\basc\b|\bdco\b|handler|apprentic", I)
SEC_EXCLUDE = re.compile(
    r"physical\s+security|facility\s+security|personnel\s+security|industrial\s+security|(?<!systems\s)(?<!system\s)security\s+officer"
    r"|security\s+operator|\bgsoc\b|console\s+operator|protective|concierge|security\s+uas|security\s+controller"
    r"|counterintelligence|transportation\s+security|anti-tamper|secured\s+spaces|security\s+technology|pedestrian|perception"
    r"|safety\s+threat|cyber\s+harm|critical\s+harm|security\s+risk\s+&\s+compliance,\s+data|logistics\s+security"
    r"|security\s+assessment\s+specialist|embedded\s+security|mission\s+integration|product\s+associate|ai\s+safety|fraud|global\s+safety"
    r"|counter[\s-]*intrusion|regulatory|\beu\s+mdr\b", I)   # Anduril's Counter Intrusion product line; the EU medical-device MDR

# ── SUPPORT_ENG ──────────────────────────────────────────────────────────────
# "Technical Consulting Engineer" is Cisco's (and HPE's) TAC title: product support engineering.
SUPPORT_RE = re.compile(
    r"\bsupport\s+engineer(ing)?\b|\btechnical\s+services\s+engineer\b|\bescalation\s+engineer\b|\btechnical\s+consulting\s+engineer\b"
    r"|\btechnical\s+support\s+(analyst|specialist|spec|associate|technician|expert|representative)\b"
    r"|\bproduct\s+technical\s+support\b|\btechnical\s+product\s+support\b|\btechnical\s+customer\s+support\s+(engineer|specialist)\b|\bproduction\s+support\s+analyst\b"
    r"|\bapplication\s+(maintenance\s+&\s+)?support\s+(analyst|engineer)\b|\bcustomer\s+reliability\s+engineer\b", I)
SUPPORT_EXCLUDE = re.compile(r"\bit\s+support\b|\bweld\b|\bcad\b|mission\s+support|operational\s+support|trade\s+support|\bhbm\b|workstation", I)

# ── SYSTEMS_IT ───────────────────────────────────────────────────────────────
# sysadmin: an IT-flavoured "... administrator" (not HR/payroll/fund/app admins).
# it_sys:   IT-qualified systems engineering ("IT Systems Engineer", "Linux System
#           Engineer", "IT Engineer", "IT Operations Engineer", "IT Solutions Engineer",
#           "Systems Engineering Associate - GovCloud", Jump's "Campus Systems Engineer").
# plain:    bare "Systems Engineer" with no qualifier, outside the aerospace/
#           hardware companies and outside Cloudflare (whose SWE title is
#           "Systems Engineer, <team>"). Flagged 'plain' because it is ambiguous.
SYSADMIN_RE = re.compile(
    r"\b(systems?|sys|it|linux|windows|unix|m365|office\s*365|microsoft|server|online|classified|network|identity"
    r"|active\s+directory|vmware|citrix|video\s*&\s*voice)\s+(systems\s+)?admin(istrator|istration)?\b|\bsysadmin\b|\bit\s+administrator\b"
    r"|\bsystems?\s+administrator\s+(specialist|ii?i?|i)\b", I)
SYSADMIN_EXCLUDE = re.compile(
    r"\bhr\b|people|payroll|workday|atlassian|salesforce\s+admin|teamcenter|hris|finance|fund|subcontract|sales\s+admin"
    r"|executive\s+admin|maintenance|client\s+(services|project)|business\s+process|flow\s+production|applications?\s+admin"
    r"|database|\bav\b|engineering\s+(solutions\s+)?systems\s+admin", I)
IT_SYS_RE = re.compile(
    r"\bit\s+(systems?\s+)?(engineer|engineering)\b|\bit\s+operations\s+(engineer|specialist)\b|\bit\s+solutions\s+engineer\b"
    r"|\bengineer,\s+it\b"
    r"|\b(linux|windows|unix|wintel|microsoft|m365|identity|messaging|collaboration|"
    r"server|virtualization)\s+(systems?\s+)?engineer\b|\bsystems\s+engineering\s+associate\b|\bcampus\s+systems\s+engineer\b"
    r"|\bsystems?\s+engineer\s*\((linux|windows|operations)|\bsystems\s+engineer\s*/\s*storage|\bbroadcast\s+it\b"
    r"|\bit\s+field\s+engineer\b|\bbit\s+field\s+engineer\b|\bsystems\s+engineer,\s+corporate\s+security\b|\bsystems\s+generalist\b|\bhpc\s+systems\s+engineer\b|\bot\s+systems\s+engineer\b"
    r"|\bsystems?\s+engineer\b.*\bgovcloud\b"
    r"|\bsystems\s+specialty\b|\bproduction\s+systems?\s+engineer\b", I)   # San Francisco's 1041 Technology Engineer; ByteDance's server-management PSE
IT_SYS_EXCLUDE = re.compile(r"business\s+systems|finance\s+systems|workday|sales\s+systems|people\s+systems|\bplm\b|\beda\b", I)
PLAIN_SYS_RE = re.compile(r"^\s*(associate\s+|junior\s+|jr\.?\s+|entry[\s-]level\s+|senior\s+|sr\.?\s+|staff\s+|principal\s+|lead\s+)?systems?\s+engineer(ing)?\b", I)
HW_AERO_COMPANIES = {"SpaceX", "Anduril", "Rocket Lab", "Waymo", "Nuro", "Kairos Power", "Medtronic",
                     "Micron Technology", "Astera Labs", "Cloudflare"}
# Matched as a leading name, so jobright's "Anduril Industries" and "Waymo LLC" count as the board keys do.
_HW_AERO_RE = re.compile(r"^\s*(?:" + "|".join(re.escape(c) for c in sorted(HW_AERO_COMPANIES)) + r")\b", I)


def is_hw_aero_company(company: str) -> bool:
    """True for the aerospace/hardware employers whose plain "Systems Engineer" is not IT systems work."""
    return bool(_HW_AERO_RE.match(company or ""))


# ── ENDPOINT_ITSUP ───────────────────────────────────────────────────────────
# "endpoint" may sit up to three words before the role noun: "Junior Endpoint Systems Analyst",
# "Endpoint Infrastructure and AVD Engineer", "End Point Management Solutions".
ENDPOINT_RE = re.compile(
    r"\bend[\s-]?point\s+(?:[\w/&-]+\s+){0,3}?(engineer|engineering|administrator|admin|specialist|analyst|management)\b"
    r"|\bdesktop\s+(engineer|support|administrator|technician|analyst)\b"
    r"|\bclient\s+platform\s+engineer|\bclient\s+engineering\b|\bend[\s-]user\s+(computing|support|services)\b|\beuc\b"
    r"|\bintune\b|\bjamf\b|\bmdm\s+(engineer|administrator)\b|\bmac(os)?\s+(engineer|administrator|admin)\b"
    r"|\bworkplace\s+(technology|engineer|it)\b|\bit\s+system\s+engineer\s*\(workplace\)|\bworkstation\s+support\b"
    r"|\bit\s+administrator\s*\(email|\bwindows\s+administration\b|\bbigfix\b|\bsccm\b|\bmecm\b|\bpatch(ing)?\s+(management|engineer)", I)
ENDPOINT_EXCLUDE = re.compile(r"endpoint\s+security\s+architect|security\s+software|clinical|adjudicat|\btrials?\b", I)
ITSUP_RE = re.compile(
    r"\bit\s+(support|analyst|specialist|technician|field\s+service|associate|service\s+desk|help\s*desk)\b|\bhelp\s*desk\b"
    r"|\bservice\s*desk\b|\bit\s+support\b|\bit\s+(\w+\s+)?(specialist|technician|technologist)\b|\btechnician,\s+it\b|\bfield\s+support\s+technician\b|\bdesktop\s+support\b|\buser\s+support\s+analyst\b", I)
ITSUP_EXCLUDE = re.compile(r"supervisor|coordinator|investment|accounts\s+associate|customer|member|patient|payroll|benefits", I)


def classify_family(title: str, company: str = "") -> tuple[str | None, str | None]:
    """Return (family, subfamily) or (None, None). One family per title."""
    t = " ".join((title or "").split())
    if not t:
        return None, None
    if NON_TECH_ROLE.search(t):
        return None, None
    if FDE_RE.search(t):
        return "ADJACENT_FDE", "forward_deployed"

    # SALES_SOLUTIONS first: "Security Solutions Engineer", "Pre-Sales Systems Engineer"
    if SE_RE.search(t) and not re.search(r"\btest\s+solutions\b|\brecruit|\bbusiness\s+solutions|^quality\s+engineer\b",
                                         t, I):
        if not re.search(r"\bsupport\s+engineer\b", t, I):
            return "SALES_SOLUTIONS", "sales_solutions_engineer"
    if company == "Pure Storage" and re.search(r"\bsystems?\s+engineer(ing)?\b", t, I) and not re.search(
            r"quality|data\s+intelligence|platform|software|hardware", t, I):
        return "SALES_SOLUTIONS", "sales_solutions_engineer"
    if SA_RE.search(t) and not SA_INTERNAL.search(t):
        return "SALES_SOLUTIONS", "solutions_architect"
    if SC_RE.search(t) and not re.search(r"business\s+solutions\s+consultant", t, I):
        return "SALES_SOLUTIONS", "solution_consultant"

    # Cloudflare titles its software engineers "Systems Engineer, <team>"
    if company == "Cloudflare" and re.match(r"\s*((senior|sr\.?|staff|principal|lead)\s+)?systems?\s+engineer", t, I):
        return None, None
    # a support-engineer role noun is support work even when the product is ML/software
    if SUPPORT_RE.search(t) and not SUPPORT_EXCLUDE.search(t) and not DC_RE.search(t):
        return "SUPPORT_ENG", "support_engineer"
    sw = bool(SWE_HW_SCI.search(t))

    if DC_RE.search(t):
        if DC_EXCLUDE.search(t) or sw:
            return "EXCLUDED_DC_FACILITIES", "dc_facilities_or_product"
        return "DATA_CENTER", "data_center_it"

    if not sw:
        # support-engineer role noun wins over the product topic ("Technical
        # Support Engineer, Application Security" is support, not security)
        if SUPPORT_RE.search(t) and not SUPPORT_EXCLUDE.search(t):
            return "SUPPORT_ENG", "support_engineer"
        if NET_RE.search(t) and not NET_EXCLUDE.search(t):
            return "NETWORK_INFRA", "network"
        # "National Security" is a business-unit name at Salesforce/Scale, not a security role
        t_sec = re.sub(r"(salesforce\s+)?national\s+security", " ", t, flags=I)
        if SEC_TERM.search(t_sec) and SEC_ROLE.search(t_sec) and not SEC_EXCLUDE.search(t_sec):
            return "SECURITY", ("security_analyst_ops" if re.search(
                r"analyst|\bsoc\b|hunter|responder|incident|investigator|cyber\s+defense|operations", t, I)
                else "security_engineer")
        if ENDPOINT_RE.search(t) and not ENDPOINT_EXCLUDE.search(t):
            return "ENDPOINT_ITSUP", "endpoint"
        if SYSADMIN_RE.search(t) and not SYSADMIN_EXCLUDE.search(t):
            return "SYSTEMS_IT", "sysadmin"
        # infrastructure-titled roles go to NETWORK_INFRA before the IT-systems check
        if INFRA_RE.search(t) and not INFRA_EXCLUDE.search(t):
            return "NETWORK_INFRA", "infrastructure_cloud"
        if IT_SYS_RE.search(t) and not IT_SYS_EXCLUDE.search(t):
            return "SYSTEMS_IT", "it_systems_engineer"
        if ITSUP_RE.search(t) and not ITSUP_EXCLUDE.search(t):
            return "ENDPOINT_ITSUP", "it_support_helpdesk"
        if PLAIN_SYS_RE.search(t) and not is_hw_aero_company(company):
            return "SYSTEMS_IT", "plain_systems_engineer"
    return None, None


# ── U.S. location ────────────────────────────────────────────────────────────
STATES = {"AL": "alabama", "AK": "alaska", "AZ": "arizona", "AR": "arkansas", "CA": "california", "CO": "colorado",
          "CT": "connecticut", "DE": "delaware", "FL": "florida", "GA": "georgia", "HI": "hawaii", "ID": "idaho",
          "IL": "illinois", "IN": "indiana", "IA": "iowa", "KS": "kansas", "KY": "kentucky", "LA": "louisiana",
          "ME": "maine", "MD": "maryland", "MA": "massachusetts", "MI": "michigan", "MN": "minnesota",
          "MS": "mississippi", "MO": "missouri", "MT": "montana", "NE": "nebraska", "NV": "nevada",
          "NH": "new hampshire", "NJ": "new jersey", "NM": "new mexico", "NY": "new york", "NC": "north carolina",
          "ND": "north dakota", "OH": "ohio", "OK": "oklahoma", "OR": "oregon", "PA": "pennsylvania",
          "RI": "rhode island", "SC": "south carolina", "SD": "south dakota", "TN": "tennessee", "TX": "texas",
          "UT": "utah", "VT": "vermont", "VA": "virginia", "WA": "washington", "WV": "west virginia",
          "WI": "wisconsin", "WY": "wyoming", "DC": "district of columbia"}
NON_US = ["canada", "toronto", "vancouver", "montreal", "ontario", "british columbia", "quebec", "ottawa", "calgary",
          "london", "united kingdom", "uk", "gbr", "england", "scotland", "edinburgh", "manchester", "belfast", "ireland",
          "dublin", "cork", "india", "ind", "bangalore", "bengaluru", "hyderabad", "pune", "chennai", "gurgaon",
          "gurugram", "noida", "mumbai", "delhi", "singapore", "sgp", "malaysia", "kuala lumpur", "japan", "jpn", "tokyo",
          "osaka", "china", "shanghai", "beijing", "shenzhen", "hong kong", "taiwan", "taipei", "taichung", "tainan",
          "hsinchu", "korea", "seoul", "vietnam", "thailand", "bangkok", "philippines", "manila", "indonesia", "jakarta",
          "australia", "aus", "sydney", "melbourne", "new zealand", "germany", "deu", "berlin", "munich", "france",
          "paris", "netherlands", "amsterdam", "belgium", "brussels", "spain", "madrid", "barcelona", "portugal",
          "lisbon", "brazil", "sao paulo", "são paulo", "mexico", "cdmx", "argentina", "buenos aires", "colombia",
          "bogota", "bogotá", "chile", "santiago", "costa rica", "poland", "warsaw", "krakow", "romania", "rou",
          "bucharest", "czech", "prague", "hungary", "budapest", "austria", "vienna", "switzerland", "che", "zurich",
          "geneva", "sweden", "stockholm", "denmark", "copenhagen", "norway", "oslo", "finland", "helsinki", "italy",
          "milan", "rome", "israel", "tel aviv", "uae", "dubai", "abu dhabi", "qatar", "doha", "saudi", "riyadh",
          "egypt", "cairo", "nigeria", "lagos", "kenya", "nairobi", "south africa", "cape town", "johannesburg",
          "serbia", "belgrade", "greece", "athens", "turkey", "istanbul", "ukraine", "emea", "apac", "apj", "latam",
          "luxembourg", "canberra", "hiroshima", "penang", "ireland", "lithuania", "estonia", "latvia", "croatia",
          "bulgaria", "sofia", "slovakia", "cyprus", "malta", "pakistan", "sri lanka", "bangladesh", "nepal",
          "peru", "lima", "uruguay", "montevideo", "guatemala", "panama", "puerto rico", "dominican",
          "ho chi minh", "hanoi", "cebu", "auckland", "wellington", "perth", "brisbane", "tunglo", "kyoto",
          "nagoya", "hangzhou", "guangzhou", "chengdu", "wuhan", "xi'an", "nanjing", "suzhou", "dalian", "xiamen",
          # scraper_brice port: places that reached the 2026-09-30 candidates with an unknown verdict
          "tunisia", "kuwait", "bahrain", "morocco", "casablanca", "ecuador", "quito", "united arab emirates",
          "frankfurt", "monterrey", "guadalajara", "jalisco", "nuevo leon", "nuevo león", "tijuana"]
# Three-letter ISO codes ("(Remote, GBR)", "(Hybrid, IND)") are matched only in
# that parenthesised/comma form, so "ind"/"che"/"can" never hit ordinary words.
# Puerto Rico is left unknown rather than non-U.S.
NON_US = [x for x in NON_US if x not in {"gbr", "ind", "sgp", "jpn", "aus", "deu", "rou", "che", "puerto rico"}]
_ISO_NON_US_RE = re.compile(
    r"[,(]\s*(gbr|ind|aus|che|deu|rou|sau|can|jpn|sgp|isr|fra|esp|ita|nld|pol|bra|mex|kor|are|irl|nzl|swe|dnk|nor"
    r"|fin|bel|aut|prt|cze|twn|chn|mys|phl|col|arg|chl|zaf)\s*\)", re.I)
_NON_US_RE = re.compile(r"(?<![a-z])(" + "|".join(re.escape(x) for x in sorted(set(NON_US), key=len, reverse=True)) + r")(?![a-z])")
US_CITIES = ["nyc", "new york", "san francisco", "sf", "bay area", "seattle", "boston", "chicago", "austin", "los angeles",
             "atlanta", "denver", "pittsburgh", "palo alto", "mountain view", "sunnyvale", "menlo park", "redmond",
             "bellevue", "san jose", "santa clara", "cupertino", "san diego", "washington dc", "washington, d.c",
             "philadelphia", "miami", "dallas", "houston", "raleigh", "durham", "portland", "salt lake", "phoenix",
             "minneapolis", "detroit", "columbus", "nashville", "charlotte", "baltimore", "st. louis", "kansas city",
             "ann arbor", "irvine", "boise", "hawthorne", "mcgregor", "starbase", "brownsville", "costa mesa",
             "redmond", "mclean", "richmond", "plano", "reston", "arlington", "herndon", "chantilly", "long beach",
             "foster city", "cary", "cape canaveral", "vandenberg", "bastrop", "memphis", "reno", "brooklyn",
             "jersey city", "manhattan", "dmv", "remote in usa", "remote - usa", "remote - us", "remote, us",
             "us remote", "remote (us)", "united states", "usa", "u.s.", "u.s", "amer", "north america", "americas",
             "fed", "federal"]
_US_CITY_RE = re.compile(r"(?<![a-z])(" + "|".join(re.escape(x) for x in sorted(set(US_CITIES), key=len, reverse=True)) + r")(?![a-z])")
_STATE_ABBR_RE = re.compile(r"(?:^|[\s,(\-/])(" + "|".join(STATES) + r")(?:$|[\s,)\-./])")
_STATE_NAME_RE = re.compile(r"(?<![a-z])(" + "|".join(re.escape(v) for v in STATES.values()) + r")(?![a-z])")


# FIS site codes lead with an ISO-3 country ("IND MHLI A-45 2ND FL", "PHL MANI 2305",
# "US FL JAX 347"); Visa leads with ISO-2 ("GB - Remote - GBR", "IN - Bengaluru").
# Without this the trailing "FL" (floor) in an Indian FIS code reads as Florida.
_LEADING_ISO_NON_US = re.compile(
    r"^\s*(ind|phl|bra|gbr|pol|chn|mys|sgp|can|mex|deu|fra|irl|nld|esp|ita|aus|jpn|zaf|are|che|swe|dnk|nor|fin|bel"
    r"|aut|prt|cze|hun|rou|svk|kor|twn|hkg|idn|tha|vnm|isr|egy|arg|chl|col|per|cri|nzl|tun|lka)\s+[a-z]{3,4}\b"
    r"|^\s*(gb|in|sg|jp|au|de|fr|ie|nl|es|it|br|mx|ca|pl|cn|hk|kr|tw|ae|sa|za|ch|se|dk|no|fi|be|at|pt|cz|ro|ci|ph"
    r"|my|id|th|vn|il|eg|ar|cl|nz)\s+-\s", re.I)
_EXTRA_NON_US = re.compile(r"zürich|cardiff|charlottetown|prince edward island|nerima|ivoire|gurugram|noida", re.I)
_EXTRA_US = re.compile(r"aliso viejo", re.I)


# ── Added in the scraper_brice port: U.S. signals that outrank a foreign match ──
# The field study's _part_is_us returned False for ANY part naming a foreign place,
# even a plain U.S. address. On the 2026-09-29/30 snapshots (ATS boards + jobright
# READMEs) that marked 93 distinct U.S. locations non-U.S. -- "Dublin, OH",
# "Vienna, Virginia", "Melbourne, FL, US", "2040 Amsterdam Ave., New York, NY",
# "Hybrid - San Francisco, New York City, London, Berlin" -- and source_gate drops
# such a row before Claude or the dashboard ever sees it. The signals below now
# win over a foreign CITY or COUNTRY name, never over an ISO/site code. Measured on
# the same snapshots: no verdict moved from U.S. to non-U.S.; the one foreign
# location it now keeps ("Hyderabad - Phoenix Equinox Tower 2") reaches Claude,
# where classifier.py's deterministic non-US override marks it INELIGIBLE.
_US_TAIL_RE = re.compile(r"(?<![a-z.])us\s*\)?\s*$")   # "..., US" / "(US)" in the country slot
# U.S. towns named like a place in NON_US, and the states they are in ("Dublin, OH").
_FALSE_FRIENDS = {k: set(v.split()) for k, v in {
    "dublin": "oh ca tx va pa ga nh in", "vienna": "va wv md ga il", "athens": "ga oh tn al tx pa ny wv mi",
    "melbourne": "fl", "brisbane": "ca", "manchester": "nh ct vt mo tn md pa nj ia ky wa mi",
    "paris": "tx tn ky il ar me mo", "lima": "oh ny pa", "geneva": "il ny oh al ne",
    "warsaw": "in mo ky ny va nc", "malta": "ny mt oh id", "rome": "ga ny", "peru": "il in ne",
    "milan": "tn mi oh il in nm", "wellington": "fl ks oh co tx", "panama": "fl", "london": "ky oh ct",
    "berlin": "nh ct md nj pa wi vt ma ny", "amsterdam": "ny", "cairo": "il ga ny wv", "belgrade": "mt me",
    "lisbon": "me oh nh nd ia", "zurich": "il", "vancouver": "wa", "perth": "nj", "bogota": "nj",
    "delhi": "ny la", "denmark": "sc wi me", "morocco": "in",
}.items()}
_FF_RE = re.compile(r"(?<![a-z])(" + "|".join(sorted(_FALSE_FRIENDS, key=len, reverse=True))
                    + r")(?:\s+[a-z]+)?\s*,\s*([a-z]{2})(?![a-z])")
# US_CITIES minus its non-city tokens ("Federal Government (Ottawa)" is Canadian).
_US_PLACE_RE = re.compile(r"(?<![a-z])(" + "|".join(
    re.escape(x) for x in sorted(set(US_CITIES) - {"fed", "federal", "amer", "americas", "north america"},
                                 key=len, reverse=True)) + r")(?![a-z])")
# Foreign countries, provinces and regions (not cities), and Canada's "<City>, ON, CA" form.
_FOREIGN_COUNTRY_RE = re.compile(
    r"(?<![a-z])(?:" + "|".join(re.escape(c) for c in sorted([
        "canada", "ontario", "british columbia", "quebec", "alberta", "united kingdom", "uk", "england", "scotland",
        "wales", "ireland", "india", "singapore", "malaysia", "japan", "china", "hong kong", "taiwan", "korea",
        "vietnam", "thailand", "philippines", "indonesia", "australia", "new zealand", "germany", "france",
        "netherlands", "belgium", "spain", "portugal", "brazil", "mexico", "argentina", "colombia", "chile",
        "costa rica", "poland", "romania", "czech", "hungary", "austria", "switzerland", "sweden", "denmark",
        "norway", "finland", "italy", "israel", "uae", "qatar", "saudi", "egypt", "nigeria", "kenya",
        "south africa", "serbia", "greece", "turkey", "ukraine", "emea", "apac", "apj", "latam", "luxembourg",
        "lithuania", "estonia", "latvia", "croatia", "bulgaria", "slovakia", "cyprus", "malta", "pakistan",
        "sri lanka", "bangladesh", "nepal", "peru", "uruguay", "guatemala", "panama", "dominican",
        "tunisia", "kuwait", "bahrain", "morocco", "ecuador", "united arab emirates", "jalisco", "nuevo leon",
        "nuevo león"],
        key=len, reverse=True)) + r")(?![a-z])"
    r"|,\s*(?:on|bc|ab|qc|mb|sk|ns|nb|nl|pe)\s*,\s*ca(?![a-z])")


def _us_outranks_foreign(pl: str) -> bool:
    """True when a lower-cased part that names a foreign city/country is still a U.S. location."""
    if _US_TAIL_RE.search(pl):
        return True
    foreign = [m.group(1) for m in _NON_US_RE.finditer(pl)]
    if foreign and all(f in _FALSE_FRIENDS for f in foreign):
        for m in _FF_RE.finditer(pl):
            if m.group(2) in _FALSE_FRIENDS[m.group(1)]:
                return True
    countries = [c.start() for c in _FOREIGN_COUNTRY_RE.finditer(pl)]
    # A full state name, unless a foreign country is named after it ("Washington, England, United Kingdom";
    # "Baja California" is Mexican).
    states = [m.end() for m in _STATE_NAME_RE.finditer(pl) if not pl[:m.start()].endswith("baja ")]
    if states and not any(c >= max(states) for c in countries):
        return True
    # A U.S. city or a bare "US" token ("Remote in the US, Chicago, Toronto"), only when no foreign
    # country is named at all.
    return not countries and bool(_US_PLACE_RE.search(pl) or re.search(r"(?<![a-z.])us(?![a-z])", pl))


# Leading work-model words in a segment ("Hybrid - San Francisco").
_SEGMENT_PREFIX_RE = re.compile(r"^(?:hybrid|remote|on[\s-]?site|in[\s-]?office)\s*[-–:]\s*")


def names_us_location(location: str) -> bool:
    """For classifier.py's non-US override: does some part of the location name a U.S. state in full ("Vienna,
    Virginia", "Albuquerque, New Mexico"), not followed by a foreign country, or -- when no foreign country is named
    -- a U.S. city standing as a whole comma-separated segment ("Chicago, New York, London", "Hybrid - San
    Francisco, London, Berlin")? The override already honours state CODES; this adds the spelled-out forms the ATS
    boards and jobright use. Stricter than is_us() about cities, so a building named after one ("Hyderabad -
    Phoenix Equinox Tower 2") is still foreign."""
    for part in re.split(r"\s*(?:\||;|/| or |\n|•)\s*", location or ""):
        pl = part.lower().strip()
        if not pl:
            continue
        countries = [c.start() for c in _FOREIGN_COUNTRY_RE.finditer(pl)]
        states = [m.end() for m in _STATE_NAME_RE.finditer(pl) if not pl[:m.start()].endswith("baja ")]
        if states and not any(c >= max(states) for c in countries):
            return True
        if not countries and any(_US_PLACE_RE.fullmatch(_SEGMENT_PREFIX_RE.sub("", seg.strip()))
                                 for seg in pl.split(",")):
            return True
    return False


def _part_is_us(p: str) -> bool | None:
    pl = p.lower()
    # An explicit U.S. country token wins inside a part ("Remote - USA, CAN, MEX").
    if re.search(r"(?<![a-z])(usa|u\.s\.a?\.?|united states)(?![a-z])", pl) or re.match(r"^\s*us\b", pl):
        return True
    strong_foreign = _ISO_NON_US_RE.search(pl) or _LEADING_ISO_NON_US.search(pl) or _EXTRA_NON_US.search(pl)
    if _NON_US_RE.search(pl) or strong_foreign:
        # "New Mexico" is a state
        if re.search(r"new\s+mexico", pl) and not re.search(r"(?<!new )mexico", pl):
            return True
        if not strong_foreign and _us_outranks_foreign(pl):   # scraper_brice port, see above
            return True
        return False
    if re.search(r"(?<![a-z])us(?![a-z])", pl):
        return True
    if _STATE_ABBR_RE.search(p) or _STATE_NAME_RE.search(pl) or _US_CITY_RE.search(pl) or _EXTRA_US.search(pl):
        return True
    return None


def is_us(location: str, url: str = "", title: str = "") -> bool | None:
    """True = at least one U.S. location; False = only non-U.S.; None = unknown.

    Workday multi-location boards report "2 Locations"; for those the primary
    location slug in the job URL (/job/<Location-Slug>/...) is used instead.
    A bare "Remote" is unknown unless the title names a U.S./non-U.S. place.
    """
    loc = (location or "").strip()
    cands = []
    if loc and not re.fullmatch(r"\d+\s+locations?", loc, I):
        cands.append(loc)
    m = re.search(r"/job/([^/]+)/", url or "")
    if m and (not cands):
        cands.append(m.group(1).replace("-", " "))
    if title:
        cands.append(title)  # "Senior Customer Engineer - Nashville", "(Remote, GBR)"
    verdicts = []
    for c in cands:
        parts = re.split(r"\s*(?:\||;|/| or |\n|•)\s*", c)
        v = [_part_is_us(p) for p in parts]
        if any(x is True for x in v):
            verdicts.append(True)
        elif any(x is False for x in v):
            verdicts.append(False)
        else:
            verdicts.append(None)
    # location verdict wins over title verdict when both exist
    for v in verdicts:
        if v is not None:
            return v
    return None
