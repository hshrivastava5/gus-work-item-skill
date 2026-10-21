# Plan: make `gus-create-story` shareable across clouds

Source skill: `~/.claude/skills/gus-create-story/SKILL.md` (single file, 239 lines).

## What blocks sharing today

| Hardcoded in the skill | Problem for other teams | Fix |
|---|---|---|
| `Assignee__c = 005B00000073lemIAA` | Assigns everything to Prithviraj | Resolve current user at runtime (`sf org display` -> username -> query `User`) and cache |
| FSC / LSC product tags + sprint patterns | Other clouds absent | `config/teams.json` — one entry per team, add yours via PR |
| `Scheduled_Build__c`, `Found_in_Build__c` (copied from W-23902376) | Wrong build for other orgs/releases | Per-team in `teams.json`; fall back to a `referenceWorkItem` clone |
| Q3Q4 taxonomy (Quality Engineering metrics) | Other clouds categorize differently | Move to `config/taxonomy.json`; teams can override per team |
| Classification hints ("Playwright -> Automation") | QE-specific | Keep as `hints` in the taxonomy file |
| Test-suite/patch rules | Patch->suite rule is a QE convention | Gate behind `teams.<id>.patchRequiresSuite` |
| "Always ask FSC or LSC" | Hardcoded 2-option question | Build the question from `teams.json` keys; skip if the user has a `defaultTeam` |
| Story points 1, P2, status New | Reasonable defaults | Keep as `defaults`, overridable per team/user |

## Target design

```
gus-work-item-skill/
  .claude-plugin/plugin.json
  skills/gus-create-story/
    SKILL.md                    # workflow only, no IDs
    config/teams.json           # per-cloud product tag, sprint pattern, builds, taxonomy ref
    config/taxonomy.json        # Category -> Metric lists + classification hints
    references/rich-text-and-test-suites.md   # HTML rules + composite POST shape
  install.sh                    # symlink into ~/.claude/skills
  PLAN.md  README.md
```

User-level overrides live in `~/.config/gus-work-item-skill/user.json`
(`defaultTeam`, `assigneeId`, `storyPoints`) so nothing personal is committed.

## Rollout steps

1. **Scaffold repo** (done in this directory).
2. **Extract constants** from the skill into `teams.json` / `taxonomy.json` (done for FSC + LSC).
3. **Runtime assignee lookup** — implemented in SKILL.md step 1.
4. **Verify IDs per cloud**: each cloud owner fills in their `productTag`, `sprintLike`,
   `scheduledBuild`, `foundInBuild` (SOQL helpers are in README). Placeholders are marked `TODO`.
5. **Dry-run** with 2-3 people from different clouds; collect taxonomy gaps.
6. **Publish**: push to git-emu (internal GitHub), share the install one-liner.
7. **Switch yourself over**: replace `~/.claude/skills/gus-create-story` with a symlink
   (`./install.sh`). Original is left untouched until you run that.

## Open questions

- Do other clouds use the same Q3Q4 `[Category][Metric]` subject prefix? If not, `taxonomy.json` needs a `prefixFormat` switch (stubbed as `subjectPrefix`).
- Is a shared "reference work item" per cloud acceptable for Scheduled/Found-in build, or should we query the current release?
- Where to host (git-emu org/repo name)?
