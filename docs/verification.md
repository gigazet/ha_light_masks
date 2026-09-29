# Verification record

This public record omits household names, entity/device identifiers, room
locations and test dates. Pilot mask names below are generic aliases; reported
test measurements and coverage limits are preserved.

## Revision 0.3.3 effect-reporting output compatibility

95 tests pass against Core 2026.9.3 / Python 3.14.7. Ruff lint/format and strict
mypy pass; the same five upstream deprecation warnings remain.

Real Core fixtures verify enrollment, shadow mode, setup, reload and static
delivery for individual lights and compatible HA groups with a retained effect
selection and no inactive-effect option. Runtime effect-only reports produce no
corrective commands. Mask priority/release, brightness, power and Resume continue
working while that metadata remains present; no effect parameter is forwarded.
Virtual lights do not advertise effect control. External power divergence with an
active mask still suspends output, and incompatible/unavailable group members
remain rejected.

This supersedes the active-effect rejection described in the historical 0.3.0
record below. Native effect control and guaranteed animation stopping remain
unsupported. These tests verify reported static channels, not physical animation
behavior. This revision has not been deployed or qualified on live hardware.

## Revision 0.3.2 security and publication verification

93 tests pass against Core 2026.9.3 / Python 3.14.7. Ruff lint/format and strict
mypy pass; the same five upstream deprecation warnings remain.

The management-service regression matrix covers Explain, Resume, Sync and Clear
Fields through real Core service dispatch. Non-admin, read-only and unknown
callers are rejected before any controller method runs. Administrators and
trusted internal calls without a user context remain supported. The fixture
creates a separate owner first so restricted test users cannot accidentally
inherit Core's first-user owner privileges.

The packaging regression checks the exact allowed output set and checksum, with
synthetic private research, verification, storage and IDE files excluded. The
0.3.2 archive contains the integration, README and license, not pilot records.
Historical ZIPs and checksum sidecars were preserved outside the repository's
release directory; they must not be published.

Household identifiers, names, locations and activity dates have been removed from
the current public documents. Git history and author metadata were not rewritten,
so publishing the existing history still requires a separate privacy decision.
These fixes have not been installed or exercised on live hardware.

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

## Revision 0.3.0 local verification

67 tests pass against Core 2026.9.3 / Python 3.14.7 on the same Windows host.
Ruff lint/format and strict mypy pass; the same five upstream deprecation warnings
remain. New coverage uses actual Core light-group config entries and service
forwarding to two in-memory physical lights:

- Optional setup name derived from the base; no independence checkbox.
- Immutable base enforced by the Configure schema, name-only edits, and preserved
  entity IDs, user-customized names, Apply and stored intent.
- Main control On/Off, brightness and color delivered to both group members;
  appearance-mask priority/release and no physical writes while the baseline is Off.
- Per-member convergence even when the aggregate reports the desired state.
  Missing feedback and a member reporting success-shaped state then raising an
  error do not produce a successful acknowledgement.
- External group-member divergence suspends output without active masks.
  Mixed On/Off startup does not wake off members without authorization.
- Member unavailability defers delivery; explicitly authorized intent resumes on
  recovery. Malformed member feedback blocks output without killing the writer.
- Incompatible color modes, Kelvin bounds and transition support, active native
  effects, unknown aggregates, cycles, repeated members and compositor children
  are rejected. Nested group topology and On/Off/brightness/color forwarding are
  exercised through real Core group services.
- Group/member and group/group ownership conflicts are rejected, including a
  disabled owner created while enrollment awaits a unique ID.
- Changed membership blocks further physical writes and config-entry reload.

The versioned manual-install archive includes source, translations, branding,
README, specification, this record and license. Packaging verifies ZIP integrity
and writes a SHA-256 sidecar. This revision has not been deployed to the live
instance or pushed by the agent. Group hardware behavior, network multicast and
physical simultaneity are not qualified by these tests.

## Revision 0.3.1 local verification

72 tests pass against Core 2026.9.3 / Python 3.14.7 on the same Windows host.
Ruff lint/format and strict mypy pass; the five upstream deprecation warnings
remain. This revision adds:

- Blank required names in both parent Configure and native subentry Add forms,
  including rejection of empty and whitespace-only input.
- Independent power-only, brightness-only and color-only light capabilities,
  with three visible read-only diagnostic permission sensors on each mask device.
- Correct sensor names, device/subentry associations, configured states while
  inactive, permission edits, stable IDs and legacy Off-requesting power states.
- Removal of a mask's sensor states and registry entries along with its light.
- Preservation of a running expiry deadline when duration changes to 0 or 120;
  an On update while active uses the new duration and renews or clears expiry.
- English/Ukrainian translation parity and loading of field descriptions,
  native editor labels and sensor states through Core's translation API.

The 0.3.1 manual-install archive includes the completed 0.3.0 changes and the
mask-editor improvements. Packaging checks ZIP integrity and writes a SHA-256
sidecar. This is local Core verification, not a live browser/device-card or
hardware test. At packaging time, nothing had been deployed, committed or pushed
for this revision. The subsequent user-installed live test is recorded below.

## User-installed live test (0.3.1)

The user reported installing the revision and requested testing the existing
test setup. Additional explicit permission covered temporarily enabling Apply
and changing the physical bulb's power, brightness and color.
The base was a single color-temperature/XY light, not a group.

The masks are described here as Circadian (priority 100, color only), Appliance
notification (101, power/brightness/color), and Attention notification (102,
power/brightness/color). All masks initially had empty stored values and were
Off, with no duration configured. Main control was Off with retained brightness
128 and 4000 K; Apply was Off.

| Scenario | Device feedback / diagnostics | Result |
|---|---|---|
| Shadow mode | Parallel Main control and Circadian On updated intent; base remained Off | Pass |
| Channel isolation | Main brightness 64 plus Circadian brightness 200 / 3000 K yielded physical brightness 64 / 3003 K | Pass |
| Color-only power isolation | Main control Off switched the base Off while Circadian remained active | Pass |
| Notification power | Appliance On woke the base despite Main control Off; green XY and brightness 80 matched | Pass |
| Higher-number priority | Attention 102 overrode Appliance 101 and Circadian 100; red XY / brightness 100 matched | Pass |
| Concurrent hidden updates | Main, Circadian and Appliance updates were retained; red output stayed unchanged, revision 6 to 9 with last-sent revision still 6 | Pass |
| Release highest mask | Attention Off revealed the updated blue Appliance intent at brightness 90 | Pass |
| Release notification | Appliance Off revealed latest Circadian 3500 K (reported 3508 K) and Main brightness 72, not Circadian brightness 220 | Pass |
| Release final mask | Circadian Off revealed restored Main control 4000 K / brightness 128 | Pass |
| Power alias | Power Off also set Main control and physical bulb Off | Pass |
| Permission indicators | Nine sensors showed configured permissions while masks were Off; Ukrainian names loaded; Circadian sensors shared its light device | Pass |
| Persistence and cleanup | After entry-only reload, all original intents, mask configuration, Apply Off and startup authorization false matched the initial Explain snapshot | Pass |

Every observed completed physical delivery reported `in_sync`, no diagnostic
error and one attempt. No matching entries were returned by the structured
system-log query. Kelvin rounding was within the integration's tolerance.
These are device-reported states, not independent visual/photometric or
transport-packet measurements.

All test mask values were cleared through the public service. The base, Main
control, Power alias and masks ended Off; Apply was restored Off; original Main
values were retained. The bulb's hidden previous Off-state color/brightness were
not exposed by HA and cannot be independently verified or restored. No mask
definitions, household automations or dashboards were changed. Only this config
entry was reloaded; Home Assistant was not restarted.

Not exercised in this live run: editor rendering, permission edits, mask CRUD,
expiry (all configured durations were disabled), group output, restart recovery
or fault injection. Their earlier local coverage is separate from this evidence.

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
