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
- Phase 1 CLI uses exports/ as the default export location

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

Status: completed and pushed

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

- Step 8C: tag and usage-history management
  - Status: completed and pushed
  - Step 8C-1A: tag listing, creation, and renaming
    - Status: completed and pushed
    - Includes repository-backed tag listing, creation, and renaming with
      input and transaction safety
  - Step 8C-1B
    - Status: completed and pushed
    - Includes safe repository-backed tag deletion with relationship-impact
      warnings, two-step confirmation, and transaction protection
  - Step 8C-2
    - Status: completed and pushed
    - Includes repository-backed literature tag listing, tag attachment, and
      tag detachment with input and transaction safety
  - Step 8C-3A
    - Status: completed and pushed
    - Includes repository-backed usage-history listing and creation with
      input, confirmation, and transaction safety
  - Step 8C-3B
    - Status: completed and pushed
    - Includes repository-backed usage-history editing and deletion with
      confirmation, input, transaction, and data-isolation safety

Completed sub-step:

- Step 8D: CLI completion
  - Status: completed and pushed
  - Step 8D-1: dedicated literature detail
    - Status: completed and pushed
    - Includes complete literature detail display with tags, usage history,
      input safety, and read-only transaction preservation
  - Step 8D-2: connect CSV export to CLI
    - Status: completed and pushed
    - Includes all-literature and last-search-result CSV export with fixed
      default paths, input safety, and read-only transaction preservation
  - Step 8D-3: connect SQLite backup to CLI
    - Status: completed and pushed
    - Includes repository-backed SQLite backup creation from the CLI with
      fixed default paths, input safety, and source-connection preservation
  - Step 8D-4: DB initialization/default path/entrypoint
    - Status: completed and pushed
    - Includes application database initialization, project-rooted default
      data/export/backup paths, and the `python3 -m src` entry point
  - Step 8D-5: overall CLI integration/display/usability cleanup
    - Status: completed and pushed
    - Includes final CLI-wide integration, display consistency, input
      handling, cancellation behavior, and usability verification

Completed scope:

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

Status: completed and pushed

Completed scope:

- Phase 1 end-to-end verification
- full regression verification
- protected-data verification
- README completion
- specification consistency verification
- Phase 1 completion assessment: GO

## Phase 1 status

Status: completed and pushed

Completion assessment: GO

Completion summary:

- Steps 0–9 completed and pushed
- Phase 1 required functionality implemented and tested
- final regression: 504 tests passed
- end-to-end application workflow verified
- restart persistence verified
- CSV export verified
- SQLite backup verified
- protected tracked-data audit passed
- no blocking issues at Phase 1 completion

## Current Phase

Phase 2

## Completed Phase 2 Steps

### Phase 2-0: Product direction / roadmap freeze

Status: completed and pushed

Scope:

- freeze the Product Goal and differentiation principles
- freeze the ChatGPT / application responsibility boundary
- freeze the Phase 2 API and cost policy
- freeze the Phase 1 preservation principles
- freeze the Phase 2-0 through Phase 2-12 roadmap
- document the Phase 3 direction and Phase 4 automation gate
- documentation only; no production code, database schema, or test changes

The full product direction and roadmap are recorded in
`docs/PRODUCT_ROADMAP.md`.

### Phase 2-1: Literature Detail Information Architecture

Status: completed and pushed

Scope:

- Literature Detail semantic architecture
- Header / Status Summary
- Overview
- Study
- Methods
- Outcomes
- Evidence
- Research Relevance
- Tags / Usage
- Phase 1 field mapping
- future structured field mapping
- low-fidelity detail wireframes
- backward compatibility
- documentation only; no production code, database schema, or test changes

The full information architecture is recorded in
`docs/LITERATURE_DETAIL_IA.md`.

## Current Step

### Phase 2-2: ChatGPT Structured Import Contract v1

Status: current step, in progress

Scope:

- JSON structured import contract
- analysis metadata
- bibliography
- study
- five methods subgroups
- outcomes and results
- limitations
- concepts
- research relevance
- evidence
- availability vocabulary
- verification vocabulary
- payload-local ID and Evidence reference integrity
- synthetic valid JSON example
- manual ChatGPT Output Instructions v1
- documentation only; no OpenAI API, external API, database schema, parser,
  Import Preview implementation, production code, dependency, or test changes

The full contract is recorded in
`docs/STRUCTURED_IMPORT_CONTRACT_V1.md`.

## Phase 2 Planned Steps

Phase 2 Steps must be performed in the following order. A later Step must not
be implemented early without an approved roadmap change.

### Phase 2-3: Structured Research Data Model

Status: planned

Includes:

- Study / Methods / Outcomes / Evidence relationships
- SQLite schema design from the conceptual model
- migration design
- Phase 1 data preservation
- mandatory migration tests

### Phase 2-4: Structured Data Repository + Import Preview

Status: planned

Includes:

- CRUD for the new structures
- ChatGPT result parser
- validation
- Import Preview
- user confirmation before save
- unverified initial state for AI-derived information
- no API

### Phase 2-5: Evidence Reference

Status: planned

Includes:

- page / section / table / figure / original text / verification
- structured item to evidence association

### Phase 2-6: Original PDF Evidence Navigation

Status: planned

Includes:

- connect `pdf_path` and Evidence
- navigation from Evidence metadata to the original source
- no automated PDF analysis at this Step

### Phase 2-7: Multi-Literature Comparison Matrix

Status: planned

Includes:

- multiple-literature selection
- side-by-side structured fields
- comparison centered on PT research Methods

### Phase 2-8: Outcome Comparability

Status: planned

Includes:

- separate Outcome name and definition comparison
- calculation / imaging / algorithm / validation display
- consideration of directly / partially / not directly / needs review states
- no mandatory AI-based automatic judgment

### Phase 2-9: Research Project Model

Status: planned

Includes:

- independent Project management
- many-to-many Literature association
- Concepts / objective / current status / unresolved issues / next action /
  protocol notes

### Phase 2-10: Own Protocol vs Literature Comparison

Status: planned

Includes:

- structured comparison of the user's research conditions and prior studies
- matches / differences / methodological caution / direct comparability

### Phase 2-11: Integrated Research Workflow

Status: planned

Includes:

- integrated Literature Detail / Import / Evidence / Comparison / Project flow
- fewer user operations
- Phase 2 UI workflow validation

### Phase 2-12: Phase 2 Integration / E2E / Completion Gate

Status: planned

Includes:

- migration persistence
- backward compatibility
- E2E
- regression
- data safety
- Phase 2 completion assessment

## Phase 3 High-Level Roadmap

Phase 3 focuses on product UI, knowledge relationships, and AI-assisted
workflow. Its order may be reevaluated at Phase 2 completion while preserving
the following high-level direction.

- Phase 3-1: Local GUI / Product UI v1
- Phase 3-2: Concept Relations / Backlinks / Research Hub
- Phase 3-3: ChatGPT Comparison Bridge, with no API by default
- Phase 3-4: Natural-Language Research Assistance, validating no-API options
  first
- Phase 3-5: Optional Markdown / Obsidian Export, only if a need is confirmed

## Phase 4 Automation Gate

Full automation is a Phase 4-or-later candidate. PubMed, Crossref, automatic
bibliographic retrieval, PDF parsing, automatic AI analysis, OpenAI API, and
cloud services may be considered only after Phase 2 / 3 usage demonstrates
that manual work is a bottleneck, automation has sufficient value, cost is
acceptable, and free alternatives are insufficient.

Paid services require explicit user approval before implementation. The cost,
necessity, free alternative, expected usage, and financial viability must be
presented before approval.

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

## Roadmap governance

The roadmap in `docs/PRODUCT_ROADMAP.md` is the default development plan.
Before changing it, explain the reason and the impact on the existing
specification, data, tests, and cost. Do not substantially change Phase
priorities or implement a later Step early without explicit user approval.

Phase 2 does not introduce OpenAI API. Paid APIs, cloud databases, paid
servers, paid SaaS, paid dependencies, and other recurring-cost services must
not be introduced without explicit user approval.
