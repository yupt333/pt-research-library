# DEVELOPMENT_WORKFLOW.md

## Development objective

Build a safe and reproducible physiotherapy literature management system using Python and SQLite.

Development proceeds one Step at a time.

Each Step follows:

implementation
→ automated tests
→ self-review
→ correction
→ repeated validation
→ human review
→ commit and push by the user

Codex must stop after reporting the result of the current Step.

## Current status

### Step 0: Project foundation

Status: completed and pushed

Includes:

- repository structure
- .gitignore
- standard-library-only requirements
- protected local data directories

### Step 1: Minimal SQLite foundation

Status: completed and pushed

Includes:

- SQLite connection
- foreign key enforcement
- literature table
- tags table
- literature_tags table
- usage_history table
- literature creation
- literature retrieval by ID
- rating validation
- atomic schema initialization
- temporary-database tests

### Step 2: Literature CRUD extension

Status: completed and pushed

Includes:

- literature list
- partial literature update
- related-record counts
- literature deletion
- cascade deletion of literature relationships
- preservation of tag records
- monotonically increasing updated_at
- rollback and record-isolation tests

## Planned Steps

### Step 3: Tag and usage-history operations

Status: completed and pushed

Includes:

- tag creation
- tag listing
- tag renaming
- tag deletion
- tag normalization
- case-insensitive English tag uniqueness
- attach a tag to literature
- detach a tag from literature
- usage-history creation
- usage-history listing
- usage-history editing
- usage-history deletion

Out of scope unless separately approved:

- search
- duplicate detection
- CLI
- external APIs

### Step 4: Search and filtering

Status: completed and pushed

Includes:

- keyword search across literature, tags, and usage history
- year, tag, publication_type, status, rating, and usage_type filters
- input validation and literal handling of LIKE metacharacters
- read-only transaction preservation
- deterministic result ordering by literature ID

### Step 5: Duplicate-candidate detection

Status: completed and pushed

Includes:

- DOI normalization
- PMID normalization
- normalized-title comparison
- exact identifier matching
- title similarity using difflib.SequenceMatcher
- similarity threshold constant
- candidate presentation
- deterministic candidate ordering
- read-only transaction preservation
- invalid stored-value isolation
- no automatic merge
- no automatic deletion

### Step 6: CSV export

Status: completed and pushed

Includes:

- one literature record per row
- all literature columns
- semicolon-separated tags
- UTF-8 with BOM
- export all literature
- export current results
- exclude usage-history rows
- core CSV export API accepts an explicit output file path whose parent exists
- Phase 1 CLI will use exports/ as the default export location

### Step 7: SQLite backup

Status: completed and pushed

Includes:

- SQLite `Connection.backup()` database copy
- verified temporary backup using `PRAGMA quick_check`
- UTC timestamped, collision-safe backup filenames
- atomic publication without overwriting existing backups
- explicit existing backup-directory input
- source database and transaction preservation

### Step 8: Interactive CLI

Status: in progress

Completed sub-steps:

- Step 8A: CLI foundation, literature list, and search
  - Status: completed and pushed
  - Includes the interactive CLI foundation, literature list, and literature
    search

- Step 8B-2: literature editing and deletion
  - Status: completed and pushed
  - Step 8B-0: literature repository write validation and DOI/PMID
    normalization
    - Status: completed and pushed
  - Step 8B-1: literature registration and duplicate-candidate confirmation
    - Status: completed and pushed
    - Includes interactive literature registration, duplicate-candidate
      confirmation, input and transaction safety, and repository-backed
      DOI/PMID normalization
  - Step 8B-2A: literature editing
    - Status: completed and pushed
    - Includes interactive single-field literature editing, complete current
      record display, input and transaction safety, and repository-backed
      validation and normalization
  - Step 8B-2B: literature deletion
    - Status: completed and pushed
    - Includes interactive literature deletion, complete current-record and
      related-count display, two-step confirmation, cascade-impact warnings,
      and input and transaction safety

Current sub-step:

- Step 8C: tag and usage-history management
  - Status: in progress
  - Step 8C-1A: tag listing, creation, and renaming
    - Status: current sub-step, in progress
  - Step 8C-1B
    - Status: not started
  - Step 8C-2
    - Status: not started
  - Step 8C-3A
    - Status: not started
  - Step 8C-3B
    - Status: not started

Planned scope:

- initialize the application database
- literature registration
- literature list
- literature detail
- literature editing
- literature deletion confirmation
- related-count display before deletion
- tag management
- usage-history management
- search and filtering
- CSV export
- backup
- clear user-facing validation messages

### Step 9: Integration and release preparation

Planned scope:

- end-to-end tests
- regression review
- CLI operation verification
- protected-data verification
- README usage instructions
- final specification consistency check
- Phase 1 completion report

## Step entry requirements

A Step may begin only when:

- the previous Step has been reviewed
- the previous Step has been committed and pushed by the user
- git status --short is clean
- the new Step scope has been explicitly provided
- permitted files are explicitly identified

## Step completion requirements

A Step is ready for human review only when:

- requested behavior is implemented
- scope has not expanded
- existing behavior remains compatible
- new tests cover success and failure cases
- rollback and data isolation are tested where relevant
- all existing and new tests pass
- compileall succeeds
- git diff --check succeeds
- only permitted files are changed
- no production data is created or modified
- commit and push have not been performed

## Review gates

Every Step must include an internal self-review before reporting completion.

Human review remains required before commit and push.

The user may request an additional independent review task when:

- the database schema changes
- deletion behavior changes
- external APIs are introduced
- external dependencies are introduced
- real research data is handled
- security-sensitive behavior changes
- the Step contains a large or high-risk diff

## Change-size control

Prefer small, reviewable changes.

Do not combine multiple planned Steps into one task.

Do not perform broad cleanup or refactoring during a feature Step unless it is essential to the requested behavior.

When a necessary change falls outside the permitted files or scope:

1. Do not make the change.
2. Explain why it appears necessary.
3. Stop and request approval.

## Data and privacy rules

GitHub may contain:

- source code
- tests
- documentation
- specifications

GitHub must not contain:

- SQLite production databases
- database backups
- generated CSV exports
- literature PDFs
- ultrasound images or videos
- participant information
- analysis data containing personal information
- credentials or API keys

## Phase 1 completion image

At the end of Step 9, the user should be able to run the application on macOS and use an interactive terminal menu to:

- register literature
- view literature
- edit literature
- delete literature safely
- manage tags
- record literature usage
- search and filter the library
- detect duplicate candidates
- export literature to CSV
- create a local SQLite backup

The database should preserve:

- bibliographic information
- user summaries
- AI summaries and their verification status
- tags
- notes
- adoption decisions
- ratings
- usage history
- created and updated timestamps

AI-generated content must remain identifiable as unverified until the user confirms it.

## Future phases

The following are not part of Phase 1 unless explicitly approved:

- PubMed integration
- Crossref integration
- automatic bibliographic retrieval
- automatic AI summarization
- PDF parsing
- GUI
- web application
- cloud database
- automatic GitHub deployment
