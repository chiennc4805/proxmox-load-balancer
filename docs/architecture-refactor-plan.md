# Architecture Refactor Plan

## Goal

Refactor the current MVP into a clear synchronous flow while keeping the codebase ready for future growth.

Current MVP flow:

```text
request -> main/router -> controller -> job state -> scheduler -> proxmox adapter -> provision -> job state -> response
```

Chosen response mode: **Option A**.

The API still waits for the clone process to finish and returns the clone result, but the response also includes `job_id` and job status.

Example:

```json
{
  "job_id": "...",
  "status": "succeeded",
  "vmid": 101,
  "node": "pve2",
  "ip_address": "192.168.1.20"
}
```

No external queue, no background worker, no async distributed architecture yet.

## Design Principles

1. Each component has one clear responsibility.
2. Components should depend on abstractions/data contracts, not each other's internals.
3. Scheduler does not own state storage.
4. Scheduler receives an algorithm and fixed filters, then returns a selected node.
5. Algorithms own their own scoring/selection logic.
6. Proxmox adapter only talks to Proxmox. It does not know about HTTP, controllers, jobs, or placement history.
7. Controller coordinates the use case but does not contain Proxmox implementation details.
8. MVP should stay synchronous and simple.

## Proposed Package Structure

```text
backend/app/
  main.py

  controllers/
    vm_controller.py
    job_controller.py

  models/
    vm.py
    job.py
    placement.py

  jobs/
    store.py

  scheduler/
    scheduler.py
    filters.py
    algorithms.py

  proxmox/
    adapter.py
    errors.py
```

## Models vs Schemas

Use `models/` only.

No separate `schemas/` package for now.

Reason:

- This is an MVP.
- The project does not need separate API DTOs, domain entities, and persistence models yet.
- Pydantic request/response models and internal dataclasses can live under `models/` with clear names.

Suggested split inside `models/`:

```text
models/vm.py
  CloneVmRequest
  CloneVmResult
  CloneToNodeRequest

models/job.py
  JobStatus
  JobRecord
  JobResult

models/placement.py
  NodeCandidate
  PlacementContext
  PlacementDecision
```

If later API schemas diverge from internal domain models, only then introduce `schemas/`.

## Component Responsibilities

### main.py

Responsibility:

- Create FastAPI app.
- Register routes/controllers.
- No business logic.

### controllers/vm_controller.py

Responsibility:

- Receive validated request object.
- Create job record.
- Call scheduler to select target node.
- Call Proxmox adapter to provision VM.
- Update job state.
- Return final response with `job_id`.

It may orchestrate the MVP use case directly. No service layer is required yet.

### controllers/job_controller.py

Responsibility:

- Read job state by `job_id`.
- List jobs later if needed.

### jobs/store.py

Responsibility:

- Create job.
- Mark job running/succeeded/failed.
- Get job by id.

It does not know scheduler algorithms or Proxmox behavior.

### scheduler/scheduler.py

Responsibility:

- Receive candidate resources and a placement request/context.
- Apply fixed filters.
- Call the selected algorithm.
- Return `PlacementDecision`.

The scheduler should not read or write state directly.

Example direction:

```python
class Scheduler:
    def __init__(self, algorithm, filters):
        self.algorithm = algorithm
        self.filters = filters

    def select_node(self, context):
        candidates = apply_filters(context.candidates, self.filters, context)
        return self.algorithm.select(context.with_candidates(candidates))
```

### scheduler/filters.py

Responsibility:

- Fixed actions that remove invalid candidates.

Initial filters:

- Node must be online.
- Node must not be in attempted nodes.

Future filters:

- Maintenance mode.
- Storage compatibility.
- Network bridge compatibility.
- Minimum memory.
- Minimum disk.

Filters do not score nodes. They only pass/fail candidates.

### scheduler/algorithms.py

Responsibility:

- Select one node from already-filtered candidates.
- Own all scoring/selection logic.

Initial algorithm:

- `RoundRobinAlgorithm`

Important:

- If an algorithm needs state, that state must be passed into its context or injected dependency.
- The scheduler itself must not become a state store.

Possible interface:

```python
class PlacementAlgorithm:
    name: str

    def select(self, context: PlacementContext) -> PlacementDecision:
        ...
```

For round robin, the needed `last_node` can be part of `PlacementContext`, loaded outside scheduler by the controller or a placement state component.

### proxmox/adapter.py

Responsibility:

- Read Proxmox resources.
- Get next VMID.
- Clone VM.
- Start VM.
- Read VM IP from guest agent.
- Validate Proxmox-specific errors.

It must not know about jobs, controllers, or scheduler internals.

## Handling Placement History

Do not place `state_store` inside `scheduler/`.

Current MVP decision:

- Keep `jobs/store.py` for `job_state`, scheduler config, and advisory placement reservation.
- Do not keep `scheduler_state` as the round-robin cursor.
- Round-robin cursor is read from `job_state.selected_node` ordered by `selected_at`.
- Placement reservation is serialized with a PostgreSQL advisory lock per `job_type` and algorithm.
- Do not let scheduler or algorithms import store implementation.

The controller coordinates the transaction boundary: it asks `JobStore` to reserve placement, and `JobStore` passes the latest `last_node` into a callback. The callback calls `Scheduler`, so the scheduler remains pure and DB-free.
## MVP Clone Flow

```text
POST /api/vms/clone
  -> main.py route registration
  -> VmController.clone_vm(request)
  -> JobStore.create(type="vm.clone", payload=request)
  -> JobStore.mark_running(job_id)
  -> ProxmoxAdapter.list/resources
  -> Scheduler.select_node(context)
  -> ProxmoxAdapter.clone_and_get_ip(..., target_node=selected_node)
  -> JobStore.mark_succeeded(job_id, result)
  -> return result + job_id
```

On failure:

```text
  -> JobStore.mark_failed(job_id, error)
  -> raise HTTPException or return failed job response, depending on current API behavior decision
```

For MVP, keep current HTTP error behavior if clone fails, but persist the failed job for debugging.

## Refactor Phases

### Phase 1: Route and Controller Split

- Move FastAPI endpoint logic out of `main.py`.
- Add `controllers/vm_controller.py`.
- Add `controllers/job_controller.py` only if job read endpoint is added in the same phase.
- Keep behavior unchanged.

### Phase 2: Models Consolidation

- Move Pydantic request classes from `main.py` to `models/vm.py`.
- Add placement dataclasses to `models/placement.py`.
- Add job models to `models/job.py`.

### Phase 3: Job State

- Add `jobs/store.py`.
- Add DB table for jobs.
- Controller creates and updates job state.
- Response includes `job_id` on success.
- Add `GET /api/jobs/{job_id}`.

### Phase 4: Scheduler Boundary

- Refactor scheduler into:
  - fixed filters
  - algorithm interface
  - scheduler orchestration
- Scheduler receives context and algorithm.
- Scheduler does not import or own DB store.

### Phase 5: Proxmox Adapter Namespace Cleanup

- Current adapter moved to `backend/app/proxmox/adapter.py`.
- `backend/app/proxmox_adapter.py` remains as a compatibility import bridge.
- Behavior remains unchanged.

### Phase 6: Preserve Compatibility

- Existing frontend can continue to work.
- Existing clone response fields remain.
- Add `job_id` and `status` fields without removing current fields.

## Decisions

1. Round-robin cursor lives in `job_state`, not `scheduler_state`.
2. Placement reservation uses PostgreSQL advisory lock per `job_type` and algorithm.
3. Clone failure keeps current HTTP error behavior, while saving `failed` job state.
4. `/api/vms/clone-to-node` also creates job state for debugging consistency.
5. Admin scheduler config remains in the current API, backed by `scheduler_config`.