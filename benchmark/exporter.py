"""
PCB 总线层分配问题 - 高保真中间表示导出器

从 KiCad 设计文件提取数据，保留焊盘级别精度，导出为标准化 JSON。
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from GridParameters import GridParameters
from BusAllocator import BusAllocator


def export_benchmark(kicad_pcb_path, kicad_pro_path, save_path=None):
    """
    从 KiCad 文件导出高保真 JSON benchmark。

    保留:
      - 每个焊盘的绝对位置、形状、尺寸、所在层
      - 焊盘到网络的映射
      - 元件位置和旋转角度
      - 总线起止焊盘信息

    Args:
        kicad_pcb_path: .kicad_pcb 文件路径
        kicad_pro_path: .kicad_pro 文件路径
        save_path: 输出 JSON 路径
    """
    save_file = kicad_pcb_path.replace('.kicad_pcb', '.exported.kicad_pcb')
    gp = GridParameters(kicad_pcb_path, kicad_pro_path, save_file)

    allocator = BusAllocator(gp)
    allocator.allocate()

    bench = {}

    # --- 元数据 ---
    bench["meta"] = {
        "source": "kicad",
        "pcb_file": os.path.basename(kicad_pcb_path),
        "pro_file": os.path.basename(kicad_pro_path),
        "coordinate_system": "kicad_absolute",
        "units": "mm"
    }

    # --- 板子信息 ---
    width = abs(gp.dia_pos_1[0] - gp.dia_pos_0[0])
    height = abs(gp.dia_pos_1[1] - gp.dia_pos_0[1])
    bench["board"] = {
        "width": round(width, 4),
        "height": round(height, 4),
        "layers": len(gp.layers),
        "layer_names": {
            str(local_id): gp.layers[layer_id]
            for local_id, layer_id in enumerate(sorted(gp.layers))
        },
        "boundary": {
            "dia0": [round(gp.dia_pos_0[0], 4), round(gp.dia_pos_0[1], 4)],
            "dia1": [round(gp.dia_pos_1[0], 4), round(gp.dia_pos_1[1], 4)]
        }
    }

    # --- NetClass ---
    netclasses = {}
    rules = getattr(getattr(gp.board, "design_setting", None), "rules", None)
    min_hole_clearance = getattr(rules, "min_hole_clearance", 0)

    for nc_name, nc in gp.netClassReal.items():
        netclasses[nc_name] = {
            "track_width": nc.track_width,
            "clearance_with_track": nc.clearance + nc.track_width / 2,
            "clearance_with_microvia": max(
                nc.clearance,
                min_hole_clearance
            ) + nc.microvia_drill / 2,
            "microvia_diameter": nc.microvia_diameter,
            "microvia_drill": nc.microvia_drill
        }
    bench["netclasses"] = netclasses

    # --- Pads (焊盘级别精度) ---
    pads = []
    pad_to_idx = {}  # pad 对象 -> 索引，用于快速查找

    for i, pad in enumerate(gp.padlist):
        p = {
            "id": i,
            "position": [round(pad.position_real[0], 4), round(pad.position_real[1], 4)],
            "layer": pad.layer,
            "shape": str(pad.shape),
            "size": [round(pad.size_real[0], 4), round(pad.size_real[1], 4)],
            "type": str(pad.type),
            "net_id": pad.netID,
            "dia0": [round(pad.pad_dia0[0], 4), round(pad.pad_dia0[1], 4)],
            "dia1": [round(pad.pad_dia1[0], 4), round(pad.pad_dia1[1], 4)]
        }
        pads.append(p)
        pad_to_idx[id(pad)] = i
    bench["pads"] = pads

    # --- Components (Footprints) ---
    components = []
    for i, fp in enumerate(gp.footprint_list):
        comp_pads = []
        for pad in fp.pads:
            if id(pad) in pad_to_idx:
                comp_pads.append(pad_to_idx[id(pad)])

        # 找关联的 bus
        bus_ids = []
        pad_net_ids = set()
        for pad in fp.pads:
            if pad.netID is not None:
                pad_net_ids.add(pad.netID)
        for bid, bus in enumerate(allocator.BusList):
            for net_id in bus.netsID:
                if net_id in pad_net_ids:
                    if bid not in bus_ids:
                        bus_ids.append(bid)
                    break

        comp = {
            "id": i,
            "name": fp.fpname,
            "dia0": [round(fp.dia_pos_0_real[0], 4), round(fp.dia_pos_0_real[1], 4)],
            "dia1": [round(fp.dia_pos_1_real[0], 4), round(fp.dia_pos_1_real[1], 4)],
            "pad_count": len(fp.pads),
            "pad_ids": comp_pads,
            "bus_ids": bus_ids
        }
        components.append(comp)
    bench["components"] = components

    # --- Nets ---
    nets = []
    for net in gp.netList:
        net_pad_ids = []
        for p in net.padList:
            if id(p) in pad_to_idx:
                net_pad_ids.append(pad_to_idx[id(p)])
        n = {
            "id": net.netID,
            "name": net.netName,
            "netclass": net.netClass,
            "pad_ids": net_pad_ids
        }
        nets.append(n)
    bench["nets"] = nets

    # --- Buses ---
    buses = []
    for bus in allocator.BusList:
        # 起止组件
        start_comp_id = -1
        end_comp_id = -1
        for comp_id, comp in enumerate(components):
            comp_fp = gp.footprint_list[comp_id]
            for pad in bus.StartPads:
                if pad in comp_fp.pads:
                    start_comp_id = comp_id
                    break
            for pad in bus.EndPads:
                if pad in comp_fp.pads:
                    end_comp_id = comp_id
                    break

        # 起止 pad 索引
        start_pad_ids = []
        end_pad_ids = []
        for pad in bus.StartPads:
            if id(pad) in pad_to_idx:
                start_pad_ids.append(pad_to_idx[id(pad)])
        for pad in bus.EndPads:
            if id(pad) in pad_to_idx:
                end_pad_ids.append(pad_to_idx[id(pad)])

        start_dir, end_dir = _get_escape_directions(bus, gp)

        b = {
            "id": bus.BusID,
            "netclass": None,
            "net_ids": bus.netsID,
            "start_comp": start_comp_id,
            "end_comp": end_comp_id,
            "start_pad_ids": start_pad_ids,
            "end_pad_ids": end_pad_ids,
            "start_pos": [round(bus.Bus_start[0], 4), round(bus.Bus_start[1], 4)],
            "end_pos": [round(bus.Bus_end[0], 4), round(bus.Bus_end[1], 4)],
            "width": round(bus.BusWidth, 4),
            "start_directions": start_dir,
            "end_directions": end_dir
        }
        for net_id in bus.netsID:
            if net_id < len(gp.netList) and gp.netList[net_id].netClass:
                b["netclass"] = gp.netList[net_id].netClass
                break
        if b["netclass"] is None:
            if "Default" in netclasses:
                b["netclass"] = "Default"
            elif netclasses:
                b["netclass"] = next(iter(netclasses.keys()))
        buses.append(b)
    bench["buses"] = buses

    # --- Obstacles ---
    obstacles = []
    for i, pad in enumerate(gp.pad_obstacles):
        obs = {
            "id": i,
            "dia0": [round(pad.pad_dia0[0], 4), round(pad.pad_dia0[1], 4)],
            "dia1": [round(pad.pad_dia1[0], 4), round(pad.pad_dia1[1], 4)],
            "type": "pad"
        }
        obstacles.append(obs)
    bench["obstacles"] = obstacles

    # --- 统计 ---
    bench["stats"] = {
        "num_components": len(components),
        "num_nets": len(nets),
        "num_buses": len(buses),
        "num_pads": len(pads),
        "num_obstacles": len(obstacles),
        "num_netclasses": len(netclasses),
        "total_bus_width": round(sum(b["width"] for b in buses), 4)
    }

    # 保存
    if save_path is None:
        base = os.path.splitext(os.path.basename(kicad_pcb_path))[0]
        save_path = os.path.join(os.path.dirname(kicad_pcb_path), base + ".json")

    with open(save_path, 'w', encoding='utf-8') as f:
        json.dump(bench, f, indent=2, ensure_ascii=False)

    print(f"Exported benchmark to {save_path}")
    print(f"  Components: {len(components)}")
    print(f"  Nets: {len(nets)}")
    print(f"  Buses: {len(buses)}")
    print(f"  Pads: {len(pads)}")

    if os.path.exists(save_file):
        os.remove(save_file)

    return bench


def _get_escape_directions(bus, gp):
    start_dirs = []
    end_dirs = []

    for comp in gp.footprint_list:
        for pad in bus.StartPads:
            if pad in comp.pads:
                cx0, cy0 = comp.dia_pos_0_real
                cx1, cy1 = comp.dia_pos_1_real
                px, py = pad.position_real[0], pad.position_real[1]
                tol = 1.0
                if abs(py - cy1) < tol: start_dirs.append(0)
                if abs(px - cx1) < tol: start_dirs.append(1)
                if abs(py - cy0) < tol: start_dirs.append(2)
                if abs(px - cx0) < tol: start_dirs.append(3)

        for pad in bus.EndPads:
            if pad in comp.pads:
                cx0, cy0 = comp.dia_pos_0_real
                cx1, cy1 = comp.dia_pos_1_real
                px, py = pad.position_real[0], pad.position_real[1]
                tol = 1.0
                if abs(py - cy1) < tol: end_dirs.append(0)
                if abs(px - cx1) < tol: end_dirs.append(1)
                if abs(py - cy0) < tol: end_dirs.append(2)
                if abs(px - cx0) < tol: end_dirs.append(3)

    start_dirs = list(set(start_dirs))
    end_dirs = list(set(end_dirs))
    return start_dirs, end_dirs


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='Export PCB benchmark to JSON')
    parser.add_argument('--kicad_pcb', type=str, required=True)
    parser.add_argument('--kicad_pro', type=str, required=True)
    parser.add_argument('--output', type=str, default=None)
    args = parser.parse_args()

    export_benchmark(args.kicad_pcb, args.kicad_pro, args.output)
