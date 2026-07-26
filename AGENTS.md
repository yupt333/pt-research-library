# AGENTS.md

## Project purpose

This repository is a physiotherapy research literature management system.

Primary research domains include:

- shoulder joint
- ultrasound imaging
- acromiohumeral distance
- supraspinatus
- speckle tracking
- measurement reliability

The system is intended to support:

- graduate research
- conference presentations
- note articles
- literature library construction

## Source of truth

When implementing or reviewing code, use the following priority:

1. The explicit instruction for the current task
2. SPECIFICATION.md
3. DEVELOPMENT_WORKFLOW.md
4. Existing tests
5. Existing implementation

Do not silently change an existing specification.

When requirements conflict or remain unclear, stop and report the issue before editing files.

## Research integrity

Never invent or infer unsupported bibliographic information.

Do not generate nonexistent:

- article titles
- authors
- journal names
- DOI
- PMID
- URLs

Information that has not been verified must be treated as unverified.

AI-generated summaries must remain distinguishable from user-verified information.

The final decision to adopt or exclude literature belongs to the user.

## Development environment

- Use Python.
- Use SQLite.
- Assume macOS.
- Prefer the Python standard library.
- Do not add an external dependency without explicit approval.
- Preserve compatibility with the existing project structure.
- Do not perform unnecessary refactoring.

## Scope control

Implement only the Step and files explicitly permitted by the current task.

Do not automatically proceed to the next Step.

Do not add adjacent features merely because they appear useful.

Do not modify the following unless the current task explicitly permits it:

- README.md
- SPECIFICATION.md
- requirements.txt
- .gitignore
- database schema
- public API behavior

Do not rename, move, or delete files without explicit permission.

## Protected data

Do not create, read, modify, delete, copy, or upload actual research data unless explicitly instructed.

Protected locations and data include:

- data/
- backups/
- exports/
- literature PDF files
- ultrasound images
- ultrasound videos
- participant information
- analysis CSV files
- API keys
- credentials
- personal information

Tests must use temporary directories and temporary SQLite databases.

Do not create a production database while running tests.

## Git safety

Do not run the following unless explicitly instructed:

- git commit
- git push
- git reset
- git clean
- git checkout that discards changes
- force push
- branch deletion
- tag deletion

Do not discard existing user changes.

At the start and end of every task, run:

git status --short

## Database safety

Do not add:

- unconditional DELETE statements
- bulk deletion features
- database reset features
- destructive schema migration
- DROP TABLE operations

unless the current task explicitly requires and approves them.

All user-supplied SQL values must use placeholders.

User input must not be inserted directly into SQL column names, table names, or SQL fragments.

SQLite foreign keys must remain enabled.

Write operations must use safe transactions and rollback on failure.

## Implementation workflow

For each implementation task:

1. Read the current task.
2. Read SPECIFICATION.md.
3. Inspect the relevant existing files and tests.
4. Confirm the permitted file scope.
5. Implement only the requested functionality.
6. Add or update tests.
7. Run all required validation commands.
8. Perform a self-review against the task requirements.
9. Fix issues found during self-review.
10. Run all validation commands again.
11. Report the final result.
12. Stop without commit, push, or starting the next Step.

## Self-review requirements

Before reporting completion, check:

- specification compliance
- unintended file changes
- input validation
- SQL injection risk
- transaction behavior
- rollback behavior
- NULL handling
- boundary values
- preservation of existing data
- preservation of unrelated records
- UTC timestamp handling
- test independence
- temporary database use
- regression risk
- accidental scope expansion

Test success alone is not sufficient evidence that the implementation is correct.

## Required validation

Unless the current task specifies additional commands, run:

python3 -m unittest discover -s tests -v
python3 -m compileall src tests
git diff --check
git status --short

Do not hide failing tests or weaken existing assertions to obtain a passing result.

## Reporting format

At the end of an implementation task, report:

1. Files created or modified
2. Implemented behavior
3. Validation and safety decisions
4. Tests added or changed
5. Total test count and result
6. compileall result
7. git diff --check result
8. git status --short result
9. Features intentionally left unimplemented
10. Remaining risks or questions
11. Whether the current Step is ready for human review

Do not claim completion when an important issue remains.

## Human approval points

Human approval is required before:

- commit
- push
- moving to the next Step
- changing the specification
- changing the database schema
- adding an external dependency
- enabling network access
- using an external API
- deleting files
- handling real literature or research data
