# Git checkpoints

Each implementation commit represents one logical step. Tests belong with the behavior they verify. Documentation is committed separately after the working demo has been checked.

## Available code versions

| Commit | Capability | Verification |
|---|---|---|
| `96a2c9f` | Week one: event API, authentication, and idempotency | 9 API tests |
| `e569bf4` | Redis broker and Celery worker configuration | 9 tests, Django checks, Celery imports, Compose configuration |
| `469c185` | HTTP delivery, attempt history, and bounded retries | 37 tests, Django checks, migration consistency |
| `badaf05` | Queue new events after commit; preserve pending events on broker failure | 43 tests |
| `0af4982` | Receiver that fails twice and succeeds on the third attempt | Live Compose demo: 503, 503, 200; duplicate jobs; replay and conflict checks |

At the delivery checkpoint, tasks can be queued manually. Automatic publication from the event API is introduced in the following commit. The demo receiver is introduced after that.

## Inspect an earlier version

First check that your working tree is clean:

```bash
git status
git log --oneline --decorate
```

Open a checkpoint without moving the development branch:

```bash
git switch --detach 469c185
```

Return to the current implementation:

```bash
git switch week-2-delivery
```

Detached HEAD is useful for inspection. If you want to make changes from that checkpoint, create a branch before committing:

```bash
git switch -c fix/retry-policy 469c185
```

## Undo a committed change

For a shared branch, use a new commit that reverses the change:

```bash
git revert COMMIT_SHA
```

Review dependencies first. A later commit may rely on code added earlier; related commits should be reverted from newest to oldest. Run the relevant tests before publishing the result. Avoid rewriting published history to undo a feature.

## Code and data are separate

Switching or reverting a commit changes tracked files. It does not restore `.env`, the virtual environment, running Docker images, or PostgreSQL and Redis volumes.

Before running an earlier checkpoint, install its dependencies and rebuild its Docker image. Use a separate database for experiments with older schemas, or plan the required migration changes. Migration `0002` adds `Event.next_attempt_at`; switching to week one does not remove that column or reset saved events. Reversing that migration would remove saved retry timestamps.

## Publish and merge

Push the branch when you are ready:

```bash
git push -u origin week-2-delivery
```

Merge the week one pull request into `main` first, then open the week two pull request against `main`. Choose **Create a merge commit** to preserve these separate commits and their IDs in the main branch history.
