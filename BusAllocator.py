from GridParameters import GridParameters
import argparse
import matplotlib.pyplot as plt
import pythoninterfacenew3 as io


class Bus:
    def __init__(self, BusID, Bus_start, StartPads, Bus_end, EndPads, BusWidth, nets):
        self.BusID = BusID
        self.Bus_start = Bus_start
        self.StartPads = StartPads
        self.Bus_end = Bus_end
        self.EndPads = EndPads
        self.BusWidth = BusWidth
        self.netsID = nets
        

class BusAllocator:
    POWER_NET_KEYWORDS = (
        "GND", "VCC", "VDD", "VSS", "VIN", "VBAT", "PWR", "POWER", "USBVCC",
    )

    def __init__(self, grid_parameter: GridParameters):
        self.netclass = grid_parameter.netClass
        self.NetList = grid_parameter.netList
        self.FootprintList = grid_parameter.footprint_list
        self.parameters = grid_parameter
        self.BusList = []
        self.dia0 = grid_parameter.dia_pos_0
        self.dia1 = grid_parameter.dia_pos_1
        self.padlist = grid_parameter.padlist

        
    def is_near(self,pad1, pad2, MaxDistance):
        dx = pad1.position.X - pad2.position.X
        dy = pad1.position.Y - pad2.position.Y
        distance = (dx**2+dy**2)**0.5
        return distance <= MaxDistance
    

    def search_nearest(self,point,pads):
        tmp = float('inf')
        for pad in pads:
            dx = pad.position_real[0] - point[0]
            dy = pad.position_real[1] - point[1]
            distance = (dx**2+dy**2)**0.5
            if distance <= tmp:
                targrtpoint = (pad.position_real[0],pad.position_real[1])
                tmp = distance
        return targrtpoint 

    
    def AllocZone(self, dia0, dia1, point, FpPos, RefFpPos):
        centralpoint = ((dia0[0]+dia1[0])/2, (dia0[1]+dia1[1])/2)
        sizex = dia1[0] - dia0[0]
        sizey = dia1[1] - dia0[1]
        if (sizex > 2.5*sizey):
            if RefFpPos.Y >= FpPos.Y :
                return 0
            else: 
                return 2
        if (sizey > 2.5*sizex):
            if RefFpPos.X >= FpPos.X :
                return 1
            else: 
                return 3
           
        if point[0]-centralpoint[0] == 0:
            if (point[1]-centralpoint[1])>0:
                return 0 
            else: return 2
        tanx = abs((point[1]-centralpoint[1])/(point[0]-centralpoint[0]))
        if dia1[0] == dia0[0]:
            return 0
        tan0 = (dia1[1]-dia0[1])/(dia1[0]-dia0[0])
        if tanx>=tan0 :
            if point[1]>centralpoint[1]:
                return 0
            else:
                return 2
        else:
            if point[0]>centralpoint[0]:
                return 1
            else:
                return 3
    

    def GenerateMultipadFPList(self):
        MultipadFPList = []
        for i in range(len(self.FootprintList)):
            if len(self.FootprintList[i].pads) > 2:
                MultipadFPList.append(self.FootprintList[i])
        return MultipadFPList
    
        

    
    def NetsInFP(self, footprint):
        PadAndNet = []
        for pad in footprint.pads:
            if pad.netID is not None and not self._is_power_net(pad.netID):
                PadAndNet.append((pad, pad.netID))
        return PadAndNet

    def _net_name(self, net_id):
        return self.parameters.id_to_name.get(net_id, "")

    def _is_power_net(self, net_id):
        name = self._net_name(net_id).upper()
        if not name:
            return False
        if name.startswith("+"):
            return True
        if any(keyword in name for keyword in self.POWER_NET_KEYWORDS):
            return True
        return False

    def _default_netclass_name(self):
        if "Default" in self.parameters.netClassReal:
            return "Default"
        if self.parameters.netClassReal:
            return next(iter(self.parameters.netClassReal.keys()))
        return None

    def _netclass_name(self, net_id):
        net = self.parameters.netid_to_net.get(net_id)
        if net is not None and net.netClass in self.parameters.netClassReal:
            return net.netClass
        return self._default_netclass_name()

    def _netclass_rule(self, netclass_name):
        if netclass_name is None:
            return None
        return self.parameters.netClassReal.get(netclass_name)
    
#    def AllocateBoundary(self, pos, footprint) ->int:
        



    def allocate(self):
        """
        disregard multipinnets

        1. add Footprint with over 2 pads to MFPList
        2. search each Footprint pair to find the same net number,sort and  store by netclass
        3. if there are over 2 nets in the same netclass between Footprint pair:
            

        """

        MFPList=self.GenerateMultipadFPList()
        BusID = 0
        with open('debug.txt', 'w') as file:
            file.write('')

        for i in range(len(MFPList)):
            PadNet1 = self.NetsInFP(MFPList[i])

        

            for j in range(i+1,len(MFPList)):
                sortPN1 = []
                zone0 = []
                zone1 = []
                zone2 = []
                zone3 = []
                for padnet in PadNet1:
                    x = self.AllocZone(MFPList[i].dia_pos_0_real,MFPList[i].dia_pos_1_real, (padnet[0].position_real[0],padnet[0].position_real[1]),MFPList[i].position , MFPList[j].position)
                    if x == 0:
                        zone0.append(padnet)
                    if x == 1:
                        zone1.append(padnet)
                    if x == 2:
                        zone2.append(padnet) 
                    if x == 3:
                        zone3.append(padnet)
                sortPN1 = [zone0, zone1, zone2, zone3]
                PadNet2 = self.NetsInFP(MFPList[j])
                sortPN2 = []
                zone0 = []
                zone1 = []
                zone2 = []
                zone3 = []
                for padnet in PadNet2:
                    x = self.AllocZone(MFPList[j].dia_pos_0_real,MFPList[j].dia_pos_1_real, (padnet[0].position_real[0],padnet[0].position_real[1]), MFPList[j].position, MFPList[i].position)
                    if x == 0:
                        zone0.append(padnet)
                    if x == 1:
                        zone1.append(padnet)
                    if x == 2:
                        zone2.append(padnet) 
                    if x == 3:
                        zone3.append(padnet)
                sortPN2 = [zone0, zone1, zone2, zone3]
                with open('debug.txt', 'a') as file:
                    file.write('({},{},{},{})'.format(MFPList[i].fpname, MFPList[i].position, MFPList[j].fpname, MFPList[j].position))
                for a in range(len(sortPN1)): #search 4 boundaries
                    for b in range(len(sortPN2)):
                        classes = {}  #classes in the same Fpedge
                        StartBusPins_temp = {}
                        EndBusPins_temp = {}
                        StartID_temp = {}
                        EndID_temp = {}
                        BusWidth_temp = {}
                        NetInBus = {}
                        for num in  range(len(sortPN1[a])):  #initialize the dict
                            padnetclass = self._netclass_name(sortPN1[a][num][1])
                            if padnetclass not in classes.keys():
                                classes[padnetclass] = []
                                StartBusPins_temp[padnetclass] = []
                                EndBusPins_temp[padnetclass] = []
                                StartID_temp[padnetclass] = []
                                EndID_temp[padnetclass] = []
                                NetInBus[padnetclass] = []
                                BusWidth_temp[padnetclass] = 0
        
        
                        for m in range(len(sortPN1[a])):
                            pad1 = sortPN1[a][m][0]
                            net1 = sortPN1[a][m][1]
                            pad1 = sortPN1[a][m][0]
                            netclass_name = self._netclass_name(net1)
                            netclass1 = self._netclass_rule(netclass_name)
                            if netclass1 is None:
                                continue
                            for n in  range(len(sortPN2[b])):
                                pad2 = sortPN2[b][n][0]
                                net2 = sortPN2[b][n][1]
                                pad2 = sortPN2[b][n][0]
                                with open('debug.txt' ,'a') as file:
                                    file.write('({},{})'.format(self.parameters.id_to_name[net1],self.parameters.id_to_name[net2]))
                                if (net1 == net2) and (net1 not in StartID_temp[netclass_name]) and (net2 not in EndID_temp[netclass_name]):
                                    classes[netclass_name].append(net1)
                                    clearance_with_track = netclass1.clearance + netclass1.track_width
                                    StartID_temp[netclass_name].append(net1)
                                    EndID_temp[netclass_name].append(net2)
                                    StartBusPins_temp[netclass_name].append(sortPN1[a][m][0])
                                    EndBusPins_temp[netclass_name].append(sortPN2[b][n][0])
                                    HPWL = abs(pad1.position_real[0] - pad2.position_real[0]) + abs(pad1.position_real[1] - pad2.position_real[1])
                                    NetInBus[netclass_name].append((net1, HPWL))
                                    BusWidth_temp[netclass_name] += clearance_with_track

                                    break
                        with open('debug.txt' ,'a') as file:
                                    file.write('\n')
                        for netclass in classes:
                            if len(classes[netclass]) >= 2:   #pad number in the same class is over 2
                                with open('debug.txt' ,'a') as file:
                                    file.write('the same net:')
                                for pin in StartBusPins_temp[netclass]:
                                    with open('debug.txt' ,'a') as file:
                                        file.write('{} '.format(self.parameters.id_to_name[pin.netID]))
                                    with open('debug.txt' ,'a') as file:
                                        file.write('\n')
                                start_sum_x = 0
                                start_sum_y = 0
                                end_sum_x = 0
                                end_sum_y = 0
#                                startpoints = []
                                for pin in StartBusPins_temp[netclass]:
                                    start_sum_x += pin.position_real[0]
                                    start_sum_y += pin.position_real[1]
                                    #startpoints.append((pin.position[0],pin.position[1]))
        
                                Bus_start_x = start_sum_x/len(StartBusPins_temp[netclass])
                                Bus_start_y = start_sum_y/len(StartBusPins_temp[netclass])
        
                                for pin in EndBusPins_temp[netclass]:
        
                                    end_sum_x += pin.position_real[0]
                                    end_sum_y += pin.position_real[1]
                                
                                Bus_end_x = end_sum_x/len(EndBusPins_temp[netclass])
                                Bus_end_y = end_sum_y/len(EndBusPins_temp[netclass]) 
                                if a == 0:
                                    Bus_start_y = MFPList[i].dia_pos_1_real[1]
                                elif a== 1:
                                    Bus_start_x = MFPList[i].dia_pos_1_real[0]
                                elif a== 2:
                                    Bus_start_y = MFPList[i].dia_pos_0_real[1]
                                elif a== 3:
                                    Bus_start_x = MFPList[i].dia_pos_0_real[0]

                                if b == 0:
                                    Bus_end_y = MFPList[j].dia_pos_1_real[1]
                                elif b == 1:
                                    Bus_end_x = MFPList[j].dia_pos_1_real[0]
                                elif b == 2:
                                    Bus_end_y = MFPList[j].dia_pos_0_real[1]
                                elif b == 3:
                                    Bus_end_x = MFPList[j].dia_pos_0_real[0]                                                                
                                Bus_start = (Bus_start_x,Bus_start_y)
                                Bus_end = (Bus_end_x, Bus_end_y)
#                                allocated_sp = self.AllocZone(MFPList[i].dia_pos_0,MFPList[i].dia_pos_1, [Bus_start_tmp])
#                                allocated_ep = self.AllocZone(MFPList[j].dia_pos_0,MFPList[j].dia_pos_1, [Bus_end_tmp])
#                                for i in range(4):
#                                    if allocated_sp[i] is not None:
#                                        if i == 0:
#                                            Bus_start = (Bus_start_tmp[0],2.54*MFPList[i].dia_pos_1[1])
#                                        elif i == 1:
#                                            Bus_start = (2.54*MFPList[i].dia_pos_1[0],Bus_start_tmp[1])
#                                        elif i == 2:
#                                            Bus_start = (Bus_start_tmp[0],2.54*MFPList[i].dia_pos_0[1])
#                                        elif i == 3:
#                                            Bus_start = (2.54*MFPList[i].dia_pos_0[0],Bus_start_tmp[1])
#                                
#                                for i in range(4):
#                                    if allocated_ep[i] is not None:
#                                        if i == 0:
#                                            Bus_end = (Bus_end_tmp[0],2.54*MFPList[j].dia_pos_1[1])
#                                        elif i == 1:
#                                            Bus_end = (2.54*MFPList[j].dia_pos_1[0],Bus_end_tmp[1])
#                                        elif i == 2:
#                                            Bus_end = (Bus_end_tmp[0],2.54*MFPList[j].dia_pos_0[1])
#                                        elif i == 3:
#                                            Bus_end = (2.54*MFPList[j].dia_pos_0[0],Bus_end_tmp[1])
        
                                BusWidth = BusWidth_temp[netclass]
                                def takeSecond(elem):
                                    return elem[1]
                                NetInBus[netclass].sort(key = takeSecond)
                                onlynets = []
                                for net in NetInBus[netclass]:
                                    onlynets.append(net[0])

#                                Bus_start = self.search_nearest(Bus_start,StartBusPins_temp[netclass])
#                                Bus_end = self.search_nearest(Bus_end,EndBusPins_temp[netclass])
                                bus = Bus(BusID,Bus_start,StartBusPins_temp[netclass],Bus_end,EndBusPins_temp[netclass],BusWidth, onlynets)
                                self.BusList.append(bus)
                                BusID +=1

def allocator_arguments():
    parser = argparse.ArgumentParser('BusAllocator')
    parser.add_argument('--kicad_pcb', type=str, dest='kicad_pcb', default="bench1/bm1.unrouted.kicad_pcb")
    parser.add_argument('--kicad_pro', type=str, dest='kicad_pro', default="bench1/bm1.unrouted.kicad_pro")
    parser.add_argument('--save_file', type=str, dest='save_file', default="bench1/bm1.routed.kicad_pcb")
    return parser.parse_args()

class Drawer():
    def __init__(self, buslist, gridparameters:GridParameters):
        self.buslist = buslist
        self.gridparameters = gridparameters
        self.footprint = gridparameters.footprint_list
    
    def AllocZone(self, dia0, dia1, point):
        centralpoint = ((dia0[0]+dia1[0])/2, (dia0[1]+dia1[1])/2)
        sizex = dia1[0] - dia0[0]
        sizey = dia1[1] - dia0[1]
        if (sizex > 2.5*sizey) | (sizey > 2.5*sizex):
            return 0

        if point[0]-centralpoint[0] == 0:
            if (point[1]-centralpoint[1])>0:
                return 0 
            else: return 2
        if (dia1[0]-dia0[0]) == 0:
            if (point[1]-centralpoint[1])>0:
                return 0 
            else: return 2
        tanx = abs((point[1]-centralpoint[1])/(point[0]-centralpoint[0]))
        tan0 = abs((dia1[1]-dia0[1])/(dia1[0]-dia0[0]))
        if tanx>=tan0 :
            if point[1]>centralpoint[1]:
                return 0
            else:
                return 2
        else:
            if point[0]>centralpoint[0]:
                return 1
            else:
                return 3
        
    
    def draw(self):
        sizex = abs(self.gridparameters.dia_pos_1[0] - self.gridparameters.dia_pos_0[0])
        sizey = abs(self.gridparameters.dia_pos_1[1] - self.gridparameters.dia_pos_0[1])
        #bench1 1200,650 bench2 700,300 bench4 500,500
#        sizex = 700
#        sizey = 300
        print(sizex,sizey)
        fig,ax = plt.subplots(figsize = (sizex/10,sizey/10))
        expand = 0
        plt.xlim(0-expand,sizex+expand)
        plt.ylim(0-expand,sizey+expand)
        padx = []
        pady = []
        color = []
        for fp in self.gridparameters.footprint_list:
            for pad in fp.pads:
                padx.append(pad.position_real[0])
                pady.append(pad.position_real[1])
                edge = self.AllocZone(fp.dia_pos_0_real,fp.dia_pos_1_real, (pad.position_real[0],pad.position_real[1]))
                if  edge == 0:
                    color.append('g')
                elif edge == 1:
                    color.append('r')
                elif edge == 2:
                    color.append('c')
                elif edge == 3:
                    color.append('m')

        plt.scatter(padx,pady,c=color,marker='.',linewidths= 0.1)

        for bus in self.buslist:
            busx = [bus.Bus_start[0],bus.Bus_end[0]]
            busy = [bus.Bus_start[1],bus.Bus_end[1]]
            plt.plot(busx,busy, linewidth = bus.BusWidth, alpha = 0.5)
            plt.text(bus.Bus_start[0], bus.Bus_start[1], s=bus.BusID)
            for d in range(len(bus.StartPads)):
                pads_x = [bus.StartPads[d].position_real[0],bus.EndPads[d].position_real[0]]
                pads_y = [bus.StartPads[d].position_real[1],bus.EndPads[d].position_real[1]]
                plt.plot(pads_x,pads_y,'k:',linewidth = 0.5, alpha= 0.5)
        for fp in self.footprint:
            plt.plot([fp.dia_pos_0_real[0],fp.dia_pos_1_real[0]],[fp.dia_pos_0_real[1],fp.dia_pos_0_real[1]],'k',linewidth = 0.5, alpha= 1)
            plt.plot([fp.dia_pos_0_real[0],fp.dia_pos_0_real[0]],[fp.dia_pos_0_real[1],fp.dia_pos_1_real[1]],'k',linewidth = 0.5, alpha= 1)
            plt.plot([fp.dia_pos_0_real[0],fp.dia_pos_1_real[0]],[fp.dia_pos_1_real[1],fp.dia_pos_1_real[1]],'k',linewidth = 0.5, alpha= 1)
            plt.plot([fp.dia_pos_1_real[0],fp.dia_pos_1_real[0]],[fp.dia_pos_0_real[1],fp.dia_pos_1_real[1]],'k',linewidth = 0.5, alpha= 1)

            
#        plt.xlim((self.gridparameters.dia_pos_0[0],self.gridparameters.dia_pos_1[0]))
#        plt.ylim((self.gridparameters.dia_pos_1[1],self.gridparameters.dia_pos_0[1]))



        plt.savefig('figs/new/bench2.png')
        plt.show()




if __name__ == '__main__':
    arg = allocator_arguments()
    benchmark_file = arg.kicad_pcb
    project_file = arg.kicad_pro
    save_file = arg.save_file
    gridParameters = GridParameters(benchmark_file, project_file, save_file)
    busallocator = BusAllocator(gridParameters)
    busallocator.allocate()
    with open('parameters.txt' ,'w') as file:
        file.write('(Bus')
    for Bus in busallocator.BusList:
        with open('parameters.txt' ,'a') as file:
            file.write('(Bus{} (start (X {})(Y {})) (end (X {})(Y {})) (width {})) '.format(Bus.BusID, Bus.Bus_start[0],Bus.Bus_start[1], Bus.Bus_end[0], Bus.Bus_end[1],Bus.BusWidth))
        print(Bus.BusID,Bus.Bus_start,Bus.Bus_end,Bus.BusWidth,  Bus.netsID)
    with open('parameters.txt' ,'a') as file:
        file.write(')')


    drawer = Drawer(busallocator.BusList, gridParameters)
    drawer.draw()

    class Position:
        def __init__(self,x,y):
            self.x = x
            self.y = y
    class Boundarypoints:
        def __init__(self,x1,y1,x2,y2):
            self.start = Position(x1,y1)
            self.end = Position(x2,y2)
    
    
    filePath = r'output.txt'
    # output test
    bus = io.Bus(filePath)
    bus.to_file(busallocator.BusList,filePath)

    boundarypoints = [Boundarypoints(busallocator.dia0[0], busallocator.dia0[1], busallocator.dia1[0], busallocator.dia1[1])]
    board = io.BoundaryPoints(filePath)
    board.to_file(boundarypoints,filePath)

    padlist = busallocator.padlist
    component = io.Components(filePath)
    component.to_file(padlist,filePath)

    path = io.PathList(filePath)
    pathlist = []
    path.to_file(pathlist,filePath)

    # input test

    buslistx = bus.from_file(filePath)
    for Bus in buslistx:
        print(Bus.Bus_start,Bus.Bus_end,Bus.BusWidth,Bus.netsID)
    boardx = board.from_file(filePath)
    for board in boardx:
        print('dia0 {} dia1 {}'.format((board.start.x, board.start.y),(board.end.x, board.end.y)))
    componentx = component.from_file(filePath)
    for cp in componentx:
        print(cp.type, cp.pad_dia0,cp.pad_dia1)











                    
                



