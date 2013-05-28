# vim: tabstop=4 shiftwidth=4 softtabstop=4

# Copyright 2011 OpenStack LLC.
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
Generic transport class for OVZ Migrations.  Common methods will be placed
in this file for reuse.
"""
from nova.openstack.common import log as logging
import os
from oslo.config import cfg

CONF = cfg.CONF
LOG = logging.getLogger('nova.virt.openvz.migration_drivers.transport')


class OVZMigrationTransport(object):
    def __init__(self, src_path, dest_path, instance_id,
                 dest_host, skip_list=None):
        self.src_path = src_path
        self.dest_path = dest_path
        self.instance_id = instance_id
        self.dest_host = dest_host
        self.skip_list = skip_list
        self.user = CONF.ovz_migration_user
        self.container_path = os.path.abspath('%s/%s' %
                                              (CONF.ovz_ve_private_dir,
                                               self.instance_id))

    def send(self):
        """
        Code should go here to support what needs to be done upon sending a
        container to another host.  This should be called from all transport
        drivers after their work is done on the source host.
        """
        LOG.debug(_('Beginning send() for %s') % self.instance_id)
        # Generic use case transport code goes here, this code is run
        # after any code in a custom transport
        LOG.debug(_('Finished send() for %s') % self.instance_id)

    def receive(self):
        """
        Code should go here to support what needs to be done upon receiving a
        container from another host.  This should be called from all transport
        drivers after their work is done on the destination host.
        """
        LOG.debug(_('Beginning receive() for %s') % self.instance_id)
        # Generic use case transport code goes here, this code is run
        # after any code in a custom transport
        LOG.debug(_('Finished receive() for %s') % self.instance_id)

    def verify(self):
        """
        Check things on the destination host to make sure that the migration
        went well.
        """
        LOG.debug(_('Beginning verify() for %s') % self.instance_id)
        # Generic use case transport code goes here
        LOG.debug(_('Finished verify() for %s') % self.instance_id)
