# Keep version-aware NexHealth routing during v3 migration

During the NexHealth v3 migration, configuration will normalize raw version labels to an internal API contract target and startup will reject unknown values. `v2`, `v2.2.2`, and `legacy_v2` select the legacy target; `v3`, `v3.0.0`, `v20240412`, and `stable_v3` select the stable target. Headers and renamed scheduling paths will be derived from the normalized target so production cannot send contradictory version selectors.

Rollback from v3 REST to v2 will be an intentional deploy/restart config change, not a dynamic database flag. The adapter will keep temporary version-aware routing so slot search and working-window management can return to the v2 paths without reverting unrelated parser and mapper changes. The extra branching is migration scaffolding and should be removed after one stable production release cycle on v3.
