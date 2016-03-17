#!/usr/bin/env python
from __future__ import print_function

__author__ = 'Hyeuk Ryu'

import numpy as np
#import pandas as pd
#import itertools


class Tower(object):

    """
    class Tower
    Tower class represent an individual wistl tower.
    """
    #id_gen = itertools.count()

    def __init__(self, conf, df_tower):
        """
        :param conf: instance of config class
        :param df_tower: data frame containing tower details
        """

        self.conf = conf
        self.df_tower = df_tower

        self.id = df_tower['id']
        self.name = df_tower['Name']
        self.ttype = df_tower['Type']  # Lattice Tower or Steel Pole
        self.funct = df_tower['Function']  # e.g., suspension, terminal, strainer
        self.line_route = df_tower['LineRoute']  # string
        self.no_circuit = 2  # double circuit (default value)
        self.design_span = self.conf.design_value[self.line_route]['span']  # design wind span
        self.terrain_cat = self.conf.design_value[self.line_route]['cat']  # Terrain Cateogry
        self.design_level = self.conf.design_value[self.line_route]['level']  # design level
        self.strong_axis = df_tower['AxisAz']  # azimuth of strong axis relative to North (deg)
        self.dev_angle = df_tower['DevAngle']  # deviation angle
        self.height = df_tower['Height']
        self.height_z = conf.drag_height[self.funct]
        self.actual_span = df_tower['actual_span']  # actual wind span on eith side

        self.design_speed = self.assign_design_speed()  # design wind speed
        self.collapse_capacity = self.compute_collapse_capacity()
        self.file_wind = self.get_wind_file()
        self.convert_factor = self.convert_10_to_z()

        #cond_collapse_prob = self.get_cond_collapse_prob()
        self.cond_pc, self.max_no_adj_towers = self.get_cond_collapse_prob()

        # computed by funcitons in transmission_line class
        self.id_sides = None  # (left, right) ~ assign_id_both_sides
        self.id_adj = None  # (23,24,0,25,26) ~ update_id_adj_towers, update_id_adj_towers

        # computed by calculate_cond_pc_adj
        self.cond_pc_adj = None  # dict
        self.cond_pc_adj_mc = {'rel_idx': None, 'cum_prob': None}

    def get_cond_collapse_prob(self):
        ''' get dict of conditional collapse probabilities
        '''

        att_value = self.df_tower[self.conf.cond_collapse_prob_metadata['by']]
        df_prob = self.conf.cond_collapse_prob[att_value]

        att = self.conf.cond_collapse_prob_metadata[att_value]['by']
        att_type = self.conf.cond_collapse_prob_metadata[att_value]['type']
        if att_type == 'string':
            tf_array = df_prob[att] == getattr(self, att)
        elif att_type == 'numeric':
            tf_array = (df_prob[att + '_lower'] <= self.df_tower[att]) & \
                       (df_prob[att + '_upper'] > self.df_tower[att])

        # change to dictionary
        cond_pc = dict(zip(df_prob.loc[tf_array, 'list'],
                           df_prob.loc[tf_array, 'probability']))
        max_no_adj_towers = self.conf.cond_collapse_prob_metadata[att_value]['max_adj']

        return cond_pc, max_no_adj_towers

    def get_wind_file(self):
        ''' return name of wind file '''
        file_head = self.conf.file_name_format.split('%')[0]
        file_tail = self.conf.file_name_format.split(')')[-1]
        return file_head + self.name + file_tail

    def assign_design_speed(self):
        """ determine design speed """
        design_speed = self.conf.design_value[self.line_route]['speed']
        if self.conf.adjust_design_by_topo:
            id_topo = np.sum(self.conf.topo_multiplier[self.name] >=
                             self.conf.design_adjustment_factor_by_topo['threshold'])
            design_speed *= self.conf.design_adjustment_factor_by_topo[id_topo]

        return design_speed

    def compute_collapse_capacity(self):
        """
        calculate adjusted collapse wind speed for a tower
        Vc = Vd(h=z)/sqrt(u)
        where u = 1-k(1-Sw/Sd)
        Sw: actual wind span
        Sd: design wind span (defined by line route)
        k: 0.33 for a single, 0.5 for double circuit
        """

        # k: 0.33 for a single, 0.5 for double circuit
        k_factor = {1: 0.33, 2: 0.5}  # hard-coded

        # calculate utilization factor
        u = min(1.0, 1.0 - k_factor[self.no_circuit] *
                (1.0 - self.actual_span / self.design_span))  # 1 in case sw/sd > 1
        #self.u_val = 1.0/np.sqrt(u)
        return self.design_speed/np.sqrt(u)

    def convert_10_to_z(self):
        """
        Mz,cat(h=10)/Mz,cat(h=z)
        tc: terrain category (defined by line route)
        asset is a Tower class instance.
        """

        tc_str = 'tc' + str(self.terrain_cat)  # Terrain
        try:
            mzcat_z = np.interp(self.height_z,
                                self.conf.terrain_multiplier['height'],
                                self.conf.terrain_multiplier[tc_str])
        except KeyError:
            print('{} is undefined in {}'.format(
                tc_str, self.conf.file_terrain_multiplier))
#            return {'error': "{} is not defined".format(tc_str)}  # these errors should be handled properly

        idx_10 = self.conf.terrain_multiplier['height'].index(10)
        mzcat_10 = self.conf.terrain_multiplier[tc_str][idx_10]
        return mzcat_z/mzcat_10

    def calculate_cond_pc_adj(self):
        """
        calculate conditional collapse probability of jth tower given ith tower
        P(j|i)
        """

        idx_m1 = np.array([i for i in range(len(self.id_adj))
            if self.id_adj[i] == -1]) - self.max_no_adj_towers  # rel_index

        try:
            max_neg = np.max(idx_m1[idx_m1 < 0]) + 1
        except ValueError:
            max_neg = - self.max_no_adj_towers

        try:
            min_pos = np.min(idx_m1[idx_m1 > 0])
        except ValueError:
            min_pos = self.max_no_adj_towers + 1

        bound_ = set(range(max_neg, min_pos))

        cond_prob = {}
        for item in self.cond_pc.keys():
            w = list(set(item).intersection(bound_))
            w.sort()
            w = tuple(w)
            if w in cond_prob:
                cond_prob[w] += self.cond_pc[item]
            else:
                cond_prob[w] = self.cond_pc[item]

        if (0,) in cond_prob:
            cond_prob.pop((0,))

        # sort by cond. prob
        rel_idx = sorted(cond_prob, key=cond_prob.get)
        prob = map(lambda v: cond_prob[v], rel_idx)

        cum_prob = np.cumsum(np.array(prob))

        self.cond_pc_adj_mc['rel_idx'] = rel_idx
        self.cond_pc_adj_mc['cum_prob'] = cum_prob

        # sum by node
        cond_pc_adj = dict()
        for key_ in cond_prob:
            for i in key_:
                try:
                    cond_pc_adj[i] += cond_prob[key_]
                except KeyError:
                    cond_pc_adj[i] = cond_prob[key_]

        if 0 in cond_pc_adj:
            cond_pc_adj.pop(0)

        self.cond_pc_adj = cond_pc_adj

