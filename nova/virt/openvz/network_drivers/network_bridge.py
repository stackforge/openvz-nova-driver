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

from nova.network import linux_net
from nova.openstack.common import log as logging
from nova.openstack.common.gettextutils import _

LOG = logging.getLogger('nova.virt.openvz.network_drivers.network_bridge')


class OVZNetworkBridgeDriver(object):
    """
    VIF driver for a Linux Bridge
    """

    def plug(self, instance, vif):
        """
        Ensure that the bridge exists and add a vif to it.
        """
        if (not vif['network'].get_meta('should_create_bridge', False) and
                vif['network'].get_meta('should_create_vlan', False)):
            if vif['network'].get_meta('should_create_vlan', False):
                LOG.debug(_('Ensuring bridge %(bridge)s and vlan %(vlan)s') %
                          {'bridge': vif['network']['bridge'],
                           'vlan': vif['network'].get_meta('vlan')})
                linux_net.LinuxBridgeInterfaceDriver.ensure_vlan_bridge(
                    vif['network'].get_meta('vlan'),
                    vif['network']['bridge'],
                    vif['network'].get_meta('bridge_interface'))
            else:
                LOG.debug(_('Ensuring bridge %s') % vif['network']['bridge'])
                linux_net.LinuxBridgeInterfaceDriver.ensure_bridge(
                    vif['network']['bridge'],
                    vif['network'].get_meta('bridge_interface'))

    def unplug(self, instance, vif):
        """
        No manual unplugging required
        """
        pass
