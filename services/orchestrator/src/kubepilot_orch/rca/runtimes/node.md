### Runtime-specific reasoning: Node.js

The failing workload is a Node.js service. Weigh these Node-specific patterns when
they match the evidence. Do not force a Node cause if signals point elsewhere.

**Event-loop blocking / stall**
- Signals: p99 latency spikes while CPU is pinned on one core, health checks
  timing out, `blocked-at` / event-loop-lag alarms. A synchronous CPU-bound call
  (JSON of a huge payload, sync crypto, regex catastrophic backtracking) blocking
  the single-threaded loop.
- Category: `EventLoopStall` / `LatencyRegression`. Recommend: move CPU-bound work
  to a worker thread / worker pool, stream instead of buffering, fix pathological
  regexes; scale out processes (cluster) — Node is single-threaded per process.

**Heap OOM (V8 old space)**
- Signals: `FATAL ERROR: Reached heap limit Allocation failed - JavaScript heap
  out of memory`, or `OOMKilled` when RSS hits the container limit. Default V8
  old-space (~1.5–2GB) may cap below the container limit; or a genuine leak
  (unbounded arrays/maps, event-listener accumulation, closures retaining scope).
- Category: `OOMKilled`. Recommend: set `--max-old-space-size` in line with the
  container limit, heap-snapshot diff to find retention, remove listener leaks.

**Unhandled rejection / uncaught exception**
- Signals: `UnhandledPromiseRejection`, `uncaughtException`, process exit and
  `CrashLoopBackOff`. A rejected promise with no `.catch`, or throwing in an async
  callback.
- Category: `ApplicationError`. Recommend: fix the rejection site; add a
  process-level handler that logs + exits cleanly (don't swallow).

**Connection / socket exhaustion**
- Signals: `ECONNRESET`, `EMFILE` (too many open files), `ETIMEDOUT` to a
  downstream, pool exhausted. Missing keep-alive limits or leaked sockets.
- Category: `DependencyFailure` / `ResourceSaturation`. Recommend: bound the HTTP
  agent pool, add downstream timeouts, raise the fd ulimit if genuinely needed.

**What to ask for:** event-loop lag metric, a heap snapshot, the unhandled
rejection stack, and RSS vs `--max-old-space-size` vs the container limit. Because
Node is single-threaded per process, CPU-bound stalls present as latency, not as
even CPU spread — keep that distinction in the category.
