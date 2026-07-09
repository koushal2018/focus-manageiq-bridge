---
name: gotcha
description: Append a new entry to GOTCHAS.md in the house format with a collision-free ID. Use whenever a non-obvious finding needs capturing (data mapping surprise, API contract quirk, join failure, residency friction, misleading label, etc.) — the per-turn reminder points here. Pass a one-line summary of the finding as the argument.
---

# Capture a gotcha

GOTCHAS.md is the PoC's primary deliverable — 100+ entries with structured
IDs, read by the ENBD team during the EBA sprint. This skill keeps new
entries collision-free and format-consistent.

## 1. Pick the family, allocate the ID

Run the helper (from the project root):

```bash
bash .claude/skills/gotcha/scripts/next_id.sh          # list families + current max
bash .claude/skills/gotcha/scripts/next_id.sh FIN      # -> next free ID, e.g. FIN-6
```

Family cheat-sheet (pick the closest; only invent a new family for a genuinely
new domain):

| Family | Domain |
|---|---|
| B | Bedrock / NL-query layer |
| C | Carbon |
| CX | CloudFront / customer-experience / share path |
| D | Postgres / data layer |
| DEP | Deploy artifacts (Terraform/Helm/CI) |
| DM | Demo / MVP |
| F | FOCUS spec / conformance |
| FIN | FinOps semantics (framing, comparability, totals) |
| G | ManageIQ appliance / API |
| H | Hardening / sharp edges |
| J | FOCUS ↔ ManageIQ join |
| LM | Landmines (host-killing severity — rare) |
| MIQ | Live MIQ collector |
| NF | Normalizer / provider-native mapping |
| O | On-prem cost / chargeback |
| OBS | Observability |
| P | Production architecture / portability / residency |
| PKG | Packaging / tenant config |
| SEC | Security |
| W | Web layer |

## 2. Write the entry in the house format

Append under the matching `## section` of GOTCHAS.md (find it with
`grep -n "^## " GOTCHAS.md`); if none fits, add the entry near the end
before any reserve sections. The format:

```markdown
### <ID>. <One-line title stating the trap, not the topic>
- **What:** What was hit, concretely — file/module names, the wrong value vs the right one, and the fix applied. Past tense, specific.
- **Why it matters:** The trap for the next person — why the failure is non-obvious, what it would be misdiagnosed as, what class of bug it belongs to. Cross-reference related gotchas by ID (e.g. "the B-6/B-7 bug class").
- **EBA action:** What the ENBD team should DO — imperative, actionable, framed for people rebuilding this during the sprint.
```

Rules distilled from the existing 100+ entries:
- The title states the *lesson* ("a config knob that silently does nothing is worse than no knob"), not the component ("tenant.json issue").
- Numbers and names are concrete: `10,105` not "the correct total"; `charge_category='Usage'` not "a filter".
- Cross-reference by ID liberally — entries form a web, not a list.
- One entry per finding. Two findings = two IDs, even if fixed in one commit.

## 3. Verify

After appending, confirm no duplicate IDs were introduced:

```bash
grep -oE '^### [A-Z]+-[0-9]+' GOTCHAS.md | sort | uniq -d   # must print nothing
```
