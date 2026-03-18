# System Architecture

## Overview

This project currently uses a verified three-layer cognition chain:

1. **Brain**: orchestration and state snapshot layer
2. **Perception**: environment and interaction signal aggregation layer
3. **Psyche**: emotion and anxiety calculation layer

In addition, the system is constrained by a foundational honesty layer:

4. **IntegrityManager**: physical-evidence verification layer for file changes, command results, and truthful completion claims

## Layered Architecture

```text
IntegrityManager (constraint / verification layer)
        ↓
CentralBrain
        ↓
PerceptionEngine
        ↓
PsycheEngine
        ↓
AnxietyEngine
        ↓
Chronos sleep bias / agent state outputs
```

## Responsibilities

### 1. CentralBrain

`CentralBrain` is the orchestration layer.

It is responsible for:
- triggering one cognition update per cycle
- asking `PerceptionEngine` to collect current signals
- passing normalized perception values into `PsycheEngine.adjust_for_anxiety(...)`
- converting resulting anxiety into Chronos sleep interval bias
- producing immutable `AgentState` snapshots

Verified integration point:
- `src/brain.py`

### 2. PerceptionEngine

`PerceptionEngine` aggregates environmental and interaction signals from:
- Treasury
- Chronos
- Memory
- user input

It produces a structured `PerceptionState` containing fields such as:
- `balance_ratio`
- `time_pressure`
- `task_complexity`
- `failure_streak`
- `social_isolation_hours`
- `input_intensity`
- `confidence`

It also exposes:
- `get_system_health()`

`system_health` is derived from the active state of registered sensors and normalized to `0.0 - 1.0`.

Verified implementation point:
- `src/perception/engine.py`

### 3. PsycheEngine

`PsycheEngine` translates external signals into psychological variables.

`CentralBrain` currently passes perception values into:
- `balance_ratio`
- `time_pressure`
- `task_complexity`
- `failure_streak`
- `social_isolation_hours`
- `system_health`

`PsycheEngine.calculate_anxiety_factors(...)` derives intermediate anxiety factors and forwards them to `AnxietyEngine.calculate_anxiety(...)`.

Verified implementation points:
- `src/psyche/engine.py`
- `src/psyche/anxiety.py`

## Verified Variable Propagation Paths

### Path A: System perception health to systemic anxiety

This path is now verified end-to-end.

```text
PerceptionEngine.get_system_health()
    -> CentralBrain.update_cognition(...)
    -> PsycheEngine.adjust_for_anxiety(system_health=...)
    -> PsycheEngine.calculate_anxiety_factors(...)
    -> systemic_anxiety
    -> AnxietyEngine.calculate_anxiety(...)
    -> current_anxiety increases when system_health < 0.5
```

Rule:
- when `system_health < 0.5`, Psyche derives a `systemic_anxiety` factor
- that factor is then added into the anxiety score inside `AnxietyEngine`

Current verified expression:
- lower perception sensor health increases anxiety

### Path B: Interaction complexity to anxiety

```text
user_input
    -> PerceptionEngine._estimate_task_complexity(...)
    -> PerceptionState.task_complexity
    -> CentralBrain.update_cognition(...)
    -> PsycheEngine.adjust_for_anxiety(task_complexity=...)
    -> AnxietyEngine.calculate_anxiety(...)
```

### Path C: Memory-derived failures to anxiety

```text
recent failure memories
    -> PerceptionEngine._get_failure_streak()
    -> PerceptionState.failure_streak
    -> CentralBrain.update_cognition(...)
    -> PsycheEngine.adjust_for_anxiety(failure_streak=...)
    -> AnxietyEngine.calculate_anxiety(...)
```

### Path D: Social isolation to anxiety

```text
memory interaction history
    -> PerceptionEngine._calculate_social_isolation_hours()
    -> PerceptionState.social_isolation_hours
    -> CentralBrain.update_cognition(...)
    -> PsycheEngine.adjust_for_anxiety(social_isolation_hours=...)
    -> AnxietyEngine.calculate_anxiety(...)
```

## IntegrityManager as the Foundational Constraint Layer

`IntegrityManager` is not part of the emotional computation path.
It is the **foundation constraint layer** that governs whether the system may truthfully claim that a task is complete.

It verifies:
- Git working tree changes
- diff-visible file modifications
- command exit codes
- timestamp-based file update claims

This means the system architecture has two different concerns:

- **Cognitive path**: Brain -> Perception -> Psyche
- **Truth path**: IntegrityManager verifies whether claimed outcomes are physically real

Without IntegrityManager verification, the agent should not present physical changes or successful execution as confirmed facts.

Verified implementation point:
- `src/utils/integrity.py`

## Current Verified State

The following behaviors are already validated by code and tests:

- `PerceptionEngine.get_system_health()` returns a normalized sensor health score
- `CentralBrain.update_cognition()` injects `system_health` into `PsycheEngine.adjust_for_anxiety(...)`
- low perception health produces higher anxiety through `systemic_anxiety`
- perception-driven fields are persisted into immutable `AgentState`
- IntegrityManager can verify changed files and test execution claims using physical evidence

## Readiness for Future Hardware Integration

This architecture is suitable for future integration with external sensor systems such as Home Assistant because:

- Perception is already separated from Brain orchestration
- sensor activity can be registered and scored through `registered_sensors`
- degraded external inputs can be translated into psychological pressure through `system_health`
- IntegrityManager provides a truth constraint for future hardware-triggered execution claims
