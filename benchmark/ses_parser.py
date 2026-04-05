"""
SPECCTRA SES file parser.

Parses Freerouting output (Specctra session .ses format) to extract
routing metrics: wirelength, vias, segments, unrouted nets.
"""

import re
import os
from typing import Optional
from dataclasses import dataclass, field


@dataclass
class SESNet:
    net_name: str
    wirelength: float = 0.0
    segments: int = 0
    vias: int = 0


@dataclass
class SESResult:
    nets: dict = field(default_factory=dict)
    total_wirelength: float = 0.0
    total_segments: int = 0
    total_vias: int = 0
    routed_net_count: int = 0
    unrouted_net_count: int = 0
    raw: str = ""


def parse_ses(ses_path: str) -> SESResult:
    """Parse a SPECCTRA session file and extract routing metrics."""
    with open(ses_path, "r", encoding="utf-8", errors="replace") as f:
        content = f.read()

    result = SESResult(raw=content[:500])

    res_m = re.search(r'\(resolution\s+um\s+(\d+)\)', content)
    scale = 1.0 / (int(res_m.group(1)) * 1000) if res_m else 0.001

    current_net = None
    in_wire = False
    in_path = False
    in_via = False
    wire_coords = []

    for line in content.split("\n"):
        stripped = line.strip()

        m = re.match(r'\(\s*net\s+(?:"([^"]*)"|([^\s()]+))', stripped)
        if m and not stripped.startswith("(network_out"):
            current_net = m.group(1) or m.group(2)
            if current_net not in result.nets:
                result.nets[current_net] = SESNet(net_name=current_net)
            continue

        m = re.match(r'\(\s*via\s+"([^"]*)"\s+(-?[\d.]+)\s+(-?[\d.]+)\s*\)?\s*', stripped)
        if m:
            in_via = True
            if current_net and current_net in result.nets:
                result.nets[current_net].vias += 1
            continue

        # Handle closing paren of multi-line via
        if stripped == ")" and in_via:
            in_via = False
            continue

        if stripped.startswith("(wire") and not stripped.startswith("(wire "):
            in_wire = True
            continue

        if in_wire:
            if stripped.startswith("(path"):
                tokens = stripped.replace("(", "").replace(")", "").split()
                if len(tokens) >= 3:
                    wire_coords = list(tokens[3:])
                in_path = True
            elif stripped == ")":
                if in_path:
                    # End of path — process accumulated coords
                    if wire_coords:
                        try:
                            coords = [float(p) for p in wire_coords]
                        except ValueError:
                            coords = []
                        if len(coords) >= 4:
                            length = 0.0
                            for i in range(0, len(coords) - 2, 2):
                                dx = coords[i + 2] - coords[i]
                                dy = coords[i + 3] - coords[i + 1]
                                length += (dx * dx + dy * dy) ** 0.5
                            length *= scale
                            n_edges = len(coords) // 2 - 1
                            if current_net and current_net in result.nets:
                                result.nets[current_net].wirelength += length
                                result.nets[current_net].segments += max(n_edges, 1)
                    in_path = False
                    wire_coords = []
                else:
                    # End of wire block
                    in_wire = False
            elif in_path:
                # Accumulate coordinate tokens from multi-line path
                tokens = stripped.split()
                wire_coords.extend(tokens)

    for net in result.nets.values():
        result.total_wirelength += net.wirelength
        result.total_segments += net.segments
        result.total_vias += net.vias
        if net.segments > 0 or net.vias > 0:
            result.routed_net_count += 1

    return result


def _parse_resolution_header(content: str) -> float:
    """Extract resolution from DSN header (mm per internal unit)."""
    m = re.search(r'\(resolution\s+mm\s+(\d+)\)', content)
    if m:
        return 1.0 / int(m.group(1))
    return 1.0  # default: already in mm


def parse_dsn_nets(dsn_path: str) -> set:
    """Parse net names from a SPECCTRA DSN file.

    Handles both quoted nets like (net "Net-(C14-Pad1)") and
    unquoted nets like (net /1.8VDD).
    """
    with open(dsn_path, "r", encoding="utf-8", errors="replace") as f:
        content = f.read()
    quoted = set(re.findall(r'\(\s*net\s+"([^"]+)"', content))
    unquoted = set(re.findall(r'\(\s*net\s+([A-Za-z0-9_/\-.]+)(?:\s|\))', content))
    return quoted | unquoted


def compare_ses_to_dsn(ses_path: str, dsn_path: str) -> dict:
    """Compare SES routing output against DSN netlist.

    Returns dict with routed/uunrouted net sets and metrics comparison.
    """
    dsn_nets = parse_dsn_nets(dsn_path)
    ses = parse_ses(ses_path)
    ses_nets = set(ses.nets.keys())
    return {
        "ses_result": ses,
        "dsn_total_nets": len(dsn_nets),
        "routed_nets": dsn_nets & ses_nets,
        "unrouted_nets": dsn_nets - ses_nets,
        "extra_in_ses": ses_nets - dsn_nets,
        "routed_count": len(dsn_nets & ses_nets),
        "unrouted_count": len(dsn_nets - ses_nets),
    }


def _parse_dsn_resolution_safe(dsn_content: str, default: float = 1.0) -> float:
    """Extract resolution scale from DSN content."""
    m = re.search(r'\(resolution\s+um\s+(\d+)\)', dsn_content)
    if m:
        return 1.0 / int(m.group(1))
    return default
