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
import base64
import fnmatch
import json
import math
from nova.compute import power_state
from nova import context
from nova import exception
from nova.network import linux_net
from nova.openstack.common import importutils
from nova.openstack.common import log as logging
from nova.openstack.common import loopingcall
from nova.virt import driver
from nova.virt import images
from ovznovadriver.localization import _
from ovznovadriver.openvz import file as ovzfile
from ovznovadriver.openvz.file_ext import boot as ovzboot
from ovznovadriver.openvz.file_ext import ext_storage
from ovznovadriver.openvz.file_ext import shutdown as ovzshutdown
from ovznovadriver.openvz import migration as ovz_migration
from ovznovadriver.openvz import network as ovznetwork
from ovznovadriver.openvz.network_drivers import tc as ovztc
from ovznovadriver.openvz import utils as ovz_utils
from ovznovadriver.openvz.volume_drivers import iscsi as ovziscsi
import os
from oslo.config import cfg
import socket
import time

openvz_conn_opts = [
    cfg.StrOpt('ovz_template_path',
               default='/var/lib/vz/template/cache',
               help='Path to use for local storage of OVz templates'),
    cfg.StrOpt('ovz_ve_private_dir',
               default='/var/lib/vz/private',
               help='Path where VEs will get placed'),
    cfg.StrOpt('ovz_ve_root_dir',
               default='/var/lib/vz/root',
               help='Path where the VEs root is'),
    cfg.StrOpt('ovz_image_template_dir',
               default='/var/lib/vz/template/cache',
               help='Path where OpenVZ images are'),
    cfg.StrOpt('ovz_config_dir',
               default='/etc/vz/conf',
               help='Where the OpenVZ configs are stored'),
    cfg.StrOpt('ovz_bridge_device',
               default='br100',
               help='Bridge device to map veth devices to'),
    cfg.StrOpt('ovz_disk_space_increment',
               default='G',
               help='Disk subscription increment'),
    cfg.StrOpt('ovz_vif_driver',
               default='ovznovadriver.openvz.network_drivers'
                       '.network_bridge.OVZNetworkBridgeDriver',
               help='The openvz VIF driver to configures the VIFs'),
    cfg.StrOpt('ovz_mount_options',
               default='defaults',
               help='Mount options for external filesystems'),
    cfg.StrOpt('ovz_volume_default_fs',
               default='ext3',
               help='FSType to use for mounted volumes'),
    cfg.StrOpt('ovz_tc_host_slave_device',
               default='eth0',
               help='Device to use as the root device for tc rules'),
    cfg.StrOpt('ovz_tc_template_dir',
               default='$pybasedir/../openvz-nova-driver/'
                       'ovznovadriver/openvz/network_drivers/templates',
               help='Where the tc templates are located'),
    cfg.StrOpt('ovz_tmp_dir',
               default='/var/tmp',
               help='Directory to use as temporary storage'),
    cfg.StrOpt('ovz_migration_method',
               default='python',
               help='Method to use for migrations'),
    cfg.StrOpt('ovz_migration_user',
               default='nova',
               help='User to use for running migrations'),
    cfg.StrOpt('ovz_migration_transport',
               default='rsync',
               help='Method to use to transport migrations'),
    cfg.StrOpt('ovz_vzmigrate_opts',
               default=None,
               help='Optional arguments to pass to vzmigrate'),
    cfg.BoolOpt('ovz_vzmigrate_online_migration',
                default=True,
                help='Perform an online migration of a container'),
    cfg.BoolOpt('ovz_vzmigrate_destroy_source_container_on_migrate',
                default=True,
                help='If a migration is successful do we delete the '
                     'container on the old host'),
    cfg.BoolOpt('ovz_vzmigrate_verbose_migration_logging',
                default=True,
                help='Log verbose messages from vzmigrate command'),
    cfg.BoolOpt('ovz_use_cpuunit',
                default=True,
                help='Use OpenVz cpuunits for guaranteed minimums'),
    cfg.BoolOpt('ovz_use_cpulimit',
                default=True,
                help='Use OpenVz cpulimit for maximum cpu limits'),
    cfg.BoolOpt('ovz_use_cpus',
                default=True,
                help='Use OpenVz cpus for max cpus '
                     'available to the container'),
    cfg.BoolOpt('ovz_use_ioprio',
                default=True,
                help='Use IO fair scheduling'),
    cfg.BoolOpt('ovz_disk_space_oversub',
                default=True,
                help='Allow over subscription of local disk'),
    cfg.BoolOpt('ovz_use_disk_quotas',
                default=True,
                help='Use disk quotas to contain disk usage'),
    cfg.BoolOpt('ovz_use_veth_devs',
                default=True,
                help='Use veth devices rather than venet'),
    cfg.BoolOpt('ovz_use_dhcp',
                default=False,
                help='Use dhcp for network configuration'),
    cfg.BoolOpt('ovz_use_bind_mount',
                default=False,
                help='Use bind mounting instead of simfs'),
    cfg.IntOpt('ovz_ioprio_limit',
               default=7,
               help='Limit for IO priority weighting'),
    cfg.IntOpt('ovz_system_num_tries',
               default=3,
               help='Number of attempts to make when '
                    'running a system command'),
    cfg.IntOpt('ovz_kmemsize_percent_of_memory',
               default=20,
               help='Percent of memory of the container to allow to be used '
                    'by the kernel'),
    cfg.IntOpt('ovz_kmemsize_barrier_differential',
               default=10,
               help='Difference of kmemsize barrier vs limit'),
    cfg.IntOpt('ovz_memory_unit_size',
               default=512,
               help='Unit size in MB'),
    cfg.IntOpt('ovz_tc_id_max',
               default=9999,
               help='Max TC id to be used in generating a new id'),
    cfg.IntOpt('ovz_tc_mbit_per_unit',
               default=20,
               help='Mbit per unit bandwidth limit'),
    cfg.IntOpt('ovz_tc_max_line_speed',
               default=1000,
               help='Line speed in Mbit'),
    cfg.IntOpt('ovz_file_descriptors_per_unit',
               default=4096,
               help='Max open file descriptors per memory unit'),
    cfg.IntOpt('ovz_rsync_iterations',
               default=1,
               help='Number of times to rsync a container when migrating'),
    cfg.IntOpt('ovz_numtcpsock_default',
               default=2000,
               help='Default number of tcp sockets to give each container'),
    cfg.FloatOpt('ovz_disk_space_oversub_percent',
                 default=1.10,
                 help='Local disk over subscription percentage'),
    cfg.FloatOpt('ovz_cpulimit_overcommit_multiplier',
                 default=1.0,
                 help='Multiplier for cpulimit to facilitate over '
                      'committing cpu resources'),
    cfg.DictOpt('ovz_numtcpsock_map',
                default={"8192": 3000, "1024": 2000, "4096": 2000,
                         "2048": 2000, "16384": 4000, "512": 2000},
                help='Mapped values for flavors based on memory allocation'),
]

CONF = cfg.CONF
CONF.register_opts(openvz_conn_opts)
CONF.import_opt('host', 'nova.config')

LOG = logging.getLogger('ovznovadriver.openvz.driver')


class OpenVzDriver(driver.ComputeDriver):
    # OpenVz sets the upper limit of cpuunits to 500000
    MAX_CPUUNITS = 500000

    def __init__(self, virtapi, read_only=False):
        """
        Create an instance of the openvz connection.
        """
        super(OpenVzDriver, self).__init__(virtapi)
        self.utility = dict()
        self.host_stats = dict()
        self._initiator = None
        self.host = None
        self.read_only = read_only
        self.vif_driver = importutils.import_object(CONF.ovz_vif_driver)
        LOG.debug(_('__init__ complete in OpenVzDriver'))

    def init_host(self, host=socket.gethostname()):
        """
        Initialize anything that is necessary for the driver to function,
        including catching up with currently running VE's on the given host.
        """
        LOG.debug(_('Hostname: %s') % host)

        if not self.host:
            self.host = host

        LOG.debug(_('Determining the computing power of the host'))
        self._get_cpulimit()
        self._refresh_host_stats()

        LOG.debug(_('Flushing host TC rules if there are any'))
        tc = ovztc.OVZTcRules()
        sf = ovzshutdown.OVZShutdownFile(0, 700)
        if sf.exists():
            with sf:
                sf.read()
                sf.run_contents()

        LOG.debug(_('Setting up host TC rules'))
        LOG.debug(_('Making TC startup script for the host'))
        bf = ovzboot.OVZBootFile(0, 700)
        with bf:
            # Make sure we're starting with a blank file
            bf.set_contents(list())
            bf.make_proper_script()
            bf.append(tc.host_start())
            bf.write()

        LOG.debug(_('Making TC shutdown script for the host'))

        # Starting fresh
        with sf:
            # Make sure we're starting with a blank file
            sf.set_contents(list())
            sf.make_proper_script()
            sf.append(tc.host_stop())
            sf.write()

        LOG.debug(_('Done setting up TC files, running TC startup'))
        bf.run_contents()

        LOG.debug(_('init_host complete in OpenVzDriver'))

    def list_instances(self):
        """
        Return the names of all the instances known to the container
        layer, as a list.
        """
        out = ovz_utils.execute(
            'vzlist', '--all', '--no-header', '--output', 'ctid',
            raise_on_error=False, run_as_root=True)
        ctids = list()
        if out:
            for line in out.splitlines():
                ctid = line.split()[0]
                ctids.append(ctid)

        return ctids

    def list_instance_uuids(self):
        """
        Return the UUIDS of all the instances known to the virtualization
        layer, as a list.
        """
        uuids = list()
        out = ovz_utils.execute(
            'vzlist', '--all', '--no-header', '-o', 'description',
            raise_on_error=False, run_as_root=True)
        if out:
            for line in out.splitlines():
                try:
                    uuids.append(json.loads(line)['uuid'])
                except (KeyError, ValueError):
                    LOG.debug(_("Unable to find a valid uuid. Are there "
                                "any instances?"))

        return uuids

    def get_host_stats(self, refresh=False):
        """
        Gather host usage stats and return their values for scheduler
        accuracy
        """
        if refresh:
            self._refresh_host_stats()

        return self.host_stats

    def _refresh_host_stats(self):
        """
        Abstraction for updating host stats
        """
        host_stats = dict()
        host_stats['vcpus'] = ovz_utils.get_vcpu_total()
        host_stats['vcpus_used'] = ovz_utils.get_vcpu_used()
        host_stats['cpu_info'] = json.dumps(ovz_utils.get_cpuinfo())
        host_stats['memory_mb'] = ovz_utils.get_memory_mb_total()
        host_stats['memory_mb_used'] = ovz_utils.get_memory_mb_used()
        host_stats['host_memory_total'] = host_stats['memory_mb']
        host_stats['host_memory_free'] = (host_stats['memory_mb'] -
                                          host_stats['memory_mb_used'])
        host_stats['disk_total'] = ovz_utils.get_local_gb_total()
        host_stats['disk_used'] = ovz_utils.get_local_gb_used()
        host_stats['disk_available'] = (
            host_stats['disk_total'] - host_stats['disk_used'])
        host_stats['local_gb'] = host_stats['disk_total']
        host_stats['local_gb_used'] = host_stats['disk_used']
        host_stats['hypervisor_type'] = ovz_utils.get_hypervisor_type()
        host_stats['hypervisor_version'] = ovz_utils.get_hypervisor_version()
        host_stats['hypervisor_hostname'] = self.host
        self.host_stats = host_stats.copy()

    def spawn(self, context, instance, image_meta, injected_files,
              admin_password, network_info=None, block_device_info=None):
        """
        Create a new virtual environment on the container platform.

        The given parameter is an instance of nova.compute.service.Instance.
        This function should use the data there to guide the creation of
        the new instance.

        The work will be done asynchronously.  This function returns a
        task that allows the caller to detect when it is complete.

        Once this successfully completes, the instance should be
        running (power_state.RUNNING).

        If this fails, any partial instance should be completely
        cleaned up, and the container platform should be in the state
        that it was before this call began.
        """

        # Update state to inform the nova stack that the VE is launching
        self.virtapi.instance_update(
            context, instance['uuid'], {'power_state': power_state.BUILDING})
        LOG.debug(_('instance %s: is building') % instance['name'])

        # Get current usages and resource availablity.
        self._get_cpuunits_usage()

        # Go through the steps of creating a container
        # TODO(imsplitbit): Need to add conditionals around this stuff to make
        # it more durable during failure. And roll back changes made leading
        # up to the error.
        self._cache_image(context, instance)
        self._create_vz(instance)
        self._set_vz_os_hint(instance)
        self._configure_vz(instance)
        self._set_name(instance)
        self._set_description(instance)

        # TODO(imsplitbit): There's probably a better way to do this
        has_networking = False
        try:
            for vif in network_info:
                if vif.labeled_ips():
                    has_networking = True
        except ValueError:
            has_networking = False
        if has_networking:
            self.plug_vifs(instance, network_info)
            self._setup_networking(instance, network_info)

        self._set_hostname(instance)
        self._set_instance_size(instance)
        self._set_onboot(instance)

        if block_device_info:
            self._attach_volumes(
                instance, block_device_info)

        if injected_files:
            self._inject_files(instance, injected_files)

        self._start(instance)
        self._gratuitous_arp_all_addresses(instance, network_info)

        if admin_password:
            self.set_admin_password(context, instance['id'],
                                    admin_password)

        # Begin making our looping async call
        timer = loopingcall.FixedIntervalLoopingCall()

        # I stole this from the libvirt driver but it is appropriate to
        # have this looping timer call so that if a VE doesn't start right
        # away we can defer all of this.
        def _wait_for_boot():
            try:
                state = self.get_info(instance)['state']
                if state == power_state.RUNNING:
                    LOG.debug(_('instance %s: booted') % instance['name'])
                    timer.stop()

            except Exception:
                LOG.error(_('instance %s: failed to boot') % instance['name'])
                timer.stop()

            timer.stop()

        timer.f = _wait_for_boot
        return timer.start(interval=0.5)

    def _create_vz(self, instance):
        """
        Attempt to load the image from openvz's image cache, upon failure
        cache the image and then retry the load.

        Run the command:

        vzctl create <ctid> --ostemplate <image_ref>

        If this fails to execute an exception is raised because this is the
        first in a long list of many critical steps that are necessary for
        creating a working VE.
        """

        # TODO(imsplitbit): This needs to set an os template for the image
        # as well as an actual OS template for OpenVZ to know what config
        # scripts to use.  This can be problematic because there is no concept
        # of OS name, it is arbitrary so we will need to find a way to
        # correlate this to what type of disto the image actually is because
        # this is the clue for openvz's utility scripts.  For now we will have
        # to set it to 'ubuntu'

        # This will actually drop the os from the local image cache
        ovz_utils.execute(
            'vzctl', 'create', instance['id'], '--ostemplate',
            instance['image_ref'], run_as_root=True)

    def _set_vz_os_hint(self, instance, ostemplate='ubuntu'):
        """
        This exists as a stopgap because currently there are no os hints
        in the image managment of nova.  There are ways of hacking it in
        via image_properties but this requires special case code just for
        this driver.

        Run the command:

        vzctl set <ctid> --save --ostemplate <ostemplate>

        Currently ostemplate defaults to ubuntu.  This facilitates setting
        the ostemplate setting in OpenVZ to allow the OpenVz helper scripts
        to setup networking, nameserver and hostnames.  Because of this, the
        openvz driver only works with debian based distros.

        If this fails to run an exception is raised as this is a critical piece
        in making openvz run a container.
        """

        # This sets the distro hint for OpenVZ to later use for the setting
        # of resolver, hostname and the like

        # TODO(imsplitbit): change the ostemplate default value to a flag
        ovz_utils.execute('vzctl', 'set', instance['id'], '--save',
                          '--ostemplate', ostemplate, run_as_root=True)

    def _cache_image(self, context, instance):
        """
        Create the disk image for the virtual environment.  This uses the
        image library to pull the image down the distro image into the openvz
        template cache.  This is the method that openvz wants to operate
        properly.
        """

        image_name = '%s.tar.gz' % instance['image_ref']
        full_image_path = '%s/%s' % (CONF.ovz_image_template_dir, image_name)

        if not os.path.exists(full_image_path):
            # Grab image and place it in the image cache
            images.fetch(context, instance['image_ref'], full_image_path,
                         instance['user_id'], instance['project_id'])
            return True
        else:
            return False

    def _configure_vz(self, instance, config='basic'):
        """
        This adds the container root into the vz meta data so that
        OpenVz acknowledges it as a container.  Punting to a basic
        config for now.

        Run the command:

        vzctl set <ctid> --save --applyconfig <config>

        This sets the default configuration file for openvz containers.  This
        is a requisite step in making a container from an image tarball.

        If this fails to run successfully an exception is raised because the
        container this executes against requires a base config to start.
        """
        ovz_utils.execute('vzctl', 'set', instance['id'], '--save',
                          '--applyconfig', config, run_as_root=True)

    def _set_onboot(self, instance):
        """
        Method to set the onboot status of the instance. This is done
        so that openvz does not handle booting, and instead the compute
        manager can handle initialization.

        I run the command:

        vzctl set <ctid> --onboot no --save

        If I fail to run an exception is raised.
        """
        ovz_utils.execute('vzctl', 'set', instance['id'], '--onboot', 'no',
                          '--save', run_as_root=True)

    def _start(self, instance):
        """
        Method to start the instance, I don't believe there is a nova-ism
        for starting so I am wrapping it under the private namespace and
        will call it from expected methods.  i.e. resume

        Run the command:

        vzctl start <ctid>

        If this fails to run an exception is raised.  I don't think it needs
        to be explained why.
        """
        # Attempt to start the VE.
        # NOTE: The VE will throw a warning that the hostname is invalid
        # if it isn't valid.  This is logged in LOG.error and is not
        # an indication of failure.
        ovz_utils.execute('vzctl', 'start', instance['id'], run_as_root=True)

        # Set instance state as RUNNING
        self.virtapi.instance_update(
            context.get_admin_context(), instance['uuid'],
            {'power_state': power_state.RUNNING})

        bf = ovzboot.OVZBootFile(instance['id'], 700)
        with bf:
            bf.read()
            bf.run_contents()

    def _stop(self, instance):
        """
        Method to stop the instance.  This doesn't seem to be a nova-ism but
        it is for openvz so I am wrapping it under the private namespace and
        will call it from expected methods.  i.e. pause

        Run the command:

        vzctl stop <ctid>

        If this fails to run an exception is raised for obvious reasons.
        """
        sf = ovzshutdown.OVZShutdownFile(instance['id'], 700)
        with sf:
            sf.read()
            sf.run_contents()

        ovz_utils.execute('vzctl', 'stop', instance['id'], run_as_root=True)

        # Update instance state
        self.virtapi.instance_update(
            context.get_admin_context(), instance['uuid'],
            {'power_state': power_state.SHUTDOWN})

    def _set_hostname(self, instance, hostname=None):
        """
        Set the hostname of a given container.  The option to pass
        a hostname to the method was added with the intention to allow the
        flexibility to override the hostname listed in the instance ref.  A
        good person wouldn't do this but it was needed for some testing and
        therefore remains for future use.

        Run the command:

        vzctl set <ctid> --save --hostname <hostname>

        If this fails to execute an exception is raised because the hostname is
        used in most cases for connecting to the guest.  While having the
        hostname not match the dns name is not a complete problem it can lead
        name mismatches.  One could argue that this should be a softer error
        and I might have a hard time arguing with that one.
        """
        if not hostname:
            hostname = instance['hostname']

        ovz_utils.execute('vzctl', 'set', instance['id'], '--save',
                          '--hostname', hostname, run_as_root=True)

    def _gratuitous_arp_all_addresses(self, instance, network_info):
        """
        Iterate through all addresses assigned to the container and send
        a gratuitous arp over it's interface to make sure arp caches have
        the proper mac address.
        """
        # TODO(imsplitbit): send id, iface, container mac, container ip and
        # gateway to _send_garp
        iface_counter = -1
        for vif in network_info:
            network = vif['network']
            v4_subnets = []
            for subnet in network['subnets']:
                if subnet['version'] == 4:
                    v4_subnets.append(subnet)
            iface_counter += 1
            vz_iface = "eth%d" % iface_counter
            LOG.debug(_('VZ interface: %s') % vz_iface)
            LOG.debug(_('bridge interface: %s') %
                      network.get_meta('bridge_interface'))
            LOG.debug(_('bridge: %s') % network['bridge'])
            LOG.debug(_('address block: %s') % v4_subnets[0]['cidr'])
            LOG.debug(_('network label: %s') % network['label'])
            for v4_subnet in v4_subnets:
                for ip in v4_subnet['ips']:
                    LOG.debug(_('Address: %s') % ip['address'])
                    LOG.debug(
                        _('Running _send_garp(%(id)s %(ip)s %(vz_iface)s)') %
                        {'id': instance['id'], 'ip': ip['address'],
                         'vz_iface': vz_iface})
                    self._send_garp(instance['id'], ip['address'], vz_iface)

    def _send_garp(self, instance_id, ip_address, interface):
        """
        It is possible in nova to have a recently released ip address given
        to a new container.  We need to send a gratuitous arp on each
        interface for the address assigned.

        The command looks like this:

        arping -q -c 5 -A -I eth0 10.0.2.4

        If this fails to execute no exception is raised because even if the
        gratuitous arp fails the container will most likely be available as
        soon as the switching/routing infrastructure's arp cache clears.
        """
        ovz_utils.execute('vzctl', 'exec2', instance_id, 'arping', '-q', '-c',
                          '5', '-A', '-I', interface, ip_address,
                          run_as_root=True, raise_on_error=False)

    def _set_name(self, instance):
        """
        Store the name of an instance in the name field for openvz.  This is
        done to facilitate the get_info method which only accepts an instance
        name as an argument.

        Run the command:

        vzctl set <ctid> --save --name <name>

        If this fails to run an exception is raised.  This is due to the
        requirement of the get_info method to have the name field filled out.
        """
        ovz_utils.execute(
            'vzctl', 'set', instance['id'], '--save', '--name',
            instance['name'], run_as_root=True)

    def _set_description(self, instance):
        """
        Save information important to associate nova information with this
        instance.

        :param instance:
        :return: None
        """
        info = json.dumps({'uuid': instance['uuid']})
        ovz_utils.execute(
            'vzctl', 'set', instance['id'], '--save', '--description', info,
            run_as_root=True)

    def _find_by_name(self, instance_name):
        """
        This method exists to facilitate get_info.  The get_info method only
        takes an instance name as it's argument.

        Run the command:

        vzlist -H --all --name_filter <name>

        If this fails to run an exception is raised because if we cannot
        locate an instance by it's name then the driver will fail to work.
        """

        # The required method get_info only accepts a name so we need a way
        # to correlate name and id without maintaining another state/meta db
        out = ovz_utils.execute('vzlist', '-H', '-o', 'ctid,status,name',
                                '--all', '--name_filter', instance_name,
                                raise_on_error=False, run_as_root=True)

        # If out is empty, there is no instance known to OpenVz by that
        # name and an exception should be raised
        if not out:
            raise exception.InstanceNotFound(
                _('Instance %s doesnt exist') % instance_name)

        # Break the output into usable chunks
        out = out.split()
        result = {'name': out[2], 'id': out[0], 'state': out[1]}
        LOG.debug(_('Results from _find_by_name: %s') % result)
        return result

    def _access_control(self, instance, host, mask=32, port=None,
                        protocol='tcp', access_type='allow'):
        """
        Does what it says.  Use this to interface with the
        linux_net.iptables_manager to allow/deny access to a host
        or network
        """

        if access_type == 'allow':
            access_type = 'ACCEPT'
        elif access_type == 'deny':
            access_type = 'REJECT'
        else:
            LOG.error(_('Invalid access_type: %s') % access_type)
            raise exception.InvalidInput(
                _('Invalid access_type: %s') % access_type)

        if port is None:
            port = ''
        else:
            port = '--dport %s' % port

        # Create our table instance
        tables = [
            linux_net.iptables_manager.ipv4['filter'],
            linux_net.iptables_manager.ipv6['filter']
        ]

        rule = '-s %s/%s -p %s %s -j %s' %\
               (host, mask, protocol, port, access_type)

        for table in tables:
            table.add_rule(str(instance['id']), rule)

        # Apply the rules
        linux_net.iptables_manager.apply()

    def _initial_secure_host(self, instance):
        """
        Lock down the host in it's default state
        """

        # TODO(tim.simpson) This hangs if the "lock_path" FLAG value refers to
        #                   a directory which can't be locked.  It'd be nice
        #                   if we could somehow detect that and raise an error
        #                   instead.

        # Create our table instance and add our chains for the instance
        table_ipv4 = linux_net.iptables_manager.ipv4['filter']
        table_ipv6 = linux_net.iptables_manager.ipv6['filter']
        table_ipv4.add_chain(str(instance['id']))
        table_ipv6.add_chain(str(instance['id']))

        # As of right now there is no API call to manage security
        # so there are no rules applied, this really is just a pass.
        # The thought here is to allow us to pass a list of ports
        # that should be globally open and lock down the rest but
        # cannot implement this until the API passes a security
        # context object down to us.

        # Apply the rules
        linux_net.iptables_manager.apply()

    def resume_state_on_host_boot(self, context, instance, network_info,
                                  block_device_info=None):
        """resume guest state when a host is booted."""
        if block_device_info:
            self._attach_volumes(instance, block_device_info)
        self._start(instance)

    def reset_instance_size(self, instance, restart_instance=False):
        """
        Public method for changing an instance back to it's original
        flavor spec.  If this fails an exception is raised because this
        means that the instance flavor setting couldn't be rescued.
        """
        try:
            self._set_instance_size(instance)
            if restart_instance:
                self.reboot(instance, None, None, None, None)
            return True
        except exception.InstanceUnacceptable:
            raise exception.InstanceUnacceptable(
                _("Instance size reset FAILED"))

    def _set_numflock(self, instance, max_file_descriptors):
        """
        Run the command:

        vzctl set <ctid> --save --numflock <number>
        """

        ovz_utils.execute('vzctl', 'set', instance['id'], '--save',
                          '--numflock', max_file_descriptors,
                          run_as_root=True)

    def _set_numfiles(self, instance, max_file_descriptors):
        """
        Run the command:

        vzctl set <ctid> --save --numfile <number>
        """

        ovz_utils.execute('vzctl', 'set', instance['id'], '--save',
                          '--numfile', max_file_descriptors,
                          run_as_root=True)

    def _set_numtcpsock(self, instance, memory_mb):
        """
        Run the commnand:

        vzctl set <ctid> --save --numtcpsock <number>

        :param instance:
        :return:
        """
        try:
            tcp_sockets = CONF.ovz_numtcpsock_map[str(memory_mb)]
        except (ValueError, TypeError, KeyError, cfg.NoSuchOptError):
            LOG.error(_('There was no acceptable tcpsocket number found '
                        'defaulting to %s') % CONF.ovz_numtcpsock_default)
            tcp_sockets = CONF.ovz_numtcpsock_default

        ovz_utils.execute('vzctl', 'set', instance['id'], '--save',
                          '--numtcpsock', tcp_sockets, run_as_root=True)

    def _set_instance_size(self, instance, network_info=None,
                           is_migration=False):
        """
        Given that these parameters make up and instance's 'size' we are
        bundling them together to make resizing an instance on the host
        an easier task.
        """
        instance_size = ovz_utils.format_system_metadata(
            instance['system_metadata'])

        LOG.debug(_('Instance system metadata: %s') % instance_size)

        if is_migration:
            instance_memory_mb = instance_size.get(
                'new_instance_type_memory_mb', None)
            if not instance_memory_mb:
                instance_memory_mb = instance_size.get(
                    'instance_type_memory_mb')
            instance_vcpus = instance_size.get('new_instance_type_vcpus', None)
            if not instance_vcpus:
                instance_vcpus = instance_size.get('instance_type_vcpus')
            instance_root_gb = instance_size.get(
                'new_instance_type_root_gb', None)
            if not instance_root_gb:
                instance_root_gb = instance_size.get('instance_type_root_gb')
        else:
            instance_memory_mb = instance_size.get('instance_type_memory_mb')
            instance_vcpus = instance_size.get('instance_type_vcpus')
            instance_root_gb = instance_size.get('instance_type_root_gb')

        instance_memory_mb = int(instance_memory_mb)
        instance_vcpus = int(instance_vcpus)
        instance_root_gb = int(instance_root_gb)

        instance_memory_bytes = ((instance_memory_mb * 1024) * 1024)
        instance_memory_pages = self._calc_pages(instance_memory_mb)
        percent_of_resource = self._percent_of_resource(instance_memory_mb)

        memory_unit_size = int(CONF.ovz_memory_unit_size)
        max_fd_per_unit = int(CONF.ovz_file_descriptors_per_unit)
        max_fd = int(instance_memory_mb / memory_unit_size) * max_fd_per_unit
        self._set_vmguarpages(instance, instance_memory_pages)
        self._set_privvmpages(instance, instance_memory_pages)
        self._set_kmemsize(instance, instance_memory_bytes)
        self._set_numfiles(instance, max_fd)
        self._set_numflock(instance, max_fd)
        if CONF.ovz_use_cpuunit:
            self._set_cpuunits(instance, percent_of_resource)
        if CONF.ovz_use_cpulimit:
            self._set_cpulimit(instance, percent_of_resource)
        if CONF.ovz_use_cpus:
            self._set_cpus(instance, instance_vcpus)
        if CONF.ovz_use_ioprio:
            self._set_ioprio(instance, instance_memory_mb)
        if CONF.ovz_use_disk_quotas:
            self._set_diskspace(instance, instance_root_gb)

        if network_info:
            self._generate_tc_rules(instance, network_info, is_migration)

    def _generate_tc_rules(self, instance, network_info, is_migration=False):
        """
        Utility method to generate tc info for instances that have been
        resized and/or migrated
        """
        LOG.debug(_('Setting network sizing'))
        bf = ovzboot.OVZBootFile(instance['id'], 755)
        sf = ovzshutdown.OVZShutdownFile(instance['id'], 755)

        if not is_migration:
            with sf:
                LOG.debug(_('Cleaning TC rules for %s') % instance['id'])
                sf.read()
                sf.run_contents(raise_on_error=False)

        # On resize we throw away existing tc_id and make a new one
        # because the resize *could* have taken place on a different host
        # where the tc_id is already in use.
        meta = ovz_utils.read_instance_metadata(instance['id'])
        tc_id = meta.get('tc_id', None)
        if tc_id:
            ovz_utils.remove_instance_metadata_key(instance['id'], 'tc_id')

        with sf:
            sf.set_contents(list())

        with bf:
            bf.set_contents(list())

        LOG.debug(_('Getting network dict for: %s') % instance['id'])
        interfaces = ovz_utils.generate_network_dict(instance['id'],
                                                     network_info)
        for net_dev in interfaces:
            LOG.debug(_('Adding tc rules for: %s') %
                      net_dev['vz_host_if'])
            tc = ovztc.OVZTcRules()
            tc.instance_info(net_dev['id'], net_dev['address'],
                             net_dev['vz_host_if'])
            with bf:
                bf.append(tc.container_start())

            with sf:
                sf.append(tc.container_stop())

        with bf:
            LOG.debug(_('Running TC rules for: %s') % instance['id'])
            bf.run_contents()
            LOG.debug(_('Saving TC rules for: %s') % instance['id'])
            bf.write()

        with sf:
            sf.write()

    def _set_vmguarpages(self, instance, num_pages):
        """
        Set the vmguarpages attribute for a container.  This number represents
        the number of 4k blocks of memory that are guaranteed to the container.
        This is what shows up when you run the command 'free' in the container.

        Run the command:

        vzctl set <ctid> --save --vmguarpages <num_pages>

        If this fails to run then an exception is raised because this affects
        the memory allocation for the container.
        """
        ovz_utils.execute('vzctl', 'set', instance['id'], '--save',
                          '--vmguarpages', num_pages, run_as_root=True)

    def _set_privvmpages(self, instance, num_pages):
        """
        Set the privvmpages attribute for a container.  This represents the
        memory allocation limit.  Think of this as a bursting limit.  For now
        We are setting to the same as vmguarpages but in the future this can be
        used to thin provision a box.

        Run the command:

        vzctl set <ctid> --save --privvmpages <num_pages>

        If this fails to run an exception is raised as this is essential for
        the running container to operate properly within it's memory
        constraints.
        """
        ovz_utils.execute('vzctl', 'set', instance['id'], '--save',
                          '--privvmpages', num_pages, run_as_root=True)

    def _set_kmemsize(self, instance, instance_memory):
        """
        Set the kmemsize attribute for a container.  This represents the
        amount of the container's memory allocation that will be made
        available to the kernel.  This is used for tcp connections, unix
        sockets and the like.

        This runs the command:

        vzctl set <ctid> --save --kmemsize <barrier>:<limit>

        If this fails to run an exception is raised as this is essential for
        the container to operate under a normal load.  Defaults for this
        setting are completely inadequate for any normal workload.
        """

        # Now use the configuration CONF to calculate the appropriate
        # values for both barrier and limit.
        kmem_limit = int(instance_memory * (
            float(CONF.ovz_kmemsize_percent_of_memory) / 100.0))
        kmem_barrier = int(kmem_limit * (
            float(CONF.ovz_kmemsize_barrier_differential) / 100.0))
        kmemsize = '%d:%d' % (kmem_barrier, kmem_limit)

        ovz_utils.execute('vzctl', 'set', instance['id'], '--save',
                          '--kmemsize', kmemsize, run_as_root=True)

    def _set_cpuunits(self, instance, percent_of_resource):
        """
        Set the cpuunits setting for the container.  This is an integer
        representing the number of cpu fair scheduling counters that the
        container has access to during one complete cycle.

        Run the command:

        vzctl set <ctid> --save --cpuunits <units>

        If this fails to run an exception is raised because this is the secret
        sauce to constraining each container within it's subscribed slice of
        the host node.
        """
        LOG.debug(_('Reported cpuunits %s') % self.MAX_CPUUNITS)
        LOG.debug(_('Reported percent of resource: %s') % percent_of_resource)

        units = int(round(self.MAX_CPUUNITS * percent_of_resource))

        if units > self.MAX_CPUUNITS:
            units = self.MAX_CPUUNITS

        ovz_utils.execute('vzctl', 'set', instance['id'], '--save',
                          '--cpuunits', units, run_as_root=True)

    def _set_cpulimit(self, instance, percent_of_resource):
        """
        This is a number in % equal to the amount of cpu processing power
        the container gets.  NOTE: 100% is 1 logical cpu so if you have 12
        cores with hyperthreading enabled then 100% of the whole host machine
        would be 2400% or --cpulimit 2400.

        Run the command:

        vzctl set <ctid> --save --cpulimit <cpulimit>

        If this fails to run an exception is raised because this is the secret
        sauce to constraining each container within it's subscribed slice of
        the host node.
        """

        cpulimit = int(round(
            (self.utility['CPULIMIT'] * percent_of_resource) *
            CONF.ovz_cpulimit_overcommit_multiplier))

        if cpulimit > self.utility['CPULIMIT']:
            cpulimit = self.utility['CPULIMIT']

        ovz_utils.execute('vzctl', 'set', instance['id'], '--save',
                          '--cpulimit', cpulimit, run_as_root=True)

    def _set_cpus(self, instance, vcpus):
        """
        The number of logical cpus that are made available to the container.
        Default to showing 2 cpus to each container at a minimum.

        Run the command:

        vzctl set <ctid> --save --cpus <num_cpus>

        If this fails to run an exception is raised because this limits the
        number of cores that are presented to each container and if this fails
        to set *ALL* cores will be presented to every container, that be bad.
        """
        vcpus = int(vcpus)
        LOG.debug(_('VCPUs: %s') % vcpus)
        utility_cpus = self.utility['CPULIMIT'] / 100

        if vcpus > utility_cpus:
            LOG.debug(
                _('OpenVZ thinks vcpus "%(vcpus)s" '
                  'is greater than "%(utility_cpus)s"') % locals())
            # We can't set cpus higher than the number of actual logical cores
            # on the system so set a cap here
            vcpus = self.utility['CPULIMIT'] / 100

        LOG.debug(_('VCPUs: %s') % vcpus)

        ovz_utils.execute('vzctl', 'set', instance['id'], '--save', '--cpus',
                          vcpus, run_as_root=True)

    def _set_ioprio(self, instance, memory_mb):
        """
        Set the IO priority setting for a given container.  This is represented
        by an integer between 0 and 7.
        Run the command:

        vzctl set <ctid> --save --ioprio <iopriority>

        If this fails to run an exception is raised because all containers are
        given the same weight by default which will cause bad performance
        across all containers when there is input/output contention.
        """
        # The old algorithm made it impossible to distinguish between a
        # 512MB container and a 2048MB container for IO priority.  We will
        # for now follow a simple map to create a more non-linear
        # relationship between the flavor sizes and their IO priority groups

        # The IO priority of a container is grouped in 1 of 8 groups ranging
        # from 0 to 7.  We can calculate an appropriate value by finding out
        # how many ovz_memory_unit_size chunks are in the container's memory
        # allocation and then using python's math library to solve for that
        # number's logarithm.
        num_chunks = int(int(memory_mb) / CONF.ovz_memory_unit_size)

        try:
            ioprio = int(round(math.log(num_chunks, 2)))
        except ValueError:
            ioprio = 0

        if ioprio > 7:
            # ioprio can't be higher than 7 so set a ceiling
            ioprio = 7

        ovz_utils.execute('vzctl', 'set', instance['id'], '--save', '--ioprio',
                          ioprio, run_as_root=True)

    def _set_diskspace(self, instance, root_gb):
        """
        Implement OpenVz disk quotas for local disk space usage.
        This method takes a soft and hard limit.  This is also the amount
        of diskspace that is reported by system tools such as du and df inside
        the container.  If no argument is given then one will be calculated
        based on the values in the instance_types table within the database.

        Run the command:

        vzctl set <ctid> --save --diskspace <soft_limit:hard_limit>

        If this fails to run an exception is raised because this command
        limits a container's ability to hijack all available disk space.
        """

        soft_limit = int(root_gb)
        hard_limit = int(soft_limit * CONF.ovz_disk_space_oversub_percent)

        # Now set the increment of the limit.  I do this here so that I don't
        # have to do this in every line above.
        soft_limit = '%s%s' % (soft_limit, CONF.ovz_disk_space_increment)
        hard_limit = '%s%s' % (hard_limit, CONF.ovz_disk_space_increment)

        ovz_utils.execute('vzctl', 'set', instance['id'], '--save',
                          '--diskspace', '%s:%s' % (soft_limit, hard_limit),
                          run_as_root=True)

    def _setup_networking(self, instance, network_info):
        """
        Create the vifs for the container's virtual networking.  This should
        only need to be run on instance spawn.

        :param instance:
        :param network_info:
        :return:
        """
        LOG.debug(_('network_info: %s') % network_info)
        interfaces = ovz_utils.generate_network_dict(instance['id'],
                                                     network_info)
        ifaces_fh = ovznetwork.OVZNetworkInterfaces(interfaces, network_info)
        ifaces_fh.add()

    def plug_vifs(self, instance, network_info):
        """
        Plug vifs into networks and configure network devices in the
        container.  This is necessary to make multi-nic go.
        """
        for vif in network_info:
            if vif.labeled_ips():
                self.vif_driver.plug(instance, vif)

    def reboot(self, context, instance, network_info, reboot_type,
               block_device_info=None, bad_volumes_callback=None):
        """Reboot the specified instance.

        After this is called successfully, the instance's state
        goes back to power_state.RUNNING. The virtualization
        platform should ensure that the reboot action has completed
        successfully even in cases in which the underlying domain/vm
        is paused or halted/stopped.

        :param instance: Instance object as returned by DB layer.
        :param network_info:
           :py:meth:`~nova.network.manager.NetworkManager.get_instance_nw_info`
        :param reboot_type: Either a HARD or SOFT reboot
        :param block_device_info: Info pertaining to attached volumes
        :param bad_volumes_callback: Function to handle any bad volumes
            encountered

        Run the command:

        vzctl restart <ctid>

        If this fails to run an exception is raised because the container
        given to this method will be in an inconsistent state.
        """
        # Run the TC rules
        sf = ovzshutdown.OVZShutdownFile(instance['id'], 700)
        with sf:
            sf.read()
            sf.run_contents()

        # Start by setting the powerstate to paused until we have successfully
        # restarted the instance.
        self.virtapi.instance_update(
            context, instance['uuid'], {'power_state': power_state.PAUSED})
        ovz_utils.execute('vzctl', 'restart', instance['id'], run_as_root=True)

        def _wait_for_reboot():
            try:
                state = self.get_info(instance)['state']
            except exception.InstanceNotFound:
                self.virtapi.instance_update(
                    context, instance['uuid'],
                    {'power_state': power_state.NOSTATE})
                LOG.error(_('During reboot %s disappeared') % instance['name'])
                raise loopingcall.LoopingCallDone

            if state == power_state.RUNNING:
                self.virtapi.instance_update(
                    context, instance['uuid'],
                    {'power_state': power_state.RUNNING})
                LOG.info(_('Instance %s rebooted') % instance['name'])
                # Run the TC rules
                bf = ovzboot.OVZBootFile(instance['id'], 700)
                with bf:
                    bf.read()
                    bf.run_contents()
                raise loopingcall.LoopingCallDone
            elif state == power_state.NOSTATE:
                LOG.error(_('Error rebooting %s') % instance['name'])
                raise loopingcall.LoopingCallDone

        timer = loopingcall.FixedIntervalLoopingCall(_wait_for_reboot)
        return timer.start(interval=0.5)

    def _inject_files(self, instance, files_to_inject):
        """
        Files to inject into instance.

        :param instance: instance ref of guest to receive injected files
        :param files_to_inject: List of files to inject formatted as
                                [['filename', 'file_contents']] only strings
                                are accepted.
        """
        LOG.debug(
            _('Files to inject into %(instance_id)s: %(files_to_inject)s') %
            {'instance_id': instance['id'],
             'files_to_inject': files_to_inject})
        for file_to_inject in files_to_inject:
            LOG.debug(_('Injecting file: %s') % file_to_inject[0])
            self.inject_file(instance,
                             base64.b64encode(file_to_inject[0]),
                             base64.b64encode(file_to_inject[1]))

    def inject_file(self, instance, b64_path, b64_contents):
        """
        Writes a file on the specified instance.

        The first parameter is an instance of nova.compute.service.Instance,
        and so the instance is being specified as instance.name. The second
        parameter is the base64-encoded path to which the file is to be
        written on the instance; the third is the contents of the file, also
        base64-encoded.
        """
        path = base64.b64decode(b64_path)
        LOG.debug(_('Injecting file: %s') % path)
        file_path = '%s/%s/%s' % (
            CONF.ovz_ve_private_dir, instance['id'], path)
        LOG.debug(_('New file path: %s') % file_path)
        fh = ovzfile.OVZFile(file_path, 644)
        with fh:
            fh.append(base64.b64decode(b64_contents))
            fh.write()

    def set_admin_password(self, context, instance_id, new_pass=None):
        """
        Set the root password on the specified instance.

        The first parameter is an instance of nova.compute.service.Instance,
        and so the instance is being specified as instance.name. The second
        parameter is the value of the new password.

        The work will be done asynchronously.  This function returns a
        task that allows the caller to detect when it is complete.

        Run the command:

        vzctl exec2 <instance_id> echo <user>:<password> | chpasswd

        If this fails to run an error is logged.
        """

        user_pass_map = 'root:%s' % new_pass

        ovz_utils.execute('vzctl', 'exec2', instance_id, 'echo',
                          user_pass_map, '|', 'chpasswd', run_as_root=True)

    def pause(self, instance):
        """
        Pause the specified instance.
        """
        self._stop(instance)

    def unpause(self, instance):
        """
        Unpause the specified instance.
        """
        self._start(instance)

    def suspend(self, instance):
        """
        suspend the specified instance
        """
        # grab an admin context to update the database
        admin_context = context.get_admin_context()

        # Suspend the instance
        ovz_utils.execute('vzctl', 'chkpnt', instance['id'],
                          '--suspend', run_as_root=True)

        # Set the instance power state to suspended for accurate reporting
        try:
            self.virtapi.instance_update(
                admin_context, instance['uuid'],
                {'power_state': power_state.SUSPENDED})
        except exception.InstanceNotFound as err:
            LOG.error(_('Instance %s not found in the database') %
                      instance['id'])
            LOG.error(err)

    def resume(self, instance, network_info, block_device_info=None):
        """
        resume the specified instance
        """
        # grab an admin context to update the database
        admin_context = context.get_admin_context()

        # Resume the instance
        ovz_utils.execute('vzctl', 'chkpnt', instance['id'],
                          '--resume', run_as_root=True)

        # Set the instance power state to running
        try:
            self.virtapi.instance_update(
                admin_context, instance['uuid'],
                {'power_state': power_state.RUNNING})
        except exception.InstanceNotFound as err:
            LOG.error(_('Instance %s not found in the database') %
                      instance['id'])
            LOG.error(err)

    def _clean_orphaned_files(self, instance_id):
        """
        When openvz deletes a container it leaves behind orphaned config
        files in /etc/vz/conf with the .destroyed extension.  We want these
        gone when we destroy a container.

        This runs a command that looks like this:

        rm -f /etc/vz/conf/<CTID>.conf.destroyed

        It this fails to execute no exception is raised but an log error
        event is triggered.
        """
        # first assemble a list of files that need to be cleaned up, then
        # do the deed.
        for filename in os.listdir(CONF.ovz_config_dir):
            if fnmatch.fnmatch(filename, '%s.*' % instance_id):
                # minor protection for /
                if CONF.ovz_config_dir == '/':
                    raise exception.InvalidDevicePath(
                        _('I refuse to operate on /'))

                filename = '%s/%s' % (CONF.ovz_config_dir, filename)
                LOG.debug(_('Deleting file: %s') % filename)
                ovz_utils.execute(
                    'rm', '-f', filename, run_as_root=True,
                    raise_on_error=False)

    def destroy(self, context, instance, network_info, block_device_info=None,
                destroy_disks=True):
        """
        Destroy (shutdown and delete) the specified instance.

        Run the command:

        vzctl destroy <ctid>

        If this does not run successfully then an exception is raised.  This is
        because a failure to destroy would leave the database and container
        in a disparate state.
        """
        # If a revert_resize is called in the compute manager we hit a case
        # where an in-flight resize on the same host deletes the instance from
        # disk.  Before any delete operations are allowed first check to be
        # sure that there is not currently a resize happening.
        #
        # NOTE(imsplitbit): There is an edge case here where an in-flight
        # resize is taking place and a user issues a destroy via the api
        # and this will result in a bad state.  We need a better solution to
        # allow in place resizes.
        meta = ovz_utils.read_instance_metadata(instance['id'])
        migration_type = meta.get('migration_type')

        if migration_type == 'resize_in_place':
            # This is a resize on the same host.  The compute manager calls
            # destroy on the source before calling revert_resize on the driver.
            # Since there is an in-flight resize we'll exit here.
            return

        # cleanup the instance metadata since this is application specific
        # it's safe to just delete all of it because if it's there we put
        # it there.
        if ovz_utils.remove_instance_metadata(instance['id']):
            LOG.debug(_('Removed metadata for instance %s') % instance['id'])
        else:
            LOG.debug(_('Problem removing metadata for instance %s') %
                      instance['id'])

        # remove all attached volumes
        if block_device_info:
            self._detach_volumes(
                instance, block_device_info)

        timer = loopingcall.FixedIntervalLoopingCall()

        def _wait_for_destroy():
            try:
                LOG.debug(_('Beginning _wait_for_destroy'))
                state = self.get_info(instance)['state']
                LOG.debug(_('State is %s') % state)

                if state is power_state.RUNNING:
                    LOG.debug(_('Ve is running, stopping now.'))
                    self._stop(instance)
                    LOG.debug(_('Ve stopped'))

                LOG.debug(_('Attempting to destroy container'))
                self._destroy(instance['id'])
            except exception.InstanceUnacceptable as err:
                LOG.error(_('There was an error with the destroy process'))
                LOG.error(_('Error from ovz_utils: %s') % err)
                timer.stop()
                LOG.debug(_('Timer stopped for _wait_for_destroy'))
                raise exception.InstanceTerminationFailure(
                    _('Error running vzctl destroy'))
            except exception.InstanceNotFound:
                LOG.debug(_('Container not found, destroyed?'))
                timer.stop()
                LOG.debug(_('Timer stopped for _wait_for_destroy'))

        LOG.debug(_('Making timer'))
        timer.f = _wait_for_destroy
        LOG.debug(_('Starting timer'))

        running_delete = timer.start(interval=0.5)
        LOG.debug(_('Waiting for timer'))
        running_delete.wait()
        LOG.debug(_('Timer finished'))

        for vif in network_info:
            LOG.debug('Unplugging vifs')
            self.vif_driver.unplug(instance, vif)

        self._clean_orphaned_files(instance['id'])

    def _destroy(self, instance_id):
        """
        Run destroy on the instance
        """
        ovz_utils.execute('vzctl', 'destroy', instance_id, run_as_root=True)

    def _attach_volumes(self, instance, block_device_mapping):
        """
        Iterate through all volumes and attach them all.  This is just a helper
        method for self.spawn so that all volumes in the db get added to a
        container before it gets started.

        This will only attach volumes that have a filesystem uuid.  This is
        a limitation that is currently imposed by nova not storing the device
        name in the volumes table so we have no point of reference for which
        device goes where.
        """
        for volume in block_device_mapping['block_device_mapping']:
            self.attach_volume(volume['connection_info'],
                               instance,
                               volume['mount_device'])

    def attach_volume(self, connection_info, instance, mountpoint):
        """
        Attach the disk at device_path to the instance at mountpoint.  For
        volumes being attached to OpenVz we require a filesystem be created
        already.
        """
        if connection_info['driver_volume_type'] == 'iscsi':
            volume = ovziscsi.OVZISCSIStorageDriver(
                instance['id'], mountpoint, connection_info)
            volume.discover_volume()
        else:
            raise NotImplementedError(
                _('There are no suitable storage drivers'))

        volume.attach()

        # Save volume info to the container's storage info store. This is
        # just a precaution and stores the volume information necessary
        # to manually re-establish communication should nova services
        # go away.
        ext_str = ext_storage.OVZExtStorage(instance['id'])
        ext_str.add_volume(mountpoint, connection_info)
        ext_str.save()

    def _disconnect_volume(self, connection_info, instance, mountpoint,
                           container_is_running=True):
        """
        Necessary for migrations to disconnect but not permanently remove
        volumes.

        :param connection_info:
        :param instance:
        :param mountpoint:
        :return:
        """
        if connection_info['driver_volume_type'] == 'iscsi':
            volume = ovziscsi.OVZISCSIStorageDriver(
                instance['id'], mountpoint, connection_info)
        else:
            raise NotImplementedError(
                _('There are no suitable storage drivers'))

        volume.detach(container_is_running)

    def _detach_volumes(self, instance, block_device_mapping,
                        disconnect_only=False, container_is_running=True):
        """
        Move bulk operations of volume connections back into the driver as
        they are relevant here and no longer need to be in their own module.
        :param instance:
        :param block_device_mapping:
        :return: None
        """
        for volume in block_device_mapping['block_device_mapping']:
            if disconnect_only:
                self._disconnect_volume(volume['connection_info'], instance,
                                        volume['mount_device'],
                                        container_is_running)
            else:
                self.detach_volume(
                    volume['connection_info'], instance,
                    volume['mount_device'])

    def detach_volume(self, connection_info, instance, mountpoint=None):
        """
        Detach the disk attached to the instance at mountpoint
        """
        # Create a default mountpoint if none exists
        if not mountpoint:
            mountpoint = connection_info['mount_device']

        self._disconnect_volume(connection_info, instance, mountpoint)

        # Remove storage connection info from the storage repo for the
        # instance.
        ext_str = ext_storage.OVZExtStorage(instance['id'])
        ext_str.remove_volume(mountpoint)
        ext_str.save()

    def get_info(self, instance):
        """
        Get a block of information about the given instance.  This is returned
        as a dictionary containing 'state': The power_state of the instance,
        'max_mem': The maximum memory for the instance, in KiB, 'mem': The
        current memory the instance has, in KiB, 'num_cpu': The current number
        of virtual CPUs the instance has, 'cpu_time': The total CPU time used
        by the instance, in nanoseconds.

        This method should raise exception.InstanceNotFound if the hypervisor
        has no knowledge of the instance
        """
        try:
            meta = self._find_by_name(instance['name'])
            LOG.debug(_('Get_info meta: %s') % meta)
        except exception.InstanceNotFound:
            LOG.error(_('Instance %s Not Found') % instance['name'])
            raise exception.InstanceNotFound(
                'Instance %s Not Found' % instance['name'])

        # Store the assumed state as the default
        # Coerced into an INT because it comes from SQLAlchemy as a string
        state = int(instance['power_state'])

        LOG.debug(_('Instance %(id)s is in state %(power_state)s') %
                  {'id': instance['id'], 'power_state': state})

        # NOTE(imsplitbit): This is not ideal but it looks like nova uses
        # codes returned from libvirt and xen which don't correlate to
        # the status returned from OpenVZ which is either 'running' or
        # 'stopped'.  There is some contention on how to handle systems
        # that were shutdown intentially however I am defaulting to the
        # nova expected behavior.
        if meta['state'] == 'running':
            new_state = power_state.RUNNING
        elif meta['state'] is None or meta['state'] == '-':
            new_state = power_state.NOSTATE
        else:
            new_state = power_state.SHUTDOWN

        if state != new_state:
            state = new_state

        LOG.debug(
            _('OpenVz says instance %(id)s is in state %(state)s') %
            {'id': instance['id'], 'state': state})

        # TODO(imsplitbit): Need to add all metrics to this dict.
        return {'state': state,
                'max_mem': 0,
                'mem': 0,
                'num_cpu': 0,
                'cpu_time': 0}

    def _calc_pages(self, instance_memory, block_size=4096):
        """
        Returns the number of pages for a given size of storage/memory
        """
        return ((int(instance_memory) * 1024) * 1024) / block_size

    def _percent_of_resource(self, instance_memory):
        """
        In order to evenly distribute resources this method will calculate a
        multiplier based on memory consumption for the allocated container and
        the overall host memory. This can then be applied to the cpuunits in
        self.utility to be passed as an argument to the self._set_cpuunits
        method to limit cpu usage of the container to an accurate percentage of
        the host.  This is only done on self.spawn so that later, should
        someone choose to do so, they can adjust the container's cpu usage
        up or down.
        """
        cont_mem_mb = (
            float(instance_memory) / float(ovz_utils.get_memory_mb_total()))

        # We shouldn't ever have more than 100% but if for some unforseen
        # reason we do, lets limit it to 1 to make all of the other
        # calculations come out clean.
        if cont_mem_mb > 1:
            LOG.error(_('_percent_of_resource came up with more than 100%'))
            return 1.0
        else:
            return cont_mem_mb

    def _get_cpulimit(self):
        """
        Fetch the total possible cpu processing limit in percentage to be
        divided up across all containers.  This is expressed in percentage
        being added up by logical processor.  If there are 24 logical
        processors then the total cpulimit for the host node will be
        2400.
        """
        self.utility['CPULIMIT'] = ovz_utils.get_vcpu_total() * 100
        LOG.debug(_('Updated cpulimit in utility'))
        LOG.debug(
            _('Current cpulimit in utility: %s') % self.utility['CPULIMIT'])

    def _get_cpuunits_usage(self):
        """
        Use openvz tools to discover the total used processing power. This is
        done using the vzcpucheck -v command.

        Run the command:

        vzcpucheck -v

        If this fails to run an exception should not be raised as this is a
        soft error and results only in the lack of knowledge of what the
        current cpuunit usage of each container.
        """
        out = ovz_utils.execute(
            'vzcpucheck', '-v', run_as_root=True, raise_on_error=False)
        if out:
            for line in out.splitlines():
                line = line.split()
                if len(line) > 0:
                    if line[0].isdigit():
                        LOG.debug(_('Usage for CTID %(id)s: %(usage)s') %
                                  {'id': line[0], 'usage': line[1]})
                        if int(line[0]) not in self.utility.keys():
                            self.utility[int(line[0])] = dict()
                        self.utility[int(line[0])] = int(line[1])

    def get_available_resource(self, nodename):
        """Retrieve resource info.

        This method is called when nova-compute launches, and
        as part of a periodic task.

        :returns: dictionary describing resources

        """
        return self.get_host_stats(refresh=True)

    def get_volume_connector(self, instance):
        if not self._initiator:
            self._initiator = ovz_utils.get_iscsi_initiator()
            if not self._initiator:
                LOG.warn(_('Could not determine iscsi initiator name'),
                         instance=instance)
        return {
            'ip': CONF.my_ip,
            'initiator': self._initiator,
            'host': CONF.host
        }

    def migrate_disk_and_power_off(self, context, instance, dest,
                                   instance_type, network_info,
                                   block_device_info=None):
        """
        Transfers the disk of a running instance in multiple phases, turning
        off the instance before the end.
        """
        LOG.debug(_('Migration context: %s') % context)
        LOG.debug(_('Migration instance: %s') % instance)
        LOG.debug(_('Migration dest: %s') % dest)
        LOG.debug(_('Migration instance_type: %s') % instance_type)
        LOG.debug(_('Migration network_info: %s') % network_info)

        if not dest:
            LOG.error(_('No destination given to migration'))
            raise exception.MigrationError(
                _('Migration destination is: %s') % dest)

        if dest == CONF.my_ip:
            # if this is an inplace resize we don't need to do any of this
            LOG.debug(_('This is an inplace migration'))
            instance.system_metadata['migration_type'] = 'resize_in_place'
            instance.save()
            return

        # Validate the ovz_migration_method flag
        if CONF.ovz_migration_method not in ['vzmigrate', 'python']:
            raise exception.MigrationError(
                _('I do not understand your migration method'))

        # Find out if we have external volumes, this will determine
        # if we will freeze the instance and attempt to preserve state of if
        # we will stop the instance completely to preserve the integrity
        # of the attached filesystems.
        if block_device_info:
            live_migration = False
            self._stop(instance)
            self._detach_volumes(
                instance, block_device_info, True, live_migration)
        else:
            live_migration = True
            self.suspend(instance)

        LOG.debug(_('ovz_migration_method is: %s') %
                  CONF.ovz_migration_method)
        if CONF.ovz_migration_method == 'vzmigrate':
            self._vzmigration_send_to_host(instance, dest)
        elif CONF.ovz_migration_method == 'python':
            self._pymigration_send_to_host(
                instance, ovz_utils.generate_network_dict(instance['id'],
                                                          network_info),
                block_device_info, dest, live_migration)

    def _pymigration_send_to_host(self, instance, network_info,
                                  block_device_info, dest, live_migration):
        """
        This performs a more complex but more secure migration using a pure
        python implemented vz migration driver.
        """
        LOG.debug(_('Beginning pure python based migration'))
        mobj = ovz_migration.OVZMigration(
            instance, network_info, block_device_info, dest, live_migration)
        mobj.dump_and_transfer_instance()
        mobj.send()

    def _vzmigration_send_to_host(self, instance, dest):
        """
        This performs a simple migration using openvz's supplied vzmigrate
        script.  It requires shared keys for root across all hosts and
        does not support containers with externally attached volumes. And
        currently TC rules aren't preserved.
        """
        LOG.debug(_('Beginning vzmigrate based migration'))
        cmd = ['vzmigrate']
        if CONF.ovz_vzmigrate_opts:
            if isinstance(CONF.ovz_vzmigrate_opts, str):
                cmd += CONF.ovz_vzmigrate_opts.split()
            elif isinstance(CONF.ovz_vzmigrate_opts, list):
                cmd += CONF.ovz_vzmigrate_opts
        if CONF.ovz_vzmigrate_online_migration:
            cmd.append('--online')
        if CONF.ovz_vzmigrate_destroy_source_container_on_migrate:
            cmd += ['-r', 'yes']
        if CONF.ovz_vzmigrate_verbose_migration_logging:
            cmd.append('-v')
        cmd.append(dest)
        cmd.append(instance['id'])
        LOG.debug(
            _('Beginning the migration of %(instance_id)s to %(dest)s') %
            {'instance_id': instance['id'], 'dest': dest})
        out = ovz_utils.execute(*cmd, run_as_root=True)
        LOG.debug(_('Output from migration process: %s') % out)

    def finish_migration(self, context, migration, instance, disk_info,
                         network_info, image_meta, resize_instance,
                         block_device_info=None, power_on=True):
        """Completes a resize, turning on the migrated instance

        :param network_info:
           :py:meth:`~nova.network.manager.NetworkManager.get_instance_nw_info`
        :param image_meta: image object returned by nova.image.glance that
                           defines the image from which this instance
                           was created
        """
        # Get the instance metadata to see what we need to do
        meta = ovz_utils.read_instance_metadata(instance['id'])
        migration_type = meta.get('migration_type')

        if migration_type == 'resize_in_place':
            # This is a resize on the same host so its simple, resize
            # in place and then exit the method
            self._set_instance_size(instance, network_info, False)
            return

        if block_device_info:
            # It is assumed that if there are externally attached volumes
            # then this is not a live migration.
            live_migration = False
        else:
            live_migration = True

        if CONF.ovz_migration_method == 'vzmigrate':
            self._vzmigrate_setup_dest_host(instance, network_info)
        elif CONF.ovz_migration_method == 'python':
            self._pymigrate_finish_migration(instance,
                                             network_info,
                                             live_migration)

        if block_device_info:
            # Once the files have been moved into place we need to attach
            # volumes.
            self._attach_volumes(instance, block_device_info)

        # Somehow the name of the instance is lost in migration so
        # set it here.
        self._set_name(instance)

        # The uuid is lost from the description field in the migration
        # so set it here.
        self._set_description(instance)

        if resize_instance:
            LOG.debug(_('A resize after migration was requested: %s') %
                      instance['id'])
            self._set_instance_size(instance, network_info, True)
            LOG.debug(_('Resized instance after migration: %s') %
                      instance['id'])
        else:
            LOG.debug(_('Regenerating TC rules for instance %s') %
                      instance['id'])
            self._generate_tc_rules(instance, network_info, True)
            LOG.debug(_('Regenerated TC rules for instance %s') %
                      instance['id'])

        if not live_migration:
            self._start(instance)

    def _pymigrate_finish_migration(self, instance, network_info,
                                    live_migration):
        """
        Take all transferred files and put them back into place to create a
        working instance.
        """
        LOG.debug(_('Beginning python based finish_migration'))
        interfaces = ovz_utils.generate_network_dict(instance['id'],
                                                     network_info)
        mobj = ovz_migration.OVZMigration(
            instance, interfaces, None, live_migration)
        mobj.undump_instance()

        # Crude but we just need to give things time to settle before cleaning
        # up all the dumped stuff
        # TODO(imsplitbit): maybe a wait_for_start method with a looping
        # timer is better here, will check into it soon
        time.sleep(5)
        mobj.cleanup_destination()
        LOG.debug(_('Finished python based finish_migration'))

    def _vzmigrate_setup_dest_host(self, instance, network_info):
        """
        Sequence to run on destination host should the migration be done
        by the vzmigrate tools.
        """
        LOG.debug(_('Stopping instance: %s') % instance['id'])
        self._stop(instance)
        LOG.debug(_('Stopped instance: %s') % instance['id'])

        self.plug_vifs(instance, network_info)

        LOG.debug(_('Starting instance: %s') % instance['id'])
        self._start(instance)
        LOG.debug(_('Started instance: %s') % instance['id'])

    def confirm_migration(self, migration, instance, network_info):
        """
        Run on the source host to confirm the migration and cleans up the
        the files from the source host.
        """
        LOG.debug(_('Beginning confirm migration for %s') % instance['id'])

        # Get the instance metadata to see what we need to do
        meta = ovz_utils.read_instance_metadata(instance['id'])
        migration_type = meta.get('migration_type')

        live_migration = True
        ext_str = ext_storage.OVZExtStorage(instance['id'])
        if ext_str._volumes:
            live_migration = False

        if migration_type == 'resize_in_place':
            # This is a resize on the same host so its simple, resize
            # in place and then exit the method
            if ovz_utils.remove_instance_metadata_key(instance['id'],
                                                      'migration_type'):
                LOG.debug(_('Removed migration_type metadata'))
            else:
                LOG.debug(_('Failed to remove migration_type metadata'))
            return

        try:
            status = self.get_info(instance)['state']
            LOG.debug(_('State in confirm_migration: %s') % status)
            if status == power_state.RUNNING:
                LOG.warn(
                    _('Instance %s is running on source after migration') %
                    instance['uuid'])
                self._stop(instance)
                status = self.get_info(instance)['state']

            if status == power_state.SHUTDOWN:
                LOG.debug(_('Cleaning up migration on source host'))
                mobj = ovz_migration.OVZMigration(
                    instance, ovz_utils.generate_network_dict(
                        instance['id'], network_info), None, live_migration)
                mobj.cleanup_source()
                self._destroy(instance['id'])
                self._clean_orphaned_files(instance['id'])
            else:
                LOG.warn(
                    _('Check instance: %(instance_id)s, it may be broken. '
                        'power_state: %(ps)s') %
                    {'instance_id': instance['id'],
                     'ps': status})
        except exception.InstanceNotFound:
            LOG.warn(
                _('Instance %s not found, migration cleaned itself up?') %
                instance['id'])
        except exception.InstanceUnacceptable:
            LOG.error(_('Failed to stop and destroy the instance'))
        LOG.debug(_('Finished confirm migration for %s') % instance['id'])

    def finish_revert_migration(self, context, instance, network_info,
                                block_device_info=None, power_on=True):
        """Finish reverting a resize, powering back on the instance."""
        # Get the instance metadata to see what we need to do
        LOG.debug(_('Beginning finish_revert_migration'))
        meta = ovz_utils.read_instance_metadata(instance['id'])
        migration_type = meta.get('migration_type')

        if migration_type == 'resize_in_place':
            # This is a resize on the same host so its simple, resize
            # in place and then exit the method
            LOG.debug(_('Reverting in-place migration for %s') %
                      instance['id'])
            self._set_instance_size(instance, network_info)
            if ovz_utils.remove_instance_metadata_key(instance['id'],
                                                      'migration_type'):
                LOG.debug(_('Removed migration_type metadata'))
                LOG.debug(_('Done reverting in-place migration for %s') %
                          instance['id'])
            else:
                LOG.debug(_('Failed to remove migration_type metadata'))
            return

        if block_device_info:
            LOG.debug(_('Instance %s has volumes') % instance['id'])
            # the instance has external volumes and was not a live migration
            # so we need to reattach external volumes
            live_migration = False
            LOG.debug(_('Starting instance %s, after revert') % instance['id'])
            ext_str = ext_storage.OVZExtStorage(instance['id'])

            for mountpoint, connection_info in ext_str.volumes():
                self.attach_volume(connection_info, instance, mountpoint)

            self._start(instance)
        else:
            LOG.debug(_('Instance %s has no volumes') % instance['id'])
            live_migration = True
            LOG.debug(_('Resuming live migration for %s') % instance['id'])
            self.resume(instance, network_info)

        mobj = ovz_migration.OVZMigration(
            instance, ovz_utils.generate_network_dict(
                instance['id'], network_info), None, live_migration)
        mobj.cleanup_files()

    def get_host_ip_addr(self):
        """
        Retrieves the IP address of the host
        """
        return CONF.my_ip

    # TODO(imsplitbit): finish the outstanding software contract with nova
    # All methods in the driver below this need to be worked out.
    def snapshot(self, context, instance, image_id, update_task_state):
        """
        Snapshots the specified instance.

        The given parameter is an instance of nova.compute.service.Instance,
        and so the instance is being specified as instance.name.

        The second parameter is the name of the snapshot.

        The work will be done asynchronously.  This function returns a
        task that allows the caller to detect when it is complete.
        """
        # TODO(imsplitbit): Need to implement vzdump
        pass

    def rescue(self, context, instance, network_info, image_meta,
               rescue_password):
        """
        Rescue the specified instance.
        """
        pass

    def unrescue(self, instance, network_info):
        """
        Unrescue the specified instance.
        """
        pass

    def get_diagnostics(self, instance_name):
        pass

    def list_disks(self, instance_name):
        """
        Return the IDs of all the virtual disks attached to the specified
        instance, as a list.  These IDs are opaque to the caller (they are
        only useful for giving back to this layer as a parameter to
        disk_stats).  These IDs only need to be unique for a given instance.

        Note that this function takes an instance ID, not a
        compute.service.Instance, so that it can be called by compute.monitor.
        """
        return ['A_DISK']

    def list_interfaces(self, instance_name):
        """
        Return the IDs of all the virtual network interfaces attached to the
        specified instance, as a list.  These IDs are opaque to the caller
        (they are only useful for giving back to this layer as a parameter to
        interface_stats).  These IDs only need to be unique for a given
        instance.

        Note that this function takes an instance ID, not a
        compute.service.Instance, so that it can be called by compute.monitor.
        """
        return ['A_VIF']

    def block_stats(self, instance_name, disk_id):
        """
        Return performance counters associated with the given disk_id on the
        given instance_name.  These are returned as [rd_req, rd_bytes, wr_req,
        wr_bytes, errs], where rd indicates read, wr indicates write, req is
        the total number of I/O requests made, bytes is the total number of
        bytes transferred, and errs is the number of requests held up due to a
        full pipeline.

        All counters are long integers.

        This method is optional.  On some platforms (e.g. XenAPI) performance
        statistics can be retrieved directly in aggregate form, without Nova
        having to do the aggregation.  On those platforms, this method is
        unused.

        Note that this function takes an instance ID, not a
        compute.service.Instance, so that it can be called by compute.monitor.
        """
        return [0L, 0L, 0L, 0L, None]

    def interface_stats(self, instance_name, iface_id):
        """
        Return performance counters associated with the given iface_id on the
        given instance_id.  These are returned as [rx_bytes, rx_packets,
        rx_errs, rx_drop, tx_bytes, tx_packets, tx_errs, tx_drop], where rx
        indicates receive, tx indicates transmit, bytes and packets indicate
        the total number of bytes or packets transferred, and errs and dropped
        is the total number of packets failed / dropped.

        All counters are long integers.

        This method is optional.  On some platforms (e.g. XenAPI) performance
        statistics can be retrieved directly in aggregate form, without Nova
        having to do the aggregation.  On those platforms, this method is
        unused.

        Note that this function takes an instance ID, not a
        compute.service.Instance, so that it can be called by compute.monitor.
        """
        return [0L, 0L, 0L, 0L, 0L, 0L, 0L, 0L]

    def get_console_output(self, instance):
        return 'FAKE CONSOLE OUTPUT'

    def get_ajax_console(self, instance):
        return 'http://fakeajaxconsole.com/?token=FAKETOKEN'

    def get_console_pool_info(self, console_type):
        return {'address': '127.0.0.1', 'username': 'fakeuser',
                'password': 'fakepassword'}

    def refresh_security_group_rules(self, security_group_id):
        """This method is called after a change to security groups.

        All security groups and their associated rules live in the datastore,
        and calling this method should apply the updated rules to instances
        running the specified security group.

        An error should be raised if the operation cannot complete.

        """
        return True

    def refresh_security_group_members(self, security_group_id):
        """This method is called when a security group is added to an instance.

        This message is sent to the virtualization drivers on hosts that are
        running an instance that belongs to a security group that has a rule
        that references the security group identified by `security_group_id`.
        It is the responsiblity of this method to make sure any rules
        that authorize traffic flow with members of the security group are
        updated and any new members can communicate, and any removed members
        cannot.

        Scenario:
            * we are running on host 'H0' and we have an instance 'i-0'.
            * instance 'i-0' is a member of security group 'speaks-b'
            * group 'speaks-b' has an ingress rule that authorizes group 'b'
            * another host 'H1' runs an instance 'i-1'
            * instance 'i-1' is a member of security group 'b'

            When 'i-1' launches or terminates we will recieve the message
            to update members of group 'b', at which time we will make
            any changes needed to the rules for instance 'i-0' to allow
            or deny traffic coming from 'i-1', depending on if it is being
            added or removed from the group.

        In this scenario, 'i-1' could just as easily have been running on our
        host 'H0' and this method would still have been called.  The point was
        that this method isn't called on the host where instances of that
        group are running (as is the case with
        :method:`refresh_security_group_rules`) but is called where references
        are made to authorizing those instances.

        An error should be raised if the operation cannot complete.

        """
        return True

    def poll_rebooting_instances(self, timeout, instances):
        """Poll for rebooting instances."""
        # TODO(Vek): Need to pass context in for access to auth_token
        return

    def poll_rescued_instances(self, timeout):
        """Poll for rescued instances."""
        # TODO(Vek): Need to pass context in for access to auth_token
        return

    def power_off(self, instance):
        """Power off the specified instance."""
        return

    def power_on(self, context, instance, network_info, block_device_info):
        """Power on the specified instance."""
        return

    def compare_cpu(self, cpu_info):
        """Compares given cpu info against host

        Before attempting to migrate a VM to this host,
        compare_cpu is called to ensure that the VM will
        actually run here.

        :param cpu_info: (str) JSON structure describing the source CPU.
        :returns: None if migration is acceptable
        :raises: :py:class:`~nova.exception.InvalidCPUInfo` if migration
                 is not acceptable.
        """
        return

    def poll_unconfirmed_resizes(self, resize_confirm_window):
        """Poll for unconfirmed resizes."""
        # TODO(Vek): Need to pass context in for access to auth_token
        return

    def host_power_action(self, host, action):
        """Reboots, shuts down or powers up the host."""
        return

    def set_host_enabled(self, host, enabled):
        """Sets the specified host's ability to accept new instances."""
        # TODO(Vek): Need to pass context in for access to auth_token
        return

    def ensure_filtering_rules_for_instance(self, instance_ref, network_info):
        """Setting up filtering rules and waiting for its completion.

        To migrate an instance, filtering rules to hypervisors
        and firewalls are inevitable on destination host.
        ( Waiting only for filtering rules to hypervisor,
        since filtering rules to firewall rules can be set faster).

        Concretely, the below method must be called.
        - setup_basic_filtering (for nova-basic, etc.)
        - prepare_instance_filter(for nova-instance-instance-xxx, etc.)

        to_xml may have to be called since it defines PROJNET, PROJMASK.
        but libvirt migrates those value through migrateToURI(),
        so , no need to be called.

        Don't use thread for this method since migration should
        not be started when setting-up filtering rules operations
        are not completed.

        :params instance_ref: nova.db.sqlalchemy.models.Instance object

        """
        # TODO(Vek): Need to pass context in for access to auth_token
        return

    def unfilter_instance(self, instance, network_info):
        """Stop filtering instance."""
        # TODO(Vek): Need to pass context in for access to auth_token
        return

    def refresh_provider_fw_rules(self):
        """This triggers a firewall update based on database changes.

        When this is called, rules have either been added or removed from the
        datastore.  You can retrieve rules with
        :method:`nova.db.provider_fw_rule_get_all`.

        Provider rules take precedence over security group rules.  If an IP
        would be allowed by a security group ingress rule, but blocked by
        a provider rule, then packets from the IP are dropped.  This includes
        intra-project traffic in the case of the allow_project_net_traffic
        flag for the libvirt-derived classes.

        """
        # TODO(Vek): Need to pass context in for access to auth_token
        return

    def agent_update(self, instance, url, md5hash):
        """
        Update agent on the specified instance.

        The first parameter is an instance of nova.compute.service.Instance,
        and so the instance is being specified as instance.name. The second
        parameter is the URL of the agent to be fetched and updated on the
        instance; the third is the md5 hash of the file for verification
        purposes.
        """
        # TODO(Vek): Need to pass context in for access to auth_token
        return

    def update_host_status(self):
        """Refresh host stats."""
        return

    def get_all_bw_usage(self, instances, start_time, stop_time=None):
        """Return bandwidth usage info for each interface on each
           running VM"""
        return []

    def snapshot_instance(self, context, instance_id, image_id):
        return

    # The method to add to aggregates is not yet implemented,
    # but the API needs to support it for the scheduler. We are
    # adding this return in here so the driver does not complain
    # when adding the aggregates to the API. The functionality of this
    # is to only set the operational_state back to active
    def add_to_aggregate(self, context, aggregate, host, **kwargs):
        #NOTE(imsplitbit): only used for Xen Pools
        return

    def resize(self, instance, flavor):
        """
        Resizes/Migrates the specified instance.

        The flavor parameter determines whether or not the instance RAM and
        disk space are modified, and if so, to what size.
        """
        return
