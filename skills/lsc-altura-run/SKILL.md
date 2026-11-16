---
name: lsc-altura-run
description: Create or update a GUS Suite Execution for an LSC Journeys Altura automation run. Reads run statuses from GUS Automation Work Items (primary) or from an Altura jobs-sheet CSV (legacy). Creates ADM_Suite_Execution__c inside TS-1218138, creates one ADM_Test_Execution__c per T-number, and updates statuses + engineer comments from GUS WI data.
---

# LSC Altura Run Execution

Config lives in `config/suite.json` — do not hardcode IDs.
The Python script in `scripts/create_run_execution.py` does all GUS work.

## What this skill does

### Primary mode (`--from-gus`, default when no CSV attached)
1. Queries GUS Automation Work Items under the epic (`epicId` in config)
2. Parses T-number, PFG, SCN, Assignee, and WI status from each WI
3. Queries GUS for all `ADM_Related_Test_Scenario__c` records in the suite
4. Creates a new `ADM_Suite_Execution__c` inside TS-1218138 with Status = "In Progress"
5. Creates one `ADM_Test_Execution__c` per T-number, defaulting to "Not Executed"
6. Updates statuses and engineer comments from GUS WI data

### Legacy CSV mode (`--jobs-sheet <path>`)
Same as above but reads run statuses from an attached Altura jobs-sheet CSV instead of GUS WIs.

### Update-existing mode (`--update-existing`)
Updates all test executions across all existing suite executions for TS-1218138 from current GUS WI data. No new execution is created.

## Input

| Input | Required | Notes |
|-------|----------|-------|
| Run label | No | Default: `LSC Journeys Automation Run - <today>` |
| Altura jobs-sheet CSV | No (legacy) | Only needed for CSV mode; attach and save to `/tmp/LSC_Jobs_Sheet_run.csv` |

## Status mappings

### GUS WI → Test Execution (primary mode)

| GUS WI Status | Test Execution Status |
|---|---|
| Closed | Pass |
| In Progress | In Progress |
| New | Not Executed |

### Altura CSV → Test Execution (legacy mode)

| Altura status | GUS status |
|---|---|
| Passed | Pass |
| Failed | Fail |
| Trigger failed | Fail |
| Running | In Progress |
| Blocked: * | Blocked |
| (no data) | Not Executed |

## Steps

### 1. Receive input

When the user says "create an execution for today's run":
- If no CSV attached → use `--from-gus` (default)
- If CSV attached → save to `/tmp/LSC_Jobs_Sheet_run.csv` and use `--jobs-sheet`
- Ask for the run label if not obvious from context

### 2. Verify prereqs

```bash
sf org list --json | python3 -c "import json,sys; orgs=json.load(sys.stdin)['result']['nonScratchOrgs']; print('gus' if any(o.get('alias')=='gus' for o in orgs) else 'NOT_FOUND')"
```

If `gus` is not listed:
```bash
sf org login web --instance-url https://gus.my.salesforce.com --alias gus
```

### 3a. Run (GUS WI mode — default)

```bash
python3 -I skills/lsc-altura-run/scripts/create_run_execution.py \
  --from-gus \
  --run-label "LSC Journeys Automation Run - <date>" \
  --config skills/lsc-altura-run/config/suite.json
```

### 3b. Run (CSV mode — legacy)

```bash
python3 -I skills/lsc-altura-run/scripts/create_run_execution.py \
  --jobs-sheet /tmp/LSC_Jobs_Sheet_run.csv \
  --run-label "LSC Journeys Automation Run - <date>" \
  --config skills/lsc-altura-run/config/suite.json
```

### 3c. Update existing executions from GUS WIs

```bash
python3 -I skills/lsc-altura-run/scripts/create_run_execution.py \
  --update-existing \
  --config skills/lsc-altura-run/config/suite.json
```

### 4. Report results

- Suite Execution ID and name
- Total Test Executions created
- Status breakdown: Pass / In Progress / Not Executed counts
- Any failures (zero expected)

### 5. Dry-run (optional)

```bash
python3 -I skills/lsc-altura-run/scripts/create_run_execution.py \
  --from-gus \
  --run-label "..." \
  --config skills/lsc-altura-run/config/suite.json \
  --dry-run
```

## Error handling

| Error | Fix |
|-------|-----|
| `epicId not set in config` | Add `epicId` to `config/suite.json` |
| `No junction records found` | Suite has no T-numbers linked. Check `suiteId` in config. |
| `sf data query failed` with auth error | Run `sf org login web --alias gus --instance-url https://gus.my.salesforce.com` |
| `Composite create failed` | Check GUS API limits; retry |

## Config reference (`config/suite.json`)

```json
{
  "targetOrg":    "gus",
  "apiVersion":   "v64.0",
  "suiteId":      "a81EE000006DpZ7YAK",
  "suiteName":    "TS-1218138",
  "suiteLabel":   "Altura Automation Suite",
  "epicId":       "a3QEE000002nuKr2AI",
  "statusMap":    { "Passed": "Pass", "Failed": "Fail", "Trigger failed": "Fail", "Running": "In Progress" },
  "wiStatusMap":  { "Closed": "Pass", "In Progress": "In Progress", "New": "Not Executed" },
  "blockedPrefix":"Blocked",
  "defaultStatus":"Not Executed",
  "batchSize":    25
}
```
