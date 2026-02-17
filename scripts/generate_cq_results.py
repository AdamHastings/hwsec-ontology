#!/usr/bin/env python3
"""Generate executable CQ results and VOI priorities from seed artifacts."""

from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Tuple


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


def as_float(x: str) -> float:
    return float(x.strip())


def ranking_for_microperf(cost_rows: List[Dict[str, str]], multiplier: float) -> Tuple[List[str], Dict[str, float]]:
    grouped: Dict[str, List[float]] = defaultdict(list)
    for row in cost_rows:
        if row.get("cost_type") != "MicroarchitecturalPerformanceCost":
            continue
        unit = row.get("unit", "")
        if "percent_runtime" not in unit:
            continue
        try:
            value = as_float(row["magnitude"])
        except Exception:
            continue
        if row.get("evidence_grade") in ("E2", "E3"):
            value *= multiplier
        grouped[row["mechanism_family"]].append(value)

    means = {fam: sum(vals) / len(vals) for fam, vals in grouped.items() if vals}
    ranking = [fam for fam, _ in sorted(means.items(), key=lambda kv: (kv[1], kv[0]))]
    return ranking, means


def generate_voi_rows(
    cost_rows: List[Dict[str, str]], incident_rows: List[Dict[str, str]]
) -> List[Dict[str, object]]:
    incident_count_by_family: Dict[str, int] = defaultdict(int)
    for row in incident_rows:
        family = row.get("linked_family", "").strip()
        if family:
            incident_count_by_family[family] += 1

    by_cell: Dict[Tuple[str, str], Dict[str, int]] = {}
    for row in cost_rows:
        key = (row["mechanism_family"], row["cost_type"])
        if key not in by_cell:
            by_cell[key] = {"e2_rows": 0, "e3_rows": 0, "transfer_rows": 0}
        grade = row.get("evidence_grade")
        if grade == "E2":
            by_cell[key]["e2_rows"] += 1
        elif grade == "E3":
            by_cell[key]["e3_rows"] += 1
        if row.get("bearing_mode") in ("Transferred", "Externalized"):
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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cost", type=Path, required=True)
    parser.add_argument("--incidents", type=Path, required=True)
    parser.add_argument("--cq-out", type=Path, required=True)
    parser.add_argument("--voi-out", type=Path, required=True)
    parser.add_argument("--sensitivity-out", type=Path, required=True)
    args = parser.parse_args()

    cost_rows = read_csv(args.cost)
    incident_rows = read_csv(args.incidents)

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
    externalized = sum(1 for row in cost_rows if row.get("bearing_mode") == "Externalized")
    cq2_pass = cq2_modes_ok == len(cost_rows) and len(cost_rows) > 0

    families = sorted({row.get("mechanism_family", "") for row in cost_rows if row.get("mechanism_family", "")})
    comparable = 0
    for fam in families:
        rows = [r for r in cost_rows if r.get("mechanism_family") == fam]
        cost_types = {r.get("cost_type", "") for r in rows if r.get("cost_type", "")}
        stakeholders = {r.get("stakeholder", "") for r in rows if r.get("stakeholder", "")}
        time_horizons = {r.get("time_horizon", "") for r in rows if r.get("time_horizon", "")}
        if len(cost_types) >= 5 and len(stakeholders) >= 2 and "Upfront" in time_horizons and "Recurring" in time_horizons:
            comparable += 1
    cq3_pass = comparable == len(families) and len(families) > 0

    required_incident_fields = [
        "incident_label",
        "linked_family",
        "loss_magnitude",
        "loss_unit",
        "attribution_confidence",
        "evidence_grade",
        "data_origin",
        "source_key",
        "source_locator",
    ]
    cq4_ok = sum(1 for row in incident_rows if nonempty(row, required_incident_fields))
    cq4_total = len(incident_rows)
    cq4_pass = cq4_total > 0 and cq4_ok == cq4_total

    baseline_rank, baseline_means = ranking_for_microperf(cost_rows, 1.0)
    minus_rank, minus_means = ranking_for_microperf(cost_rows, 0.8)
    plus_rank, plus_means = ranking_for_microperf(cost_rows, 1.2)
    cq5_pass = baseline_rank and baseline_rank == minus_rank == plus_rank

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
            "coverage_metric": f"{cq2_modes_ok}/{len(cost_rows)} tuples include bearing mode; Externalized={externalized}",
            "notes": "Internalized/transferred/externalized burden transfer is executable",
        },
        {
            "cq_id": "CQ3",
            "status": "pass" if cq3_pass else "partial",
            "coverage_metric": (
                f"{comparable}/{len(families)} families meet comparability rule "
                "(>=5 cost types, >=2 stakeholders, upfront+recurring)"
            ),
            "notes": "Cross-family comparison is executable on shared burden dimensions",
        },
        {
            "cq_id": "CQ4",
            "status": "pass" if cq4_pass else "partial",
            "coverage_metric": f"{cq4_ok}/{cq4_total} incident tuples include family-linked loss + confidence + provenance",
            "notes": "Incident-loss linkage is now represented in artifact form",
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
    ]
    write_csv(args.cq_out, ["cq_id", "status", "coverage_metric", "notes"], cq_rows)


if __name__ == "__main__":
    main()
