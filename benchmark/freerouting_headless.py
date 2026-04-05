"""
Freerouting headless wrapper.

Automates the full pipeline:
  KiCad PCB → SPECCTRA DSN → Freerouting CLI → SES → routed KiCad PCB

One command to route a KiCad PCB without opening any GUI.
"""

import os
import subprocess
import tempfile
import shutil
import json
import time
import argparse
import sys
from typing import Optional

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from benchmark.dsn_exporter import DSNExporter, export_dsn
from benchmark.ses_parser import parse_ses, parse_dsn_nets, SESResult
from benchmark.ses_to_pcb import merge_ses_to_pcb

# === Default paths ===
FREEROUTER_DIR = os.path.join(PROJECT_ROOT, "freerouter")

def _find_java() -> str:
    """Find the Java executable bundled in the freerouter directory."""
    for root, dirs, files in os.walk(FREEROUTER_DIR):
        if os.name == "nt":
            for f in files:
                if f == "java.exe":
                    return os.path.join(root, f)
        else:
            if "bin" in dirs:
                java = os.path.join(root, "bin", "java")
                if os.path.isfile(java):
                    return java
    return "java"


def _find_jar() -> str:
    """Find the freerouting JAR."""
    override = os.environ.get("FREEROUTING_JAR")
    if override:
        return os.path.abspath(override)
    for fname in os.listdir(FREEROUTER_DIR):
        if fname.endswith(".jar") and "freerouting" in fname.lower():
            return os.path.join(FREEROUTER_DIR, fname)
    return os.path.join(FREEROUTER_DIR, "freerouting.jar")


def route_pcb(
    pcb_path: str,
    output_dir: str = None,
    passes: int = 20,
    threads: int = 4,
    strategy: str = "hybrid",
    timeout_seconds: int = 600,
    corridor_guide_path: Optional[str] = None,
    corridor_penalty: float = 0.0,
    corridor_mode: str = "soft",
    corridor_scale: float = 1.0,
    corridor_max_factor: float = 1.0,
    corridor_tie_break_weight: float = 0.1,
) -> dict:
    """
    Route a single KiCad PCB using Freerouting headless.

    Args:
        pcb_path: Path to .kicad_pcb file
        output_dir: Directory for DSN/SES/routed PCB output
        passes: Number of autorouter passes
        threads: Number of threads for optimization
        strategy: Optimization strategy (greedy, global, hybrid)
        timeout_seconds: Max time for Freerouting (seconds)
        corridor_guide_path: Path to corridor guide JSON (for BA patched jar)
        corridor_penalty: Base penalty factor (0 disables corridor guide)
        corridor_mode: "hard", "soft", or "tie_breaker"
        corridor_scale: Length-scale factor for soft/tie_breaker modes
        corridor_max_factor: Upper bound on outside penalty factor
        corridor_tie_break_weight: Microscopic sorting bias in tie_breaker mode

    Returns:
        dict: Routing result with metrics
    """
    pcb_path = os.path.abspath(pcb_path)
    if not pcb_path.endswith(".kicad_pcb"):
        raise ValueError(f"Expected .kicad_pcb file, got {pcb_path}")

    base = os.path.splitext(os.path.basename(pcb_path))[0]
    if output_dir is None:
        output_dir = os.path.dirname(pcb_path)

    os.makedirs(output_dir, exist_ok=True)

    dsn_path = os.path.abspath(os.path.join(output_dir, f"{base}.dsn"))
    ses_path = os.path.abspath(os.path.join(output_dir, f"{base}.ses"))

    # Step 1: Export DSN
    print(f"[1/4] Exporting DSN from {os.path.basename(pcb_path)} ...")
    dsn_content = export_dsn(pcb_path, dsn_path)
    print(f"       DSN: {dsn_path} ({len(dsn_content)} bytes)")

    # Step 2: Run Freerouting
    print(f"[2/4] Running Freerouting (passes={passes}, threads={threads}) ...")
    java = _find_java()
    jar = _find_jar()

    cmd = [java]
    if corridor_guide_path and corridor_penalty > 0:
        cmd.extend([
            f"-Dba.corridor.guide={os.path.abspath(corridor_guide_path)}",
            f"-Dba.corridor.penalty={corridor_penalty}",
            f"-Dba.corridor.mode={corridor_mode}",
            f"-Dba.corridor.scale={corridor_scale}",
            f"-Dba.corridor.max_factor={corridor_max_factor}",
            f"-Dba.corridor.tie_break_weight={corridor_tie_break_weight}",
        ])
    cmd.extend([
        "-jar", jar,
        "-de", dsn_path,
        "-do", ses_path,
        "-mp", str(passes),
        "-mt", str(threads),
        "-us", strategy,
        "-dct", "5",
        "-da",
        "-oit", "0.5",
        "--gui.enabled=false",
    ])
    print(f"       {' '.join(cmd)}")

    t0 = time.time()
    proc = subprocess.run(
        cmd,
        cwd=output_dir,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_seconds,
    )
    elapsed = time.time() - t0

    if proc.returncode != 0:
        stderr_tail = proc.stderr[-500:] if proc.stderr else "(no stderr)"
        raise RuntimeError(f"Freerouting failed (exit {proc.returncode}): {stderr_tail}")
    if not os.path.exists(ses_path):
        stdout_tail = proc.stdout[-1000:] if proc.stdout else "(no stdout)"
        stderr_tail = proc.stderr[-1000:] if proc.stderr else "(no stderr)"
        raise RuntimeError(
            "Freerouting finished without writing SES output. "
            f"stdout tail: {stdout_tail} stderr tail: {stderr_tail}"
        )

    print(f"       Completed in {elapsed:.1f}s")

    # Step 3: Parse SES
    print(f"[3/4] Parsing SES output ...")
    routes = parse_ses(ses_path)
    dsn_nets = parse_dsn_nets(dsn_path)
    routed_nets = dsn_nets & set(routes.nets.keys())
    unrouted_nets = dsn_nets - set(routes.nets.keys())

    result = {
        "pcb": os.path.basename(pcb_path),
        "dsn": dsn_path,
        "ses": ses_path,
        "runtime_seconds": round(elapsed, 1),
        "passes": passes,
        "threads": threads,
        "strategy": strategy,
        "corridor_guide_path": corridor_guide_path,
        "corridor_penalty": corridor_penalty,
        "corridor_mode": corridor_mode,
        "corridor_scale": corridor_scale,
        "corridor_max_factor": corridor_max_factor,
        "corridor_tie_break_weight": corridor_tie_break_weight,
        "total_wirelength": round(routes.total_wirelength, 2),
        "total_segments": routes.total_segments,
        "total_vias": routes.total_vias,
        "dsn_total_net_count": len(dsn_nets),
        "routed_net_count": len(routed_nets),
        "unrouted_net_count": len(unrouted_nets),
        "unrouted_nets": sorted(unrouted_nets),
        "per_net": {
            name: {
                "wirelength": round(n.wirelength, 2),
                "segments": n.segments,
                "vias": n.vias,
            }
            for name, n in routes.nets.items()
            if n.segments > 0 or n.vias > 0
        },
    }

    # Save SES as JSON for easy analysis
    ses_json_path = os.path.join(output_dir, f"{base}_ses.json")
    with open(ses_json_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, default=str)

    print(f"       Wirelength: {result['total_wirelength']:.1f}mm")
    print(f"       Segments: {result['total_segments']}, Vias: {result['total_vias']}")
    print(f"       Routed: {result['routed_net_count']}, Unrouted: {result['unrouted_net_count']}")
    print(f"       SES parse saved: {ses_json_path}")

    # Step 4: Merge SES routes back into KiCad PCB
    routed_pcb_path = os.path.join(output_dir, f"{base}_routed.kicad_pcb")
    print(f"\n[4/4] Merging SES routes into routed PCB ...")
    merge_ses_to_pcb(pcb_path, ses_path, routed_pcb_path)
    result["routed_pcb"] = routed_pcb_path
    print(f"       Routed PCB: {routed_pcb_path}")

    return result


def batch_route(
    pcb_paths: list[str],
    output_dir: str,
    **kwargs,
) -> dict:
    """Route multiple PCB files sequentially, collecting results for comparison."""
    all_results = {}
    for pcb_path in pcb_paths:
        label = os.path.splitext(os.path.basename(pcb_path))[0]
        # Clean up method suffix from filename
        for suffix in ("_clean", "_greedy_stub", "_feedback_stub", "_greedy_full"):
            label = label.replace(suffix, "")
        print(f"\n=== Routing: {os.path.basename(pcb_path)} ===")
        try:
            result = route_pcb(pcb_path, output_dir=output_dir, **kwargs)
            # Use the method name from the filename for the result key
            base = os.path.splitext(os.path.basename(pcb_path))[0]
            method = _extract_method(base)
            all_results[method] = result
        except Exception as e:
            print(f"ERROR routing {pcb_path}: {e}")
            all_results[pcb_path] = {"error": str(e)}

    # Save aggregate comparison
    cmp_path = os.path.join(output_dir, "routed_comparison.json")
    with open(cmp_path, "w", encoding="utf-8") as f:
        json.dump(all_results, f, indent=2, default=str)

    return all_results


def _extract_method(base_name: str) -> str:
    """Extract method name from PCB filename suffix."""
    # e.g. bm4.unrouted_clean → clean
    #      bm4.unrouted_greedy_stub → greedy_stub
    parts = base_name.split("_")
    if len(parts) >= 3 and parts[-1] in ("stub", "full"):
        return f"{parts[-2]}_{parts[-1]}"
    if len(parts) >= 2:
        return parts[-1]
    return "unknown"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Headless Freerouting wrapper")
    sub = parser.add_subparsers(dest="command")

    p_route = sub.add_parser("route", help="Route a single PCB")
    p_route.add_argument("--pcb", required=True, help="Path to .kicad_pcb")
    p_route.add_argument("--output-dir", default=None, help="Output directory")
    p_route.add_argument("--passes", type=int, default=20, help="Routing passes")
    p_route.add_argument("--threads", type=int, default=4, help="Thread count")
    p_route.add_argument("--strategy", default="hybrid", help="Routing strategy")
    p_route.add_argument("--timeout", type=int, default=600, help="Timeout (seconds)")

    p_batch = sub.add_parser("batch", help="Route multiple PCBs for comparison")
    p_batch.add_argument("--pcbs", required=True, nargs="+", help="PCB files to route")
    p_batch.add_argument("--output-dir", required=True, help="Output directory")
    p_batch.add_argument("--passes", type=int, default=20)
    p_batch.add_argument("--threads", type=int, default=4)
    p_batch.add_argument("--timeout", type=int, default=600)

    args = parser.parse_args()

    if args.command == "route":
        route_pcb(
            args.pcb,
            output_dir=args.output_dir,
            passes=args.passes,
            threads=args.threads,
            strategy=args.strategy,
            timeout_seconds=args.timeout,
        )
    elif args.command == "batch":
        batch_route(
            args.pcbs,
            args.output_dir,
            passes=args.passes,
            threads=args.threads,
            timeout_seconds=args.timeout,
        )
    else:
        parser.print_help()
