#!/usr/bin/env python3
"""Generate executable CQ results and derived artifacts from seed datasets."""

from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Tuple


TRANSFER_MODES = {"Transferred", "Externalized"}

COST_TYPE_BUCKET = {
    "MicroarchitecturalPerformanceCost": "w_perf",
    "PhysicalResourceCost": "w_labor",
    "EngineeringLaborCost": "w_labor",
    "VerificationValidationCost": "w_labor",
    "ToolchainInfrastructureCost": "w_labor",
    "LifecycleOperationsCost": "w_ops",
    "ComplianceAssuranceCost": "w_compliance",
    "MarketContractualCost": "w_compliance",
    "LiabilityRedressCost": "w_compliance",
    "ReputationTrustCost": "w_compliance",
    "OpportunityCost": "w_opportunity",
}


def read_csv(path: Path) -> List[Dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, fieldnames: Iterable[str], rows: Iterable[Dict[str, object]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(fieldnames), lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def nonempty(row: Dict[str, str], keys: Iterable[str]) -> bool:
    return all(row.get(k, "").strip() != "" for k in keys)


def as_float(x: str, default: float = 0.0) -> float:
    try:
        return float(x.strip())
    except Exception:
        return default


def ranking_for_microperf(cost_rows: List[Dict[str, str]], multiplier: float) -> Tuple[List[str], Dict[str, float]]:
    grouped: Dict[str, List[float]] = defaultdict(list)
    for row in cost_rows:
        if row.get("cost_type") != "MicroarchitecturalPerformanceCost":
            continue
        unit = row.get("unit", "")
        if "percent_runtime" not in unit:
            continue
        value = as_float(row.get("magnitude", ""))
        if row.get("evidence_grade") in ("E2", "E3"):
            value *= multiplier
        family = row.get("mechanism_family", "").strip()
        if family:
            grouped[family].append(value)

    means = {fam: sum(vals) / len(vals) for fam, vals in grouped.items() if vals}
    ranking = [fam for fam, _ in sorted(means.items(), key=lambda kv: (kv[1], kv[0]))]
    return ranking, means


def generate_voi_rows(cost_rows: List[Dict[str, str]], incident_rows: List[Dict[str, str]]) -> List[Dict[str, object]]:
    incident_count_by_family: Dict[str, int] = defaultdict(int)
    for row in incident_rows:
        family = row.get("linked_family", "").strip()
        if family:
            incident_count_by_family[family] += 1

    by_cell: Dict[Tuple[str, str], Dict[str, int]] = {}
    for row in cost_rows:
        key = (row.get("mechanism_family", "").strip(), row.get("cost_type", "").strip())
        if not key[0] or not key[1]:
            continue
        if key not in by_cell:
            by_cell[key] = {"e2_rows": 0, "e3_rows": 0, "transfer_rows": 0}
        grade = row.get("evidence_grade")
        if grade == "E2":
            by_cell[key]["e2_rows"] += 1
        elif grade == "E3":
            by_cell[key]["e3_rows"] += 1
        if row.get("bearing_mode") in TRANSFER_MODES:
            by_cell[key]["transfer_rows"] += 1

    scored: List[Dict[str, object]] = []
    for (family, cost_type), counts in by_cell.items():
        incidents = incident_count_by_family.get(family, 0)
        score = (
            1.0 * counts["e2_rows"]
            + 2.0 * counts["e3_rows"]
            + 1.2 * counts["transfer_rows"]
            + 0.8 * incidents
        )
        scored.append(
            {
                "mechanism_family": family,
                "cost_type": cost_type,
                "e2_rows": counts["e2_rows"],
                "e3_rows": counts["e3_rows"],
                "transfer_externalized_rows": counts["transfer_rows"],
                "incident_link_rows": incidents,
                "voi_score": f"{score:.2f}",
            }
        )

    scored.sort(key=lambda r: (-float(r["voi_score"]), r["mechanism_family"], r["cost_type"]))
    for i, row in enumerate(scored, start=1):
        row["priority_rank"] = i
    return scored


def max_abs_by_cost_type(cost_rows: List[Dict[str, str]]) -> Dict[str, float]:
    by_type: Dict[str, float] = defaultdict(float)
    for row in cost_rows:
        cost_type = row.get("cost_type", "").strip()
        if not cost_type:
            continue
        value = abs(as_float(row.get("magnitude", "")))
        by_type[cost_type] = max(by_type[cost_type], value)
    for cost_type, value in list(by_type.items()):
        if value <= 0:
            by_type[cost_type] = 1.0
    return by_type


def normalized_incident_loss_by_family(incident_rows: List[Dict[str, str]]) -> Dict[str, float]:
    values_by_family: Dict[str, List[float]] = defaultdict(list)
    max_loss = 0.0
    for row in incident_rows:
        family = row.get("linked_family", "").strip()
        if not family:
            continue
        value = abs(as_float(row.get("loss_magnitude", "")))
        values_by_family[family].append(value)
        max_loss = max(max_loss, value)
    if max_loss <= 0:
        max_loss = 1.0

    return {
        family: (sum(vals) / len(vals)) / max_loss
        for family, vals in values_by_family.items()
        if vals
    }


def objective_row_term(
    row: Dict[str, str],
    objective: Dict[str, str],
    max_by_type: Dict[str, float],
) -> float:
    cost_type = row.get("cost_type", "").strip()
    magnitude = abs(as_float(row.get("magnitude", "")))
    denom = max_by_type.get(cost_type, 1.0)
    normalized = magnitude / denom if denom > 0 else 0.0

    bucket = COST_TYPE_BUCKET.get(cost_type)
    base_weight = as_float(objective.get(bucket, "0")) if bucket else 0.0
    transfer_weight = as_float(objective.get("w_transfer_externalized", "0"))
    mode = row.get("bearing_mode", "").strip()
    extra_weight = transfer_weight if mode in TRANSFER_MODES else 0.0
    return (base_weight + extra_weight) * normalized


def generate_objective_comparisons(
    cost_rows: List[Dict[str, str]],
    incident_rows: List[Dict[str, str]],
    objective_rows: List[Dict[str, str]],
) -> Tuple[List[Dict[str, object]], int, int]:
    grouped_by_family: Dict[str, List[Dict[str, str]]] = defaultdict(list)
    for row in cost_rows:
        family = row.get("mechanism_family", "").strip()
        if family:
            grouped_by_family[family].append(row)

    max_by_type = max_abs_by_cost_type(cost_rows)
    normalized_incident = normalized_incident_loss_by_family(incident_rows)

    required_weight_fields = [
        "w_perf",
        "w_labor",
        "w_ops",
        "w_compliance",
        "w_opportunity",
        "w_transfer_externalized",
        "w_incident_loss",
    ]

    objective_comparisons: List[Dict[str, object]] = []
    objective_total = len(objective_rows)
    objective_valid = 0

    for objective in objective_rows:
        objective_id = objective.get("objective_id", "").strip()
        objective_label = objective.get("objective_label", "").strip()
        baseline_family = objective.get("baseline_family", "").strip()

        if not objective_id or not objective_label or not baseline_family:
            continue
        if not nonempty(objective, required_weight_fields):
            continue

        family_scores: Dict[str, float] = {}
        for family, rows in grouped_by_family.items():
            if not rows:
                continue
            cost_component = sum(
                objective_row_term(row, objective, max_by_type) for row in rows
            ) / len(rows)
            incident_component = as_float(objective.get("w_incident_loss", "0")) * normalized_incident.get(family, 0.0)
            family_scores[family] = cost_component + incident_component

        if baseline_family not in family_scores or len(family_scores) < 2:
            continue

        objective_valid += 1
        baseline_score = family_scores[baseline_family]
        ordered = sorted(family_scores.items(), key=lambda kv: (kv[1], kv[0]))
        for rank, (family, score) in enumerate(ordered, start=1):
            objective_comparisons.append(
                {
                    "objective_id": objective_id,
                    "objective_label": objective_label,
                    "baseline_family": baseline_family,
                    "mechanism_family": family,
                    "objective_score": f"{score:.4f}",
                    "delta_vs_baseline": f"{(score - baseline_score):.4f}",
                    "rank": rank,
                }
            )

    return objective_comparisons, objective_valid, objective_total


def generate_shacl_equivalent_results(cost_rows: List[Dict[str, str]]) -> Tuple[List[Dict[str, object]], int, int, int]:
    internalized_violations = 0
    transfer_violations = 0
    inferred_pairs = set()

    for row in cost_rows:
        mechanism = row.get("mechanism_instance", "").strip()
        bearer = row.get("stakeholder", "").strip()
        if mechanism and bearer:
            inferred_pairs.add((mechanism, bearer))

        mode = row.get("bearing_mode", "").strip()
        decision_maker = row.get("decision_maker", "").strip()
        transfer_target = row.get("transfer_target", "").strip()

        if mode == "Internalized" and decision_maker and bearer and decision_maker != bearer:
            internalized_violations += 1

        if mode in TRANSFER_MODES and (not transfer_target or transfer_target == decision_maker):
            transfer_violations += 1

    rows = [
        {
            "rule_id": "CQ7-R1",
            "description": "Internalized costs require decision-maker == bearer.",
            "violations": internalized_violations,
            "checked_rows": len(cost_rows),
            "status": "pass" if internalized_violations == 0 else "fail",
            "validator": "shacl-equivalent",
        },
        {
            "rule_id": "CQ7-R2",
            "description": "Transferred/externalized costs require non-empty transfer target distinct from decision-maker.",
            "violations": transfer_violations,
            "checked_rows": len(cost_rows),
            "status": "pass" if transfer_violations == 0 else "fail",
            "validator": "shacl-equivalent",
        },
    ]
    return rows, internalized_violations, transfer_violations, len(inferred_pairs)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cost", type=Path, required=True)
    parser.add_argument("--incidents", type=Path, required=True)
    parser.add_argument("--objectives", type=Path, required=True)
    parser.add_argument("--shacl-shapes", type=Path, required=True)
    parser.add_argument("--cq-out", type=Path, required=True)
    parser.add_argument("--voi-out", type=Path, required=True)
    parser.add_argument("--sensitivity-out", type=Path, required=True)
    parser.add_argument("--objective-out", type=Path, required=True)
    parser.add_argument("--shacl-out", type=Path, required=True)
    args = parser.parse_args()

    if not args.shacl_shapes.exists():
        raise FileNotFoundError(f"Missing SHACL shapes file: {args.shacl_shapes}")

    cost_rows = read_csv(args.cost)
    incident_rows = read_csv(args.incidents)
    objective_rows = read_csv(args.objectives)

    required_cost_fields = [
        "stakeholder",
        "time_horizon",
        "magnitude",
        "unit",
        "evidence_grade",
        "data_origin",
        "source_key",
        "source_locator",
    ]
    cq1_ok = sum(1 for row in cost_rows if nonempty(row, required_cost_fields))
    cq1_total = len(cost_rows)
    cq1_pass = cq1_ok == cq1_total and cq1_total > 0

    cq2_modes_ok = sum(1 for row in cost_rows if row.get("bearing_mode", "").strip() != "")
    transfer_rows = [row for row in cost_rows if row.get("bearing_mode", "").strip() in TRANSFER_MODES]
    cq2_target_ok = sum(1 for row in transfer_rows if row.get("transfer_target", "").strip() != "")
    externalized = sum(1 for row in cost_rows if row.get("bearing_mode", "").strip() == "Externalized")
    cq2_pass = cq2_modes_ok == len(cost_rows) and cq2_target_ok == len(transfer_rows) and len(cost_rows) > 0

    objective_comparisons, cq3_valid, cq3_total = generate_objective_comparisons(
        cost_rows, incident_rows, objective_rows
    )
    write_csv(
        args.objective_out,
        [
            "objective_id",
            "objective_label",
            "baseline_family",
            "mechanism_family",
            "objective_score",
            "delta_vs_baseline",
            "rank",
        ],
        objective_comparisons,
    )
    cq3_pass = cq3_total > 0 and cq3_valid == cq3_total

    required_incident_fields = [
        "incident_label",
        "linked_family",
        "residual_risk_bearer",
        "loss_magnitude",
        "loss_unit",
        "attribution_confidence",
        "evidence_grade",
        "data_origin",
        "source_key",
        "source_locator",
        "attribution_evidence_type",
        "linkage_mechanism",
        "counterfactual_effect",
    ]
    cq4_ok = sum(1 for row in incident_rows if nonempty(row, required_incident_fields))
    cq4_total = len(incident_rows)
    cq4_pass = cq4_total > 0 and cq4_ok == cq4_total

    baseline_rank, baseline_means = ranking_for_microperf(cost_rows, 1.0)
    minus_rank, minus_means = ranking_for_microperf(cost_rows, 0.8)
    plus_rank, plus_means = ranking_for_microperf(cost_rows, 1.2)
    cq5_pass = bool(baseline_rank) and baseline_rank == minus_rank == plus_rank

    sensitivity_rows = []
    for scenario, means in (
        ("baseline", baseline_means),
        ("minus20_e2e3", minus_means),
        ("plus20_e2e3", plus_means),
    ):
        ordered = sorted(means.items(), key=lambda kv: (kv[1], kv[0]))
        for rank, (family, mean_val) in enumerate(ordered, start=1):
            sensitivity_rows.append(
                {
                    "scenario": scenario,
                    "rank": rank,
                    "mechanism_family": family,
                    "mean_microperf_percent_runtime": f"{mean_val:.4f}",
                }
            )
    write_csv(
        args.sensitivity_out,
        ["scenario", "rank", "mechanism_family", "mean_microperf_percent_runtime"],
        sensitivity_rows,
    )

    voi_rows = generate_voi_rows(cost_rows, incident_rows)
    write_csv(
        args.voi_out,
        [
            "priority_rank",
            "mechanism_family",
            "cost_type",
            "e2_rows",
            "e3_rows",
            "transfer_externalized_rows",
            "incident_link_rows",
            "voi_score",
        ],
        voi_rows,
    )
    cq6_pass = len(voi_rows) > 0

    shacl_rows, cq7_internalized_v, cq7_transfer_v, inferred_pairs = generate_shacl_equivalent_results(cost_rows)
    write_csv(
        args.shacl_out,
        ["rule_id", "description", "violations", "checked_rows", "status", "validator"],
        shacl_rows,
    )
    cq7_pass = cq7_internalized_v == 0 and cq7_transfer_v == 0 and inferred_pairs > 0

    opportunity_rows = [row for row in cost_rows if row.get("cost_type", "").strip() == "OpportunityCost"]
    cq8_required = [
        "foregone_alternative",
        "foregone_resource",
        "foregone_benefit",
        "design_constraint",
    ]
    cq8_ok = sum(1 for row in opportunity_rows if nonempty(row, cq8_required))
    cq8_total = len(opportunity_rows)
    cq8_pass = cq8_total > 0 and cq8_ok == cq8_total

    cq_rows = [
        {
            "cq_id": "CQ1",
            "status": "pass" if cq1_pass else "partial",
            "coverage_metric": (
                f"{cq1_ok}/{cq1_total} tuples include stakeholder + time + magnitude + unit + evidence + "
                "data_origin + source_key + source_locator"
            ),
            "notes": "Cost visibility by bearer, horizon, and row-level provenance is executable",
        },
        {
            "cq_id": "CQ2",
            "status": "pass" if cq2_pass else "partial",
            "coverage_metric": (
                f"{cq2_modes_ok}/{len(cost_rows)} tuples include bearing mode; "
                f"transfer-target coverage {cq2_target_ok}/{len(transfer_rows)}; Externalized={externalized}"
            ),
            "notes": "Internalized/transferred/externalized burden transfer is executable with explicit transfer targets",
        },
        {
            "cq_id": "CQ3",
            "status": "pass" if cq3_pass else "partial",
            "coverage_metric": (
                f"{cq3_valid}/{cq3_total} objectives include explicit weights + baseline and yield ranked deltas"
            ),
            "notes": "Comparative choice is anchored to objective-function weighting and baseline counterfactual",
        },
        {
            "cq_id": "CQ4",
            "status": "pass" if cq4_pass else "partial",
            "coverage_metric": (
                f"{cq4_ok}/{cq4_total} incident tuples include loss + confidence + provenance + "
                "attribution evidence + linkage mechanism + counterfactual effect + residual-risk bearer"
            ),
            "notes": "Incident linkage distinguishes attribution evidence, linkage pathway, and counterfactual claim",
        },
        {
            "cq_id": "CQ5",
            "status": "pass" if cq5_pass else "partial",
            "coverage_metric": (
                "Family ranking stable in both +/-20% E2/E3 perturbation directions"
                if cq5_pass
                else "Family ranking changes under +/-20% E2/E3 perturbation"
            ),
            "notes": "Sensitivity test is computed from seeded microperformance tuples",
        },
        {
            "cq_id": "CQ6",
            "status": "pass" if cq6_pass else "partial",
            "coverage_metric": f"VOI ranking computed for {len(voi_rows)} family/cost cells",
            "notes": "Information-gap prioritization is executable via uncertainty/transfer/incident scoring",
        },
        {
            "cq_id": "CQ7",
            "status": "pass" if cq7_pass else "partial",
            "coverage_metric": (
                f"Internalized contradictions={cq7_internalized_v}; "
                f"transfer-target contradictions={cq7_transfer_v}; "
                f"inferred mechanism-bearer pairs={inferred_pairs}"
            ),
            "notes": "SHACL-equivalent consistency checks validate burden semantics beyond field presence",
        },
        {
            "cq_id": "CQ8",
            "status": "pass" if cq8_pass else "partial",
            "coverage_metric": (
                f"{cq8_ok}/{cq8_total} OpportunityCost tuples include explicit alternative-use + "
                "foregone resource + benefit + design constraint"
            ),
            "notes": "Opportunity cost is represented as explicit AlternativeUse nodes under design constraints",
        },
    ]
    write_csv(args.cq_out, ["cq_id", "status", "coverage_metric", "notes"], cq_rows)


if __name__ == "__main__":
    main()
