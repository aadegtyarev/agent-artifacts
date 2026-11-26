# Durable local subscriptions

Use `artifacts watch` when a subscription must survive a harness restart.
The collector runs in an ordinary terminal on Windows, Linux and macOS. No
service is required. Keep that terminal open, or use a user supervisor if
you want automatic restarts. The utility does not install one. `wait` and
`wait --once` remain available for one-process watches and one-result scripts.

```
artifacts watch collect https://HOST/a/ID
artifacts watch status https://HOST/a/ID
artifacts watch follow https://HOST/a/ID
artifacts watch ack https://HOST/a/ID EVENT_ID
```

On Windows without an `artifacts` command on `PATH`, replace `artifacts` with
`python "C:\path\to\agent-artifacts\scripts\artifacts_cli.py"`. Open a
second terminal for the reader. `Ctrl+C` stops the reader or collector without
deleting its journal. Restart `collect` with the same URL to resume sampling.

Project URLs (`/p/ID`) also work. Collect uses the existing paired client
configuration and refuses a different URL origin. Use ordinary hostnames or
IP literals without percent escapes or IPv6 zone identifiers. Status, follow and ack need
only the full URL and local state. All processes must share `XDG_STATE_HOME`
(or the default state directory: `%LOCALAPPDATA%` on Windows and
`~/.local/state` elsewhere). Keep the journal on a private local drive;
on Windows it inherits the directory's access permissions. Collection
defaults to 5 seconds; use `--every SECONDS` to choose a longer interval.
Polling time and rate-limit retries can add to it. Only one collector per
scope can hold its lock.
Status reports lock ownership, poll health, timestamps and pending count; an
active lock alone is not evidence of a healthy poll. Temporary service failures
are retried. A permanent refusal stops the collector, records the reason and
leaves pending events in place.

Follow emits JSON lines containing stable `id` and compact `event` fields. It
prints each pending ID at most once per attach; reconnecting replays any that
remain unacknowledged. Output, reader exit, and successful transport are not
acknowledgment. Ack only after the consumer has durably accepted responsibility.
Events and checkpoints contain IDs and counts, not comment text or keys. The
underlying API is sampled, not an exact historical event stream. A receiver
performs `ack` only at its explicit, durable acceptance boundary; it does not
own the collector process.

The same commands are available as `artifacts_cli.py watch COMMAND ...`.
Use `watch pi URL --session SESSION_ID` to bind one Pi adapter per scope;
it checks the local mailbox every second, validates the selected live
session before each batch, and retries failures at five-second intervals. Status reports the active binding (or null after it
exits). The adapter never acknowledges delivery. Stop it before binding another
session; pending IDs replay on a new attach. `watch follow URL --timeout SECONDS`
ends a JSON-lines attach at its deadline without stopping the collector; a new
Monitor can attach and catch up. Follow only reads the journal. The deadline
also applies when the journal is locked or stdout is backed up. A deadline
can interrupt the final line; consume only complete JSON lines. The notice
goes to stderr when writable. Both readers are independent of the collector
and do not change legacy `wait` behavior.

When resuming an interrupted review, run `artifacts watch recover URL` to read
the numbers of open `working` threads and any pending Workspace reports. It
does not take, close, reassign or acknowledge them. On a project the file
list includes outstanding closure IDs, even for files without comments. It
fetches individual threads only for files reporting open comments, with
pacing between requests. Decide which work to resume in the receiving
session. When migrating a link, stop its previous watcher before starting
the supervised collector and receiver. Confirm the collector's last
successful poll, pending count, and Pi recipient with `watch status`.
Stopping or rebinding a process does not delete the local journal.

A `review_closed` notice contains the artifact and closing publication IDs
(and the project ID for a project watch). It is not an edit request. Confirm
it with `artifacts review-source ID`; use `review-evidence ID CLOSURE N` to
read a captured thread without later replies. The closing publication remains recoverable after an acknowledgment until the
server verifies the native Doc. Restart an old collector after updating the
skill so it can read the new event; keep its journal.
