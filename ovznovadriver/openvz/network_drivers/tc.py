# vim: tabstop=4 shiftwidth=4 softtabstop=4

# Copyright 2013 Rackspace
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.
from Cheetah import Template
from nova.conductor import api
from nova import context
from nova import exception
from nova.openstack.common import lockutils
from nova.openstack.common import log as logging
from ovznovadriver.localization import _
from ovznovadriver.openvz import utils as ovz_utils
import os
from oslo.config import cfg
import random

CONF = cfg.CONF
LOG = logging.getLogger('ovznovadriver.openvz.network_drivers.tc')

global _ovz_tc_available_ids
global _ovz_tc_inflight_ids
_ovz_tc_available_ids = None
_ovz_tc_inflight_ids = None


class OVZTcRules(object):
    # Set our class variables if they don't exist
    # TODO(imsplitbit): figure out if there is a more pythonic way to do this
    if not _ovz_tc_available_ids:
        _ovz_tc_available_ids = list()

    if not _ovz_tc_inflight_ids:
        _ovz_tc_inflight_ids = list()

    def __init__(self):
        """
        This class is used to return TC rulesets for both a host and guest for
        use with host and guest startup and shutdown.
        """
        self.conductor = api.API()

        if not len(OVZTcRules._ovz_tc_available_ids):
            LOG.debug(_('Available_ids is empty, filling it with numbers'))
            OVZTcRules._ovz_tc_available_ids = [
                i for i in range(1, CONF.ovz_tc_id_max)
            ]

        # do an initial clean of the available ids
        self._remove_used_ids()
        LOG.debug(
            _('OVZTcRules thinks ovz_tc_host_slave_device is set to %s')
            % CONF.ovz_tc_host_slave_device)

    def instance_info(self, instance_id, address, vz_iface):
        """
        Use this method when creating or resizing an instance.  It will
        generate a new tc ruleset

        :param instance_id: Instance to generate the rules for
        :param address: IP address for the instance
        :param vz_iface: interface on the hosts bridge that is associated with
        the instance
        """
        # TODO(jcru) figure out if there is a case for no instance_id else
        # take it out
        if not instance_id:
            self.instance_type = dict()
            self.instance_type['memory_mb'] = 2048
        else:
            admin_context = context.get_admin_context()
            self.instance = self.conductor.instance_get(
                admin_context, instance_id)
            self.instance_type = self.conductor.instance_type_get(
                admin_context, self.instance['instance_type_id'])
        LOG.info(_('CT TC address: %s') % address)

        self.address = address
        self.vz_iface = vz_iface

        # TODO(jcru) Ideally wish to move this to resources.py
        # check under instance_type/flavor extra_specs to see if bandwidth has
        # been predefined for flavor
        extra_specs = self.instance_type.get("extra_specs", {})
        self.bandwidth = extra_specs.get("vz_bandwidth", None)
        if not self.bandwidth:
            LOG.info(_('No (vz_bandwidth) extra_specs key/value defined for '
                'flavor id (%s)') % self.instance_type['flavorid'])
            # Calculate the bandwidth total by figuring out how many units we
            # have
            self.bandwidth = int(round(self.instance_type['memory_mb'] /
                CONF.ovz_memory_unit_size)) * CONF.ovz_tc_mbit_per_unit
        else:
            int(self.bandwidth)

        LOG.info(_('Allotted bandwidth: %s') % self.bandwidth)

        self.tc_id = self._get_instance_tc_id()
        if not self.tc_id:
            LOG.debug(_('No preassigned tc_id for %s, getting a new one') %
                      instance_id)
            self.tc_id = self.get_id()

        self._save_instance_tc_id()
        LOG.debug(_('Saved the tc_id in the database for the instance'))

    @lockutils.synchronized('get_id_lock', 'openvz-')
    def get_id(self):
        """
        Uses nova utils decorator @lockutils.synchronized to make sure that we
        do not return duplicate available ids.  This will return a random id
        number between 1 and 9999 which are the limits of TC.
        """
        self._remove_used_ids()
        LOG.debug(_('Pulling new TC id'))
        tc_id = self._pull_id()
        LOG.debug(_('TC id %s pulled, testing for dupe') % tc_id)
        while tc_id in OVZTcRules._ovz_tc_inflight_ids:
            LOG.debug(_('TC id %s inflight already, pulling another') % tc_id)
            tc_id = self._pull_id()
            LOG.debug(_('TC id %s pulled, testing for dupe') % tc_id)
        LOG.info(_('TC id %s pulled, verified unique') % tc_id)
        self._reserve_id(tc_id)
        return tc_id

    def container_start(self):
        template = self._load_template('tc_container_start.template')
        search_list = {
            'prio': self.tc_id,
            'host_iface': CONF.ovz_tc_host_slave_device,
            'vz_iface': self.vz_iface,
            'bandwidth': self.bandwidth,
            'vz_address': self.address,
            'line_speed': CONF.ovz_tc_max_line_speed
        }
        ovz_utils.save_instance_metadata(self.instance['id'],
                                         'tc_id', self.tc_id,
                                         fail_on_dupe_key=False)
        return self._fill_template(template, search_list).splitlines()

    def container_stop(self):
        template = self._load_template('tc_container_stop.template')
        search_list = {
            'prio': self.tc_id,
            'host_iface': CONF.ovz_tc_host_slave_device,
            'vz_iface': self.vz_iface,
            'bandwidth': self.bandwidth,
            'vz_address': self.address
        }
        ovz_utils.save_instance_metadata(self.instance['id'],
                                         'tc_id', self.tc_id,
                                         fail_on_dupe_key=False)
        return self._fill_template(template, search_list).splitlines()

    def host_start(self):
        template = self._load_template('tc_host_start.template')
        search_list = {
            'host_iface': CONF.ovz_tc_host_slave_device,
            'line_speed': CONF.ovz_tc_max_line_speed
        }
        return self._fill_template(template, search_list).splitlines()

    def host_stop(self):
        template = self._load_template('tc_host_stop.template')
        search_list = {
            'host_iface': CONF.ovz_tc_host_slave_device
        }
        return self._fill_template(template, search_list).splitlines()

    def _load_template(self, template_name):
        """
        read and return the template file
        """
        full_template_path = '%s/%s' % (
            CONF.ovz_tc_template_dir, template_name)
        full_template_path = os.path.abspath(full_template_path)
        try:
            LOG.debug(_('Opening template file: %s') % full_template_path)
            template_file = open(full_template_path).read()
            LOG.debug(_('Template file opened successfully'))
            return template_file
        except Exception as err:
            LOG.error(_('Unable to open template: %s') % full_template_path)
            raise exception.FileNotFound(err)

    def _fill_template(self, template, search_list):
        """
        Take the vars in search_list and fill out template
        """
        return str(Template.Template(template, searchList=[search_list]))

    def _pull_id(self):
        """
        Pop a random id from the list of available ids for tc rules
        """
        return OVZTcRules._ovz_tc_available_ids[random.randint(
            0, len(OVZTcRules._ovz_tc_available_ids) - 1)]

    def _list_existing_ids(self):
        LOG.debug(_('Attempting to list existing IDs'))
        out = ovz_utils.execute('tc', 'filter', 'show', 'dev',
                                CONF.ovz_tc_host_slave_device,
                                run_as_root=True)
        ids = list()
        for line in out.splitlines():
            line = line.split()
            if line[0] == 'filter':
                tc_id = int(line[6])
                if tc_id not in ids:
                    ids.append(int(tc_id))

        # get the metadata for instances from the database
        # this will provide us with any ids of instances that aren't
        # currently running so that we can remove active and provisioned
        # but inactive ids from the available list
        instances_metadata = ovz_utils.read_all_instance_metadata()
        LOG.debug(_('Instances metadata: %s') % instances_metadata)
        if instances_metadata:
            for instance_id, meta in instances_metadata.iteritems():
                tc_id = meta.get('tc_id')
                if tc_id:
                    if tc_id not in ids:
                        LOG.debug(
                            _('TC id "%(tc_id)s" for instance '
                              '"%(instance_id)s" found') % locals())
                        ids.append(int(tc_id))
        return ids

    def _reserve_id(self, tc_id):
        """
        This removes the id from the _ovz_tc_available_ids and adds it to the
        _ovz_tc_inflight_ids
        """
        LOG.debug(_('Beginning reservation process'))
        OVZTcRules._ovz_tc_inflight_ids.append(tc_id)
        LOG.info(_('Added id "%s" to _ovz_tc_inflight_ids') % tc_id)
        OVZTcRules._ovz_tc_available_ids.remove(tc_id)
        LOG.info(_('Removed id "%s" from _ovz_tc_available_ids') % tc_id)

    def _remove_used_ids(self):
        """
        Clean ids that are currently provisioned on the host from the
        list of _ovz_tc_available_ids
        """
        LOG.debug(_('Beginning cleanup of used ids'))
        used_ids = self._list_existing_ids()
        LOG.debug(_('Used ids found, removing from _ovz_tc_available_ids'))
        for tc_id in used_ids:
            if tc_id in OVZTcRules._ovz_tc_available_ids:
                OVZTcRules._ovz_tc_available_ids.remove(tc_id)
        LOG.debug(_('Removed all ids in use'))

    def _save_instance_tc_id(self):
        """
        Save the tc id in the instance metadata in the database
        """
        ovz_utils.save_instance_metadata(self.instance['id'], 'tc_id',
                                         self.tc_id, fail_on_dupe_key=False)

    def _get_instance_tc_id(self):
        """
        Look up instance metadata in the db and see if there is already
        a tc_id for the instance
        """
        instance_metadata = ovz_utils.read_instance_metadata(
            self.instance['id'])
        LOG.debug(_('Instances metadata: %s') % instance_metadata)
        if instance_metadata:
            tc_id = instance_metadata.get('tc_id')
            LOG.debug(
                _('TC id for instance %(instance_id)s is %(tc_id)s') %
                {'instance_id': self.instance['id'], 'tc_id': tc_id})
            return tc_id
