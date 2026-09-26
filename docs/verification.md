# Verification record

This public record omits household names, entity/device identifiers, room
locations and test dates. Pilot mask names below are generic aliases; reported
test measurements and coverage limits are preserved.

## Automated baseline

Version 0.1.0: 32 tests passed against Home Assistant Core 2026.9.3 on a Windows
development host with Python 3.14.7. Ruff lint/format checks and strict mypy passed.
The tests use real Core services, entities, scenes, config subentries and temporary
storage, with an in-memory physical light. Five upstream deprecation warnings
were observed from Home Assistant HTTP and the backoff library.

These are not full Home Assistant process/startup tests. The upstream pytest
instance context is used directly because the standard HA pytest plugin imports
a Unix-only process runner.

## Revision 0.2.0 local verification

47 tests pass on the same Core 2026.9.3 / Python 3.14.7 Windows baseline.
Ruff lint/format and strict mypy pass. New tests exercise the real parent Options
Flow, not only configuration helper functions:

- Empty Configure menu and mask count; optional Power alias defaults off.
- Add/Edit/Remove with ordinary light devices, per-mask capabilities, independent
  activation, duplicate-priority/empty-channel/capability validation and confirmed
  removal of saved intent.
- Stable entity and device identities, active non-restoring masks and unchanged
  lease deadlines through configuration edits; manual reload still applies
  restore policy and expired activations never return.
- Integration-hidden mask visibility migration while preserving user-hidden
  choices, and continued support for legacy force-off subentries.
- Overlapping configuration reloads, old-worker shutdown, cleanup after failed
  reload, and preserved power-authorization/safety barriers.
- Compatible base replacement with retained intents/IDs, durable shadow mode,
  no replacement-triggered physical commands and explicit On authorization.
  Unsafe/incompatible/owned targets, storage failures and an ownership change
  during storage I/O are rejected.

The manual 0.2.0 archive contains the integration, local brand icon, documentation
and MIT license. The package builder checks archive integrity and writes a SHA-256
sidecar. GitHub Actions are configured for Linux tests, hassfest and HACS validation,
but those remote jobs have not run. HACS listing/publication is not established.
No 0.2.0 code has been deployed to the live instance.

## User-installed live pilot (0.1.0)

The user installed 0.1.0 on Home Assistant Core 2026.9.3, Linux x86_64 Container,
Python 3.14.6. The test parent had Normal and Power ports, Apply enabled, and no
masks. Normal and the connected physical light initially reported Off.

With explicit permission, two temporary appearance-only, power-transparent masks
were added: a lower-priority Circadian and a higher-priority Notification. Routine
light actions and the integration's Explain response were used to verify stored
intent, channel ownership, desired state and observed device feedback.

| Scenario | Evidence | Result |
|---|---|---|
| Shadow mode | Normal accepted On, brightness 64, 3200 K; physical output remained Off | Pass |
| Normal delivery | Apply On yielded brightness 64 and 3205 K against a 3200 K request | Pass |
| Priority and channel isolation | Notification green XY won; brightness remained 64 despite mask brightness 200 | Pass |
| Hidden lower update | Circadian changed to 4700 K; revision advanced from 4 to 5 while last-sent revision stayed 4 | Pass |
| Power Off with masks active | Physical output reported Off; mask activation remained On | Pass |
| Appearance update while dark | Notification changed to red; output stayed Off and last-sent revision did not advance | Pass |
| Power-only input | Supplied brightness 240 and 2700 K did not replace Normal brightness 64 or notification color | Pass |
| Notification release | Latest circadian 4700 K became desired; output reported 4716 K | Pass |
| Concurrent producer updates | Parallel Normal/Circadian/Notification requests converged to brightness 80 and notification green | Pass |
| Configuration reload (0.1 behavior) | Changing notification duration retained values and released both non-restoring activations | Pass |
| Automatic expiry | A 15-second notification lease deactivated automatically and exposed circadian 4300 K; output reported 4310 K at brightness 80 | Pass |
| Cleanup | Temporary subentries and virtual entities removed; parent remained loaded | Pass |

No matching Light Masks warnings/errors were returned by the structured system-log
query. Observed steady states were `in_sync`. This is not a transport-level packet
count or a latency benchmark.

Final state was verified against the initial baseline: Normal and physical output
Off; stored Normal brightness 255 and color temperature 2500 K; Apply enabled;
suspension false; power authorization true; no masks; `in_sync`; no diagnostic
error. Saved Normal attributes were restored in shadow mode without illuminating
the light at full brightness.

No household automations were edited and the HA process was not restarted.
Subentry operations did reload this integration's config entry.

Historical pilot archives predate the public-documentation privacy cleanup and
the management-service authorization fix. They are not approved release assets.
Generated archives are Git-ignored and are not included in a clone.

## Not established by the live pilot

- Independent visual or photometric confirmation.
- Full HA process restart, power-loss durability, storage fault injection, or
  device disconnect/reconnect behavior.
- External-control conflict behavior on this physical endpoint.
- Native effects, groups, RGBW/RGBWW, force-on/force-off masks, or other endpoints.
- Sustained animation/high-write-rate performance or long-running soak stability.

Some failure/recovery scenarios have automated coverage, but that does not replace
hardware qualification. Do not publish Home Assistant configuration exports,
credentials, device registries, runtime storage or unredacted diagnostics.
