import os
import math
import json
import html
import time
import functions_framework

from google import genai
from google.genai import types


# ============================================================
# CONFIGURATION
# ============================================================

GEMINI_MODEL = os.environ.get(
    "GEMINI_MODEL",
    "gemini-3.6-flash"
)

client = genai.Client(
    api_key=os.environ.get("GEMINI_API_KEY")
)


# ============================================================
# HELPERS
# ============================================================

def safe_float(value):
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def safe_div(numerator, denominator):
    n = safe_float(numerator)
    d = safe_float(denominator)

    if n is None or d is None or d == 0:
        return None

    return n / d


def clean_key(key):
    """
    Power BI Execute Queries frequently returns keys such as:
        [NetSales]
        VW_TABLE[STORE_ULTIMATE]

    Convert them to simpler names.
    """
    if not isinstance(key, str):
        return key

    if "[" in key and key.endswith("]"):
        return key[key.rfind("[") + 1:-1]

    return key


def clean_row(row):
    return {
        clean_key(k): v
        for k, v in row.items()
    }


def clean_rows(rows):
    if not isinstance(rows, list):
        return []

    return [
        clean_row(row)
        for row in rows
        if isinstance(row, dict)
    ]


def first_value(row, names, default=None):
    for name in names:
        if name in row and row[name] is not None:
            return row[name]

    return default


def canonical_store(value):
    if value is None:
        return None

    return " ".join(
        str(value).strip().upper().split()
    )


def percentile_rank(value, peer_values):
    """
    Percentile from 0 to 1.

    Uses the proportion of valid peer observations at or below
    the store's metric.
    """
    value = safe_float(value)

    valid = [
        safe_float(v)
        for v in peer_values
        if safe_float(v) is not None
    ]

    if value is None or not valid:
        return None

    if len(valid) == 1:
        return 0.5

    less_or_equal = sum(
        1 for v in valid
        if v <= value
    )

    return less_or_equal / len(valid)


def blended_percentile(
    value,
    enterprise_values,
    concept_values
):
    ent = percentile_rank(
        value,
        enterprise_values
    )

    concept = percentile_rank(
        value,
        concept_values
    )

    if ent is None and concept is None:
        return None

    if concept is None:
        return ent

    if ent is None:
        return concept

    return (
        0.70 * concept
        +
        0.30 * ent
    )


def points_from_percentile(
    percentile,
    max_points
):
    if percentile is None:
        return None

    return percentile * max_points


def risk_tier(score):
    if score is None:
        return "Not Scored"

    if score >= 75:
        return "Critical"

    if score >= 60:
        return "High"

    if score >= 40:
        return "Elevated"

    if score >= 20:
        return "Moderate"

    return "Low"


# ============================================================
# NORMALIZE INPUT DATA
# ============================================================

# ============================================================
# STORE MAPPING
# ============================================================

def normalize_store_mapping(rows):
    """
    Build canonical translation dictionaries from Store No Mapping.
    """
    canonical_by_store360 = {}
    canonical_by_collections = {}
    metadata_by_store360 = {}

    for raw in clean_rows(rows):

        store360_raw = first_value(
            raw,
            [
                "Store360_Name",
                "Store360Name",
                "STORE NAME_1"
            ]
        )

        collections_raw = first_value(
            raw,
            [
                "Collections_Store",
                "CollectionsStore",
                "STORE NAME"
            ]
        )

        store_no = first_value(
            raw,
            [
                "Store_No",
                "Store No",
                "STORE NO."
            ]
        )

        concept = first_value(
            raw,
            [
                "Concept",
                "CONCEPT"
            ]
        )

        email_code = first_value(
            raw,
            [
                "Email_Code",
                "EMAIL CODE"
            ]
        )

        store360 = canonical_store(store360_raw)
        collections = canonical_store(collections_raw)

        if not store360:
            continue

        canonical_by_store360[store360] = store360

        if collections:
            canonical_by_collections[collections] = store360

        metadata_by_store360.setdefault(
            store360,
            {
                "store": store360,
                "concept": concept,
                "email_code": email_code,
                "store_numbers": []
            }
        )

        if concept and not metadata_by_store360[store360].get("concept"):
            metadata_by_store360[store360]["concept"] = concept

        if email_code and not metadata_by_store360[store360].get("email_code"):
            metadata_by_store360[store360]["email_code"] = email_code

        if store_no is not None:
            store_no_text = str(store_no)
            if store_no_text not in metadata_by_store360[store360]["store_numbers"]:
                metadata_by_store360[store360]["store_numbers"].append(
                    store_no_text
                )

    return {
        "canonical_by_store360": canonical_by_store360,
        "canonical_by_collections": canonical_by_collections,
        "metadata_by_store360": metadata_by_store360
    }


def resolve_store(
    store360_value,
    collections_value,
    mapping
):
    """
    Resolve to the canonical Store360 name.
    """

    store360 = canonical_store(store360_value)
    collections = canonical_store(collections_value)

    if store360:
        mapped = (
            mapping
            .get("canonical_by_store360", {})
            .get(store360)
        )
        if mapped:
            return mapped

    if collections:
        mapped = (
            mapping
            .get("canonical_by_collections", {})
            .get(collections)
        )
        if mapped:
            return mapped

    return store360 or collections

#Operational Audit

def normalize_oa(rows, mapping):
    output = {}

    for raw in clean_rows(rows):

        source_store = first_value(
            raw,
            [
                "STORE_ULTIMATE",
                "Store"
            ]
        )

        store = resolve_store(
            source_store,
            None,
            mapping
        )

        if not store:
            continue

        output[store] = {
            "store": store,

            "concept": first_value(
                raw,
                [
                    "CONCEPT_ULTIMATE",
                    "Concept"
                ]
            ),

            "vp": first_value(
                raw,
                [
                    "VP/COO",
                    "VP_COO",
                    "VP"
                ]
            ),

            "net_sales":
                safe_float(
                    first_value(
                        raw,
                        ["NetSales"]
                    )
                ),

            "transactions":
                safe_float(
                    first_value(
                        raw,
                        ["Transactions"]
                    )
                ),


            "fraud_loss":
                safe_float(
                    first_value(
                        raw,
                        ["FraudLoss"]
                    )
                ),


            "fraud_loss_pct":
                safe_float(
                    first_value(
                        raw,
                        ["FraudLoss_Pct"]
                    )
                ),


            "void_transactions":
                safe_float(
                    first_value(
                        raw,
                        ["Void_Transactions"]
                    )
                ),


            "refund_transactions":
                safe_float(
                    first_value(
                        raw,
                        ["Refund_Transactions"]
                    )
                ),

            "value_flag_transactions":
                safe_float(
                    first_value(
                        raw,
                        ["Infocode_Value_Flag"]
                    )
                ),

            "bff_transactions":
                safe_float(
                    first_value(
                        raw,
                        ["Suspicious_BFF_Discount_Transactions"]
                    )
                ),

            # Multiple Tender
            "bistro_gc_other_tender":
                safe_float(
                    first_value(
                        raw,
                        ["Bistro_GC_Other_Tender_Transactions"]
                    )
                ),

            "irregular_mt_cash_count":
                safe_float(
                    first_value(
                        raw,
                        ["Irregular_MT_Cash_Amount"]
                    )
                ),

            # Beyond Store Hours
            "nonsale_beyond_store_hours":
                safe_float(
                    first_value(
                        raw,
                        ["NonSale_Beyond_Store_Hours"]
                    )
                ),

            "sale_beyond_store_hours":
                safe_float(
                    first_value(
                        raw,
                        ["Sale_Beyond_Store_Hours"]
                    )
                ),

            # Manager Meal
            "manager_meal_peak":
                safe_float(
                    first_value(
                        raw,
                        ["Manager_Meal_Peak_Hours"]
                    )
                ),

            "manager_meal_takeout":
                safe_float(
                    first_value(
                        raw,
                        ["Manager_Meal_Takeout"]
                    )
                ),

            # Diagnostics Only
            "marketing_meal_takeout":
                safe_float(
                    first_value(
                        raw,
                        ["Marketing_Meal_Takeout"]
                    )
                ),

            "manager_meal_threshold_breach":
                safe_float(
                    first_value(
                        raw,
                        ["Manager_Meal_Threshold_Breach_Count"]
                    )
                ),

            "employee_welfare_threshold_breach":
                safe_float(
                    first_value(
                        raw,
                        ["Employee_Welfare_Threshold_Breach_Count"]
                    )
                ),

            "rnd_threshold_breach":
                safe_float(
                    first_value(
                        raw,
                        ["RND_Threshold_Breach_Count"]
                    )
                ),

        }

    return output


def normalize_settlement(rows, mapping):
    output = {}

    for raw in clean_rows(rows):

        store360_raw = first_value(
            raw,
            [
                "Store360Name",
                "Store360_Name",
                "Store"
            ]
        )

        collections_raw = first_value(
            raw,
            [
                "CollectionsStore",
                "Collections_Store"
            ]
        )

        store = resolve_store(
            store360_raw,
            collections_raw,
            mapping
        )

        if not store:
            continue

        output[store] = {
            "store": store,

            "collections_store":
                first_value(
                    raw,
                    ["CollectionsStore"]
                ),

            "cash_pos":
                safe_float(
                    first_value(
                        raw,
                        ["Cash_POS"]
                    )
                ),

            "cash_collection":
                safe_float(
                    first_value(
                        raw,
                        ["Cash_Collection"]
                    )
                ),

            "cash_net_variance":
                safe_float(
                    first_value(
                        raw,
                        ["Cash_Net_Variance"]
                    )
                ),

            "cash_absolute_daily_variance":
                safe_float(
                    first_value(
                        raw,
                        ["Cash_Absolute_Daily_Variance"]
                    )
                ),

            "card_pos":
                safe_float(
                    first_value(
                        raw,
                        ["Card_POS"]
                    )
                ),

            "card_collections":
                safe_float(
                    first_value(
                        raw,
                        ["Card_Collections"]
                    )
                ),

            "card_net_variance":
                safe_float(
                    first_value(
                        raw,
                        ["Card_Net_Variance"]
                    )
                ),

            "card_absolute_daily_variance":
                safe_float(
                    first_value(
                        raw,
                        ["Card_Absolute_Daily_Variance"]
                    )
                ),

            "delivery_pos":
                safe_float(
                    first_value(
                        raw,
                        ["Delivery_POS"]
                    )
                ),

            "delivery_collections":
                safe_float(
                    first_value(
                        raw,
                        ["Delivery_Collections"]
                    )
                ),

            "delivery_net_variance":
                safe_float(
                    first_value(
                        raw,
                        ["Delivery_Net_Variance"]
                    )
                ),

            "delivery_absolute_daily_variance":
                safe_float(
                    first_value(
                        raw,
                        ["Delivery_Absolute_Daily_Variance"]
                    )
                ),
        }

    return output


def normalize_food_cost(rows, mapping):
    output = {}

    for raw in clean_rows(rows):

        source_store = first_value(
            raw,
            ["Store"]
        )

        store = resolve_store(
            source_store,
            None,
            mapping
        )

        if not store:
            continue

        output[store] = {
            "store": store,

            "concept":
                first_value(
                    raw,
                    [
                        "Concept_",
                        "Concept"
                    ]
                ),

            "vp":
                first_value(
                    raw,
                    [
                        "VP/COO_",
                        "VP_COO",
                        "VP"
                    ]
                ),

            "revenue":
                safe_float(
                    first_value(
                        raw,
                        ["Revenue"]
                    )
                ),

            "toplist_cost":
                safe_float(
                    first_value(
                        raw,
                        ["Toplist_Cost"]
                    )
                ),

            "toplist_pct_revenue":
                safe_float(
                    first_value(
                        raw,
                        ["Toplist_PctRevenue"]
                    )
                ),

            "expected_toplist":
                safe_float(
                    first_value(
                        raw,
                        ["Expected_Toplist"]
                    )
                ),

            "expected_toplist_pct_revenue":
                safe_float(
                    first_value(
                        raw,
                        ["Expected_Toplist_PctRevenue"]
                    )
                ),

            "actual_food_cost":
                safe_float(
                    first_value(
                        raw,
                        ["Actual_Food_Cost"]
                    )
                ),

            "actual_food_cost_pct_revenue":
                safe_float(
                    first_value(
                        raw,
                        ["Actual_Food_Cost_PctRevenue"]
                    )
                ),

            "variance_vs_toplist":
                safe_float(
                    first_value(
                        raw,
                        ["Variance_vs_Toplist"]
                    )
                ),

            "variance_vs_toplist_pct_revenue":
                safe_float(
                    first_value(
                        raw,
                        [
                            "Variance_vs_Toplist_PctRevenue"
                        ]
                    )
                ),

            "variance_vs_expected":
                safe_float(
                    first_value(
                        raw,
                        ["Variance_vs_Expected"]
                    )
                ),

            "variance_vs_expected_pct_revenue":
                safe_float(
                    first_value(
                        raw,
                        [
                            "Variance_vs_Expected_PctRevenue"
                        ]
                    )
                ),
        }

    return output


# ============================================================
# CREATE STORE MASTER
# ============================================================

NON_STORE_EXACT_NAMES = {
    "CENTRAL KITCHEN",
    "CENTRAL BAKERY"
}


def is_excluded_non_store(store, concept):
    """
    Exclude commissaries / central-production locations from the
    restaurant enterprise risk ranking.
    """

    store_name = canonical_store(store)

    concept_name = (
        canonical_store(concept)
        if concept is not None
        else None
    )

    if concept_name == "COMMISSARY":
        return True

    if store_name in NON_STORE_EXACT_NAMES:
        return True

    if store_name and store_name.endswith("_CK"):
        return True

    return False


def coverage_confidence(coverage_pct):
    if coverage_pct is None:
        return "Insufficient Data"

    if coverage_pct >= 95:
        return "High Confidence"

    if coverage_pct >= 60:
        return "Moderate Confidence"

    if coverage_pct > 0:
        return "Limited Data"

    return "Insufficient Data"


def coverage_adjusted_rank_score(
    raw_risk_score,
    coverage_pct
):
    """
    Ranking score only:
        raw risk score × sqrt(coverage ratio)
    """

    score = safe_float(
        raw_risk_score
    )

    coverage = safe_float(
        coverage_pct
    )

    if (
        score is None
        or coverage is None
        or coverage <= 0
    ):
        return None

    coverage_ratio = max(
        0.0,
        min(
            coverage / 100.0,
            1.0
        )
    )

    return (
        score
        *
        (coverage_ratio ** 0.5)
    )


def create_store_master(
    oa,
    settlement,
    food_cost,
    mapping
):
    """
    Build enterprise restaurant population from all available
    sources while excluding commissaries / central-production
    locations.

    Hierarchy fallback:
    OA -> Food Cost -> Canonical Store Mapping
    """

    stores = set(
        oa.keys()
    )

    stores.update(
        settlement.keys()
    )

    stores.update(
        food_cost.keys()
    )

    metadata = (
        mapping.get(
            "metadata_by_store360",
            {}
        )
        if isinstance(
            mapping,
            dict
        )
        else {}
    )

    master = {}

    excluded_non_store = []

    for store in stores:

        oa_row = oa.get(
            store,
            {}
        )

        fc_row = food_cost.get(
            store,
            {}
        )

        mapping_row = metadata.get(
            store,
            {}
        )

        concept = (
            oa_row.get(
                "concept"
            )
            or fc_row.get(
                "concept"
            )
            or mapping_row.get(
                "concept"
            )
        )

        vp = (
            oa_row.get(
                "vp"
            )
            or fc_row.get(
                "vp"
            )
            or mapping_row.get(
                "vp"
            )
        )

        if is_excluded_non_store(
            store,
            concept
        ):
            excluded_non_store.append(
                {
                    "store":
                        store,

                    "concept":
                        concept
                }
            )

            continue

        master[store] = {
            "store":
                store,

            "concept":
                concept,

            "vp":
                vp,

            "store_numbers":
                mapping_row.get(
                    "store_numbers",
                    []
                ),

            "email_code":
                mapping_row.get(
                    "email_code"
                ),

            "oa":
                oa.get(
                    store
                ),

            "settlement":
                settlement.get(
                    store
                ),

            "food_cost":
                food_cost.get(
                    store
                )
        }

    print(
        "Excluded non-store locations:",
        excluded_non_store
    )

    return master


# ============================================================
# CALCULATE RAW RISK METRICS
# ============================================================

def calculate_metrics(master):

    for store, record in master.items():

        oa = record.get("oa")
        st = record.get("settlement")
        fc = record.get("food_cost")

        metrics = {}

        # ----------------------------------------------------
        # OPERATIONAL AUDIT
        # ----------------------------------------------------

        if oa:

            txn = oa.get(
                "transactions"
            )

            metrics["fraud_loss_pct"] = (
                oa.get(
                    "fraud_loss_pct"
                )
            )

            metrics["value_flag_per_1000"] = (
                safe_div(
                    oa.get(
                        "value_flag_transactions"
                    ),
                    txn
                )
            )

            if metrics["value_flag_per_1000"] is not None:
                metrics["value_flag_per_1000"] *= 1000

            metrics["void_per_1000"] = (
                safe_div(
                    oa.get(
                        "void_transactions"
                    ),
                    txn
                )
            )

            if metrics["void_per_1000"] is not None:
                metrics["void_per_1000"] *= 1000

            metrics["refund_per_1000"] = (
                safe_div(
                    oa.get(
                        "refund_transactions"
                    ),
                    txn
                )
            )

            if metrics["refund_per_1000"] is not None:
                metrics["refund_per_1000"] *= 1000

            metrics["bff_per_1000"] = (
                safe_div(
                    oa.get(
                        "bff_transactions"
                    ),
                    txn
                )
            )

            if metrics["bff_per_1000"] is not None:
                metrics["bff_per_1000"] *= 1000

            multiple_tender = (
                (
                    oa.get(
                        "irregular_mt_cash_count"
                    )
                    or 0
                )
                +
                (
                    oa.get(
                        "bistro_gc_other_tender"
                    )
                    or 0
                )
            )

            metrics["multiple_tender_per_1000"] = (
                safe_div(
                    multiple_tender,
                    txn
                )
            )

            if metrics["multiple_tender_per_1000"] is not None:
                metrics["multiple_tender_per_1000"] *= 1000

            manager_meal = (
                (
                    oa.get(
                        "manager_meal_peak"
                    )
                    or 0
                )
                +
                (
                    oa.get(
                        "manager_meal_takeout"
                    )
                    or 0
                )
            )

            metrics["manager_meal_per_1000"] = (
                safe_div(
                    manager_meal,
                    txn
                )
            )

            if metrics["manager_meal_per_1000"] is not None:
                metrics["manager_meal_per_1000"] *= 1000

            beyond_store_hours = (
                (
                    oa.get(
                        "nonsale_beyond_store_hours"
                    )
                    or 0
                )
                +
                (
                    oa.get(
                        "sale_beyond_store_hours"
                    )
                    or 0
                )
            )

            metrics["beyond_store_hours_per_1000"] = (
                safe_div(
                    beyond_store_hours,
                    txn
                )
            )

            if metrics["beyond_store_hours_per_1000"] is not None:
                metrics["beyond_store_hours_per_1000"] *= 1000

            

        # ----------------------------------------------------
        # SETTLEMENT
        # ----------------------------------------------------

        if st:

            cash_pos = st.get(
                "cash_pos"
            )

            cash_collection = st.get(
                "cash_collection"
            )

            card_pos = st.get(
                "card_pos"
            )

            card_collections = st.get(
                "card_collections"
            )

            delivery_pos = st.get(
                "delivery_pos"
            )

            delivery_collections = st.get(
                "delivery_collections"
            )

            # A settlement channel is scoreable only when BOTH:
            # - POS amount exists
            # - corresponding collection/deposit amount exists
            #
            # Missing collection data is treated as unavailable,
            # NOT as a 100% settlement variance.

            cash_available = (
                cash_pos is not None
                and cash_collection is not None
                and abs(cash_pos) > 0
            )

            card_available = (
                card_pos is not None
                and card_collections is not None
                and abs(card_pos) > 0
            )

            delivery_available = (
                delivery_pos is not None
                and delivery_collections is not None
                and abs(delivery_pos) > 0
            )

            metrics["cash_abs_variance_pct"] = (
                safe_div(
                    st.get(
                        "cash_absolute_daily_variance"
                    ),
                    abs(cash_pos)
                )
                if cash_available
                else None
            )

            metrics["card_abs_variance_pct"] = (
                safe_div(
                    st.get(
                        "card_absolute_daily_variance"
                    ),
                    abs(card_pos)
                )
                if card_available
                else None
            )

            metrics["delivery_abs_variance_pct"] = (
                safe_div(
                    st.get(
                        "delivery_absolute_daily_variance"
                    ),
                    abs(delivery_pos)
                )
                if delivery_available
                else None
            )

            record[
                "settlement_channel_availability"
            ] = {
                "cash":
                    cash_available,

                "card":
                    card_available,

                "delivery":
                    delivery_available
            }

        else:

            record[
                "settlement_channel_availability"
            ] = {
                "cash": False,
                "card": False,
                "delivery": False
            }

        # ----------------------------------------------------
        # FOOD COST
        # ----------------------------------------------------

        if fc:

            # Only unfavorable gaps generate deterministic
            # risk points in V1.

            actual_pct = (
                fc.get(
                    "actual_food_cost_pct_revenue"
                )
            )

            toplist_pct = (
                fc.get(
                    "toplist_pct_revenue"
                )
            )

            expected_pct = (
                fc.get(
                    "expected_toplist_pct_revenue"
                )
            )

            if (
                actual_pct is not None
                and toplist_pct is not None
            ):
                metrics[
                    "unfavorable_toplist_gap"
                ] = max(
                    0,
                    actual_pct - toplist_pct
                )
            else:
                metrics[
                    "unfavorable_toplist_gap"
                ] = None

            if (
                actual_pct is not None
                and expected_pct is not None
            ):
                metrics[
                    "unfavorable_expected_gap"
                ] = max(
                    0,
                    actual_pct - expected_pct
                )
            else:
                metrics[
                    "unfavorable_expected_gap"
                ] = None

        record["metrics"] = metrics

    return master


# ============================================================
# SCORE CONFIGURATION
# ============================================================

INDICATORS = {

    # OA = 40

    "fraud_loss_pct": {
        "pillar": "oa",
        "max_points": 10
    },

    "value_flag_per_1000": {
        "pillar": "oa",
        "max_points": 5
    },

    "void_per_1000": {
        "pillar": "oa",
        "max_points": 5
    },

    "refund_per_1000": {
        "pillar": "oa",
        "max_points": 5
    },

    "bff_per_1000": {
        "pillar": "oa",
        "max_points": 5
    },

    "multiple_tender_per_1000": {
        "pillar": "oa",
        "max_points": 5
    },

    "manager_meal_per_1000": {
        "pillar": "oa",
        "max_points": 3
    },

    "beyond_store_hours_per_1000": {
        "pillar": "oa",
        "max_points": 2
    },


    # Settlement = 30
    "cash_abs_variance_pct": {
        "pillar": "settlement",
        "max_points": 12
    },

    "card_abs_variance_pct": {
        "pillar": "settlement",
        "max_points": 9
    },

    "delivery_abs_variance_pct": {
        "pillar": "settlement",
        "max_points": 9
    },

    # Food Cost currently = 25 observable points.
    # These are rescaled to the 30-point pillar.
    "unfavorable_toplist_gap": {
        "pillar": "food_cost",
        "max_points": 15
    },

    "unfavorable_expected_gap": {
        "pillar": "food_cost",
        "max_points": 10
    }
}


PILLAR_TARGETS = {
    "oa": 40,
    "settlement": 30,
    "food_cost": 30
}


# ============================================================
# SCORE STORES
# ============================================================

def score_stores(master):

    records = list(master.values())

    for indicator, cfg in INDICATORS.items():

        enterprise_values = [
            r["metrics"].get(indicator)
            for r in records
            if r["metrics"].get(indicator)
            is not None
        ]

        for record in records:

            value = (
                record["metrics"]
                .get(indicator)
            )

            if value is None:
                continue

            concept = record.get("concept")

            concept_values = [
                r["metrics"].get(indicator)
                for r in records
                if r.get("concept") == concept
                and
                r["metrics"].get(indicator)
                is not None
            ]

            pct = blended_percentile(
                value,
                enterprise_values,
                concept_values
            )

            record.setdefault(
                "indicator_scores",
                {}
            )

            record["indicator_scores"][
                indicator
            ] = {
                "raw_value": value,
                "risk_percentile": pct,
                "raw_points":
                    points_from_percentile(
                        pct,
                        cfg["max_points"]
                    ),
                "max_points":
                    cfg["max_points"],
                "pillar":
                    cfg["pillar"]
            }

    # --------------------------------------------------------
    # PILLAR SCORES + PARTIAL INDICATOR COVERAGE
    # --------------------------------------------------------

    for record in records:

        pillar_results = {}

        total_available_weight = 0.0
        total_weighted_points = 0.0

        for pillar, target_max in (
            PILLAR_TARGETS.items()
        ):

            configured_max = sum(
                cfg["max_points"]
                for cfg
                in INDICATORS.values()
                if cfg["pillar"] == pillar
            )

            items = [
                x
                for x in
                record.get(
                    "indicator_scores",
                    {}
                ).values()
                if x["pillar"] == pillar
            ]

            available_points = sum(
                x["raw_points"]
                for x in items
                if x["raw_points"] is not None
            )

            available_max = sum(
                x["max_points"]
                for x in items
                if x["raw_points"] is not None
            )

            if (
                configured_max <= 0
                or available_max <= 0
            ):
                pillar_results[pillar] = {
                    "score": None,
                    "max_score":
                        target_max,
                    "coverage": 0,
                    "coverage_pct": 0,
                    "available_weight": 0
                }

                continue

            # Scale configured indicator weights to the intended
            # pillar weight. For OA and Settlement this is 1.0.
            # Food Cost currently has 25 configured observable
            # points scaled to the intended 30-point pillar.
            pillar_scale = (
                target_max
                /
                configured_max
            )

            coverage_ratio = (
                available_max
                /
                configured_max
            )

            available_weight = (
                available_max
                *
                pillar_scale
            )

            weighted_points = (
                available_points
                *
                pillar_scale
            )

            normalized_score = (
                available_points
                /
                available_max
                *
                target_max
            )

            pillar_results[pillar] = {
                "score":
                    normalized_score,
                "max_score":
                    target_max,
                "coverage":
                    coverage_ratio,
                "coverage_pct":
                    coverage_ratio * 100,
                "available_weight":
                    available_weight
            }

            total_available_weight += (
                available_weight
            )

            total_weighted_points += (
                weighted_points
            )

        record["pillar_scores"] = (
            pillar_results
        )

        # ----------------------------------------------------
        # OVERALL SCORE + COVERAGE
        #
        # Missing indicators are removed from BOTH numerator and
        # denominator. This means missing delivery/cash collection
        # data does not create risk points and correctly reduces
        # overall coverage.
        # ----------------------------------------------------

        if total_available_weight <= 0:

            overall = None
            coverage_pct = 0

        else:

            overall = (
                total_weighted_points
                /
                total_available_weight
                *
                100
            )

            coverage_pct = (
                total_available_weight
                /
                100
                *
                100
            )

        record["risk_score"] = overall

        record["risk_tier"] = (
            risk_tier(
                overall
            )
        )

        record["coverage_pct"] = (
            coverage_pct
        )

    # --------------------------------------------------------
    # COVERAGE / CONFIDENCE
    # --------------------------------------------------------

    for record in records:

        record[
            "coverage_confidence"
        ] = coverage_confidence(
            record.get(
                "coverage_pct"
            )
        )

        record[
            "ranking_score"
        ] = coverage_adjusted_rank_score(
            record.get(
                "risk_score"
            ),
            record.get(
                "coverage_pct"
            )
        )

    # --------------------------------------------------------
    # RAW RISK PERCENTILES
    # --------------------------------------------------------

    valid_scores = [
        record["risk_score"]
        for record in records
        if record[
            "risk_score"
        ] is not None
    ]

    for record in records:

        score = record.get(
            "risk_score"
        )

        record[
            "enterprise_risk_percentile"
        ] = percentile_rank(
            score,
            valid_scores
        )

        concept = record.get(
            "concept"
        )

        concept_scores = [
            peer["risk_score"]
            for peer in records
            if peer.get(
                "concept"
            ) == concept
            and peer[
                "risk_score"
            ] is not None
        ]

        record[
            "concept_risk_percentile"
        ] = percentile_rank(
            score,
            concept_scores
        )

    # --------------------------------------------------------
    # COVERAGE-AWARE ENTERPRISE RANK
    # --------------------------------------------------------

    ranked = sorted(
        records,
        key=lambda record: (
            record.get(
                "ranking_score"
            )
            if record.get(
                "ranking_score"
            ) is not None
            else -1
        ),
        reverse=True
    )

    rank = 0

    for record in ranked:

        if record.get(
            "ranking_score"
        ) is None:
            record[
                "enterprise_rank"
            ] = None
            continue

        rank += 1

        record[
            "enterprise_rank"
        ] = rank

    return ranked


# ============================================================
# REPORT PAYLOAD
# ============================================================

def compact_store(record):

    indicator_scores = (
        record.get(
            "indicator_scores",
            {}
        )
    )

    def pillar_score(name):
        value = (
            record["pillar_scores"]
            [name]["score"]
        )

        return (
            round(value, 2)
            if value is not None
            else None
        )

    top_drivers = sorted(
        [
            {
                "indicator": k,
                "points":
                    round(
                        v["raw_points"],
                        2
                    )
            }
            for k, v
            in indicator_scores.items()
            if v["raw_points"]
            is not None
        ],
        key=lambda x: x["points"],
        reverse=True
    )[:5]

    return {
        "rank":
            record.get(
                "enterprise_rank"
            ),

        "store":
            record["store"],

        "concept":
            record.get("concept"),

        "vp":
            record.get("vp"),

        "risk_score":
            round(
                record["risk_score"],
                2
            )
            if record["risk_score"]
            is not None
            else None,

        "risk_tier":
            record["risk_tier"],

        "enterprise_percentile":
            round(
                (
                    record[
                        "enterprise_risk_percentile"
                    ]
                    or 0
                )
                * 100,
                1
            )
            if record[
                "enterprise_risk_percentile"
            ] is not None
            else None,

        "concept_percentile":
            round(
                (
                    record[
                        "concept_risk_percentile"
                    ]
                    or 0
                )
                * 100,
                1
            )
            if record[
                "concept_risk_percentile"
            ] is not None
            else None,

        "oa_score":
            pillar_score("oa"),

        "settlement_score":
            pillar_score(
                "settlement"
            ),

        "food_cost_score":
            pillar_score(
                "food_cost"
            ),

        "coverage_pct":
            round(
                record[
                    "coverage_pct"
                ],
                1
            ),

        "coverage_confidence":
            record.get(
                "coverage_confidence"
            ),

        "ranking_score":
            round(
                record.get(
                    "ranking_score"
                ),
                2
            )
            if record.get(
                "ranking_score"
            ) is not None
            else None,

        "settlement_channel_availability":
            record.get(
                "settlement_channel_availability",
                {
                    "cash": False,
                    "card": False,
                    "delivery": False
                }
            ),

        "top_risk_drivers":
            top_drivers,

        "metrics":
            record.get("metrics"),

        "oa_diagnostics":
            record.get("oa"),

        "settlement_diagnostics":
            record.get(
                "settlement"
            ),

        "food_cost_diagnostics":
            record.get(
                "food_cost"
            )
    }


# ============================================================
# GEMINI REPORT
# ============================================================

def build_prompt(
    reporting_period,
    scored_stores
):

    report_data = [
        compact_store(r)
        for r in scored_stores
    ]

    prompt = f"""
You are the Enterprise Store Risk Reporting Engine for
The Bistro Group.

REPORTING PERIOD:
{json.dumps(reporting_period)}

IMPORTANT:
The numerical risk scores, rankings, percentiles, pillar
scores and classifications in the supplied dataset were
calculated by a deterministic scoring engine.

DO NOT:
- recalculate any risk score
- change any risk tier
- change any ranking
- change any percentile
- accuse any store or employee of fraud, theft or misconduct
- prescribe management actions or recommendations
- invent missing information

The Store Risk Index is a CONTROL AND ANOMALY RISK indicator.
It does not establish that fraud, theft or misconduct occurred.

Produce a concise CFO-ready HTML report.

REQUIRED STRUCTURE:

<h1>Enterprise Store Risk Report</h1>

1. EXECUTIVE OVERVIEW

Summarize:
- enterprise risk distribution
- number of Critical, High, Elevated, Moderate and Low stores
- concentration of risk by concept where evident
- data coverage limitations
- important cross-signal patterns

2. ENTERPRISE STORE RISK RANKING

Create a table containing ALL scored stores.

Columns:
Rank
Store
Concept
Risk Score
Risk Tier
OA Score / 40
Settlement Score / 30
Food Cost Score / 30
Enterprise Percentile
Concept Percentile
Data Coverage

Order exactly by the supplied enterprise rank.

3. CRITICAL AND HIGH RISK STORES

Discuss only Critical and High stores.

For each store:
- identify the largest numerical risk drivers
- distinguish OA, settlement and food-cost signals
- identify whether the store is a multi-signal anomaly
- cite underlying metrics where useful
- use neutral control-risk language

Do not state that fraud occurred.

4. CROSS-SIGNAL RISK

Identify stores showing elevated signals across two or more
independent pillars.

Give greater narrative emphasis to stores with independent
signals across:
- transaction / operational audit
- settlement
- food cost / inventory

5. CONCEPT-LEVEL PATTERNS

Discuss meaningful concentrations of risk within concepts.
Do not assume a concept is problematic merely because it
contains more stores.

6. DATA COVERAGE

Identify material limitations caused by missing OA,
settlement or food-cost information.

A missing source is not equivalent to zero risk.

7. APPENDIX — RISK SCORING METHODOLOGY

Explain:

Overall Store Risk Index: 0–100.

Pillar weights:
Operational Audit = 40
Settlement = 30
Food Cost = 30

Operational Audit indicators:
FraudLoss % = 10
Non Sale Value Flag / 1000 transactions = 5
Void / 1000 = 5
Refund / 1000 = 5
Suspicious BFF Transactions/ 1000 = 5
Multiple tender / 1000 = 5
Manager’s Meal – Peak & Take-Out / 1000 = 3
Beyond Store Hours / 1000 = 2


Settlement:
Cash absolute daily variance / Cash POS = 12
Card absolute daily variance / Card POS = 9
Delivery absolute daily variance / Delivery POS = 9

Food Cost:
Unfavorable Actual vs Toplist % Revenue = 15
Unfavorable Actual vs Expected % Revenue = 10

The currently observable 25 Food Cost indicator points are
normalized to the 30-point Food Cost pillar.

Indicator scoring:
70% concept-relative percentile
+
30% enterprise-relative percentile.

Missing pillars are excluded from the available score
denominator and disclosed through Data Coverage.

Include this statement prominently:

"The Store Risk Index measures relative control and anomaly
risk. It does not establish fraud, theft, misconduct or
financial loss."

Use clean HTML only.
Do not include markdown fences.

ENTERPRISE RISK DATA:
{json.dumps(report_data, default=str)}
"""

    return prompt


def call_gemini(prompt):

    waits = [0, 5, 10]

    last_error = None

    for attempt, wait_seconds in enumerate(
        waits,
        start=1
    ):

        if wait_seconds:
            time.sleep(wait_seconds)

        try:

            print(
                f"Gemini attempt "
                f"{attempt}/{len(waits)}"
            )

            response = (
                client.models.generate_content(
                    model=GEMINI_MODEL,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        temperature=0.1,
                        thinking_config=
                            types.ThinkingConfig(
                                thinking_budget=64
                            )
                    )
                )
            )

            return response

        except Exception as exc:

            last_error = exc

            print(
                f"Gemini attempt "
                f"{attempt} failed: "
                f"{repr(exc)}"
            )

    raise last_error



# ============================================================
# REPORT GENERATION FROM PRE-SCORED STORES
# ============================================================

INDICATOR_LABELS = {
    "fraud_loss_pct": "Fraud Loss %",
    "value_flag_per_1000": "Non-Sale Value Flag / 1,000",
    "void_per_1000": "Void / 1,000",
    "refund_per_1000": "Refund / 1,000",
    "bff_per_1000": "BFF / 1,000",
    "multiple_tender_per_1000": "Multiple Tender / 1,000",
    "manager_meal_per_1000": "Manager Meal (Peak + Takeout) / 1,000",
    "beyond_store_hours_per_1000": "Beyond Store Hours / 1,000",
    "cash_abs_variance_pct": "Cash absolute daily variance / Cash POS",
    "card_abs_variance_pct": "Card absolute daily variance / Card POS",
    "delivery_abs_variance_pct": "Delivery absolute daily variance / Delivery POS",
    "unfavorable_toplist_gap": "Unfavorable Actual Food Cost vs Toplist (% of Revenue)",
    "unfavorable_expected_gap": "Unfavorable Actual Food Cost vs Expected Toplist (% of Revenue)"
}


def esc(value):
    if value is None:
        return ""
    return html.escape(str(value))


def fmt_num(value, decimals=2):
    value = safe_float(value)
    if value is None:
        return "—"
    return f"{value:,.{decimals}f}"


def fmt_pct(value, decimals=1):
    value = safe_float(value)
    if value is None:
        return "—"
    return f"{value:,.{decimals}f}%"


def summarize_for_report(scored_stores):
    """
    Build a compact deterministic summary for Gemini.
    The full enterprise ranking remains outside Gemini.
    """
    tier_counts = {
        "Critical": 0,
        "High": 0,
        "Elevated": 0,
        "Moderate": 0,
        "Low": 0,
        "Not Scored": 0
    }

    confidence_counts = {
        "High Confidence": 0,
        "Moderate Confidence": 0,
        "Limited Data": 0,
        "Insufficient Data": 0
    }

    concept_summary = {}
    vp_summary = {}
    cross_signal_stores = []

    for store in scored_stores:
        tier = store.get("risk_tier") or "Not Scored"
        tier_counts.setdefault(tier, 0)
        tier_counts[tier] += 1

        confidence = (
            store.get("coverage_confidence")
            or "Insufficient Data"
        )
        confidence_counts.setdefault(confidence, 0)
        confidence_counts[confidence] += 1

        concept = store.get("concept") or "Unmapped"
        vp = store.get("vp") or "Unmapped"

        concept_summary.setdefault(
            concept,
            {"stores": 0, "critical": 0, "high": 0}
        )
        concept_summary[concept]["stores"] += 1
        if tier == "Critical":
            concept_summary[concept]["critical"] += 1
        elif tier == "High":
            concept_summary[concept]["high"] += 1

        vp_summary.setdefault(
            vp,
            {"stores": 0, "critical": 0, "high": 0}
        )
        vp_summary[vp]["stores"] += 1
        if tier == "Critical":
            vp_summary[vp]["critical"] += 1
        elif tier == "High":
            vp_summary[vp]["high"] += 1

        pillar_scores = [
            safe_float(store.get("oa_score")),
            safe_float(store.get("settlement_score")),
            safe_float(store.get("food_cost_score"))
        ]

        material_pillars = sum(
            1
            for score in pillar_scores
            if score is not None and score >= 18
        )

        if material_pillars >= 2:
            cross_signal_stores.append(
                {
                    "rank": store.get("rank"),
                    "store": store.get("store"),
                    "risk_score": store.get("risk_score"),
                    "risk_tier": tier,
                    "oa_score": store.get("oa_score"),
                    "settlement_score": store.get("settlement_score"),
                    "food_cost_score": store.get("food_cost_score"),
                    "coverage_pct": store.get("coverage_pct")
                }
            )

    concept_concentration = sorted(
        [
            {"concept": key, **value}
            for key, value in concept_summary.items()
        ],
        key=lambda x: (
            x["critical"],
            x["high"],
            x["stores"]
        ),
        reverse=True
    )[:15]

    vp_concentration = sorted(
        [
            {"vp": key, **value}
            for key, value in vp_summary.items()
        ],
        key=lambda x: (
            x["critical"],
            x["high"],
            x["stores"]
        ),
        reverse=True
    )[:15]

    top_ranked_stores = []

    for store in scored_stores[:40]:
        metrics = store.get("metrics") or {}
        top_drivers = []

        for driver in (store.get("top_risk_drivers") or [])[:5]:
            indicator = driver.get("indicator")
            top_drivers.append(
                {
                    "indicator": indicator,
                    "label": INDICATOR_LABELS.get(
                        indicator,
                        indicator
                    ),
                    "points": driver.get("points"),
                    "raw_value": metrics.get(indicator)
                }
            )

        top_ranked_stores.append(
            {
                "rank": store.get("rank"),
                "store": store.get("store"),
                "concept": store.get("concept"),
                "vp": store.get("vp"),
                "risk_score": store.get("risk_score"),
                "risk_tier": store.get("risk_tier"),
                "ranking_score": store.get("ranking_score"),
                "oa_score": store.get("oa_score"),
                "settlement_score": store.get("settlement_score"),
                "food_cost_score": store.get("food_cost_score"),
                "enterprise_percentile":
                    store.get("enterprise_percentile"),
                "concept_percentile":
                    store.get("concept_percentile"),
                "coverage_pct": store.get("coverage_pct"),
                "coverage_confidence":
                    store.get("coverage_confidence"),
                "settlement_channel_availability":
                    store.get("settlement_channel_availability"),
                "top_risk_drivers": top_drivers
            }
        )

    return {
        "store_count": len(scored_stores),
        "tier_counts": tier_counts,
        "coverage_confidence_counts": confidence_counts,
        "concept_concentration": concept_concentration,
        "vp_concentration": vp_concentration,
        "cross_signal_stores": cross_signal_stores[:25],
        "top_ranked_stores": top_ranked_stores
    }


def build_narrative_prompt(
    reporting_period,
    report_summary
):
    return f"""
You are the Enterprise Store Risk Reporting Engine for
The Bistro Group.

REPORTING PERIOD:
{json.dumps(reporting_period, default=str)}

DETERMINISTIC REPORT SUMMARY:
{json.dumps(report_summary, default=str)}

The numerical store scores, tiers, rankings, percentiles, coverage
values and confidence classifications were already calculated by a
deterministic Python engine.

DO NOT:
- recalculate or change any score, rank, tier, percentile or coverage
- infer fraud, theft, misconduct or employee wrongdoing
- invent unavailable data
- prescribe management actions or recommendations
- generate the complete enterprise ranking table
- generate the methodology annex

Return CLEAN HTML ONLY.

Produce only these narrative sections:

<h2>1. Executive Risk Summary</h2>
Concise CFO-level overview of risk-tier distribution, highest
concentrations, concept/VP patterns, cross-signal themes and material
coverage limitations.

<h2>2. Critical and High Risk Stores</h2>
Discuss the most material Critical and High stores. Prioritize the
highest-ranked and strongest multi-signal cases rather than writing one
paragraph per store. Reference supplied top risk drivers and coverage.

<h2>3. Cross-Signal Risk</h2>
Identify meaningful stores with independent elevated signals across
Operational Audit, Collections & Settlement, and Food Cost & Inventory.

<h2>4. Concept and VP Risk Concentration</h2>
Discuss concentrations without assuming a larger concept or VP is
inherently higher risk.

<h2>5. Data Coverage and Confidence</h2>
Explain meaningful coverage limitations. Missing data is not equivalent
to low risk.

<h2>6. Management Watchlist</h2>
Create a compact HTML table of approximately 10-15 stores requiring the
most management visibility.

Columns:
Rank
Store
Risk Tier
Risk Score
Coverage
Primary Risk Drivers
Cross-Signal

This is a watchlist only. Do not prescribe actions.

Use control-risk, exception and anomaly language.
Do not state that fraud occurred.

FOOD COST TERMINOLOGY — MANDATORY:
- Do NOT describe Food Cost indicators as "revenue gaps", "topline gaps",
  "revenue shortfalls", or similar wording.
- "Unfavorable Actual vs Toplist % Revenue" means the gap between
  Actual Food Cost % of Revenue and Toplist / theoretical Food Cost %
  of Revenue.
- "Unfavorable Actual vs Expected Toplist % Revenue" means the gap
  between Actual Food Cost % of Revenue and Expected Toplist Food Cost %
  of Revenue.
- Preferred wording:
  "unfavorable Actual Food Cost vs Toplist gap"
  "unfavorable Actual Food Cost vs Expected Toplist gap"
  "Food Cost % of Revenue variance"
- Toplist means recipe / theoretical food cost.
- Revenue is only the denominator used to express Food Cost percentages;
  these indicators do NOT represent lost revenue or topline performance.
"""


def build_enterprise_ranking_html(scored_stores):
    rows = []

    for store in scored_stores:
        rows.append(
            "<tr>"
            f"<td>{esc(store.get('rank'))}</td>"
            f"<td>{esc(store.get('store'))}</td>"
            f"<td>{esc(store.get('concept'))}</td>"
            f"<td>{esc(store.get('vp'))}</td>"
            f"<td class='num'>{fmt_num(store.get('risk_score'))}</td>"
            f"<td>{esc(store.get('risk_tier'))}</td>"
            f"<td class='num'>{fmt_num(store.get('ranking_score'))}</td>"
            f"<td class='num'>{fmt_num(store.get('oa_score'))}</td>"
            f"<td class='num'>{fmt_num(store.get('settlement_score'))}</td>"
            f"<td class='num'>{fmt_num(store.get('food_cost_score'))}</td>"
            f"<td class='num'>{fmt_pct(store.get('enterprise_percentile'))}</td>"
            f"<td class='num'>{fmt_pct(store.get('concept_percentile'))}</td>"
            f"<td class='num'>{fmt_pct(store.get('coverage_pct'))}</td>"
            f"<td>{esc(store.get('coverage_confidence'))}</td>"
            "</tr>"
        )

    return (
        "<h2>7. Enterprise Store Risk Ranking</h2>"
        "<p>The ranking below is generated directly from the "
        "deterministic scoring output. Gemini does not calculate "
        "or reorder these results.</p>"
        "<table><thead><tr>"
        "<th>Rank</th>"
        "<th>Store</th>"
        "<th>Concept</th>"
        "<th>VP</th>"
        "<th>Risk Score</th>"
        "<th>Risk Tier</th>"
        "<th>Ranking Score</th>"
        "<th>OA / 40</th>"
        "<th>Settlement / 30</th>"
        "<th>Food Cost / 30</th>"
        "<th>Enterprise %ile</th>"
        "<th>Concept %ile</th>"
        "<th>Coverage</th>"
        "<th>Confidence</th>"
        "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table>"
    )


def build_methodology_annex_html():
    return """
<div class="appendix">
<h2>Annex A — Report Methodology and Ranking Logic</h2>

<h3>A1. Purpose of the Report</h3>
<p>The Enterprise Store Risk Report is a store-level control and anomaly
risk screening and prioritization tool. It identifies stores with
comparatively elevated indicators across available operational,
settlement and food-cost data. It is not a finding of fraud, theft,
misconduct or financial loss.</p>

<h3>A2. Data Sources</h3>
<table>
<thead><tr><th>Risk Dimension</th><th>Primary Information</th></tr></thead>
<tbody>
<tr><td>Operational Audit</td><td>Fraud Loss %, Non-Sale Value Flag activity, Void 
transactions, Refund transactions, BFF activity, Multiple Tender activity, Manager Meal 
activity, and Beyond Store Hours activity.</td></tr>
<tr><td>Collections &amp; Settlement</td><td>POS amounts versus corresponding
collection/deposit information for Cash, Card and Delivery.</td></tr>
<tr><td>Food Cost &amp; Inventory</td><td>Actual Food Cost, Toplist/recipe cost
and Expected Toplist relative to Food &amp; Beverage revenue.</td></tr>
<tr><td>Canonical Store Mapping</td><td>Store360 identity,
Collections-store identity and Concept fields used to align source
systems.</td></tr>
</tbody></table>

<h3>A3. Store Population and Exclusions</h3>
<p>The ranking is intended for operating restaurant stores. Exclusions
are Concept = COMMISSARY; store names ending in <strong>_CK</strong>;
<strong>CENTRAL KITCHEN</strong>; and <strong>CENTRAL BAKERY</strong>.</p>

<h3>A4. Canonical Store Matching</h3>
<p>Store360 names and Collections-store names are translated to a
canonical operating-store identity before source datasets are combined.</p>

<h3>A5. Operational Audit Indicators</h3>
<table>
<thead><tr><th>Indicator</th><th>Maximum Points</th></tr></thead>
<tbody>
<tr><td>Fraud Loss %</td><td>10</td></tr>
<tr><td>Non-Sale Value Flag / 1,000 Transactions</td><td>5</td></tr>
<tr><td>Void / 1,000 Transactions</td><td>5</td></tr>
<tr><td>Refund / 1,000 Transactions</td><td>5</td></tr>
<tr><td>BFF / 1,000 Transactions</td><td>5</td></tr>
<tr><td>Multiple Tender / 1,000 Transactions</td><td>5</td></tr>
<tr><td>Manager Meal (Peak + Takeout) / 1,000 Transactions</td><td>3</td></tr>
<tr><td>Beyond Store Hours / 1,000 Transactions</td><td>2</td></tr>
</tbody></table>
<p><strong>Indicator Rate per 1,000 =
Indicator Transaction Count ÷ Total Transactions × 1,000.</strong></p>

<h3>A6. Collections &amp; Settlement Indicators</h3>
<table>
<thead><tr><th>Indicator</th><th>Maximum Points</th></tr></thead>
<tbody>
<tr><td>Cash absolute daily variance / Cash POS</td><td>12</td></tr>
<tr><td>Card absolute daily variance / Card POS</td><td>9</td></tr>
<tr><td>Delivery absolute daily variance / Delivery POS</td><td>9</td></tr>
</tbody></table>
<p>Net variance is directional. Absolute daily variance measures total
exception magnitude without favorable and unfavorable days cancelling.
A channel is scoreable only when both POS and the corresponding
collection/deposit amount exist. Missing collection/deposit information
is unavailable data, not a 100% variance or a financial loss.</p>

<h3>A7. Food Cost Indicators</h3>
<table>
<thead><tr><th>Indicator</th><th>Maximum Points</th></tr></thead>
<tbody>
<tr><td>Unfavorable Actual Food Cost vs Toplist (% of Revenue)</td><td>15</td></tr>
<tr><td>Unfavorable Actual Food Cost vs Expected Toplist (% of Revenue)</td><td>10</td></tr>
</tbody></table>
<p>Toplist means recipe/theoretical cost.</p>
<p><strong>Unfavorable Toplist Gap =
max(0, Actual Food Cost % Revenue − Toplist % Revenue)</strong></p>
<p><strong>Unfavorable Expected Gap =
max(0, Actual Food Cost % Revenue − Expected Toplist % Revenue)</strong></p>
<p>Favorable gaps do not generate risk points.</p>

<h3>A8. Indicator Percentile Scoring</h3>
<p><strong>Indicator Risk Percentile =
70% Concept-Relative Percentile +
30% Enterprise-Relative Percentile.</strong></p>

<h3>A9. Pillar Scores</h3>
<table>
<thead><tr><th>Pillar</th><th>Target Weight</th></tr></thead>
<tbody>
<tr><td>Operational Audit</td><td>40</td></tr>
<tr><td>Collections &amp; Settlement</td><td>30</td></tr>
<tr><td>Food Cost &amp; Inventory</td><td>30</td></tr>
</tbody></table>
<p>The currently observable Food Cost indicators contain 25 configured
raw points and are normalized to the intended 30-point Food Cost
pillar.</p>

<h3>A10. Missing Data and Data Coverage</h3>
<p>Missing indicators receive neither risk points nor favorable points.
They are excluded from both the available score numerator and
denominator. Data Coverage falls accordingly. Missing information is
never interpreted as zero risk.</p>

<h3>A11. Raw Risk Score</h3>
<p>Risk Score is a 0–100 representation of the severity of the available
observed evidence.</p>

<h3>A12. Coverage Confidence</h3>
<table>
<thead><tr><th>Coverage</th><th>Classification</th></tr></thead>
<tbody>
<tr><td>&ge; 95%</td><td>High Confidence</td></tr>
<tr><td>&ge; 60% and &lt; 95%</td><td>Moderate Confidence</td></tr>
<tr><td>&gt; 0% and &lt; 60%</td><td>Limited Data</td></tr>
<tr><td>0% / no scoreable data</td><td>Insufficient Data</td></tr>
</tbody></table>

<h3>A13. Coverage-Aware Enterprise Ranking</h3>
<p><strong>Ranking Score =
Raw Risk Score × √(Coverage Ratio).</strong></p>
<p>Raw Risk Score remains unchanged. Ranking Score is used only for
enterprise ordering. The square-root coverage adjustment prevents
limited-data stores from automatically outranking stores supported by
broader evidence while preserving visibility of severe anomalies.</p>

<h3>A14. Enterprise Percentile</h3>
<p>Enterprise Percentile is relative Risk Score position versus the
eligible enterprise population for the reporting period. It is not a
probability that fraud occurred.</p>

<h3>A15. Concept Percentile</h3>
<p>Concept Percentile compares the store's Risk Score with eligible
stores in the same concept.</p>

<h3>A16. Risk Tiers</h3>
<table>
<thead><tr><th>Risk Score</th><th>Risk Tier</th></tr></thead>
<tbody>
<tr><td>&ge; 75</td><td>Critical</td></tr>
<tr><td>60 to &lt; 75</td><td>High</td></tr>
<tr><td>40 to &lt; 60</td><td>Elevated</td></tr>
<tr><td>20 to &lt; 40</td><td>Moderate</td></tr>
<tr><td>&lt; 20</td><td>Low</td></tr>
</tbody></table>

<h3>A17. Top Risk Drivers</h3>
<p>Top Risk Drivers are the scoreable indicators contributing the
largest supplied point values to the store's observed risk profile.</p>

<h3>A18. Interpretation Limitations</h3>
<div class="methodology-warning"><strong>
The Store Risk Index measures relative control and anomaly risk.
It does not establish fraud, theft, misconduct or financial loss.
</strong></div>
<p>A low score does not guarantee the absence of risk, particularly
where data coverage is incomplete.</p>
</div>
"""


def assemble_enterprise_report_html(
    narrative_html,
    scored_stores
):
    return (
        narrative_html
        + build_enterprise_ranking_html(
            scored_stores
        )
        + build_methodology_annex_html()
    )


def generate_report_from_scored(
    reporting_period,
    scored_stores
):
    """
    Fast report mode:
    Gemini = narrative only.
    Python = full ranking table + permanent methodology annex.
    """

    report_summary = summarize_for_report(
        scored_stores
    )

    prompt = build_narrative_prompt(
        reporting_period,
        report_summary
    )

    print(
        "Narrative prompt characters:",
        len(prompt)
    )

    response = call_gemini(
        prompt
    )

    usage = getattr(
        response,
        "usage_metadata",
        None
    )

    input_tokens = (
        getattr(
            usage,
            "prompt_token_count",
            0
        )
        or 0
    )

    output_tokens = (
        getattr(
            usage,
            "candidates_token_count",
            0
        )
        or 0
    )

    thinking_tokens = (
        getattr(
            usage,
            "thoughts_token_count",
            0
        )
        or 0
    )

    total_tokens = (
        getattr(
            usage,
            "total_token_count",
            0
        )
        or 0
    )

    estimated_cost_usd = (
        input_tokens
        / 1_000_000
        * 0.75
        +
        (
            output_tokens
            + thinking_tokens
        )
        / 1_000_000
        * 3.75
    )

    report_html = assemble_enterprise_report_html(
        response.text,
        scored_stores
    )

    return {
        "success": True,
        "mode": "generate_report",
        "report_success": True,
        "model": GEMINI_MODEL,
        "reporting_period": reporting_period,
        "store_count": len(scored_stores),
        "report_html": report_html,
        "usage": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "thinking_tokens": thinking_tokens,
            "total_tokens": total_tokens,
            "estimated_cost_usd": estimated_cost_usd
        }
    }


# ============================================================
# HTTP ENTRY POINT
# ============================================================

@functions_framework.http
def app(request):

    try:

        payload = (
            request.get_json(
                silent=True
            )
            or {}
        )

        mode = (
            payload.get(
                "mode",
                "score_only"
            )
            or "score_only"
        ).strip().lower()

        if mode not in {
            "score_only",
            "generate_report"
        }:
            return (
                {
                    "success": False,
                    "error": "InvalidMode",
                    "message": (
                        "mode must be 'score_only' "
                        "or 'generate_report'"
                    )
                },
                400
            )

        reporting_period = (
            payload.get(
                "reporting_period",
                {}
            )
        )

        pre_scored_stores = (
            payload.get(
                "scored_stores",
                []
            )
        )

        # ========================================================
        # REPORT FROM PRE-SCORED DATA
        # ========================================================
        #
        # If mode=generate_report and scored_stores is supplied,
        # DO NOT recalculate the score. Gemini receives the fixed,
        # deterministic score_only output directly.
        #
        if (
            mode == "generate_report"
            and isinstance(
                pre_scored_stores,
                list
            )
            and len(
                pre_scored_stores
            ) > 0
        ):
            try:

                report_result = (
                    generate_report_from_scored(
                        reporting_period,
                        pre_scored_stores
                    )
                )

                return (
                    report_result,
                    200
                )

            except Exception as gemini_exc:

                print(
                    "Gemini report generation failed:",
                    repr(
                        gemini_exc
                    )
                )

                # Preserve the deterministic scored data even when
                # Gemini is temporarily unavailable.
                return (
                    {
                        "success":
                            True,

                        "mode":
                            "generate_report",

                        "report_success":
                            False,

                        "model":
                            GEMINI_MODEL,

                        "reporting_period":
                            reporting_period,

                        "store_count":
                            len(
                                pre_scored_stores
                            ),

                        "report_html":
                            None,

                        "report_error": {
                            "error":
                                type(
                                    gemini_exc
                                ).__name__,

                            "message":
                                str(
                                    gemini_exc
                                )
                        },

                        "usage": {
                            "input_tokens":
                                0,

                            "output_tokens":
                                0,

                            "thinking_tokens":
                                0,

                            "total_tokens":
                                0,

                            "estimated_cost_usd":
                                0
                        }
                    },
                    200
                )

        mapping_rows = (
            payload.get(
                "store_mapping",
                []
            )
        )

        oa_rows = (
            payload.get(
                "operational_audit",
                []
            )
        )

        settlement_rows = (
            payload.get(
                "settlement",
                []
            )
        )

        food_cost_rows = (
            payload.get(
                "food_cost",
                []
            )
        )

        print(
            "Input counts:",
            {
                "mapping":
                    len(mapping_rows)
                    if isinstance(
                        mapping_rows,
                        list
                    )
                    else 0,

                "oa":
                    len(oa_rows)
                    if isinstance(
                        oa_rows,
                        list
                    )
                    else 0,

                "settlement":
                    len(settlement_rows)
                    if isinstance(
                        settlement_rows,
                        list
                    )
                    else 0,

                "food_cost":
                    len(food_cost_rows)
                    if isinstance(
                        food_cost_rows,
                        list
                    )
                    else 0
            }
        )

        mapping = (
            normalize_store_mapping(
                mapping_rows
            )
        )

        oa = normalize_oa(
            oa_rows,
            mapping
        )

        settlement = (
            normalize_settlement(
                settlement_rows,
                mapping
            )
        )

        food_cost = (
            normalize_food_cost(
                food_cost_rows,
                mapping
            )
        )

        master = create_store_master(
            oa,
            settlement,
            food_cost,
            mapping
        )

        master = calculate_metrics(
            master
        )

        ranked = score_stores(
            master
        )

        scored_output = [
            compact_store(r)
            for r in ranked
        ]

        print(
            "Stores scored:",
            len(scored_output)
        )

        # ========================================================
        # SCORE-ONLY MODE
        # ========================================================

        if mode == "score_only":
            return (
                {
                    "success": True,
                    "mode": "score_only",
                    "reporting_period":
                        reporting_period,
                    "store_count":
                        len(scored_output),
                    "scored_stores":
                        scored_output
                },
                200
            )

        # ========================================================
        # REPORT MODE
        # ========================================================

        prompt = build_prompt(
            reporting_period,
            ranked
        )

        print(
            "Stores scored:",
            len(ranked)
        )

        print(
            "Prompt characters:",
            len(prompt)
        )

        response = call_gemini(
            prompt
        )

        usage = getattr(
            response,
            "usage_metadata",
            None
        )

        input_tokens = (
            getattr(
                usage,
                "prompt_token_count",
                0
            )
            or 0
        )

        output_tokens = (
            getattr(
                usage,
                "candidates_token_count",
                0
            )
            or 0
        )

        thinking_tokens = (
            getattr(
                usage,
                "thoughts_token_count",
                0
            )
            or 0
        )

        total_tokens = (
            getattr(
                usage,
                "total_token_count",
                0
            )
            or 0
        )

        # Same working cost convention used in the
        # Store Deep Dive service.
        estimated_cost_usd = (
            input_tokens
            / 1_000_000
            * 0.75
            +
            (
                output_tokens
                +
                thinking_tokens
            )
            / 1_000_000
            * 3.75
        )

        return (
            {
                "success": True,
                "mode":
                    "generate_report",
                "report_success":
                    True,
                "model":
                    GEMINI_MODEL,

                "reporting_period":
                    reporting_period,

                "store_count":
                    len(scored_output),

                "scored_stores":
                    scored_output,

                "report_html":
                    response.text,

                "usage": {
                    "input_tokens":
                        input_tokens,

                    "output_tokens":
                        output_tokens,

                    "thinking_tokens":
                        thinking_tokens,

                    "total_tokens":
                        total_tokens,

                    "estimated_cost_usd":
                        estimated_cost_usd
                }
            },
            200
        )

    except Exception as exc:

        print(
            "Enterprise Risk Error:",
            repr(exc)
        )

        return (
            {
                "success": False,
                "error":
                    type(exc).__name__,
                "message":
                    str(exc)
            },
            500
        )
