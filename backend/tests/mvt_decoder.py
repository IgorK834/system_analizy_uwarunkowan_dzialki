"""Minimalny dekoder Mapbox Vector Tile 2.1 do testów kontraktu (BK-401).

Dekoduje nazwy warstw, extent, identyfikatory cech, atrybuty i typ geometrii
oraz komendy geometrii (współrzędne kafla). Nie jest używany w runtime — testy
nie potrzebują dodatkowej zależności do czytania protobuf.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Any

GEOMETRY_TYPES = {0: "UNKNOWN", 1: "POINT", 2: "LINESTRING", 3: "POLYGON"}


@dataclass
class MvtFeature:
    id: int | None
    geometry_type: str
    properties: dict[str, Any]
    geometry: list[int] = field(default_factory=list)

    def vertices(self) -> list[tuple[int, int]]:
        """Bezwzględne współrzędne kafla wszystkich wierzchołków."""
        points: list[tuple[int, int]] = []
        x = y = 0
        index = 0
        commands = self.geometry
        while index < len(commands):
            command = commands[index] & 0x7
            count = commands[index] >> 3
            index += 1
            if command in (1, 2):
                for _ in range(count):
                    x += _zigzag(commands[index])
                    y += _zigzag(commands[index + 1])
                    index += 2
                    points.append((x, y))
        return points


@dataclass
class MvtLayer:
    name: str
    extent: int
    version: int
    features: list[MvtFeature]


def _zigzag(value: int) -> int:
    return (value >> 1) ^ -(value & 1)


def _varint(data: bytes, pos: int) -> tuple[int, int]:
    result = shift = 0
    while True:
        byte = data[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result, pos
        shift += 7


def _fields(data: bytes):
    pos = 0
    while pos < len(data):
        key, pos = _varint(data, pos)
        number, wire = key >> 3, key & 0x7
        if wire == 0:
            value, pos = _varint(data, pos)
        elif wire == 1:
            value = data[pos:pos + 8]
            pos += 8
        elif wire == 2:
            length, pos = _varint(data, pos)
            value = data[pos:pos + length]
            pos += length
        elif wire == 5:
            value = data[pos:pos + 4]
            pos += 4
        else:  # pragma: no cover - niewspierane typy wire nie występują w MVT
            raise ValueError(f"Nieobsługiwany typ wire {wire}.")
        yield number, wire, value


def _packed(data: bytes) -> list[int]:
    values: list[int] = []
    pos = 0
    while pos < len(data):
        value, pos = _varint(data, pos)
        values.append(value)
    return values


def _value(data: bytes) -> Any:
    for number, _wire, raw in _fields(data):
        if number == 1:
            return raw.decode("utf-8")
        if number == 2:
            return struct.unpack("<f", raw)[0]
        if number == 3:
            return struct.unpack("<d", raw)[0]
        if number in (4, 5):
            return raw if raw < 1 << 63 else raw - (1 << 64)
        if number == 6:
            return _zigzag(raw)
        if number == 7:
            return bool(raw)
    return None


def _layer(data: bytes) -> MvtLayer:
    name = ""
    extent = 4096
    version = 1
    keys: list[str] = []
    values: list[Any] = []
    raw_features: list[bytes] = []
    for number, _wire, raw in _fields(data):
        if number == 1:
            name = raw.decode("utf-8")
        elif number == 2:
            raw_features.append(raw)
        elif number == 3:
            keys.append(raw.decode("utf-8"))
        elif number == 4:
            values.append(_value(raw))
        elif number == 5:
            extent = raw
        elif number == 15:
            version = raw
    features: list[MvtFeature] = []
    for raw in raw_features:
        feature_id: int | None = None
        geometry_type = "UNKNOWN"
        tags: list[int] = []
        geometry: list[int] = []
        for number, _wire, value in _fields(raw):
            if number == 1:
                feature_id = value
            elif number == 2:
                tags = _packed(value)
            elif number == 3:
                geometry_type = GEOMETRY_TYPES.get(value, "UNKNOWN")
            elif number == 4:
                geometry = _packed(value)
        properties = {
            keys[tags[index]]: values[tags[index + 1]] for index in range(0, len(tags), 2)
        }
        features.append(MvtFeature(feature_id, geometry_type, properties, geometry))
    return MvtLayer(name=name, extent=extent, version=version, features=features)


def decode_tile(data: bytes) -> dict[str, MvtLayer]:
    """Zwraca warstwy kafla po nazwie; pusty kafel daje pusty słownik."""
    layers: dict[str, MvtLayer] = {}
    for number, _wire, raw in _fields(data):
        if number == 3:
            layer = _layer(raw)
            layers[layer.name] = layer
    return layers
