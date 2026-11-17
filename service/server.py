import sys, os, json, re, subprocess
from datetime import datetime, timezone
from typing import Optional
from pathlib import Path

# Allow override via env; default to ~/altura-sdk
_SDK = os.environ.get('ALTURA_SDK_PATH', str(Path.home() / 'altura-sdk'))
sys.path.insert(0, f'{_SDK}/src')

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

app = FastAPI(title="Altura-GUS Results Mapping")

ALTURA_CA  = os.environ.get('ALTURA_CA_CERT', str(Path.home() / '.altura' / 'combined-ca.pem'))
GUS_ORG    = 'gus'
API_VER    = 'v64.0'
SUITE_ID        = 'a81EE000006DpZ7YAK'
SUITE_NAME      = 'TS-1218138'
EPIC_ID         = 'a3QEE000002nuKr2AI'
AUTOMATION_TAG  = 'a1aEE000002nrYHYAY'  # Industries_LSC_Q3Q4_AlturaAutomation (fallback)

# PFG name → GUS Product Tag ID (read from ADM_Test_Scenario__c.Comments__c "PFG: <name>")
_PFG_TAG_MAP: dict = {
    'Account Management':    'a1aB00000001DepIAE',
    'Application Experience':'a1aEE000002pHdZYAU',
    'Consent Management':    'a1aB0000000MQyfIAG',
    'Engagement Execution':  'a1aEE000002pHX7YAM',
    'Engagement Planning':   'a1aEE000002pHSHYA2',
    'Events Management':     'a1aEE000002pHYjYAM',
    'Intelligent Conent':    'a1aEE000002pHTtYAM',  # typo matches GUS data
    'Key Account Management':'a1aEE000002pHVVYA2',
    'Medical Engagement':    'a1aEE000002pHaLYAU',
    'Order Management':      'a1aB0000000CGoUIAW',
    'Sample Management':     'a1aEE000002pHbxYAE',
    'Territory Management':  'a1aEE000000UcvtYAC',
}

# Cache of T-number → PFG (populated once per process from GUS)
_t_to_pfg_cache: dict = {}
_t_to_wi_cache: dict  = {}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_client():
    from api.client import AlturaApiClient
    return AlturaApiClient(verify_ssl=ALTURA_CA)


# Known product tag names that are NOT PFGs — block them explicitly
_BLOCKED_PFG_TOKENS = {
    'Industries-LSC-Q3', 'Industries-LS-Q3', 'Industries-FSC-Q3Q4',
    'Industries-FSC-Q3', 'Industries-LSC-Q4', 'Life Sciences Cloud',
    'Life Sciences', 'Industries', 'LSC', 'FSC',
}

# Canonical PFG names — validated against ADM_Test_Scenario__c.Comments__c "PFG: <name>"
_KNOWN_PFGS = {
    'Account Management', 'Admin Console', 'Application Experience',
    'Consent Management', 'Data, Analytics, Integration & Ecosystem',
    'Engagement Execution', 'Event Management', 'Events Management',
    'Engagement Planning', 'Intelligent Content', 'Intelligent Conent',  # typo in GUS data
    'Key Account Management', 'Medical Engagement', 'Order Management',
    'Product Management', 'Regulated Content Management for Life Sciences',
    'Sample Management', 'Territory Management', 'AI Enablement & Agentforce',
}

def pfg_from_test_name(test_name: str) -> str:
    """Extract PFG from Altura snapshot.test_name.

    Formats seen:
      'LSC-AccountManagement-264'          -> 'Account Management'
      'LSC Engagement Execution LSC-TJ-...'-> 'Engagement Execution'
      'LSC Events Management LSC-TJ-...'  -> 'Events Management'
      'LSC Sample Management ...'         -> 'Sample Management'
    """
    if not test_name:
        return ''

    # Format 1: 'LSC-AccountManagement-NNN' or 'LSC-AccountManagement-Web'
    m = re.match(r'^LSC-([A-Z][A-Za-z]+(?:[A-Z][A-Za-z]+)*)-', test_name)
    if m:
        # Split camelCase → words: 'AccountManagement' → 'Account Management'
        raw = m.group(1)
        words = re.sub(r'([A-Z])', r' \1', raw).strip()
        if words in _KNOWN_PFGS:
            return words

    # Format 2: 'LSC <PFG Name> LSC-TJ-...' or 'LSC <PFG Name> T-...'
    m2 = re.match(r'^LSC\s+(.+?)\s+(?:LSC-TJ|T-\d)', test_name)
    if m2:
        candidate = m2.group(1).strip()
        if candidate in _KNOWN_PFGS:
            return candidate

    # Format 3: free text — scan for any known PFG substring
    for pfg in sorted(_KNOWN_PFGS, key=len, reverse=True):
        if pfg.lower() in test_name.lower():
            return pfg

    return ''


def parse_run_id(text: str) -> str:
    text = text.strip()
    m = re.search(r'/runs/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})', text)
    if m:
        return m.group(1)
    if re.match(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', text):
        return text
    raise ValueError(f'Cannot parse a valid run UUID from: {text!r}')


def fetch_run_analysis(run_id: str) -> dict:
    c = get_client()

    raw = c.get(f'/api/v2/executor/run-detail/{run_id}')
    if hasattr(raw, 'json'): raw = raw.json()
    top = raw.get('data', raw)
    if isinstance(top, dict) and 'data' in top: top = top['data']

    job_id          = top.get('job_id', '')
    overall_verdict = (top.get('verdict') or top.get('results_state') or '').lower()
    n_passed        = top.get('n_passed', 0)
    n_total         = top.get('n_total', 0)
    scheduled_at    = top.get('scheduled_fire_time', '') or top.get('completed_at', '')
    completed_at    = top.get('completed_at', '')

    snap        = top.get('snapshot', {}) or {}
    branch      = snap.get('branch', '')
    test_name   = snap.get('test_name', '') or ''   # e.g. "LSC-AccountManagement-264"

    stages   = top.get('stages', [])
    top_rr   = top.get('run_results', [])

    all_scn_results = []
    if stages:
        for si, stage in enumerate(stages):
            for rr in stage.get('run_results', []):
                rr['_stage'] = si
                all_scn_results.append(rr)
    elif top_rr:
        for rr in top_rr:
            rr['_stage'] = 0
            all_scn_results.append(rr)
    else:
        stage_id = top.get('current_stage_run_id')
        if stage_id:
            raw2 = c.get(f'/api/v2/executor/run-detail/{stage_id}')
            if hasattr(raw2, 'json'): raw2 = raw2.json()
            sd = raw2.get('data', raw2)
            if isinstance(sd, dict) and 'data' in sd: sd = sd['data']
            for rr in sd.get('run_results', []):
                rr['_stage'] = 0
                all_scn_results.append(rr)

    verdict_counts = {'passed': 0, 'failed': 0, 'blocked': 0, 'not_run': 0}
    scn_rows = []

    for rr in all_scn_results:
        scn    = rr.get('ref_id', '')
        verdict = (rr.get('verdict') or rr.get('status') or '').lower()
        n_p    = rr.get('n_passed', 0)
        n_t    = rr.get('n_total', 0)
        stage  = rr.get('_stage', 0)

        summary   = rr.get('detail', {}).get('result_summary', {}) or {}
        failures  = summary.get('failures', []) or []
        outcomes  = summary.get('outcomes', {}) or {}
        alts_out  = outcomes.get('alts', {}) or {}

        alt_details = []
        for f in failures:
            alt_details.append({
                'alt_id':     f.get('alt_id', ''),
                'alt_name':   f.get('alt_name', ''),
                'journey_id': f.get('journey_id', ''),
                'message':    f.get('message', ''),
                'reason_code':f.get('reason_code', ''),
            })

        if verdict == 'passed':
            display = 'Passed';   verdict_counts['passed'] += 1
        elif verdict in ('failed', 'error', 'cancelled'):
            display = 'Failed';   verdict_counts['failed'] += 1
        elif verdict == 'blocked':
            display = 'Blocked';  verdict_counts['blocked'] += 1
        elif alts_out.get('not_run', 0) > 0 and alts_out.get('passed', 0) == 0 and alts_out.get('failed', 0) == 0:
            display = 'Did Not Run'; verdict_counts['not_run'] += 1
        else:
            display = verdict.capitalize() if verdict else 'Unknown'

        primary_msg = failures[0].get('message', '')    if failures else ''
        primary_rc  = failures[0].get('reason_code', '') if failures else ''

        scn_rows.append({
            'stage':          stage,
            'scn':            scn,
            'verdict':        display,
            'n_passed':       n_p,
            'n_total':        n_t,
            'alt_total':      alts_out.get('total', 0),
            'alt_passed':     alts_out.get('passed', 0),
            'alt_failed':     alts_out.get('failed', 0),
            'alt_skipped':    alts_out.get('skipped', 0),
            'primary_failure': primary_msg,
            'reason_code':    primary_rc,
            'alt_details':    alt_details,
            'started_at':     rr.get('started_at', ''),
            'completed_at':   rr.get('completed_at', ''),
        })

    # Resolve PFG: snapshot.test_name is primary (authoritative from Altura),
    # GUS WI Subject parsing is fallback. Never allow product-tag names through.
    pfg_name = pfg_from_test_name(test_name)
    if not pfg_name or pfg_name in _BLOCKED_PFG_TOKENS:
        all_scn_ids = [r['scn'] for r in scn_rows if r.get('scn')]
        pfg_name = resolve_pfg_from_scns(all_scn_ids)
    # Final guard: if still a blocked token, clear it
    if pfg_name in _BLOCKED_PFG_TOKENS:
        pfg_name = ''

    # Identify SCNs with no T-number mapping (can't be submitted to GUS)
    scn_to_t: dict = {}
    try:
        with open('/tmp/triggered_daily.json') as f:
            _jobs = json.load(f)
        for _job in _jobs:
            for _scn in (_job.get('scns') or []):
                scn_to_t[_scn] = _job['t_num']
    except Exception:
        pass
    no_t_scns = [r['scn'] for r in scn_rows if r.get('scn') and not scn_to_t.get(r['scn'])]

    return {
        'run_id':          run_id,
        'job_id':          job_id,
        'overall_verdict': overall_verdict,
        'n_passed':        n_passed,
        'n_total':         n_total,
        'pfg_name':        pfg_name,
        'branch':          branch,
        'scheduled_at':    scheduled_at,
        'completed_at':    completed_at,
        'scn_rows':        scn_rows,
        'verdict_counts':  verdict_counts,
        'total_scns':      len(scn_rows),
        'no_t_scns':       no_t_scns,
    }


def load_gus_pfg_map():
    """Query GUS epic WIs and build T-number → PFG map from Subject__c.
    Subject format: 'Account Management_LSC-TJ-AM-015_T-16553004_SCN-25710_...'
    """
    global _t_to_pfg_cache, _t_to_wi_cache
    if _t_to_pfg_cache:
        return _t_to_pfg_cache

    soql = (
        f"SELECT Name, Subject__c FROM ADM_Work__c "
        f"WHERE Epic__c = '{EPIC_ID}'"
    )
    records = sf_query(soql)
    for rec in records:
        subj = rec.get('Subject__c', '')
        wi   = rec.get('Name', '')
        # Extract T-number from subject
        m_t = re.search(r'(T-\d{7,9})', subj)
        if not m_t:
            continue
        t_num = m_t.group(1)
        # PFG is the first segment before '_' or first word-boundary chunk
        # Subject format: "Account Management_LSC-TJ-..." or "Account Management LSC-TJ-..."
        pfg_match = re.match(r'^([A-Za-z][A-Za-z ]+?)(?:_| LSC-)', subj)
        pfg = pfg_match.group(1).strip() if pfg_match else ''
        # Only accept known PFG names — block product tag names and other noise
        if pfg and t_num and pfg not in _BLOCKED_PFG_TOKENS and pfg in _KNOWN_PFGS:
            _t_to_pfg_cache[t_num] = pfg
            _t_to_wi_cache[t_num]  = wi

    return _t_to_pfg_cache


def resolve_pfg_from_scns(scn_list: list) -> str:
    """Given a list of SCN IDs, look up their T-numbers and return the dominant PFG."""
    pfg_map = load_gus_pfg_map()

    # Load SCN → T-number from triggered_daily.json
    scn_to_t: dict = {}
    try:
        with open('/tmp/triggered_daily.json') as f:
            jobs = json.load(f)
        for job in jobs:
            for scn in (job.get('scns') or []):
                scn_to_t[scn] = job['t_num']
    except Exception:
        pass

    from collections import Counter
    pfg_counts: Counter = Counter()
    for scn in scn_list:
        t = scn_to_t.get(scn)
        if t:
            pfg = pfg_map.get(t, '')
            if pfg:
                pfg_counts[pfg] += 1

    return pfg_counts.most_common(1)[0][0] if pfg_counts else ''


def sf_query(soql):
    r = subprocess.run(
        ['sf', 'data', 'query', '--target-org', GUS_ORG, '--query', soql, '--json'],
        capture_output=True, text=True
    )
    out = json.loads(r.stdout)
    return out.get('result', {}).get('records', [])


def composite_post(requests_list):
    BATCH = 25
    ok = fail = 0
    for i in range(0, len(requests_list), BATCH):
        payload = {'allOrNone': False, 'compositeRequest': requests_list[i:i+BATCH]}
        r = subprocess.run(
            ['sf', 'api', 'request', 'rest', '--target-org', GUS_ORG,
             '--method', 'POST', f'/services/data/{API_VER}/composite',
             '--body', json.dumps(payload)],
            capture_output=True, text=True
        )
        result = json.loads(r.stdout)
        # sf CLI may return the array directly or wrap it in compositeResponse
        if isinstance(result, list):
            sub = result
        elif isinstance(result, dict):
            sub = result.get('compositeResponse', [])
        else:
            sub = []
        for s in sub:
            if isinstance(s, dict) and 200 <= s.get('httpStatusCode', 0) < 300:
                ok += 1
            else:
                fail += 1
    return ok, fail


def create_gus_execution(run_id, job_id, pfg_name, scheduled_at, scn_rows, custom_name=None):
    # Reject blocked/non-PFG tokens that may have slipped through
    pfg = pfg_name if (pfg_name and pfg_name not in _BLOCKED_PFG_TOKENS) else 'LSC'
    if scheduled_at:
        try:
            dt = datetime.fromisoformat(scheduled_at.replace('Z', '+00:00'))
            dt_str = dt.strftime('%Y-%m-%d %H:%M')
        except Exception:
            dt_str = scheduled_at[:16]
    else:
        dt_str = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')

    exec_name = custom_name or f'{SUITE_NAME}_{pfg}_{dt_str}'

    # 1. Create Suite Execution (Name is auto-number; store label in Comments__c)
    r = subprocess.run(
        ['sf', 'api', 'request', 'rest', '--target-org', GUS_ORG,
         '--method', 'POST', f'/services/data/{API_VER}/sobjects/ADM_Suite_Execution__c',
         '--body', json.dumps({
             'Status__c': 'In Progress',
             'Test_Suite__c': SUITE_ID,
             'Comments__c': exec_name,
         })],
        capture_output=True, text=True
    )
    raw_result = json.loads(r.stdout)
    # sf CLI wraps success in {'result': {'id': ...}} or returns {'id': ...} directly
    if isinstance(raw_result, dict):
        suite_exec_id = raw_result.get('id') or (raw_result.get('result') or {}).get('id', '')
    else:
        suite_exec_id = ''
    if not suite_exec_id:
        raise Exception(f'Suite execution create failed: {r.stdout[:400]}')

    # 2. Build SCN → T-number map and determine which T-numbers belong to this PFG
    scn_to_t: dict = {}
    pfg_t_numbers: set = set()
    try:
        with open('/tmp/triggered_daily.json') as f:
            _jobs = json.load(f)
        for _job in _jobs:
            for _scn in (_job.get('scns') or []):
                scn_to_t[_scn] = _job['t_num']
    except Exception:
        pass

    # T-numbers seen in this Altura run (via scn_rows)
    run_t_numbers: set = set()
    for row in scn_rows:
        t = scn_to_t.get(row['scn'], '')
        if t:
            run_t_numbers.add(t)

    # Load full PFG map and collect all T-numbers for this PFG from GUS epic WIs
    pfg_map = load_gus_pfg_map()
    for t_num, t_pfg in pfg_map.items():
        if t_pfg == pfg:
            pfg_t_numbers.add(t_num)

    # Union: T-numbers in the run OR in this PFG's WIs (cover the full PFG scope)
    relevant_t_numbers = pfg_t_numbers | run_t_numbers

    # 3. Query only the junction records for T-numbers in this PFG
    # Junction Test_Scenario__r.Name holds the T-number (e.g. "T-16489031")
    # Comments__c holds "PFG: <name> | SCNs: ..." — read it to assign the correct product tag
    jrn_records = sf_query(
        f"SELECT Id, Test_Scenario__c, Test_Scenario__r.Name, "
        f"Test_Scenario__r.Product_Tag__c, Test_Scenario__r.Comments__c "
        f"FROM ADM_Related_Test_Scenario__c "
        f"WHERE Test_Suite__c = '{SUITE_ID}'"
    )

    t_to_junction = {}
    # scn_id → correct PFG product tag ID (derived from Comments__c)
    scenario_tag_updates: dict = {}
    for rec in jrn_records:
        try:
            jrn_id = rec.get('Id') or rec.get('id', '')
            ts = rec.get('Test_Scenario__r') or {}
            t_name = ts.get('Name') if isinstance(ts, dict) else rec.get('Test_Scenario__r.Name', '')
            # Only include T-numbers that belong to this PFG
            if t_name and jrn_id and t_name in relevant_t_numbers:
                t_to_junction[t_name] = jrn_id
                # Determine correct product tag from "PFG: <name>" in Comments__c
                scn_id = rec.get('Test_Scenario__c', '')
                comments = (ts.get('Comments__c') if isinstance(ts, dict) else '') or ''
                current_tag = (ts.get('Product_Tag__c') if isinstance(ts, dict) else '') or ''
                pfg_match = re.match(r'PFG:\s*(.+?)(?:\s*\||\s*$)', comments)
                pfg_label = pfg_match.group(1).strip() if pfg_match else ''
                correct_tag = _PFG_TAG_MAP.get(pfg_label, AUTOMATION_TAG)
                if scn_id and current_tag != correct_tag:
                    scenario_tag_updates[scn_id] = correct_tag
        except (KeyError, TypeError):
            pass

    # Ensure product tags on test scenario records match their PFG (GUS UI reads this column)
    if scenario_tag_updates:
        fix_records = [
            {'attributes': {'type': 'ADM_Test_Scenario__c'}, 'Id': sid, 'Product_Tag__c': tag_id}
            for sid, tag_id in scenario_tag_updates.items()
        ]
        PATCH_BATCH = 25
        for i in range(0, len(fix_records), PATCH_BATCH):
            batch = fix_records[i:i+PATCH_BATCH]
            subprocess.run(
                ['sf', 'api', 'request', 'rest', '--target-org', GUS_ORG,
                 '--method', 'PATCH', f'/services/data/{API_VER}/composite/sobjects',
                 '--body', json.dumps({'allOrNone': False, 'records': batch})],
                capture_output=True, text=True
            )

    # Build T-number → best Altura verdict from scn_rows
    # Multiple SCNs may map to the same T-number; prefer failed > passed > other
    VERDICT_RANK = {'Failed': 3, 'Blocked': 2, 'Passed': 1, 'Did Not Run': 0}
    t_to_best_row: dict = {}
    for row in scn_rows:
        t = scn_to_t.get(row['scn'], '')
        if not t:
            continue
        existing = t_to_best_row.get(t)
        if existing is None or VERDICT_RANK.get(row['verdict'], 0) > VERDICT_RANK.get(existing['verdict'], 0):
            t_to_best_row[t] = row

    STATUS_MAP = {
        'Passed':     'Pass',
        'Failed':     'Fail',
        'Blocked':    'Blocked',
        'Did Not Run':'Not Executed',
    }

    # 4. Build composite POST for test executions — only PFG-scoped T-numbers
    te_requests = []
    matched = unmatched = 0
    for t_name, jrn_id in t_to_junction.items():
        row = t_to_best_row.get(t_name)
        if row:
            status = STATUS_MAP.get(row['verdict'], 'Not Executed')
            comment = f"Altura: {row['verdict']}"
            if row.get('primary_failure'):
                comment += f" | {row['primary_failure'][:200]}"
            if row.get('reason_code'):
                comment += f" [{row['reason_code']}]"
            matched += 1
        else:
            # T-number is in this PFG but wasn't in the Altura run
            status  = 'Not Executed'
            comment = 'Not in this Altura run'
            unmatched += 1

        te_requests.append({
            'method': 'POST',
            'url': f'/services/data/{API_VER}/sobjects/ADM_Test_Execution__c',
            'referenceId': f'te{len(te_requests)}',
            'body': {
                'Suite_Execution__c':     suite_exec_id,
                'Related_Test_Scenario__c': jrn_id,
                'Status__c':              status,
                'Comments__c':            comment,
            }
        })

    ok, fail = composite_post(te_requests)

    # 5. Mark suite execution Completed
    subprocess.run(
        ['sf', 'data', 'update', 'record', '--target-org', GUS_ORG,
         '--sobject', 'ADM_Suite_Execution__c',
         '--record-id', suite_exec_id,
         '--values', "Status__c='Completed'",
         '--json'],
        capture_output=True, text=True
    )

    return {
        'suite_exec_id':   suite_exec_id,
        'exec_name':       exec_name,
        'te_created_ok':   ok,
        'te_failed':       fail,
        'scn_matched':     matched,
        'scn_unmatched':   unmatched,
        'pfg_scope_count': len(relevant_t_numbers),
        'gus_url': f'https://gus.lightning.force.com/lightning/r/ADM_Suite_Execution__c/{suite_exec_id}/view',
    }


# ---------------------------------------------------------------------------
# API routes
# ---------------------------------------------------------------------------

class AnalyzeRequest(BaseModel):
    input: str

class SubmitGUSRequest(BaseModel):
    run_id:       str
    job_id:       str
    pfg_name:     str
    scheduled_at: str
    scn_rows:     list
    custom_name:  Optional[str] = None


@app.get("/")
async def index():
    return FileResponse("static/index.html")


@app.post("/api/analyze")
async def analyze(req: AnalyzeRequest):
    try:
        run_id = parse_run_id(req.input)
        result = fetch_run_analysis(run_id)
        return JSONResponse(result)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/submit-gus")
async def submit_gus(req: SubmitGUSRequest):
    try:
        result = create_gus_execution(
            req.run_id, req.job_id, req.pfg_name,
            req.scheduled_at, req.scn_rows, req.custom_name
        )
        return JSONResponse(result)
    except Exception as e:
        import traceback
        raise HTTPException(status_code=500, detail=f"{str(e)}\n{traceback.format_exc()[-600:]}")


@app.get("/api/debug-junctions")
async def debug_junctions():
    """Return raw junction records to diagnose field shape."""
    recs = sf_query(
        f"SELECT Id, Test_Scenario__r.Name "
        f"FROM ADM_Related_Test_Scenario__c "
        f"WHERE Test_Suite__c = '{SUITE_ID}' LIMIT 3"
    )
    return JSONResponse({'count': len(recs), 'sample': recs[:3]})


app.mount("/static", StaticFiles(directory="static"), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8765, reload=False)
