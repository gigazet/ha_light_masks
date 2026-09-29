# Light Masks: specification and implementation plan

**Status:** 0.3.3 local implementation; anonymized live evidence covers 0.1.0 and 0.3.1.
Full hardware qualification remains incomplete; see [verification results](../docs/verification.md).
**Target baseline:** Home Assistant Core 2026.9.3.
**Working name / domain:** Light Masks / `light_masks`; check naming availability before publishing.

## Implementation checkpoint: 0.3.3

Base lights and compatible group members may report a native effect without
blocking enrollment, setup, reload or Resume. That metadata may be a retained
write-only selection rather than a running animation. Static power, brightness
and color composition remains unchanged; effect-only reports are not reconciled.
Native effects are not selected, stopped or restored by masks, and reported
static-channel convergence does not prove a physical effect has stopped.
The qualified effect-composition design in section 7.2 remains deferred.

## Implementation checkpoint: 0.3.2

Entry-wide management services require administrator permission for user-context
calls, using Home Assistant's native admin-service wrapper. Trusted internal
calls without a user context remain available. Ordinary entity actions retain
Core's entity permission checks.

Public documentation uses generic examples rather than household inventory.
Installation packages exclude research and verification records. Older Git
history and historical release assets require separate review before publication;
sanitizing the current tree does not remove earlier disclosures.

## Implementation checkpoint: 0.3.1

Both parent Configure and native mask subentry editors require an explicitly
entered name, and explain power contributions, separate brightness/color
permissions, higher-number priority and timer renewal beside the fields.
Saving duration preserves a running deadline; the next On command, including
an update while active, uses the new duration. Legacy Off-requesting behavior is
explained next to its compatibility-only checkbox rather than in every edit form.

Each mask device exposes three read-only diagnostic sensors for its configured
power, brightness and color permissions. They share the light's device/subentry
and lifecycle, remain informative while the mask is inactive, and do not change
arbitration or persistence. Power distinguishes transparent, On and legacy Off
contributions. The normal HA color card's brightness control cannot be hidden
independently, so the editor explains when it is local-only.

## Historical implementation checkpoint: 0.3.0

The base is now immutable after enrollment. Configure edits its display name only;
setup Name is optional and defaults to the base's display name. The independence
acknowledgement is removed. **Main control** replaces the default name Normal:
use this baseline virtual entity in ordinary lighting automations, and separate
masks for temporary channel overrides. Existing entity IDs, custom names, stored
intent and the internal `normal` key remain unchanged.

Registered HA light groups (including nested groups) are supported when every
member has identical static color modes, Kelvin bounds and transition support.
Unknown aggregates, repeated/cyclic members, compositor outputs and overlapping
ownership are rejected. Persisted member identities protect against topology
changes after reload. Delivery still goes through the HA group service; every
leaf must report the desired state before confirmation. This is not atomic
hardware multicast or per-member scene restoration. Group-member divergence
suspends output rather than adopting an aggregate average. Mixed startup power
needs explicit On authorization; newly issued Main control On or Resume can
unify the members. An already-dispatched group command cannot be recalled.

The README defines the current supported contract. Group hardware qualification
and broader design targets below remain separate from local Core-boundary tests.

## Historical implementation checkpoint: 0.2.0

That revision added the integration entry's Configure menu for base-light
selection and mask Add/Edit/Remove operations, with a visible mask count. Each
mask is a visible, independent light device, not a Helper. Boolean checkboxes
select power, brightness and color contributions; priority remains unique per
output. Controls reflect selected channels and base capabilities. HA color modes
also expose dimming, but unchecked brightness ownership never affects the output.

The confirmed power contract is **Off releases**. A checked power contribution
asserts On only while the mask is active. Existing force-off masks are preserved
as a legacy option, not offered for new masks. New setups default to Normal only;
the optional Power port remains an explicitly shared-state alias.

Successful configuration edits preserve identities, activation, saved values and
existing deadlines, including non-restoring masks. Manual reload/restart still
applies restore policy. Configuration reloads are serialized and use a temporary
handoff because Core deletes runtime data during unload. Same-output edits retain
safety and On-authorization barriers; expired leases are always released.

Compatible base replacement preserves virtual identities/intents and durably
turns Apply off before committing the new target. Existing colors and permissions
must be representable; ownership is rechecked after storage I/O. The entry must
be loaded. The old output is left untouched and the replacement requires explicit
review/apply/On authorization. Incompatible conversion remains deferred.

HACS metadata, local branding, MIT licensing, reproducible packaging and
test/lint/type/hassfest/HACS workflows are included. Remote validation/publication
and live 0.2.0 qualification are not claimed. Refer to the README for the current
installation contract; the broader design targets below include deferred work,
such as presets, visual previews and atomic priority swaps.

## Historical implementation checkpoint: 0.1.0

The implementation is in `custom_components/light_masks`; setup and pilot guidance
are in [README.md](../README.md). The remaining sections retain the broader design
and acceptance targets, not a claim that every feature below has shipped.

Implemented: individual-output enrollment, normal/power ports, native mask
subentries, channel-priority composition, independent virtual intent, atomic
toggle, durable serialized mutation, restore policy and absolute leases,
shadow/apply, startup On protection, replace-latest physical writer, bounded
confirmation/retries, external-change suspension, Repairs, diagnostic actions,
English/Ukrainian resources and real Core-boundary tests.

The original pilot's differences and deferred work (some superseded by 0.2.0):

- Individual static lights only; group planning, Lightener topology, RGBW/RGBWW,
  effects and mixed-capability distribution remain deferred.
- Every accepted mutation is persisted immediately with atomic replacement,
  including appearance updates; the proposed five-second batched persistence
  window is not used. HA Store's swallowed write errors are explicitly propagated
  by a pinned-version adapter. This is not a hardware power-loss/fsync guarantee.
- UI duration `0` means expiry disabled; positive runtime leases remain strictly
  positive. This replaces the proposed rejection of a zero UI duration.
- Mask edits reload the parent and release masks without restore enabled.
  Parent-output reconfiguration and composition/removal previews are deferred.
- No separate 50 ms debounce or exponential retry backoff; the writer coalesces
  while busy and waits through bounded acknowledgement windows.
- An explicit advanced force-on mask activation authorizes On at runtime; restored
  activation alone never bypasses the observed-off startup barrier.
- Initial unavailable outputs delay config-entry setup. Already-loaded virtual
  entities remain usable during an outage. Registry-ID rename resolution occurs
  on reload, not immediately.
- Input validation errors are explicit but not all runtime error messages are
  localized. Hidden mask entities are not an area/voice exclusion guarantee.
- Core 2026.9.3 service/config/scene boundaries are exercised locally using an
  in-memory physical light. Full Linux startup, crash/soak testing, migrations
  beyond store version 1, and hardware qualification remain outstanding.

The subsequent live pilot on Linux exercised mask priority, power isolation,
concurrent inputs, expiry and config-entry reload. It did not exercise full HA
process startup, outages or fault injection. Examples below describe generic
automation patterns rather than a household inventory. They are not runtime
dependencies or a claim of compatibility with every blueprint.

## 1. Decision summary

Build a local, entity-first light compositor. An existing light is the output endpoint. The integration exposes a normal-control light and multiple independently addressable mask lights above that endpoint. Automations continue to use ordinary `light.turn_on`, `light.turn_off`, scenes, and light selectors.

Each mask has its own activation state, stored settings, priority, and permission to affect particular output channels. Arbitration is **per channel**, not a choice of one complete light state. A color notification can therefore override circadian color without taking ownership of brightness or power.

Two requirements were explicitly confirmed:

- Adoption may change automation targets or blueprint light inputs. Automation logic should remain unchanged wherever its existing contract permits.
- Appliance notifications default to **color only, visible only while normal lighting is on**. They must not wake a dark room or prevent a wall switch from turning the light off.

The integration owns arbitration, not appliance logic, sun calculations, presence detection, or brightness curves. It does not intercept Home Assistant's global light services, infer ownership from automation names, or rewrite automations.

**Important distinction:** an inactive mask is transparent; it is not an off command. A separate normal-control entity must retain ordinary, persistent on/off semantics. Without that distinction, switching off the final mask could unexpectedly expose an underlying on state.

## 2. Design rationale and prior art

### 2.1 Generalized design inputs

These are generic requirements and synthetic examples, not facts about a
particular installation.

| Requirement or example | Design consequence |
|---|---|
| Local-first control; ordinary lights can communicate events | No cloud dependency and an explicit manual escape path |
| Distinct everyday controls, independent producers and diagnostics | Separate Main control from mask entities and diagnostic sensors |
| Configured timezones may differ | Use UTC for persisted deadlines |
| A group can mix a switch-controlled light with color lights | A convenient group name does not imply compatible capabilities |
| A light may accept XY and CCT but not RGB directly | Inspect supported modes and normalize supported colors |
| An appliance automation sends On-with-color followed by Off | Retarget to its own mask so final Off releases the notification |
| An automation sends only a mobile notification | No lighting migration is needed |
| Existing automations may be intentionally disabled | Never enable them implicitly during migration |
| A circadian automation reads its target state and triggers on target On | Distinguish mask activation from physical illumination; see section 8 |
| A notification automation uses scenes or RGB values to track its state | Snapshot its dedicated mask, never the shared physical output |
| Manual control may carry unwanted brightness/color arguments | Offer a power-only alias while retaining the normal controller's bypass contract |

Compatibility must be checked against each installation's actual consumers and
device behavior rather than inferred from these examples.

### 2.2 Relationship to Layers and Lightener

| Topic | Layers | Lightener | Proposed Light Masks |
|---|---|---|---|
| Main abstraction | Runtime layers submitted through `layers.set/clear` | One virtual light distributes brightness across several lights | Persistent virtual light entities represent independent producers |
| Automation API | Integration-specific actions | Standard light actions | Standard light actions for routine use |
| Arbitration | Priority fold with inherited brightness/color; `adjust` and `follow` modes | Brightness mapping, not competing-intent arbitration | Explicit channel ownership, separate activation/power, stable per-mask priorities |
| Feedback | Base/layers maintained around external changes | Virtual state derived partly from child states and preferred brightness | Producer intent remains independent of physical observations |
| Clear / Off | Clear removes a layer; set-off can hold output off | Off turns controlled lights off | Mask Off releases; normal-control Off persists |
| Distribution | Expands target light groups into members | Applies per-child curves | Separate endpoint planner; preserves group broadcasts where safe |

Layers already supports inherited attributes and atomic color replacement. **Per-channel composition alone is not a novel difference.** The substantial change is the entity-based producer API, persistent mask configuration, explicit channel filtering, and separation of producer state from the rendered result.

Adopt the useful ideas, not the implementation wholesale: live intents instead of stale scene restoration, a pure resolver, shadow mode, delivery diagnostics, and Lightener's familiar virtual-light setup experience. Implement independently; review licensing before reusing any upstream code.

## 3. Goals, boundaries, and invariants

### 3.1 Required outcomes

1. Independent notification, circadian, and normal-lighting producers can operate concurrently without knowing about each other.
2. A producer's updates remain current while a higher-priority producer hides them.
3. Removing a mask reveals the **current** lower-priority result, not a snapshot taken when the mask started.
4. Color-only and brightness-only masks never turn an off endpoint on.
5. A normal power-off wins against every power-transparent mask, irrespective of its priority.
6. Ordinary automation actions remain usable after retargeting, with documented exceptions for state-reading and scene-based logic.
7. Every physical endpoint has one renderer. Dependency cycles and known overlapping ownership are rejected.
8. Reloads, retries, and late reports cannot resurrect expired masks or obsolete output revisions.

### 3.2 Explicit non-goals

- No automatic identification of washer, person, automation, or intent from service context.
- No global replacement of `light.turn_on`, monkey-patching of entity methods, or interception of scene internals.
- No built-in circadian schedule, appliance-state machine, motion controller, or Lightener brightness-curve editor.
- No automatic migration, renaming, group rewrite, dashboard edit, or voice exposure.
- No claim that all third-party light integrations support precise acknowledgements, cancellation, transitions, or effect stopping.
- No arbitration between two writers that deliberately use the same input entity. Those writers still share one intent.
- No preservation of arbitrary heterogeneous member states when a group is explicitly enrolled as one indivisible output.

## 4. Entity and configuration model

### 4.1 Vocabulary

- **Output endpoint:** the existing light being controlled. It is never replaced or renamed automatically.
- **Normal intent:** the persistent underlying requested power, brightness, and appearance.
- **Normal-control light:** the ordinary virtual light used for normal lighting.
- **Mask light:** an independently active overlay with a channel mask and priority.
- **Composed result:** what should be shown after arbitration.
- **Observed result:** what the underlying integration currently reports.

One mask light is one layer. Do not introduce another layer/mask hierarchy in v1.

Example entity names below are illustrative, not existing entities:

| Entity | Meaning of On / Off | Role |
|---|---|---|
| `light.kitchen_light_control` | Normal requested power on / off | Existing combined motion/brightness/CCT controller or ordinary UI |
| `light.kitchen_light_power` | The same normal requested power on / off | Optional power-only port for wall-switch automations |
| `light.kitchen_circadian_mask` | Include / exclude circadian intent | Color producer, normally left active |
| `light.kitchen_washer_mask` | Include / exclude washer intent | Independent notification |
| `light.kitchen_dishwasher_mask` | Include / exclude dishwasher intent | Independent notification |
| `switch.kitchen_light_masks_apply` | Apply / shadow | Commissioning and pause of physical writes |
| `sensor.kitchen_light_masks_status` | Delivery/operating status | Diagnostic, not an illumination sensor |

The two normal ports share one backing normal intent; they are not independent masks. Off through either normal port persists until a later normal On. The power-only port ignores brightness/color by contract and exposes only `ColorMode.ONOFF`; Home Assistant may strip unsupported service arguments before the entity handler receives them.

Create the full normal-control light by default. Make the power-only port opt-in to avoid unnecessary entities. Both normal ports report the same logical power state. A mask activation or color change must not change their on-state.

### 4.2 Configuration storage

Use one config entry per composed output, with a native config subentry per mask on the selected Core baseline. Normal and power-port entities belong to the parent entry. Mask UUIDs are stable and independent of names and z-order.

Configuration holds:

| Scope | Fields |
|---|---|
| Parent | Output registry reference; endpoint policy; optional power-only port; explicit normal fallback values; apply mode; supported delivery policy |
| Mask | UUID; name; integer z-order; owned channels; power contribution; initial preset; activation restore policy; optional maximum active duration; default transitions |
| Runtime store | Normal intent; each mask's last accepted values and activation; absolute expiration; logical revision; suspension state |

Use Home Assistant's supported config-entry APIs and `homeassistant.helpers.storage.Store`. Never edit `.storage` directly. Version configuration and runtime storage separately. The runtime store, not a second RestoreEntity snapshot, is the authoritative persisted intent.

At first setup, offer to seed normal intent from a valid current observation while no ownership exists. Show the proposed values before accepting them. If the output is off and appearance is unknown, require explicit fallback brightness/color for channels the integration will need to restore; do not invent an apparent historical state.

Retain the registry entity identity where available and resolve the current entity ID. Track renames using registry events. Removed/recreated entities must require re-selection rather than silently rebinding to a coincidentally reused name. For entities without a registry identity, clearly mark the weaker name-based binding.

### 4.3 Mask options

| Option | Values / default | Meaning |
|---|---|---|
| Z-order | Integer 1-1000 | Larger wins, relative to other masks on the same output |
| Affect brightness | Boolean, false for notifications | Replace underlying brightness when active and a value is set |
| Affect appearance | Boolean, true for notifications | Replace color or a qualified effect |
| Power contribution | `transparent` by default; advanced `force_on` / `force_off` | Active mask may leave power alone, assert On, or assert Off |
| Initial preset | Optional valid light values | Values used on a bare first activation |
| Restore active state | `inactive` for notifications; `restore` for continuous adaptation | Behavior after HA restart |
| Maximum active duration | Optional positive duration | Safety lease, not an appliance-state timer |
| Transition defaults | Activation/update/release seconds | Fallback when the initiating action has no transition |

Reject duplicate z-orders within a parent entry. This avoids activation-time tie breakers and makes reload behavior deterministic. Reordering is an atomic configuration transaction, including swaps.

Reject masks with no possible output contribution. An unset channel is transparent even when enabled in configuration. Turning a mask off preserves its last values for reuse but removes **all** of its contributions.

Force-power masks are advanced opt-ins with a warning: a higher-priority force-on mask can keep a light on after normal Off. The default notification policy does not use that behavior. An active force-off mask is still shown as On because its On means "this mask is active."

## 5. Standard light API contract

### 5.1 Normal-control entities

- `light.turn_on`: set normal power On and update only provided normal values. Keep omitted values.
- `light.turn_off`: set normal power Off; preserve normal appearance/brightness for the next On.
- `light.toggle`: atomically invert normal requested power, not observed physical power.
- Power-only port: modify normal power only, even if a legacy switch action carries color/brightness settings.

Normal-control state and attributes describe **normal intent**, not notification overrides. Otherwise a notification would feed its own color/brightness back into the normal controller.

Existing manual and motion controllers may share the normal intent while continuing to use their existing bypass logic. This integration does not infer "human wins" from context or replace that bypass contract.

### 5.2 Mask entities

| Input | Result |
|---|---|
| `light.turn_on` with values | Update mask-local values and activate it |
| Bare `light.turn_on` | Activate using stored values/preset; renew lease if configured |
| `light.turn_off` | Deactivate; recompute lower layers; never interpret as force-off |
| `light.toggle` | Atomically toggle activation, not physical illumination |
| Repeated On while hidden | Update its own intent; keep it available for later reveal |
| Positive brightness change | Update mask-local brightness; output changes only if brightness ownership is enabled |
| Brightness zero | Treat as mask Off, including Core's zero-brightness normalization path |

Mask light on-state always means **active**, even while the physical output is off or another mask wins. Mask attributes describe its own last intent, not someone else's rendered color.

Separate input storage from output permissions. For example, a color-capable HA light necessarily also offers brightness controls; a color-only mask can retain supplied brightness for standard API compatibility while filtering it out of the composed output. The UI must say "brightness is stored but does not affect the output" and diagnostics must expose excluded channels. Do not let a default brightness added by HA become output ownership.

Derive valid supported color modes from the endpoint and the mask role. Use Core's color conversion and light-service normalization; do not override standard state-attribute keys. Keep unset ownership distinct from any display preset required to represent a valid color mode.

Relative brightness actions operate on that producer's own stored brightness, never the observed notification brightness. Test Core's normalization of `brightness_step` and percentage forms at the service boundary. Override toggle at the entity boundary so concurrent calls serialize against the authoritative intent.

### 5.3 Small supplementary action surface

Routine automations must not need custom actions. Provide these for diagnostics and administration:

- `light_masks.explain`: response-only action returning intents, per-channel winners, desired output, observed output, and delivery status.
- `light_masks.clear_fields`: clear selected stored mask fields back to transparent without deleting the mask.
- `light_masks.sync`: explicitly reconcile current intent, subject to availability, apply mode, and suspension checks.
- `light_masks.resume`: explicitly accept recovery from external-change suspension after showing the desired output.

Use ordinary entity services for activation. Configuration and presets belong in the config/options flow, not a second mandatory automation DSL. Enforce normal HA entity/service permissions; do not register an unrestricted global API that bypasses entity access checks.

## 6. Resolution rules

### 6.1 Channels

Use three independently arbitrated channels:

1. **Power:** On or Off.
2. **Brightness:** positive HA brightness value.
3. **Appearance:** a tagged union of one color mode/value or one native effect with its required parameters.

Color is atomic. Never simultaneously output old XY and new Kelvin, or combine independently selected RGB components. Treat effects as an alternative appearance owner, not a freely composable extra flag over another mask's color.

Resolve power first from normal intent and active nontransparent power masks. Resolve brightness and appearance independently from normal intent and active masks, in ascending z-order. Unset/excluded fields do not replace anything.

Then apply the power gate:

- If resolved power is Off, send only an off command when needed. Keep brightness/appearance intent in memory; do not send `light.turn_on` to pre-stage it.
- If resolved power is On, build one coherent desired On payload containing the resolved supported values.
- If normal power is unknown and no active mask explicitly decides it, do not guess On; report incomplete initialization.

This two-pass rule is intentional. It differs from a bottom-to-top adjust operation that disappears whenever the partially folded power happens to be Off. Here a low-priority color mask can provide appearance to a higher-priority force-on mask that does not own appearance.

Pseudocode, not an implementation:

```text
validate input and take an immutable intent revision
power = normal.power
brightness = normal.brightness
appearance = normal.appearance
winners = normal for each initialized channel

for mask in active, unexpired masks ordered by increasing z-order:
    replace power if this mask asserts power
    replace brightness if enabled and populated
    replace appearance if enabled and populated
    record each replacement's owner

if power is unknown:
    produce no physical command and an initialization diagnostic
elif power is off:
    produce desired off
else:
    produce desired on using the independently selected channels

plan only the endpoint writes necessary to reach that desired result
```

Do not erase, freeze, or copy lower-priority intent when a mask becomes hidden.

### 6.2 Worked example

Normal intent is initially Off, brightness 45%, CCT 4000 K. Circadian mask has z=10 and owns appearance only. Washer mask has z=80 and owns appearance only. Dishwasher mask has z=70 and owns appearance only.

| Event | Normal power | Active notification | Visible result |
|---|---|---|---|
| Circadian sets 4300 K and activates | Off | None | Off; no color On command |
| Wall switch turns normal power On | On | None | 45%, 4300 K |
| Washer sets green and activates | On | Washer | 45%, green |
| Circadian updates to 3600 K | On | Washer | Still green; 3600 K retained underneath |
| Normal controller changes brightness to 30% | On | Washer | 30%, green |
| Dishwasher sets yellow and activates | On | Washer + dishwasher | Green wins appearance |
| Wall switch turns normal power Off | Off | Both still active | Off |
| Wall switch turns normal power On | On | Both still active | 30%, green |
| Washer turns its mask Off | On | Dishwasher | 30%, yellow |
| Dishwasher turns its mask Off | On | None | 30%, 3600 K |

Turning off the room does not acknowledge a washer event. It only suppresses its visibility. The washer mask remains active until its automation clears it or its configured lease expires.

### 6.3 Transitions

Transitions are command metadata, not a fourth persistent channel.

- Use the transition supplied by the event that changes the composed result, or that operation's configured default.
- A hidden update changes no physical output and cannot restart an unrelated transition.
- Revealing a lower mask uses the releasing event's transition, not the transition attached to an old hidden update.
- If several events coalesce, use the latest event that actually changed the final composed result; an unrelated hidden event must not overwrite it.
- Native transitions may still execute inside a bulb after a newer command. Best effort is to send the newest desired command, not to claim cancellation.
- Confirmation deadlines include the commanded transition duration. Do not retry a five-second fade after one second because its target has not yet been reached.

## 7. Effects and hardware capability boundaries

### 7.1 Static colors first

The first vertical slice supports On/Off, brightness, Kelvin, and endpoint-supported static color modes. A color-only notification preserves logical brightness; perceived brightness may still vary between colors and fixtures. Do not promise photometric equality.

Keep the caller's supported color representation when possible, particularly XY for Zigbee lights. Normalize only through supported HA conversions and hardware limits. Persist both accepted intent and the normalized desired payload when they differ, and expose the reason.

Use the base's Kelvin range. Test min/max clamps without repeated resend loops. `rgbw`, `rgbww`, and white-channel behavior require explicit tests before being advertised; rejecting an unsupported mode is preferable to silently losing white-channel intent.

### 7.2 Native effects are a qualified feature

Support effects only when a particular endpoint has a verified start, stop, and static-color-restoration path. A generic `effect_list` is not proof of reversibility.

- A running effect owns the whole appearance channel.
- An explicit static-color update to the same mask clears that mask's previously selected effect.
- A brightness-only update retains the mask's effect.
- An effect-only update may retain the mask's local color as an effect parameter, but it does not inherit an unrelated lower mask's changing color.
- Higher-priority static appearance must stop a lower effect before applying color.
- Releasing an effect must stop it and render the current lower appearance, not a saved physical snapshot.
- Do not blindly send `effect: none` or a guessed stop name. Devices can expose different stop commands, and some expose none.
- Effects whose power or brightness behavior cannot be isolated must be rejected for color-only masks, or explicitly require the corresponding ownership options.

Declare unsupported effect requests visibly. V1 may ship static masks before native-effect adapters are qualified. A software pulse/animation engine is a separate future feature, not a hidden timer loop in the initial compositor. Flash/strobe is out of the initial scope.

## 8. Target-only automation compatibility

The promise is **unchanged automation logic where compatible**, not "every possible automation becomes correct by replacing one entity ID."

### 8.1 Direct notification actions: good fit

For an appliance automation using normal On-with-color followed by Off, retarget all its light actions to one dedicated mask. Its final Off releases the notification without switching off normal illumination. Non-light actions and triggers remain unchanged.

With the color-only default, a supplied `brightness_pct: 25` is intentionally excluded from physical output. If 25% notification brightness is wanted later, enable brightness ownership on that mask. The automation's restart/delay behavior is not repaired by the integration.

One mask per independent producer is mandatory. Error/finished/reminder states from a single serialized appliance controller may share one mask. If they require different priorities, use distinct masks and corresponding targets; a fixed-priority entity cannot infer priority from the color red.

### 8.2 Standalone circadian blueprint: compatible with a semantic caveat

Retarget the standalone color blueprint to a color-only circadian mask and activate that mask during commissioning.

Because the mask stays On while active, the existing `is_state(target, "on")` condition allows it to keep its intended CCT current even while the room is dark. The integration's final power gate prevents physical On commands. On normal power-up, the latest cached circadian intent is included in the same output command.

Its color threshold compares against its **own** intent and is not confused by a green notification. Keep existing bypass inputs unchanged.

However, physical Off-to-On no longer triggers the blueprint's target-On trigger. The mask's own activation does. Sun changes and bypass changes continue to update it, but the integration cannot recompute an external automation's formula on demand. A stale cached value after missed events/restart is possible until the automation runs.

Do not fake the mask's state to mirror physical power: that would break the contract that mask Off means inactive, and could prevent a dark-room color producer from activating. If exact physical-on-trigger behavior is required, it needs a later explicit blueprint input for an observation entity, not an implicit compatibility claim.

### 8.3 Existing scene-based appliance blueprint: conditional fit

Retarget its single light input to a dedicated, initially inactive mask. Its snapshot then captures **mask state**, not physical state. Restoring a snapshot of Off invokes `light.turn_off` on the mask and releases it. Its RGB-based notification detection reads its own color even when another appliance hides it.

This is supported by inspection of Core 2026.9.3's light scene reproduction: an Off snapshot reproduces a turn-off action; an On snapshot reproduces color/brightness/effect attributes, unless the current state already matches.

Limitations must remain visible:

- Dynamically created scenes and automation wait/delay state do not become restart-durable just because the integration's intents are durable.
- A scene made while the mask is already active restores that active mask; it does not mean release.
- A blueprint that re-snapshots at reminder-window start, even during notification, needs a compatibility test: it can preserve an active notification rather than an inactive baseline.
- Missing scenes and interrupted actions are not repaired by the compositor.
- Core scenes do not serialize this integration's full mask configuration, lease metadata, or per-field unset ownership.

Use `restore active state = inactive` for these notification masks initially and an optional maximum duration as a safety net. Claim target-only compatibility only for tested paths. A future blueprint version using direct mask On/Off instead of scenes would be simpler, but is outside this implementation's requirement and must not be changed automatically.

### 8.4 Combined home-light controller and manual switches

The combined motion/night/sunrise blueprint remains pointed at the normal-control light. It still owns its normal brightness/color/power logic; notification masks sit above it.

Route a wall-switch automation that should control only power to the optional normal power port. This filters its legacy brightness/color payloads without rewriting action logic. Retain its existing bypass inputs.

There are two additional boundaries:

- An Off action in a power-owning automation should target normal control, not a transparent overlay. Otherwise Off releases instead of holding normal illumination off.
- Lux-based closed-loop controllers read real room illumination. A brightness-owning mask changes that illumination even if the normal-control entity reports underlying intent. Such controllers may compensate or saturate. Color-only notifications minimize, but do not eliminate, this physical coupling. Preserve existing bounds/bypass and test it; do not claim logical isolation makes sensor feedback independent.

## 9. Endpoint ownership, groups, and Lightener

### 9.1 Ownership graph

Track a directed graph of control dependencies and a set of known physical leaves. Reject:

- Duplicate ownership of the same output by two parent entries.
- A parent selecting one of its own normal or mask entities as its output.
- Direct or transitive cycles.
- A group and one of its members being independently owned by different renderers.
- Known overlap between two groups, including different group entities reaching the same bulb.

Generic HA membership cannot prove every overlap. Zigbee group membership, WLED master/segments, and proprietary virtual lights may hide physical relationships. Require explicit non-overlap acknowledgement for opaque endpoints; do not claim automatic discovery of all physical conflicts.

### 9.2 Atomic endpoints versus HA aggregate groups

**Atomic endpoint mode:** one bulb, one verified uniform Zigbee group, or an explicitly accepted opaque endpoint. Send at most one native command per resolved update. Preserve broadcast efficiency and the endpoint's established abstraction.

For an opaque group, "already on" means the endpoint's aggregate logical state, not a guarantee about every member. A color-only `turn_on` to a partially lit group could wake its off members. Therefore strict "never turn on an off member" cannot be certified for an opaque group without uniform-state guarantees.

**HA aggregate mode:** recursively resolve known members, calculate per-leaf desired output and availability, and suppress appearance-only On calls to leaves whose normal power is Off. Preserve member normal state when adopting a mixed initial group. An explicit normal group On may intentionally turn all normal members on.

Only combine writes into a broadcast when all members need the same payload and doing so cannot address a gated-off or separately owned leaf. Recheck ownership when group membership changes; suspend affected output and create a Repair if a change introduces a conflict. Never automatically adopt a new member and send it a cached On.

Aggregate output status must distinguish full convergence from partial failure. Successful delivery to one member is not successful delivery to the room.

A group containing an unrelated switch-controlled light is **not** automatically a suitable color-only pilot. Choose an existing compatible color-capable group, or separately approve a correctly scoped group during rollout.

### 9.3 Composition with Lightener

Do not reimplement brightness curves. The preferred topology for independent notifications on physical outputs is:

```text
Room automations
       |
Existing Lightener brightness distribution
       |
Per-output normal-control light <--- wall-switch power port, if needed
       |                     \
       |                      + circadian / appliance mask inputs
       |
Light Masks compositor
       |
Physical light or safe Zigbee group
```

Update Lightener's configured members to the corresponding normal-control entities during an approved migration. Its curve output then updates normal brightness, and a notification can override only the output's appearance.

Putting one compositor above a Lightener is a different, room-wide mode: calls reach the Lightener, which distributes them according to its curves. It is not automatically safe for color-only updates because that virtual light may turn on previously off children. Defer certification of that topology until its behavior is tested.

Do not simultaneously configure a compositor over Lightener and compositors over the same children. `lightener_studio` is a separate integration and requires its own compatibility tests before migration.

## 10. Runtime architecture and delivery

### 10.1 Components

```text
Standard HA light services / scenes / UI
                 |
    Normal ports and MaskLight entities
                 |
     Per-parent serialized intent controller
                 |
        Pure channel resolver
                 |
  Capability and group-aware output planner
                 |
  Per-endpoint single-writer delivery worker
                 |
       Existing light integration
                 |
   Observations -> delivery verification
```

The resolver has no HA imports, I/O, mutable global state, or wall-clock reads. Supply time and capabilities explicitly.

Use a push/event-driven controller with state-change subscriptions, not periodic polling or an unnecessary polling DataUpdateCoordinator. Entity properties read cached state and perform no I/O.

### 10.2 Concurrency rules

- Serialize input mutations, toggles, config changes, and expiry events per parent.
- Increment an intent revision on each accepted semantic change.
- Resolve from an immutable snapshot after the mutation.
- Never hold the intent lock while awaiting a network/service call.
- Keep one in-flight delivery per physical endpoint and one replaceable newest pending desired revision, not an unbounded queue of old effects.
- A newer Off discards unsent older On revisions.
- An already transmitted command cannot be recalled; after it returns, immediately reconcile the newest desired revision.
- Completion/retry callbacks check both endpoint generation and desired revision before acting.
- Do not resend a command whose effective payload has not changed. Hidden-only updates generate zero physical commands.

The input call returning means intent was accepted, not that a bulb has confirmed delivery. Invalid configuration/input raises a translated validation error. Delivery failure is reported through status, logs and Repairs; it must not be represented as a confirmed physical state.

### 10.3 Observation is not intent

Maintain separate:

- Requested normal/mask values.
- Normalized desired physical values.
- Last sent revision/context.
- Last observed state.
- Delivery/verification status.

Never copy an output report wholesale into every producer. Never infer the obscured normal color from a currently green notification.

Own-command context is a useful correlation signal, not proof that other reports are human commands. Many device integrations do not preserve context. Compare normalized payloads, known transition windows, and bounded in-flight acknowledgements without treating every mismatch as a user action.

When no masks are active, the renderer is settled, and a confirmed external state change occurs, adopt valid observed changes as the new normal baseline.

When masks are active and an unexplained settled output change cannot safely be attributed, **suspend physical rendering for that parent**, cancel retries, preserve intents, and surface a Repair/diagnostic. Do not fight a physical switch or guess the hidden baseline. An operator can use the original entity while suspended, then explicitly resume after reviewing desired output. Input intents may still be stored, but status must clearly show that they are not being delivered.

This is a conservative escape path, not the usual manual workflow. Routine wall switches use the normal power port and never trigger external-change suspension. Direct Zigbee binding, vendor apps, or raw-entity automations are outside exclusive-writer guarantees and must be inventoried before commissioning.

### 10.4 Delivery and tolerances

Initial proposed defaults, to be calibrated in the pilot:

- Coalesce non-power updates for up to 50 ms; power Off should bypass optional coalescing.
- At most three send attempts per desired revision with bounded backoff.
- Confirm power exactly; compare brightness within 2/255, Kelvin within 100 K, XY within 0.01 after supported conversion.
- Do not repeatedly resend sub-tolerance changes; keep the exact logical intent for later composition.
- Use transition-aware deadlines and a bounded device-report grace period.
- Devices that cannot report completion are `unverified`, not `in_sync`.
- Persistent mismatch stops retrying and raises a Repair. New intent or explicit sync can start a new bounded attempt.

These numbers are design targets, not measured hardware characteristics. Do not suppress a real service exception behind a tolerance match.

## 11. Persistence, timers, and failure recovery

### 11.1 Mask lease

An optional maximum duration protects against a missed release:

- Start or renew the deadline on every accepted mask On, including an unchanged On.
- Off cancels it. TTL zero is invalid, not "unlimited."
- Store an absolute UTC deadline; runtime scheduling uses HA's scheduler.
- On expiry, deactivate the mask and resolve current lower intent.
- Expiry never forces On merely because the expiring mask was active.
- Nevertheless, releasing an advanced force-off mask can expose a currently On normal intent. The advanced configuration must warn about this. With the confirmed power-transparent notification default, expiry cannot change power.
- An expired persisted mask cannot be sent once at startup before being removed.

A duration is a fail-safe, not a guarantee that the appliance is still finished. Do not infer acknowledgement or appliance state.

### 11.2 Persistence contract

Persist activation, release, leases, and normal power changes before reporting those state-changing input operations accepted. Coalesce frequent appearance-only persistence writes with a documented maximum five-second loss window on an abrupt process failure. Flush on orderly unload.

Do not claim crash-durable per-frame sunrise updates. If storage fails, show the persistence fault and reject operations requiring durable acceptance; do not report a durable mask activation when it was only stored in memory.

Test storage migrations, malformed data, and write failures. Invalid persisted configuration fails visibly and does not default to an On output.

### 11.3 Startup and reconnection

1. Load and validate configuration and runtime store.
2. Discard expired masks and apply each mask's restore-active policy.
3. Wait for valid endpoint availability/capabilities.
4. Resolve desired output without sending it.
5. Reconcile only under the explicit apply/recovery policy.

Initial setup starts in shadow mode. An existing apply setting may persist, but **HA startup alone must not change observed Off to On**. If restored intent requests On while the endpoint reports Off, publish `awaiting_resume`; a new explicit normal-power input or administrator resume can authorize On. A color-only update cannot authorize it.

While running, a newly received normal On during an endpoint outage creates a pending power intent that may be delivered when the endpoint returns. Mere reconnection does not authorize replay of an old startup-blocked On. Expired notifications and obsolete revisions are discarded before recovery.

An endpoint becoming unavailable does not mean Off and must not erase any intent. Input entities remain usable as virtual intent stores; expose `output_available` and delivery status explicitly. Never substitute a fabricated observed brightness.

### 11.4 Apply, unload, and removal

- Apply Off means shadow: accept/resolve intents but send nothing. It does **not** undo the last physical command.
- Apply On shows a warning that current intent may be delivered; normal startup power safeguards still apply unless the user explicitly authorizes resume.
- Reload/unload removes listeners, timers, and tasks without commanding hardware or restoring a stale scene.
- Deleting an active mask while applied is an explicit release and may reveal lower intent; show its preview.
- Removing the integration does not guarantee restoration. The removal checklist is: inspect baseline, deactivate masks while applied, verify intended normal result, retarget consumers to original endpoints, then remove.
- Store failures, capability loss, missing outputs, and topology conflicts produce distinct Repairs with actionable recovery steps.

## 12. User experience and diagnostics

### 12.1 Setup

1. Select the existing output light.
2. Show its capabilities, known members, possible overlaps, and atomic/aggregate policy.
3. Establish normal baseline and optional power-only control port.
4. Add mask subentries using presets: Color notification, Circadian appearance, Brightness adjustment, or Advanced power override.
5. Preview example results and per-channel ownership in shadow mode.
6. Retarget selected consumers only after reviewing their read/write contracts.
7. Enable Apply explicitly for a limited pilot.

Mask editors must show both priority and enabled channels. Do not label an inactive mask as "physical light off," or a hidden active mask as failed. Display "Active, appearance hidden by Washer" or "Active, not visible because normal power is off."

Use native entity controls and integration options first. No required custom Lovelace card or custom frontend panel in v1. English and Ukrainian translations should cover setup, entity names, validation, status and Repairs.

### 12.2 Diagnostics

Provide a compact parent status with mutually distinguishable outcomes:

`shadow`, `awaiting_resume`, `in_sync`, `pending`, `unverified`, `unavailable`, `partial_failure`, `failed`, `suspended_external_change`.

Expose, primarily through the explain action and diagnostics download:

- Current normal intent and active mask list.
- Per-channel owner, priority, and suppressed masks.
- Desired versus observed output.
- Excluded and unset fields.
- Current revision, last sent revision, retry count, and normalized/clamped values.
- Deadline and restart policy.
- Group members, gated-off leaves, and failed/unverified leaves.
- External suspension reason and explicit recovery instructions.

Keep verbose histories out of state attributes to avoid inflating MariaDB recorder writes. Use bounded debug logs, rate-limited warnings, and a bounded diagnostic event ring. Do not log user tokens or unrelated home state.

Do not automatically create powercalc entries for virtual masks: they represent intent and consume no additional physical power.

### 12.3 Areas, labels, broad targets, and voice

Normal-control entities are household-facing; masks are intermediate controls. Offer, but do not silently create or assign, the existing `Helper` label. Do not expose mask lights to voice by default.

There is no HA guarantee that a hidden entity or `Helper` label excludes it from area/floor/domain-wide service calls. `light.turn_on` targeting every light could activate all masks; `light.turn_off` could clear outstanding notifications.

Keep masks off the normal area's default targeting path where practical, provide explicit normal-control groups/labels for room and all-house actions, and audit broad targets before rollout. Do not automatically relocate the physical device or break its existing area assignment.

## 13. Implementation structure

Proposed production package:

```text
custom_components/light_masks/
    __init__.py       config-entry lifecycle and platform setup
    manifest.json    local integration metadata and version
    config_flow.py   parent and mask subentry configuration
    const.py         stable domain keys and defaults
    model.py         typed immutable intents, channels, output revisions
    resolve.py       pure priority/channel resolution
    controller.py    serialized mutations, expiry, reconciliation
    light.py         normal-control, power-port, and mask LightEntity classes
    output.py        capability normalization, topology, delivery workers
    store.py         versioned runtime persistence and migrations
    switch.py        apply/shadow control
    sensor.py        compact delivery status
    services.py      explain, clear_fields, sync, resume
    services.yaml    action schemas and UI descriptions
    diagnostics.py   redacted diagnostic export
    repairs.py       actionable repair handling
    strings.json
    translations/en.json
    translations/uk.json
```

Split output topology/effect adapters into additional modules only when that code warrants it. Do not subclass LightGroup for mask entities: its child-derived state is the wrong semantic model.

Use typed config-entry runtime data, stable entity unique IDs based on entry/mask identity, push subscriptions, translated `ServiceValidationError` messages, and HA-managed task cleanup. Include a manifest version, config flow, documentation/issue links, and appropriate local integration metadata. No dependency on Layers or Lightener.

## 14. Implementation phases and release gates

| Phase | Deliverable | Exit condition |
|---|---|---|
| 0. Contract and Core feasibility | Test fixture on Core 2026.9.3; verify mask on-state, scene reproduction, argument conversion, power-only filtering, subentry lifecycle and toggle behavior | No undocumented Core behavior required for ordinary services; incompatible blueprint paths explicitly classified |
| 1. Pure composition | Typed model, resolver, channel permissions, stable priorities, current lower-intent reveal | Resolver and invariant tests pass without HA |
| 2. Atomic static-light vertical slice | One parent, normal light, optional power port, two mask lights, static color/CCT, shadow/apply | Demonstrate washer + circadian + switch example against a fake endpoint with zero global interception |
| 3. Reliable lifecycle | Store/migration, restart safety, leases, revisioned single writer, availability, bounded retries, Repairs | Failure/restart/concurrency tests pass; no stale replay or startup-only On |
| 4. Home composition | Known HA groups, overlap/cycle checks, partial-member gating, broad-target guidance, upstream Lightener test | No accidental wake-up of off members; no known double writer; broadcast behavior measured |
| 5. Qualified native effects | Device-specific start/stop/restore certification | Clearing and superseding effects always converge; unsupported adapters remain disabled |
| 6. Controlled adoption and release | Translations, diagnostics, HACS packaging, user guide and compatibility matrix | Approved pilot succeeds; rollback rehearsed; no house-wide migration by default |

Do not make native effects a prerequisite for learning whether the static entity model works. Do make reliability and safe grouping prerequisites for claiming production suitability.

Every phase produces executable tests and updated user-facing documentation. Run Ruff/type checks, pure pytest tests, then targeted HA integration tests. CI runs hassfest and HACS validation in addition to the supported Core-version matrix.

Start with Core 2026.9.3 as the minimum tested release; do not advertise older-version support until tested. Pin compatible test tooling/Python from that Core version's requirements rather than guessing them from unrelated integrations.

## 15. Acceptance test matrix

Tests must drive real HA service normalization and scene reproduction in addition to unit-testing the resolver.

| ID | Scenario | Required assertion |
|---|---|---|
| R01 | Normal Off; turn on a color-only mask | Mask is active; physical On call count is zero |
| R02 | Normal On at 45%; notification action supplies green and 25% brightness to color-only mask | Output green at normal 45%; excluded brightness is observable in diagnostics |
| R03 | Circadian update while washer hides it; release washer | Current circadian value revealed, not pre-washer value |
| R04 | Higher color mask and lower brightness mask | Both win their respective channels |
| R05 | Normal Off with several power-transparent masks | One required Off at most; no follow-up color On |
| R06 | Higher-priority force-on versus normal Off | Force-on wins only in explicitly configured advanced mode |
| R07 | Activate force-off mask, then turn that mask Off | Active mask holds output Off; deactivation releases to current lower power |
| R08 | Duplicate priority / invalid empty mask | Configuration rejected with specific error |
| R09 | Clear color field while keeping mask active | Lower appearance revealed; unrelated brightness ownership unchanged |
| R10 | All masks inactive | Normal intent alone determines output |
| A01 | Mask `toggle` while physical light is Off or mask hidden | Toggle uses activation, not output; two concurrent toggles cancel |
| A02 | Normal power-port toggle carrying legacy brightness/CCT | Only normal power changes |
| A03 | Brightness zero and relative brightness at HA service boundary | Correct per-role Off semantics; no step based on another producer's attributes |
| A04 | Snapshot inactive mask, activate it, scene-restore | Mask becomes inactive; normal output is not restored from an old physical snapshot |
| A05 | Two appliance mask snapshots and notification overlap | Scene IDs and local RGB state stay independent |
| A06 | Reminder re-snapshot while active; missing scene after restart | Limitation reproduced/documented; no claim of automatic release or scene reconstruction |
| A07 | Circadian mask active while physical output Off | Logical updates accepted; zero hardware writes; latest cached color included on normal On |
| A08 | Physical normal On after stale circadian cache | Documented cached result used; no claim the blueprint's physical-on trigger ran |
| C01 | RGB to XY conversion; CCT endpoints and limits | One valid appearance payload; no mixed color modes or repeated clamping loop |
| C02 | Static color replaces mask's own effect | Effect cleared and supported stop path executed |
| C03 | Higher static mask hides lower effect; release | Verified stop/restart behavior; no leaked effect state |
| C04 | Unsupported effect or unsafe color-only effect | Visible validation/qualification error, not silent fallback |
| G01 | Mixed HA group with some members normally Off | Appearance-only update never sends On to those members |
| G02 | Verified uniform Zigbee group | One broadcast, not one command per bulb |
| G03 | Opaque partial group without uniform-state guarantee | Strict per-member no-wake mode refused |
| G04 | Duplicate output, group/member overlap, transitive cycle, runtime membership conflict | Enrollment rejected or affected renderer suspended before writes |
| G05 | Lightener drives normal proxies | Brightness curves preserved; hidden notification never becomes normal feedback |
| G06 | Lightener Studio | Separate certification before rollout |
| D01 | New Off arrives while older On is queued/in flight | Unsent On discarded; no obsolete retry; newest Off eventually delivered |
| D02 | Old acknowledgement after newer revision | Does not confirm or overwrite current desired revision |
| D03 | Hidden updates / identical effective commands | Zero physical writes |
| D04 | Long transition with intermediate device reports | No premature retry or external-change suspension |
| D05 | Device unavailable and then returns | Intents retained; only newest authorized unexpired result delivered |
| D06 | Device reports no verifiable acknowledgement | Status unverified, never falsely in-sync |
| D07 | Persistent error / mismatched result | Bounded retries and Repair; no infinite service loop |
| D08 | Unexplained settled raw-device change under active masks | Rendering suspended; no retry fight; intents preserved |
| P01 | Restart with inactive-on-restart notification | Notification remains inactive; no scene restoration dependency |
| P02 | Restart with restored unexpired mask and expired mask | Policies respected before any output write; expired mask never flashes |
| P03 | Startup observed Off with restored normal On | No startup-only On; awaiting explicit power authorization |
| P04 | New normal On during runtime outage | Authorized newest On may be delivered at recovery; color-only update cannot authorize it |
| P05 | Lease renewal / expiry across restart and clock changes | Absolute deadline respected; no duplicate release or resurrected activation |
| P06 | Store write failure, corrupt store, migration, abrupt crash | Explicit fault; documented appearance loss window; no fabricated safe-looking state |
| P07 | Unload/reload/remove | No orphan listeners/timers, unexpected hardware commands, or stale revision workers |
| U01 | Area/all-light targets and voice exposure | Pilot inventory proves masks cannot be unintentionally included in normal household actions |
| U02 | Explanations and failures | Per-channel winners and delivery outcome match actual service log; no misleading physical-success state |

### 15.1 Quantitative targets

Proposed initial acceptance targets on a documented test host:

- Pure resolution for one output with 32 masks: p95 below 5 ms, measured without I/O.
- A burst of 100 hidden-only updates: exactly zero physical commands.
- A burst during a blocked delivery: at most one newest pending revision per endpoint, regardless of input count.
- Normal-power Off to service dispatch: p95 below 100 ms when no prior service call blocks the endpoint; report hardware latency separately.
- Maximum three send attempts per desired revision.
- No periodic hardware polling introduced by mask arbitration.
- No monotonic listener/task growth over 100 reload cycles.

These are release criteria, not current performance claims. Record measurement method, hardware, Core version, and transport.

## 16. Adoption and rollback

### 16.1 Inventory before changes

For the selected output, inspect its upstream/downstream groups and all consumers: automation definitions and blueprint inputs, scripts, scenes, helpers, Lightener/Studio configuration, dashboards, voice exposure, area/label targets, powercalc and direct device control.

Capture the current configuration of every object to be retargeted. Record old/new targets and registry IDs in the migration record. Do not rename the physical entity to impersonate a virtual one.

### 16.2 Pilot

Use one approved RGB-capable logical output with understood membership and no competing direct writers. Start with a single endpoint for a bench proof; use groups only after confirming their compatibility and ownership.

Create:

- Normal control and, if needed, its power-only port.
- Circadian color mask, z=10, power-transparent, brightness excluded.
- Washer color mask, z=80, power-transparent, brightness excluded, inactive after restart.
- A second test notification mask, z=70, to exercise overlap.

Commission circadian activation in shadow mode. Retarget only selected light references. Do not enable disabled automations as a side effect. Do not migrate another virtual-light integration until its compatibility gate passes.

Exercise the full timeline in section 6, power off/on during notifications, simultaneous updates, lower-layer reveal, unavailable recovery, and HA restart. Measure output command counts and inspect actual observed state.

An appliance automation may be a candidate for target-only migration. Resolve its target's group memberships and competing writers first; changing just that automation while leaving group writers aimed at the raw bulb does not create exclusive ownership.

### 16.3 Rollback

While the integration is still available, deactivate masks and deliberately bring the normal output to the agreed state. Turn Apply off and stop pending workers. Restore recorded automation targets and group/Lightener memberships through supported HA APIs; restore prior enabled/disabled states. Remove virtual entries only after consumers no longer reference them.

If the integration is unavailable, use original output entities and restore consumer targets directly. Do not require a whole-instance backup restore to undo a target change. Keep a normal backup before installation/restart and before any broader migration.

No integration installation, HA restart, group rewrite, or consumer migration is authorized by this planning document.

## 17. Remaining decisions and release blockers

The entity/priority contract and default notification power policy are settled for this proposal. These items are deliberately left as implementation or commissioning gates rather than invented facts:

| Item | Proposed disposition |
|---|---|
| Public integration name/domain/repository | Use Light Masks / `light_masks` provisionally; verify before publishing |
| First real output and exact target migration set | Select after complete dependency inventory, with user approval |
| Group and opaque virtual-output compatibility | Certify per endpoint policy; do not treat any light entity as automatically safe |
| Native effect stop semantics | Qualify actual device adapters; static colors ship independently |
| Scene-based appliance edge paths | Test and document; optional future blueprint revision requires a separate request |
| Exact circadian physical-on recomputation | Not guaranteed by target-only migration; cache semantics are explicit |
| Transport tolerance and timeout values | Calibrate in pilot; distinguish optimistic from confirmed delivery |
| Direct physical/vendor-app control under masks | Conservative suspension is proposed; wider automatic-adoption policies require separate evidence and design |

## 18. Sources and reproducibility

The design inputs above have been generalized from private commissioning
research. Private household inventories and blueprint filenames are intentionally
not part of the public specification. Use the local test suite and anonymized
verification record for reproducible implementation evidence.

Upstream sources:

- [Layers README](https://github.com/lukab-dev/ha-layers/blob/main/README.md), blob `afa5f5694781d431ca457a04b46c61ce861b26ec`.
- [Layers resolver](https://github.com/lukab-dev/ha-layers/blob/main/custom_components/layers/logic/resolve.py), blob `74c77f7d331f299658192753a9db988039158dec`. Confirms inherited channels, atomic color, and the bottom-up `adjust` gate.
- [Layers specification](https://github.com/lukab-dev/ha-layers/blob/main/docs/SPEC.md), specification reference; conclusions above rely on the README and inspected resolver, not an assertion that every implementation path was audited.
- [Lightener README](https://github.com/fredck/lightener/blob/master/README.md), blob `660df834fb6a1b45ea155311faddf364d7e7e18c`.
- [Lightener light implementation](https://github.com/fredck/lightener/blob/master/custom_components/lightener/light.py), blob `6204b330a96af3735b1bf1f37c0b1d0a868e71cf`.
- [Lightener config flow](https://github.com/fredck/lightener/blob/master/custom_components/lightener/config_flow.py), blob `32c40f8c48286ae713d5d1a78a40e64e443e34b1`.
- [HA light entity contract](https://developers.home-assistant.io/docs/core/entity/light/).
- [HA config flow documentation](https://developers.home-assistant.io/docs/core/integration/config_flow/).
- [HA 2026.9.3 light scene reproduction](https://github.com/home-assistant/core/blob/2026.9.3/homeassistant/components/light/reproduce_state.py), blob `ca8d8eab89bdef8f32256313557308ec17db5195`.

Branch links and developer documentation can change; the listed blob hashes identify the files inspected. Recheck APIs and upstream behavior during phase 0. Generalized examples are not a substitute for a pre-migration inventory.
