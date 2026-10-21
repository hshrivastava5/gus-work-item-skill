---
name: gus-create-story
description: Create a categorized GUS User Story (ADM_Work__c) for any Industries cloud team — picks the team's product tag and current sprint from config, classifies the work into a Category/Metric taxonomy, prefixes the subject as [Category][Metric], assigns it to the current user, and writes a rich-text description. Adds a linked test suite for patch stories when the team requires it.
---

# Create GUS User Story

Config lives next to this file — read it first, never hardcode IDs:

- `config/teams.json` — `defaults` + per-team `productTag`, `sprintLike`, `patchRequiresSuite` (+ shared `foundInBuild`)
- `config/taxonomy.json` — shared Category -> Metric lists (same for all clouds), classification `hints`, `subjectPrefix` format
- `references/rich-text-and-test-suites.md` — HTML rules for `Details__c` and the composite POST shape for test suites
- Optional user overrides: `~/.config/gus-work-item-skill/user.json` (`defaultTeam`, `assigneeId`, `storyPoints`)

## Setup (first run)

1. Confirm `sf` CLI is authed to the GUS org: `sf org list` (alias from `defaults.targetOrg`). If missing, tell the user to run `! sf org login web --alias gus --instance-url https://gus.my.salesforce.com`.
2. Resolve the **assignee**: use `assigneeId` from `user.json` if set; else
   `sf org display --target-org gus --json` -> `result.username`, then
   `SELECT Id, Name FROM User WHERE Username = '<username>' LIMIT 1`. Offer to cache the Id in `user.json`.
3. Check `teams.json` for `TODO` values on the chosen team; if present, stop and show the README helper queries.

## Steps

1. **Pick the team.** Use `user.json.defaultTeam` if set and the prompt doesn't say otherwise;
   else ask (AskUserQuestion) with one option per key in `teams.json` (skip the `TODO_*` entry).
   Pre-select a recommendation from each team's `keywords`, but still confirm.
2. **Find the sprint** for that team:
   ```sql
   SELECT Id, Name FROM ADM_Sprint__c WHERE Name LIKE '<sprintLike>' AND Start_Date__c <= TODAY AND End_Date__c >= TODAY ORDER BY End_Date__c DESC LIMIT 1
   ```
   If none: same query with `End_Date__c <= TODAY` ordered `End_Date__c DESC`.
3. **Release (Scheduled Build) — always ask the user.** Suggest recent values first:
   ```sql
   SELECT Scheduled_Build__r.Name, COUNT(Id) c FROM ADM_Work__c WHERE Assignee__c = '<assignee>' AND Scheduled_Build__c != null AND CreatedDate = LAST_N_DAYS:60 GROUP BY Scheduled_Build__r.Name ORDER BY MAX(CreatedDate) DESC LIMIT 4
   ```
   Offer those as options (AskUserQuestion; "Other" lets them type e.g. `264`). Resolve the typed
   release: `SELECT Id, Name FROM ADM_Build__c WHERE Name = '<release>'`. If no exact match, list
   `Name LIKE '%<release>%'` candidates and ask again. Never guess a release.
4. **Get subject + description** from the prompt, else ask. If only a subject is given, derive the description.
   `Details__c` must be >= 20 characters. Strip any existing `[...]` prefix from the subject first.
5. **Classify** into Category + Metric using `taxonomy.json`.
   Use `hints`; fall back to `fallbackCategory`. Build the subject with `subjectPrefix`.
6. **Review gate.** Show team (product tag + sprint), release, Category, Metric, final subject, and assignee.
   Proceed only after the user confirms.
7. **Format the description as rich-text HTML** (see references file). Never a run-on paragraph.
8. **Create** `ADM_Work__c` via `sf api request rest "/services/data/v60.0/sobjects/ADM_Work__c" --method POST --body @body.json --target-org <targetOrg>` with:

   | Field | Value |
   |---|---|
   | `RecordTypeId` | `defaults.recordTypeId` |
   | `Product_Tag__c` | team `productTag` |
   | `Sprint__c` | sprint Id from step 2 |
   | `Assignee__c` | assignee from Setup |
   | `Scheduled_Build__c` | release Id from step 3 |
   | `Found_in_Build__c` | `defaults.foundInBuild` (`NA`) |
   | `Story_Points__c`, `Status__c`, `Type__c`, `Priority__c` | `defaults` (user overrides allowed) |
   | `Subject__c` | prefixed subject |
   | `Details__c` and `Details_and_Steps_to_Reproduce__c` | same HTML |

9. **Query back** the record to get its `W-XXXXXXXX` Name.
10. **Patches:** if Category = `Customer Advocacy` and Metric = `Patches` and the team has
   `patchRequiresSuite: true`, ask for **Quadrant** and **automation links**, then create the
   suite + scenarios + junctions per `references/rich-text-and-test-suites.md`.
11. **Report:** W-number, team/product tag, sprint, Category/Metric, assignee, and (for patches) suite + T-numbers.

## Notes

- Category/Metric only drive the subject prefix; there are no such fields on `ADM_Work__c`.
- Use the exact taxonomy strings so subjects stay groupable on the sprint wall.
- Never use `--values` for HTML; always `sf api request rest` with a JSON body file.
- Never hardcode an Id in this file. Add or change IDs in `config/`.
