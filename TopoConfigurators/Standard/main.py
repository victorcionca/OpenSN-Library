from opensn.operator.emulator_operator import EmulatorOperator
from opensn.model.instance import Instance
from opensn.model.position import Position
from opensn.const.dict_fields import PARAMETER_KEY_CONNECT,PARAMETER_KEY_DELAY,PARAMETER_KEY_BANDWIDTH,PARAMETER_KEY_LOSS
from opensn.model.link import LinkBase
from opensn.utils.tools import dec2ra
import config
from datetime import datetime, timedelta
import trajectory
from instance_types import TYPE_GROUND_STATION, TYPE_SATELLITE, EX_ORBIT_INDEX,EX_ALTITUDE_KEY,EX_LATITUDE_KEY,EX_LONGITUDE_KEY, EX_AREA_KEY, EX_TLE0_KEY, EX_TLE2_KEY
from address_type import LINK_V4_ADDR_KEY
from time import sleep
from address_allocator import alloc_ipv4,format_ipv4
from loguru import logger
import json, math
step_second = 5

polar_threshold = dec2ra(66.5)

def genenrate_config(cli:EmulatorOperator,node_index:int,instance_id:str):
    instance_info = cli.get_instance(node_index,instance_id)
    config_map = {
        "instance_id": instance_id,
        "link_infos": {},
        "end_infos": {},
    }
    if instance_info.type == TYPE_SATELLITE:
        config_map['area'] = instance_info.extra[EX_AREA_KEY]
    for k,v in instance_info.connections.items():
        instance_index = -1
        link_info = cli.get_link(node_index,k)
        for end_index in range(len(link_info.end_infos)):
            if link_info.end_infos[end_index].instance_id == instance_id:
                instance_index = end_index
        if instance_index < 0:
            return {}
        another_instance_info = cli.get_instance(link_info.end_infos[1-instance_index].end_node_index,link_info.end_infos[1-instance_index].instance_id)
        config_map["link_infos"][k] = link_info.address_infos[instance_index]
        config_map["end_infos"][k] = {
            "instance_id": v.instance_id,
            "type": v.instance_type,
        }
        if another_instance_info.type == TYPE_SATELLITE:
            config_map["end_infos"][k]['area'] = another_instance_info.extra[EX_AREA_KEY]
    return config_map

def update_orbit_longitude(cli:EmulatorOperator, instances:dict[str,Instance],
                           orbit_id:int, new_longitude:int,
                           num_orbits:int):
    """
    Change the TLE of the instance to the new longitude and update the
    instance configuration with the data

    Parameters
    cli         -- connection to etcd store
    instances   -- maps satellite names ("NODE_X_Y") to instance. Only satellites
    orbit_id    -- orbit that must be updated
    new_longitude- new longitude for the orbit
    """
    next_orbit_id = (orbit_id+1)%num_orbits
    prev_orbit_id = (orbit_id-1)%num_orbits
    # First get all the satellites in the orbit of interest
    sats = [inst for name, inst in instances.items() if int(name[5]) == orbit_id]
    for inst in sats:
        name = inst.extra[EX_TLE0_KEY]
        # Change to the new longitude
        trajectory.satellite_change_longitude(inst, new_longitude)
        # Push the new instance to etcd
        cli.put_instance(inst)
        # remove link to next orbit
        sat_in_orbit = int(name.split('_')[-1])
        sat_in_nxt_orbit = instances[f"NODE_{next_orbit_id}_{sat_in_orbit}"]
        cli.disable_link_between(inst.node_index, inst.instance_id,
                                 inst.node_index, sat_in_nxt_orbit.instance_id)
        # add links from new previous orbit
        sat_in_prev_orbit = instances[f"NODE_{prev_orbit_id}_{sat_in_orbit}"]
        cli.enable_link_between(inst.node_index, inst.instance_id,
                                inst.node_index, sat_in_prev_orbit.instance_id)

if __name__ == "__main__":

    instance_config_updated:dict[str,str] = {}
    
    cli = EmulatorOperator(config.ADDR,config.PORT)

    orbit_time = 0
    if config.STARTTIME != "":
        time_now = datetime.strptime(config.STARTTIME, "%Y-%m-%d-%H:%M:%S")
        orbit_time = time_now

    bound_threshold = 0
    if config.BOUNDTHRESHOLD != "":
        bound_threshold = int(config.BOUNDTHRESHOLD)*1000
    bound_target = None
    if config.BOUNDTARGET != "":
        long_s, lat_s = config.BOUNDTARGET.split(',')
        b_long = float(long_s)/180*math.pi
        b_lat = float(lat_s)/180*math.pi
        bound_target = Position(b_lat, b_long, 0)

    num_orbits = 3
    sat_name_inst_map = dict()
    west_most_orbit = 0
    last_orbit_long = 270
    orbit_long_delta = 15
    # Create Emulator Operator
    while True:
        node_list = cli.get_node_map()
        all_instance_map: dict[str,Instance] = {}
        node_link_map: dict[int,dict[str,LinkBase]] = {}
        ground_station_list:list[Instance] = []
        build_satmap = False
        if not sat_name_inst_map:
            build_satmap = True
        for node_index,node in node_list.items():
            instance_map = cli.get_instance_map(node_index)
            for instance_id,instance in instance_map.items():
                # Build the mapping between satellite name to instance
                if instance.type == TYPE_SATELLITE:
                    if build_satmap:
                        sat_name_inst_map[instance.extra[EX_TLE0_KEY]] = instance
                all_instance_map[instance_id] = instance
                if instance.type == TYPE_GROUND_STATION:
                    ground_station_list.append(instance)
                    gs_position = Position()
                    gs_position.latitude = float(instance.extra[EX_LATITUDE_KEY]) / 180 * math.pi
                    gs_position.longitude = float(instance.extra[EX_LONGITUDE_KEY]) / 180 * math.pi
                    gs_position.altitude = float(instance.extra[EX_ALTITUDE_KEY])
                    cli.put_position(instance_id,gs_position)

        address_map = {}
        for node_index,node in node_list.items():
            node_link_map[node_index] = {}
            link_map = cli.get_link_map(node_index)
            for link_id,link_info in link_map.items():
                if LINK_V4_ADDR_KEY not in link_info.address_infos[0] or \
                    LINK_V4_ADDR_KEY not in link_info.address_infos[1] is None:
                    if link_id not in address_map.keys():
                        address_map[link_id] = alloc_ipv4(30)
                    
                    subnet = address_map[link_id]
                    link_info.address_infos = [{
                        LINK_V4_ADDR_KEY: format_ipv4(subnet[1],30)
                    },
                    {
                        LINK_V4_ADDR_KEY: format_ipv4(subnet[2],30)
                    }]
                    cli.put_link(link_info)
                node_link_map[node_index][link_id] = link_info
                

        position_map: dict[str,Position] = {"":Position()}
        if config.STARTTIME == "":
            time_now = datetime.now()
        # ---- Bounding box implementation
        # If the distance between an orbit and a target point
        # is greater than the bound threshold, move the orbit to the end.
        # It is too resource intensive to remove all the satellite containers
        # and spin a new set of them.
        # Instead we will update the TLE of the satellites from the first
        # orbit.
        if bound_threshold > 0 and bound_target is not None:
            first_sat = sat_name_inst_map[f"NODE_{west_most_orbit}_0"]
            #orbital_period = trajectory.get_orbital_period(first_sat)
            #if time_now > orbit_time + timedelta(seconds=orbital_period):
            #    orbit_time = time_now
            if not trajectory.check_orbit_has_coverage(first_sat, time_now, bound_target,
                                            bound_threshold):
                last_orbit_long = (last_orbit_long+orbit_long_delta)%360
                print(f"[{time_now}] Orbit {west_most_orbit} lost coverage. Update orbit long to {last_orbit_long}")
                update_orbit_longitude(cli, sat_name_inst_map,
                                       west_most_orbit, last_orbit_long,
                                       num_orbits)
                # Update the west-most orbit id
                west_most_orbit = (west_most_orbit+1)%num_orbits
            else:
                print(f"[{time_now}] Still covered")

        print(f"[{time_now} Update satellite positions")
        for instance_id,instance_info in all_instance_map.items():
            if instance_info.start:
                new_postion = trajectory.calculate_postion(instance_info,time_now)
                cli.put_position(instance_id,new_postion)
            else:
                new_postion = Position()
            position_map[instance_id] = new_postion
        # Do Ground Station Reconnect
    

        for ground_station in ground_station_list:
            if not ground_station.start:
                continue
            gs_position = position_map[ground_station.instance_id]
            satellite_id,change = trajectory.select_closest_satellite(
                ground_station,
                position_map,
                all_instance_map
            )
            if change:
                address1 = {}
                address2 = {}
                
                old_link_id = ""
                for key in ground_station.connections.keys():
                    old_link_id = key
                    break
                if old_link_id != "":
                    old_link = cli.disable_link_between(
                        ground_station.node_index,
                        ground_station.instance_id,
                        ground_station.connections[key].end_node_index,
                        ground_station.connections[key].instance_id
                    )
                    logger.info("Switch %s from %s to %s"%(
                        ground_station.instance_id,
                        ground_station.connections[old_link_id].instance_id,
                        satellite_id
                    ))
                    old_sat_config = genenrate_config(cli,ground_station.connections[old_link_id].end_node_index,ground_station.connections[old_link_id].instance_id)
                    cli.put_instance_config(ground_station.connections[old_link_id].end_node_index,ground_station.connections[old_link_id].instance_id,json.dumps(old_sat_config))

                    # config_map = genenrate_config(
                    #     satellite_id,all_instance_map[ground_station.connections[key].instance_id],node_link_map)
                    if len(old_link) > 0:
                        address1 = old_link[old_link_id].address_infos[0]
                        address2 = old_link[old_link_id].address_infos[1]
                else:
                    subnet = alloc_ipv4(30)
                    address1 = {LINK_V4_ADDR_KEY:format_ipv4(subnet[1],30)}
                    address2 = {LINK_V4_ADDR_KEY:format_ipv4(subnet[2],30)}
                    logger.info("Switch %s from %s to %s"%(
                        ground_station.instance_id,
                        "None",
                        satellite_id
                    ))
                cli.enable_link_between(
                    ground_station.node_index,
                    ground_station.instance_id,
                    all_instance_map[satellite_id].node_index,
                    all_instance_map[satellite_id].instance_id,
                    address_info1=address1,
                    address_info2=address2,
                    init_parameter={PARAMETER_KEY_BANDWIDTH: 1000000000}
                )
                gs_config = genenrate_config(cli,ground_station.node_index,ground_station.instance_id)
                # print(gs_config)
                cli.put_instance_config(ground_station.node_index,ground_station.instance_id,json.dumps(gs_config))
                sat_config = genenrate_config(cli,all_instance_map[satellite_id].node_index,all_instance_map[satellite_id].instance_id)
                # print(sat_config)
                cli.put_instance_config(all_instance_map[satellite_id].node_index,all_instance_map[satellite_id].instance_id,json.dumps(sat_config))

        

        for node_index,link_map in node_link_map.items():
            for link_id,link_info in link_map.items():
                if link_info.parameter is None:
                    link_info.parameter = {}
                if link_info.end_infos[0].instance_id=="" or link_info.end_infos[1].instance_id == "":
                    continue
                if not link_info.enable:
                    continue
                if link_info.end_infos[0].instance_type == TYPE_SATELLITE and \
                    link_info.end_infos[1].instance_type == TYPE_SATELLITE and \
                    all_instance_map[link_info.end_infos[1].instance_id].extra[EX_ORBIT_INDEX] != \
                    all_instance_map[link_info.end_infos[0].instance_id].extra[EX_ORBIT_INDEX] and \
                    (abs(position_map[link_info.end_infos[0].instance_id].latitude) > polar_threshold or \
                    abs(position_map[link_info.end_infos[1].instance_id].latitude) > polar_threshold):
                        # if PARAMETER_KEY_CONNECT in link_info.parameter.keys() and link_info.parameter[PARAMETER_KEY_CONNECT]==1:
                        #     logger.info("connect %s"%link_id)
                        link_info.parameter[PARAMETER_KEY_CONNECT] = 0
                else:
                    # if PARAMETER_KEY_CONNECT not in link_info.parameter.keys() or link_info.parameter[PARAMETER_KEY_CONNECT]==0:
                    #         logger.info("disconnect %s"%link_id)
                    link_info.parameter[PARAMETER_KEY_CONNECT] = 1

                distance = trajectory.distance_meter(
                    position_map[link_info.end_infos[0].instance_id],
                    position_map[link_info.end_infos[1].instance_id]
                )
                delay = int(trajectory.get_propagation_delay_s(distance)*1000000)
                link_info.parameter[PARAMETER_KEY_DELAY] = delay
                link_info.parameter[PARAMETER_KEY_BANDWIDTH] = 1000000000
                link_info.parameter[PARAMETER_KEY_LOSS] = 150
                cli.put_link_parameter(link_info.node_index,link_info.link_id,link_info.parameter)
                
        for instance_id,instance_info in all_instance_map.items():
            if not instance_info.start:
                continue
            config_map = genenrate_config(cli,instance_info.node_index,instance_id)
            cli.put_instance_config_if_not_exist(instance_info.node_index,instance_id,json.dumps(config_map))
        sleep(step_second)
        if config.STARTTIME != "":
            time_now += timedelta(seconds=step_second)
