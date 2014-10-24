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
Driver for OVZ Migrations.  Uses rsync as a backend.
"""
from nova.openstack.common import log as logging
from ovznovadriver.localization import _
from ovznovadriver.openvz.migration_drivers import transport
from ovznovadriver.openvz import utils as ovz_utils
import os
from oslo.config import cfg

CONF = cfg.CONF
LOG = logging.getLogger('ovznovadriver.openvz.migration_drivers.rsync')


class OVZMigrationRsyncTransport(transport.OVZMigrationTransport):
    def __init__(self, src_path, dest_path, instance_id,
                 dest_host, skip_list=None):
        super(OVZMigrationRsyncTransport, self).__init__(src_path,
                                                         dest_path,
                                                         instance_id,
                                                         dest_host,
                                                         skip_list)

    def send(self):
        """
        Send image/files to a destination.  This should be run on the source
        host.
        """
        LOG.debug(_('Running _rsync()'))
        self._rsync(self.src_path, self.dest_path)
        LOG.debug(_('Ran _rsync()'))
        super(OVZMigrationRsyncTransport, self).send()

    def _rsync(self, src_path, dest_path):
        """
        Copy a path from one place to another using rsync
        """
        dest = '%s@%s:%s' % (self.user, self.dest_host,
                             os.path.abspath(dest_path))
        counter = 1
        while counter <= CONF.ovz_rsync_iterations:
            LOG.info(_('RSyncing %(src_path)s, attempt: %(counter)s') %
                     locals())
            ovz_utils.execute('rsync',
                              '-qavz',
                              src_path,
                              dest,
                              run_as_root=True)
            counter += 1
