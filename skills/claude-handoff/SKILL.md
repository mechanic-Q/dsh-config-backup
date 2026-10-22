---
name: claude-handoff
description: Hand the current conversation off to a fresh background agent that picks up the work immediately.
argument-hint: "What will the next session be used for?"
disable-model-invocation: true
---

Write a handoff summary of the current conversation so a fresh agent can continue the work. Save it to the temporary directory of the user's OS, then launch a background agent seeded with it: call the `subagent` tool with `run_in_background: true`, a short descriptive `description` (e.g. "Fix login bug"), and a `prompt` whose first line points the agent at the summary file by absolute path and tells it to continue the work described there. Pointing at the file keeps the shell from running backticks or expanding `$` in the summary.

The subagent starts in the current working directory and returns immediately with a subagent id. You are notified in-session when its run settles, and you can keep steering it by sending it a message with that id.

Include a "suggested skills" section in the summary, naming which skills the next agent should call the `skill` tool for.

Do not duplicate content already captured in other artifacts (specs, plans, ADRs, issues, commits, diffs). Reference them by path or URL instead.

Redact any sensitive information, such as API keys, passwords, or personally identifiable information, since the summary becomes the agent's prompt.

If the user passed arguments, treat them as a description of what the next session will focus on and tailor the summary accordingly.
