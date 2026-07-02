### Runtime-specific reasoning: Python (CPython)

The failing workload is a CPython service. Weigh these Python-specific patterns
when they match the evidence. Do not force a Python cause if signals point
elsewhere.

**Gunicorn / uWSGI worker timeout**
- Signals: `[CRITICAL] WORKER TIMEOUT (pid:N)`, workers killed and respawned,
  requests hanging then 502/504. A slow view or a blocking call exceeding the
  worker `timeout`.
- Category: `WorkerTimeout` / `LatencyRegression`. Recommend: find the slow
  handler, raise the worker timeout only as a stopgap, move blocking work off the
  request path, size `workers`/`threads` for the workload.

**GIL contention / CPU-bound blocking the event loop**
- Signals: high CPU on one core, request latency rising under load while other
  cores idle; asyncio event-loop starvation (`Task was destroyed but it is
  pending`, callbacks delayed). CPU-bound work inside async handlers.
- Category: `ResourceSaturation` / `EventLoopStall`. Recommend: move CPU-bound
  work to a process pool, scale out processes (not threads) for CPU-bound loads.

**Memory growth / leak**
- Signals: RSS climbing to the limit → `OOMKilled`, no `MemoryError` trace.
  Unbounded caches, references held in module globals, large request buffering.
- Category: `OOMKilled`. Recommend: `tracemalloc`/`objgraph` to find retention,
  bound caches, stream large payloads.

**Unhandled exception → crash loop**
- Signals: a Python traceback (`Traceback (most recent call last):`) ending in an
  exception class, process exit, `CrashLoopBackOff`. Often a missing env/config
  at startup (`KeyError`, `ImproperlyConfigured`).
- Category: `ApplicationError` / `ConfigError`. Recommend: fix the failing import
  /config; add a readiness gate so a bad config fails fast and visibly.

**What to ask for:** the full traceback, gunicorn/uwsgi logs (worker timeouts),
RSS trend, and whether the blocking is CPU-bound or I/O-bound. Prefer scaling
**processes** over threads for CPU-bound Python because of the GIL.
