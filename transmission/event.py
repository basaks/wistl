import numpy as np
from scipy.stats import lognorm
import pandas as pd

def dir_wind_speed(speed, bearing, t0):

    # angle between wind direction and tower conductor
    phi = np.abs(bearing - t0)

    tf = (phi <= np.pi/4) | (phi > np.pi/4*7) | ((phi > np.pi/4*3) & (phi <= np.pi/4*5))

    cos_ = abs(np.cos(np.pi/4.0-phi))
    sin_ = abs(np.sin(np.pi/4.0-phi))

    adj = speed*np.max(np.vstack((cos_, sin_)), axis=0)

    dir_speed = np.where(tf, adj, speed)  # adj if true, otherwise speed

    return dir_speed


class Event(object):

    """
    class Event
    Inputs:
    tower: instance of tower class
    vel_file: velocity file containing velocity time history at this tower location.
    """
    def __init__(self, tower, vel_file):

        self.tower = tower
        self.vel_file = vel_file
        self.pc_wind = None  # pd.DataFrame <- cal_pc_wind
        self.pc_adj = None  # dict (ntime,) <- cal_pc_adj_towers
        self.mc_wind = None  # dict(nsims, ntime)
        self.mc_adj = None  # dict

    @property
    def wind(self):
        # Time,Longitude,Latitude,Speed,UU,VV,Bearing,Pressure
        data = pd.read_csv(self.vel_file, header=0, parse_dates=[0], index_col=[0],
            usecols=[0, 3, 6], names=['', '', '', 'speed', '', '', 'bearing', ''])

        speed = data['speed'].values
        bearing = np.deg2rad(data['bearing'].values)  # degree

        # angle of conductor relative to NS
        t0 = np.deg2rad(self.tower.strong_axis) - np.pi/2.0

        convert_factor = self.convert_10_to_z()
        dir_speed = convert_factor * dir_wind_speed(speed, bearing, t0)

        data['dir_speed'] = pd.Series(dir_speed, index=data.index)
        return data

    def convert_10_to_z(self):
        """
        Mz,cat(h=10)/Mz,cat(h=z)
        tc: terrain category (defined by line route)
        asset is a Tower class instance.
        """
        terrain_height = self.tower.terrain_height
        tc_str = 'tc' + str(self.tower.terrain_cat)  # Terrain

        try:
            mzcat_z = np.interp(self.tower.height_z, terrain_height['height'], terrain_height[tc_str])
        except KeyError:
            print "%s is not defined" %tc_str
            return {'error': "{} is not defined".format(tc_str)}  # these errors should be handled properly

        mzcat_10 = terrain_height[tc_str][terrain_height['height'].index(10)]
        return mzcat_z/mzcat_10

    # originally a part of Tower class but moved wind to pandas timeseries
    def cal_pc_wind(self, asset, frag, ntime, ds_list, nds):
        """
        compute probability of damage due to wind
        - asset: instance of Tower object
        - frag: dictionary by asset.const_type
        - ntime:  
        - ds_list: [('collapse', 2), ('minor', 1)]
        - nds:
        """

        pc_wind = np.zeros((ntime, nds))

        vratio = self.wind.dir_speed.values/asset.adj_design_speed

        self.vratio = vratio

        try:
            fragx = frag[asset.ttype][asset.funct]
            idf = np.sum(fragx['dev_angle'] <= asset.dev_angle)

            for (ds, ids) in ds_list: # damage state
                med = fragx[idf][ds]['param0']
                sig = fragx[idf][ds]['param1']

                temp = lognorm.cdf(vratio, sig, scale=med)
                pc_wind[:,ids-1] = temp # 2->1

        except KeyError:        
                print "fragility is not defined for %s" %asset.const_type

        self.pc_wind = pd.DataFrame(pc_wind, columns=[x[0] for x in ds_list], index=self.wind.index)
                
        return

    def cal_pc_adj(self, asset, cond_pc):  # only for analytical approach
        """
        calculate collapse probability of jth tower due to pull by the tower
        Pc(j,i) = P(j|i)*Pc(i)
        """
        # only applicable for tower collapse

        pc_adj = {}
        for rel_idx in asset.cond_pc_adj.keys():
            abs_idx = asset.adj_list[rel_idx + asset.max_no_adj_towers]
            pc_adj[abs_idx] = (self.pc_wind.collapse.values * 
                                      asset.cond_pc_adj[rel_idx])

        self.pc_adj = pc_adj

        return

    def cal_mc_adj(self, asset, nsims, ntime, ds_list, nds, rv, idx):
        """
        2. determine if adjacent tower collapses or not due to pull by the tower
        jtime: time index (array)
        idx: multiprocessing thread id
        """

        # if rv is None:  # perfect correlation
        #     prng = np.random.RandomState()
        #     rv = prng.uniform(size=(nsims, ntime))

        # 1. determine damage state of tower due to wind
        val = np.array([rv < self.pc_wind[ds[0]].values for ds in ds_list]) # (nds, nsims, ntime)

        ds_wind = np.sum(val, axis=0) # (nsims, ntime) 0(non), 1, 2 (collapse)

        #tf = event.pc_wind.collapse.values > rv # true means collapse
        mc_wind = {}
        for (ds, ids) in ds_list:
            (mc_wind.setdefault(ds,{})['isim'], 
             mc_wind.setdefault(ds,{})['itime']) = np.where(ds_wind == ids)

        #if unq_itime == None:

        # for collapse
        unq_itime = np.unique(mc_wind['collapse']['itime'])

        nprob = len(asset.cond_pc_adj_mc['cum_prob']) # 

        mc_adj = {}  # impact on adjacent towers

        # simulaiton where none of adjacent tower collapses    
        #if max_idx == 0:

        if nprob > 0:

            for jtime in unq_itime:

                jdx = np.where(mc_wind['collapse']['itime'] == jtime)[0]
                idx_sim = mc_wind['collapse']['isim'][jdx] # index of simulation
                nsims = len(idx_sim)
                if idx:
                    prng = np.random.RandomState(idx)
                else:
                    prng = np.random.RandomState()
                rv = prng.uniform(size=(nsims))

                list_idx_cond = []
                for rv_ in rv:
                    idx_cond = sum(rv_ >= asset.cond_pc_adj_mc['cum_prob'])
                    list_idx_cond.append(idx_cond)

                # ignore simulation where none of adjacent tower collapses    
                unq_list_idx_cond = set(list_idx_cond) - set([nprob])

                for idx_cond in unq_list_idx_cond:

                    # list of idx of adjacent towers in collapse
                    rel_idx = asset.cond_pc_adj_mc['rel_idx'][idx_cond]

                    # convert relative to absolute fid
                    abs_idx = [asset.adj_list[j + asset.max_no_adj_towers] for 
                               j in rel_idx]

                    # filter simulation          
                    isim = [i for i, x in enumerate(list_idx_cond) if x == idx_cond]
                    mc_adj.setdefault(jtime, {})[tuple(abs_idx)] = idx_sim[isim]

        self.mc_wind = mc_wind
        self.mc_adj = mc_adj
        return