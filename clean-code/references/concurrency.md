# Concurrency

Read this when your code involves threads, async work, shared state, or potential race conditions.

> Concurrency is a decoupling strategy — it separates what gets done from when it gets done.

## Why It Matters and Why It's Hard

Decoupling *what* from *when* can dramatically improve throughput and structure — but it comes at a cost. Be honest about the trade-offs.

- **Concurrency doesn't always improve performance** — it only helps when there's real wait time to share across threads, or independent work to run in parallel.
- **It adds overhead** — both runtime cost (coordination, context switching) and design cost (the code is harder to reason about).
- **Correct concurrent design is hard** — even simple problems gain many possible execution paths, most of which you never picture.
- **Bugs aren't repeatable** — threading defects appear intermittently, so they get dismissed as "one-offs," cosmic rays, or glitches. They are not. Treat every spurious failure as a real defect until proven otherwise.

## Defense Principles

Strategies to keep concurrent code correct and maintainable.

- **Single Responsibility Principle** — keep concurrency code separate from the rest. It is complex enough to be its own reason to change; don't tangle thread management into business logic.
- **Limit the scope of data** — severely restrict access to any shared, mutable data. Guard critical sections with `synchronized`/locks, and keep the number of critical sections as small as possible. The more places that touch shared data, the more places you can get it wrong.
- **Use copies of data** — avoid sharing entirely where you can. Copy objects and treat them as read-only, or let each thread collect results into its own copy and merge afterward. The cost of copying is often far cheaper than the locking (and the bugs) it avoids.
- **Threads should be as independent as possible** — give each thread its own data from an independent source, with no shared state. Partition data into independent subsets that can be processed without coordination.

## Know Your Library

Use what the platform already got right instead of hand-rolling locks.

- **Thread-safe collections** — prefer concurrent collections (e.g. `ConcurrentHashMap`) over manually synchronizing standard ones.
- **Executor framework / thread pools** — use them to manage thread lifecycles and queuing rather than spawning raw threads.
- **Nonblocking solutions** — atomic primitives (compare-and-swap, atomic variables) often outperform locks for simple shared state.
- **Not all classes are thread-safe** — read the docs. Many library classes are explicitly *not* safe for concurrent use.

## Know Your Execution Models

Recognize the standard problems and the terms that describe what goes wrong.

- **Producer/Consumer** — producers create work onto a bound queue; consumers take it off. They coordinate on queue space and availability.
- **Readers/Writers** — many readers share data that writers occasionally update. The challenge is letting reads proceed without starving writers (or vice versa).
- **Dining Philosophers** — competitors contend for shared resources; naive resource acquisition leads to deadlock or starvation.

Key failure modes:

- **Bound resources** — fixed-size resources (connections, buffer slots) used in a concurrent environment; you must handle exhaustion.
- **Mutual exclusion** — only one thread may access a shared resource at a time.
- **Starvation** — a thread (or group) is perpetually denied access to what it needs.
- **Deadlock** — threads wait on each other forever, none able to proceed.
- **Livelock** — threads keep reacting to each other and making no progress, busy but stuck.

## Beware Synchronization Dependencies

- Dependencies between synchronized methods cause subtle bugs. Avoid using more than one method on a shared object.
- When you must use more than one, prefer client-based or server-based locking that treats the calls as a single atomic operation.
- Keep synchronized sections as small as possible — lock the least amount of code for the shortest time. Large critical sections increase contention and the chance of deadlock.

## Write Correct Shut-Down Code

- Graceful shutdown is hard to get right. Coordinating many threads to stop cleanly invites lost messages and orphaned work.
- **Deadlock on shutdown** is common — threads waiting for signals that never come because the producer already quit. Think about shutdown early; don't bolt it on at the end.

## Testing Threaded Code

You cannot prove concurrent code correct, but you can make defects far more likely to surface.

- **Treat spurious failures as candidate threading issues** — never write off an intermittent failure as a one-off. Investigate every one.
- **Get the non-threaded code working first** — don't chase threading bugs that are actually plain logic bugs. Make the single-threaded base solid.
- **Make threaded code pluggable and tunable** — let thread counts, configurations, and timing be adjustable so you can test many scenarios.
- **Run with more threads than processors** — frequent task swapping exposes timing bugs.
- **Run on different platforms** — thread scheduling differs across OSes and JVMs; test on the ones you target, early and often.
- **Instrument the code to force failures (jiggling)** — insert `sleep`, `yield`, or `priority` calls (manually or via tooling) to perturb execution order and shake loose hidden races.
