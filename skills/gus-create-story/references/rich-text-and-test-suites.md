# Rich-text descriptions, test suites and patches

Reference for SKILL.md. Team-specific IDs come from `config/teams.json`.

## Description Formatting (rich text — read this)

**Format the description carefully — it is not readable as a run-on blob.**
`Details__c` and `Details_and_Steps_to_Reproduce__c` are **rich-text** fields
(`type=textarea`, `htmlFormatted=true`, `extraTypeInfo=richtextarea`). A long
single paragraph renders as an unreadable wall of text. Author the description
as clean **HTML**:

- Short intro `<p>` sentence, then labelled sections with `<p><b>Heading</b></p>`.
- Use `<ul>/<li>` (or `<ol>/<li>` for ordered notes) for anything that is a list
  — scope items, per-event behavior, review findings, etc. One idea per `<li>`.
- Bold the key token in each bullet (`<b>eventName</b>`, field names) so the list
  scans quickly.
- Make PRs / sheets / tickets clickable with `<a href="...">label</a>` (Salesforce
  adds `target="_blank"` itself).
- Allowed tags are the Lightning rich-text allowlist: `p, b, strong, i, u, br,
  ul, ol, li, a`. Avoid `<code>`, `<h1>`–`<h6>`, `<table>` — they get stripped.
  Use HTML entities (`&mdash;`, `&amp;`) rather than raw `—`/`&`.

**Writing HTML — use the REST API, not `--values`.** The `sf data create/update
record --values` key=value parser mangles multi-tag HTML (splits on spaces,
chokes on quotes). Write the HTML into a JSON body file and PATCH/POST it via the
CLI's own managed auth:

```bash
# body.json: {"Details__c": "<p>…</p>", "Details_and_Steps_to_Reproduce__c": "<p>…</p>"}
sf api request rest "/services/data/v60.0/sobjects/ADM_Work__c/<recId>" \
  --method PATCH --body @body.json --target-org gus
```

(Do **not** hand-build a bearer token from `sf org display` — the gus session
token is rejected as `INVALID_AUTH_HEADER`; `sf api request rest` uses the
CLI's own auth and works.) For a brand-new record you can still `sf data create
record` with a minimal plaintext `Details__c` to satisfy the ≥20-char rule, then
immediately PATCH the rich-text HTML in — or POST the full record via
`sf api request rest` in one call.

## Test Suites & Test Scenarios (mandatory shape — read this)

When creating a GUS **test suite** with **test scenarios** (test cases) — always for a
PATCH (see next section), and whenever the user asks to add test cases/scenarios —
build them in **one atomic Salesforce Composite POST**
(`/services/data/v60.0/composite`, `allOrNone=true`) and follow the mandated field
shape below. Composite allows **≤25 subrequests**; if suite + cases + junctions +
work-link exceeds 25, split into two calls (call 1: suite + scenarios; call 2:
junctions + work-link, using the real ids returned by call 1).

### Object model (4 objects)

- **`ADM_Test_Suite__c`** — the suite. `Suite_Name__c` (descriptive scope), `Source__c='CMD'`.
- **`ADM_Test_Scenario__c`** — one test case each (fields below).
- **`ADM_Related_Test_Scenario__c`** — junction scenario↔suite: `Test_Suite__c` + `Test_Scenario__c`.
- **`ADM_Related_Test_Suite__c`** — junction suite↔work: `Work__c=<W- record id>` + `Test_Suite__c`.

### Mandatory fields on every `ADM_Test_Scenario__c`

| Field | Mandate |
|-------|---------|
| `Test_Name__c` | **Required.** Concise action title of the test case. |
| `Quadrant__c` | **Ask the user.** Picklist: `Q1`, `Q2`, `Q3`, `Q4 Performance`, `Q4 Security`. (Q2 = functional/business-facing is the usual default — but always ask.) |
| `Execution_Type__c` | `Automated` when automation links exist, else `Manual`. Picklist: `Manual`, `Automated`, `Cannot be automated`. |
| `Priority__c` | `P2` |
| `Source__c` | `CMD` |
| `Product_Tag__c` | Same as the work item's team `productTag` from `config/teams.json` |
| `Test_Automation__c` | **When Automated:** the automation link(s) — e.g. `<PR URL> (<spec path>)`. Leave unset for Manual. |
| `Unique_Automation_Name__c` | **When Automated:** the unique automation id — the spec file path (e.g. `test-scripts/Altura/financial-services-cloud/SCN-20760/ALT-80110408-test.spec.ts`). |
| `Prerequisite__c` | **Required rich-text HTML** — a `<ul>` of preconditions. |
| `Test_Details__c` | **Required rich-text HTML** — an `<ol>` of steps. For Automated cases, the **last `<li>` names the spec file + PR** (e.g. `Automated by <b>…spec.ts</b> (PR #NNNN).`). |
| `Expected_Results__c` | **Required rich-text HTML** — a `<ul>` of assertions/observable outcomes. |

The three rich-text blocks follow the same HTML authoring rules as *Description
Formatting* (allowlist `p, b, strong, i, u, br, ul, ol, li, a`; entities for `—`/`&`;
never `--values` — POST via Composite / `sf api request rest`). Bold the key token
(field/event/object name) in each bullet.

### Deriving scenarios from an automation PR

When the user points at a PR for the test scenarios: pull the PR (`git-emu` MCP
`pull_request_read` → `get` + `get_files`), map **one test case per meaningful test
scenario** in the PR (exclude pure setup/prereq steps unless asked), set
`Execution_Type__c='Automated'`, `Test_Automation__c=<PR URL> (<spec>)`, and
`Unique_Automation_Name__c=<spec path>` per case. **Confirm the PR actually matches the
story's feature** before attaching — if it's a different feature, flag it and ask.

Create with:
`sf api request rest "/services/data/v60.0/composite" --method POST --body @composite.json --target-org gus`
(managed-auth REST path — never `--values`). To **delete/replace** a suite, remove
children first (junctions + work-link), then scenarios, then the suite, via
`sf data delete record` (the REST collections `DELETE` needs a body mode; `sf data
delete record` uses managed auth and just works).

## Patches — always add a linked test suite

**When the work is a PATCH** (Category `Customer Advocacy` → Metric `Patches`, i.e. a
patch-verification story or one covering PATCH work items), **always** also create a
linked test suite following the mandated shape above. Never leave a patch story without
a test suite. Ask the user the **Quadrant** and any **automation links** first, then
POST the suite + `ADM_Test_Scenario__c` case(s) (one per patch WI / feature / PR
scenario) + `ADM_Related_Test_Scenario__c` junctions + one `ADM_Related_Test_Suite__c`
work-link.

