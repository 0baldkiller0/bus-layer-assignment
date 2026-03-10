import math
from kiutils.board import Board
from kiutils.items.brditems import Segment, Via
from kiutils.items.common import Position

from kiutils_pro import KiCadPro


class Pad:
    def __init__(self, pos, layer, shape, size, pad_type, net_id, dia0, dia1):
        self.position_real = pos
        self.layer = layer
        self.shape = shape
        self.size_real = size
        self.type = pad_type
        self.netID = net_id

        self.position = [to_grid_coord_round_down(pos[0]), to_grid_coord_round_down(pos[1]), pos[2]]
        self.size = [to_grid_coord_round_up(size[0]), to_grid_coord_round_up(size[1])]
        self.pad_dia0 = dia0
        self.pad_dia1 = dia1       

class Net:
    def __init__(self, net_id, net_name, net_class):
        self.netID = net_id
        self.netName = net_name
        self.netClass = net_class
        self.padList = []

class Footprint:
    def __init__(self,pads,position,name,dia0,dia1):
        self.pads = pads
        self.position = position
        self.fpname = name
        self.dia_pos_0_real = [dia0[0],dia0[1]]
        self.dia_pos_1_real = [dia1[0],dia1[1]]
        self.size_real = (self.dia_pos_1_real[0] - self.dia_pos_0_real[0],self.dia_pos_1_real[1] - self.dia_pos_0_real[1])
        self.dia_pos_0 = [to_grid_coord_round_down(dia0[0]),to_grid_coord_round_down(dia0[1])]
        self.dia_pos_1 = [to_grid_coord_round_down(dia1[0]),to_grid_coord_round_down(dia1[1])]
        self.size = (self.dia_pos_1[0] - self.dia_pos_0[0],self.dia_pos_1[1] - self.dia_pos_0[1])



class NetClass:
    def __init__(self, track_width, microvia_diameter, microvia_drill, clearance, min_hole_clearance):
        self.track_width = to_grid_coord_round_up(track_width)
        self.microvia_diameter = to_grid_coord_round_up(microvia_diameter)
        self.microvia_drill = to_grid_coord_round_up(microvia_drill)

        self.clearance_with_track = to_grid_coord_round_down(clearance + track_width / 2)

        if clearance < min_hole_clearance:
            self.clearance_with_microvia = to_grid_coord_round_down(min_hole_clearance + microvia_drill / 2)
        else:
            self.clearance_with_microvia = to_grid_coord_round_down(clearance + microvia_drill / 2)


def to_grid_coord_round_down(coord_i):
    return int(coord_i * 3 / 0.25)  # 0.25


def to_grid_coord_round_up(coord_i):
    return math.ceil(coord_i * 3 / 0.25)  # 0.25


class GridParameters:
    def __init__(self, kicad_pcb, kicad_pro, save_kicad_pcb, rewrite_board=False):
        self.filename = kicad_pcb
        self.save_filename = save_kicad_pcb
        board = Board().from_file(kicad_pcb)
        self.board = board

        project = KiCadPro().from_file(kicad_pro)

        boundary_list = [
            item for item in board.graphicItems
            if hasattr(item, "start") and hasattr(item, "end")
        ]
        if not boundary_list:
            raise ValueError(f"No line-like board boundary graphics found in {kicad_pcb}")
        self.dia_pos_0 = [boundary_list[0].start.X, boundary_list[0].start.Y]
        self.dia_pos_1 = [boundary_list[0].start.X, boundary_list[0].start.Y]
        pos_set = set([])
        for gr_line in boundary_list:
            start_pos = [gr_line.start.X, gr_line.start.Y]
            end_pos = [gr_line.end.X, gr_line.end.Y]
            if str(start_pos) not in pos_set:
                pos_set.add(str(start_pos))
                if self.dia_pos_0[0] > start_pos[0]:
                    self.dia_pos_0[0] = start_pos[0]
                if self.dia_pos_0[1] > start_pos[1]:
                    self.dia_pos_0[1] = start_pos[1]
                if self.dia_pos_1[0] < start_pos[0]:
                    self.dia_pos_1[0] = start_pos[0]
                if self.dia_pos_1[1] < start_pos[1]:
                    self.dia_pos_1[1] = start_pos[1]

            if str(end_pos) not in pos_set:
                pos_set.add(str(end_pos))
                if self.dia_pos_0[0] > end_pos[0]:
                    self.dia_pos_0[0] = end_pos[0]
                if self.dia_pos_0[1] > end_pos[1]:
                    self.dia_pos_0[1] = end_pos[1]
                if self.dia_pos_1[0] < end_pos[0]:
                    self.dia_pos_1[0] = end_pos[0]
                if self.dia_pos_1[1] < end_pos[1]:
                    self.dia_pos_1[1] = end_pos[1]


        layers = {}
        layers_ = {}
        i = 0
        for layer in board.layers:
            if layer.type == 'signal':
                layers[layer.name] = i
                layers_[i] = layer.name
            i += 1

        net_list = [] #each net just contain num and name?????
        for net in board.nets:
            # parse net_class class
            if net.name != '':
                board_net = Net(net.number, net.name, project.netSetting.netClassPatterns.get(net.name, None))
                net_list.append(board_net)
            else:
                board_net = Net(net.number, net.name, None)
                net_list.append(board_net)
        # net_list.pop(0)
        self.pad_obstacles = []
        self.footprint_list = []
        self.padlist = []
        self.fp = board.footprints
        for footprint in board.footprints:
#            boundary_list = footprint.graphicItems
#            self.dia_pos_0 = [boundary_list[0].start.X, boundary_list[0].start.Y]
#            self.dia_pos_1 = [boundary_list[0].start.X, boundary_list[0].start.Y]
#            pos_set = set([])
#            for gr_line in boundary_list:
#                start_pos = [gr_line.start.X, gr_line.start.Y]
#                end_pos = [gr_line.end.X, gr_line.end.Y]
#                if str(start_pos) not in pos_set:
#                    pos_set.add(str(start_pos))
#                    if self.dia_pos_0[0] > start_pos[0]:
#                        self.dia_pos_0[0] = start_pos[0]
#                    if self.dia_pos_0[1] > start_pos[1]:
#                        self.dia_pos_0[1] = start_pos[1]
#                    if self.dia_pos_1[0] < start_pos[0]:
#                        self.dia_pos_1[0] = start_pos[0]
#                    if self.dia_pos_1[1] < start_pos[1]:
#                        self.dia_pos_1[1] = start_pos[1]
#    
#                if str(end_pos) not in pos_set:
#                    pos_set.add(str(end_pos))
#                    if self.dia_pos_0[0] > end_pos[0]:
#                        self.dia_pos_0[0] = end_pos[0]
#                    if self.dia_pos_0[1] > end_pos[1]:
#                        self.dia_pos_0[1] = end_pos[1]
#                    if self.dia_pos_1[0] < end_pos[0]:
#                        self.dia_pos_1[0] = end_pos[0]
#                    if self.dia_pos_1[1] < end_pos[1]:
#                        self.dia_pos_1[1] = end_pos[1]
#            if footprint.position.angle is None:
#                theta = 0
#            else:
#                theta = footprint.position.angle * math.pi / 180
#            tmp0 = [footprint.position.X - self.dia_pos_0[0],footprint.position.Y - self.dia_pos_0[1]]
#            tmp1 = [footprint.position.X - self.dia_pos_0[0],footprint.position.Y - self.dia_pos_0[1]]
#            for item in footprint.graphicItems:
#                if type(item).__name__ =="FpText":
#                    #dia0 = dia1 = (footprint.position.X ,footprint.position.Y)
#                    continue
#            
#                elif type(item).__name__ =="FpPoly" :
#                    for i in range(len(item.coordinates)): #item have 4 items
#                        point = item.coordinates[i]
#                        dx = point.X * math.cos(theta) + point.Y * math.sin(theta)
#                        dy = point.Y * math.cos(theta) - point.X * math.sin(theta)
#                        x = footprint.position.X + dx
#                        y = footprint.position.Y + dy
#                        if  (dx < 0) & (dy < 0):
#                            dia0 = (x - self.dia_pos_0[0], y - self.dia_pos_0[1])
#                        if  (dx > 0) & (dy > 0):
#                            dia1 = (x - self.dia_pos_0[0], y - self.dia_pos_0[1])
#                elif type(item).__name__ =="FpCircle" :
#                    point = item.center
#                    r = item.end.X - item.center.X  #end is right to center
#                    dx = point.X * math.cos(theta) + point.Y * math.sin(theta)
#                    dy = point.Y * math.cos(theta) - point.X * math.sin(theta)
#                    x = footprint.position.X + dx
#                    y = footprint.position.Y + dy
#                    dia0 = (x - self.dia_pos_0[0] - r, y - self.dia_pos_0[1] - r)
#                    dia1 = (x - self.dia_pos_0[0] + r, y - self.dia_pos_0[1] + r)
#
#                elif type(item).__name__ =="FpLine":
#                    point0 = item.start
#                    dx = point0.X * math.cos(theta) + point0.Y * math.sin(theta)
#                    dy = point0.Y * math.cos(theta) - point0.X * math.sin(theta)
#                    x0 = footprint.position.X + dx
#                    y0 = footprint.position.Y + dy
#                    point1 = item.end
#                    dx = point1.X * math.cos(theta) + point1.Y * math.sin(theta)
#                    dy = point1.Y * math.cos(theta) - point1.X * math.sin(theta)
#                    x1 = footprint.position.X + dx
#                    y1 = footprint.position.Y + dy
#                    if  x0 <= x1:
#                        dia00 = x0
#                        dia10 = x1
#                    else:
#                        dia10 = x0
#                        dia00 = x1
#
#                    if  y0 <= y1:
#                        dia01 = y0
#                        dia11 = y1
#                    else:
#                        dia11 = y0
#                        dia01 = y1
#
#                    dia0 = (dia00 - self.dia_pos_0[0], dia01 - self.dia_pos_0[1])
#                    dia1 = (dia10 - self.dia_pos_0[0], dia11 - self.dia_pos_0[1])
#                if dia0[0] <= tmp0[0]:
#                    tmp0[0] = dia0[0]
#                if dia0[1] <= tmp0[1]:
#                    tmp0[1] = dia0[1]
#                if dia1[0] >= tmp1[0]:
#                    tmp1[0] = dia1[0]
#                if dia1[1] >= tmp1[1]:
#                    tmp1[1] = dia1[1]
#            
#            dia0 = tmp0
#            dia1 = tmp1
#
#
#                    for i in range(2,len(footprint.graphicItems)):
#                        gr_line = footprint.graphicItems[i]
#                        if type(gr_line).__name__ =="FpLine":
#                            start_pos = [gr_line.start.X, gr_line.start.Y]
#                            end_pos = [gr_line.end.X, gr_line.end.Y]
#                            if str(start_pos) not in pos_set:
#                                pos_set.add(str(start_pos))
#                                if dia0[0] > start_pos[0]:
#                                    dia0[0] = start_pos[0]
#                                if dia0[1] > start_pos[1]:
#                                    dia0[1] = start_pos[1]
#                                if dia1[0] < start_pos[0]:
#                                    dia1[0] = start_pos[0]
#                                if dia1[1] < start_pos[1]:
#                                    dia1[1] = start_pos[1]
#
#                            if str(end_pos) not in pos_set:
#                                pos_set.add(str(end_pos))
#                                if dia0[0] > end_pos[0]:
#                                    dia0[0] = end_pos[0]
#                                if dia0[1] > end_pos[1]:
#                                    dia0[1] = end_pos[1]
#                                if dia1[0] < end_pos[0]:
#                                    dia1[0] = end_pos[0]
#                                if dia1[1] < end_pos[1]:
#                                    dia1[1] = end_pos[1]
#                            dia0 = [x + dia0[0] - self.dia_pos_0[0], y + dia0[1] - self.dia_pos_0[1]]
#                            dia1 = [x + dia1[0] - self.dia_pos_1[0], y + dia1[1] - self.dia_pos_1[1]]

                
            pad_list = []
            tmp0 = [footprint.position.X - self.dia_pos_0[0],footprint.position.Y - self.dia_pos_0[1]]
            tmp1 = [footprint.position.X - self.dia_pos_0[0],footprint.position.Y - self.dia_pos_0[1]]
            for pad in footprint.pads:
                if footprint.position.angle is None:
                    theta = 0
                else:
                    theta = footprint.position.angle * math.pi / 180

                dx = pad.position.X * math.cos(theta) + pad.position.Y * math.sin(theta)
                dy = pad.position.Y * math.cos(theta) - pad.position.X * math.sin(theta)
                x = footprint.position.X + dx
                y = footprint.position.Y + dy
                pad_pos = [x - self.dia_pos_0[0], 
                           y - self.dia_pos_0[1],
                           layers[footprint.layer]]
                if (footprint.position.angle == None) | (footprint.position.angle == 180):
                    ptdia0 = [pad_pos[0] - pad.size.X/2, pad_pos[1] - pad.size.Y/2]
                    ptdia1 = [pad_pos[0] + pad.size.X/2, pad_pos[1] + pad.size.Y/2]
                else:
                    ptdia0 = [pad_pos[0] - pad.size.Y/2, pad_pos[1] - pad.size.X/2]
                    ptdia1 = [pad_pos[0] + pad.size.Y/2, pad_pos[1] + pad.size.X/2]
                if pad.position.angle is None:
                    alpha = 0
                else:
                    alpha = pad.position.angle * math.pi / 180
                size_x = pad.size.X * math.cos(alpha) + pad.size.Y * math.sin(alpha)
                size_y = pad.size.Y * math.cos(alpha) - pad.size.X * math.sin(alpha)
                pad_size = [abs(size_x), abs(size_y)]
                pad_shape = pad.shape
                if pad.net:
                    board_pad = Pad(pad_pos, footprint.layer, pad_shape, pad_size, pad.type, pad.net.number, ptdia0, ptdia1)
                    net_list[pad.net.number].padList.append(board_pad)
                else:
                    board_pad = Pad(pad_pos, footprint.layer, pad_shape, pad_size, pad.type, None, ptdia0, ptdia1)
                    self.pad_obstacles.append(board_pad)
                pad_list.append(board_pad)
                self.padlist.append(board_pad)
                if ptdia0[0] <= tmp0[0]:
                    tmp0[0] = ptdia0[0]
                if ptdia0[1] <= tmp0[1]:
                    tmp0[1] = ptdia0[1]
                if ptdia1[0] >= tmp1[0]:
                    tmp1[0] = ptdia1[0]
                if ptdia1[1] >= tmp1[1]:
                    tmp1[1] = ptdia1[1]
            dia0 = tmp0
            dia1 = tmp1

            fp = Footprint(pad_list,footprint.position, footprint.entryName, dia0, dia1)
            self.footprint_list.append(fp)
            
                

        self.gridSize = [to_grid_coord_round_down(self.dia_pos_1[0] - self.dia_pos_0[0]),
                         to_grid_coord_round_down(self.dia_pos_1[1] - self.dia_pos_0[1]),
                         len(layers)]  # grid size
        self.layers = layers_
        self.grLines = boundary_list
        self.netNum = len(net_list) - 1
        self.netClassReal = project.netSetting.classes  #use it
        self.netClass = {}
        self.netid_to_class = {}
        for net_class in self.netClassReal:
            self.netClass[net_class] = NetClass(self.netClassReal[net_class].track_width,
                                                self.netClassReal[net_class].microvia_diameter,
                                                self.netClassReal[net_class].microvia_drill,
                                                self.netClassReal[net_class].clearance,
                                                project.board.design_setting.rules.min_hole_clearance)
        self.id_to_name = {}
        for net in net_list:
            self.id_to_name[net.netID] = net.netName
       
        self.netid_to_net = {}


        
        self.netList = net_list
        self.padclass_list = {} 
        for net in net_list:
            self.netid_to_net[net.netID] = net
            if net.netClass != None:
                self.netid_to_class[net.netID] = self.netClassReal[net.netClass]
            else: continue

        if rewrite_board:
            try:
                board.to_file()
            except Exception as exc:
                print(f"Warning: skipped KiCad board rewrite for {kicad_pcb}: {exc}")

    def to_real_coord(self, grid_coord):
        grid_x = grid_coord[0] / 3 * 0.25  # 0.25
        grid_y = grid_coord[1] / 3 * 0.25  # 0.25
        x = self.dia_pos_0[0] + grid_x
        y = self.dia_pos_0[1] + grid_y
        layer = self.layers[grid_coord[2]]
        return [x, y, layer]

    def store_route(self, merge_route_combo):
        board = Board().from_file(self.filename)
        i = 1
        item_id = 0
        for net in merge_route_combo:
            for segment in net:
                start = self.to_real_coord(segment[0])
                end = self.to_real_coord(segment[1])
                start_pos = Position(start[0], start[1])
                end_pos = Position(end[0], end[1])
                if start[2] == end[2]:
                    width = self.netClassReal[self.netList[i].netClass].track_width
                    layer = start[2]
                    item = Segment(start_pos, end_pos, width, layer, False, i, str(item_id))
                    board.traceItems.append(item)
                else:
                    size = self.netClassReal[self.netList[i].netClass].microvia_diameter
                    drill = self.netClassReal[self.netList[i].netClass].microvia_drill
                    layers = [self.layers[0], self.layers[1]]
                    item = Via('micro', False, start_pos, size, drill, layers, False, False, False, i, str(item_id))
                    board.traceItems.append(item)
                item_id += 1
            i += 1
        board.to_file(self.save_filename)
