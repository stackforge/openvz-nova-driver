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

import json

from nova import exception
from nova.openstack.common import log as logging
from oslo.config import cfg
from ovznovadriver.localization import _
from ovznovadriver.openvz import utils as ovz_utils

CONF = cfg.CONF

LOG = logging.getLogger('ovznovadriver.openvz.container')

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

