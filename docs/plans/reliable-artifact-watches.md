# Reliable feedback subscriptions

Status: local collector, readers and unfinished-work recovery implemented. A disposable test passed Pi exit and rebinding, Claude Monitor reattachment and transient supervision of a killed collector. The live watcher and installation are unchanged.

## What failed

- An installed watch exited after a restart while its process manager still displayed `ready`. Pi's managed processes belong to its session; `ready` describes startup, not health.
- On the latest real notification, the project watch did not exit: it reported desktop comments #13–16 about eight seconds after publication. The receiving session had only the Google lane and left them waiting. Delivery and assignment are separate checks.
- The current continuous poller keeps its baseline and deduplication state in memory. On a disposable instance, resolving a comment while the watch was down was visible to the old poller but not a new one. A thread moved to `working` by `take` remained unfinished in the database but was not reported by a new watch.
- One failed Pi notification ended the poller; its status kept the error but not the event that needed retry. Repeated `503` responses were indistinguishable from a quiet watch until timeout.

## Design

One durable subscription per file or project. The utility, not the currently running Pi task or Claude Monitor, owns its checkpoint and pending deliveries. Use the existing owner API and pacing; do not poll every file on every tick. Keep `wait ID --once` compatible.

1. Persist the successful poll checkpoint and compact outgoing event IDs/counts atomically. Do not persist keys, comment bodies or reviewer names. State lives under the user's private state directory and belongs to the service origin and scope, not a process ID. A process restart restores it rather than taking a new baseline. Events await acknowledgment; crashes before acknowledgment can replay the same ID. A notice is not an acknowledgment that someone applied the review.
2. Keep polling and collecting when delivery fails. Pi and Claude are consumers of that stream, not owners of its progress. A consumer can reconnect or bind a different live session explicitly. A quiet subscription reports last successful poll, pending delivery count, last failure and current destination. Retry transient failure without sending the same notice at every poll.
3. Make recovery independent of harness lifetime. Verify running the poller under a supervisor owned by the utility instead of `start_process`. After a harness closes there is nowhere to send it; keep the backlog and reattach when it returns. Claude Monitor may expire; its reader can reconnect without resetting the watch. Do not install a new background service on the operator's machine without approval.
4. On recovery show open unfinished work, including `working` threads left by an interrupted review; do not automatically take, resolve or duplicate work assigned to another session. Delivery to a session outside the work's scope must not look like work handled.

The current API samples state, not every transition. Pending work and retained resolutions must be recoverable; resolution and deletion between polls cannot be reconstructed from the existing counts. A durable server event API would be a separate change if an exact historical audit is required.

## Check before switching

- Publish two comments through the existing review flow. Observe both through one live Pi receiver and one live Claude Monitor in separate runs. Reattach after a Monitor deadline and verify catch-up without a new baseline.
- Kill the poller after a successful fetch and before delivery, then restart it. Verify the outgoing event is still pending; force a Pi broker failure and recover without killing the poller. After acknowledgment, restart again and verify no repeat.
- Start a fresh session with a review interrupted after `take`. Show the unfinished thread in recovery without changing its server state.
- Force several API `503`/`429` responses. If the summary fails, the checkpoint stays put. If only a detail fails, keep successful details and approvals, show degraded health, and resume at the throttled file on a detail `429`. Queued events remain in both cases. Resume ordinary polling without flooding the API.
- Stop and restart a harness while a supervised watch is running. Confirm a detach does not lose comments and the destination shown at reconnection is the intended session. Prove the old and new watchers are not both running on the live project when switching.
