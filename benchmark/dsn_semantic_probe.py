"""
Minimal DSN semantic probe for Freerouting.

The probe creates tiny KiCad PCB files and routes them through the existing
KiCad -> DSN -> Freerouting pipeline. It checks whether pre-existing wires,
especially locked/protected wires, are treated as physical constraints.
"""

import argparse
import json
import os
import shutil
import sys
from typing import Iterable, Optional

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from benchmark.dsn_exporter import export_dsn
from benchmark.freerouting_headless import route_pcb


def _segment_text(
    locked: bool,
    start: tuple[float, float],
    end: tuple[float, float],
    width: float,
    layer: str,
    net: int,
    tstamp: str,
) -> str:
    lock = " locked" if locked else ""
    return (
        f'  (segment{lock} (start {start[0]} {start[1]}) '
        f'(end {end[0]} {end[1]}) (width {width}) '
        f'(layer "{layer}") (net {net}) (tstamp {tstamp}))\n'
    )


def _minimal_pcb(case: str) -> str:
    extra_segments = ""
    block_footprints = ""
    if case == "dummy_plain":
        block_footprints = _block_footprints()
        extra_segments = _segment_text(
            locked=False,
            start=(50, 5),
            end=(50, 95),
            width=2.0,
            layer="F.Cu",
            net=2,
            tstamp="probe-dummy-plain",
        )
    elif case == "dummy_protect":
        block_footprints = _block_footprints()
        extra_segments = _segment_text(
            locked=True,
            start=(50, 5),
            end=(50, 95),
            width=2.0,
            layer="F.Cu",
            net=2,
            tstamp="probe-dummy-protect",
        )
    elif case == "same_net_protect":
        extra_segments = _segment_text(
            locked=True,
            start=(50, 5),
            end=(50, 95),
            width=2.0,
            layer="F.Cu",
            net=1,
            tstamp="probe-same-net-protect",
        )
    elif case != "clean":
        raise ValueError(f"Unknown probe case: {case}")

    return f"""(kicad_pcb (version 20221018) (generator codex-dsn-probe)

  (general
    (thickness 1.6)
  )

  (paper "A4")
  (layers
    (0 "F.Cu" signal "Top")
    (31 "B.Cu" signal "Bottom")
    (32 "B.Adhes" user "B.Adhesive")
    (33 "F.Adhes" user "F.Adhesive")
    (34 "B.Paste" user)
    (35 "F.Paste" user)
    (36 "B.SilkS" user "B.Silkscreen")
    (37 "F.SilkS" user "F.Silkscreen")
    (38 "B.Mask" user)
    (39 "F.Mask" user)
    (44 "Edge.Cuts" user)
  )

  (setup
    (pad_to_mask_clearance 0.05)
  )

  (net 0 "")
  (net 1 "TARGET")
  (net 2 "BLOCK")

  (gr_rect (start 0 0) (end 100 100)
    (stroke (width 0.1) (type solid)) (fill none) (layer "Edge.Cuts") (tstamp edge-0))

  (footprint "Probe:THPad" (layer "F.Cu")
    (tstamp probe-left)
    (at 10 50)
    (attr through_hole)
    (fp_text reference "J1" (at 0 -3) (layer "F.SilkS")
      (effects (font (size 1 1) (thickness 0.15)))
      (tstamp ref-left))
    (fp_text value "" (at 0 3) (layer "F.Fab")
      (effects (font (size 1 1) (thickness 0.15)))
      (tstamp val-left))
    (pad "1" thru_hole circle (at 0 0) (size 2 2) (drill 0.7) (layers *.Cu *.Mask)
      (net 1 "TARGET") (tstamp pad-left))
  )

  (footprint "Probe:THPad" (layer "F.Cu")
    (tstamp probe-right)
    (at 90 50)
    (attr through_hole)
    (fp_text reference "J2" (at 0 -3) (layer "F.SilkS")
      (effects (font (size 1 1) (thickness 0.15)))
      (tstamp ref-right))
    (fp_text value "" (at 0 3) (layer "F.Fab")
      (effects (font (size 1 1) (thickness 0.15)))
      (tstamp val-right))
    (pad "1" thru_hole circle (at 0 0) (size 2 2) (drill 0.7) (layers *.Cu *.Mask)
      (net 1 "TARGET") (tstamp pad-right))
  )

{block_footprints}
{extra_segments})\n"""


def _block_footprints() -> str:
    return """  (footprint "Probe:BlockPad" (layer "F.Cu")
    (tstamp probe-block-top)
    (at 50 5)
    (attr through_hole)
    (fp_text reference "B1" (at 0 -3) (layer "F.SilkS")
      (effects (font (size 1 1) (thickness 0.15)))
      (tstamp ref-block-top))
    (fp_text value "" (at 0 3) (layer "F.Fab")
      (effects (font (size 1 1) (thickness 0.15)))
      (tstamp val-block-top))
    (pad "1" thru_hole circle (at 0 0) (size 2 2) (drill 0.7) (layers *.Cu *.Mask)
      (net 2 "BLOCK") (tstamp pad-block-top))
  )

  (footprint "Probe:BlockPad" (layer "F.Cu")
    (tstamp probe-block-bottom)
    (at 50 95)
    (attr through_hole)
    (fp_text reference "B2" (at 0 -3) (layer "F.SilkS")
      (effects (font (size 1 1) (thickness 0.15)))
      (tstamp ref-block-bottom))
    (fp_text value "" (at 0 3) (layer "F.Fab")
      (effects (font (size 1 1) (thickness 0.15)))
      (tstamp val-block-bottom))
    (pad "1" thru_hole circle (at 0 0) (size 2 2) (drill 0.7) (layers *.Cu *.Mask)
      (net 2 "BLOCK") (tstamp pad-block-bottom))
  )
"""


def _write_probe_pcb(case: str, output_dir: str) -> str:
    path = os.path.join(output_dir, f"probe_{case}.kicad_pcb")
    with open(path, "w", encoding="utf-8") as f:
        f.write(_minimal_pcb(case))
    return path


def _count_protect(dsn_path: str) -> int:
    with open(dsn_path, "r", encoding="utf-8") as f:
        return f.read().count("(type protect)")


def run_probe(
    output_dir: str,
    passes: int = 20,
    threads: int = 4,
    timeout_seconds: int = 120,
    keep_input_pcbs: bool = True,
) -> list[dict]:
    os.makedirs(output_dir, exist_ok=True)
    cases = ["clean", "dummy_plain", "dummy_protect", "same_net_protect"]
    results = []

    print("\n" + "=" * 72)
    print("Minimal DSN semantic probe")
    print(f"  output={output_dir}")
    print(f"  passes={passes}, threads={threads}, timeout={timeout_seconds}s")

    for case in cases:
        entry = {"case": case}
        pcb_path = _write_probe_pcb(case, output_dir)
        dsn_preview = os.path.join(output_dir, f"probe_{case}_preview.dsn")

        try:
            export_dsn(pcb_path, dsn_preview)
            entry["preview_dsn"] = dsn_preview
            entry["preview_protect_count"] = _count_protect(dsn_preview)

            real = route_pcb(
                pcb_path,
                output_dir=output_dir,
                passes=passes,
                threads=threads,
                timeout_seconds=timeout_seconds,
            )
            entry.update({
                "real_wirelength_mm": real["total_wirelength"],
                "real_segments": real["total_segments"],
                "real_vias": real["total_vias"],
                "real_routed_nets": real["routed_net_count"],
                "real_unrouted_nets": real["unrouted_net_count"],
                "real_runtime_sec": real["runtime_seconds"],
                "dsn": real["dsn"],
                "ses": real["ses"],
                "routed_pcb": real["routed_pcb"],
            })
            entry["dsn_protect_count"] = _count_protect(real["dsn"])
            print(
                f"  {case:<18} wl={entry['real_wirelength_mm']:>7.2f}mm "
                f"seg={entry['real_segments']:>3} vias={entry['real_vias']:>2} "
                f"unrouted={entry['real_unrouted_nets']:>2} "
                f"protect={entry['dsn_protect_count']}"
            )
        except Exception as exc:
            entry["error"] = str(exc)
            print(f"  {case:<18} ERROR: {exc}")
        finally:
            if not keep_input_pcbs and os.path.exists(pcb_path):
                os.remove(pcb_path)

        results.append(entry)

    out_json = os.path.join(output_dir, "dsn_semantic_probe.json")
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump({"results": results}, f, indent=2, default=str)
    print(f"  saved={out_json}")
    return results


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Minimal DSN semantic probe")
    parser.add_argument("--output-dir", default="runs/dsn_semantic_probe")
    parser.add_argument("--passes", type=int, default=20)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--remove-input-pcbs", action="store_true")
    args = parser.parse_args(argv)

    run_probe(
        output_dir=args.output_dir,
        passes=args.passes,
        threads=args.threads,
        timeout_seconds=args.timeout,
        keep_input_pcbs=not args.remove_input_pcbs,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
