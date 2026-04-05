"""
SES-to-KiCad PCB converter.

Parses Freerouting SES output and merges routed wires/vias back into
a KiCad .kicad_pcb file, producing a routed PCB.
"""

import re
import os
import sys
from typing import Optional

from kiutils.board import Board
from kiutils.items.brditems import Segment, Via
from kiutils.items.common import Position


def _parse_ses_routes(ses_path: str) -> dict:
    """Parse SES network_out section, return {net_name: [(type, layer, coords)]}.

    Each wire is: ('wire', layer_name, width, [(x1,y1), (x2,y2), ...])
    Each via is:  ('via', None, None, [(x, y)])
    Coordinates are in um * 10 (SPECCTRA resolution um 10).
    """
    with open(ses_path, "r", encoding="utf-8", errors="replace") as f:
        content = f.read()

    # Extract resolution: (resolution um N) → 1 unit = (1/N) um
    # Convert to mm: value / N / 1000 = value / (N * 1000)
    res_m = re.search(r'\(resolution\s+um\s+(\d+)\)', content)
    scale = 1.0 / (int(res_m.group(1)) * 1000) if res_m else 0.001

    # Find the (routes ...) block
    routes_start = content.find("(routes")
    if routes_start < 0:
        return {}

    net_items = {}  # net_name -> [(type, layer, width, coords_list)]
    current_net = None
    in_wire = False
    in_path = False
    wire_layer = None
    wire_width = None
    wire_coords = []

    # Parse from routes_start to end
    tail = content[routes_start:]

    for line in tail.split("\n"):
        stripped = line.strip()

        m = re.match(r'\(\s*net\s+(?:"([^"]*)"|([^\s()]+))', stripped)
        if m and not stripped.startswith("(network_out"):
            current_net = m.group(1) or m.group(2)
            net_items.setdefault(current_net, [])
            continue

        # Via: (via "NAME" X Y) or (via "NAME" X Y \n )
        m = re.match(r'\(\s*via\s+"([^"]*)"\s+(-?[\d.]+)\s+(-?[\d.]+)\s*\)?\s*', stripped)
        if m:
            if current_net:
                x = float(m.group(2)) * scale
                y = -float(m.group(3)) * scale  # SPECCTRA Y↓ → KiCad Y↑
                net_items[current_net].append(("via", None, None, [(x, y)]))
            continue

        if stripped.startswith("(wire") and not stripped.startswith("(wire "):
            in_wire = True
            wire_layer = None
            wire_width = None
            wire_coords = []
            continue

        if in_wire:
            if stripped.startswith("(path"):
                tokens = stripped.replace("(", "").replace(")", "").split()
                if len(tokens) >= 3:
                    wire_layer = tokens[1]
                    wire_width = float(tokens[2]) * scale
                    wire_coords = []
                    for i in range(3, len(tokens) - 1, 2):
                        try:
                            x = float(tokens[i]) * scale
                            y = -float(tokens[i + 1]) * scale  # SPECCTRA Y↓ → KiCad Y↑
                            wire_coords.append((x, y))
                        except (ValueError, IndexError):
                            pass
                in_path = True
            elif stripped == ")":
                if in_path:
                    if wire_coords and current_net and wire_layer:
                        net_items[current_net].append(
                            ("wire", wire_layer, wire_width, list(wire_coords))
                        )
                    in_path = False
                    wire_coords = []
                else:
                    in_wire = False
            elif in_path:
                # Multi-line coordinate tokens
                tokens = stripped.split()
                for i in range(0, len(tokens) - 1, 2):
                    try:
                        x = float(tokens[i]) * scale
                        y = -float(tokens[i + 1]) * scale  # SPECCTRA Y↓ → KiCad Y↑
                        wire_coords.append((x, y))
                    except (ValueError, IndexError):
                        pass

    return net_items


def merge_ses_to_pcb(pcb_path: str, ses_path: str, output_path: str = None) -> str:
    """Merge Freerouting SES routes into a KiCad PCB, producing a routed PCB.

    Args:
        pcb_path: Source .kicad_pcb (with nets defined)
        ses_path: Freerouting SES output
        output_path: Output .kicad_pcb path (auto-generated if None)

    Returns:
        Path to the routed .kicad_pcb file
    """
    if output_path is None:
        base = os.path.splitext(pcb_path)[0]
        output_path = f"{base}_routed.kicad_pcb"

    board = Board().from_file(pcb_path)

    # Build net number → name and name → number maps
    net_name_to_num = {}
    for net in getattr(board, "nets", []):
        net_name_to_num[net.name] = int(net.number)

    # Parse SES routes
    routes = _parse_ses_routes(ses_path)
    print(f"Parsed {len(routes)} nets from SES")

    # Clear existing trace items (we're replacing them with routed ones)
    board.traceItems = []

    tstamp_counter = 0
    total_wires = 0
    total_vias = 0

    for net_name, items in routes.items():
        net_num = net_name_to_num.get(net_name)
        if net_num is None:
            # Try matching without leading slash
            alt_name = net_name.lstrip("/")
            for name, num in net_name_to_num.items():
                if name.lstrip("/") == alt_name:
                    net_num = num
                    break
        if net_num is None:
            print(f"  WARNING: net '{net_name}' not found in PCB, skipping {len(items)} items")
            continue

        for item_type, layer, width, coords in items:
            if item_type == "wire":
                for i in range(len(coords) - 1):
                    x1, y1 = coords[i]
                    x2, y2 = coords[i + 1]
                    board.traceItems.append(Segment(
                        start=Position(x1, y1),
                        end=Position(x2, y2),
                        width=width,
                        layer=layer,
                        net=net_num,
                        tstamp=f"fr-{tstamp_counter}",
                    ))
                    tstamp_counter += 1
                    total_wires += 1
            elif item_type == "via":
                x, y = coords[0]
                # Find the layers this via connects
                layers = []
                if layer:
                    layers = [layer]
                else:
                    # Default: F.Cu + B.Cu
                    layers = ["F.Cu", "B.Cu"]
                board.traceItems.append(Via(
                    position=Position(x, y),
                    size=0.6,
                    drill=0.3,
                    layers=layers,
                    net=net_num,
                    tstamp=f"fr-{tstamp_counter}",
                ))
                tstamp_counter += 1
                total_vias += 1

    board.to_file(output_path)
    print(f"Merged {total_wires} segments, {total_vias} vias → {output_path}")
    return output_path


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Merge Freerouting SES routes into KiCad PCB")
    ap.add_argument("--pcb", required=True, help="Source .kicad_pcb file")
    ap.add_argument("--ses", required=True, help="Freerouting .ses output")
    ap.add_argument("-o", "--output", default=None, help="Output .kicad_pcb path")
    args = ap.parse_args()
    merge_ses_to_pcb(args.pcb, args.ses, args.output)