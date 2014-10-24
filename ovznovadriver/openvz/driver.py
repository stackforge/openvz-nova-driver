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
from ovznovadriver.openvz.container import OvzContainer
from ovznovadriver.openvz.container import OvzContainers
from ovznovadriver.openvz.file_ext import boot as ovzboot
from ovznovadriver.openvz.file_ext import ext_storage
from ovznovadriver.openvz.file_ext import shutdown as ovzshutdown
from ovznovadriver.openvz import migration as ovz_migration
from ovznovadriver.openvz import network as ovznetwork
from ovznovadriver.openvz.network_drivers import tc as ovztc
from ovznovadriver.openvz import utils as ovz_utils
from ovznovadriver.openvz.volume_drivers import iscsi as ovziscsi
from ovznovadriver.openvz.resources import VZResourceManager
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
    cfg.StrOpt('ovz_ve_conf_dir',
               default='/etc/vz/conf',
               help='Path where the VEs confs are'),
    cfg.StrOpt('ovz_lock_dir',
                default='/tmp/ovz_lock',
                help='Path where file locks are placed.'),
    cfg.StrOpt('ovz_image_template_dir',
               default='/var/lib/vz/template/cache',
               help='Path where OpenVZ images are'),
    cfg.StrOpt('ovz_config_dir',
               default='/etc/vz/conf',
               help='Where the OpenVZ configs are stored'),
    cfg.StrOpt('ovz_bridge_device',
               default='br100',
               help='Bridge device to map veth devices to'),
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
    cfg.BoolOpt('ovz_disk_space_oversub',
                default=True,
                help='Allow over subscription of local disk'),
    cfg.BoolOpt('ovz_use_veth_devs',
                default=True,
                help='Use veth devices rather than venet'),
    cfg.BoolOpt('ovz_use_dhcp',
                default=False,
                help='Use dhcp for network configuration'),
    cfg.BoolOpt('ovz_use_bind_mount',
                default=False,
                help='Use bind mounting instead of simfs'),
    cfg.IntOpt('ovz_system_num_tries',
               default=3,
               help='Number of attempts to make when '
                    'running a system command'),
    cfg.IntOpt('ovz_tc_id_max',
               default=9999,
               help='Max TC id to be used in generating a new id'),
    cfg.IntOpt('ovz_tc_mbit_per_unit',
               default=20,
               help='Mbit per unit bandwidth limit'),
    cfg.IntOpt('ovz_tc_max_line_speed',
               default=1000,
               help='Line speed in Mbit'),
    cfg.IntOpt('ovz_rsync_iterations',
               default=1,
               help='Number of times to rsync a container when migrating'),
    cfg.IntOpt('ovz_numtcpsock_default',
               default=2000,
               help='Default number of tcp sockets to give each container'),
    cfg.DictOpt('ovz_numtcpsock_map',
                default={"8192": 3000, "1024": 2000, "4096": 2000,
                         "2048": 2000, "16384": 4000, "512": 2000},
                help='Mapped values for flavors based on memory allocation'),
    cfg.StrOpt('ovz_layout',
                default='simfs',
                help='OVZ layout options to use during create.'),
]

CONF = cfg.CONF
CONF.register_opts(openvz_conn_opts)
CONF.import_opt('host', 'nova.config')

LOG = logging.getLogger('ovznovadriver.openvz.driver')


class OpenVzDriver(driver.ComputeDriver):

    def __init__(self, virtapi, read_only=False):
        """
        Create an instance of the openvz connection.
        """
        super(OpenVzDriver, self).__init__(virtapi)
        self.resource_manager = VZResourceManager(virtapi)
        self.host_stats = dict()
        self._initiator = None
        self.host = None
        self.read_only = read_only
        self.vif_driver = importutils.import_object(CONF.ovz_vif_driver)
        ovz_utils.execute('mkdir', '-p', CONF.ovz_lock_dir, run_as_root=True)
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
        self.resource_manager.get_cpulimit()
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
        ctids = list()
        containers = OvzContainers.list()
        for container in containers:
            ctids.append(container.ovz_id)

        return ctids

    def list_instance_uuids(self):
        """
        Return the UUIDS of all the instances known to the virtualization
        layer, as a list.
        """
        uuids = list()
        containers = OvzContainers.list()
        for container in containers:
            if container.uuid is not None:
                uuids.append(container.uuid)

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
        host_stats['memory_mb_used'] = OvzContainers.get_memory_mb_used()
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
        self.resource_manager.get_cpuunits_usage()

        # Go through the steps of creating a container
        # TODO(imsplitbit): Need to add conditionals around this stuff to make
        # it more durable during failure. And roll back changes made leading
        # up to the error.
        self._cache_image(context, instance)
        container = OvzContainer.create(
            image=instance['image_ref'],
            uuid=instance['uuid'],
            name=instance['name'],
            nova_id=instance['id'],
        )

        # TODO(jcru) change ostemplate='ubuntu' to config
        container.set_vz_os_hint(ostemplate='ubuntu')
        # A config file may be applied here but it may be
        # config parameters may be overwritten afterwards
        self.resource_manager.apply_config(context, container,
            instance['instance_type_id'])

        # instance.system_metadata will be saved by nova.compute.manager
        # after this method returns (driver.spawn)
        # ovz_utils.save_instance_metadata does not work because when this
        # method returns nova.compute.manager calls instance.save and any
        # info that was not updated through instance will be reverted
        instance.system_metadata['ovz_id'] = container.ovz_id

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
            self._setup_networking(container, network_info)

        # TODO(jimbobhickville) - move this stuff to OvzContainer
        self._set_hostname(container, hostname=instance['hostname'])
        self.resource_manager.configure_container_resources(context, container,
            instance['instance_type_id'])
        self._set_onboot(container)

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

    def _set_onboot(self, container):
        """
        Method to set the onboot status of the instance. This is done
        so that openvz does not handle booting, and instead the compute
        manager can handle initialization.

        I run the command:

        vzctl set <ctid> --onboot no --save

        If I fail to run an exception is raised.
        """
        ovz_utils.execute('vzctl', 'set', container.ovz_id, '--onboot', 'no',
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
        container = OvzContainer.find(uuid=instance['uuid'])
        ovz_utils.execute('vzctl', 'start', container.ovz_id, run_as_root=True)

        # Set instance state as RUNNING
        self.virtapi.instance_update(
            context.get_admin_context(), instance['uuid'],
            {'power_state': power_state.RUNNING})

        bf = ovzboot.OVZBootFile(container.ovz_id, 700)
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
        container = OvzContainer.find(uuid=instance['uuid'])
        sf = ovzshutdown.OVZShutdownFile(container.ovz_id, 700)
        with sf:
            sf.read()
            sf.run_contents()

        ovz_utils.execute('vzctl', 'stop', container.ovz_id, run_as_root=True)

        # Update instance state
        self.virtapi.instance_update(
            context.get_admin_context(), instance['uuid'],
            {'power_state': power_state.SHUTDOWN})

    def _set_hostname(self, container, hostname):
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
        ovz_utils.execute('vzctl', 'set', container.ovz_id, '--save',
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
        container = OvzContainer.find(nova_id=instance_id)
        ovz_utils.execute('vzctl', 'exec2', container.ovz_id, 'arping', '-q',
                          '-c', '5', '-A', '-I', interface, ip_address,
                          run_as_root=True, raise_on_error=False)

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

    def _setup_networking(self, container, network_info):
        """
        Create the vifs for the container's virtual networking.  This should
        only need to be run on instance spawn.

        :param instance:
        :param network_info:
        :return:
        """
        LOG.debug(_('network_info: %s') % network_info)
        interfaces = ovz_utils.generate_network_dict(container,
                                                     network_info)
        ifaces_fh = ovznetwork.OVZNetworkInterfaces(container, interfaces,
                                                    network_info)
        ifaces_fh.add()

    def plug_vifs(self, instance, network_info):
        """
        Plug vifs into networks and configure network devices in the
        container.  This is necessary to make multi-nic go.
        """
        for vif in network_info:
            if vif.labeled_ips():
                self.vif_driver.plug(instance, vif)

    # TODO(pdmars): get_blockdev and rescan_volume are added to expose this
    # information to nova-compute for online volume extending. They are
    # required because after Cinder extends the volume, it needs nova-compute
    # to run rescan on the compute host and then it needs to query the size
    # of the block device until it reports the new size. Currently, the
    # extensions to Cinder and Nova for online extending are in progress and
    # haven't been merged into the public yet.
    def get_blockdev(self, instance, connection_info):
        volume = ovziscsi.OVZISCSIStorageDriver(instance['id'],
                                                None,
                                                connection_info)
        device_name = volume.device_name()
        return volume.get_blockdev(device_name)

    def rescan_volume(self, instance, connection_info):
        volume = ovziscsi.OVZISCSIStorageDriver(instance['id'],
                                                None,
                                                connection_info)
        volume.rescan()

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
        container = OvzContainer.find(uuid=instance['uuid'])
        sf = ovzshutdown.OVZShutdownFile(container.ovz_id, 700)
        with sf:
            sf.read()
            sf.run_contents()

        # Start by setting the powerstate to paused until we have successfully
        # restarted the instance.
        self.virtapi.instance_update(
            context, instance['uuid'], {'power_state': power_state.PAUSED})
        ovz_utils.execute('vzctl', 'restart', container.ovz_id,
                          run_as_root=True)

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
                bf = ovzboot.OVZBootFile(container.ovz_id, 700)
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
        for file_to_inject in files_to_inject:
            LOG.info(
                _('Injecting file: %(instance_id)s: %(file)s') %
                {'instance_id': instance['id'], 'file': file_to_inject[0]})
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
        container = OvzContainer.find(uuid=instance['uuid'])
        path = base64.b64decode(b64_path)
        LOG.debug(_('Injecting file: %s') % path)
        file_path = '%s/%s/%s' % (
            CONF.ovz_ve_private_dir, container.ovz_id, path)
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
        container = OvzContainer.find(nova_id=instance_id)
        ovz_utils.execute('vzctl', 'exec2', container.ovz_id, 'echo',
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
        ovz_utils.execute('vzctl', 'chkpnt', instance['uuid'],
                          '--suspend', run_as_root=True)

        # Set the instance power state to suspended for accurate reporting
        try:
            self.virtapi.instance_update(
                admin_context, instance['uuid'],
                {'power_state': power_state.SUSPENDED})
        except exception.InstanceNotFound as err:
            LOG.error(_('Instance %s not found in the database') %
                      instance['uuid'])
            LOG.error(err)

    def resume(self, instance, network_info, block_device_info=None):
        """
        resume the specified instance
        """
        # grab an admin context to update the database
        admin_context = context.get_admin_context()

        # Resume the instance
        ovz_utils.execute('vzctl', 'chkpnt', instance['uuid'],
                          '--resume', run_as_root=True)

        # Set the instance power state to running
        try:
            self.virtapi.instance_update(
                admin_context, instance['uuid'],
                {'power_state': power_state.RUNNING})
        except exception.InstanceNotFound as err:
            LOG.error(_('Instance %s not found in the database') %
                      instance['uuid'])
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
        try:
            container = OvzContainer.find(nova_id=instance_id)
        except exception.InstanceNotFound:
            LOG.info("Instance %s cannot be deleted, since it does not exist"
                     % instance_id)
            return
        except BaseException:
            raise

        for filename in os.listdir(CONF.ovz_config_dir):
            if fnmatch.fnmatch(filename, '%s.*' % container.ovz_id):
                # minor protection for /
                if CONF.ovz_config_dir == '/':
                    raise exception.InvalidDevicePath(
                        _('I refuse to operate on /'))

                filename = '%s/%s' % (CONF.ovz_config_dir, filename)
                LOG.info(_('Deleting file: %s') % filename)
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
            LOG.info(_('Removed metadata for instance %s') % instance['id'])
        else:
            LOG.error(_('Problem removing metadata for instance %s') %
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
                LOG.info(_('State is %s') % state)

                if state is power_state.RUNNING:
                    LOG.info(_('Ve is running, stopping now.'))
                    self._stop(instance)
                    LOG.debug(_('Ve stopped'))

                LOG.info(_('Attempting to destroy container'))
                self._destroy(instance['id'])
            except exception.InstanceUnacceptable as err:
                LOG.error(_('There was an error with the destroy process'))
                LOG.error(_('Error from ovz_utils: %s') % err)
                timer.stop()
                LOG.debug(_('Timer stopped for _wait_for_destroy'))
                raise exception.InstanceTerminationFailure(
                    _('Error running vzctl destroy'))
            except exception.InstanceNotFound:
                LOG.warn(_('Container not found, destroyed?'))
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
        try:
            container = OvzContainer.find(nova_id=instance_id)
            container.delete()
        except exception.InstanceNotFound:
            LOG.info("Instance %s cannot be deleted, since it does not exist"
                     % instance_id)
            return
        except BaseException:
            raise

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
        container = OvzContainer.find(uuid=instance['uuid'])
        if connection_info['driver_volume_type'] == 'iscsi':
            volume = ovziscsi.OVZISCSIStorageDriver(
                container.ovz_id, mountpoint, connection_info)
            volume.discover_volume()
        else:
            raise NotImplementedError(
                _('There are no suitable storage drivers'))

        volume.attach()

        # Save volume info to the container's storage info store. This is
        # just a precaution and stores the volume information necessary
        # to manually re-establish communication should nova services
        # go away.
        ext_str = ext_storage.OVZExtStorage(container.ovz_id)
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
        container = OvzContainer.find(uuid=instance['uuid'])
        if connection_info['driver_volume_type'] == 'iscsi':
            volume = ovziscsi.OVZISCSIStorageDriver(
                container.ovz_id, mountpoint, connection_info)
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
        container = OvzContainer.find(uuid=instance['uuid'])
        ext_str = ext_storage.OVZExtStorage(container.ovz_id)
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
        container = OvzContainer.find(name=instance['name'])

        # Store the assumed state as the default
        # Coerced into an INT because it comes from SQLAlchemy as a string
        state = int(instance['power_state'])

        LOG.info(_('Instance %(uuid)s is in state %(power_state)s') %
                 {'uuid': container.uuid, 'power_state': state})

        # NOTE(imsplitbit): This is not ideal but it looks like nova uses
        # codes returned from libvirt and xen which don't correlate to
        # the status returned from OpenVZ which is either 'running' or
        # 'stopped'.  There is some contention on how to handle systems
        # that were shutdown intentially however I am defaulting to the
        # nova expected behavior.
        if container.state == 'running':
            new_state = power_state.RUNNING
        elif container.state is None or container.state == '-':
            new_state = power_state.NOSTATE
        else:
            new_state = power_state.SHUTDOWN

        if state != new_state:
            state = new_state

        LOG.debug(
            _('OpenVz says instance %(uuid)s is in state %(state)s') %
            {'uuid': container.uuid, 'state': state})

        # TODO(imsplitbit): Need to add all metrics to this dict.
        return {'state': state,
                'max_mem': 0,
                'mem': 0,
                'num_cpu': 0,
                'cpu_time': 0}

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
        LOG.info(_('Migration context: %s') % context)
        LOG.info(_('Migration instance: %s') % instance)
        LOG.info(_('Migration dest: %s') % dest)
        LOG.info(_('Migration instance_type: %s') % instance_type)
        LOG.info(_('Migration network_info: %s') % network_info)

        if not dest:
            LOG.error(_('No destination given to migration'))
            raise exception.MigrationError(
                _('Migration destination is: %s') % dest)

        if dest == CONF.my_ip:
            # if this is an inplace resize we don't need to do any of this
            LOG.info(_('This is an inplace migration'))
            instance.system_metadata['migration_type'] = 'resize_in_place'
            instance.save()
            return
        elif instance.system_metadata.get('migration_type'):
            # if we failed to clear this metadata on a previous migration
            # it will cause everything to blow up, so clear it out now
            del instance.system_metadata['migration_type']
            instance.save()

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
            container = OvzContainer.find(uuid=instance['uuid'])
            self._pymigration_send_to_host(
                instance, ovz_utils.generate_network_dict(container,
                                                          network_info),
                block_device_info, dest, live_migration)

    def _pymigration_send_to_host(self, instance, network_info,
                                  block_device_info, dest, live_migration):
        """
        This performs a more complex but more secure migration using a pure
        python implemented vz migration driver.
        """
        LOG.info(_('Beginning pure python based migration'))
        container = OvzContainer.find(uuid=instance['uuid'])
        mobj = ovz_migration.OVZMigration(
            container, network_info, block_device_info, dest, live_migration)
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
        # default is yes
        if not CONF.ovz_vzmigrate_destroy_source_container_on_migrate:
            cmd += ['-r', 'no']
        if CONF.ovz_vzmigrate_verbose_migration_logging:
            cmd.append('-v')
        cmd.append(dest)
        cmd.append(instance['uuid'])
        LOG.info(
            _('Beginning the migration of %(instance_id)s to %(dest)s') %
            {'instance_id': instance['uuid'], 'dest': dest})
        out = ovz_utils.execute(*cmd, run_as_root=True)
        LOG.info(_('Output from migration process: %s') % out)

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
            LOG.info(_('Finishing resize-in-place for %s') % instance['uuid'])
            container = OvzContainer.find(uuid=instance['uuid'])
            self.resource_manager.configure_container_resources(context,
                container, instance['instance_type_id'])
            self.resource_manager.configure_container_network(container,
                network_info, is_migration=False)

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

        container = OvzContainer.find(uuid=instance['uuid'])

        if resize_instance:
            LOG.debug(_('A resize after migration was requested: %s') %
                      instance['uuid'])
            self.resource_manager.configure_container_resources(context,
                container, instance['instance_type_id'])
            self.resource_manager.configure_container_network(container,
                network_info, is_migration=True)
            LOG.info(_('Resized instance after migration: %s') %
                     instance['uuid'])
        else:
            LOG.debug(_('Regenerating TC rules for instance %s') %
                      instance['uuid'])
            self.resource_manager.configure_container_network(container,
                network_info, is_migration=True)
            LOG.info(_('Regenerated TC rules for instance %s') %
                     instance['uuid'])

        if not live_migration:
            self._start(instance)

        # Some data gets lost in the migration, make sure ovz has current info
        container.save_ovz_metadata()
        # instance.system_metadata will be saved by nova.compute.manager
        # after this method returns (driver.finish_migration)
        # ovz_utils.save_instance_metadata does not work because when this
        # method returns nova.compute.manager calls instance.save and any
        # info that was not updated through instance will be reverted
        instance.system_metadata['ovz_id'] = container.ovz_id

    def _pymigrate_finish_migration(self, instance, network_info,
                                    live_migration):
        """
        Take all transferred files and put them back into place to create a
        working instance.
        """
        LOG.debug(_('Beginning python based finish_migration'))

        # generate a unique local id on the destination
        container = OvzContainer(
            ovz_id=OvzContainer.get_next_id(),
            uuid=instance['uuid'],
            nova_id=instance['id'],
            host=CONF.host,
            name=instance['name'],
        )

        interfaces = ovz_utils.generate_network_dict(container,
                                                     network_info)
        mobj = ovz_migration.OVZMigration(
            container, interfaces, None, live_migration)
        mobj.undump_instance()

        # the vif name should have changed during migration, so set it up anew
        self.plug_vifs(instance, network_info)
        self._setup_networking(container, network_info)

        # Crude but we just need to give things time to settle before cleaning
        # up all the dumped stuff
        # TODO(imsplitbit): maybe a wait_for_start method with a looping
        # timer is better here, will check into it soon
        time.sleep(5)
        mobj.cleanup_destination()
        LOG.info(_('Finished python based finish_migration'))

    def _vzmigrate_setup_dest_host(self, instance, network_info):
        """
        Sequence to run on destination host should the migration be done
        by the vzmigrate tools.
        """
        LOG.debug(_('Stopping instance: %s') % instance['uuid'])
        self._stop(instance)
        LOG.info(_('Stopped instance: %s') % instance['uuid'])

        self.plug_vifs(instance, network_info)

        LOG.debug(_('Starting instance: %s') % instance['uuid'])
        self._start(instance)
        LOG.info(_('Started instance: %s') % instance['uuid'])

    def confirm_migration(self, migration, instance, network_info):
        """
        Run on the source host to confirm the migration and cleans up the
        the files from the source host.
        """
        LOG.debug(_('Beginning confirm migration for %s') % instance['uuid'])

        # Get the instance metadata to see what we need to do
        meta = ovz_utils.read_instance_metadata(instance['id'])
        migration_type = meta.get('migration_type')

        container = OvzContainer.find(uuid=instance['uuid'])
        live_migration = True
        ext_str = ext_storage.OVZExtStorage(container.ovz_id)
        if ext_str._volumes:
            live_migration = False

        if migration_type == 'resize_in_place':
            # This is a resize on the same host so its simple, resize
            # in place and then exit the method
            if ovz_utils.remove_instance_metadata_key(instance['id'],
                                                      'migration_type'):
                LOG.debug(_('Removed migration_type metadata'))
            else:
                LOG.warn(_('Failed to remove migration_type metadata'))
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
                LOG.info(_('Cleaning up migration on source host'))
                mobj = ovz_migration.OVZMigration(
                    container, ovz_utils.generate_network_dict(
                        container, network_info), None, live_migration)
                mobj.cleanup_source()
                self._destroy(instance['id'])
                self._clean_orphaned_files(instance['id'])
            else:
                LOG.warn(
                    _('Check instance: %(instance_id)s, it may be broken. '
                        'power_state: %(ps)s') %
                    {'instance_id': instance['uuid'],
                     'ps': status})
        except exception.InstanceNotFound:
            LOG.warn(
                _('Instance %s not found, migration cleaned itself up?') %
                instance['uuid'])
        except exception.InstanceUnacceptable:
            LOG.error(_('Failed to stop and destroy the instance'))
        LOG.info(_('Finished confirm migration for %s') % instance['uuid'])

    def finish_revert_migration(self, context, instance, network_info,
                                block_device_info=None, power_on=True):
        """Finish reverting a resize, powering back on the instance."""
        # Get the instance metadata to see what we need to do
        LOG.debug(_('Beginning finish_revert_migration'))
        meta = ovz_utils.read_instance_metadata(instance['id'])
        migration_type = meta.get('migration_type')
        container = OvzContainer.find(uuid=instance['uuid'])

        if migration_type == 'resize_in_place':
            # This is a resize on the same host so its simple, resize
            # in place and then exit the method
            LOG.info(_('Reverting in-place migration for %s') %
                     instance['id'])
            self.resource_manager.configure_container_resources(context,
                container, instance['instance_type_id'])
            self.resource_manager.configure_container_network(container,
                network_info)
            if ovz_utils.remove_instance_metadata_key(instance['id'],
                                                      'migration_type'):
                LOG.debug(_('Removed migration_type metadata'))
                LOG.info(_('Done reverting in-place migration for %s') %
                         instance['uuid'])
            else:
                LOG.warn(_('Failed to remove migration_type metadata'))
            return

        container.save_ovz_metadata()
        if block_device_info:
            LOG.info(_('Instance %s has volumes') % instance['id'])
            # the instance has external volumes and was not a live migration
            # so we need to reattach external volumes
            live_migration = False
            LOG.info(_('Starting instance %s, after revert') %
                     instance['uuid'])
            ext_str = ext_storage.OVZExtStorage(container.ovz_id)

            for mountpoint, connection_info in ext_str.volumes():
                self.attach_volume(connection_info, instance, mountpoint)

            self._start(instance)
        else:
            LOG.info(_('Instance %s has no volumes') % instance['uuid'])
            live_migration = True
            LOG.info(_('Resuming live migration for %s') % instance['uuid'])
            self.resume(instance, network_info)

        mobj = ovz_migration.OVZMigration(
            container, ovz_utils.generate_network_dict(
                container, network_info), None, live_migration)
        mobj.cleanup_files()

        # instance.system_metadata will be saved by nova.compute.manager
        # after this method returns (driver.finish_revert_migration)
        # ovz_utils.save_instance_metadata does not work because when this
        # method returns nova.compute.manager calls instance.save and any
        # info that was not updated through instance will be reverted
        instance.system_metadata['ovz_id'] = container.ovz_id

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
