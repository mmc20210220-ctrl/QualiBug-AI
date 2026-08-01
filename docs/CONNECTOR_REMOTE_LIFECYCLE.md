# Connector remote lifecycle

## Purpose

QualiBug treats a connector snapshot as a view of the resources currently visible inside the configured scope. A resource absent from one snapshot is not automatically considered removed from the customer system. It may have moved outside the configured scope, become temporarily invisible, or been affected by a permission or traversal failure.

This lifecycle authority is internal to QualiBug. It never edits, moves, comments on, shares, changes permissions for, or removes customer materials.

## Stable identity

Connector Source Occurrences use stable connector resource identity, not display title or parent path, as identity. Therefore:

- renaming updates display metadata without creating a new occurrence;
- moving inside the configured scope updates parent metadata without creating a new occurrence;
- a returned resource can reactivate its prior occurrence and history;
- historical bytes and interpretation evidence remain retained after internal retirement.

## Complete snapshots

`authoritative_snapshot_complete=true` means enumeration completed for resources currently accessible inside the configured scope. It does not prove a remote cause.

Authentication, permission, traversal, network, rate-limit, supported-resource export, malformed enumeration, or unknown failures must prevent absence evidence from advancing. The supported snapshot must commit successfully before lifecycle reconciliation runs.

## Missing-resource states

First complete absence:

```text
ABSENT_FROM_CONFIGURED_SCOPE_UNCONFIRMED
```

The occurrence remains active and QualiBug keeps the last-known-good material while waiting for another complete snapshot.

After the configured number of consecutive complete absences, normally two:

```text
ABSENT_FROM_CONFIGURED_SCOPE_CONFIRMED
```

This still means only that the resource was absent from the configured scope.

## RETAIN and RETIRE_MISSING

The canonical ingestion authority always runs with `RETAIN`. `RETIRE_MISSING` is handled only by the guarded lifecycle coordinator after content commit.

`RETIRE_MISSING` requires consecutive complete-snapshot evidence and retirement count/ratio thresholds. It changes only the QualiBug Source Occurrence to `retired_remote_scope`, stops that occurrence from being treated as current knowledge, and retains historical bytes, chunks, interpretation records, and audit evidence. It never purges customer or internal source bytes.

## Reappearance

When the same stable remote identity returns, the missing counter resets, the lifecycle becomes `REAPPEARED`, and prior occurrence identity and history are preserved where possible. A new occurrence is created only when content or interpretation identity truly changes.

## Public projection

The connector inventory exposes bounded aggregate counts only: present, absent, unconfirmed missing, retirement eligible, internally retired, renamed, moved within scope, and reappeared.

It does not expose lifecycle remote IDs, Source Refs, customer content, credentials, raw cursors, internal paths, or arbitrary diagnostics. The frontend rejects the projection unless the server explicitly proves:

```text
remote_deletion_inferred = false
permission_loss_inferred = false
customer_material_mutation_executed = false
remote_resource_identities_returned = false
source_refs_returned = false
historical_source_bytes_retained = true
```

## Evidence persistence

Lifecycle evidence is attached to the existing Connector Sync Run receipt. No second source or lifecycle registry is created. If the receipt cannot be updated, lifecycle status becomes `PARTIAL_RECEIPT_NOT_PERSISTED` instead of reporting a complete reconciliation.
