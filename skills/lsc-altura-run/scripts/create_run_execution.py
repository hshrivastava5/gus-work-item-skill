#!/usr/bin/env python3 -I
"""
LSC Altura Run Execution Creator
=================================
Usage (from Altura jobs CSV — creates new execution):
    python3 -I create_run_execution.py --jobs-sheet <path/to/LSC_Jobs_Sheet.csv> \\
                                        [--run-label "LSC Journeys Automation Run - Nov 2 2026"] \\
                                        [--config <path/to/suite.json>]

Usage (from GUS Work Items — creates new execution):
    python3 -I create_run_execution.py --from-gus \\
                                        [--run-label "LSC Journeys Automation Run - Nov 2 2026"] \\
                                        [--config <path/to/suite.json>]

Usage (update all existing suite executions from GUS WIs):
    python3 -I create_run_execution.py --update-existing \\
                                        [--config <path/to/suite.json>]

Usage (update a SPECIFIC existing suite execution from a CSV):
    python3 -I create_run_execution.py --update-execution <suite-exec-id> \\
                                        --jobs-sheet <path/to/LSC_Jobs_Sheet.csv> \\
                                        [--config <path/to/suite.json>]

Creates a new ADM_Suite_Execution__c inside the configured GUS test suite,
creates one ADM_Test_Execution__c per scenario linked to the suite, then
updates statuses either from the provided Altura jobs sheet CSV or directly
from GUS Automation Work Items under the configured epic.
"""

import argparse
import csv
import json
import os
import re
import subprocess
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

# ── Config ─────────────────────────────────────────────────────────────────────

SCRIPT_DIR  = Path(__file__).resolve().parent
DEFAULT_CFG = SCRIPT_DIR.parent / "config" / "suite.json"

def load_config(path: Path) -> dict:
    with open(path) as f:
        return json.load(f)

# ── Salesforce helpers ──────────────────────────────────────────────────────────

def sf_post(path: str, body: dict, cfg: dict) -> dict:
    tmp = "/tmp/_sf_post_body.json"
    with open(tmp, "w") as f:
        json.dump(body, f)
    r = subprocess.run(
        ["sf", "api", "request", "rest",
         "--target-org", cfg["targetOrg"],
         "--method", "POST",
         "--body", f"@{tmp}",
         f"/services/data/{cfg['apiVersion']}/{path}"],
        capture_output=True, text=True
    )
    if r.returncode != 0:
        raise RuntimeError(f"sf POST {path} failed:\n{r.stderr[:400]}")
    return json.loads(r.stdout)

def sf_query(soql: str, cfg: dict) -> list:
    r = subprocess.run(
        ["sf", "data", "query",
         "--target-org", cfg["targetOrg"],
         "--query", soql,
         "--json"],
        capture_output=True, text=True
    )
    if r.returncode != 0:
        raise RuntimeError(f"SOQL query failed:\n{r.stderr[:400]}")
    data = json.loads(r.stdout)
    return data.get("result", {}).get("records", [])

def composite_batch(subrequests: list, cfg: dict, label: str = "") -> list:
    tmp = "/tmp/_sf_composite.json"
    payload = {"compositeRequest": subrequests, "allOrNone": False}
    with open(tmp, "w") as f:
        json.dump(payload, f)
    r = subprocess.run(
        ["sf", "api", "request", "rest",
         "--target-org", cfg["targetOrg"],
         "--method", "POST",
         "--body", f"@{tmp}",
         f"/services/data/{cfg['apiVersion']}/composite"],
        capture_output=True, text=True
    )
    if r.returncode != 0:
        raise RuntimeError(f"Composite {label} failed:\n{r.stderr[:400]}")
    data = json.loads(r.stdout)
    return data.get("compositeResponse", [])

# ── Data fetchers ───────────────────────────────────────────────────────────────

def fetch_junction_records(cfg: dict) -> list:
    """Return all ADM_Related_Test_Scenario__c records for the suite."""
    print("  Querying GUS for junction records (ADM_Related_Test_Scenario__c)...")
    records = sf_query(
        f"SELECT Id, Test_Scenario__c, Test_Scenario__r.Name "
        f"FROM ADM_Related_Test_Scenario__c "
        f"WHERE Test_Suite__c = '{cfg['suiteId']}' "
        f"ORDER BY Order__c ASC NULLS LAST",
        cfg
    )
    print(f"  Found {len(records)} junction records")
    return records

def fetch_scenario_metadata(t_names: list, cfg: dict) -> dict:
    """Fetch Priority__c and Comments__c from ADM_Test_Scenario__c for the given T-numbers.

    Comments__c was populated earlier with 'PFG: <pfg> | SCNs: <scn1,...>'
    Returns {T-name: {pfg, priority, scns: []}}
    """
    print("  Querying GUS for scenario metadata (PFG / Priority / SCNs)...")
    result = {}
    chunk_size = 100
    for i in range(0, len(t_names), chunk_size):
        chunk = t_names[i:i + chunk_size]
        in_clause = ", ".join(f"'{n}'" for n in chunk)
        records = sf_query(
            f"SELECT Name, Priority__c, Comments__c "
            f"FROM ADM_Test_Scenario__c "
            f"WHERE Name IN ({in_clause})",
            cfg
        )
        for rec in records:
            name = rec["Name"]
            comments = rec.get("Comments__c") or ""
            pfg, scns = _parse_comments(comments)
            result[name] = {
                "pfg": pfg,
                "priority": rec.get("Priority__c") or "",
                "scns": scns,
            }
    print(f"  Retrieved metadata for {len(result)} T-numbers")
    return result

def _parse_comments(comments: str):
    """Extract pfg and scns from 'PFG: X | SCNs: Y, Z' comment format."""
    pfg = ""
    scns = []
    pfg_m = re.search(r"PFG:\s*([^|]+)", comments)
    if pfg_m:
        pfg = pfg_m.group(1).strip()
    scn_m = re.search(r"SCNs:\s*([^|]+)", comments)
    if scn_m:
        raw = scn_m.group(1).strip()
        scns = [s.strip() for s in raw.split(",") if s.strip()]
    return pfg, scns

def fetch_gus_wi_data(cfg: dict) -> dict:
    """Fetch Automation WI data from GUS for the configured epic.

    Queries ADM_Work__c WHERE Subject LIKE '%_Automation' and parses
    T-number, PFG, SCN, assignee, and status from each record.

    Returns {T-name: {status, pfg, scns, assignee}}
    """
    epic_id = cfg.get("epicId")
    if not epic_id:
        raise RuntimeError("epicId not set in config — required for --from-gus mode")

    print("  Querying GUS for Automation Work Items...")
    records = sf_query(
        f"SELECT Subject__c, Status__c, Assignee__r.Name "
        f"FROM ADM_Work__c "
        f"WHERE Epic__c = '{epic_id}' AND Subject__c LIKE '%\\_Automation'",
        cfg
    )
    print(f"  Found {len(records)} Automation WIs")

    result = {}
    for rec in records:
        subject = rec["Subject__c"]
        # Parse T-number (first match wins; some subjects have two T-numbers)
        t_match = re.search(r'(T-\d+)', subject)
        if not t_match:
            continue
        t_name = t_match.group(1)

        # PFG = everything before first _LSC-TJ-
        pfg_match = re.match(r'^(.+?)_LSC-TJ-', subject)
        pfg = pfg_match.group(1).strip() if pfg_match else ""

        # SCN(s) = segment between T-number part and _Automation suffix
        scn_match = re.search(r'T-\d[\w\- ]*_(.+?)_Automation$', subject)
        scn_raw = scn_match.group(1).strip() if scn_match else ""
        scns = [s.strip() for s in re.split(r'[\s,]+', scn_raw)
                if s.strip().startswith('SCN-')]

        assignee = (rec.get("Assignee__r") or {}).get("Name", "")

        result[t_name] = {
            "status":   rec["Status__c"],
            "pfg":      pfg,
            "scns":     scns,
            "assignee": assignee,
        }

    print(f"  Parsed WI data for {len(result)} unique T-numbers")
    return result

# ── CSV parsing ─────────────────────────────────────────────────────────────────

def parse_jobs_sheet(csv_path: str) -> dict:
    """Parse LSC_Jobs_Sheet.csv → {T-number: {status, detail, scns, job}}"""
    result = {}
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            raw_t = row.get("Taleggio Id", "").strip()
            t_nums = re.findall(r"T-\d+", raw_t)
            if not t_nums:
                continue
            status  = row.get("Last run status", "").strip()
            detail  = row.get("Last run detail", "").strip()
            job     = row.get("Altura Job / Suite Id", "").strip()
            scn_raw = row.get("Altura SCN Id", "").strip()
            scns    = [s.strip() for s in scn_raw.split(",") if s.strip()]
            for t in t_nums:
                result[t] = {
                    "status": status,
                    "detail": detail,
                    "job":    job,
                    "scns":   scns,
                }
    return result

# ── GUS status mapping ──────────────────────────────────────────────────────────

def map_status(raw: str, cfg: dict) -> str:
    """Map Altura jobs-sheet status to GUS test execution status."""
    if cfg["blockedPrefix"] in raw:
        return "Blocked"
    return cfg["statusMap"].get(raw, cfg["defaultStatus"])

def map_wi_status(gus_wi_status: str, cfg: dict) -> str:
    """Map GUS Work Item status to test execution status."""
    return cfg.get("wiStatusMap", {}).get(gus_wi_status, cfg["defaultStatus"])

# ── Core workflow ───────────────────────────────────────────────────────────────

def create_suite_execution(label: str, cfg: dict) -> str:
    print(f"\nCreating Suite Execution: '{label}'...")
    res = sf_post(
        "sobjects/ADM_Suite_Execution__c",
        {
            "Test_Suite__c": cfg["suiteId"],
            "Status__c":     "In Progress",
            "Comments__c":   label,
        },
        cfg
    )
    if not res.get("success"):
        raise RuntimeError(f"Suite execution creation failed: {res}")
    exec_id = res["id"]
    print(f"  Created: {exec_id}")
    return exec_id

def create_test_executions(junction_recs: list, scenario_meta: dict,
                           suite_exec_id: str, cfg: dict) -> dict:
    """Create one ADM_Test_Execution__c per junction record.
    Returns {T-name: exec_record_id}
    """
    print(f"\nCreating {len(junction_recs)} Test Execution records...")
    t_to_exec = {}
    total_ok = 0
    total_fail = 0
    bs = cfg["batchSize"]

    for i in range(0, len(junction_recs), bs):
        batch = junction_recs[i:i + bs]
        subreqs = []
        for j, rec in enumerate(batch):
            t_name = rec["Test_Scenario__r"]["Name"]
            meta   = scenario_meta.get(t_name, {})
            pfg    = meta.get("pfg", "")
            scns   = meta.get("scns", [])
            scn_str = ", ".join(scns)
            parts  = []
            if pfg:
                parts.append(f"PFG: {pfg}")
            if scn_str:
                parts.append(f"SCNs: {scn_str}")
            comment = " | ".join(parts)
            subreqs.append({
                "method":      "POST",
                "url":         f"/services/data/{cfg['apiVersion']}/sobjects/ADM_Test_Execution__c",
                "referenceId": f"exec_{i+j}",
                "body": {
                    "Related_Test_Scenario__c": rec["Id"],
                    "Suite_Execution__c":       suite_exec_id,
                    "Status__c":               cfg["defaultStatus"],
                    "Comments__c":             comment,
                },
            })

        responses = composite_batch(subreqs, cfg, f"create-{i//bs+1}")
        for k, resp in enumerate(responses):
            if resp.get("httpStatusCode") in [200, 201]:
                exec_id = resp["body"]["id"]
                t_name  = batch[k]["Test_Scenario__r"]["Name"]
                t_to_exec[t_name] = exec_id
                total_ok += 1
            else:
                total_fail += 1
                print(f"  [create fail] ref={resp.get('referenceId')}: "
                      f"{resp.get('body', '')!r:.120}")

        bnum = i // bs + 1
        btot = (len(junction_recs) + bs - 1) // bs
        print(f"  Batch {bnum}/{btot}: {sum(1 for r in responses if r.get('httpStatusCode') in [200,201])} ok")

    print(f"  Total: {total_ok} created, {total_fail} failed")
    return t_to_exec

def update_statuses_from_csv(t_to_exec: dict, run_data: dict, scenario_meta: dict,
                              cfg: dict) -> tuple:
    """Update test executions from Altura jobs-sheet CSV data. Returns (ok, fail)."""
    updates = []
    for t_name, info in run_data.items():
        exec_id = t_to_exec.get(t_name)
        if not exec_id:
            print(f"  [warn] No exec record for {t_name} — skipping")
            continue
        gus_status = map_status(info["status"], cfg)
        meta   = scenario_meta.get(t_name, {})
        pfg    = meta.get("pfg", "")
        scns   = info["scns"] or meta.get("scns", [])
        scn_str = ", ".join(scns)
        job    = info.get("job", "")
        detail = info.get("detail", "")
        parts  = []
        if pfg:
            parts.append(f"PFG: {pfg}")
        if scn_str:
            parts.append(f"SCNs: {scn_str}")
        if job:
            parts.append(f"Job: {job}")
        if detail:
            parts.append(f"Detail: {detail[:120]}")
        comment = " | ".join(parts)
        updates.append({"id": exec_id, "status": gus_status, "comment": comment})

    return _apply_updates(updates, cfg)

def update_statuses_from_gus(t_to_exec: dict, wi_data: dict, scenario_meta: dict,
                              cfg: dict) -> tuple:
    """Update test executions from GUS Work Item data. Returns (ok, fail)."""
    updates = []
    for t_name, exec_id in t_to_exec.items():
        wi = wi_data.get(t_name)
        if not wi:
            continue  # no WI for this T-number — leave as Not Executed
        gus_status = map_wi_status(wi["status"], cfg)
        meta     = scenario_meta.get(t_name, {})
        pfg      = wi.get("pfg") or meta.get("pfg", "")
        scns     = wi.get("scns") or meta.get("scns", [])
        scn_str  = ", ".join(scns)
        assignee = wi.get("assignee", "")
        parts = []
        if pfg:
            parts.append(f"PFG: {pfg}")
        if scn_str:
            parts.append(f"SCNs: {scn_str}")
        if assignee:
            parts.append(f"Engineer: {assignee}")
        comment = " | ".join(parts)
        updates.append({"id": exec_id, "status": gus_status, "comment": comment})

    return _apply_updates(updates, cfg)

def _apply_updates(updates: list, cfg: dict) -> tuple:
    if not updates:
        print("  No updates needed.")
        return 0, 0

    print(f"\nUpdating {len(updates)} Test Executions...")
    ok, fail = 0, 0
    bs = cfg["batchSize"]

    for i in range(0, len(updates), bs):
        batch = updates[i:i + bs]
        subreqs = []
        for j, upd in enumerate(batch):
            subreqs.append({
                "method":      "PATCH",
                "url":         f"/services/data/{cfg['apiVersion']}/sobjects/ADM_Test_Execution__c/{upd['id']}",
                "referenceId": f"upd_{i+j}",
                "body": {
                    "Status__c":   upd["status"],
                    "Comments__c": upd["comment"],
                },
            })
        responses = composite_batch(subreqs, cfg, f"update-{i//bs+1}")
        b_ok = sum(1 for r in responses if r.get("httpStatusCode") in [200, 201, 204])
        b_fail = len(responses) - b_ok
        ok   += b_ok
        fail += b_fail
        bnum  = i // bs + 1
        btot  = (len(updates) + bs - 1) // bs
        print(f"  Batch {bnum}/{btot}: {b_ok}/{len(responses)} ok")
        for r in responses:
            if r.get("httpStatusCode") not in [200, 201, 204]:
                print(f"    [update fail] {r}")

    return ok, fail

# ── Update-specific-execution mode ─────────────────────────────────────────────

def update_execution_from_csv(suite_exec_id: str, csv_path: str, cfg: dict):
    """Update test executions in a specific suite execution from a CSV file."""
    print(f"\nUpdating suite execution {suite_exec_id} from CSV: {csv_path}")

    # Fetch test executions for this specific suite execution
    test_execs = sf_query(
        f"SELECT Id, Related_Test_Scenario__c, Related_Test_Scenario__r.Test_Scenario__r.Name "
        f"FROM ADM_Test_Execution__c "
        f"WHERE Suite_Execution__c = '{suite_exec_id}'",
        cfg
    )
    print(f"  {len(test_execs)} test executions found")

    # Build t_name → exec_id map
    t_to_exec = {}
    for te in test_execs:
        try:
            t_name = te["Related_Test_Scenario__r"]["Test_Scenario__r"]["Name"]
            t_to_exec[t_name] = te["Id"]
        except (KeyError, TypeError):
            pass

    print(f"  {len(t_to_exec)} T-numbers mapped")

    # Fetch scenario metadata for comments
    t_names = list(t_to_exec.keys())
    scenario_meta = fetch_scenario_metadata(t_names, cfg)

    # Parse CSV
    run_data = parse_jobs_sheet(csv_path)
    print(f"  {len(run_data)} T-numbers in CSV")

    from collections import Counter
    status_counts = Counter(map_status(v["status"], cfg) for v in run_data.values())
    matched = sum(1 for t in run_data if t in t_to_exec)
    print(f"  Matched to test executions: {matched}/{len(run_data)}")
    for s, n in sorted(status_counts.items()):
        print(f"    {s}: {n}")

    # Apply updates
    ok, fail = update_statuses_from_csv(t_to_exec, run_data, scenario_meta, cfg)

    print(f"\n{'='*50}")
    print(f"EXECUTION UPDATED: {suite_exec_id}")
    print(f"  CSV rows processed: {len(run_data)}")
    print(f"  Status updates: {ok} ok, {fail} failed")
    print(f"  Not Executed (no CSV data): {len(t_to_exec) - matched}")
    print(f"  Status breakdown:")
    for s, n in sorted(status_counts.items()):
        print(f"    {s}: {n}")
    print(f"{'='*50}")


# ── Update-existing mode ────────────────────────────────────────────────────────

def update_existing_executions(cfg: dict):
    """Update all test executions in every suite execution for this suite from GUS WIs."""
    print(f"\nFetching all suite executions for {cfg['suiteName']}...")
    suite_execs = sf_query(
        f"SELECT Id, Name, Comments__c FROM ADM_Suite_Execution__c "
        f"WHERE Test_Suite__c = '{cfg['suiteId']}' ORDER BY CreatedDate ASC",
        cfg
    )
    print(f"  Found {len(suite_execs)} suite executions")

    # Fetch GUS WI data once
    wi_data = fetch_gus_wi_data(cfg)

    # Fetch scenario metadata once
    junction_recs = fetch_junction_records(cfg)
    t_names = [r["Test_Scenario__r"]["Name"] for r in junction_recs]
    scenario_meta = fetch_scenario_metadata(t_names, cfg)

    total_updated = 0
    total_failed = 0

    for se in suite_execs:
        se_id   = se["Id"]
        se_name = se["Name"]
        label   = se.get("Comments__c") or se_name
        print(f"\n── {se_name}: {label}")

        # Fetch test executions for this suite execution
        test_execs = sf_query(
            f"SELECT Id, Related_Test_Scenario__c, Related_Test_Scenario__r.Test_Scenario__r.Name "
            f"FROM ADM_Test_Execution__c "
            f"WHERE Suite_Execution__c = '{se_id}'",
            cfg
        )
        print(f"  {len(test_execs)} test executions found")

        # Build t_name → exec_id map
        t_to_exec = {}
        for te in test_execs:
            try:
                t_name = (te["Related_Test_Scenario__r"]
                            ["Test_Scenario__r"]["Name"])
                t_to_exec[t_name] = te["Id"]
            except (KeyError, TypeError):
                pass

        ok, fail = update_statuses_from_gus(t_to_exec, wi_data, scenario_meta, cfg)
        print(f"  Updated: {ok}  Failed: {fail}")
        total_updated += ok
        total_failed  += fail

    print(f"\n{'='*50}")
    print(f"ALL EXECUTIONS UPDATED")
    print(f"Total updated: {total_updated}  Total failed: {total_failed}")
    print(f"{'='*50}")

# ── Main ────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--jobs-sheet",
                       help="Path to LSC_Jobs_Sheet.csv from the Altura run (creates new execution)")
    group.add_argument("--from-gus", action="store_true",
                       help="Read run statuses from GUS Automation WIs (no CSV needed)")
    group.add_argument("--update-existing", action="store_true",
                       help="Update all test executions in existing suite executions from GUS WIs")
    # --update-execution is NOT in the mutually exclusive group so --csv can accompany it
    parser.add_argument("--update-execution", metavar="SUITE_EXEC_ID",
                        help="Update a specific existing suite execution from a CSV (use with --csv)")
    parser.add_argument("--csv",
                        help="CSV path for --update-execution mode")
    parser.add_argument("--run-label",
                        default=f"LSC Journeys Automation Run - {datetime.now().strftime('%b %-d %Y')}",
                        help="Label for the suite execution (default: today's date)")
    parser.add_argument("--config", default=str(DEFAULT_CFG),
                        help="Path to suite.json config (default: ../config/suite.json)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Parse and validate inputs without writing to GUS")
    args = parser.parse_args()

    cfg = load_config(Path(args.config))
    print(f"Suite: {cfg['suiteName']} ({cfg['suiteId']})")

    # ── Update-existing mode ──────────────────────────────────────────────────
    if args.update_existing:
        update_existing_executions(cfg)
        return

    # ── Update-specific-execution mode ───────────────────────────────────────
    if args.update_execution:
        csv_path = args.csv or args.jobs_sheet
        if not csv_path:
            print("ERROR: --update-execution requires --csv <path> (or --jobs-sheet <path>)")
            sys.exit(1)
        update_execution_from_csv(args.update_execution, csv_path, cfg)
        return

    print(f"Run label: {args.run_label}")

    # ── Determine data source ─────────────────────────────────────────────────
    if args.from_gus or not args.jobs_sheet:
        print(f"\nData source: GUS Work Items (epic: {cfg.get('epicId')})")
        wi_data = fetch_gus_wi_data(cfg)
        status_counts = Counter(
            map_wi_status(v["status"], cfg) for v in wi_data.values()
        )
        for s, n in sorted(status_counts.items()):
            print(f"    {s}: {n}")
        run_data = None
    else:
        print(f"\nData source: jobs sheet ({args.jobs_sheet})")
        run_data = parse_jobs_sheet(args.jobs_sheet)
        wi_data  = None
        print(f"  {len(run_data)} T-numbers with run data")
        status_counts = Counter(map_status(v["status"], cfg) for v in run_data.values())
        for s, n in sorted(status_counts.items()):
            print(f"    {s}: {n}")

    # ── Fetch junction records and scenario metadata ───────────────────────────
    junction_recs = fetch_junction_records(cfg)
    if not junction_recs:
        print("ERROR: No junction records found for the suite. Is the suite populated?")
        sys.exit(1)

    t_names = [r["Test_Scenario__r"]["Name"] for r in junction_recs]
    scenario_meta = fetch_scenario_metadata(t_names, cfg)

    if args.dry_run:
        print("\n[DRY RUN] All inputs validated. Would create:")
        print(f"  1 Suite Execution: '{args.run_label}'")
        print(f"  {len(junction_recs)} Test Executions")
        src_count = len(wi_data) if wi_data else len(run_data)
        print(f"  Update {src_count} with statuses")
        return

    # ── Create suite execution ─────────────────────────────────────────────────
    suite_exec_id = create_suite_execution(args.run_label, cfg)

    # ── Create test executions ─────────────────────────────────────────────────
    t_to_exec = create_test_executions(junction_recs, scenario_meta, suite_exec_id, cfg)

    # ── Update statuses ────────────────────────────────────────────────────────
    if wi_data is not None:
        upd_ok, upd_fail = update_statuses_from_gus(t_to_exec, wi_data, scenario_meta, cfg)
        src_count = len(wi_data)
    else:
        upd_ok, upd_fail = update_statuses_from_csv(t_to_exec, run_data, scenario_meta, cfg)
        src_count = len(run_data)

    # ── Summary ────────────────────────────────────────────────────────────────
    print("\n" + "="*50)
    print("DONE")
    print(f"Suite Execution:      {suite_exec_id}")
    print(f"Test Executions:      {len(t_to_exec)}/{len(junction_recs)} created")
    print(f"Statuses updated:     {upd_ok}/{src_count} (fail: {upd_fail})")
    print(f"\nStatus breakdown:")
    for s, n in sorted(status_counts.items()):
        print(f"  {s}: {n}")
    print(f"  Not Executed: {len(junction_recs) - src_count}")
    print("="*50)

if __name__ == "__main__":
    main()
