"""
Generate a small clean KiCad PCB for Phase 1 assignment-expression tests.

The board intentionally reverses pad order between left and right connector
columns, creating a high-crossing bus set that should separate good and bad
layer assignments in short Freerouting runs.
"""

import argparse
import json
import os
import random
from typing import Iterable, Optional


LAYERS = {
    "0": "F.Cu",
    "1": "In1.Cu",
    "2": "In2.Cu",
    "3": "B.Cu",
}


def _split_layer(bus_id: int, layer_count: int = 4) -> int:
    period = max(1, 2 * layer_count - 2)
    pos = bus_id % period
    return pos if pos < layer_count else period - pos


def _avg(points: list[list[float]]) -> list[float]:
    return [
        round(sum(p[0] for p in points) / len(points), 4),
        round(sum(p[1] for p in points) / len(points), 4),
    ]


def _pad_entry(
    pad_id: int,
    net_id: int,
    x: float,
    y: float,
    component_id: int,
    pad_type: str,
    pad_name: str = "1",
) -> dict:
    half = 0.6
    return {
        "id": pad_id,
        "component_id": component_id,
        "pad_name": pad_name,
        "position": [round(x, 4), round(y, 4)],
        "layer": "F.Cu",
        "shape": "circle",
        "size": [1.2, 1.2],
        "type": pad_type,
        "net_id": net_id,
        "dia0": [round(x - half, 4), round(y - half, 4)],
        "dia1": [round(x + half, 4), round(y + half, 4)],
    }


def _component_entry(component_id: int, ref: str, x: float, y: float) -> dict:
    return {
        "id": component_id,
        "ref": ref,
        "value": "HC_PAD",
        "position": [round(x, 4), round(y, 4)],
        "layer": "F.Cu",
        "dia0": [round(x - 0.8, 4), round(y - 0.8, 4)],
        "dia1": [round(x + 0.8, 4), round(y + 0.8, 4)],
    }


def build_benchmark(
    net_count: int = 24,
    bus_count: int = 6,
    pad_type: str = "thru_hole",
    permutation: str = "reverse",
    seed: int = 42,
) -> tuple[dict, dict]:
    if net_count % bus_count != 0:
        raise ValueError("net_count must be divisible by bus_count")

    width = 72.0
    height = 58.0
    left_x = 9.0
    right_x = 63.0
    y0 = 8.0
    pitch = 1.75
    nets_per_bus = net_count // bus_count

    components = []
    pads = []
    nets = []

    left_pad_ids = []
    right_pad_ids = []
    y_positions = [round(y0 + i * pitch, 4) for i in range(net_count)]
    right_order = list(range(net_count))
    if permutation == "reverse":
        right_order = list(reversed(right_order))
    elif permutation == "shuffle":
        rng = random.Random(seed)
        rng.shuffle(right_order)
    else:
        raise ValueError(f"Unsupported permutation: {permutation}")
    right_rank = {net_idx: rank for rank, net_idx in enumerate(right_order)}

    for idx in range(net_count):
        net_id = idx + 1
        net_name = f"HC{idx:02d}"
        left_y = y_positions[idx]
        right_y = y_positions[right_rank[idx]]
        left_component = idx * 2
        right_component = idx * 2 + 1
        left_pad = idx * 2
        right_pad = idx * 2 + 1

        components.append(_component_entry(left_component, f"J{idx + 1}L", left_x, left_y))
        components.append(_component_entry(right_component, f"J{idx + 1}R", right_x, right_y))
        pads.append(_pad_entry(left_pad, net_id, left_x, left_y, left_component, pad_type))
        pads.append(_pad_entry(right_pad, net_id, right_x, right_y, right_component, pad_type))
        left_pad_ids.append(left_pad)
        right_pad_ids.append(right_pad)
        nets.append({
            "id": net_id,
            "name": net_name,
            "netclass": "Default",
            "pad_ids": [left_pad, right_pad],
        })

    buses = []
    for bid in range(bus_count):
        start = bid * nets_per_bus
        stop = start + nets_per_bus
        net_ids = list(range(start + 1, stop + 1))
        bus_left_pads = left_pad_ids[start:stop]
        bus_right_pads = right_pad_ids[start:stop]
        start_points = [pads[pid]["position"] for pid in bus_left_pads]
        end_points = [pads[pid]["position"] for pid in bus_right_pads]
        buses.append({
            "id": bid,
            "name": f"BUS{bid}",
            "net_ids": net_ids,
            "netclass": "Default",
            "width": 0.25,
            "start_component": "LEFT",
            "end_component": "RIGHT",
            "start_pad_ids": bus_left_pads,
            "end_pad_ids": bus_right_pads,
            "start_pos": _avg(start_points),
            "end_pos": _avg(end_points),
            "start_directions": [1],
            "end_directions": [3],
        })

    bench = {
        "meta": {
            "source": "synthetic_kicad",
            "pcb_file": "high_conflict_4L_clean.kicad_pcb",
            "coordinate_system": "board_local",
            "units": "mm",
            "pad_type": pad_type,
            "permutation": permutation,
            "seed": seed,
            "description": "Right-side pad order is permuted to force bus crossings.",
        },
        "board": {
            "width": width,
            "height": height,
            "layers": 4,
            "layer_names": dict(LAYERS),
            "boundary": {
                "dia0": [0.0, 0.0],
                "dia1": [width, height],
            },
        },
        "netclasses": {
            "Default": {
                "track_width": 0.2,
                "clearance_with_track": 0.2,
                "clearance_with_microvia": 0.15,
                "microvia_diameter": 0.45,
                "microvia_drill": 0.2,
            }
        },
        "components": components,
        "pads": pads,
        "nets": nets,
        "buses": buses,
        "obstacles": [],
    }

    assignments = {
        "bad_all_L0": {str(bus["id"]): 0 for bus in buses},
        "bad_all_L3": {str(bus["id"]): 3 for bus in buses},
        "stripe": {str(bus["id"]): bus["id"] % 4 for bus in buses},
        "split_outer_inner": {str(bus["id"]): _split_layer(bus["id"]) for bus in buses},
    }
    return bench, assignments


def _pcb_header() -> list[str]:
    return [
        "(kicad_pcb (version 20221018) (generator ba_high_conflict)",
        "",
        "  (general",
        "    (thickness 1.6)",
        "  )",
        "",
        "  (paper \"A4\")",
        "  (layers",
        "    (0 \"F.Cu\" signal \"Top\")",
        "    (1 \"In1.Cu\" signal \"Inner1\")",
        "    (2 \"In2.Cu\" signal \"Inner2\")",
        "    (31 \"B.Cu\" signal \"Bottom\")",
        "    (36 \"B.SilkS\" user \"B.Silkscreen\")",
        "    (37 \"F.SilkS\" user \"F.Silkscreen\")",
        "    (38 \"B.Mask\" user)",
        "    (39 \"F.Mask\" user)",
        "    (44 \"Edge.Cuts\" user)",
        "  )",
        "",
        "  (setup",
        "    (pad_to_mask_clearance 0.05)",
        "  )",
        "",
        "  (net 0 \"\")",
    ]


def _pcb_footprint(ref: str, x: float, y: float, net_id: int, net_name: str, pad_type: str) -> str:
    if pad_type == "smd":
        attr = "smd"
        pad = (
            f'    (pad "1" smd circle (at 0 0) (size 1.2 1.2) (layers "F.Cu" "F.Mask")\n'
            f'      (net {net_id} "{net_name}") (tstamp {ref}-pad))'
        )
    else:
        attr = "through_hole"
        pad = (
            f'    (pad "1" thru_hole circle (at 0 0) (size 1.2 1.2) '
            f'(drill 0.5) (layers *.Cu *.Mask)\n'
            f'      (net {net_id} "{net_name}") (tstamp {ref}-pad))'
        )
    return f"""  (footprint "BA:HC_PAD" (layer "F.Cu")
    (tstamp {ref}-fp)
    (at {x:.4f} {y:.4f})
    (attr {attr})
    (fp_text reference "{ref}" (at 0 -1.6) (layer "F.SilkS")
      (effects (font (size 0.8 0.8) (thickness 0.12)))
      (tstamp {ref}-ref))
    (fp_text value "" (at 0 1.6) (layer "F.Fab")
      (effects (font (size 0.8 0.8) (thickness 0.12)))
      (tstamp {ref}-val))
{pad}
  )
"""


def write_pcb(bench: dict, output_path: str) -> None:
    lines = _pcb_header()
    for net in bench["nets"]:
        lines.append(f'  (net {net["id"]} "{net["name"]}")')
    lines.append("")

    lines.extend([
        "  (gr_rect (start 0 0) (end 72 58)",
        "    (stroke (width 0.1) (type default))",
        "    (fill none) (layer \"Edge.Cuts\") (tstamp edge-rect))",
        "",
    ])

    pads_by_id = {pad["id"]: pad for pad in bench["pads"]}
    nets_by_id = {net["id"]: net["name"] for net in bench["nets"]}
    for pad in bench["pads"]:
        ref = bench["components"][pad["component_id"]]["ref"]
        x, y = pad["position"]
        lines.append(_pcb_footprint(
            ref, x, y, pad["net_id"], nets_by_id[pad["net_id"]], pad["type"]
        ))

    lines.append(")")
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def generate(
    output_dir: str,
    bench_path: str,
    assignments_path: str,
    pad_type: str,
    net_count: int,
    bus_count: int,
    permutation: str,
    seed: int,
) -> dict:
    os.makedirs(output_dir, exist_ok=True)
    bench, assignments = build_benchmark(
        net_count=net_count,
        bus_count=bus_count,
        pad_type=pad_type,
        permutation=permutation,
        seed=seed,
    )
    suffix = "smd" if pad_type == "smd" else "thru"
    pcb_path = os.path.join(output_dir, f"high_conflict_4L_{suffix}_clean.kicad_pcb")
    bench["meta"]["pcb_file"] = pcb_path

    os.makedirs(os.path.dirname(bench_path), exist_ok=True)
    write_pcb(bench, pcb_path)
    with open(bench_path, "w", encoding="utf-8") as f:
        json.dump(bench, f, indent=2)
    with open(assignments_path, "w", encoding="utf-8") as f:
        json.dump(assignments, f, indent=2)

    return {
        "pcb": pcb_path,
        "bench": bench_path,
        "assignments": assignments_path,
        "nets": len(bench["nets"]),
        "buses": len(bench["buses"]),
        "layers": bench["board"]["layers"],
        "pad_type": pad_type,
        "permutation": permutation,
        "seed": seed,
    }


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Generate Phase 1 high-conflict clean PCB")
    parser.add_argument("--output-dir", default="runs/phase1_high_conflict")
    parser.add_argument("--bench", default="benchmark/synthetic/high_conflict_4L.json")
    parser.add_argument("--assignments", default="runs/phase1_high_conflict/high_conflict_assignments.json")
    parser.add_argument("--pad-type", choices=["thru_hole", "smd"], default="thru_hole")
    parser.add_argument("--net-count", type=int, default=24)
    parser.add_argument("--bus-count", type=int, default=6)
    parser.add_argument("--permutation", choices=["reverse", "shuffle"], default="reverse")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args(argv)

    result = generate(
        args.output_dir,
        args.bench,
        args.assignments,
        args.pad_type,
        args.net_count,
        args.bus_count,
        args.permutation,
        args.seed,
    )
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
