"""Pure native-address selection. Member IDs never enter a service payload."""

from collections.abc import Mapping
from dataclasses import dataclass

from .model import Intent, Resolution


@dataclass(frozen=True)
class Dispatch:
    output: str
    zones: tuple[str, ...]
    on: bool
    payload: dict[str, object]
    transition: float


def plan_dispatch(
    outputs: Mapping[str, str],
    desired: Mapping[str, Intent],
    needed: set[str],
    transitions: Mapping[str, float],
    supports_transition: bool,
    aggregate: str | None,
) -> tuple[Dispatch, ...]:
    commands = []
    for zone, output in outputs.items():
        if zone not in needed:
            continue
        intent = desired[zone]
        payload = Resolution(intent).payload()
        transition = transitions[zone] if supports_transition else 0.0
        if supports_transition:
            payload["transition"] = transition
        commands.append(Dispatch(output, (zone,), intent.on, payload, transition))
    if aggregate and needed == set(outputs) and commands:
        first = commands[0]
        if all(c.on == first.on and c.payload == first.payload for c in commands):
            return (Dispatch(aggregate, tuple(outputs), first.on, first.payload, first.transition),)
    return tuple(commands)
