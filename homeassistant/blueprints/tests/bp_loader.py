"""Blueprint YAML loader for the spec-07 static tests: `!input x` -> Input("x"); anchors resolved."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

import yaml

BP_DIR = Path(__file__).resolve().parents[1] / "automation" / "vps_lightsail"

@dataclass(frozen=True)
class Input:
    name: str

class BlueprintLoader(yaml.SafeLoader):
    """SafeLoader that also understands Home Assistant's `!input` tag."""

BlueprintLoader.add_constructor(
    "!input", lambda loader, node: Input(loader.construct_scalar(node)))

def load(text: str) -> Any:
    return yaml.load(text, Loader=BlueprintLoader)  # SafeLoader subclass: no arbitrary objects

def load_file(name: str) -> Any:
    return load((BP_DIR / name).read_text())

def walk(node: Any) -> Iterator[Any]:
    """Depth-first over every value of a loaded document (mapping keys are not yielded)."""
    yield node
    children = node.values() if isinstance(node, dict) else node if isinstance(node, list) else ()
    for child in children:
        yield from walk(child)

def declared_inputs(doc: dict) -> dict[str, dict]:
    """Flattened inputs, same rule as HA's Blueprint.inputs: a section is a dict holding `input`."""
    flat: dict[str, dict] = {}
    for key, value in doc["blueprint"]["input"].items():
        if value and "input" in value:
            flat.update(value["input"])
        else:
            flat[key] = value
    return flat

def used_inputs(doc: dict) -> set[str]:
    return {node.name for node in walk(doc) if isinstance(node, Input)}
