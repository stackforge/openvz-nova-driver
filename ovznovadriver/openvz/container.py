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

import json

from nova import exception
from nova.compute import flavors
from nova.openstack.common import log as logging
from oslo.config import cfg
from ovznovadriver.localization import _
from ovznovadriver.openvz import utils as ovz_utils

CONF = cfg.CONF

LOG = logging.getLogger(__name__)


def generate_vz_settings_from_flavor(flavor):

    settings = {}

    return settings


# More of the openvz logic belongs in here, but it was hard to de-tangle it
# from all the libraries, so a minimal subset to get the desired outcome
# was ported with the hope that future commits will further improve upon it

class OvzContainer(object):
    """
    A class for objects that represent OpenVZ containers.

    All methods for interacting with OpenVZ containers spawn from here.
    """

    def __init__(self, ovz_id, nova_id=None, name=None, uuid=None,
                 host=None, state=None):
        self.ovz_data = {
            'ovz_id': ovz_id,
            'nova_id': nova_id,
            'name': name,
            'uuid': uuid,
            'host': host,
        }
        if state is not None:
            self.ovz_data['state'] = state

    @property
    def ovz_id(self):
        return self.ovz_data['ovz_id']

    @property
    def nova_id(self):
        return self.ovz_data['nova_id']

    @property
    def name(self):
        return self.ovz_data['name']

    @property
    def uuid(self):
        return self.ovz_data['uuid']

    @property
    def host(self):
        return self.ovz_data['host']

    @property
    def state(self):
        if 'state' in self.ovz_data:
            return self.ovz_data['state']

        self._load_state()
        return self.ovz_data['state']

    @classmethod
    def create(cls, image, **kwargs):
        """
        Create an OpenVZ container with the specified arguments.
        """

        # TODO(imsplitbit): This needs to set an os template for the image
        # as well as an actual OS template for OpenVZ to know what config
        # scripts to use.  This can be problematic because there is no concept
        # of OS name, it is arbitrary so we will need to find a way to
        # correlate this to what type of disto the image actually is because
        # this is the clue for openvz's utility scripts.  For now we will have
        # to set it to 'ubuntu'


        container = OvzContainer(ovz_id=cls.get_next_id(), **kwargs)

        ovz_utils.execute('vzctl', 'create', container.ovz_id, '--ostemplate',
                          image, run_as_root=True)
        container.save_ovz_metadata()
        return container

    @classmethod
    def get_next_id(cls):
        """
        Gets the next available local openvz id.
        """
        # openvz reserves ids 0-100, so start at 101
        id = 101
        existing = OvzContainers.list(host=None)
        if existing:
            highest = int(existing[-1].ovz_id, base=10)
            if highest > 100:
                id = highest + 1
        return str(id)

    def save_ovz_metadata(self):
        """
        Save information important to associate nova information with this
        instance.
        """
        description = json.dumps({
            'host': CONF.host,
            'name': self.name,
            'nova_id': str(self.nova_id),
            'uuid': self.uuid,
        })

        # setting the name to the uuid lets you use the uuid with vzctl
        ovz_utils.execute(
            'vzctl', 'set', self.ovz_id, '--save', '--name', self.uuid,
            '--description', description, run_as_root=True)

    def _load_state(self):
        """
        Load the current state data from OpenVZ.
        """
        out = ovz_utils.execute('vzlist', '--no-header', '--all',
                                '--output', 'status', self.ovz_id,
                                raise_on_error=False, run_as_root=True)
        if not out:
            raise exception.InstanceNotFound(
                _('Instance %s doesnt exist') % self.ovz_id)

        out = out.split()
        self.ovz_data.state = out[0]

    @classmethod
    def find(cls, ovz_id=None, name=None, nova_id=None, uuid=None):
        """
        Find a specific OpenVZ container by name, ovz_id (ctid),
        nova_id (instance id), or uuid.
        """

        if (ovz_id):
            return cls._find(ovz_id)

        if (name):
            name_wildcard = '*"name": "' + name + '"*'
            return cls._find('--description', name_wildcard)

        if (uuid):
            uuid_wildcard = '*"uuid": "' + uuid + '"*'
            return cls._find('--description', uuid_wildcard)

        if (nova_id):
            nova_id_wildcard = '*"nova_id": "' + str(nova_id) + '"*'
            return cls._find('--description', nova_id_wildcard)

    @classmethod
    def _find(cls, *params):
        vzlist_cmd = ['vzlist', '--no-header', '--all',
                      '--output', 'ctid,status,description']
        vzlist_cmd.extend(params)
        out = ovz_utils.execute(*vzlist_cmd, raise_on_error=False,
                                run_as_root=True)

        # for testing, we allow multiple nova hosts per node.  We don't want
        # to load containers for the other hosts, so pretend they don't exist
        found = None
        if out is not None:
            lines = out.splitlines()
            for line in lines:
                possible = cls._load_from_vzlist(line)
                if CONF.host == possible.host:
                    found = possible
                    break

        if found is None:
            raise exception.InstanceNotFound(
                _('Instance %s does not exist') % str(params))

        LOG.debug(_('Loading container from vzlist %(params)s: %(ovz_data)s') %
            {'params': params, 'ovz_data': found.ovz_data})

        return found

    @classmethod
    def _load_from_vzlist(cls, line):
        # since the JSON bits have spaces in them, limit how many splits we do
        raw = line.split(None, 2)
        ovz_data = {}
        if raw[2] is not None and raw[2] != '-':
            try:
                ovz_data = json.loads(raw[2])
            except BaseException, e:
                LOG.error("ERROR parsing json %s: %s" % (raw[2], e))
        ovz_data['ovz_id'] = raw[0]
        ovz_data['state'] = raw[1]
        return OvzContainer(**ovz_data)

    def prep_for_migration(self):
        # this only really happens in development VMs, but we can't have two
        # containers with the same name, so we need to rename the old one
        temp_name = '%s-migrated' % self.uuid
        ovz_utils.execute(
            'vzctl', 'set', self.ovz_id, '--save', '--name', temp_name,
            run_as_root=True)

    def delete(self):
        """
        Remove the OpenVZ container this object represents.
        """
        ovz_utils.execute('vzctl', 'destroy', self.ovz_id, run_as_root=True)

    def apply_config(self, config='basic'):
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
        ovz_utils.execute('vzctl', 'set', self.ovz_id, '--save',
                          '--applyconfig', config, run_as_root=True)

    def set_vz_os_hint(self, ostemplate='ubuntu'):
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
        ovz_utils.execute('vzctl', 'set', self.ovz_id, '--save',
                          '--ostemplate', ostemplate, run_as_root=True)

    def set_numflock(self, max_file_descriptors):
        """
        Run the command:

        vzctl set <ctid> --save --numflock <number>
        """

        ovz_utils.execute('vzctl', 'set', self.ovz_id, '--save',
                          '--numflock', max_file_descriptors,
                          run_as_root=True)

    def set_numfiles(self, max_file_descriptors):
        """
        Run the command:

        vzctl set <ctid> --save --numfile <number>
        """

        ovz_utils.execute('vzctl', 'set', self.ovz_id, '--save',
                          '--numfile', max_file_descriptors,
                          run_as_root=True)

    # TODO(jcru) extract calculations from here and only pass tcp_sockets to
    # function.
    def set_numtcpsock(self, memory_mb):
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

        ovz_utils.execute('vzctl', 'set', self.ovz_id, '--save',
                          '--numtcpsock', tcp_sockets, run_as_root=True)

    def set_vmguarpages(self, num_pages):
        """
        Set the vmguarpages attribute for a container.  This number represents
        the number of 4k blocks of memory that are guaranteed to the container.
        This is what shows up when you run the command 'free' in the container.

        Run the command:

        vzctl set <ctid> --save --vmguarpages <num_pages>

        If this fails to run then an exception is raised because this affects
        the memory allocation for the container.
        """
        ovz_utils.execute('vzctl', 'set', self.ovz_id, '--save',
                          '--vmguarpages', num_pages, run_as_root=True)

    def set_privvmpages(self, num_pages):
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
        ovz_utils.execute('vzctl', 'set', self.ovz_id, '--save',
                          '--privvmpages', num_pages, run_as_root=True)

    def set_kmemsize(self, instance_memory):
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

        # TODO(jcru) move calculations to ResourceManager class
        # Now use the configuration CONF to calculate the appropriate
        # values for both barrier and limit.
        kmem_limit = int(instance_memory * (
            float(CONF.ovz_kmemsize_percent_of_memory) / 100.0))
        kmem_barrier = int(kmem_limit * (
            float(CONF.ovz_kmemsize_barrier_differential) / 100.0))
        kmemsize = '%d:%d' % (kmem_barrier, kmem_limit)

        ovz_utils.execute('vzctl', 'set', self.ovz_id, '--save',
                          '--kmemsize', kmemsize, run_as_root=True)

    def set_cpuunits(self, units):
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

        ovz_utils.execute('vzctl', 'set', self.ovz_id, '--save',
                          '--cpuunits', units, run_as_root=True)

    def set_cpulimit(self, cpulimit):
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

        ovz_utils.execute('vzctl', 'set', self.ovz_id, '--save',
                          '--cpulimit', cpulimit, Run_as_root=True)

    # TODO(jcru) CALC extract self.utility
    def set_cpus(self, vcpus):
        """
        The number of logical cpus that are made available to the container.
        Default to showing 2 cpus to each container at a minimum.

        Run the command:

        vzctl set <ctid> --save --cpus <num_cpus>

        If this fails to run an exception is raised because this limits the
        number of cores that are presented to each container and if this fails
        to set *ALL* cores will be presented to every container, that be bad.
        """

        ovz_utils.execute('vzctl', 'set', self.ovz_id, '--save', '--cpus',
                          vcpus, run_as_root=True)

    def set_ioprio(self, ioprio):
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

        ovz_utils.execute('vzctl', 'set', self.ovz_id, '--save',
                          '--ioprio', ioprio, run_as_root=True)

    def set_diskspace(self, soft_limit, hard_limit):
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

        ovz_utils.execute('vzctl', 'set', self.ovz_id, '--save',
                          '--diskspace', '%s:%s' % (soft_limit, hard_limit),
                          run_as_root=True)

    def set_vswap(self, ram, swap):
        """
        Implement OpenVz vswap memory management model (The other being user
        beancounters).

        The sum of physpages_limit and swappages_limit limits the maximum
        amount of memory which can be used by a container. When physpages limit
        is reached, memory pages belonging to the container are pushed out to
        so called virtual swap (vswap). The difference between normal swap and
        vswap is that with vswap no actual disk I/O usually occurs. Instead, a
        container is artificially slowed down, to emulate the effect of the
        real swapping. Actual swap out occurs only if there is a global memory
        shortage on the system.

        Run the command:

        vzctl set <ctid> --ram <physpages_limit> --swap <swappages_limit>
        """

        ovz_utils.execute('vzctl', 'set', self.ovz_id, '--save',
                          '--ram', ram, '--swap', swap, run_as_root=True)


class OvzContainers(object):

    @classmethod
    def _list(cls, fields, host=CONF.host):
        # it's important that description is the last field so it doesn't get
        # truncated.  Alternatively, the --no-trim option added in openvz 3.2
        # will make the point moot, if we want to force that dependency
        vzlist_cmd = ['vzlist', '--all', '--no-header',
                      '--output', fields]
        if host is not None:
            host_wildcard = '*"host": "' + host + '"*'
            vzlist_cmd.extend(['--description', host_wildcard])
        out = ovz_utils.execute(*vzlist_cmd,
            raise_on_error=False, run_as_root=True)
        if out:
            return out.splitlines()

        return list()

    @classmethod
    def list(cls, host=CONF.host):
        results = list()

        lines = cls._list('ctid,status,description', host=host)
        for line in lines:
            results.append(OvzContainer._load_from_vzlist(line))

        return results

    @classmethod
    def get_memory_mb_used(cls, block_size=4096):
        total_used_mb = 0
        lines = cls._list('ctid,privvmpages.l')
        for line in lines:
            line = line.split()
            total_used_mb += ((int(line[1]) * block_size) / 1024 ** 2)

        return total_used_mb
