"""Machine-readable Merge Boss planner catalog.

Durable learned facts live in ``docs/.../knowledge.mbk``.  This JSON file is a
planner-oriented cache of the identities, merge transitions and producer
outputs that runtime code needs directly.
"""

from __future__ import annotations

import json
import hashlib
import time
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from phone_harness.trace import emit_process_event

from phone_harness.merge_planner import MergeTransition
from phone_harness.producer_planner import ProducerOutput, ProducerSpec
from phone_harness.visual_descriptor import rank_descriptors, sprite_descriptor_from_image


DEFAULT_CATALOG_PATH = (
    Path(__file__).resolve().parents[3]
    / "docs"
    / "game-operations"
    / "aliexpress-merge-boss"
    / "catalog.json"
)
DEFAULT_VISUAL_PATH = DEFAULT_CATALOG_PATH.with_name("visual.mbv")


def _load_visual_overlays(path):
    visual_path = Path(path).with_name("visual.mbv")
    if not visual_path.exists():
        return {
            "items": {}, "producers": {}, "order_items": [],
            "board_items": [], "board_producers": [],
        }
    item_overlays = {}
    producer_overlays = {}
    order_item_overlays = []
    board_item_overlays = []
    board_producer_overlays = []
    lines = [line for line in visual_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not lines or json.loads(lines[0]).get("fmt") != "MBV1":
        raise ValueError("unsupported Merge Boss visual template format")
    for line in lines[1:]:
        item = json.loads(line)
        if item.get("k") == "item":
            item_overlays[(item["family"], int(item["level"]))] = item["d"]
        elif item.get("k") == "producer":
            producer_overlays[(item["identity"], int(item["level"]))] = item["d"]
        elif item.get("k") == "order-item":
            order_item_overlays.append(item)
        elif item.get("k") == "board-item":
            board_item_overlays.append(item)
        elif item.get("k") == "board-producer":
            board_producer_overlays.append(item)
    return {
        "items": item_overlays,
        "producers": producer_overlays,
        "order_items": order_item_overlays,
        "board_items": board_item_overlays,
        "board_producers": board_producer_overlays,
    }


def _read_visual_records(path=None):
    target = Path(path) if path is not None else DEFAULT_VISUAL_PATH
    if not target.exists():
        return target, []
    lines = [line for line in target.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not lines or json.loads(lines[0]).get("fmt") != "MBV1":
        raise ValueError("unsupported Merge Boss visual template format")
    return target, [json.loads(line) for line in lines[1:]]


def _write_visual_records(target, records):
    temporary = target.with_suffix(target.suffix + ".tmp")
    payload = [json.dumps({"fmt": "MBV1"}, separators=(",", ":"))]
    payload.extend(
        json.dumps(record, ensure_ascii=False, separators=(",", ":"))
        for record in records
    )
    temporary.write_text("\n".join(payload) + "\n", encoding="utf-8")
    temporary.replace(target)


@dataclass(frozen=True)
class MergeBossProducerCatalogEntry:
    identity: str
    level: int
    spec: ProducerSpec
    visual_descriptor: dict | None = None


@dataclass(frozen=True)
class MergeBossItemFamilyCatalogEntry:
    family_id: str
    display_name: str | None
    levels: tuple[tuple[int, str], ...]
    complete: bool
    level_metadata: tuple[tuple[int, dict], ...] = ()
    generation_producers: tuple[tuple[int, dict | None], ...] = ()

    def identity_at(self, level):
        for known_level, identity in self.levels:
            if known_level == level:
                return identity
        return None

    def level_of(self, identity):
        for level, known_identity in self.levels:
            if known_identity == identity:
                return level
        return None

    def descriptor_at(self, level):
        for known_level, metadata in self.level_metadata:
            if known_level == level:
                return metadata.get("visual_descriptor")
        return None

    def sprite_descriptor_at(self, level):
        for known_level, metadata in self.level_metadata:
            if known_level == level:
                return metadata.get("sprite_descriptor")
        return None

    def generation_producer_levels(self):
        return tuple(level for level, _descriptor in self.generation_producers)

    def generation_producer_descriptor(self, level):
        for known_level, descriptor in self.generation_producers:
            if known_level == level:
                return descriptor
        return None


class MergeBossCatalog:
    def __init__(
        self,
        transitions,
        producers,
        item_families=(),
        *,
        order_item_visuals=(),
        board_item_visuals=(),
        board_producer_visuals=(),
        source_path=None,
    ):
        self.transitions = tuple(transitions)
        self.producers = tuple(producers)
        self.item_families = tuple(item_families)
        self.order_item_visuals = tuple(order_item_visuals)
        self.board_item_visuals = tuple(board_item_visuals)
        self.board_producer_visuals = tuple(board_producer_visuals)
        self.source_path = Path(source_path) if source_path is not None else DEFAULT_CATALOG_PATH

    @classmethod
    def load(cls, path=None):
        source = Path(path) if path is not None else DEFAULT_CATALOG_PATH
        data = json.loads(source.read_text(encoding="utf-8"))
        if data.get("version") not in {1, 2}:
            raise ValueError("unsupported Merge Boss catalog version")

        transitions = [
            MergeTransition(
                item["input_identity"],
                int(item["input_level"]),
                item["output_identity"],
                int(item["output_level"]),
            )
            for item in data.get("merge_transitions", [])
        ]
        item_families = []
        transition_keys = {
            (
                transition.input_identity,
                transition.input_level,
                transition.output_identity,
                transition.output_level,
            )
            for transition in transitions
        }
        visual_overlays = _load_visual_overlays(source)
        for item in data.get("item_families", []):
            raw_levels = [dict(level_item) for level_item in item.get("levels", [])]
            for level_item in raw_levels:
                overlay = visual_overlays["items"].get(
                    (item["family_id"], int(level_item["level"]))
                )
                if overlay is not None and "sprite_descriptor" not in level_item:
                    level_item["sprite_descriptor"] = overlay
            levels = tuple(sorted(
                (
                    (int(level_item["level"]), level_item["identity"])
                    for level_item in raw_levels
                ),
                key=lambda value: value[0],
            ))
            level_metadata = tuple(sorted(
                ((int(level_item["level"]), level_item) for level_item in raw_levels),
                key=lambda value: value[0],
            ))
            complete = bool(item.get("complete", False))
            level_numbers = [level for level, _identity in levels]
            if complete and level_numbers != list(range(1, 11)):
                raise ValueError(
                    f"complete item family {item['family_id']!r} must define levels 1 through 10"
                )
            family = MergeBossItemFamilyCatalogEntry(
                family_id=item["family_id"],
                display_name=item.get("display_name"),
                levels=levels,
                complete=complete,
                level_metadata=level_metadata,
                generation_producers=tuple(
                    (
                        int(producer_item["level"]),
                        producer_item.get("visual_descriptor"),
                    )
                    for producer_item in item.get("generation_producers", [])
                ),
            )
            item_families.append(family)
            by_level = dict(levels)
            for input_level in range(1, 10):
                output_level = input_level + 1
                if input_level not in by_level or output_level not in by_level:
                    continue
                key = (
                    by_level[input_level],
                    input_level,
                    by_level[output_level],
                    output_level,
                )
                if key in transition_keys:
                    continue
                transitions.append(MergeTransition(*key))
                transition_keys.add(key)
        producers = []
        for item in data.get("producers", []):
            identity = item["identity"]
            level = int(item["level"])
            outputs = tuple(
                ProducerOutput(output["identity"], int(output["level"]))
                for output in item.get("possible_outputs", [])
            )
            visual_descriptor = item.get("visual_descriptor")
            if visual_descriptor is None:
                visual_descriptor = visual_overlays["producers"].get((identity, level))
            visual_family_id = item.get("visual_family_id")
            if visual_descriptor is None and visual_family_id:
                family = next(
                    (family for family in item_families if family.family_id == visual_family_id),
                    None,
                )
                if family is not None:
                    visual_descriptor = family.generation_producer_descriptor(level)
            producers.append(MergeBossProducerCatalogEntry(
                identity=identity,
                level=level,
                spec=ProducerSpec(
                    producer_id=f"{identity} Lv.{level}",
                    outputs=outputs,
                    outputs_complete=bool(item.get("outputs_complete", False)),
                ),
                visual_descriptor=visual_descriptor,
            ))
        return cls(
            transitions,
            producers,
            item_families,
            order_item_visuals=visual_overlays["order_items"],
            board_item_visuals=visual_overlays["board_items"],
            board_producer_visuals=visual_overlays["board_producers"],
            source_path=source,
        )

    def producer(self, identity, level):
        for item in self.producers:
            if item.identity == identity and item.level == level:
                return item
        return None

    def family(self, family_id):
        for family in self.item_families:
            if family.family_id == family_id:
                return family
        return None

    def family_for_item(self, identity, level=None):
        for family in self.item_families:
            known_level = family.level_of(identity)
            if known_level is None:
                continue
            if level is None or known_level == level:
                return family
        return None

    def rank_visual_items(self, descriptor, *, limit=5):
        """Rank learned item-family sprites for one observed visual descriptor."""
        candidates = {}
        metadata = {}
        for family in self.item_families:
            for level, identity in family.levels:
                visual = family.descriptor_at(level)
                if visual is None:
                    continue
                key = f"{family.family_id}\0{level}\0{identity}"
                candidates[key] = visual
                metadata[key] = {
                    "family_id": family.family_id,
                    "identity": identity,
                    "level": level,
                }
        ranked = rank_descriptors(descriptor, candidates, limit=limit)
        return [{**metadata[item["key"]], **item} for item in ranked]

    def rank_sprite_items(self, descriptor, *, limit=5):
        """Rank learned item identities using background-reduced sprite descriptors."""
        candidates = {}
        metadata = {}
        for family in self.item_families:
            for level, identity in family.levels:
                sprite = family.sprite_descriptor_at(level)
                if sprite is None:
                    continue
                key = f"{family.family_id}\0{level}\0{identity}"
                candidates[key] = sprite
                metadata[key] = {
                    "family_id": family.family_id,
                    "identity": identity,
                    "level": level,
                }
        for index, record in enumerate(self.board_item_visuals):
            identity = str(record["identity"])
            level = int(record["level"])
            family = self.family_for_item(identity, level)
            key = f"board\0{identity}\0{level}\0{index}"
            candidates[key] = record["d"]
            metadata[key] = {
                "family_id": None if family is None else family.family_id,
                "identity": identity,
                "level": level,
                "template_source": "board-item",
            }
        ranked = rank_descriptors(descriptor, candidates, limit=limit)
        return [{**metadata[item["key"]], **item} for item in ranked]

    def rank_visual_producers(self, descriptor, *, limit=5):
        candidates = {}
        metadata = {}
        for producer in self.producers:
            if producer.visual_descriptor is None:
                continue
            key = f"{producer.identity}\0{producer.level}"
            candidates[key] = producer.visual_descriptor
            metadata[key] = {
                "identity": producer.identity,
                "level": producer.level,
            }
        for index, record in enumerate(self.board_producer_visuals):
            identity = str(record["identity"])
            level = int(record["level"])
            key = f"board-producer\0{identity}\0{level}\0{index}"
            candidates[key] = record["d"]
            metadata[key] = {
                "identity": identity,
                "level": level,
                "template_source": "board-producer",
            }
        ranked = rank_descriptors(descriptor, candidates, limit=limit)
        return [{**metadata[item["key"]], **item} for item in ranked]

    def rank_order_items(self, descriptor, *, limit=5):
        candidates = {}
        metadata = {}
        for index, record in enumerate(self.order_item_visuals):
            key = f"{record['identity']}\0{int(record['level'])}\0{index}"
            candidates[key] = record["d"]
            metadata[key] = {
                "identity": record["identity"],
                "level": int(record["level"]),
            }
        ranked = rank_descriptors(descriptor, candidates, limit=limit)
        return [{**metadata[item["key"]], **item} for item in ranked]


def _normalized_family_levels(levels):
    normalized = []
    for item in levels:
        level = int(item["level"])
        identity = str(item["identity"]).strip()
        if not identity:
            raise ValueError("item family identity must be non-empty")
        payload = {"level": level, "identity": identity}
        for optional_key in ("visual_descriptor", "template_bounds"):
            if optional_key in item:
                payload[optional_key] = item[optional_key]
        normalized.append(payload)
    normalized.sort(key=lambda item: item["level"])
    if [item["level"] for item in normalized] != list(range(1, 11)):
        raise ValueError("item family hint must define levels 1 through 10")
    return normalized


def item_family_hint_id(levels):
    """Return a stable internal family id from one complete level-1..10 hint."""
    normalized = _normalized_family_levels(levels)
    material = "\n".join(
        f"{item['level']}\0{item['identity']}" for item in normalized
    ).encode("utf-8")
    return f"hint-{hashlib.sha256(material).hexdigest()[:12]}"


def record_item_family_hint(
    levels,
    *,
    path=None,
    family_id=None,
    display_name=None,
    source="item_i",
):
    """Atomically persist a complete Merge Boss item-family hint.

    The JSON catalog is the machine-readable durable memory used across chat
    rotation. Re-reading the same family updates its evidence sources instead of
    creating duplicates.
    """
    started = time.perf_counter()
    target = Path(path) if path is not None else DEFAULT_CATALOG_PATH
    normalized = _normalized_family_levels(levels)
    resolved_id = family_id or item_family_hint_id(normalized)
    data = json.loads(target.read_text(encoding="utf-8"))
    if data.get("version") == 1:
        data["version"] = 2
    if data.get("version") != 2:
        raise ValueError("unsupported Merge Boss catalog version")

    families = data.setdefault("item_families", [])
    existing = next(
        (item for item in families if item.get("family_id") == resolved_id),
        None,
    )
    sources = []
    if existing is not None:
        sources.extend(existing.get("learned_from", []))
    if source and source not in sources:
        sources.append(source)
    payload = {
        "family_id": resolved_id,
        "display_name": display_name,
        "complete": True,
        "levels": normalized,
        "learned_from": sources,
    }
    if existing is not None:
        for preserved_key in ("generation_producers",):
            if preserved_key in existing:
                payload[preserved_key] = existing[preserved_key]
    if existing is None:
        families.append(payload)
    else:
        existing.clear()
        existing.update(payload)

    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(target)
    emit_process_event(
        "knowledge.write",
        summary=f"Learned Merge Boss family {resolved_id}",
        phase="knowledge",
        status="persisted",
        data={
            "scope": "merge_boss",
            "kind": "item_family",
            "family_id": resolved_id,
            "display_name": display_name,
            "evidence_source": source,
            "path": str(target),
            "duration_ms": round((time.perf_counter() - started) * 1000, 3),
        },
    )
    return resolved_id


def record_item_family_visuals(
    family_id,
    image_path,
    slots,
    *,
    path=None,
):
    """Atomically upsert one family's level-1..10 sprite templates into MBV1."""
    started = time.perf_counter()
    target, records = _read_visual_records(path)
    family_id = str(family_id).strip()
    if not family_id:
        raise ValueError("family_id must be non-empty")
    slot_records = list(slots)
    levels = [int(item["expected_level"]) for item in slot_records]
    if levels != list(range(1, 11)):
        raise ValueError("visual family slots must define levels 1 through 10 in order")

    learned = []
    with Image.open(Path(image_path)) as source:
        image = source.convert("RGB")
        for slot in slot_records:
            bounds = slot["bounds"]
            x = float(bounds["x"])
            y = float(bounds["y"])
            w = float(bounds["w"])
            h = float(bounds["h"])
            crop = image.crop((x, y, x + w, y + h))
            try:
                descriptor = sprite_descriptor_from_image(crop)
            finally:
                crop.close()
            learned.append({
                "k": "item",
                "family": family_id,
                "level": int(slot["expected_level"]),
                "d": descriptor,
            })

    replacement_keys = {("item", family_id, item["level"]) for item in learned}
    kept = [
        record for record in records
        if (record.get("k"), record.get("family"), int(record.get("level", -1)))
        not in replacement_keys
    ]
    _write_visual_records(target, kept + learned)
    emit_process_event(
        "knowledge.write",
        summary=f"Updated {family_id} visual templates",
        phase="knowledge",
        status="persisted",
        data={"scope": "merge_boss", "kind": "item_visuals", "family_id": family_id, "count": 10, "path": str(target), "duration_ms": round((time.perf_counter() - started) * 1000, 3)},
    )
    return tuple(item["d"] for item in learned)


def record_producer_visual(
    identity,
    level,
    descriptor,
    *,
    path=None,
):
    """Atomically upsert a board-context producer visual template into MBV1."""
    started = time.perf_counter()
    target, records = _read_visual_records(path)
    identity = str(identity).strip()
    level = int(level)
    if not identity:
        raise ValueError("producer identity must be non-empty")
    record = {
        "k": "producer",
        "identity": identity,
        "level": level,
        "d": descriptor,
    }
    kept = [
        item for item in records
        if not (
            item.get("k") == "producer"
            and item.get("identity") == identity
            and int(item.get("level", -1)) == level
        )
    ]
    _write_visual_records(target, kept + [record])
    emit_process_event(
        "knowledge.write",
        summary=f"Updated producer visual {identity} Lv{level}",
        phase="knowledge",
        status="persisted",
        data={"scope": "merge_boss", "kind": "producer_visual", "identity": identity, "level": level, "path": str(target), "duration_ms": round((time.perf_counter() - started) * 1000, 3)},
    )
    return descriptor


def record_order_item_visual(
    identity,
    level,
    descriptor,
    *,
    path=None,
):
    """Upsert a reusable order-strip sprite template into MBV1."""
    started = time.perf_counter()
    target, records = _read_visual_records(path)
    identity = str(identity).strip()
    level = int(level)
    if not identity:
        raise ValueError("order item identity must be non-empty")
    record = {
        "k": "order-item",
        "identity": identity,
        "level": level,
        "d": descriptor,
    }
    kept = [
        item for item in records
        if not (
            item.get("k") == "order-item"
            and item.get("identity") == identity
            and int(item.get("level", -1)) == level
        )
    ]
    _write_visual_records(target, kept + [record])
    emit_process_event(
        "knowledge.write",
        summary=f"Updated order visual {identity} Lv{level}",
        phase="knowledge",
        status="persisted",
        data={"scope": "merge_boss", "kind": "order_visual", "identity": identity, "level": level, "path": str(target), "duration_ms": round((time.perf_counter() - started) * 1000, 3)},
    )
    return descriptor


def record_board_item_visual(
    identity,
    level,
    descriptor,
    *,
    path=None,
):
    """Persist one observed board-render variant without replacing hint templates."""
    started = time.perf_counter()
    target, records = _read_visual_records(path)
    identity = str(identity).strip()
    level = int(level)
    if not identity:
        raise ValueError("board item identity must be non-empty")
    record = {
        "k": "board-item",
        "identity": identity,
        "level": level,
        "d": descriptor,
    }
    duplicate = any(
        item.get("k") == "board-item"
        and item.get("identity") == identity
        and int(item.get("level", -1)) == level
        and item.get("d") == descriptor
        for item in records
    )
    if not duplicate:
        _write_visual_records(target, records + [record])
        emit_process_event(
            "knowledge.write",
            summary=f"Added board visual {identity} Lv{level}",
            phase="knowledge",
            status="persisted",
            data={
                "scope": "merge_boss",
                "kind": "board_visual",
                "identity": identity,
                "level": level,
                "path": str(target),
                "duration_ms": round((time.perf_counter() - started) * 1000, 3),
            },
        )
    return descriptor


def record_board_producer_visual(
    identity,
    level,
    descriptor,
    *,
    path=None,
):
    """Persist one observed producer render variant without replacing the canonical template."""
    started = time.perf_counter()
    target, records = _read_visual_records(path)
    identity = str(identity).strip()
    level = int(level)
    if not identity:
        raise ValueError("board producer identity must be non-empty")
    record = {
        "k": "board-producer",
        "identity": identity,
        "level": level,
        "d": descriptor,
    }
    duplicate = any(
        item.get("k") == "board-producer"
        and item.get("identity") == identity
        and int(item.get("level", -1)) == level
        and item.get("d") == descriptor
        for item in records
    )
    if not duplicate:
        _write_visual_records(target, records + [record])
        emit_process_event(
            "knowledge.write",
            summary=f"Added board producer visual {identity} Lv{level}",
            phase="knowledge",
            status="persisted",
            data={
                "scope": "merge_boss",
                "kind": "board_producer_visual",
                "identity": identity,
                "level": level,
                "path": str(target),
                "duration_ms": round((time.perf_counter() - started) * 1000, 3),
            },
        )
    return descriptor


def record_producer_output_hint(
    identity,
    level,
    outputs,
    *,
    path=None,
    complete=True,
    source="producer_i",
):
    """Persist a producer output hint without discarding observed outputs."""
    started = time.perf_counter()
    target = Path(path) if path is not None else DEFAULT_CATALOG_PATH
    target_identity = str(identity).strip()
    target_level = int(level)
    if not target_identity:
        raise ValueError("producer identity must be non-empty")

    normalized_outputs = []
    hint_keys = set()
    for output in outputs:
        output_identity = str(output["identity"]).strip()
        output_level = int(output["level"])
        if not output_identity:
            raise ValueError("producer output identity must be non-empty")
        if not 1 <= output_level <= 10:
            raise ValueError("producer output level must be between 1 and 10")
        key = (output_identity, output_level)
        if key in hint_keys:
            continue
        hint_keys.add(key)
        normalized_outputs.append({
            "identity": output_identity,
            "level": output_level,
            "source": source,
        })

    data = json.loads(target.read_text(encoding="utf-8"))
    if data.get("version") == 1:
        data["version"] = 2
    if data.get("version") != 2:
        raise ValueError("unsupported Merge Boss catalog version")

    producers = data.setdefault("producers", [])
    existing = next(
        (
            item for item in producers
            if item.get("identity") == target_identity
            and int(item.get("level", -1)) == target_level
        ),
        None,
    )
    if existing is None:
        existing = {
            "identity": target_identity,
            "level": target_level,
            "outputs_complete": False,
            "possible_outputs": [],
        }
        producers.append(existing)

    existing_by_key = {
        (item["identity"], int(item["level"])): dict(item)
        for item in existing.get("possible_outputs", [])
    }
    previous_keys = set(existing_by_key)
    for output in normalized_outputs:
        key = (output["identity"], output["level"])
        merged = existing_by_key.get(key, {})
        merged.update(output)
        existing_by_key[key] = merged

    conflicting_previous = previous_keys - hint_keys
    existing["possible_outputs"] = sorted(
        existing_by_key.values(),
        key=lambda item: (int(item["level"]), item["identity"]),
    )
    existing["outputs_complete"] = bool(complete and not conflicting_previous)
    learned_from = list(existing.get("learned_from", []))
    if source and source not in learned_from:
        learned_from.append(source)
    existing["learned_from"] = learned_from

    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(target)
    emit_process_event(
        "knowledge.write",
        summary=f"Learned producer outputs {target_identity} Lv{target_level}",
        phase="knowledge",
        status="persisted",
        data={
            "scope": "merge_boss",
            "kind": "producer_outputs",
            "identity": target_identity,
            "level": target_level,
            "output_count": len(normalized_outputs),
            "complete": existing["outputs_complete"],
            "evidence_source": source,
            "path": str(target),
            "duration_ms": round((time.perf_counter() - started) * 1000, 3),
        },
    )
    return existing["outputs_complete"]

