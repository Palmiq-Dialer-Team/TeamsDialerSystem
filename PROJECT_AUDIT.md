# TeamsPowerDialer Project Audit

Date: 2026-03-06
Scope: Static architecture and code review of the current `v2.0.0` codebase
Primary objective: Determine whether the project currently resembles a professional product or a spaghetti-code system, and identify the most important remediation paths.

---

## Executive Summary

TeamsPowerDialer is **not spaghetti in the absolute sense**, but it is also **not yet consistently professional across all layers**.

The project has several strong, product-grade foundations:

- Clear client ownership of AMD in `Installer/modules/ai_core.py`
- A usable FastAPI backend in `Backend/main.py`
- SQLite persistence with WAL enabled in `Backend/database.py`
- Object-based Teams automation in `Installer/modules/teams_auto.py`
- Reasonable defensive behavior in many failure paths

The main structural weakness is the installer layer, especially `Installer/main.py`, which has become a multi-role orchestration file that mixes:

- PyQt UI control
- call-state coordination
- AMD lifecycle management
- Teams automation workflow
- websocket message handling
- local Excel persistence decisions
- central database update behavior
- callback and disposition business rules

This means the project is best classified today as:

- Overall product grade: **Functional**
- Backend grade: **Functional to Professional**
- Installer grade: **Functional with clear spaghetti pressure**

If the current architecture continues to grow without refactoring, the installer side is the area most likely to degrade into true spaghetti.

---

## Investigation Sequence

This audit consolidates my direct review with the additional findings supplied from another agent instance.

### Step 1: Architectural boundary review

Reviewed the split between:

- `Installer/`
- `Backend/`

Primary files examined:

- `Installer/main.py`
- `Installer/modules/ai_core.py`
- `Installer/modules/streamer.py`
- `Installer/modules/teams_auto.py`
- `Backend/main.py`
- `Backend/database.py`
- `Backend/docs/AMD_WORKFLOW_SPEC.md`

Goals:

- Check separation of concerns
- inspect threading and worker interaction
- determine whether call flow uses a real state model or scattered flags

### Step 2: Client-side AI and audio path

Reviewed:

- `Installer/modules/ai_core.py`
- `Installer/modules/audio_core.py`
- `Installer/main.py`

Goals:

- Inspect model lifecycle
- assess VAD to Whisper handoff latency
- verify fallback behavior when loopback or capture setup fails

### Step 3: Backend scalability and data safety

Reviewed:

- `Backend/main.py`
- `Backend/database.py`
- `Backend/control_panel/db_viewer.py`
- related callback/export/websocket sections

Goals:

- Inspect SQLite access patterns
- evaluate WebSocket cleanup and uptime behavior
- inspect export and deduplication cost as the DB grows

### Step 4: Integration and automation

Reviewed:

- `Installer/modules/teams_auto.py`
- `Installer/modules/active_data_reader.py`
- `Installer/modules/data_handler.py`
- related import and refresh flow in `Installer/main.py`

Goals:

- verify Teams automation method
- assess Excel reliability for large or malformed inputs
- identify UI-freeze and integration brittleness

### Step 5: Security, deployment, and code-smell audit

Reviewed:

- `Installer/config.py`
- `Installer/launcher.py`
- `Backend/outlook_manager.py`
- `Installer/requirements.txt`
- `Backend/requirements.txt`
- root `requirements.txt`

Goals:

- find hardcoded infrastructure values
- compare dependencies against imports
- identify god files, stale modules, and broad exception patterns

---

## Final Product Verdict

### Is this a professional product or spaghetti code?

The most accurate answer is:

**A serious functional product with several professional subsystems, but with a client-side architecture that is accumulating enough coupling and state complexity to become spaghetti if not corrected soon.**

This codebase already contains real product thinking. It is not a toy. It has:

- a coherent AMD strategy
- a real backend
- persistence
- UI automation
- callback handling
- exports
- update flow

But professionalism in software is not only about features. It is also about:

- predictable state transitions
- clear ownership boundaries
- stable contracts
- recoverable failure modes
- maintainable module size
- operationally safe deployment

On those dimensions, the project is uneven.

---

## Task 1: Architectural Integrity and Pattern Analysis

**Grade:** Functional

### Separation of concerns

The broad split between installer and backend is understandable and mostly intentional:

- The installer owns AMD, call behavior, Teams control, and Excel-facing workflows.
- The backend owns storage, dashboards, callbacks, exports, and websocket fan-out.

This is reinforced by `Backend/docs/AMD_WORKFLOW_SPEC.md`, which explicitly says AMD is client-only. That is a good design decision because it creates a single source of truth for audio intent decisions.

However, the architecture becomes less clean when examining workflow ownership in detail.

#### What is working well

- `Installer/modules/ai_core.py` is a clearly defined AMD subsystem.
- `Backend/main.py` does not try to run Whisper or VAD anymore.
- `Backend/database.py` is straightforward and pragmatic for persistence operations.

#### Where abstractions leak

- `Installer/main.py` handles both local workbook mutations and central DB record flow. This creates a split-brain persistence model where local file state and server state can diverge.
- `Backend/main.py` stores installer-specific client filesystem metadata like `source_workbook_path`, `source_sheet_name`, and `source_row_index` for callbacks. Those are implementation details of the installer, not stable backend domain concepts.
- The backend callback flow is coupled to the shape of `contact_data` coming from the installer and to workbook-oriented semantics, which tightens coupling across tiers.
- The backend exposes `/api/calls`, but the installer largely relies on websocket `log_disposition` and `update_disposition` patterns instead of consistently using a stable call creation/update contract.

### Concurrency and threading

The code uses multiple workers:

- `AudioWorker`
- `AMDWorker`
- `StreamerWorker`
- `DialingWorker`
- background thread for AI preload
- asyncio loop thread inside `Installer/modules/streamer.py`

This is workable, but there are several real risks.

#### Critical threading findings

- `Installer/modules/streamer.py` invokes registered callbacks from the websocket asyncio thread.
- In `Installer/main.py`, methods like `handle_force_hangup()` mutate UI state, timers, and worker objects and can therefore be reached from the wrong thread.
- The same inbound event can be processed twice because the streamer both queues messages and invokes callbacks directly, while `check_incoming_messages()` also polls and handles messages.

#### Worker lifecycle findings

- `AMDWorker.stop()` is only partially cooperative. If the worker is already inside transcription or analysis, it may still emit late signals after the call context has changed.
- `stop_audio_threads()`, `_stop_autocall_listening()`, and `handle_human_detected()` use `wait(1000)` patterns from UI-facing code. These are short waits, but they still create GUI hitch risk.
- `AudioStreamer.start()` performs a blocking sleep during startup, which can also contribute to UI lag.

### State management

This is the weakest part of Task 1.

There is a genuine mini-state-machine in `Installer/modules/ai_core.py` through `AMDState`:

- `ringing`
- `speech_buffering`
- `done`

That part is clean enough.

The full installer call lifecycle is not modeled that way. Instead, it is spread across overlapping booleans and UI state variables, including:

- `call_status`
- `is_autocalling_active`
- `_bot_initiated_hangup`
- `_confirmed_human`
- `waiting_for_disposition`
- `_autocall_was_active_before_wait`
- `_using_local_ai_this_call`
- `_call_record_created`
- `timer_extended`
- `_no_answer_handling`
- `has_no_response_history`
- `current_ui_state`

This means system correctness depends on call ordering and careful flag resets rather than on one authoritative transition table.

### Critical Issues

- `Installer/modules/streamer.py` delivers callbacks on the wrong thread.
- Inbound websocket handling appears duplicated, especially for destructive events like `force_hangup`.
- `Installer/main.py` is effectively a god controller and is the main source of architectural complexity.
- `Backend/database.py::update_call_disposition()` updates rows heuristically using `agent_id + phone + recent time window` rather than a stable `call_id`.
- Backend callback storage contains installer filesystem coordinates, which weakens the backend contract.

### Optimization Opportunities

- Introduce a real call session state model.
- Move orchestration policy out of `Installer/main.py`.
- Normalize persistence around stable IDs instead of heuristic updates.
- Remove duplicate inbound websocket handling.
- Make all UI-affecting message delivery Qt-safe.

### Refactoring Roadmap

1. Create a `CallSessionState` enum with validated transitions.
2. Extract call orchestration into a dedicated service, separate from the main window.
3. Route all inbound websocket messages through a single Qt-safe dispatcher.
4. Standardize on explicit call creation plus exact `call_id` updates.
5. Remove installer filesystem leakage from the backend schema or replace it with domain identifiers.

---

## Task 2: Client-Side AI Performance

**Grade:** Functional

### Resource efficiency

The model lifecycle is partially correct.

#### What is already good

- `Installer/main.py::_init_ai_models()` preloads one `AMDDetector`.
- `Installer/modules/ai_core.py::_ensure_whisper()` caches the heavy `faster-whisper` model.

This means Whisper is not being re-created for every call, which is a strong design choice.

#### Main efficiency weakness

Silero VAD is still reloaded per call:

- each new `AMDState` calls `AMDDetector.get_new_vad_model()`
- that method performs a fresh `torch.hub.load(...)`

For a desktop dialer, this is expensive and unnecessary. It adds avoidable latency and resource churn to each monitored call.

### Inference latency

The AMD implementation is conservative and reasonably safe, but not optimized for minimum hangup latency.

Current behavior includes:

- a hard 3-second grace period
- multiple speech chunks before state transition
- early transcription only after silence plus minimum buffered duration
- fallback to a full 5-second buffer
- CPU-based Whisper with a comparatively expensive decoding profile

The code does contain a useful optimization: it can trigger transcription early after about 1.2 seconds of silence with enough buffered speech. That is better than waiting for a full fixed window every time.

Still, the system is not aggressively tuned for low dead air:

- `beam_size=5`
- `best_of=5`
- `word_timestamps=True`
- `compute_type="float32"`
- internal Whisper `vad_filter=True` despite already doing external VAD

These settings favor caution over speed.

### Error handling

Error handling is mixed.

#### Graceful behaviors already present

- Transient loopback read failures in `Installer/modules/audio_core.py` degrade to silence instead of crashing.
- Whisper failures return empty transcript results rather than hard-crashing the call loop.
- `analyze_buffered_audio()` generally fails safe toward keeping the call rather than hanging up too aggressively.

#### Serious fallback gaps

- If audio capture fails to start, `AudioWorker` exits, but the surrounding call flow can still proceed.
- Auto-call timers can still be armed before healthy audio analysis is fully confirmed.
- `AudioWorker.finished_signal` exists but there is no strong recovery path wired from it.
- `AMDWorker.speech_detected_signal` exists but is not connected, even though the workflow documentation implies that speech should affect no-answer timing.

This means the system can silently degrade into timeout-driven hangups without real audio analysis.

### Critical Issues

- Per-call Silero reload through `torch.hub.load(...)` is the main unnecessary re-initialization cost.
- Whisper settings are heavier than needed for an AMD classifier.
- Capture startup failure does not cleanly switch the app into a manual-monitoring fallback mode.
- Documented signal behavior and actual runtime wiring are not fully aligned.

### Optimization Opportunities

- Cache or pool VAD instances instead of reloading from hub.
- Use a cheaper Whisper profile for classification use.
- Make audio health an explicit state before auto-hangup logic is armed.
- Separate AMD input feed from dashboard streaming feed to avoid extra conversion overhead.

### Refactoring Roadmap

1. Preload Silero once and reuse resettable per-call instances or pooled workers.
2. Tune Whisper for fast AMD classification: lower beam width, lower best-of, and lighter compute profile.
3. Add explicit degraded-mode behavior when capture health is not confirmed.
4. Wire or remove currently unused AMD/audio signals.
5. Introduce a per-call AI session object so mutable AMD runtime state is not shared across calls.

---

## Task 3: Backend Scalability and Data Safety

**Grade:** Functional

### Database patterns

The SQLite layer is pragmatic but not yet backend-professional for long-term scale.

#### Good baseline choices

- WAL mode is enabled in `Backend/database.py`.
- useful indexes exist on calls and callbacks.
- logic is easy to trace and not over-engineered.

#### Weaknesses

- Nearly every database function creates a fresh `sqlite3.connect(...)`.
- There is no shared connection helper and no consistent context-manager pattern.
- DB calls are synchronous and happen directly inside async FastAPI handlers and websocket handlers.
- Every write commits immediately, which increases contention as activity grows.

That is acceptable for low concurrency, but it will become the first pressure point under higher write frequency.

### SQLite locking and snapshot safety

One of the most important backend findings is the control-panel snapshot behavior:

- `Backend/control_panel/db_viewer.py` copies the DB file with `shutil.copy2()`
- `Backend/database.py` runs the database in WAL mode

Copying only the main `.db` file is not WAL-safe. It can produce stale or incomplete snapshots because recent committed data may still be in the WAL sidecar rather than the main database file.

### API design

The API layer is serviceable but inconsistent:

- some Pydantic request models exist
- validation is shallow
- some endpoints return plain success/error dicts instead of proper HTTP semantics
- date filter parsing in export silently ignores invalid values instead of returning a client error

There is also excessive debug surface exposure:

- `/api/debug/status`
- `/api/debug/disconnect_agent/{agent_id}`
- `/api/debug/disconnect_all_agents`

Those routes expose useful operational power and internal state and should not appear publicly available without stronger protection.

### WebSocket lifecycle and uptime behavior

The connection manager is fairly readable, but not fully hardened.

Key risks:

- reconnects can replace stored sockets without explicitly closing prior sockets first
- admin connection bookkeeping is less reconnect-safe than the agent path
- some broadcasts iterate live dictionaries across awaits
- long-lived in-memory structures such as metadata and console session maps can drift over time without strong pruning

The classic websocket agent path is good enough for normal operation, but not fully production-hardened for very long uptimes or reconnect churn.

### Deduplication and export scalability

The "Export All" path in `Backend/database.py` is one of the clearest scale bottlenecks.

For all-call exports it uses:

- full filtered scan
- window ranking
- JSON extraction from `contact_data`
- `fetchall()`
- full in-memory CSV or XLSX generation

This is expensive as the DB grows.

There is also a correctness concern: the dedupe key is based on:

- `agent_id`
- first name
- last name
- first email

That means:

- blank identity fields can collapse unrelated contacts
- phone is not part of the dedupe key
- small changes in imported contact data can break dedupe consistency

### Critical Issues

- SQLite access is synchronous and per-operation inside async handlers.
- control-panel DB snapshots are not WAL-safe.
- export dedupe logic is correctness-risky and expensive at scale.
- debug/admin surface is broader than it should be.
- backend row updates still rely on installer-driven heuristics more than stable identity.

### Optimization Opportunities

- centralize DB connection setup and options
- move blocking DB work off the event loop
- replace snapshot copying with SQLite backup-aware logic
- materialize a stable dedupe key
- stream exports rather than loading everything into memory

### Refactoring Roadmap

1. Introduce a shared DB connection helper with consistent options and cleanup.
2. Move DB work off the async event loop for heavier paths.
3. Replace snapshot copying with SQLite backup API or WAL-aware snapshot logic.
4. Redesign export dedupe around a stable persisted identity key.
5. Tighten or remove unauthenticated debug endpoints and improve endpoint response modeling.

---

## Task 4: Integration and Automation

**Grade:** Functional

### Teams automation robustness

This part is better than expected.

The Teams integration in `Installer/modules/teams_auto.py` is primarily object-based:

- UI Automation
- window discovery
- control lookup by title/type
- `invoke()`
- fallback to `click_input()`
- cached window handle hangup flow

This is materially better than coordinate-driven clicking.

However, it is still brittle in a different way:

- relies on English strings like `Call`, `Leave`, `Dismiss`
- depends on Teams window titles and popup wording
- is sensitive to localization and Teams UI changes

So the design is the right type of automation, but not yet resilient enough to call professional-grade.

### Excel reliability

The Excel layer works for normal cases, but it is one of the least production-ready parts of the installer.

#### What it handles reasonably well

- retries COM attachment to active Excel
- header detection across sheets
- visible-only filtering support
- duplicate header normalization
- row padding and truncation
- defensive skipping of malformed rows

#### What is weak

- `import_contacts()` and refresh logic run on the GUI thread
- `load_data_shadow_copy()` can read all worksheets by default
- `UsedRange.Value` is pulled eagerly into memory
- large workbooks or Excel COM stalls can freeze PyQt
- row parse failures are often silently skipped

There is also evidence that `Installer/modules/data_handler.py` is stale or internally inconsistent relative to the tuple contract returned by `load_data_shadow_copy()`.

### Critical Issues

- Excel import and refresh remain synchronous in the main UI thread.
- workbook ingestion is eager and can scan multiple sheets by default.
- malformed-row handling favors silent skipping over operator-visible diagnostics.
- Teams selectors are object-based but still text-fragile.

### Optimization Opportunities

- move Excel read/normalize work into a worker thread
- import only the active sheet by default
- provide an import diagnostics report
- centralize Teams UI selector definitions and fallback rules

### Refactoring Roadmap

1. Move Excel import and refresh into a background worker with progress and timeout behavior.
2. Default to active-sheet import and make multi-sheet import explicit.
3. Add row-level diagnostics for skipped or malformed data.
4. Centralize Teams selectors and add capability/version probing.
5. Remove or repair stale wrapper modules such as `data_handler.py` if still referenced.

---

## Task 5: Security, Deployment, and Spaghetti Audit

**Grade:** Functional

### Hardcoded values

No obvious committed plaintext credentials were found in the core runtime files reviewed. Backend Outlook credentials are environment-driven, which is good.

However, the codebase contains many hardcoded infrastructure assumptions:

- fixed LAN IPs in installer and test client config files
- organization-specific OneDrive naming in `Installer/config.py`
- default workbook path assumptions
- Windows-only and environment-specific deployment assumptions

The most serious deployment concern is the updater flow:

- plain HTTP update source
- fixed IP endpoint
- ZIP extraction directly into the app directory
- dependency installation on every launch

This is operationally fragile and security-weak.

### Dependency management

Dependencies appear to have drifted.

Observed concerns:

- multiple requirements files are not fully aligned
- the root `requirements.txt` still reflects older architecture assumptions
- installer requirements appear to include packages with no clear active runtime use
- backend requirements include packages tied to incomplete or optional paths
- naming inconsistencies exist, such as `qdarktheme` versus `pyqtdarktheme`

One concrete concern: `Backend/main.py` references `opus_handler.OpusDecoder`, but no matching `opus_handler.py` was found in the repository during this review.

### Code smell audit

The clearest code-smell concentration is file size and responsibility creep.

Key examples:

- `Installer/main.py` is a god file
- `Backend/main.py` is also large and multi-purpose
- `Installer/modules/disposition_manager.py` combines too many responsibilities

Broad exception handling is also widespread. In many places this prevents hard crashes, but it also:

- hides root causes
- makes testing harder
- makes production behavior less diagnosable

### Critical Issues

- updater trust model is weak
- infrastructure config is heavily hardcoded
- requirements are inconsistent and likely bloated
- `Installer/main.py` has exceeded a maintainable responsibility boundary
- exception handling is too broad across the codebase

### Optimization Opportunities

- move environment-specific values to settings or env config
- split dependency sets by runtime role
- remove stale or unused packages
- replace blanket `except Exception` with narrower error handling and better logging
- break large orchestration files into role-focused modules

### Refactoring Roadmap

1. Move IPs, update endpoints, and organization-specific paths into environment-neutral config.
2. Replace updater transport and verification with a safer distribution mechanism.
3. Separate runtime dependency sets for installer, backend, test client, and optional tooling.
4. Break `Installer/main.py` into UI, workflow, integration, and state modules.
5. Replace blanket exception handling with typed exceptions plus structured logs.

---

## Cross-Cutting Themes

Across all five tasks, the same patterns recur:

### 1. Thick client orchestration

The installer owns too much workflow intelligence in one place.

### 2. Heuristic state instead of explicit state

Call lifecycle correctness depends on coordinated flags rather than a central state model.

### 3. Partial professionalism

Individual subsystems are often reasonable, but system-level contracts between them are not yet tight enough.

### 4. Defensive but opaque failure handling

The code tends to avoid crashing, but often at the cost of observability and deterministic behavior.

### 5. Good foundations, weak operational hardening

Architecture is good enough to improve. A full rewrite is not justified. A focused hardening and refactor program is.

---

## Priority Summary

### Highest priority

- Make websocket event delivery Qt-safe and single-path only
- introduce stable `call_id`-based backend updates
- formalize call session state in the installer
- fix updater and deployment trust assumptions
- stop blind auto-hangup when audio health is not confirmed

### Medium priority

- refactor Excel import off the UI thread
- replace per-call VAD reload with cached lifecycle management
- harden backend DB access and WAL-safe snapshots
- redesign export dedupe for scale and correctness

### Lower priority but still valuable

- remove stale modules and dependency drift
- narrow exception handling
- split large files by responsibility

---

## Conclusion

TeamsPowerDialer already has enough structure and product logic to be worth refining rather than replacing. The backend direction is generally sound. The client-side AMD ownership is a good decision. The Teams automation approach is the right type of automation.

The biggest reason the codebase does not yet feel fully professional is not the feature set. It is the concentration of orchestration logic, state flags, and persistence coordination inside `Installer/main.py`, combined with a deployment story that still contains hardcoded infrastructure assumptions and a weak updater model.

In short:

- **Not spaghetti overall**
- **At risk of becoming spaghetti on the installer side**
- **Strong candidate for targeted hardening rather than rewrite**

---

## Remediation Options Array

```json
[
  {
    "area": "Task 1 - State management",
    "priority": "P0",
    "issue": "Installer call flow is controlled by scattered booleans instead of an authoritative state machine.",
    "possible_solutions": [
      "Introduce a CallSessionState enum and a transition table.",
      "Move call orchestration out of Installer/main.py into a dedicated workflow service.",
      "Emit structured state-transition events for debugging and QA."
    ],
    "expected_benefit": "Fewer regressions, easier reasoning, cleaner UI logic."
  },
  {
    "area": "Task 1 - Message handling",
    "priority": "P0",
    "issue": "Inbound websocket messages can be delivered on the wrong thread and through duplicate paths.",
    "possible_solutions": [
      "Route all inbound messages through one Qt-safe dispatcher.",
      "Remove direct callback execution from the websocket asyncio thread.",
      "Delete or retire legacy server force_hangup handling if local AMD is the only authority."
    ],
    "expected_benefit": "Thread safety, fewer duplicate side effects, more predictable teardown."
  },
  {
    "area": "Task 1 and Task 3 - Persistence contract",
    "priority": "P0",
    "issue": "Backend updates still depend on heuristic row matching instead of stable identities.",
    "possible_solutions": [
      "Create call records once and propagate a stable call_id to the installer.",
      "Update the exact row by call_id rather than by agent_id, phone, and time window.",
      "Add explicit client-backend DTOs for call lifecycle events."
    ],
    "expected_benefit": "Stronger consistency and simpler reasoning about data correctness."
  },
  {
    "area": "Task 2 - AI model lifecycle",
    "priority": "P1",
    "issue": "Silero VAD is reloaded per call through torch.hub.load.",
    "possible_solutions": [
      "Preload Silero once at app start and reset per-call state only.",
      "Maintain a small pool of reusable VAD instances.",
      "Benchmark whether a lighter or exported runtime can replace hub-based loading."
    ],
    "expected_benefit": "Lower per-call latency and reduced CPU and memory churn."
  },
  {
    "area": "Task 2 - AMD latency",
    "priority": "P1",
    "issue": "Whisper settings are heavier than needed for fast AMD classification.",
    "possible_solutions": [
      "Reduce beam_size and best_of to 1 for AMD classification mode.",
      "Disable word timestamps in AMD mode.",
      "Test int8 or hybrid compute profiles and remove duplicate internal VAD if external gating is sufficient."
    ],
    "expected_benefit": "Faster hangup decisions and lower CPU usage."
  },
  {
    "area": "Task 2 - Fallback behavior",
    "priority": "P0",
    "issue": "Auto-call can continue into timeout logic even when audio capture never became healthy.",
    "possible_solutions": [
      "Gate no-answer timers on confirmed capture health.",
      "Introduce explicit degraded states such as AI_DISABLED or LOOPBACK_UNAVAILABLE.",
      "Fall back to manual monitoring instead of blind timeout hangups."
    ],
    "expected_benefit": "Safer behavior and fewer false no-response outcomes."
  },
  {
    "area": "Task 3 - DB access pattern",
    "priority": "P1",
    "issue": "SQLite calls are synchronous, repetitive, and executed directly in async handlers.",
    "possible_solutions": [
      "Introduce a shared get_connection helper with standard pragmas and cleanup.",
      "Move blocking DB work off the event loop.",
      "Serialize higher-volume write paths through a dedicated write worker if SQLite remains the datastore."
    ],
    "expected_benefit": "Better scalability and fewer event-loop stalls."
  },
  {
    "area": "Task 3 - WAL snapshots",
    "priority": "P1",
    "issue": "Control-panel snapshots are not WAL-safe.",
    "possible_solutions": [
      "Use the SQLite backup API for snapshots.",
      "Perform a controlled checkpoint before snapshotting if appropriate.",
      "Label snapshot freshness and source explicitly in the UI."
    ],
    "expected_benefit": "More reliable admin visibility into production data."
  },
  {
    "area": "Task 3 - Export scaling",
    "priority": "P1",
    "issue": "Export All deduplication is expensive and correctness-risky.",
    "possible_solutions": [
      "Persist a stable dedupe key or contact identity column.",
      "Index generated identity fields instead of computing json_extract-based partitions for every export.",
      "Stream CSV responses instead of fetchall plus full in-memory generation."
    ],
    "expected_benefit": "Faster exports and better correctness as the DB grows."
  },
  {
    "area": "Task 3 - Security surface",
    "priority": "P0",
    "issue": "Debug and admin endpoints expose operational power and internal state too broadly.",
    "possible_solutions": [
      "Require auth for all debug and control endpoints.",
      "Restrict dangerous endpoints to localhost-only or admin-only access.",
      "Remove endpoints that are not needed in production."
    ],
    "expected_benefit": "Lower operational and security risk."
  },
  {
    "area": "Task 4 - Excel reliability",
    "priority": "P1",
    "issue": "Excel import and refresh are synchronous and can block PyQt.",
    "possible_solutions": [
      "Move Excel COM and DataFrame processing to a worker thread.",
      "Add import progress, timeout, and cancel support.",
      "Default to active-sheet import and make full-workbook scans explicit."
    ],
    "expected_benefit": "Better UI responsiveness and fewer apparent freezes."
  },
  {
    "area": "Task 4 - Teams automation",
    "priority": "P2",
    "issue": "Teams selectors are object-based but still fragile against UI text changes and localization.",
    "possible_solutions": [
      "Centralize selectors in one adapter layer.",
      "Add alternate selector strategies per Teams UI version.",
      "Add a diagnostic mode to validate selectors before a production call."
    ],
    "expected_benefit": "Greater resilience against Teams UI changes."
  },
  {
    "area": "Task 5 - Updater and deployment",
    "priority": "P0",
    "issue": "Updater uses plain HTTP, hardcoded endpoints, and direct ZIP extraction.",
    "possible_solutions": [
      "Move to HTTPS with certificate validation.",
      "Add signature or hash verification for update artifacts.",
      "Validate ZIP paths before extraction and stop doing dependency installation on every launch."
    ],
    "expected_benefit": "Major improvement in deployment safety and trust."
  },
  {
    "area": "Task 5 - Hardcoded environment assumptions",
    "priority": "P1",
    "issue": "Server URLs, LAN IPs, and OneDrive assumptions are hardcoded.",
    "possible_solutions": [
      "Move infrastructure settings into env-backed or user-editable config.",
      "Add first-run configuration and validation screens.",
      "Keep committed config files environment-neutral."
    ],
    "expected_benefit": "Better portability, easier deployment, fewer operator errors."
  },
  {
    "area": "Task 5 - Dependency drift",
    "priority": "P2",
    "issue": "Requirements files appear inconsistent and contain likely stale or unused packages.",
    "possible_solutions": [
      "Split dependencies into installer, backend, test client, and optional ML/tooling sets.",
      "Perform a static import audit and remove unused packages.",
      "Repair incomplete optional paths such as the missing opus_handler dependency chain or remove them from runtime docs and requirements."
    ],
    "expected_benefit": "Cleaner installs, smaller environments, lower maintenance cost."
  },
  {
    "area": "Task 5 - Code smell",
    "priority": "P1",
    "issue": "God files and broad exception handling reduce maintainability and observability.",
    "possible_solutions": [
      "Break Installer/main.py into UI shell, call workflow, AI integration, persistence adapter, and messaging modules.",
      "Break large support classes such as DispositionSaver by responsibility.",
      "Replace blanket exception handling with typed errors and structured logging."
    ],
    "expected_benefit": "Higher maintainability, easier testing, better production diagnostics."
  }
]
```

---

## Existing Implementation Report

### 1. Client-side AMD ownership already exists

Current implementation location:

- `Backend/docs/AMD_WORKFLOW_SPEC.md`
- `Installer/modules/ai_core.py`

How it currently works:

- The client captures loopback audio and performs VAD plus Whisper analysis locally.
- The backend stores metadata and forwards audio but does not perform AMD.

Why it satisfies the requirement:

- This already enforces a single source of truth for AMD behavior and avoids duplicated AMD logic across tiers.

### 2. Teams automation is already object-based

Current implementation location:

- `Installer/modules/teams_auto.py`

How it currently works:

- Uses pywinauto UI Automation, control discovery, `invoke()`, window handles, and fallback object-driven interactions.

Why it satisfies the requirement:

- The live implementation is not based on screen coordinates or blind pixel clicking, so the requested object-based automation foundation is already present.

### 3. Whisper lifecycle is already partially optimized

Current implementation location:

- `Installer/modules/ai_core.py`
- `Installer/main.py`

How it currently works:

- One `AMDDetector` is preloaded and the heavy Whisper model is cached inside it.

Why it satisfies the requirement:

- The main expensive transcription model is not recreated on every call, so the remaining lifecycle issue is primarily Silero VAD, not Whisper.

### 4. Snapshot-based DB viewing already exists

Current implementation location:

- `Backend/control_panel/db_viewer.py`

How it currently works:

- The control panel creates a read-only snapshot copy before browsing database contents.

Why it satisfies the requirement:

- The design intent is already correct: the admin tool avoids opening the live production DB directly for browsing. The remaining problem is the WAL-safe implementation detail, not the concept itself.
