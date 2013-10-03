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

from Cheetah import Template
from nova import exception
from nova.openstack.common import log as logging
from nova.openstack.common.gettextutils import _
from nova.virt.openvz import file as ovzfile
from nova.virt.openvz.file_ext import boot as ovzboot
from nova.virt.openvz.file_ext import shutdown as ovzshutdown
from nova.virt.openvz.network_drivers import tc as ovztc
from nova.virt.openvz import utils as ovz_utils
import os
from oslo.config import cfg

CONF = cfg.CONF
LOG = logging.getLogger('nova.virt.openvz.network')


class OVZNetworkInterfaces(object):
    """
    Helper class for managing interfaces in OpenVz
    """
    #TODO(imsplitbit): fix this to work with redhat based distros
    def __init__(self, interface_info):
        """
        Manage the network interfaces for your OpenVz containers.
        """
        self.interface_info = interface_info
        LOG.debug(_('Interface info: %s') % self.interface_info)
        self.boot_file = ovzboot.OVZBootFile(self.interface_info[0]['id'], 755)
        with self.boot_file:
            self.boot_file.set_contents(list())
        self.shutdown_file = ovzshutdown.OVZShutdownFile(
            self.interface_info[0]['id'], 755)
        with self.shutdown_file:
            self.shutdown_file.set_contents(list())

    def add(self):
        """
        Add all interfaces and addresses to the container.
        """
        if CONF.ovz_use_veth_devs:
            for net_dev in self.interface_info:
                self._add_netif(net_dev['id'], net_dev['name'],
                                net_dev['bridge'], net_dev['mac'])
                tc_rules = ovztc.OVZTcRules()
                tc_rules.instance_info(net_dev['id'], net_dev['address'],
                                       net_dev['vz_host_if'])
                with self.boot_file:
                    self.boot_file.append(tc_rules.container_start())

                with self.shutdown_file:
                    self.shutdown_file.append(tc_rules.container_stop())

            self._load_template()
            self._fill_templates()
        else:
            for net_dev in self.interface_info:
                self._add_ip(net_dev['id'], net_dev['address'])

        with self.boot_file:
            self.boot_file.write()

        with self.shutdown_file:
            self.shutdown_file.write()

        self._set_nameserver(self.interface_info[0]['id'],
                             self.interface_info[0]['dns'])

    def _load_template(self):
        """
        Load templates needed for network interfaces.
        """
        if CONF.ovz_use_veth_devs:
            # TODO(imsplitbit): make a cheatah template for dhcp networking
            self.template = open(CONF.injected_network_template).read()
        else:
            self.template = None

    def _fill_templates(self):
        """
        Iterate through each file necessary for creating interfaces on a
        given platform, open the file and write the contents of the template
        to the file.
        """
        for filename in self._filename_factory():
            network_file = OVZNetworkFile(filename)
            self.iface_file = str(
                Template.Template(self.template,
                                  searchList=[
                                      {'interfaces': self.interface_info,
                                       'use_ipv6': CONF.use_ipv6}]))
            with network_file:
                network_file.append(self.iface_file.split('\n'))
                network_file.write()

    def _filename_factory(self, variant='debian'):
        """
        Generate a path for the file needed to implement an interface
        """
        #TODO(imsplitbit): Figure out how to introduce OS hints into nova
        # so we can generate support for redhat based distros.  This will
        # require an os hint to be placed in glance to use for making
        # decisions.  Then we can create a generator that will generate
        # redhat style interface paths like:
        #
        # /etc/sysconfig/network-scripts/ifcfg-eth0
        #
        # for now, we just return the debian path.

        redhat_path = '/etc/sysconfig/network-scripts/'
        debian_path = '/etc/network/interfaces'
        prefix = '%(private_dir)s/%(instance_id)s' %\
                 {'private_dir': CONF.ovz_ve_private_dir,
                  'instance_id': self.interface_info[0]['id']}
        prefix = os.path.abspath(prefix)

        #TODO(imsplitbit): fix this placeholder for RedHat compatibility.
        if variant == 'redhat':
            for net_dev in self.interface_info:
                path = prefix + redhat_path + ('ifcfg-%s' % net_dev['name'])
                path = os.path.abspath(path)
                LOG.debug(_('Generated filename %(path)s') % locals())
                yield path
        elif variant == 'debian':
            path = prefix + debian_path
            path = os.path.abspath(path)
            LOG.debug(_('Generated filename %(path)s') % locals())
            yield path
        else:
            raise exception.InvalidMetadata(
                _('Variant %(variant)s is not known') % locals())

    def _add_netif(self, instance_id, netif, bridge, host_mac):
        """
        This is a work around to add the eth devices the way OpenVZ
        wants them.

        When this works, it runs a command similar to this:

        vzctl set 1 --save --netif_add \
            eth0,,veth1.eth0,11:11:11:11:11:11,br100
        """
        host_if = 'veth%s.%s' % (instance_id, netif)
        ovz_utils.execute('vzctl', 'set', instance_id, '--save', '--netif_add',
                          '%s,,%s,%s,%s' % (netif, host_if, host_mac, bridge),
                          run_as_root=True)

    def _add_ip(self, instance_id, ip):
        """
        Add an IP address to a container if you are not using veth devices.

        Run the command:

        vzctl set <ctid> --save --ipadd <ip>

        If this fails to run an exception is raised as this indicates a failure
        to create a network available connection within the container thus
        making it unusable to all but local users and therefore unusable to
        nova.
        """
        ovz_utils.execute('vzctl', 'set', instance_id, '--save', '--ipadd', ip,
                          run_as_root=True)

    def _set_nameserver(self, instance_id, dns):
        """
        Get the nameserver for the assigned network and set it using
        OpenVz's tools.

        Run the command:

        vzctl set <ctid> --save --nameserver <nameserver>

        If this fails to run an exception is raised as this will indicate
        the container's inability to do name resolution.
        """
        ovz_utils.execute('vzctl', 'set', instance_id, '--save',
                          '--nameserver', dns, run_as_root=True)


class OVZNetworkFile(ovzfile.OVZFile):
    """
    An abstraction for network files.  This is necessary for multi-platform
    support.  OpenVz runs on all linux distros and can host all linux distros
    but they don't all create interfaces the same way.  This should make it
    easy to add interface files to all flavors of linux.
    """

    def __init__(self, filename):
        super(OVZNetworkFile, self).__init__(filename, 644)
