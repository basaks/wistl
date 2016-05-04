from __future__ import print_function

import os
import shapefile
import geopy.distance
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import pandas as pd
import numpy as np

from shapely.geometry import LineString, Point
from geopy.distance import great_circle

from wistl.transmission_line import TransmissionLine


def create_transmission_network_under_wind_event(event_tuple, cfg):
    """ create dict of transmission network
    :param event_tuple: tuple of event_id and scale
    :param cfg: instance of config class
    :return: an instance of TransmissionNetwork
    """

    network = TransmissionNetwork(cfg)
    network.event_tuple = event_tuple

    return network


class TransmissionNetwork(object):
    """ class for a collection of transmission lines"""

    def __init__(self, cfg):

        self.cfg = cfg

        # read and process line information
        self.df_lines = read_shape_file(cfg.file_shape_line)
        self.df_lines = populate_df_lines(self.df_lines)
        cfg.no_towers_by_line = \
            self.df_lines.set_index('LineRoute').to_dict()['no_towers']

        # read and process tower information
        self.df_towers = read_shape_file(cfg.file_shape_tower)
        self.df_towers = populate_df_towers(self.df_towers, cfg)

        # assigned outside
        self._event_tuple = None

        # assigned in @event_tuple.setter
        self.event_id_scale = None
        self.event_id = None
        self.scale = None
        self.path_event = None
        self.time_index = None
        self.path_output = None

        self.lines = dict()
        for line_name, grouped in self.df_towers.groupby('LineRoute'):

            if line_name in self.cfg.selected_lines:
                try:
                    idx = self.df_lines[self.df_lines.LineRoute ==
                                        line_name].index[0]
                except IndexError:
                    msg = '{} not in the line shapefile'.format(line_name)
                    raise IndexError(msg)

                self.lines[line_name] = TransmissionLine(
                    cfg=self.cfg,
                    df_towers=grouped.copy(),
                    ps_line=self.df_lines.loc[idx])

        if cfg.line_interaction:
            self.set_line_interaction()

            if cfg.figure:
                self.plot_line_interaction()

    @property
    def event_tuple(self):
        return self._event_tuple

    @event_tuple.setter
    def event_tuple(self, value):
        try:
            event_id, scale = value
        except ValueError:
            msg = "Pass a tuple of event_id and scale"
            raise ValueError(msg)
        else:
            self.event_id = event_id
            self.scale = scale
            self.path_event = os.path.join(self.cfg.path_wind_scenario_base,
                                           event_id)
            self.event_id_scale = self.cfg.event_id_scale_str.format(
                event_id=event_id, scale=scale)
            self.path_output = os.path.join(self.cfg.path_output,
                                            self.event_id_scale)
            if not os.path.exists(self.path_output) and self.cfg.save:
                os.makedirs(self.path_output)
                print('{} is created'.format(self.path_output))
            self.set_damage_line()

    def set_damage_line(self):
        # assign event information to instances of TransmissionLine

        line = None
        for line in self.lines.itervalues():
            line.event_tuple = (self.event_id, self.scale)

        try:
            # assuming same time index for each tower in the same network
            self.time_index = line.time_index
        except AttributeError:
            msg = 'No transmission line created'
            raise AttributeError(msg)

    def set_line_interaction(self):

        for line_name, line in self.lines.iteritems():

            for tower in line.towers.itervalues():
                id_on_target_line = dict()

                for target_line in self.cfg.line_interaction[line_name]:

                    line_string = self.lines[target_line].line_string
                    line_coord = self.lines[target_line].coord

                    closest_pt_on_line = line_string.interpolate(
                        line_string.project(tower.point))

                    closest_pt_coord = np.array(closest_pt_on_line.coords)[0, :]

                    closest_pt_lat_lon = closest_pt_coord[::-1]

                    # compute distance
                    dist_from_line = geopy.distance.great_circle(
                        tower.coord_lat_lon, closest_pt_lat_lon).meters

                    if dist_from_line < tower.height:

                        id_on_target_line[target_line] = {
                            'id': find_id_nearest_pt(closest_pt_coord,
                                                     line_coord),
                            'vector': unit_vector(closest_pt_coord -
                                                  tower.coord)}

                if id_on_target_line:
                    tower.id_on_target_line = id_on_target_line

    def plot_line_interaction(self):

        for line_name, line in self.lines.iteritems():

            plt.figure()
            plt.plot(line.coord[:, 0],
                     line.coord[:, 1], '-', label=line_name)

            for target_line in self.cfg.line_interaction[line_name]:

                plt.plot(self.lines[target_line].coord[:, 0],
                         self.lines[target_line].coord[:, 1],
                         '--', label=target_line)

                for tower in line.towers.itervalues():
                    try:
                        id_pt = tower.id_on_target_line[target_line]['id']
                    except KeyError:
                        plt.plot(tower.coord[0], tower.coord[1], 'ko')
                    else:
                        target_tower_name = self.lines[
                            target_line].name_by_line[id_pt]
                        target_tower = self.lines[target_line].towers[
                            target_tower_name]

                        plt.plot([tower.coord[0], target_tower.coord[0]],
                                 [tower.coord[1], target_tower.coord[1]],
                                 'ro-',
                                 label='{}->{}'.format(tower.name,
                                                       target_tower_name))

            plt.title(line_name)
            plt.legend(loc=0)
            plt.xlabel('Longitude')
            plt.ylabel('Latitude')
            png_file = os.path.join(self.cfg.path_output,
                                    'line_interaction_{}.png'.format(line_name))

            if not os.path.exists(self.cfg.path_output):
                os.makedirs(self.cfg.path_output)

            plt.savefig(png_file)
            print('{} is created'.format(png_file))
            plt.close()


def populate_df_lines(df_lines):
    """
    add the following columns to df_lines: coord, coord_lat_lon, line_string,
        name_output, no_towers, and actual_span
    :param df_lines: pandas.DataFrame
    :return: df_lines: pandas.DataFrame
    """
    df_lines = df_lines.merge(df_lines['Shapes'].apply(assign_shapely_line),
                              left_index=True, right_index=True)
    df_lines['name_output'] = df_lines['LineRoute'].apply(
        lambda x: '_'.join(x for x in x.split() if x.isalnum()))
    df_lines['no_towers'] = df_lines['coord'].apply(lambda x: len(x))
    df_lines['actual_span'] = df_lines['coord_lat_lon'].apply(
        calculate_distance_between_towers)
    return df_lines


def populate_df_towers(df_towers, cfg):
    """
    add the following columns to df_towers: coord, coord_lat_lon, point,
        design_span, design_level, design_speed, terrain_cat, frag_func,
        frag_scale, frag_arg, file_wind_base_name
    :param df_towers: pandas.DataFrame
    :param cfg: an instance of TransmissionConfig
    :return:
    """
    df_towers = df_towers.merge(df_towers['Shapes'].apply(assign_shapely_point),
                                left_index=True, right_index=True)
    if cfg:
        df_towers = df_towers.merge(df_towers['LineRoute'].apply(
            assign_design_values, args=(cfg,)),
            left_index=True, right_index=True)
        df_towers = df_towers.merge(df_towers.apply(assign_fragility_parameters,
                                                    args=(cfg,), axis=1),
                                    left_index=True, right_index=True)
        df_towers['file_wind_base_name'] = df_towers['Name'].apply(
            lambda x: cfg.wind_file_head + x + cfg.wind_file_tail)
    return df_towers


def calculate_distance_between_towers(coord_lat_lon):
    """
    calculate actual span between the towers
    :param coord_lat_lon: list of coord in lat, lon
    :return: array of actual span between towers
    """
    coord_lat_lon = np.stack(coord_lat_lon)
    dist_forward = np.zeros(len(coord_lat_lon) - 1)
    for i, (pt0, pt1) in enumerate(zip(coord_lat_lon[0:-1], coord_lat_lon[1:])):
        dist_forward[i] = great_circle(pt0, pt1).meters

    actual_span = 0.5 * (dist_forward[0:-1] + dist_forward[1:])
    actual_span = np.insert(actual_span, 0, [0.5 * dist_forward[0]])
    actual_span = np.append(actual_span, [0.5 * dist_forward[-1]])
    return actual_span


def assign_design_values(line_route, cfg):
    """
    create pandas series of design level related values
    :param line_route: line route name
    :param cfg: an instance of TransmissionConfig
    :return: pandas series of design_span, design_level, design_speed, and
             terrain cat
    """
    design_value = cfg.design_value[line_route]
    return pd.Series({'design_span': design_value['span'],
                      'design_level': design_value['level'],
                      'design_speed': design_value['speed'],
                      'terrain_cat': design_value['cat']})


def assign_shapely_point(shape):
    """
    create pandas series of coord, coord_lat_lon, and Point
    :param shape: Shapefile instance
    :return: pandas series of coord, coord_lat_lon, and Point
    """
    coord = np.array(shape.points[0])
    return pd.Series({'coord': coord,
                      'coord_lat_lon': coord[::-1],
                      'point': Point(coord)})


def assign_shapely_line(shape):
    """
    create pandas series of coord, coord_lat_lon, and line_string
    :param shape: Shapefile instance
    :return: pandas series of coord, coord_lat_lon, and line_string
    """
    coord = shape.points
    return pd.Series({'coord': coord,
                      'coord_lat_lon': np.array(coord)[:, ::-1].tolist(),
                      'line_string': LineString(coord)})


def assign_fragility_parameters(ps_tower, cfg):
    """
    assign fragility parameters by Type, Function, Dev Angle
    :param ps_tower: pandas series of towers
    :param cfg: an instance of TransmissionConfig
    :return: pandas series of frag_func, frag_scale, frag_arg
    """
    tf_array = np.ones((cfg.fragility.shape[0],), dtype=bool)
    for att, att_type in zip(cfg.fragility_metadata['by'],
                             cfg.fragility_metadata['type']):
        if att_type == 'string':
            tf_array *= cfg.fragility[att] == ps_tower[att]
        elif att_type == 'numeric':
            tf_array *= (cfg.fragility[att + '_lower'] <=
                         ps_tower[att]) & \
                        (cfg.fragility[att + '_upper'] >
                         ps_tower[att])

    params = pd.Series({'frag_scale': dict(), 'frag_arg': dict(),
                        'frag_func': None})
    for ds in cfg.damage_states:
        try:
            idx = cfg.fragility[tf_array &
                                (cfg.fragility['limit_states'] == ds)].index[0]
        except IndexError:
            msg = 'No matching fragility params for {}'.format(ps_tower.Name)
            raise IndexError(msg)
        else:
            fn_form = cfg.fragility.loc[idx, cfg.fragility_metadata['function']]
            params['frag_func'] = fn_form
            params['frag_scale'][ds] = cfg.fragility.loc[
                idx, cfg.fragility_metadata[fn_form]['scale']]
            params['frag_arg'][ds] = cfg.fragility.loc[
                idx, cfg.fragility_metadata[fn_form]['arg']]
    return params


def find_id_nearest_pt(pt_coord, line_coord):
    """
    find the index of line_coord matching point coord
    :param pt_coord: (2,)
    :param line_coord: (#,2)
    :return: index of the nearest in the line_coord
    """
    assert pt_coord.shape == (2,)
    assert line_coord.shape[1] == 2
    diff = np.linalg.norm(line_coord - pt_coord, axis=1)
    return np.argmin(diff)


def read_shape_file(file_shape):
    """
    read shape file and return data frame
    :param file_shape: Esri shape file
    :return data_frame: pandas.DataFrame
    """

    try:
        sf = shapefile.Reader(file_shape)
    except shapefile.ShapefileException:
        msg = '{} is not a valid shapefile'.format(file_shape)
        raise shapefile.ShapefileException(msg)
    else:
        shapes = sf.shapes()
        records = sf.records()
        fields = [x[0] for x in sf.fields[1:]]
        fields_type = [x[1] for x in sf.fields[1:]]

    shapefile_type = {'C': object, 'F': np.float64, 'N': np.int64}
    data_frame = pd.DataFrame(records, columns=fields)

    for name_, type_ in zip(data_frame.columns, fields_type):
        if data_frame[name_].dtype != shapefile_type[type_]:
            data_frame[name_] = data_frame[name_].astype(shapefile_type[type_])

    if 'Shapes' in fields:
        raise KeyError('Shapes is already in the fields')
    else:
        data_frame['Shapes'] = shapes

    return data_frame


def compute_damage_probability_line_interaction_per_network(network):
    """
    compute damage probability due to line interaction
    :param network: a dictionary of lines
    :return: network: a dictionary of lines
    """

    for line_name, line in network.iteritems():

        tf_ds = np.zeros((line.no_towers,
                          line.cfg.no_sims,
                          len(line.time_index)), dtype=bool)

        for trigger_line, target_lines in line.cfg.line_interaction.iteritems():

            if line_name in target_lines:

                try:
                    pd_id = pd.DataFrame(np.vstack(
                        network[trigger_line].damage_index_line_interaction[
                            line_name]),
                        columns=['id_tower', 'id_sim', 'id_time'],
                        dtype=np.int64)
                except ValueError:
                    print('{}'.format(
                        network[trigger_line].damage_index_line_interaction[
                            line_name]))
                else:

                    id_tower = pd_id['id_tower'].values
                    id_sim = pd_id['id_sim'].values
                    id_time = pd_id['id_time'].values

                    try:
                        tf_ds[id_tower, id_sim, id_time] = True
                    except IndexError:
                        print('{}:{}:{}'.format(pd_id.head(),
                                                pd_id.dtypes,
                                                'why???'))
                        print('trigger:{}, {}, {}'.format(trigger_line,
                                                          line_name,
                                                          line.event_id_scale))

        # append damage state by line itself
        # due to either direct wind and adjacent towers
        # also need to override non-collapse damage states

        cds_list = line.cfg.damage_states[:]  # to avoid effect
        cds_list.reverse()  # [collapse, minor]

        tf_sim = dict()

        # append damage state by either direct wind or adjacent towers
        for ds in cds_list:

            tf_ds[line.damage_index_simulation[ds]['id_tower'],
                  line.damage_index_simulation[ds]['id_sim'],
                  line.damage_index_simulation[ds]['id_time']] = True

            tf_sim[ds] = np.copy(tf_ds)

            line.damage_prob_line_interaction[ds] = \
                pd.DataFrame(np.sum(tf_ds, axis=1).T / float(line.cfg.no_sims),
                             columns=line.name_by_line,
                             index=line.time_index)

        # compute mean and std of no. of towers for each of damage states
        (line.est_no_damage_line_interaction,
            line.prob_no_damage_line_interaction) = \
            line.compute_damage_stats(tf_sim)

        if line.cfg.save:
            line.write_hdf5(file_str='damage_prob_line_interaction',
                            value=line.damage_prob_line_interaction)

            line.write_hdf5(file_str='est_no_damage_line_interaction',
                            value=line.est_no_damage_line_interaction)

            line.write_hdf5(file_str='prob_no_damage_line_interaction',
                            value=line.prob_no_damage_line_interaction)

    return network


def unit_vector(vector):
    """
    create unit vector
    :param vector: tuple(x, y)
    :return: unit vector

    """
    return vector / np.linalg.norm(vector)
