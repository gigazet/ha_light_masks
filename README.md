# Light Masks

**0.2.0 - configurable integration with independent mask light devices.**

Repository: <https://github.com/gigazet/ha_light_masks>.

A Home Assistant custom integration that composes independent virtual lights onto
one existing physical light. Ordinary switches, circadian lighting and appliance
notifications keep using standard light actions; they target different virtual
entities instead of competing for the physical light.

The [specification](researches/light_masks_integration_specification.md) explains
the home-specific design and longer-term scope. This implementation is original
code informed by Layers and Lightener; neither integration is a dependency.
See [verification results](docs/verification.md) for the automated baseline and
the user-installed live pilot.

## How it works

Each configured physical output gets:

- **Normal:** persistent normal power, brightness and color intent.
- **Power:** an optional on/off-only port sharing Normal's power intent.
- **Masks:** independent virtual lights, each with a unique priority and channel
  permissions. Higher numbers win independently for power, brightness and color.
- **Apply to output**, **Resume**, and a **Delivery status** sensor.

A mask's On state means **active**, not necessarily visible or physically lit.
Mask Off releases its contributions; Normal/Power Off persists a normal Off request.
Hidden lower-priority settings continue updating. Releasing a notification exposes
the newest circadian color, not a stale scene snapshot.

Appearance-only masks ignore their stored brightness during composition. They
still accept brightness through normal light actions because HA color lights also
support brightness. Color is atomic: a winner contributes one color representation.
Unset mask fields are transparent. Off retains values for the next activation;
`light_masks.clear_fields` explicitly clears them.

Default masks leave power **transparent**. Checking **Control on/off** makes an
active mask contribute On at its priority; turning it Off releases that contribution,
not the underlying light. This can override the ordinary switch, so leave power
unchecked for routine appliance notifications. Existing 0.1 `force_off` masks keep
their behavior and expose a legacy checkbox when edited: On holds the output Off,
and Off releases it. New masks do not offer that inverted behavior.

## Compatibility and boundaries

The development baseline is **Home Assistant Core 2026.9.3 / Python 3.14.7**.
No earlier or later Core version is qualified. The integration uses native config
subentries and has no additional runtime pip requirements.

Supported outputs are individually addressed lights advertising only these modes:
`onoff`, `brightness`, `color_temp`, `xy`, `hs`, `rgb`.
Core performs normal light-service conversions, such as RGB requests to XY.
Only select channels the endpoint actually supports.

Not supported in this release:

- Groups, Lightener/Lightener Studio outputs, other masks, overlapping segment
  aliases, RGBW/RGBWW/white-mode endpoints, native effects, and flash actions.
- Cross-output synchronization, group broadcast planning, automatic automation
  migration, incompatible base-light conversion, or a visual composition preview.
- Attributing every hardware report to a human or a specific external automation.

Known aggregates and duplicate owners are rejected. Opaque vendor groups and
aliases cannot always be detected: setup requires explicit confirmation that the
output is independent. Do not configure a physical endpoint controlled by another
compositor, direct Zigbee binding, vendor effects or still-competing automations.
Supported native effects on the bulb are fine while inactive; reported active
effects block the compositor.

Masks are visible, ordinary light entities on individual devices, not Helper
entries. They are **not excluded from area/floor/domain-wide actions or voice
exposure** by this integration. Audit those targets and assistants before applying.
An untargeted `light.turn_on` can activate masks, including power overrides.
Use explicit virtual entity IDs; manage areas, labels and exposure yourself.

## Manual installation

1. Back up Home Assistant configuration. Build or obtain `light-masks-0.2.0.zip`
   (local builds are in `dist`) and extract it.
   Copy its `custom_components\light_masks` directory into the Home Assistant
   configuration directory's `custom_components` directory. Do not copy `.venv`,
   tests or the development dependency files into Home Assistant.
2. Restart Home Assistant when convenient. Installation and restart are manual.
3. Open **Settings > Devices & services > Add integration > Light Masks**.
   Select one available independent output and confirm its topology. Leave the
   optional Power alias unchecked unless switches need an on/off-only port that
   shares Normal's state.
4. Open this integration entry's **Configure** menu. Use **Add mask**, **Edit mask**
   and **Remove mask** to manage the number of masks. Each has a name, unique
   priority, **Control on/off**, **Control brightness**, and **Control color
   (RGB / color temperature)** checkboxes, restart restore policy and optional
   maximum duration. Native **Add mask / Configure mask** subentry actions remain
   available and use the same validation.

Initial setup is **shadow mode**: intent changes are stored but no hardware
commands are sent. Observed attributes seed Normal; missing attributes use the
configured brightness/CCT fallback or white for RGB-only endpoints. Kelvin is
clamped to the physical endpoint's advertised range.

English and Ukrainian UI resources are included. Entity IDs depend on the chosen
names; discover the generated IDs instead of copying guessed IDs from examples.
Power-only masks expose an on/off control; brightness-only masks expose dimming;
color masks expose the base's supported color modes. HA color modes also imply a
brightness control, but an unchecked brightness contribution remains local.

## HACS installation and repository

Once the repository is published and accessible to your HACS account, add
`https://github.com/gigazet/ha_light_masks` under **HACS > Custom repositories**,
category **Integration**, and download Light Masks. Restart HA, then follow setup
above. HACS installs the integration directory; the manual-install ZIP is not a
HACS `zip_release` asset. The minimum declared HA version is 2026.9.3.

The repository includes `hacs.json`, integration-local branding, an MIT license,
and GitHub Actions for tests, Ruff, mypy, hassfest and HACS validation. This does
not constitute a default HACS catalog listing or certification. Remote validation
still requires publication and appropriate repository metadata: description,
topics and issues enabled. Before releasing, run the checks below, run GitHub
validation, and create a version-matching tag/release. No publishing is performed
by the package builder.

## Configure and upgrade

**Configure > Base light and integration name** changes the parent name or output.
A compatible replacement keeps existing virtual entity/device IDs and saved
intents, but durably disables Apply and clears On authorization. Neither light is
commanded by the replacement itself; the previous output is left as-is. Review
the new output and enable Apply explicitly; Resume can authorize a pending On.
Disabling Apply does not undo a physical command already in flight.
The integration must be loaded to replace its base. Unsupported saved color
modes or selected channels are rejected, not silently converted or discarded.

Mask name, priority and channel edits preserve identity, independent values,
activation and current lease deadlines across a successful configuration reload.
Changed permissions and priority take effect immediately. Editing a name updates
the integration-provided device name; user-customized names/IDs remain yours.
Removing a mask requires confirmation, removes its device/entity and saved intent,
and reveals the current lower-priority result. References in automations, scenes
and dashboards are not rewritten.

To upgrade from 0.1, back up HA and replace the component files, then restart.
Keep the existing integration entry; config subentry IDs and storage format are
unchanged. Existing Power aliases and legacy force-off masks are retained.
Integration-hidden masks become visible; user-hidden masks stay hidden. The
upgrade restart still applies each mask's restore policy. **0.2.0 has not been
deployed or tested on the live pilot instance**; the previous live record covers
0.1.0 only.

## Safe commissioning

Start with one non-critical RGB endpoint. Review its consumers, group membership
and hardware behavior before commissioning; do not assume a mixed group is safe.

| Producer | Target | Priority | Appearance | Brightness | Power | Restore |
|---|---|---:|---|---|---|---|
| Wall switch | Power port | Normal baseline | No | No | Normal on/off | Normal intent persists |
| Circadian automation | Circadian mask | 10 | Yes | No | Transparent | Yes |
| Washer automation | Washer mask | 100 | Yes | No | Transparent | No |

Activate the Circadian mask while testing. A blueprint that updates only an On
target will then update its stored color even when normal room lighting is Off.
This changes the meaning of its target's On state: turning Normal on does not
trigger a mask-On automation. A blueprint whose only trigger is physical On needs
separate compatibility review; universal target-only migration is not promised.
Do not enable disabled automations implicitly.

A washer notification's color, 25% brightness and final Off can target
the Washer mask without adding arbitration logic. Appearance-only ownership means
the 25% brightness stays local to that mask and does not dim the room. Its final
Off releases the notification without switching the room off.

Exercise the masks manually in shadow mode and inspect `light_masks.explain`.
Audit and retarget every physical writer, group consumer and scene before enabling
Apply. Then verify on hardware: notification while lit, notification while dark,
switch Off during notification, lower color updates while hidden, notification
release, disconnect/reconnect and restart. Keep the original light available as a
manual escape hatch.

No household automations or blueprints have been changed.

## Persistence, leases and recovery

Accepted input mutations are serialized and written through HA Store using atomic
file replacement **before** virtual state publication. This includes appearance
updates; this release does not batch them. Frequent animation frames therefore produce disk
writes and are not the intended use. Filesystem/hardware power-loss durability is
not an fsync guarantee.

HA Store normally logs and swallows some write failures. `IntentStore` converts
those failures to explicit errors, and rejects acceptance during shutdown or
read-only operation. This small private-API adapter is covered against the pinned
Core version and must be reviewed when upgrading Core. A storage failure rejects
the command, blocks output and raises a Repair; fix storage, Resume, then resubmit
the rejected command.

Normal intent and operating mode persist. Mask values persist, but activation
after a **restart or manual reload** restores only when **Restore activation** is
enabled. Successful configuration-edit reloads instead preserve current activation
and deadlines, including other non-restoring masks. Expired leases never reactivate.
Same-output edits retain existing power-authorization and safety barriers.
Entity identities survive ordinary reloads; a failed reload does not leave a
state-preservation handoff for a later manual retry.

Duration **0 disables automatic expiry** in the UI. A positive duration sets an
absolute deadline and every accepted On renews it. Off cancels it; Clear Fields
does not renew it. Expired masks are removed before delivery, and a one-second
timer releases runtime expirations without polling the device. Expiry uses an
immediate transition request; actual device timing is not guaranteed. Editing
duration does not recalculate an existing retained lease.

Startup cannot replay a restored On into an observed-off output without new
authorization. An explicit normal On, an explicit advanced force-on activation,
or Resume authorizes On. Ordinary appearance-mask updates do not. An initial
unavailable endpoint delays integration setup; once loaded, virtual entities
continue accepting intents during outages.

| Control / status | Meaning |
|---|---|
| Apply Off / Shadow | Store and resolve only. Does not undo an already-dispatched command. |
| Apply On | Deliver current intent subject to startup and safety barriers. |
| Resume | Revalidate, clear suspension and authorize On. **May wake the light** when Apply is enabled. |
| Sync | Retry current result; does not clear suspension or authorize startup On. |
| Pending | Physical delivery or confirmation is underway. |
| In sync | The reported output matches the normalized desired state within tolerance. Not independent proof of emitted light. |
| Unverified | A call returned but no confirming state report arrived; automatic retries stop. |
| Failed | Bounded delivery failed, storage failed, or the endpoint became unsafe. Inspect diagnostics/Repairs. |
| Suspended after external change | Output changed unexpectedly while masks were active; intents remain intact and rendering pauses. |

One replace-latest writer serializes physical calls. Hidden changes cause no
additional output calls; superseded queued work is discarded. There are at most
three attempts per effective result, with transition-aware confirmation windows,
not unlimited retrying. Retries wait through the acknowledgement window; no
exponential backoff or separate 50 ms debounce is implemented.

An external settled change with no active masks can become the normal baseline.
With active masks it suspends output instead of copying notification color into
Normal. Changes during in-flight transitions cannot always be identified as
external; exclusive targeting and hardware qualification remain necessary.

An output entity rename is resolved by its registry ID **on reload**, not
immediately. A missing registry entry or incompatible stored color after a device
capability change fails visibly. Use Configure for a supported replacement while
the entry is loaded; an already-broken/unloaded entry must first be recovered.
Do not manually edit the integration's storage.

Scenes can snapshot ordinary mask state/values, but do not preserve unset-field
metadata or leases. Snapshotting an already-active mask can later restore it active.
Use dedicated masks per producer and prefer a straightforward final Off/release.

## Diagnostic actions

The integration exposes `light_masks.explain` (response data), `sync`, `resume`,
and `clear_fields` in Developer Tools > Actions. All select a Light Masks config
entry. Clear Fields additionally selects its mask entity and `appearance` and/or
`brightness`; it cannot clear Normal's fallback.

Explain and downloadable integration diagnostics report intents, channel winners,
desired/observed values, revisions, attempts and power authorization. Diagnostic
data is scoped to this compositor, not the entire home.

## Rollback

With Apply enabled and the output healthy, release masks and verify the desired
normal result. Turn Apply off, retarget consumers/scenes to the original physical
entity, then remove the Light Masks config entry. Removal deletes its stored
intent and virtual entities, not the physical light. It does not restore an old
scene or send hardware commands. Remove the custom component directory and restart
only after no remaining Light Masks entries or consumers need it.

## Development and verification

Use `uv sync --group ha` with the checked-in `uv.lock`, then:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\light_masks -q --timeout=20
.\.venv\Scripts\ruff.exe check custom_components tests scripts
.\.venv\Scripts\ruff.exe format --check custom_components tests scripts
.\.venv\Scripts\mypy.exe
.\.venv\Scripts\python.exe scripts\package_light_masks.py
```

The packaging command creates the manual-install ZIP and its SHA-256 checksum in
`dist`, excluding tests, caches and dependencies.

Tests use actual Core light services, entities, scenes, options flows, config subentries and
temporary Store files with an in-memory physical light. On this Windows development
host, the standard HA pytest plugin is disabled because it imports the Unix-only
process runner; its upstream test-instance context is used directly instead.
This is **not** a full Linux process/startup test or hardware qualification.

The suite covers arbitration, leases, atomic toggles, RGB-to-XY normalization,
producer-local relative brightness, hidden-update suppression, coalescing behind
blocked I/O, failed/no-report delivery, real atomic-write failures, startup power
protection, external-change suspension/adoption, mask lifecycle, reload cleanup,
scene restoration, action metadata and translation structure. Configure coverage
includes device visibility/capabilities, stable identities, independent retained
state, deadline preservation, confirmed removal, overlapping reloads, failed
reload cleanup and compatible base replacement with shadow-mode persistence.
The pure 32-mask
resolver is tested at p95 below 5 ms; this does not measure end-to-end disk/device
latency. The earlier user-installed 0.1.0 Linux Container pilot passed the live
checks documented in [verification results](docs/verification.md). Full process
restart, disconnect/crash recovery, visual confirmation and long soak tests remain
required before broader household adoption.

Relative brightness uses Core's standard preprocessing. Sequential steps operate
on the targeted producer, not the physical output. Concurrent relative steps to
the same virtual light can be normalized by Core before the intent lock; do not
treat them as an atomic counter. Separate producers should use separate masks.
