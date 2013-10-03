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

"""
A driver specific to OpenVz as the support for Ovz in libvirt
is sketchy at best.
"""

import json
from nova.openstack.common import log as logging
from nova.openstack.common.gettextutils import _
from nova.virt.openvz import file as ovzfile
import os
from oslo.config import cfg


CONF = cfg.CONF

LOG = logging.getLogger('nova.virt.openvz.file_ext.ext_storage')


class OVZExtStorage(object):
    """
    Local store for volume information needing to be persisted to the local
    host.
    """
    def __init__(self, instance_id):
        """
        :param instance_id: CTID of the container
        :return: None
        """
        filename = '%s/%s.ext_storage' % (CONF.ovz_config_dir, instance_id)
        filename = os.path.abspath(filename)
        self.instance_id = instance_id
        self.local_store = ovzfile.OVZFile(filename, 600)

        # read the local store for the CTID or create it if it doesn't already
        # exist.
        with self.local_store:
            self.local_store.read()

        # preload volume info
        self.load_volumes()

    def load_volumes(self):
        """
        return the contents of self.contents *after* converting it from json
        to python objects.

        :return: list
        """
        try:
            self._volumes = json.loads(self.local_store.contents[0])
        except (ValueError, IndexError):
            self._volumes = dict()

    def add_volume(self, device, volume_info):
        """
        Add a volume to local storage so volumes can be reconnected to in
        emergencies without nova services if need be.

        :param device: Device name
        :param volume_info: Nova volume information from block_device_map
        :return: None
        """
        self._volumes[device] = volume_info

    def remove_volume(self, device):
        """
        removes a volume from the volume store.

        :param device:
        :return: None
        """
        try:
            self._volumes.pop(device)
            LOG.debug(
                _('Removed volume %(device)s from instance %(instance_id)s') %
                {'device': device, 'instance_id': self.instance_id})
        except KeyError:
            LOG.error(
                _('Volume %(device)s was not in local store for '
                  'instance %(instance_id)s') %
                {'device': device, 'instance_id': self.instance_id})

    def save(self):
        """
        Flushes contents of self._volumes to disk for persistance

        :return: None
        """
        self.local_store.set_contents(json.dumps(self._volumes))
        with self.local_store:
            self.local_store.write()

    def volumes(self):
        """
        Simple generator to give back the volumes in an iterable and
        adventurous way.

        :return: device name, connection info
        """
        for key in self._volumes.keys():
            yield key, self._volumes[key]
