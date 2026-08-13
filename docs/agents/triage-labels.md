# Triage Labels

The skills speak in terms of five canonical triage roles. This repo maps those roles to ClickUp statuses, not GitHub/GitLab labels.

| Label in mattpocock/skills | ClickUp status        | Meaning                                  |
| -------------------------- | --------------------- | ---------------------------------------- |
| `needs-triage`             | `backlog`             | Maintainer needs to evaluate this issue  |
| `needs-info`               | `unclear`             | Waiting on reporter for more information |
| `ready-for-agent`          | `ready`               | Fully specified, ready for an AFK agent  |
| `ready-for-human`          | `review`              | Requires human implementation or review  |
| `wontfix`                  | `cancelled / wont do` | Will not be actioned                     |

When a skill mentions a role, use the corresponding ClickUp status from this table. Use the exact lowercase status string returned by ClickUp MCP when calling `createTask` or `updateTask`.
