#!/usr/bin/env python
from __future__ import print_function

__author__ = 'Hyeuk Ryu'

import os
import shapefile
import pandas as pd
import numpy as np

from wistl.transmission_line import TransmissionLine
from wistl.tower import Tower


def create_damaged_network(conf):
    """ create dict of damaged network
    :param conf: instance of config class
    :return: dictionary of damaged_networks
    """

    damaged_networks = dict()

    for path_wind in conf.path_wind_scenario:

        event_id = path_wind.split('/')[-1]
        if event_id in damaged_networks:
            raise KeyError('{} is already assigned'.format(event_id))

        path_output_scenario = os.path.join(conf.path_output, event_id)
        if not os.path.exists(path_output_scenario):
            os.makedirs(path_output_scenario)

        damaged_networks[event_id] = TransmissionNetwork(conf)
        damaged_networks[event_id].event_id = event_id

    return damaged_networks


class TransmissionNetwork(object):
    """ class for a collection of wistl lines"""

    def __init__(self, conf):

        self.conf = conf

        df_towers = read_shape_file(self.conf.file_shape_tower)
        self.df_lines = read_shape_file(self.conf.file_shape_line)

        self.lines = dict()
        for name, grouped in df_towers.groupby('LineRoute'):
            if name in self.conf.sel_lines:
                try:
                    tf = self.df_lines['LineRoute'] == name
                    idx = self.df_lines[tf].index[0]
                except IndexError:
                    msg = '{} not in the line shapefile'.format(name)
                    print (msg)
                    raise IndexError(msg)

                self.lines[name] = TransmissionLine(
                    conf=self.conf,
                    df_towers=grouped,
                    df_line=self.df_lines.loc[idx])

        self._event_id = None
        self._time_index = None

    @property
    def time_index(self):
        return self._time_index

    @property
    def event_id(self):
        return self._event_id

    @event_id.setter
    def event_id(self, event_id):
        self._event_id = event_id
        self._set_event()

    def _set_event(self):

        for key, line in self.lines.iteritems():
            #self.lines[key] = TransmissionLine(line)
            self.lines[key].event_id = self.event_id

        # after the for loop self.lines are DamageLine instances
        # assuming same time index for each tower in the same network
        self._time_index = self.lines[key].time_index


def read_shape_file(file_shape):
    """
    read shape file and return data frame
    :param file_shape:
    :return data_frame:
    """
    sf = shapefile.Reader(file_shape)
    shapes = sf.shapes()
    records = sf.records()
    fields = [x[0] for x in sf.fields[1:]]
    fields_type = [x[1] for x in sf.fields[1:]]

    shapefile_type = {'C': object, 'F': np.float64, 'N': np.int64}

    data_frame = pd.DataFrame(records, columns=fields)

    for name_, type_ in zip(data_frame.columns, fields_type):
        if data_frame[name_].dtype != shapefile_type[type_]:
            data_frame[name_] = \
                data_frame[name_].astype(shapefile_type[type_])

    if 'Shapes' in fields:
        raise KeyError('Shapes is already in the fields')
    else:
        data_frame['Shapes'] = shapes

    return data_frame


def mc_loop_over_line(damage_line):

    event_id = damage_line.event_id
    line_name = damage_line.name

    if damage_line.conf.random_seed:
        try:
            seed = damage_line.conf.seed[event_id][line_name]
        except KeyError:
            msg = '{}:{} is undefined. Check the config file'.format(
                event_id, line_name)
            print(msg)
            raise KeyError(msg)
    else:
        seed = None

    rand_number_generator = np.random.RandomState(seed)

    # perfect correlation within a single line
    rv = rand_number_generator.uniform(size=(damage_line.conf.nsims,
                                             len(damage_line.time_index)))

    for tower in damage_line.towers.itervalues():
        tower.compute_mc_adj(rv, seed)

    damage_line.compute_damage_probability_simulation()

    if not damage_line.conf.skip_non_cascading_collapse:
        damage_line.compute_damage_probability_simulation_non_cascading()
