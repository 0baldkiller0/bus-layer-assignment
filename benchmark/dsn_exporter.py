"""
SPECCTRA DSN file exporter.

Converts a KiCad .kicad_pcb to SPECCTRA DSN format compatible with
Freerouting v2.2.4, matching KiCad's native export format.
"""

import os
from typing import Optional

from kiutils.board import Board


class DSNExporter:
    """Export SPECCTRA DSN from a KiCad PCB board."""

    def __init__(self, board: Board, design_name: str = None):
        self.board = board
        self.design_name = design_name or "pcb_design"
        self._layer_letter = {}       # layer_name -> 'T'/'B'
        self._signal_layers = []      # ordered signal layer names
        self._padstack_names = {}     # (shape, w, h, layers_key) -> name
        self._padstack_defs = {}      # name -> (shape, w, h, layer_names)
        self._via_padstack_name = None
        self._net_id_to_name = {}   # net number -> net name
        self._scan_board()

    # ------------------------------------------------------------------
    # coordinate helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _fmt_um(value_mm: float) -> str:
        """mm -> um with 0.1 um precision."""
        return f"{float(value_mm) * 1000:.1f}"

    @staticmethod
    def _fmt_um6(value_mm: float) -> str:
        """mm -> um with 6-decimal precision (for padstack names)."""
        return f"{float(value_mm) * 1000:.6f}"

    @staticmethod
    def _q(s: str) -> str:
        if any(c in s for c in " ()[]"):
            return f'"{s}"'
        return s

    # ------------------------------------------------------------------
    # board scanning
    # ------------------------------------------------------------------

    def _scan_board(self):
        """Extract signal layers, footprint info, build padstack registry."""
        for i, layer in enumerate(self.board.layers):
            t = getattr(layer, "type", None)
            name = getattr(layer, "name", None) or getattr(layer, "canonicalName", None)
            if t == "signal" and name:
                self._signal_layers.append(name)
                # map layer name -> single letter for padstack naming
                if "F" in name or "Top" in name:
                    self._layer_letter[name] = "T"
                elif "B" in name or "Bottom" in name:
                    self._layer_letter[name] = "B"
                else:
                    self._layer_letter[name] = name[0].upper()

        # collect all unique padstacks from every footprint
        for fp in getattr(self.board, "footprints", []):
            for pad in getattr(fp, "pads", []):
                name = self._register_padstack(pad)
                # also track via-like through-hole pads for via definition
                ptype = str(getattr(pad, "type", "smd")).lower()
                shape = str(getattr(pad, "shape", "rect")).lower()
                size = getattr(pad, "size", None)
                w = float(size.X) if size else 1.0
                layers = getattr(pad, "layers", [])
                layer_names = [str(l) for l in layers]
                sigs = [l for l in layer_names if l in self._signal_layers]
                if ptype != "smd" and len(sigs) >= 2 and shape in ("circle", "round") and self._via_padstack_name is None:
                    self._via_padstack_name = name
                    self._via_diameter = w
                    self._via_layers = sigs

        # also collect via padstacks from trace items
        for item in getattr(self.board, "traceItems", []):
            cls_name = type(item).__name__
            if cls_name == "Via":
                layers = getattr(item, "layers", None)
                if layers and len(layers) >= 2:
                    size = float(getattr(item, "size", 0.6))
                    size_um = int(size * 1000)
                    vn = f"Via[0-{len(layers)-1}]_{size_um}:300_um"
                    if vn not in self._padstack_defs:
                        self._padstack_defs[vn] = ("circle", size, size, [str(layers[0]), str(layers[-1])])
                    if self._via_padstack_name is None:
                        self._via_padstack_name = vn
                        self._via_diameter = size
                        self._via_layers = [str(layers[0]), str(layers[-1])]

        # fallback default via if no via was found
        if self._via_padstack_name is None and len(self._signal_layers) >= 2:
            self._via_diameter = 0.6  # mm
            self._via_layers = list(self._signal_layers)
            d_um = int(self._via_diameter * 1000)
            self._via_padstack_name = f"Via[0-{len(self._signal_layers)-1}]_{d_um}:300_um"
            if self._via_padstack_name not in self._padstack_defs:
                self._padstack_defs[self._via_padstack_name] = ("circle", self._via_diameter, self._via_diameter, self._via_layers)

        # build net id -> name mapping for wire-net association
        for net in getattr(self.board, "nets", []):
            self._net_id_to_name[int(net.number)] = net.name

    def _pad_layer_letter(self, pad) -> str:
        """Return 'T', 'B', or 'A' for a pad's copper layers."""
        ptype = str(getattr(pad, "type", "smd")).lower()
        # through-hole pads span all signal layers
        if ptype in ("thru_hole", "through_hole", "connect", "np_thru_hole"):
            return "A"
        layers = [str(l) for l in getattr(pad, "layers", [])]
        sigs = self._filter_signal_layers(layers)
        if len(sigs) > 1:
            return "A"
        elif len(sigs) == 1:
            return self._layer_letter.get(sigs[0], "T")
        return "T"

    def _filter_signal_layers(self, layer_names):
        """Return signal layer names that match, handling wildcards like *.Cu."""
        result = []
        for lname in layer_names:
            if lname in self._signal_layers:
                result.append(lname)
            elif lname == "*.Cu":
                result.extend(self._signal_layers)
        return list(dict.fromkeys(result))  # dedupe preserving order

    def _register_padstack(self, pad) -> str:
        """Build/retrieve a padstack name for *pad* and record its definition."""
        shape = str(getattr(pad, "shape", "rect")).lower()
        size = getattr(pad, "size", None)
        w = float(size.X) if size else 1.0
        h = float(size.Y) if size else 1.0
        ptype = str(getattr(pad, "type", "smd")).lower()

        letter = self._pad_layer_letter(pad)
        # for 'A' (through-hole) pads, use all signal layers; for SMD, just the pad's copper layer
        if letter == "A":
            sigs = list(self._signal_layers)
        else:
            layers = [str(l) for l in getattr(pad, "layers", [])]
            sigs = self._filter_signal_layers(layers)
            if not sigs:
                if letter == "T" and self._signal_layers:
                    sigs = [self._signal_layers[0]]
                elif letter == "B" and self._signal_layers:
                    sigs = [self._signal_layers[-1]]
                else:
                    sigs = list(self._signal_layers)

        w_um = self._fmt_um6(w)
        h_um = self._fmt_um6(h)

        if shape in ("circle", "round"):
            name = f"Round[{letter}]Pad_{w_um}_um"
        elif shape == "oval":
            name = f"Oval[{letter}]Pad_{w_um}x{h_um}_um"
        else:
            name = f"Rect[{letter}]Pad_{w_um}x{h_um}_um"

        if name not in self._padstack_defs:
            self._padstack_defs[name] = (shape, w, h, list(sigs))
        return name

    def _footprint_image_name(self, fp) -> str:
        """Return the library image name for a footprint."""
        lib_id = str(getattr(fp, "libId", ""))
        if ":" in lib_id:
            return lib_id.split(":")[-1].strip()
        return str(getattr(fp, "entryName", "")) or "unknown"

    def _fp_ref(self, fp) -> Optional[str]:
        for gi in getattr(fp, "graphicItems", []):
            if getattr(gi, "type", "") == "reference":
                return str(getattr(gi, "text", "")).strip()
        return str(getattr(fp, "entryName", "")) or None

    # ------------------------------------------------------------------
    # main export
    # ------------------------------------------------------------------

    def export(self) -> str:
        lines = []
        self._emit_header(lines)
        self._emit_structure(lines)
        self._emit_placement(lines)
        self._emit_library(lines)
        self._emit_network(lines)
        self._emit_wiring(lines)
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # section emitters
    # ------------------------------------------------------------------

    def _emit_header(self, lines: list):
        lines.append(f'(pcb {self._q(self.design_name)}')
        lines.append(f'  (parser')
        lines.append(f'    (string_quote ")')
        lines.append(f'    (space_in_quoted_tokens on)')
        lines.append(f'    (host_cad "Python DSN Exporter")')
        lines.append(f'  )')
        lines.append(f'  (resolution um 10)')
        lines.append(f'  (unit um)')

    def _emit_structure(self, lines: list):
        lines.append(f'  (structure')
        for i, name in enumerate(self._signal_layers):
            lines.append(f'    (layer {self._q(name)}')
            lines.append(f'      (type signal)')
            lines.append(f'      (property')
            lines.append(f'        (index {i})')
            lines.append(f'      )')
            lines.append(f'    )')

        # via references — list all via padstacks
        for name in sorted(self._padstack_defs.keys()):
            if name.startswith("Via["):
                lines.append(f'    (via {self._q(name)})')

        # rules
        lines.append(f'    (rule')
        lines.append(f'      (width 200)')
        lines.append(f'      (clearance 127)')
        lines.append(f'      (clearance 31.75 (type smd_smd))')
        lines.append(f'    )')

        # boundary from footprint extents
        margin_mm = 5.0
        min_x = min_y = float('inf')
        max_x = max_y = float('-inf')
        for fp in getattr(self.board, "footprints", []):
            px, py = float(fp.position.X), float(fp.position.Y)
            min_x = min(min_x, px)
            min_y = min(min_y, py)
            max_x = max(max_x, px)
            max_y = max(max_y, py)
        if min_x == float('inf'):
            min_x = min_y = 0.0
            max_x = max_y = 50.0

        llx = min_x - margin_mm
        lly = min_y - margin_mm
        urx = max_x + margin_mm
        ury = max_y + margin_mm

        lines.append(f'    (boundary')
        lines.append(f'      (path pcb 0'
                     f' {self._fmt_um(llx)} {-float(lly) * 1000:.1f}'
                     f' {self._fmt_um(urx)} {-float(lly) * 1000:.1f}'
                     f' {self._fmt_um(urx)} {-float(ury) * 1000:.1f}'
                     f' {self._fmt_um(llx)} {-float(ury) * 1000:.1f}'
                     f' {self._fmt_um(llx)} {-float(lly) * 1000:.1f})')
        lines.append(f'    )')
        lines.append(f'  )')

    def _emit_placement(self, lines: list):
        lines.append(f'  (placement')
        # group components by footprint image name
        groups = {}  # image_name -> [(ref, x_mm, y_mm, rotation)]
        for fp in getattr(self.board, "footprints", []):
            ref = self._fp_ref(fp)
            img = self._footprint_image_name(fp)
            if not ref:
                continue
            x = float(fp.position.X)
            y = float(fp.position.Y)
            rot_val = getattr(fp.position, "angle", None)
            rot = -float(rot_val) if rot_val is not None else 0.0
            groups.setdefault(img, []).append((ref, x, y, rot))

        for img, comps in groups.items():
            lines.append(f'    (component {self._q(img)}')
            for ref, x, y, rot in comps:
                # KiCad SPECCTRA format: (place REF X_um Y_um side rotation)
                # Y is negated, side is 'front' or 'back'
                side = "front"  # all our components are front-side
                lines.append(f'      (place {ref} {self._fmt_um(x)} {-float(y) * 1000:.1f}'
                             f' {side} {rot:.0f})')
            lines.append(f'    )')
        lines.append(f'  )')

    def _emit_library(self, lines: list):
        lines.append(f'  (library')
        seen_images = set()
        for fp in getattr(self.board, "footprints", []):
            img = self._footprint_image_name(fp)
            if not img or img in seen_images:
                continue
            seen_images.add(img)

            # compute outline from pad extents
            pad_min_x = pad_min_y = float('inf')
            pad_max_x = pad_max_y = float('-inf')
            pads = []
            for pad in getattr(fp, "pads", []):
                num = str(getattr(pad, "number", "1") or "1").strip()
                if not num:
                    continue  # skip pads with no number (e.g. np_thru_hole)
                px = float(pad.position.X)
                py = float(pad.position.Y)
                pads.append((num, px, py, pad))
                pad_min_x = min(pad_min_x, px)
                pad_min_y = min(pad_min_y, py)
                pad_max_x = max(pad_max_x, px)
                pad_max_y = max(pad_max_y, py)
            if pad_min_x == float('inf'):
                pad_min_x = pad_min_y = pad_max_x = pad_max_y = 0.0

            # outline with 0.25mm margin
            ol_margin = 0.25
            ol_x1 = pad_min_x - ol_margin
            ol_y1 = pad_min_y - ol_margin
            ol_x2 = pad_max_x + ol_margin
            ol_y2 = pad_max_y + ol_margin

            lines.append(f'    (image {self._q(img)}')
            lines.append(f'      (outline (polygon signal 0'
                         f' {self._fmt_um(ol_x2)} {self._fmt_um(ol_y2)}'
                         f' {self._fmt_um(ol_x2)} {self._fmt_um(ol_y1)}'
                         f' {self._fmt_um(ol_x1)} {self._fmt_um(ol_y1)}'
                         f' {self._fmt_um(ol_x1)} {self._fmt_um(ol_y2)}))')

            for num, px, py, pad in pads:
                ps_name = self._register_padstack(pad)
                lines.append(f'      (pin {ps_name} {num}'
                             f' {self._fmt_um(px)} {self._fmt_um(py)})')
            lines.append(f'    )')

        # emit all padstack definitions
        for name, (shape, w, h, layer_names) in self._padstack_defs.items():
            lines.append(f'    (padstack {self._q(name)}')
            for lname in layer_names:
                w_um = w * 1000 / 2  # half-width for rect
                h_um = h * 1000 / 2  # half-height for rect
                if shape in ("circle", "round"):
                    lines.append(f'      (shape (circle {self._q(lname)} {self._fmt_um(w)}))')
                else:
                    lines.append(f'      (shape (rect {self._q(lname)}'
                                 f' {-w_um:.1f} {-h_um:.1f} {w_um:.1f} {h_um:.1f}))')
            lines.append(f'      (attach off)')
            lines.append(f'    )')
        lines.append(f'  )')

    def _emit_network(self, lines: list):
        # build net -> pin_refs mapping
        net_pins = {}
        for fp in getattr(self.board, "footprints", []):
            ref = self._fp_ref(fp)
            if not ref:
                continue
            for pad in getattr(fp, "pads", []):
                net = getattr(pad, "net", None)
                if net is None:
                    continue
                net_name = getattr(net, "name", "")
                net_id = getattr(net, "number", 0)
                pin_num = str(getattr(pad, "number", "1"))
                pin_ref = f"{ref}-{pin_num}"
                key = net_name or f"Net-{net_id}"
                net_pins.setdefault(key, []).append(pin_ref)

        lines.append(f'  (network')
        net_labels = sorted(net_pins.keys())
        for net_label in net_labels:
            pins = net_pins[net_label]
            if not pins:
                continue
            unique_pins = list(dict.fromkeys(pins))
            lines.append(f'    (net {self._q(net_label)}')
            lines.append(f'      (pins {" ".join(unique_pins)})')
            lines.append(f'    )')

        # emit a default net class with rules
        all_nets = [n for n in net_labels if net_pins[n]]
        if all_nets:
            lines.append(f'    (class kicad_default'
                         f' {" ".join(self._q(n) for n in all_nets)}')
            if self._via_padstack_name:
                lines.append(f'      (circuit')
                lines.append(f'        (use_via {self._q(self._via_padstack_name)})')
                lines.append(f'      )')
            lines.append(f'      (rule')
            lines.append(f'        (width 200)')
            lines.append(f'        (clearance 127)')
            lines.append(f'      )')
            lines.append(f'    )')

        lines.append(f'  )')

    def _emit_wiring(self, lines: list):
        # Group trace items by net so Freerouting can associate wires with nets
        net_items = {}  # net_name -> [(type, item)]
        unassigned = []
        for item in getattr(self.board, "traceItems", []):
            cls_name = type(item).__name__
            net_id = getattr(item, "net", None)
            if net_id is not None:
                net_name = self._net_id_to_name.get(int(net_id))
                if net_name:
                    net_items.setdefault(net_name, []).append((cls_name, item))
                    continue
            unassigned.append((cls_name, item))

        lines.append(f'  (wiring')

        # Emit net-scoped wires/vias
        for net_name, items in net_items.items():
            lines.append(f'    (net {self._q(net_name)}')
            for cls_name, item in items:
                if cls_name == "Segment":
                    self._emit_segment(lines, item, indent='      ')
                elif cls_name == "Via":
                    self._emit_via(lines, item, indent='      ')
            lines.append(f'    )')

        # Emit unassigned items (fallback - shouldn't happen in practice)
        for cls_name, item in unassigned:
            if cls_name == "Segment":
                self._emit_segment(lines, item)
            elif cls_name == "Via":
                self._emit_via(lines, item)

        lines.append(f'  )')
        lines.append(f')')

    def _emit_segment(self, lines: list, seg, indent='    '):
        layer = str(getattr(seg, "layer", self._signal_layers[0] if self._signal_layers else "F.Cu"))
        width = float(getattr(seg, "width", 0.25))
        sx, sy = float(seg.start.X), float(seg.start.Y)
        ex, ey = float(seg.end.X), float(seg.end.Y)
        locked = bool(getattr(seg, "locked", False))
        lines.append(f'{indent}(wire')
        lines.append(f'{indent}  (path {self._q(layer)} {self._fmt_um(width)}'
                     f' {self._fmt_um(sx)} {-float(sy) * 1000:.1f}'
                     f' {self._fmt_um(ex)} {-float(ey) * 1000:.1f})')
        if locked:
            lines.append(f'{indent}  (type protect)')
        lines.append(f'{indent})')

    def _emit_via(self, lines: list, via, indent='    '):
        x, y = float(via.position.X), float(via.position.Y)
        layers = getattr(via, "layers", None)
        if not layers or len(layers) < 2:
            return
        # build via padstack name from via size if needed
        size = float(getattr(via, "size", 0.6))
        size_um = int(size * 1000)
        via_name = f"Via[0-{len(layers)-1}]_{size_um}:300_um"
        # register the padstack if not already present
        if via_name not in self._padstack_defs:
            self._padstack_defs[via_name] = ("circle", size, size, list(layers[:2]))
        lines.append(f'{indent}(via {self._q(via_name)}'
                     f' {self._fmt_um(x)} {-float(y) * 1000:.1f})')


def export_dsn(pcb_path: str, output_path: Optional[str] = None) -> str:
    board = Board().from_file(pcb_path)
    design_name = os.path.splitext(os.path.basename(pcb_path))[0]
    exporter = DSNExporter(board, design_name)
    dsn_content = exporter.export()
    if output_path:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(dsn_content)
        print(f"DSN exported to {output_path}")
    return dsn_content


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Export KiCad PCB to SPECCTRA DSN")
    ap.add_argument("pcb", help="Path to .kicad_pcb file")
    ap.add_argument("-o", "--output", help="Output .dsn path")
    args = ap.parse_args()
    export_dsn(args.pcb, args.output)
