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

from Cheetah import Template
import json
from nova.compute import power_state
from nova import exception
from nova.virt.openvz import utils as ovz_utils
import os
from oslo.config import cfg
import random
import socket

CONF = cfg.CONF

global _ovz_tc_available_ids
global _ovz_tc_inflight_ids
_ovz_tc_available_ids = None
_ovz_tc_inflight_ids = None


class FakeOVZExtStorage(object):
    def __init__(self, instance_id):
        self.instance_id = instance_id
        self._volumes = {}

    def add_volume(self, mount_point, connection_info):
        self._volumes[mount_point] = connection_info

    def remove_volume(self, mount_point):
        self._volumes.pop(mount_point)


class FakeOVZTcRules(object):
    if not _ovz_tc_available_ids:
        _ovz_tc_available_ids = list()

    if not _ovz_tc_inflight_ids:
        _ovz_tc_inflight_ids = list()

    def __init__(self):
        if not len(FakeOVZTcRules._ovz_tc_available_ids):
            FakeOVZTcRules._ovz_tc_available_ids = [
                i for i in range(1, CONF.ovz_tc_id_max)
            ]
        self.instance_type = {'memory_mb': 512}
        self._remove_used_ids()

    def instance_info(self, instance_id, address, vz_iface):
        if not instance_id:
            self.instance_type = dict()
            self.instance_type['memory_mb'] = 2048

        self.address = address
        self.vz_iface = vz_iface
        self.bandwidth = int(
            round(self.instance_type['memory_mb'] /
                  CONF.ovz_memory_unit_size)) * CONF.ovz_tc_mbit_per_unit
        self.tc_id = self._get_instance_tc_id()
        if not self.tc_id:
            self.tc_id = self.get_id()

        self._save_instance_tc_id()

    def get_id(self):
        self._remove_used_ids()
        tc_id = self._pull_id()
        while tc_id in FakeOVZTcRules._ovz_tc_inflight_ids:
            tc_id = self._pull_id()
        self._reserve_id(tc_id)
        return id

    def container_start(self):
        template = self._load_template('tc_container_start.template')
        search_list = {
            'prio': self.tc_id,
            'host_iface': CONF.ovz_tc_host_slave_device,
            'vz_iface': self.vz_iface,
            'bandwidth': self.bandwidth,
            'vz_address': self.address,
            'line_speed': CONF.ovz_tc_max_line_speed
        }
        return self._fill_template(template, search_list).splitlines()

    def container_stop(self):
        template = self._load_template('tc_container_stop.template')
        search_list = {
            'prio': self.tc_id,
            'host_iface': CONF.ovz_tc_host_slave_device,
            'vz_iface': self.vz_iface,
            'bandwidth': self.bandwidth,
            'vz_address': self.address
        }
        return self._fill_template(template, search_list).splitlines()

    def host_start(self):
        template = self._load_template('tc_host_start.template')
        search_list = {
            'host_iface': CONF.ovz_tc_host_slave_device,
            'line_speed': CONF.ovz_tc_max_line_speed
        }
        return self._fill_template(template, search_list).splitlines()

    def host_stop(self):
        template = self._load_template('tc_host_stop.template')
        search_list = {
            'host_iface': CONF.ovz_tc_host_slave_device
        }
        return self._fill_template(template, search_list).splitlines()

    def _load_template(self, template_name):
        full_template_path = '%s/%s' % (
            CONF.ovz_tc_template_dir, template_name)
        full_template_path = os.path.abspath(full_template_path)
        try:
            template_file = open(full_template_path).read()
            return template_file
        except Exception as err:
            raise exception.FileNotFound(err)

    def _fill_template(self, template, search_list):
        return str(Template.Template(template, searchList=[search_list]))

    def _pull_id(self):
        return _ovz_tc_available_ids[random.randint(
            0, len(_ovz_tc_available_ids) - 1)]

    def _list_existing_ids(self):
        return [1, 3, 6]

    def _reserve_id(self, tc_id):
        FakeOVZTcRules._ovz_tc_inflight_ids.append(tc_id)
        FakeOVZTcRules._ovz_tc_available_ids.remove(tc_id)

    def _remove_used_ids(self):
        used_ids = self._list_existing_ids()
        for tc_id in used_ids:
            if tc_id in FakeOVZTcRules._ovz_tc_available_ids:
                FakeOVZTcRules._ovz_tc_available_ids.remove(tc_id)

    def _save_instance_tc_id(self):
        return

    def _get_instance_tc_id(self):
        return 1


class Context(object):
    def __init__(self):
        self.is_admin = False
        self.read_deleted = "yes"


class AdminContext(Context):
    def __init__(self):
        super(AdminContext, self).__init__()
        self.is_admin = True


# Stubs for faked file operations to allow unittests to test code paths
# without actually leaving file turds around the test box.
class FakeOvzFile(object):
    def __init__(self, filename, perms):
        self.filename = filename
        self.permissions = perms
        self.contents = []

    def __enter__(self):
        """
        This may feel dirty but we need to be able to read and write files
        as a non priviledged user so we need to change the permissions to
        be more open for our file operations and then secure them once we
        are done.
        """
        if not self.exists():
            self.make_path()
            self.touch()

        self.set_permissions(666)

    def __exit__(self, _type, value, tb):
        if self.exists():
            self.set_permissions(self.permissions)

    def exists(self):
        return True

    def make_path(self, path=None):
        return

    def touch(self):
        return

    def set_permissions(self, perms):
        return

    def read(self):
        return

    def run_contents(self, raise_on_error=True):
        return

    def set_contents(self, contents):
        self.contents = contents

    def make_proper_script(self):
        return

    def append(self, contents):
        if not isinstance(contents, list):
            contents = [str(contents)]
        self.contents = self.contents + contents

    def prepend(self, contents):
        if not isinstance(contents, list):
            contents = [str(contents)]
        self.contents = contents + self.contents

    def write(self):
        return


class FakeOVZShutdownFile(FakeOvzFile):
    def __init__(self, instance_id, permissions):
        filename = "%s/%s.shutdown" % (CONF.ovz_config_dir, instance_id)
        filename = os.path.abspath(filename)
        super(FakeOVZShutdownFile, self).__init__(filename, permissions)


class FakeOVZBootFile(FakeOvzFile):
    def __init__(self, instance_id, permissions):
        filename = "%s/%s.shutdown" % (CONF.ovz_config_dir, instance_id)
        filename = os.path.abspath(filename)
        super(FakeOVZBootFile, self).__init__(filename, permissions)


class FakeOVZNetworkFile(FakeOvzFile):
    def __init__(self, filename):
        self.filename = filename
        super(FakeOVZNetworkFile, self).__init__(filename, 644)


class FakeOVZInstanceVolumeOps(object):
    def __init__(self, instance):
        self.instance = instance

    def attach_all_volumes(self):
        return

    def detach_all_volumes(self):
        return


class FakeOVZISCSIStorageDriver(object):
    def init_iscsi_device(self):
        return

    def disconnect_iscsi_volume(self):
        return

    def discover_volume(self):
        return

    def setup(self):
        return

    def prepare_filesystem(self):
        return

    def attach(self):
        return

    def detach(self, container_is_running=True):
        return

    def write_and_close(self):
        return


class FakeAggregate(object):
    def __init__(self):
        self.id = 1


class FakePosixStatVFSResult(object):
    def __init__(self):
        self.f_frsize = 4096
        self.f_blocks = 61005206
        self.f_bavail = 58342965
        self.f_bfree = 58342965


METAKEY = 'tc_id'
METAVALUE = '1002'
METADATA = {METAKEY: METAVALUE}

STATVFSRESULT = FakePosixStatVFSResult()

AGGREGATE = FakeAggregate()

ROOTPASS = '2s3cUr3'

USER = {'user': 'admin', 'role': 'admin', 'id': 1}

PROJECT = {'name': 'test', 'id': 2}

ADMINCONTEXT = AdminContext()

CONTEXT = Context()

DESTINATION = '127.0.7.1'

BDM = {
    'block_device_mapping': [
        {
            'connection_info': {
                'data': {
                    'volume_id': 'c49a7247-731e-4135-8420-7a3c67002582'
                },
                'driver_volume_type': 'iscsi',
                'mount_device': '/dev/sdgg'
            },
            'mount_device': '/dev/sdgg'
        }
    ]
}

INSTANCETYPE = {
    'id': 1,
    'vcpus': 1,
    'name': 'm1.small',
    'memory_mb': 2048,
    'swap': 0,
    'root_gb': 20
}

INSTANCE = {
    "image_ref": 1,
    "name": "instance-00001002",
    "instance_type_id": 1,
    "id": 1002,
    "uuid": "07fd1fc9-eb75-4375-88d5-6247ce2fb7e4",
    "hostname": "test.foo.com",
    "power_state": power_state.RUNNING,
    "admin_pass": ROOTPASS,
    "user_id": USER['id'],
    "project_id": PROJECT['id'],
    "memory_mb": INSTANCETYPE['memory_mb'],
    "block_device_mapping": BDM,
    "system_metadata": [
        {'key': METAKEY, 'value': METAVALUE},
        {'key': 'instance_type_name', 'value': INSTANCETYPE['name']},
        {'key': 'instance_type_root_gb', 'value': INSTANCETYPE['root_gb']},
        {'key': 'instance_type_memory_mb', 'value': INSTANCETYPE['memory_mb']},
        {'key': 'instance_type_vcpus', 'value': INSTANCETYPE['vcpus']}
    ]
}

BLKID = '0670a412-bba5-4ef4-a954-7fec8f2a06aa\n'

IMAGEPATH = '%s/%s.tar.gz' % \
            (CONF.ovz_image_template_dir, INSTANCE['image_ref'])

INSTANCES = [INSTANCE, INSTANCE]

RES_PERCENT = .50

RES_OVER_PERCENT = 1.50

VZLIST = "\t1001\n\t%d\n\t1003\n\t1004\n" % (INSTANCE['id'],)

VZLISTDETAIL = "        %d         running   %s" \
               % (INSTANCE['id'], INSTANCE['hostname'])

FINDBYNAME = VZLISTDETAIL.split()
FINDBYNAME = {'name': FINDBYNAME[2], 'id': int(FINDBYNAME[0]),
              'state': FINDBYNAME[1]}
FINDBYNAMENOSTATE = VZLISTDETAIL.split()
FINDBYNAMENOSTATE = {
    'name': FINDBYNAMENOSTATE[2], 'id': int(FINDBYNAMENOSTATE[0]),
    'state': '-'}
FINDBYNAMESHUTDOWN = VZLISTDETAIL.split()
FINDBYNAMESHUTDOWN = {
    'name': FINDBYNAMESHUTDOWN[2], 'id': int(FINDBYNAMESHUTDOWN[0]),
    'state': 'stopped'}

VZNAME = """\tinstance-00001001\n"""

VZNAMES = """\tinstance-00001001\n\t%s
              \tinstance-00001003\n\tinstance-00001004\n""" % (
    INSTANCE['name'],)

GOODSTATUS = {
    'state': power_state.RUNNING,
    'max_mem': 0,
    'mem': 0,
    'num_cpu': 0,
    'cpu_time': 0
}

NOSTATUS = {
    'state': power_state.NOSTATE,
    'max_mem': 0,
    'mem': 0,
    'num_cpu': 0,
    'cpu_time': 0
}

SHUTDOWNSTATUS = {
    'state': power_state.SHUTDOWN,
    'max_mem': 0,
    'mem': 0,
    'num_cpu': 0,
    'cpu_time': 0
}

ERRORMSG = "vz command ran but output something to stderr"

MEMINFO = """MemTotal:         506128 kB
MemFree:          291992 kB
Buffers:           44512 kB
Cached:            64708 kB
SwapCached:            0 kB
Active:           106496 kB
Inactive:          62948 kB
Active(anon):      62108 kB
Inactive(anon):      496 kB
Active(file):      44388 kB
Inactive(file):    62452 kB
Unevictable:        2648 kB
Mlocked:            2648 kB
SwapTotal:       1477624 kB
SwapFree:        1477624 kB
Dirty:                 0 kB
Writeback:             0 kB
AnonPages:         62908 kB
Mapped:            14832 kB
Shmem:               552 kB
Slab:              27988 kB
SReclaimable:      17280 kB
SUnreclaim:        10708 kB
KernelStack:        1448 kB
PageTables:         3092 kB
NFS_Unstable:          0 kB
Bounce:                0 kB
WritebackTmp:          0 kB
CommitLimit:     1730688 kB
Committed_AS:     654760 kB
VmallocTotal:   34359738367 kB
VmallocUsed:       24124 kB
VmallocChunk:   34359711220 kB
HardwareCorrupted:     0 kB
HugePages_Total:       0
HugePages_Free:        0
HugePages_Rsvd:        0
HugePages_Surp:        0
Hugepagesize:       2048 kB
DirectMap4k:        8128 kB
DirectMap2M:      516096 kB
"""

PROCINFO = """
processor	: 0
vendor_id	: GenuineIntel
cpu family	: 6
model		: 58
model name	: Intel(R) Core(TM) i7-3610QM CPU @ 2.30GHz
stepping	: 9
microcode	: 0x13
cpu MHz		: 1200.000
cache size	: 6144 KB
physical id	: 0
siblings	: 8
core id		: 0
cpu cores	: 4
apicid		: 0
initial apicid	: 0
fpu		: yes
fpu_exception	: yes
cpuid level	: 13
wp		: yes
flags		: fpu vme de pse tsc msr pae mce cx8 apic sep mtrr pge mca
bogomips	: 4589.55
clflush size	: 64
cache_alignment	: 64
address sizes	: 36 bits physical, 48 bits virtual
power management:

processor	: 1
vendor_id	: GenuineIntel
cpu family	: 6
model		: 58
model name	: Intel(R) Core(TM) i7-3610QM CPU @ 2.30GHz
stepping	: 9
microcode	: 0x13
cpu MHz		: 1200.000
cache size	: 6144 KB
physical id	: 0
siblings	: 8
core id		: 0
cpu cores	: 4
apicid		: 1
initial apicid	: 1
fpu		: yes
fpu_exception	: yes
cpuid level	: 13
wp		: yes
flags		: fpu vme de pse tsc msr pae mce cx8 apic sep mtrr pge mca
bogomips	: 4589.36
clflush size	: 64
cache_alignment	: 64
address sizes	: 36 bits physical, 48 bits virtual
power management:

processor	: 2
vendor_id	: GenuineIntel
cpu family	: 6
model		: 58
model name	: Intel(R) Core(TM) i7-3610QM CPU @ 2.30GHz
stepping	: 9
microcode	: 0x13
cpu MHz		: 1200.000
cache size	: 6144 KB
physical id	: 0
siblings	: 8
core id		: 1
cpu cores	: 4
apicid		: 2
initial apicid	: 2
fpu		: yes
fpu_exception	: yes
cpuid level	: 13
wp		: yes
flags		: fpu vme de pse tsc msr pae mce cx8 apic sep mtrr pge mca
bogomips	: 4589.36
clflush size	: 64
cache_alignment	: 64
address sizes	: 36 bits physical, 48 bits virtual
power management:

processor	: 3
vendor_id	: GenuineIntel
cpu family	: 6
model		: 58
model name	: Intel(R) Core(TM) i7-3610QM CPU @ 2.30GHz
stepping	: 9
microcode	: 0x13
cpu MHz		: 1200.000
cache size	: 6144 KB
physical id	: 0
siblings	: 8
core id		: 1
cpu cores	: 4
apicid		: 3
initial apicid	: 3
fpu		: yes
fpu_exception	: yes
cpuid level	: 13
wp		: yes
flags		: fpu vme de pse tsc msr pae mce cx8 apic sep mtrr pge mca
bogomips	: 4589.36
clflush size	: 64
cache_alignment	: 64
address sizes	: 36 bits physical, 48 bits virtual
power management:

processor	: 4
vendor_id	: GenuineIntel
cpu family	: 6
model		: 58
model name	: Intel(R) Core(TM) i7-3610QM CPU @ 2.30GHz
stepping	: 9
microcode	: 0x13
cpu MHz		: 1200.000
cache size	: 6144 KB
physical id	: 0
siblings	: 8
core id		: 2
cpu cores	: 4
apicid		: 4
initial apicid	: 4
fpu		: yes
fpu_exception	: yes
cpuid level	: 13
wp		: yes
flags		: fpu vme de pse tsc msr pae mce cx8 apic sep mtrr pge mca
bogomips	: 4589.37
clflush size	: 64
cache_alignment	: 64
address sizes	: 36 bits physical, 48 bits virtual
power management:

processor	: 5
vendor_id	: GenuineIntel
cpu family	: 6
model		: 58
model name	: Intel(R) Core(TM) i7-3610QM CPU @ 2.30GHz
stepping	: 9
microcode	: 0x13
cpu MHz		: 1200.000
cache size	: 6144 KB
physical id	: 0
siblings	: 8
core id		: 2
cpu cores	: 4
apicid		: 5
initial apicid	: 5
fpu		: yes
fpu_exception	: yes
cpuid level	: 13
wp		: yes
flags		: fpu vme de pse tsc msr pae mce cx8 apic sep mtrr pge mca
bogomips	: 4589.36
clflush size	: 64
cache_alignment	: 64
address sizes	: 36 bits physical, 48 bits virtual
power management:

processor	: 6
vendor_id	: GenuineIntel
cpu family	: 6
model		: 58
model name	: Intel(R) Core(TM) i7-3610QM CPU @ 2.30GHz
stepping	: 9
microcode	: 0x13
cpu MHz		: 1200.000
cache size	: 6144 KB
physical id	: 0
siblings	: 8
core id		: 3
cpu cores	: 4
apicid		: 6
initial apicid	: 6
fpu		: yes
fpu_exception	: yes
cpuid level	: 13
wp		: yes
flags		: fpu vme de pse tsc msr pae mce cx8 apic sep mtrr pge mca
bogomips	: 4589.37
clflush size	: 64
cache_alignment	: 64
address sizes	: 36 bits physical, 48 bits virtual
power management:

processor	: 7
vendor_id	: GenuineIntel
cpu family	: 6
model		: 58
model name	: Intel(R) Core(TM) i7-3610QM CPU @ 2.30GHz
stepping	: 9
microcode	: 0x13
cpu MHz		: 1200.000
cache size	: 6144 KB
physical id	: 0
siblings	: 8
core id		: 3
cpu cores	: 4
apicid		: 7
initial apicid	: 7
fpu		: yes
fpu_exception	: yes
cpuid level	: 13
wp		: yes
flags		: fpu vme de pse tsc msr pae mce cx8 apic sep mtrr pge mca
bogomips	: 4589.37
clflush size	: 64
cache_alignment	: 64
address sizes	: 36 bits physical, 48 bits virtual
power management:
"""

UTILITY = {
    'CTIDS': {
        1: {

        }
    },
    'UTILITY': 10000,
    'TOTAL': 1000,
    'UNITS': 100000,
    'MEMORY_MB': 512000,
    'CPULIMIT': 2400
}

CPUUNITSCAPA = {
    'total': 500000,
    'subscribed': 1000
}

CPUCHECKCONT = """VEID      CPUUNITS
-------------------------
0       1000
26      25000
27      25000
Current CPU utilization: 51000
Power of the node: 758432
"""

CPUCHECKNOCONT = """Current CPU utilization: 51000
Power of the node: 758432
"""

FILECONTENTS = """mount UUID=FEE52433-F693-448E-B6F6-AA6D0124118B /mnt/foo
        mount --bind /mnt/foo /vz/private/1/mnt/foo
        """


INTERFACEINFO = [
    {
        'id': 1002,
        'interface_number': 0,
        'bridge': 'br100',
        'name': 'eth0',
        'vz_host_if': 'veth1002.eth0',
        'mac': '02:16:3e:0c:2c:08',
        'address': '10.0.2.16',
        'netmask': '255.255.255.0',
        'gateway': '10.0.2.2',
        'broadcast': '10.0.2.255',
        'dns': '192.168.2.1',
        'address_v6': None,
        'gateway_v6': None,
        'netmask_v6': None
    },
    {
        'id': 1002,
        'interface_number': 1,
        'bridge': 'br200',
        'name': 'eth1',
        'vz_host_if': 'veth1002.eth1',
        'mac': '02:16:3e:40:5e:1b',
        'address': '10.0.4.16',
        'netmask': '255.255.255.0',
        'gateway': '10.0.2.2',
        'broadcast': '10.0.4.255',
        'dns': '192.168.2.1',
        'address_v6': None,
        'gateway_v6': None,
        'netmask_v6': None
    }
]

TEMPFILE = '/tmp/foo/file'

NETTEMPLATE = """
    # This file describes the network interfaces available on your system
    # and how to activate them. For more information, see interfaces(5).

    # The loopback network interface
    auto lo
    iface lo inet loopback

    #for $ifc in $interfaces
    auto ${ifc.name}
    iface ${ifc.name} inet static
            address ${ifc.address}
            netmask ${ifc.netmask}
            broadcast ${ifc.broadcast}
            gateway ${ifc.gateway}
            dns-nameservers ${ifc.dns}

    #if $use_ipv6
    iface ${ifc.name} inet6 static
        address ${ifc.address_v6}
        netmask ${ifc.netmask_v6}
        gateway ${ifc.gateway_v6}
    #end if

    #end for
    """

HOSTSTATS = {
    'vcpus': 12,
    'vcpus_used': 2,
    'cpu_info': json.dumps(PROCINFO),
    'memory_mb': 36864,
    'memory_mb_used': 2048,
    'host_memory_total': 36864,
    'host_memory_free': 34816,
    'disk_total': 232,
    'disk_used': 10,
    'disk_available': 222,
    'local_gb': 232,
    'local_gb_used': 10,
    'hypervisor_type': ovz_utils.get_hypervisor_type(),
    'hypervisor_version': '3.2.0-31-generic',
    'hypervisor_hostname': socket.gethostname()
}

FILESTOINJECT = [
    ['/tmp/testfile1', FILECONTENTS],
    ['/tmp/testfile2', FILECONTENTS]
]

OSLISTDIR = ['1002.start', '1002.stop']

INITIATORNAME = 'iqn.1993-08.org.debian:01:f424a54e43'

ISCSIINITIATOR = """## DO NOT EDIT OR REMOVE THIS FILE!
## If you remove this file, the iSCSI daemon will not start.
## If you change the InitiatorName, existing access control lists
## may reject this initiator.  The InitiatorName must be unique
## for each iSCSI initiator.  Do NOT duplicate iSCSI InitiatorNames.
InitiatorName=%s
""" % INITIATORNAME

PRIVVMPAGES_2048 = "%s     524288\n" % INSTANCE['id']
PRIVVMPAGES_1024 = "1003     262144\n"
PRIVVMPAGES = PRIVVMPAGES_2048 + PRIVVMPAGES_1024

UNAME = ('Linux', 'imsplitbit-M17xR4', '3.2.0-31-generic',
         '#50-Ubuntu SMP Fri Sep 7 16:16:45 UTC 2012', 'x86_64', 'x86_64')
