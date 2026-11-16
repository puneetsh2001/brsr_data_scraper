
from pathlib import Path
from collections import defaultdict
from lxml import etree
import pandas as pd


# ============================================================
# CONFIGURATION
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parents[2]

TAXONOMY_ROOT = (
    PROJECT_ROOT
    / "data"
    / "raw"
    / "sebi_brsr_taxonomy"
    / "extracted"
)

CORE_DIR = TAXONOMY_ROOT / "core"
BRSR_DIR = TAXONOMY_ROOT / "BRSR"

OUTPUT_DIR = PROJECT_ROOT / "data" / "processed"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

CORE_XSD = CORE_DIR / "in-capmkt.xsd"
LABEL_XML = CORE_DIR / "in-capmkt-lab.xml"
PRESENTATION_XML = BRSR_DIR / "in-capmkt-pre-2026-02-28.xml"
DEFINITION_XML = BRSR_DIR / "in-capmkt-def-2026-02-28.xml"
ROLES_XSD = BRSR_DIR / "in-capmkt-roles-2026-02-28.xsd"

CONCEPTS_CSV = OUTPUT_DIR / "nse_brsr_taxonomy_concepts.csv"
PRESENTATION_CSV = OUTPUT_DIR / "nse_brsr_presentation_relationships.csv"
DEFINITION_CSV = OUTPUT_DIR / "nse_brsr_definition_relationships.csv"
ROLES_CSV = OUTPUT_DIR / "nse_brsr_taxonomy_roles.csv"

XLINK = "http://www.w3.org/1999/xlink"
XBRL_LABEL_ROLE = "http://www.xbrl.org/2003/role/label"
XBRL_DOCUMENTATION_ROLE = "http://www.xbrl.org/2003/role/documentation"


# ============================================================
# HELPER FUNCTIONS
# ============================================================

def local_name(element):
    return etree.QName(element).localname


def get_concept_name(href):
    """
    Extract concept name from taxonomy locator href.

    Examples:
    in-capmkt.xsd#in-capmkt_NSESymbol -> NSESymbol
    ../core/in-capmkt.xsd#in-capmkt_ScripCode -> ScripCode
    """

    if not href or "#" not in href:
        return None

    fragment = href.split("#", 1)[1]

    if "_" in fragment:
        fragment = fragment.split("_", 1)[1]

    return fragment


def get_link_role(element):
    """
    Read extended link role.
    """

    return (
        element.get("role")
        or element.get(f"{{{XLINK}}}role")
        or ""
    )


def get_xlink_attribute(element, attribute):
    return element.get(f"{{{XLINK}}}{attribute}")


def get_resource_text(element):
    return "".join(element.itertext()).strip()


def append_unique(mapping, key, value):
    if key and value and value not in mapping[key]:
        mapping[key].append(value)


def join_values(values):
    return " | ".join(values) if values else ""


# ============================================================
# 1. LOAD CORE TAXONOMY CONCEPTS
# ============================================================

def load_concepts():

    print("\nLoading core taxonomy concepts...")

    tree = etree.parse(str(CORE_XSD))
    root = tree.getroot()

    concepts = {}

    for element in root.iter():

        if local_name(element) != "element":
            continue

        name = element.get("name")

        if not name:
            continue

        concepts[name] = {
            "concept_name": name,
            "data_type": element.get("type", ""),
            "abstract": element.get("abstract", "false").lower() == "true",
            "nillable": element.get("nillable", "false").lower() == "true",
            "substitution_group": element.get("substitutionGroup", ""),
            "period_type": element.get(
                "{http://www.xbrl.org/2003/instance}periodType", ""
            ),
            "standard_label": "",
            "documentation": "",
            "other_labels": "",
            "presentation_roles": "",
            "presentation_role_descriptions": "",
            "parent_concepts": "",
        }

    print("Concepts loaded:", len(concepts))

    return concepts


# ============================================================
# 2. EXTRACT LABELS
# ============================================================

def extract_labels(concepts):

    print("\nExtracting taxonomy labels...")

    tree = etree.parse(str(LABEL_XML))
    root = tree.getroot()

    standard_labels = defaultdict(list)
    documentation_labels = defaultdict(list)
    other_labels = defaultdict(list)

    for link in root.iter():

        if local_name(link) != "labelLink":
            continue

        locators = {}
        resources = {}

        for child in link:

            child_name = local_name(child)

            if child_name == "loc":

                locator_label = get_xlink_attribute(child, "label")
                href = get_xlink_attribute(child, "href")

                concept_name = get_concept_name(href)

                if locator_label and concept_name:
                    locators[locator_label] = concept_name

            elif child_name == "label":

                resource_label = get_xlink_attribute(child, "label")

                if not resource_label:
                    continue

                # IMPORTANT:
                # If role is absent, XBRL default is standard label.
                label_role = (
                    child.get("role")
                    or XBRL_LABEL_ROLE
                )

                label_text = get_resource_text(child)

                resources[resource_label] = {
                    "role": label_role,
                    "text": label_text,
                    "language": child.get(
                        "{http://www.w3.org/XML/1998/namespace}lang",
                        ""
                    ),
                }

        for child in link:

            if local_name(child) != "labelArc":
                continue

            from_label = get_xlink_attribute(child, "from")
            to_label = get_xlink_attribute(child, "to")

            concept_name = locators.get(from_label)
            resource = resources.get(to_label)

            if not concept_name or not resource:
                continue

            if concept_name not in concepts:
                continue

            label_text = resource["text"]
            label_role = resource["role"]
            language = resource["language"]

            if not label_text:
                continue

            # Keep English labels for the main mapping.
            if language and language.lower() != "en":
                continue

            if label_role == XBRL_LABEL_ROLE:

                append_unique(
                    standard_labels,
                    concept_name,
                    label_text
                )

            elif label_role == XBRL_DOCUMENTATION_ROLE:

                append_unique(
                    documentation_labels,
                    concept_name,
                    label_text
                )

            else:

                append_unique(
                    other_labels,
                    concept_name,
                    label_text
                )

    # Transfer extracted labels into concept records.
    for concept_name, record in concepts.items():

        labels = standard_labels.get(concept_name, [])

        if labels:
            record["standard_label"] = labels[0]

        record["documentation"] = join_values(
            documentation_labels.get(concept_name, [])
        )

        record["other_labels"] = join_values(
            other_labels.get(concept_name, [])
        )

    print("Concepts with standard labels:", len(standard_labels))
    print("Concepts with documentation:", len(documentation_labels))
    print("Concepts with alternative labels:", len(other_labels))


# ============================================================
# 3. EXTRACT PRESENTATION RELATIONSHIPS
# ============================================================

def extract_presentation_relationships(concepts):

    print("\nExtracting presentation relationships...")

    tree = etree.parse(str(PRESENTATION_XML))
    root = tree.getroot()

    relationships = []
    concept_roles = defaultdict(list)
    concept_parents = defaultdict(list)

    for link in root.iter():

        if local_name(link) != "presentationLink":
            continue

        role_uri = get_link_role(link)

        locators = {}

        for child in link:

            if local_name(child) != "loc":
                continue

            locator_label = get_xlink_attribute(child, "label")
            href = get_xlink_attribute(child, "href")

            concept_name = get_concept_name(href)

            if locator_label and concept_name:
                locators[locator_label] = concept_name

        # Associate role with every concept locator in this link.
        # This includes root concepts as well as child concepts.
        for concept_name in locators.values():

            if concept_name in concepts:
                append_unique(
                    concept_roles,
                    concept_name,
                    role_uri
                )

        for child in link:

            if local_name(child) != "presentationArc":
                continue

            parent_label = get_xlink_attribute(child, "from")
            child_label = get_xlink_attribute(child, "to")

            parent_concept = locators.get(parent_label)
            child_concept = locators.get(child_label)

            if not parent_concept or not child_concept:
                continue

            order = child.get("order", "")

            relationships.append({
                "role_uri": role_uri,
                "parent_concept": parent_concept,
                "child_concept": child_concept,
                "order": order,
            })

            if child_concept in concepts:
                append_unique(
                    concept_parents,
                    child_concept,
                    parent_concept
                )

    # Transfer relationship information to concept records.
    for concept_name, record in concepts.items():

        record["presentation_roles"] = join_values(
            concept_roles.get(concept_name, [])
        )

        record["parent_concepts"] = join_values(
            concept_parents.get(concept_name, [])
        )

    df = pd.DataFrame(relationships)
    df.to_csv(PRESENTATION_CSV, index=False, encoding="utf-8-sig")

    print("Presentation relationships:", len(relationships))
    print(
        "Concepts with presentation roles:",
        len(concept_roles)
    )
    print(
        "Concepts with parent concepts:",
        len(concept_parents)
    )


# ============================================================
# 4. EXTRACT DEFINITION RELATIONSHIPS
# ============================================================

def extract_definition_relationships():

    print("\nExtracting definition relationships...")

    tree = etree.parse(str(DEFINITION_XML))
    root = tree.getroot()

    relationships = []

    for link in root.iter():

        if local_name(link) != "definitionLink":
            continue

        role_uri = get_link_role(link)

        locators = {}

        for child in link:

            if local_name(child) != "loc":
                continue

            locator_label = get_xlink_attribute(child, "label")
            href = get_xlink_attribute(child, "href")

            concept_name = get_concept_name(href)

            if locator_label and concept_name:
                locators[locator_label] = concept_name

        for child in link:

            if local_name(child) != "definitionArc":
                continue

            from_label = get_xlink_attribute(child, "from")
            to_label = get_xlink_attribute(child, "to")

            from_concept = locators.get(from_label)
            to_concept = locators.get(to_label)

            if not from_concept or not to_concept:
                continue

            relationships.append({
                "role_uri": role_uri,
                "from_concept": from_concept,
                "to_concept": to_concept,
                "arcrole": get_xlink_attribute(child, "arcrole"),
                "order": child.get("order", ""),
            })

    pd.DataFrame(relationships).to_csv(
        DEFINITION_CSV,
        index=False,
        encoding="utf-8-sig"
    )

    print("Definition relationships:", len(relationships))


# ============================================================
# 5. EXTRACT ROLE DEFINITIONS
# ============================================================

def extract_roles():

    print("\nExtracting taxonomy role definitions...")

    tree = etree.parse(str(ROLES_XSD))
    root = tree.getroot()

    roles = []

    for element in root.iter():

        if local_name(element) != "roleType":
            continue

        role_id = element.get("id", "")
        role_uri = element.get("roleURI", "")

        definition = ""

        for child in element:

            if local_name(child) == "definition":
                definition = get_resource_text(child)
                break

        roles.append({
            "role_id": role_id,
            "role_uri": role_uri,
            "definition": definition,
        })

    pd.DataFrame(roles).to_csv(
        ROLES_CSV,
        index=False,
        encoding="utf-8-sig"
    )

    role_descriptions = {
        role["role_uri"]: role["definition"]
        for role in roles
        if role["role_uri"]
    }

    print("Role definitions:", len(roles))

    return role_descriptions


# ============================================================
# 6. ADD ROLE DESCRIPTIONS TO CONCEPTS
# ============================================================

def add_role_descriptions(concepts, role_descriptions):

    for record in concepts.values():

        role_uris = [
            role.strip()
            for role in record["presentation_roles"].split("|")
            if role.strip()
        ]

        descriptions = []

        for role_uri in role_uris:

            description = role_descriptions.get(role_uri)

            if description:
                append_unique(
                    {"values": descriptions},
                    "values",
                    description
                )

        record["presentation_role_descriptions"] = join_values(
            descriptions
        )


# ============================================================
# 7. SAVE FINAL CONCEPT MAPPING
# ============================================================

def save_concepts(concepts):

    print("\nSaving taxonomy concept mapping...")

    df = pd.DataFrame(list(concepts.values()))

    df.to_csv(
        CONCEPTS_CSV,
        index=False,
        encoding="utf-8-sig"
    )

    print("Concepts saved:", len(df))
    print("Output:", CONCEPTS_CSV)


# ============================================================
# 8. QUALITY SUMMARY
# ============================================================

def print_quality_summary(concepts):

    df = pd.DataFrame(list(concepts.values()))

    print("\n" + "=" * 75)
    print("BRSR TAXONOMY MAPPING QUALITY SUMMARY")
    print("=" * 75)

    print("\nTOTAL CONCEPTS:", len(df))

    columns = [
        "standard_label",
        "documentation",
        "other_labels",
        "presentation_roles",
        "presentation_role_descriptions",
        "parent_concepts",
    ]

    for column in columns:

        populated = (
            df[column]
            .fillna("")
            .astype(str)
            .str.strip()
            .ne("")
            .sum()
        )

        print(f"\n{column}")
        print("Populated:", populated)
        print("Missing:", len(df) - populated)

    print("\nSAMPLE IDENTITY CONCEPTS")

    targets = [
        "NSESymbol",
        "ScripCode",
        "ISIN",
        "NameOfTheCompany",
        "CorporateIdentityNumber",
        "DateOfIncorporation",
    ]

    for concept in targets:

        result = df[df["concept_name"] == concept]

        if not result.empty:

            row = result.iloc[0]

            print(f"\n{concept}")
            print("Standard label:", row["standard_label"])
            print("Other labels:", row["other_labels"])
            print("Presentation roles:", row["presentation_roles"])
            print("Parent concepts:", row["parent_concepts"])

    print("\n" + "=" * 75)
    print("MAPPING BUILDER COMPLETED")
    print("=" * 75)


# ============================================================
# MAIN
# ============================================================

def configure_version(taxonomy_dir, version, output_dir):
    """
    Point the builder at another taxonomy version, e.g.
    data/raw/sebi_brsr_taxonomy/versions/2024-04-30 with version
    2024-04-30. Defaults (2026-02-28, data/processed) are unchanged.
    """

    global CORE_DIR, BRSR_DIR, CORE_XSD, LABEL_XML
    global PRESENTATION_XML, DEFINITION_XML, ROLES_XSD
    global OUTPUT_DIR, CONCEPTS_CSV, PRESENTATION_CSV
    global DEFINITION_CSV, ROLES_CSV

    root = Path(taxonomy_dir)
    CORE_DIR = root / "core"
    BRSR_DIR = root / "BRSR"
    CORE_XSD = CORE_DIR / "in-capmkt.xsd"
    LABEL_XML = CORE_DIR / "in-capmkt-lab.xml"
    PRESENTATION_XML = BRSR_DIR / f"in-capmkt-pre-{version}.xml"
    DEFINITION_XML = BRSR_DIR / f"in-capmkt-def-{version}.xml"
    ROLES_XSD = BRSR_DIR / f"in-capmkt-roles-{version}.xsd"

    OUTPUT_DIR = Path(output_dir)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    CONCEPTS_CSV = OUTPUT_DIR / "nse_brsr_taxonomy_concepts.csv"
    PRESENTATION_CSV = OUTPUT_DIR / "nse_brsr_presentation_relationships.csv"
    DEFINITION_CSV = OUTPUT_DIR / "nse_brsr_definition_relationships.csv"
    ROLES_CSV = OUTPUT_DIR / "nse_brsr_taxonomy_roles.csv"


def main():

    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--taxonomy-dir", default=None)
    parser.add_argument("--version", default=None)
    parser.add_argument("--output-dir", default=None)
    args = parser.parse_args()

    if args.taxonomy_dir:
        if not (args.version and args.output_dir):
            raise SystemExit(
                "--taxonomy-dir needs --version and --output-dir"
            )
        configure_version(args.taxonomy_dir, args.version, args.output_dir)

    required_files = [
        CORE_XSD,
        LABEL_XML,
        PRESENTATION_XML,
        DEFINITION_XML,
        ROLES_XSD,
    ]

    for file in required_files:

        if not file.exists():
            raise FileNotFoundError(
                f"Required taxonomy file not found: {file}"
            )

    concepts = load_concepts()

    extract_labels(concepts)

    extract_presentation_relationships(concepts)

    extract_definition_relationships()

    role_descriptions = extract_roles()

    add_role_descriptions(
        concepts,
        role_descriptions
    )

    save_concepts(concepts)

    print_quality_summary(concepts)


if __name__ == "__main__":
    main()
