# Merge coordinator's checks

Run from a checkout's root. Each takes `--help`, reads only, and exits 2 with the reason when GitHub gave no answer.

| Command | Answers |
| --- | --- |
| `python3 -m scripts.coordinator.next_migration` | the next free migration number: above origin/main's highest and every open PR's |
| `python3 -m scripts.coordinator.light_check <pr>` | head SHA, mergeable, draft, light, checks, sensitive files, numbered migrations and their clashes |
| `python3 -m scripts.coordinator.deploy_status <sha>` | every Railway commit status; flags a non-success and a "No deployment needed" |
| `scripts/coordinator/engine_reading.sql` | paste into the production SQL tool: one row, read only (unread per kraj and total, merges in the last hour, cap, schema MB, brake, lease). CI's SQL sweep PREPAREs it against the schema |

## Reading the lane

Railway logs, realtime-worker `sreality`, filter:

```
"AUTODEDUP lane claimed" OR "autodedup lane pass failed" OR "claim cap halved"
```

Railway metric: `MEMORY_USAGE_GB`.

- **`claimed`** runs above the cap by design: a build pass takes the build feed up to the cap plus six feeds at `cap//5`.
- **`bound_by=time`** after a killed pass is by design: the kill halved the rate the time bound is cut from.
- **Lease holder** `<hostname>:<pid>:<epoch>`: the last field is the pass's start second, not the process's boot.
- **`brake_interval_seconds`** 0 means the brake is on: the lane does not run.
