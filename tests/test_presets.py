"""Bambu Studio presets flattened along their inherits chain, on a fake
profile tree so the suite never needs Bambu Studio installed."""

import json

import pytest

from printcheck import presets


@pytest.fixture
def profiles(tmp_path, monkeypatch):
    root = tmp_path / "BBL"
    tree = {
        "machine": {
            "fdm_machine_common": {"name": "fdm_machine_common", "printer_structure": "i3",
                                   "machine_max_acceleration_y": ["8000"]},
            "A2L": {"name": "A2L", "inherits": "fdm_machine_common", "from": "system",
                    "printable_height": "325"},
        },
        "process": {
            "base": {"name": "base", "wall_loops": "2", "outer_wall_speed": ["200"]},
            "0.20mm Standard": {"name": "0.20mm Standard", "inherits": "base", "setting_id": "GP1",
                                "layer_height": "0.2"},
        },
        "filament": {
            "PETG base": {"name": "PETG base", "filament_type": ["PETG"], "filament_density": ["1.28"],
                          "instantiation": "false"},
            "PETG HF": {"name": "PETG HF", "inherits": "PETG base", "slow_down_layer_time": ["7"],
                        "type": "filament"},
            "PLA Basic": {"name": "PLA Basic", "filament_type": ["PLA"], "nozzle_temperature": ["220", "225"]},
        },
    }
    for cat, items in tree.items():
        (root / cat).mkdir(parents=True)
        for name, body in items.items():
            (root / cat / f"{name}.json").write_text(json.dumps(body))
    monkeypatch.setattr(presets, "PROFILES", str(root))
    return root


def test_available(profiles, monkeypatch, tmp_path):
    assert presets.available()
    monkeypatch.setattr(presets, "PROFILES", str(tmp_path / "missing"))
    assert not presets.available()


def test_flat_merges_the_inherits_chain(profiles):
    m = presets.flat("machine", "A2L")
    assert m["printer_structure"] == "i3" and m["printable_height"] == "325"
    assert m["name"] == "A2L"                         # the child wins
    assert presets.flat("machine", "nope") == {}


def test_resolve_full(profiles):
    st = presets.resolve({
        "machine": "A2L", "process": "0.20mm Standard",
        "process_overrides": {"brim_type": "outer_only"},
        "filaments": [("PETG HF", "black", {"slow_down_layer_time": ["12"]}), "PLA Basic"],
    })
    assert st["printable_height"] == "325" and st["layer_height"] == "0.2"
    assert "inherits" not in st and "setting_id" not in st and "name" not in st
    assert st["filament_type"] == ["PETG", "PLA"]
    # a key the second filament lacks takes the first filament's value
    assert st["slow_down_layer_time"] == ["12", "12"]
    assert st["nozzle_temperature"][1] == ["220", "225"]  # a list of two stays a list
    assert st["filament_settings_id"] == ["PETG HF", "PLA Basic"]
    assert st["brim_type"] == "outer_only"
    assert "instantiation" not in st and "type" not in st


def test_resolve_two_tuple_filament(profiles):
    st = presets.resolve({"filaments": [("PLA Basic", "white")]})
    assert st["filament_type"] == ["PLA"] and st["printer_structure"] == "i3"


def test_resolve_nothing(profiles):
    st = presets.resolve(None, default_material="PLA")
    assert st == {"filament_type": ["PLA"], "printer_structure": "i3"}
