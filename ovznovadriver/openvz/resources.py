# vim: tabstop=4 shiftwidth=4 softtabstop=4

# Copyright 2014 Rackspace
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

from ovznovadriver.openvz import utils as ovz_utils
from ovznovadriver.openvz.file_ext import boot as ovzboot
from ovznovadriver.openvz.file_ext import shutdown as ovzshutdown
from ovznovadriver.openvz.network_drivers import tc as ovztc

from oslo.config import cfg

from nova.openstack.common import log as logging

__openvz_resource_opts = [
    cfg.BoolOpt('ovz_use_cpuunit',
                default=True,
                help='Use OpenVz cpuunits for guaranteed minimums'),
    cfg.BoolOpt('ovz_use_cpulimit',
                default=True,
                help='Use OpenVz cpulimit for maximum cpu limits'),
    cfg.FloatOpt('ovz_cpulimit_overcommit_multiplier',
                 default=1.0,
                 help='Multiplier for cpulimit to facilitate over '
                      'committing cpu resources'),
    cfg.BoolOpt('ovz_use_cpus',
                default=True,
                help='Use OpenVz cpus for max cpus '
                     'available to the container'),
    cfg.BoolOpt('ovz_use_ioprio',
                default=True,
                help='Use IO fair scheduling'),
    cfg.BoolOpt('ovz_use_ubc',
                default=True,
                help='Use OpenVz Vswap memory management model instead of '
                     'User BeanCounters'),
    cfg.IntOpt('ovz_file_descriptors_per_unit',
               default=4096,
               help='Max open file descriptors per memory unit'),
    cfg.IntOpt('ovz_memory_unit_size',
               default=512,
               help='Unit size in MB'),
    cfg.BoolOpt('ovz_use_disk_quotas',
                default=True,
                help='Use disk quotas to contain disk usage'),
    cfg.StrOpt('ovz_disk_space_increment',
               default='G',
               help='Disk subscription increment'),
    cfg.FloatOpt('ovz_disk_space_oversub_percent',
                 default=1.10,
                 help='Local disk over subscription percentage'),
    cfg.IntOpt('ovz_kmemsize_percent_of_memory',
               default=20,
               help='Percent of memory of the container to allow to be used '
                    'by the kernel'),
    cfg.IntOpt('ovz_kmemsize_barrier_differential',
               default=10,
               help='Difference of kmemsize barrier vs limit'),
    ]

CONF = cfg.CONF
CONF.register_opts(__openvz_resource_opts)

LOG = logging.getLogger(__name__)


class VZResourceManager(object):
    """Manage OpenVz container resources

    Meant to be a collection of class_methods that will decide/calculate
    resource configs and apply them through the Container class"""

    # TODO (jcru) make this a config?
    # OpenVz sets the upper limit of cpuunits to 500000
    MAX_CPUUNITS = 500000

    def __init__(self, virtapi):
        """Requires virtapi (api to conductor) to get flavor info"""
        self.virtapi = virtapi
        # TODO (jcru) replace dict (self.utility) with self.cpulimit?
        self.utility = dict()

    def _get_flavor_info(self, context, flavor_id):
        """Get the latest flavor info which contains extra_specs"""
        # instnace_type refers to the flavor (what you see in flavor list)
        return self.virtapi.flavor_get(context, flavor_id)

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

    def get_cpulimit(self):
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

    def get_cpuunits_usage(self):
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

    def configure_container_resources(self, context, container,
                                      requested_flavor_id):
        instance_type = self._get_flavor_info(context, requested_flavor_id)

        self._setup_memory(container, instance_type)
        self._setup_file_limits(container, instance_type)
        self._setup_cpu(container, instance_type)
        self._setup_io(container, instance_type)
        self._setup_disk_quota(container, instance_type)

    # TODO(jcru) normally we would pass a context anf requested flavor as
    # configure_container_resources but we look up instance_type within tc
    # code.  Ideally all of the networking setup would happen here
    def configure_container_network(self, container, network_info,
                                    is_migration=False):
        self._generate_tc_rules(container, network_info, is_migration)

    def _setup_memory(self, container, instance_type):
        """
        """
        if CONF.ovz_use_ubc:
            self._setup_memory_with_ubc(instance_type, container)
            return

        self._setup_memory_with_vswap(container, instance_type)

    def _setup_memory_with_ubc(self, container, instance_type):
        instance_memory_mb = int(instance_type.get('memory_mb'))

        instance_memory_bytes = ((instance_memory_mb * 1024) * 1024)
        instance_memory_pages = self._calc_pages(instance_memory_mb)

        # Now use the configuration CONF to calculate the appropriate
        # values for both barrier and limit.
        kmem_limit = int(instance_memory_mb * (
            float(CONF.ovz_kmemsize_percent_of_memory) / 100.0))
        kmem_barrier = int(kmem_limit * (
            float(CONF.ovz_kmemsize_barrier_differential) / 100.0))

        container.set_vmguarpages(instance_memory_pages)
        container.set_privvmpages(instance_memory_pages)
        container.set_kmemsize(kmem_barrier, kmem_limit)

    def _setup_memory_with_vswap(self, container, instance_type):
        memory = int(instance_type.get('memory_mb'))
        # TODO(jcru) not sure if we wish to definie this within extra_specs
        # or use flavor.swap value (instance_type.get('swap'))
        # Check if vswap swap value is predefined in flavors extra_specs
        instance_type_extra_specs = instance_type.get('extra_specs', {})
        swap = instance_type_extra_specs.get('vz_vswap', None)

        # If no swap has been setup under the flavor extra specs then calculate
        # double the amount of RAM (this is really bad for large flavor sizes)
        swap = int(swap) if swap else memory * 2

        container.set_vswap(instance, memory, swap)

    def _setup_file_limits(self, container, instance_type):
        instance_memory_mb = int(instance_type.get('memory_mb'))
        memory_unit_size = int(CONF.ovz_memory_unit_size)
        max_fd_per_unit = int(CONF.ovz_file_descriptors_per_unit)
        max_fd = int(instance_memory_mb / memory_unit_size) * max_fd_per_unit

        container.set_numfiles(max_fd)
        container.set_numflock(max_fd)

    # TODO(jcru) overide caclulated values?
    def _setup_cpu(self, container, instance_type):
        """
        """
        instance_memory_mb = instance_type.get('memory_mb')
        instance_type_extra_specs = instance_type.get('extra_specs', {})
        percent_of_resource = self._percent_of_resource(instance_memory_mb)

        if CONF.ovz_use_cpuunit:
            LOG.debug(_('Reported cpuunits %s') % self.MAX_CPUUNITS)
            LOG.debug(_('Reported percent of resource: %s') % percent_of_resource)

            units = int(round(self.MAX_CPUUNITS * percent_of_resource))

            if units > self.MAX_CPUUNITS:
                units = self.MAX_CPUUNITS

            container.set_cpuunits(units)

        if CONF.ovz_use_cpulimit:
            # Check if cpulimit for flavor is predefined in flavors extra_specs
            cpulimit = instance_type_extra_specs.get('vz_cpulimit', None)

            if not cpulimit:
                cpulimit = int(round(
                    (self.utility['CPULIMIT'] * percent_of_resource) *
                    CONF.ovz_cpulimit_overcommit_multiplier))
            else:
                cpulimit = int(cpulimit)

            if cpulimit > self.utility['CPULIMIT']:
                LOG.warning(_("The cpulimit that was calculated or predefined "
                    "(%s) is to high based on the CPULIMIT (%s)") %
                (cpulimit, self.utility['CPULIMIT']))
                LOG.warning(_("Using CPULIMIT instead."))
                cpulimit = self.utility['CPULIMIT']

            container.set_cpulimit(cpulimit)

        if CONF.ovz_use_cpus:
            vcpus = int(instance_type.get('vcpus'))
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
            container.set_cpus(vcpus)

    def _setup_io(self, container, instance_type):
        if CONF.ovz_use_ioprio:
            instance_memory_mb = instance_type.get('memory_mb')
            num_chunks = int(int(instance_memory_mb) / CONF.ovz_memory_unit_size)

            try:
                ioprio = int(round(math.log(num_chunks, 2)))
            except ValueError:
                ioprio = 0

            if ioprio > 7:
                # ioprio can't be higher than 7 so set a ceiling
                ioprio = 7

            container.set_ioprio(instance, ioprio)

    def _setup_disk_quota(self, container, instance_type):
        if CONF.ovz_use_disk_quotas:
            instance_root_gb = instance_type.get('root_gb')

            soft_limit = int(instance_root_gb)
            hard_limit = int(soft_limit * CONF.ovz_disk_space_oversub_percent)

            # Now set the increment of the limit.  I do this here so that I don't
            # have to do this in every line above.
            soft_limit = '%s%s' % (soft_limit, CONF.ovz_disk_space_increment)
            hard_limit = '%s%s' % (hard_limit, CONF.ovz_disk_space_increment)

            container.set_diskspace(soft_limit, hard_limit)

    # TODO(jcru) move more of tc logic into here ?
    def _generate_tc_rules(self, container, network_info, is_migration=False):
        """
        Utility method to generate tc info for instances that have been
        resized and/or migrated
        """
        LOG.debug(_('Setting network sizing'))
        boot_file = ovzboot.OVZBootFile(container.ovz_id, 755)
        shutdown_file = ovzshutdown.OVZShutdownFile(container.ovz_id, 755)

        if not is_migration:
            with shutdown_file:
                LOG.debug(_('Cleaning TC rules for %s') % container.nova_id)
                shutdown_file.read()
                shutdown_file.run_contents(raise_on_error=False)

        # On resize we throw away existing tc_id and make a new one
        # because the resize *could* have taken place on a different host
        # where the tc_id is already in use.
        meta = ovz_utils.read_instance_metadata(container.nova_id)
        tc_id = meta.get('tc_id', None)
        if tc_id:
            ovz_utils.remove_instance_metadata_key(container.nova_id, 'tc_id')

        with shutdown_file:
            shutdown_file.set_contents(list())

        with boot_file:
            boot_file.set_contents(list())

        LOG.debug(_('Getting network dict for: %s') % container.uuid)
        interfaces = ovz_utils.generate_network_dict(container,
                                                     network_info)
        for net_dev in interfaces:
            LOG.debug(_('Adding tc rules for: %s') %
                      net_dev['vz_host_if'])
            tc = ovztc.OVZTcRules()
            tc.instance_info(container.nova_id, net_dev['address'],
                             net_dev['vz_host_if'])
            with boot_file:
                boot_file.append(tc.container_start())

            with shutdown_file:
                shutdown_file.append(tc.container_stop())

        with boot_file:
            if not is_migration:
                # during migration, the instance isn't yet running, so it'll
                # just spew errors to attempt to apply these rules before then
                LOG.debug(_('Running TC rules for: %s') % container.ovz_id)
                boot_file.run_contents()
            LOG.debug(_('Saving TC rules for: %s') % container.ovz_id)
            boot_file.write()

        with shutdown_file:
            shutdown_file.write()
