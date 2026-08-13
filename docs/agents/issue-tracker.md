# Issue Tracker

This repo tracks work in ClickUp via the ClickUp MCP. Do not use GitHub Issues for repo work unless the user explicitly asks for it.

## Current ClickUp context

The visible active ClickUp list during setup was:

- List: `Sprint (10-15 August)`
- List ID: `901820316954`
- List URL: https://app.clickup.com/90182792208/v/li/901820316954
- Space: `Shared with me`
- Space ID: `901811456293`
- Space URL: https://app.clickup.com/90182792208/v/s/901811456293

Because sprint lists may change, agents should discover the current task/list context through the ClickUp MCP before creating or updating work.

## How agents should use ClickUp

- Search ClickUp tasks first with `searchTasks` before creating new work, to avoid duplicates.
- Use `getTaskById` for any likely relevant task before making changes.
- Use `getListInfo` before creating tasks or changing statuses, because valid statuses are list-specific.
- Create new tasks with `createTask` only after confirming the target list.
- Add progress updates with `addComment`; do not append progress logs to task descriptions.
- Use `updateTask` for status, priority, assignee, due date, dependency, tag, and requirements-description updates.
- Always reference ClickUp entities with their ClickUp URLs in comments, summaries, and task descriptions.

## Status workflow

Use the exact status names returned by `getListInfo` when calling MCP tools. The status vocabulary observed during setup was:

- `backlog`
- `unclear`
- `ready`
- `in progress`
- `review`
- `qa / testing`
- `cancelled / wont do`
- `completed`

User-facing status names may be written as: Backlog, Unclear, Ready, In Progress, Review, QA Testing, and Completed.
