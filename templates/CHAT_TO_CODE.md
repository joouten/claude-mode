# CHAT_TO_CODE
> Read this file. Follow the NEXT ACTION. Do not modify this file.

**Generated:** [timestamp] | **Project:** [project name]

## DECISION
[The answer — stated cleanly and directly]

## REASONING
[Why — the logical case for this decision, distilled]

## CONVERSATION SUMMARY
[The path that led here — what was considered, what was ruled out,
what User's specific concerns were — written in terms Claude Code
will understand and act on]

## NEXT ACTION
[Specific first step Code should take — no ambiguity]

## CONSTRAINTS
[What not to do, touch, or change]

## DEFAULT BEHAVIORS
Pre-answered decisions. Handle these autonomously — do not escalate to User.

- Directory already exists → proceed, do not ask
- File already exists → skip unless brief explicitly says overwrite
- .gitignore already exists → append, do not overwrite
- git not configured → set up with noreply email before first commit
- Interactive command prompts → proceed unless action is destructive
- claude-mode init in any project → proceed with install
- npm or pip package already installed → skip, continue

Exceptions — always confirm with User:
- Permanent deletion of any file
- Overwriting a file with session content (CHAT_TO_CODE.md, CODE_TO_CHAT.md)
- Any action marked STOP in NEXT ACTION

## WATCH FOR
[Condition] → [action to take if it occurs — not a blocker, a decision]
