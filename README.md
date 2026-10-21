# gus-work-item-skill

Claude Code skill that creates categorized GUS user stories for any Industries cloud:
team-aware product tag + sprint, `[Category][Metric]` subject, assignee = you, rich-text
description, and (for patches) a linked test suite. See `PLAN.md` for the design.

## Install

```bash
git clone <repo-url> ~/gus-work-item-skill && ~/gus-work-item-skill/install.sh
```

Then in Claude Code: `/gus-create-story <what you did>`. Requires `sf` CLI authed to GUS (alias `gus`).

## Add your cloud

Edit `skills/gus-create-story/config/teams.json`, copy the `TODO_YOUR_CLOUD` block, and fill in `productTag` and `sprintLike` (the release is asked per story):

```sql
-- product tag
SELECT Id, Name FROM ADM_Product_Tag__c WHERE Name LIKE '%<your team>%'
-- sprint pattern (check names)
SELECT Name FROM ADM_Sprint__c WHERE Name LIKE '%<your team>%' ORDER BY End_Date__c DESC LIMIT 5
```

Run with `sf data query --query "<soql>" --target-org gus`. Open a PR with your entry.

## Personal overrides

`~/.config/gus-work-item-skill/user.json`:

```json
{ "defaultTeam": "FSC", "assigneeId": "005...", "storyPoints": 1 }
```
