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

import base64
import json
import mox
from nova.compute import manager
from nova.compute import power_state
from nova import exception
from nova.openstack.common import processutils
from nova import test
from nova.tests.openvz import fakes
from nova.virt.openvz import driver as openvz_conn
from nova.virt.openvz.file_ext import ext_storage
from nova.virt.openvz import migration
from nova.virt.openvz import utils as ovz_utils
import os
from oslo.config import cfg

CONF = cfg.CONF


class OpenVzDriverTestCase(test.TestCase):
    def setUp(self):
        super(OpenVzDriverTestCase, self).setUp()
        try:
            CONF.injected_network_template
        except AttributeError:
            CONF.register_opt(
                cfg.StrOpt('injected_network_template',
                           default='nova/virt/interfaces.template',
                           help='Stub for network template '
                                'for testing purposes'))
        CONF.use_ipv6 = False
        self.ext_str_filename = '%s/%s.ext_storage' % (CONF.ovz_config_dir,
                                                       fakes.INSTANCE['id'])
        self.ext_str_filename = os.path.abspath(self.ext_str_filename)
        self.ext_str_permissions = 600

    def test_legacy_nwinfo(self):
        ovz_conn = openvz_conn.OpenVzDriver(
            manager.ComputeVirtAPI(None), False)
        self.assertTrue(ovz_conn.legacy_nwinfo())

    def test_start_success(self):
        # Testing happy path :-D
        # Mock the objects needed for this test to succeed.
        self.mox.StubOutWithMock(openvz_conn.ovzboot, 'OVZBootFile')
        openvz_conn.ovzboot.OVZBootFile(
            fakes.INSTANCE['id'], mox.IgnoreArg()).AndReturn(
                fakes.FakeOVZBootFile(fakes.INSTANCE['id'], 700))
        self.mox.StubOutWithMock(ovz_utils.utils, 'execute')
        ovz_utils.utils.execute(
            'vzctl', 'start', fakes.INSTANCE['id'],
            run_as_root=True).AndReturn(
                ('', fakes.ERRORMSG))
        conn = openvz_conn.OpenVzDriver(manager.ComputeVirtAPI(None), False)
        self.mox.StubOutWithMock(conn.virtapi, 'instance_update')
        conn.virtapi.instance_update(
            mox.IgnoreArg(), fakes.INSTANCE['uuid'],
            {'power_state': power_state.RUNNING})
        # Start the tests
        self.mox.ReplayAll()
        # Create our connection object.  For all intents and purposes this is
        # a real OpenVzDriver object.
        conn._start(fakes.INSTANCE)

    def test_start_fail(self):
        self.mox.StubOutWithMock(ovz_utils.utils, 'execute')
        ovz_utils.utils.execute(
            'vzctl', 'start', fakes.INSTANCE['id'],
            run_as_root=True).AndRaise(
                exception.InstanceUnacceptable(fakes.ERRORMSG))
        self.mox.ReplayAll()

        conn = openvz_conn.OpenVzDriver(manager.ComputeVirtAPI(None), False)
        self.assertRaises(
            exception.InstanceUnacceptable, conn._start, fakes.INSTANCE)

    def test_list_instances_success(self):
        # Testing happy path of OpenVzDriver.list_instances()
        self.mox.StubOutWithMock(ovz_utils.utils, 'execute')
        ovz_utils.utils.execute(
            'vzlist', '--all', '--no-header', '--output', 'ctid',
            run_as_root=True).AndReturn(
                (fakes.VZLIST, fakes.ERRORMSG))
        # Start test
        self.mox.ReplayAll()
        conn = openvz_conn.OpenVzDriver(manager.ComputeVirtAPI(None), False)
        vzs = conn.list_instances()
        self.assertEqual(vzs.__class__, list)

    def test_list_instances_fail(self):
        self.mox.StubOutWithMock(ovz_utils.utils, 'execute')
        ovz_utils.utils.execute(
            'vzlist', '--all', '--no-header', '--output', 'ctid',
            run_as_root=True).AndRaise(
                exception.InstanceUnacceptable(fakes.ERRORMSG))
        # Start test
        self.mox.ReplayAll()
        conn = openvz_conn.OpenVzDriver(manager.ComputeVirtAPI(None), False)
        self.assertRaises(exception.InstanceUnacceptable, conn.list_instances)

    def test_create_vz_success(self):
        self.mox.StubOutWithMock(ovz_utils.utils, 'execute')
        ovz_utils.utils.execute(
            'vzctl', 'create', fakes.INSTANCE['id'], '--ostemplate',
            fakes.INSTANCE['image_ref'], run_as_root=True).AndReturn(
                ('', fakes.ERRORMSG))
        self.mox.ReplayAll()
        conn = openvz_conn.OpenVzDriver(manager.ComputeVirtAPI(None), False)
        conn._create_vz(fakes.INSTANCE)

    def test_create_vz_fail(self):
        self.mox.StubOutWithMock(ovz_utils.utils, 'execute')
        ovz_utils.utils.execute(
            'vzctl', 'create', fakes.INSTANCE['id'], '--ostemplate',
            fakes.INSTANCE['image_ref'], run_as_root=True).AndRaise(
                exception.InstanceUnacceptable(fakes.ERRORMSG))
        self.mox.ReplayAll()
        conn = openvz_conn.OpenVzDriver(manager.ComputeVirtAPI(None), False)
        self.assertRaises(
            exception.InstanceUnacceptable, conn._create_vz, fakes.INSTANCE)

    def test_set_vz_os_hint_success(self):
        self.mox.StubOutWithMock(ovz_utils.utils, 'execute')
        ovz_utils.utils.execute(
            'vzctl', 'set', fakes.INSTANCE['id'], '--save', '--ostemplate',
            'ubuntu', run_as_root=True).AndReturn(
                ('', fakes.ERRORMSG))
        self.mox.ReplayAll()
        conn = openvz_conn.OpenVzDriver(manager.ComputeVirtAPI(None), False)
        conn._set_vz_os_hint(fakes.INSTANCE)

    def test_set_vz_os_hint_failure(self):
        self.mox.StubOutWithMock(ovz_utils.utils, 'execute')
        ovz_utils.utils.execute(
            'vzctl', 'set', fakes.INSTANCE['id'], '--save', '--ostemplate',
            'ubuntu', run_as_root=True).AndRaise(
                exception.InstanceUnacceptable(fakes.ERRORMSG))
        self.mox.ReplayAll()
        conn = openvz_conn.OpenVzDriver(manager.ComputeVirtAPI(None), False)
        self.assertRaises(
            exception.InstanceUnacceptable, conn._set_vz_os_hint,
            fakes.INSTANCE)

    def test_configure_vz_success(self):
        self.mox.StubOutWithMock(ovz_utils.utils, 'execute')
        ovz_utils.utils.execute(
            'vzctl', 'set', fakes.INSTANCE['id'], '--save', '--applyconfig',
            'basic', run_as_root=True).AndReturn(
                ('', fakes.ERRORMSG))
        self.mox.ReplayAll()
        conn = openvz_conn.OpenVzDriver(manager.ComputeVirtAPI(None), False)
        conn._configure_vz(fakes.INSTANCE)

    def test_configure_vz_failure(self):
        self.mox.StubOutWithMock(ovz_utils.utils, 'execute')
        ovz_utils.utils.execute(
            'vzctl', 'set', fakes.INSTANCE['id'], '--save', '--applyconfig',
            'basic', run_as_root=True).AndRaise(
                exception.InstanceUnacceptable(fakes.ERRORMSG))
        self.mox.ReplayAll()
        conn = openvz_conn.OpenVzDriver(manager.ComputeVirtAPI(None), False)
        self.assertRaises(
            exception.InstanceUnacceptable, conn._configure_vz, fakes.INSTANCE)

    def test_stop_success(self):
        self.mox.StubOutWithMock(openvz_conn.ovzshutdown, 'OVZShutdownFile')
        openvz_conn.ovzshutdown.OVZShutdownFile(
            fakes.INSTANCE['id'], mox.IgnoreArg()).AndReturn(
                fakes.FakeOVZShutdownFile(fakes.INSTANCE['id'], 700))
        self.mox.StubOutWithMock(ovz_utils.utils, 'execute')
        ovz_utils.utils.execute(
            'vzctl', 'stop', fakes.INSTANCE['id'],
            run_as_root=True).AndReturn(
                ('', fakes.ERRORMSG))
        conn = openvz_conn.OpenVzDriver(manager.ComputeVirtAPI(None), False)
        self.mox.StubOutWithMock(conn.virtapi, 'instance_update')
        conn.virtapi.instance_update(
            mox.IgnoreArg(), fakes.INSTANCE['uuid'],
            {'power_state': power_state.SHUTDOWN})
        self.mox.ReplayAll()
        conn._stop(fakes.INSTANCE)

    def test_stop_failure_on_exec(self):
        self.mox.StubOutWithMock(openvz_conn.ovzshutdown, 'OVZShutdownFile')
        openvz_conn.ovzshutdown.OVZShutdownFile(
            fakes.INSTANCE['id'], mox.IgnoreArg()).AndReturn(
                fakes.FakeOVZShutdownFile(fakes.INSTANCE['id'], 700))
        self.mox.StubOutWithMock(ovz_utils.utils, 'execute')
        ovz_utils.utils.execute(
            'vzctl', 'stop', fakes.INSTANCE['id'], run_as_root=True).AndRaise(
                exception.InstanceUnacceptable(fakes.ERRORMSG))
        self.mox.ReplayAll()
        conn = openvz_conn.OpenVzDriver(manager.ComputeVirtAPI(None), False)
        self.assertRaises(
            exception.InstanceUnacceptable, conn._stop, fakes.INSTANCE)

    def test_stop_failure_on_db_access(self):
        self.mox.StubOutWithMock(openvz_conn.ovzshutdown, 'OVZShutdownFile')
        openvz_conn.ovzshutdown.OVZShutdownFile(
            fakes.INSTANCE['id'], mox.IgnoreArg()).AndReturn(
                fakes.FakeOVZShutdownFile(fakes.INSTANCE['id'], 700))
        self.mox.StubOutWithMock(ovz_utils.utils, 'execute')
        ovz_utils.utils.execute(
            'vzctl', 'stop', fakes.INSTANCE['id'],
            run_as_root=True).AndReturn(('', fakes.ERRORMSG))
        conn = openvz_conn.OpenVzDriver(manager.ComputeVirtAPI(None), False)
        self.mox.StubOutWithMock(conn.virtapi, 'instance_update')
        conn.virtapi.instance_update(
            mox.IgnoreArg(), fakes.INSTANCE['uuid'],
            {'power_state': power_state.SHUTDOWN}).AndRaise(
                exception.InstanceNotFound('FAIL'))
        self.mox.ReplayAll()
        self.assertRaises(
            exception.InstanceNotFound, conn._stop, fakes.INSTANCE)

    def test_set_vmguarpages_success(self):
        self.mox.StubOutWithMock(ovz_utils.utils, 'execute')
        ovz_utils.utils.execute(
            'vzctl', 'set', fakes.INSTANCE['id'], '--save', '--vmguarpages',
            mox.IgnoreArg(), run_as_root=True).AndReturn(('', fakes.ERRORMSG))
        self.mox.ReplayAll()
        conn = openvz_conn.OpenVzDriver(manager.ComputeVirtAPI(None), False)
        conn._set_vmguarpages(
            fakes.INSTANCE, conn._calc_pages(fakes.INSTANCE['memory_mb']))

    def test_set_vmguarpages_failure(self):
        self.mox.StubOutWithMock(ovz_utils.utils, 'execute')
        ovz_utils.utils.execute(
            'vzctl', 'set', fakes.INSTANCE['id'], '--save', '--vmguarpages',
            mox.IgnoreArg(), run_as_root=True).AndRaise(
                exception.InstanceUnacceptable(fakes.ERRORMSG))
        self.mox.ReplayAll()
        conn = openvz_conn.OpenVzDriver(manager.ComputeVirtAPI(None), False)
        self.assertRaises(
            exception.InstanceUnacceptable, conn._set_vmguarpages,
            fakes.INSTANCE, conn._calc_pages(fakes.INSTANCE['memory_mb']))

    def test_set_privvmpages_success(self):
        self.mox.StubOutWithMock(ovz_utils.utils, 'execute')
        ovz_utils.utils.execute(
            'vzctl', 'set', fakes.INSTANCE['id'], '--save', '--privvmpages',
            mox.IgnoreArg(), run_as_root=True).AndReturn(('', fakes.ERRORMSG))
        self.mox.ReplayAll()
        conn = openvz_conn.OpenVzDriver(manager.ComputeVirtAPI(None), False)
        conn._set_privvmpages(
            fakes.INSTANCE, conn._calc_pages(fakes.INSTANCE['memory_mb']))

    def test_set_privvmpages_failure(self):
        self.mox.StubOutWithMock(ovz_utils.utils, 'execute')
        ovz_utils.utils.execute(
            'vzctl', 'set', fakes.INSTANCE['id'], '--save', '--privvmpages',
            mox.IgnoreArg(), run_as_root=True).AndRaise(
                exception.InstanceUnacceptable(fakes.ERRORMSG))
        self.mox.ReplayAll()
        conn = openvz_conn.OpenVzDriver(manager.ComputeVirtAPI(None), False)
        self.assertRaises(
            exception.InstanceUnacceptable, conn._set_privvmpages,
            fakes.INSTANCE, conn._calc_pages(fakes.INSTANCE['memory_mb']))

    def test_set_kmemsize_success(self):
        kmemsize = ((fakes.INSTANCE['memory_mb'] * 1024) * 1024)
        self.mox.StubOutWithMock(ovz_utils.utils, 'execute')
        ovz_utils.utils.execute(
            'vzctl', 'set', fakes.INSTANCE['id'], '--save', '--kmemsize',
            mox.IgnoreArg(), run_as_root=True).AndReturn(('', ''))
        self.mox.ReplayAll()
        conn = openvz_conn.OpenVzDriver(manager.ComputeVirtAPI(None), False)
        conn._set_kmemsize(fakes.INSTANCE, kmemsize)

    def test_set_kmemsize_failure(self):
        kmemsize = ((fakes.INSTANCE['memory_mb'] * 1024) * 1024)
        self.mox.StubOutWithMock(ovz_utils.utils, 'execute')
        ovz_utils.utils.execute(
            'vzctl', 'set', fakes.INSTANCE['id'], '--save', '--kmemsize',
            mox.IgnoreArg(), run_as_root=True).AndRaise(
                exception.InstanceUnacceptable(fakes.ERRORMSG))
        self.mox.ReplayAll()
        conn = openvz_conn.OpenVzDriver(manager.ComputeVirtAPI(None), False)
        self.assertRaises(
            exception.InstanceUnacceptable, conn._set_kmemsize, fakes.INSTANCE,
            kmemsize)

    def test_cache_image_path_doesnt_exist(self):
        self.mox.StubOutWithMock(openvz_conn.os.path, 'exists')
        openvz_conn.os.path.exists(mox.IgnoreArg()).AndReturn(False)
        self.mox.StubOutWithMock(openvz_conn.images, 'fetch')
        openvz_conn.images.fetch(
            fakes.ADMINCONTEXT, fakes.INSTANCE['image_ref'], fakes.IMAGEPATH,
            fakes.INSTANCE['user_id'], fakes.INSTANCE['project_id'])
        self.mox.ReplayAll()
        ovz_conn = openvz_conn.OpenVzDriver(
            manager.ComputeVirtAPI(None), False)
        self.assertTrue(
            ovz_conn._cache_image(fakes.ADMINCONTEXT, fakes.INSTANCE))

    def test_cache_image_path_exists(self):
        self.mox.StubOutWithMock(openvz_conn.os.path, 'exists')
        openvz_conn.os.path.exists(mox.IgnoreArg()).AndReturn(True)
        self.mox.ReplayAll()
        ovz_conn = openvz_conn.OpenVzDriver(
            manager.ComputeVirtAPI(None), False)
        self.assertFalse(
            ovz_conn._cache_image(fakes.ADMINCONTEXT, fakes.INSTANCE))

    def test_access_control_allow(self):
        self.mox.StubOutWithMock(
            openvz_conn.linux_net.iptables_manager, 'apply')
        openvz_conn.linux_net.iptables_manager.apply().MultipleTimes()
        self.mox.ReplayAll()
        ovz_conn = openvz_conn.OpenVzDriver(
            manager.ComputeVirtAPI(None), False)
        ovz_conn._initial_secure_host(fakes.INSTANCE)
        ovz_conn._access_control(fakes.INSTANCE, '1.1.1.1')

    def test_access_control_deny_and_port(self):
        self.mox.StubOutWithMock(
            openvz_conn.linux_net.iptables_manager, 'apply')
        openvz_conn.linux_net.iptables_manager.apply().MultipleTimes()
        self.mox.ReplayAll()
        ovz_conn = openvz_conn.OpenVzDriver(
            manager.ComputeVirtAPI(None), False)
        ovz_conn._initial_secure_host(fakes.INSTANCE)
        ovz_conn._access_control(
            fakes.INSTANCE, '1.1.1.1', port=22, access_type='deny')

    def test_access_control_invalid_access_type(self):
        ovz_conn = openvz_conn.OpenVzDriver(
            manager.ComputeVirtAPI(None), False)
        self.assertRaises(
            exception.InvalidInput, ovz_conn._access_control, fakes.INSTANCE,
            '1.1.1.1', port=22, access_type='invalid')

    def test_reset_instance_size_success(self):
        ovz_conn = openvz_conn.OpenVzDriver(
            manager.ComputeVirtAPI(None), False)
        self.mox.StubOutWithMock(ovz_conn, '_set_instance_size')
        ovz_conn._set_instance_size(fakes.INSTANCE)
        self.mox.StubOutWithMock(ovz_conn, 'reboot')
        ovz_conn.reboot(fakes.INSTANCE, None, None, None, None)
        self.mox.ReplayAll()
        self.assertTrue(ovz_conn.reset_instance_size(fakes.INSTANCE, True))

    def test_reset_instance_size_failure(self):
        ovz_conn = openvz_conn.OpenVzDriver(
            manager.ComputeVirtAPI(None), False)
        self.mox.StubOutWithMock(ovz_conn, '_set_instance_size')
        ovz_conn._set_instance_size(fakes.INSTANCE).AndRaise(
            exception.InstanceUnacceptable(fakes.ERRORMSG))
        self.mox.ReplayAll()
        self.assertRaises(
            exception.InstanceUnacceptable, ovz_conn.reset_instance_size,
            fakes.INSTANCE, True)

    def test_set_numflock(self):
        instance_memory_mb = int(fakes.INSTANCETYPE['memory_mb'])
        memory_unit_size = int(CONF.ovz_memory_unit_size)
        max_fd_per_unit = int(CONF.ovz_file_descriptors_per_unit)
        max_fd = int(instance_memory_mb / memory_unit_size) * max_fd_per_unit
        self.mox.StubOutWithMock(ovz_utils.utils, 'execute')
        ovz_utils.utils.execute(
            'vzctl', 'set', fakes.INSTANCE['id'], '--save', '--numflock',
            max_fd, run_as_root=True).AndReturn(('', ''))
        self.mox.ReplayAll()
        ovz_conn = openvz_conn.OpenVzDriver(
            manager.ComputeVirtAPI(None), False)
        ovz_conn._set_numflock(fakes.INSTANCE, max_fd)

    def test_sum_numfiles(self):
        instance_memory_mb = int(fakes.INSTANCETYPE['memory_mb'])
        memory_unit_size = int(CONF.ovz_memory_unit_size)
        max_fd_per_unit = int(CONF.ovz_file_descriptors_per_unit)
        max_fd = int(instance_memory_mb / memory_unit_size) * max_fd_per_unit
        self.mox.StubOutWithMock(ovz_utils.utils, 'execute')
        ovz_utils.utils.execute(
            'vzctl', 'set', fakes.INSTANCE['id'], '--save', '--numfile',
            max_fd, run_as_root=True).AndReturn(('', ''))
        self.mox.ReplayAll()
        ovz_conn = openvz_conn.OpenVzDriver(
            manager.ComputeVirtAPI(None), False)
        ovz_conn._set_numfiles(fakes.INSTANCE, max_fd)

    def test_set_numtcpsock(self):
        instance_meta = ovz_utils.format_system_metadata(
            fakes.INSTANCE['system_metadata'])
        numtcpsock = json.loads(
            CONF.ovz_numtcpsock_map
        )[str(instance_meta['instance_type_memory_mb'])]
        self.mox.StubOutWithMock(openvz_conn.ovz_utils, 'execute')
        openvz_conn.ovz_utils.execute(
            'vzctl', 'set', fakes.INSTANCE['id'], '--save', '--numtcpsock',
            numtcpsock, run_as_root=True).AndReturn(('', ''))
        self.mox.ReplayAll()
        ovz_conn = openvz_conn.OpenVzDriver(
            manager.ComputeVirtAPI(None), False)
        ovz_conn._set_numtcpsock(
            fakes.INSTANCE, instance_meta['instance_type_memory_mb'])

    def test_set_numtcpsock_no_flag(self):
        instance_meta = ovz_utils.format_system_metadata(
            fakes.INSTANCE['system_metadata'])
        self.mox.StubOutWithMock(openvz_conn.ovz_utils, 'execute')
        openvz_conn.ovz_utils.execute(
            'vzctl', 'set', fakes.INSTANCE['id'], '--save', '--numtcpsock',
            CONF.ovz_numtcpsock_default, run_as_root=True).AndReturn(('', ''))
        self.mox.ReplayAll()
        ovz_conn = openvz_conn.OpenVzDriver(
            manager.ComputeVirtAPI(None), False)
        ovz_conn._set_numtcpsock(
            fakes.INSTANCE,
            (instance_meta['instance_type_memory_mb'] + 1))

    def test_set_instance_size_with_instance_type_id(self):
        instance_memory_bytes = ((int(fakes.INSTANCETYPE['memory_mb'])
                                  * 1024) * 1024)
        ovz_conn = openvz_conn.OpenVzDriver(
            manager.ComputeVirtAPI(None), False)
        pages = ovz_conn._calc_pages(fakes.INSTANCE['memory_mb'])
        memory_unit_size = int(CONF.ovz_memory_unit_size)
        max_fd_per_unit = int(CONF.ovz_file_descriptors_per_unit)
        max_fd = int(
            fakes.INSTANCE['memory_mb'] / memory_unit_size) * max_fd_per_unit
        self.mox.StubOutWithMock(ovz_conn, '_percent_of_resource')
        ovz_conn._percent_of_resource(
            fakes.INSTANCETYPE['memory_mb']).AndReturn(
                float(fakes.INSTANCE['memory_mb']) /
                float(fakes.HOSTSTATS['memory_mb']))
        self.mox.StubOutWithMock(ovz_conn, '_set_vmguarpages')
        ovz_conn._set_vmguarpages(
            fakes.INSTANCE, pages)
        self.mox.StubOutWithMock(ovz_conn, '_set_privvmpages')
        ovz_conn._set_privvmpages(fakes.INSTANCE, pages)
        self.mox.StubOutWithMock(ovz_conn, '_set_kmemsize')
        ovz_conn._set_kmemsize(fakes.INSTANCE, instance_memory_bytes)
        self.mox.StubOutWithMock(ovz_conn, '_set_numfiles')
        ovz_conn._set_numfiles(fakes.INSTANCE, max_fd)
        self.mox.StubOutWithMock(ovz_conn, '_set_numflock')
        ovz_conn._set_numflock(fakes.INSTANCE, max_fd)
        self.mox.StubOutWithMock(ovz_conn, '_set_cpuunits')
        ovz_conn._set_cpuunits(fakes.INSTANCE, mox.IgnoreArg())
        self.mox.StubOutWithMock(ovz_conn, '_set_cpulimit')
        ovz_conn._set_cpulimit(fakes.INSTANCE, mox.IgnoreArg())
        self.mox.StubOutWithMock(ovz_conn, '_set_cpus')
        ovz_conn._set_cpus(fakes.INSTANCE, fakes.INSTANCETYPE['vcpus'])
        self.mox.StubOutWithMock(ovz_conn, '_set_ioprio')
        ovz_conn._set_ioprio(
            fakes.INSTANCE, int(fakes.INSTANCETYPE['memory_mb']))
        self.mox.StubOutWithMock(ovz_conn, '_set_diskspace')
        ovz_conn._set_diskspace(fakes.INSTANCE, fakes.INSTANCETYPE['root_gb'])
        self.mox.StubOutWithMock(ovz_conn, '_generate_tc_rules')
        ovz_conn._generate_tc_rules(fakes.INSTANCE, fakes.NETWORKINFO, False)
        self.mox.ReplayAll()
        ovz_conn._set_instance_size(
            fakes.INSTANCE, fakes.NETWORKINFO)

    def test_set_instance_size_without_instance_type_id(self):
        ovz_conn = openvz_conn.OpenVzDriver(
            manager.ComputeVirtAPI(None), False)
        instance_memory_bytes = ((int(fakes.INSTANCETYPE['memory_mb'])
                                  * 1024) * 1024)
        pages = ovz_conn._calc_pages(fakes.INSTANCE['memory_mb'])
        memory_unit_size = int(CONF.ovz_memory_unit_size)
        max_fd_per_unit = int(CONF.ovz_file_descriptors_per_unit)
        max_fd = int(
            fakes.INSTANCE['memory_mb'] / memory_unit_size) * max_fd_per_unit
        self.mox.StubOutWithMock(ovz_conn, '_percent_of_resource')
        ovz_conn._percent_of_resource(
            fakes.INSTANCETYPE['memory_mb']).AndReturn(
                float(fakes.INSTANCE['memory_mb']) /
                float(fakes.HOSTSTATS['memory_mb']))
        self.mox.StubOutWithMock(ovz_conn, '_set_vmguarpages')
        ovz_conn._set_vmguarpages(
            fakes.INSTANCE, pages)
        self.mox.StubOutWithMock(ovz_conn, '_set_privvmpages')
        ovz_conn._set_privvmpages(fakes.INSTANCE, pages)
        self.mox.StubOutWithMock(ovz_conn, '_set_kmemsize')
        ovz_conn._set_kmemsize(fakes.INSTANCE, instance_memory_bytes)
        self.mox.StubOutWithMock(ovz_conn, '_set_numfiles')
        ovz_conn._set_numfiles(fakes.INSTANCE, max_fd)
        self.mox.StubOutWithMock(ovz_conn, '_set_numflock')
        ovz_conn._set_numflock(fakes.INSTANCE, max_fd)
        self.mox.StubOutWithMock(ovz_conn, '_set_cpuunits')
        ovz_conn._set_cpuunits(fakes.INSTANCE, mox.IgnoreArg())
        self.mox.StubOutWithMock(ovz_conn, '_set_cpulimit')
        ovz_conn._set_cpulimit(fakes.INSTANCE, mox.IgnoreArg())
        self.mox.StubOutWithMock(ovz_conn, '_set_cpus')
        ovz_conn._set_cpus(fakes.INSTANCE, fakes.INSTANCETYPE['vcpus'])
        self.mox.StubOutWithMock(ovz_conn, '_set_ioprio')
        ovz_conn._set_ioprio(
            fakes.INSTANCE, int(fakes.INSTANCETYPE['memory_mb']))
        self.mox.StubOutWithMock(ovz_conn, '_set_diskspace')
        ovz_conn._set_diskspace(
            fakes.INSTANCE,
            ovz_utils.format_system_metadata(
                fakes.INSTANCE['system_metadata'])['instance_type_root_gb'])
        self.mox.StubOutWithMock(ovz_conn, '_generate_tc_rules')
        ovz_conn._generate_tc_rules(fakes.INSTANCE, fakes.NETWORKINFO, False)
        self.mox.ReplayAll()
        ovz_conn._set_instance_size(
            fakes.INSTANCE, fakes.NETWORKINFO)

    def test_generate_tc_rules(self):
        self.mox.StubOutWithMock(openvz_conn.ovzboot, 'OVZBootFile')
        openvz_conn.ovzboot.OVZBootFile(
            mox.IgnoreArg(), mox.IgnoreArg()).AndReturn(
                fakes.FakeOVZBootFile(0, 700))
        self.mox.StubOutWithMock(ovz_utils, 'read_instance_metadata')
        ovz_utils.read_instance_metadata(
            fakes.INSTANCE['id']).AndReturn(fakes.METADATA)
        self.mox.StubOutWithMock(ovz_utils, 'remove_instance_metadata_key')
        ovz_utils.remove_instance_metadata_key(
            fakes.INSTANCE['id'], fakes.METAKEY)
        self.mox.StubOutWithMock(openvz_conn.ovzshutdown, 'OVZShutdownFile')
        openvz_conn.ovzshutdown.OVZShutdownFile(
            mox.IgnoreArg(), mox.IgnoreArg()).AndReturn(
                fakes.FakeOVZShutdownFile(0, 700))
        self.mox.StubOutWithMock(openvz_conn.ovztc, 'OVZTcRules')
        openvz_conn.ovztc.OVZTcRules().MultipleTimes().AndReturn(
            fakes.FakeOVZTcRules())
        ovz_conn = openvz_conn.OpenVzDriver(
            manager.ComputeVirtAPI(None), False)
        self.mox.ReplayAll()
        ovz_conn._generate_tc_rules(fakes.INSTANCE, fakes.NETWORKINFO)

    def test_set_onboot_success(self):
        self.mox.StubOutWithMock(ovz_utils.utils, 'execute')
        ovz_utils.utils.execute(
            'vzctl', 'set', fakes.INSTANCE['id'], '--onboot', 'no', '--save',
            run_as_root=True).AndReturn(('', ''))
        self.mox.ReplayAll()
        conn = openvz_conn.OpenVzDriver(manager.ComputeVirtAPI(None), False)
        conn._set_onboot(fakes.INSTANCE)

    def test_set_onboot_failure(self):
        self.mox.StubOutWithMock(ovz_utils.utils, 'execute')
        ovz_utils.utils.execute(
            'vzctl', 'set', fakes.INSTANCE['id'], '--onboot', 'no', '--save',
            run_as_root=True).AndRaise(
                exception.InstanceUnacceptable(
                    fakes.ERRORMSG))
        self.mox.ReplayAll()
        conn = openvz_conn.OpenVzDriver(manager.ComputeVirtAPI(None), False)
        self.assertRaises(
            exception.InstanceUnacceptable, conn._set_onboot, fakes.INSTANCE)

    def test_set_cpuunits_success(self):
        self.mox.StubOutWithMock(ovz_utils.utils, 'execute')
        ovz_utils.utils.execute(
            'vzctl', 'set', fakes.INSTANCE['id'], '--save', '--cpuunits',
            mox.IgnoreArg(), run_as_root=True).AndReturn(('', ''))
        conn = openvz_conn.OpenVzDriver(manager.ComputeVirtAPI(None), False)
        self.mox.StubOutWithMock(conn, '_percent_of_resource')
        conn._percent_of_resource(mox.IgnoreArg()).AndReturn(fakes.RES_PERCENT)
        self.mox.ReplayAll()
        conn._set_cpuunits(
            fakes.INSTANCE, conn._percent_of_resource(
                fakes.INSTANCETYPE['memory_mb'])
        )

    def test_set_cpuunits_over_subscribe(self):
        self.mox.StubOutWithMock(ovz_utils.utils, 'execute')
        ovz_utils.utils.execute(
            'vzctl', 'set', fakes.INSTANCE['id'], '--save', '--cpuunits',
            mox.IgnoreArg(), run_as_root=True).AndReturn(('', ''))
        conn = openvz_conn.OpenVzDriver(manager.ComputeVirtAPI(None), False)
        self.mox.StubOutWithMock(conn, '_percent_of_resource')
        conn._percent_of_resource(
            mox.IgnoreArg()).AndReturn(fakes.RES_OVER_PERCENT)
        self.mox.ReplayAll()
        conn._set_cpuunits(
            fakes.INSTANCE, conn._percent_of_resource(
                fakes.INSTANCETYPE['memory_mb'])
        )

    def test_set_cpuunits_failure(self):
        self.mox.StubOutWithMock(ovz_utils.utils, 'execute')
        ovz_utils.utils.execute(
            'vzctl', 'set', fakes.INSTANCE['id'], '--save', '--cpuunits',
            mox.IgnoreArg(), run_as_root=True).AndRaise(
                processutils.ProcessExecutionError(fakes.ERRORMSG))
        conn = openvz_conn.OpenVzDriver(manager.ComputeVirtAPI(None), False)
        self.mox.StubOutWithMock(conn, '_percent_of_resource')
        conn._percent_of_resource(mox.IgnoreArg()).AndReturn(fakes.RES_PERCENT)
        self.mox.ReplayAll()
        self.assertRaises(
            exception.InstanceUnacceptable, conn._set_cpuunits, fakes.INSTANCE,
            conn._percent_of_resource(fakes.INSTANCETYPE['memory_mb']))

    def test_set_cpulimit_success(self):
        self.mox.StubOutWithMock(ovz_utils.utils, 'execute')
        ovz_utils.utils.execute(
            'vzctl', 'set', fakes.INSTANCE['id'], '--save', '--cpulimit',
            fakes.UTILITY['CPULIMIT'] * fakes.RES_PERCENT,
            run_as_root=True).AndReturn(
                ('', fakes.ERRORMSG))
        conn = openvz_conn.OpenVzDriver(manager.ComputeVirtAPI(None), False)
        self.mox.StubOutWithMock(conn, '_percent_of_resource')
        conn._percent_of_resource(mox.IgnoreArg()).AndReturn(
            fakes.RES_PERCENT
        )
        self.mox.StubOutWithMock(conn, 'utility')
        conn.utility = fakes.UTILITY
        self.mox.ReplayAll()
        conn._set_cpulimit(
            fakes.INSTANCE, conn._percent_of_resource(
                fakes.INSTANCETYPE['memory_mb'])
        )

    def test_set_cpulimit_over_subscribe(self):
        self.mox.StubOutWithMock(ovz_utils.utils, 'execute')
        ovz_utils.utils.execute(
            'vzctl', 'set', fakes.INSTANCE['id'], '--save', '--cpulimit',
            fakes.UTILITY['CPULIMIT'], run_as_root=True).AndReturn(
                ('', fakes.ERRORMSG))
        conn = openvz_conn.OpenVzDriver(manager.ComputeVirtAPI(None), False)
        self.mox.StubOutWithMock(conn, '_percent_of_resource')
        conn._percent_of_resource(
            mox.IgnoreArg()).AndReturn(fakes.RES_OVER_PERCENT)
        self.mox.StubOutWithMock(conn, 'utility')
        conn.utility = fakes.UTILITY
        self.mox.ReplayAll()
        conn._set_cpulimit(
            fakes.INSTANCE, conn._percent_of_resource(
                fakes.INSTANCETYPE['memory_mb'])
        )

    def test_set_cpulimit_failure(self):
        self.mox.StubOutWithMock(ovz_utils.utils, 'execute')
        ovz_utils.utils.execute(
            'vzctl', 'set', fakes.INSTANCE['id'], '--save', '--cpulimit',
            fakes.UTILITY['CPULIMIT'] * fakes.RES_PERCENT,
            run_as_root=True).AndRaise(
                exception.InstanceUnacceptable(fakes.ERRORMSG))
        conn = openvz_conn.OpenVzDriver(manager.ComputeVirtAPI(None), False)
        self.mox.StubOutWithMock(conn, '_percent_of_resource')
        conn._percent_of_resource(mox.IgnoreArg()).AndReturn(fakes.RES_PERCENT)
        self.mox.StubOutWithMock(conn, 'utility')
        conn.utility = fakes.UTILITY
        self.mox.ReplayAll()
        self.assertRaises(
            exception.InstanceUnacceptable, conn._set_cpulimit, fakes.INSTANCE,
            conn._percent_of_resource(fakes.INSTANCETYPE['memory_mb']))

    def test_set_cpus_too_many_vcpus_success(self):
        conn = openvz_conn.OpenVzDriver(manager.ComputeVirtAPI(None), False)
        self.mox.StubOutWithMock(ovz_utils.utils, 'execute')
        ovz_utils.utils.execute(
            'vzctl', 'set', fakes.INSTANCE['id'], '--save', '--cpus',
            fakes.UTILITY['CPULIMIT'] / 100,
            run_as_root=True).AndReturn(
                ('', fakes.ERRORMSG))
        self.mox.StubOutWithMock(conn, 'utility')
        conn.utility = fakes.UTILITY
        self.mox.ReplayAll()
        conn._set_cpus(fakes.INSTANCE, fakes.HOSTSTATS['vcpus'] * 200)

    def test_set_cpus_success(self):
        conn = openvz_conn.OpenVzDriver(manager.ComputeVirtAPI(None), False)
        self.mox.StubOutWithMock(ovz_utils.utils, 'execute')
        ovz_utils.utils.execute(
            'vzctl', 'set', fakes.INSTANCE['id'], '--save', '--cpus',
            mox.IgnoreArg(), run_as_root=True).AndReturn(('', fakes.ERRORMSG))
        self.mox.StubOutWithMock(conn, 'utility')
        conn.utility = fakes.UTILITY
        self.mox.ReplayAll()
        conn._set_cpus(fakes.INSTANCE, fakes.INSTANCETYPE['vcpus'])

    def test_set_cpus_failure(self):
        conn = openvz_conn.OpenVzDriver(manager.ComputeVirtAPI(None), False)
        self.mox.StubOutWithMock(ovz_utils.utils, 'execute')
        ovz_utils.utils.execute(
            'vzctl', 'set', fakes.INSTANCE['id'], '--save', '--cpus',
            mox.IgnoreArg(), run_as_root=True).AndRaise(
                exception.InstanceUnacceptable(fakes.ERRORMSG))
        self.mox.ReplayAll()
        self.mox.StubOutWithMock(conn, 'utility')
        conn.utility = fakes.UTILITY
        self.assertRaises(
            exception.InstanceUnacceptable, conn._set_cpus, fakes.INSTANCE,
            fakes.INSTANCETYPE['vcpus'])

    def test_calc_pages_success(self):
        # this test is a little sketchy because it is testing the default
        # values of memory for instance type id 1.  if this value changes then
        # we will have a mismatch.

        # TODO(imsplitbit): make this work better.  This test is very brittle
        # because it relies on the default memory size for flavor 1 never
        # changing.  Need to fix this.
        conn = openvz_conn.OpenVzDriver(manager.ComputeVirtAPI(None), False)
        self.assertEqual(
            conn._calc_pages(fakes.INSTANCE['memory_mb']), 524288)

    def test_get_cpuunits_capability_success(self):
        self.mox.StubOutWithMock(ovz_utils.utils, 'execute')
        ovz_utils.utils.execute(
            'vzcpucheck', run_as_root=True).AndReturn(
                (fakes.CPUCHECKNOCONT, fakes.ERRORMSG))
        self.mox.ReplayAll()
        ovz_utils.get_cpuunits_capability()

    def test_get_cpuunits_capability_failure(self):
        self.mox.StubOutWithMock(ovz_utils.utils, 'execute')
        ovz_utils.utils.execute(
            'vzcpucheck', run_as_root=True).AndRaise(
                exception.InstanceUnacceptable(fakes.ERRORMSG))
        self.mox.ReplayAll()
        self.assertRaises(
            exception.InstanceUnacceptable, ovz_utils.get_cpuunits_capability)

    def test_get_cpuunits_usage_success(self):
        self.mox.StubOutWithMock(ovz_utils.utils, 'execute')
        ovz_utils.utils.execute(
            'vzcpucheck', '-v', run_as_root=True).AndReturn(
                (fakes.CPUCHECKCONT, fakes.ERRORMSG))
        self.mox.ReplayAll()
        conn = openvz_conn.OpenVzDriver(manager.ComputeVirtAPI(None), False)
        conn._get_cpuunits_usage()

    def test_get_cpuunits_usage_failure(self):
        self.mox.StubOutWithMock(ovz_utils.utils, 'execute')
        ovz_utils.utils.execute(
            'vzcpucheck', '-v', run_as_root=True).AndRaise(
                exception.InstanceUnacceptable(fakes.ERRORMSG))
        self.mox.ReplayAll()
        conn = openvz_conn.OpenVzDriver(manager.ComputeVirtAPI(None), False)
        self.assertRaises(
            exception.InstanceUnacceptable, conn._get_cpuunits_usage)

    def test_percent_of_resource(self):
        conn = openvz_conn.OpenVzDriver(manager.ComputeVirtAPI(None), False)
        self.mox.StubOutWithMock(conn, 'utility')
        conn.utility = fakes.UTILITY
        self.mox.ReplayAll()
        self.assertEqual(
            float, type(conn._percent_of_resource(fakes.INSTANCE['memory_mb']))
        )

    def test_set_ioprio_success(self):
        self.mox.StubOutWithMock(ovz_utils.utils, 'execute')
        ovz_utils.utils.execute(
            'vzctl', 'set', fakes.INSTANCE['id'], '--save', '--ioprio',
            mox.IgnoreArg(), run_as_root=True).AndReturn(('', fakes.ERRORMSG))
        conn = openvz_conn.OpenVzDriver(manager.ComputeVirtAPI(None), False)
        self.mox.ReplayAll()
        conn._set_ioprio(
            fakes.INSTANCE, fakes.INSTANCE['memory_mb'])

    def test_set_ioprio_too_high_success(self):
        # Artificially inflate the memory for the instance to test the case
        # where the logarithm would result in a value higher than 7, the
        # code should set 7 as the cap.
        self.mox.StubOutWithMock(ovz_utils.utils, 'execute')
        ovz_utils.utils.execute(
            'vzctl', 'set', fakes.INSTANCE['id'], '--save', '--ioprio',
            7, run_as_root=True).AndReturn(
                ('', fakes.ERRORMSG))
        conn = openvz_conn.OpenVzDriver(manager.ComputeVirtAPI(None), False)
        self.mox.ReplayAll()
        conn._set_ioprio(
            fakes.INSTANCE, fakes.INSTANCE['memory_mb'] * 100)

    def test_set_ioprio_failure(self):
        self.mox.StubOutWithMock(ovz_utils.utils, 'execute')
        ovz_utils.utils.execute(
            'vzctl', 'set', fakes.INSTANCE['id'], '--save', '--ioprio',
            mox.IgnoreArg(), run_as_root=True).AndRaise(
                exception.InstanceUnacceptable(fakes.ERRORMSG))
        conn = openvz_conn.OpenVzDriver(manager.ComputeVirtAPI(None), False)
        self.mox.ReplayAll()
        self.assertRaises(
            exception.InstanceUnacceptable, conn._set_ioprio, fakes.INSTANCE,
            fakes.INSTANCE['memory_mb'])

    def test_set_diskspace_success(self):
        self.mox.StubOutWithMock(ovz_utils.utils, 'execute')
        ovz_utils.utils.execute(
            'vzctl', 'set', fakes.INSTANCE['id'], '--save', '--diskspace',
            mox.IgnoreArg(), run_as_root=True).AndReturn(('', fakes.ERRORMSG))
        self.mox.ReplayAll()
        conn = openvz_conn.OpenVzDriver(manager.ComputeVirtAPI(None), False)
        conn._set_diskspace(fakes.INSTANCE, fakes.INSTANCETYPE['root_gb'])

    def test_set_diskspace_failure(self):
        self.mox.StubOutWithMock(ovz_utils.utils, 'execute')
        ovz_utils.utils.execute(
            'vzctl', 'set', fakes.INSTANCE['id'], '--save', '--diskspace',
            mox.IgnoreArg(), run_as_root=True).AndRaise(
                exception.InstanceUnacceptable(fakes.ERRORMSG))
        self.mox.ReplayAll()
        conn = openvz_conn.OpenVzDriver(manager.ComputeVirtAPI(None), False)
        self.assertRaises(
            exception.InstanceUnacceptable, conn._set_diskspace,
            fakes.INSTANCE, fakes.INSTANCETYPE['root_gb'])

    def test_attach_volumes_success(self):
        conn = openvz_conn.OpenVzDriver(manager.ComputeVirtAPI(None), False)
        self.mox.StubOutWithMock(conn, 'attach_volume')
        conn.attach_volume(
            fakes.BDM['block_device_mapping'][0]['connection_info'],
            fakes.INSTANCE['name'],
            (fakes.BDM['block_device_mapping'][0]
                ['connection_info']['mount_device']))
        self.mox.ReplayAll()
        conn._attach_volumes(fakes.INSTANCE['name'], fakes.BDM)

    def test_attach_volume(self):
        self.mox.StubOutWithMock(ext_storage.ovzfile, 'OVZFile')
        ext_storage.ovzfile.OVZFile(
            self.ext_str_filename, self.ext_str_permissions).AndReturn(
                fakes.FakeOvzFile(
                    self.ext_str_filename, self.ext_str_permissions))
        self.mox.StubOutWithMock(openvz_conn.ovziscsi, 'OVZISCSIStorageDriver')
        openvz_conn.ovziscsi.OVZISCSIStorageDriver(
            fakes.INSTANCE['id'],
            fakes.BDM['block_device_mapping'][0]['mount_device'],
            fakes.BDM['block_device_mapping'][0]['connection_info']).AndReturn(
                fakes.FakeOVZISCSIStorageDriver())
        ovz_conn = openvz_conn.OpenVzDriver(
            manager.ComputeVirtAPI(None), False)
        self.mox.ReplayAll()
        ovz_conn.attach_volume(
            fakes.BDM['block_device_mapping'][0]['connection_info'],
            fakes.INSTANCE,
            fakes.BDM['block_device_mapping'][0]['mount_device'])

    def test_detach_volume(self):
        self.mox.StubOutWithMock(ext_storage.ovzfile, 'OVZFile')
        ext_storage.ovzfile.OVZFile(
            self.ext_str_filename, self.ext_str_permissions).AndReturn(
                fakes.FakeOvzFile(
                    self.ext_str_filename, self.ext_str_permissions))
        self.mox.StubOutWithMock(openvz_conn.ovziscsi, 'OVZISCSIStorageDriver')
        openvz_conn.ovziscsi.OVZISCSIStorageDriver(
            fakes.INSTANCE['id'],
            fakes.BDM['block_device_mapping'][0]['mount_device'],
            fakes.BDM['block_device_mapping'][0]['connection_info']).AndReturn(
                fakes.FakeOVZISCSIStorageDriver())
        ovz_conn = openvz_conn.OpenVzDriver(
            manager.ComputeVirtAPI(None), False)
        self.mox.ReplayAll()
        ovz_conn.detach_volume(
            fakes.BDM['block_device_mapping'][0]['connection_info'],
            fakes.INSTANCE)

    def test_get_available_resource(self):
        ovz_conn = openvz_conn.OpenVzDriver(
            manager.ComputeVirtAPI(None), False)
        self.mox.StubOutWithMock(ovz_conn, 'get_host_stats')
        ovz_conn.get_host_stats(refresh=True).AndReturn(fakes.HOSTSTATS)
        self.mox.ReplayAll()
        ovz_conn.get_available_resource(None)

    def test_get_volume_connector(self):
        self.mox.StubOutWithMock(openvz_conn.ovz_utils, 'get_iscsi_initiator')
        openvz_conn.ovz_utils.get_iscsi_initiator().AndReturn(
            fakes.ISCSIINITIATOR)
        self.mox.ReplayAll()
        ovz_conn = openvz_conn.OpenVzDriver(
            manager.ComputeVirtAPI(None), False)
        iscsi_initiator = ovz_conn.get_volume_connector(fakes.INSTANCE)
        self.assertTrue(isinstance(iscsi_initiator, dict))
        self.assertEqual(CONF.my_ip, iscsi_initiator['ip'])
        self.assertEqual(fakes.ISCSIINITIATOR, iscsi_initiator['initiator'])
        self.assertEqual(CONF.host, iscsi_initiator['host'])

    def test_gratuitous_arp_all_addresses(self):
        conn = openvz_conn.OpenVzDriver(manager.ComputeVirtAPI(None), False)
        self.mox.StubOutWithMock(conn, '_send_garp')
        conn._send_garp(
            fakes.INSTANCE['id'], mox.IgnoreArg(),
            mox.IgnoreArg()).MultipleTimes()
        self.mox.ReplayAll()
        conn._gratuitous_arp_all_addresses(fakes.INSTANCE, fakes.NETWORKINFO)

    def test_send_garp_success(self):
        self.mox.StubOutWithMock(ovz_utils.utils, 'execute')
        ovz_utils.utils.execute(
            'vzctl', 'exec2', fakes.INSTANCE['id'], 'arping', '-q', '-c', '5',
            '-A', '-I', fakes.NETWORKINFO[0][0]['bridge_interface'],
            fakes.NETWORKINFO[0][1]['ips'][0]['ip'],
            run_as_root=True).AndReturn(
                ('', fakes.ERRORMSG))
        self.mox.ReplayAll()
        conn = openvz_conn.OpenVzDriver(manager.ComputeVirtAPI(None), False)
        conn._send_garp(
            fakes.INSTANCE['id'], fakes.NETWORKINFO[0][1]['ips'][0]['ip'],
            fakes.NETWORKINFO[0][0]['bridge_interface'])

    def test_send_garp_faiure(self):
        self.mox.StubOutWithMock(ovz_utils.utils, 'execute')
        ovz_utils.utils.execute(
            'vzctl', 'exec2', fakes.INSTANCE['id'], 'arping', '-q', '-c', '5',
            '-A', '-I', fakes.NETWORKINFO[0][0]['bridge_interface'],
            fakes.NETWORKINFO[0][1]['ips'][0]['ip'],
            run_as_root=True).AndRaise(
                exception.InstanceUnacceptable(fakes.ERRORMSG))
        self.mox.ReplayAll()
        conn = openvz_conn.OpenVzDriver(manager.ComputeVirtAPI(None), False)
        self.assertRaises(
            exception.InstanceUnacceptable, conn._send_garp,
            fakes.INSTANCE['id'], fakes.NETWORKINFO[0][1]['ips'][0]['ip'],
            fakes.NETWORKINFO[0][0]['bridge_interface'])

    def test_init_host_success(self):
        self.mox.StubOutWithMock(openvz_conn.ovzboot, 'OVZBootFile')
        openvz_conn.ovzboot.OVZBootFile(
            mox.IgnoreArg(), mox.IgnoreArg()).AndReturn(
                fakes.FakeOVZBootFile(0, 700))
        self.mox.StubOutWithMock(openvz_conn.ovzshutdown, 'OVZShutdownFile')
        openvz_conn.ovzshutdown.OVZShutdownFile(
            mox.IgnoreArg(), mox.IgnoreArg()).AndReturn(
                fakes.FakeOVZShutdownFile(0, 700))
        self.mox.StubOutWithMock(openvz_conn.ovztc, 'OVZTcRules')
        openvz_conn.ovztc.OVZTcRules().AndReturn(fakes.FakeOVZTcRules())
        ovz_conn = openvz_conn.OpenVzDriver(
            manager.ComputeVirtAPI(None), False)
        self.mox.StubOutWithMock(ovz_conn, '_refresh_host_stats')
        ovz_conn._refresh_host_stats()
        self.mox.StubOutWithMock(ovz_conn, '_get_cpulimit')
        ovz_conn._get_cpulimit()
        self.mox.ReplayAll()
        ovz_conn.init_host()

    def test_get_host_stats(self):
        ovz_conn = openvz_conn.OpenVzDriver(
            manager.ComputeVirtAPI(None), False)
        self.mox.StubOutWithMock(ovz_conn, '_refresh_host_stats')
        ovz_conn._refresh_host_stats()
        self.mox.ReplayAll()
        result = ovz_conn.get_host_stats(True)
        self.assertTrue(isinstance(result, dict))

    def test_refresh_host_stats(self):
        self.mox.StubOutWithMock(openvz_conn.ovz_utils, 'get_vcpu_total')
        openvz_conn.ovz_utils.get_vcpu_total().AndReturn(
            fakes.HOSTSTATS['vcpus'])
        self.mox.StubOutWithMock(openvz_conn.ovz_utils, 'get_vcpu_used')
        openvz_conn.ovz_utils.get_vcpu_used().AndReturn(
            fakes.HOSTSTATS['vcpus_used'])
        self.mox.StubOutWithMock(openvz_conn.ovz_utils, 'get_cpuinfo')
        openvz_conn.ovz_utils.get_cpuinfo().AndReturn(
            fakes.PROCINFO)
        self.mox.StubOutWithMock(openvz_conn.ovz_utils, 'get_memory_mb_total')
        openvz_conn.ovz_utils.get_memory_mb_total().AndReturn(
            fakes.HOSTSTATS['memory_mb'])
        self.mox.StubOutWithMock(openvz_conn.ovz_utils, 'get_memory_mb_used')
        openvz_conn.ovz_utils.get_memory_mb_used().AndReturn(
            fakes.HOSTSTATS['memory_mb_used'])
        self.mox.StubOutWithMock(openvz_conn.ovz_utils, 'get_local_gb_total')
        openvz_conn.ovz_utils.get_local_gb_total().AndReturn(
            fakes.HOSTSTATS['disk_total'])
        self.mox.StubOutWithMock(openvz_conn.ovz_utils, 'get_local_gb_used')
        openvz_conn.ovz_utils.get_local_gb_used().AndReturn(
            fakes.HOSTSTATS['disk_used'])
        self.mox.StubOutWithMock(
            openvz_conn.ovz_utils, 'get_hypervisor_version')
        openvz_conn.ovz_utils.get_hypervisor_version().AndReturn(
            fakes.HOSTSTATS['hypervisor_version'])
        self.mox.ReplayAll()
        ovz_conn = openvz_conn.OpenVzDriver(
            manager.ComputeVirtAPI(None), False)
        ovz_conn._refresh_host_stats()

    def test_set_hostname_success(self):
        self.mox.StubOutWithMock(ovz_utils.utils, 'execute')
        ovz_utils.utils.execute(
            'vzctl', 'set', fakes.INSTANCE['id'], '--save', '--hostname',
            fakes.INSTANCE['hostname'], run_as_root=True).AndReturn(
                ('', fakes.ERRORMSG))
        self.mox.ReplayAll()
        ovz_conn = openvz_conn.OpenVzDriver(
            manager.ComputeVirtAPI(None), False)
        ovz_conn._set_hostname(fakes.INSTANCE)

    def test_set_hostname_failure(self):
        self.mox.StubOutWithMock(ovz_utils.utils, 'execute')
        ovz_utils.utils.execute(
            'vzctl', 'set', fakes.INSTANCE['id'], '--save', '--hostname',
            fakes.INSTANCE['hostname'], run_as_root=True).AndRaise(
                exception.InstanceUnacceptable(fakes.ERRORMSG))
        self.mox.ReplayAll()
        ovz_conn = openvz_conn.OpenVzDriver(
            manager.ComputeVirtAPI(None), False)
        self.assertRaises(
            exception.InstanceUnacceptable, ovz_conn._set_hostname,
            fakes.INSTANCE)

    def test_set_name_success(self):
        self.mox.StubOutWithMock(ovz_utils.utils, 'execute')
        ovz_utils.utils.execute(
            'vzctl', 'set', fakes.INSTANCE['id'], '--save', '--name',
            fakes.INSTANCE['name'], run_as_root=True).AndReturn(
                ('', fakes.ERRORMSG))
        self.mox.ReplayAll()
        ovz_conn = openvz_conn.OpenVzDriver(
            manager.ComputeVirtAPI(None), False)
        ovz_conn._set_name(fakes.INSTANCE)

    def test_set_name_failure(self):
        self.mox.StubOutWithMock(ovz_utils.utils, 'execute')
        ovz_utils.utils.execute(
            'vzctl', 'set', fakes.INSTANCE['id'], '--save', '--name',
            fakes.INSTANCE['name'], run_as_root=True).AndRaise(
                exception.InstanceUnacceptable(fakes.ERRORMSG))
        self.mox.ReplayAll()
        ovz_conn = openvz_conn.OpenVzDriver(
            manager.ComputeVirtAPI(None), False)
        self.assertRaises(
            exception.InstanceUnacceptable, ovz_conn._set_name, fakes.INSTANCE)

    def test_find_by_name_success(self):
        self.mox.StubOutWithMock(ovz_utils.utils, 'execute')
        ovz_utils.utils.execute(
            'vzlist', '-H', '-o', 'ctid,status,name', '--all', '--name_filter',
            fakes.INSTANCE['name'], run_as_root=True).AndReturn(
                (fakes.VZLISTDETAIL, fakes.ERRORMSG))
        self.mox.ReplayAll()
        ovz_conn = openvz_conn.OpenVzDriver(
            manager.ComputeVirtAPI(None), False)
        meta = ovz_conn._find_by_name(fakes.INSTANCE['name'])
        self.assertEqual(fakes.INSTANCE['hostname'], meta['name'])
        self.assertEqual(str(fakes.INSTANCE['id']), meta['id'])
        self.assertEqual('running', meta['state'])

    def test_find_by_name_not_found(self):
        self.mox.StubOutWithMock(ovz_utils.utils, 'execute')
        ovz_utils.utils.execute(
            'vzlist', '-H', '-o', 'ctid,status,name', '--all', '--name_filter',
            fakes.INSTANCE['name'], run_as_root=True).AndReturn(
                ('', fakes.ERRORMSG))
        self.mox.ReplayAll()
        ovz_conn = openvz_conn.OpenVzDriver(
            manager.ComputeVirtAPI(None), False)
        self.assertRaises(
            exception.NotFound, ovz_conn._find_by_name, fakes.INSTANCE['name'])

    def test_find_by_name_failure(self):
        self.mox.StubOutWithMock(ovz_utils.utils, 'execute')
        ovz_utils.utils.execute(
            'vzlist', '-H', '-o', 'ctid,status,name', '--all', '--name_filter',
            fakes.INSTANCE['name'], run_as_root=True).AndRaise(
                exception.InstanceUnacceptable(fakes.ERRORMSG))
        self.mox.ReplayAll()
        ovz_conn = openvz_conn.OpenVzDriver(
            manager.ComputeVirtAPI(None), False)
        self.assertRaises(
            exception.InstanceUnacceptable, ovz_conn._find_by_name,
            fakes.INSTANCE['name'])

    def test_plug_vifs(self):
        ovz_conn = openvz_conn.OpenVzDriver(
            manager.ComputeVirtAPI(None), False)
        ovz_conn.vif_driver = mox.MockAnything()
        ovz_conn.vif_driver.plug(
            fakes.INSTANCE, mox.IgnoreArg(), mox.IgnoreArg())
        self.mox.ReplayAll()
        ovz_conn.plug_vifs(fakes.INSTANCE, fakes.NETWORKINFO)

    def test_reboot_success(self):
        self.mox.StubOutWithMock(openvz_conn.ovzshutdown, 'OVZShutdownFile')
        openvz_conn.ovzshutdown.OVZShutdownFile(
            fakes.INSTANCE['id'], mox.IgnoreArg()).AndReturn(
                fakes.FakeOVZShutdownFile(fakes.INSTANCE['id'], 700))
        self.mox.StubOutWithMock(openvz_conn.ovzboot, 'OVZBootFile')
        openvz_conn.ovzboot.OVZBootFile(
            fakes.INSTANCE['id'], mox.IgnoreArg()).AndReturn(
                fakes.FakeOVZBootFile(fakes.INSTANCE['id'], 700))
        ovz_conn = openvz_conn.OpenVzDriver(
            manager.ComputeVirtAPI(None), False)
        self.mox.StubOutWithMock(ovz_conn.virtapi, 'instance_update')
        ovz_conn.virtapi.instance_update(
            fakes.ADMINCONTEXT, fakes.INSTANCE['uuid'],
            mox.IgnoreArg()).MultipleTimes()
        self.mox.StubOutWithMock(ovz_utils.utils, 'execute')
        ovz_utils.utils.execute(
            'vzctl', 'restart', fakes.INSTANCE['id'],
            run_as_root=True).AndReturn(('', ''))
        self.mox.StubOutWithMock(ovz_conn, 'get_info')
        ovz_conn.get_info(
            fakes.INSTANCE).MultipleTimes().AndReturn(fakes.GOODSTATUS)
        self.mox.ReplayAll()
        timer = ovz_conn.reboot(
            fakes.ADMINCONTEXT, fakes.INSTANCE, fakes.NETWORKINFO, None)
        timer.wait()

    def test_reboot_fail_in_get_info(self):
        self.mox.StubOutWithMock(openvz_conn.ovzshutdown, 'OVZShutdownFile')
        openvz_conn.ovzshutdown.OVZShutdownFile(
            fakes.INSTANCE['id'], mox.IgnoreArg()).AndReturn(
                fakes.FakeOVZShutdownFile(fakes.INSTANCE['id'], 700))
        ovz_conn = openvz_conn.OpenVzDriver(
            manager.ComputeVirtAPI(None), False)
        self.mox.StubOutWithMock(ovz_conn.virtapi, 'instance_update')
        ovz_conn.virtapi.instance_update(
            fakes.ADMINCONTEXT, fakes.INSTANCE['uuid'],
            mox.IgnoreArg()).MultipleTimes()
        self.mox.StubOutWithMock(ovz_utils.utils, 'execute')
        ovz_utils.utils.execute(
            'vzctl', 'restart', fakes.INSTANCE['id'],
            run_as_root=True).AndReturn(
                ('', fakes.ERRORMSG))
        self.mox.StubOutWithMock(ovz_conn, 'get_info')
        ovz_conn.get_info(fakes.INSTANCE).AndRaise(exception.NotFound)
        self.mox.ReplayAll()
        timer = ovz_conn.reboot(
            fakes.ADMINCONTEXT, fakes.INSTANCE, fakes.NETWORKINFO, None)
        self.assertRaises(exception.NotFound, timer.wait)

    def test_reboot_fail_because_not_found(self):
        self.mox.StubOutWithMock(openvz_conn.ovzshutdown, 'OVZShutdownFile')
        openvz_conn.ovzshutdown.OVZShutdownFile(
            fakes.INSTANCE['id'], mox.IgnoreArg()).AndReturn(
                fakes.FakeOVZShutdownFile(fakes.INSTANCE['id'], 700))
        ovz_conn = openvz_conn.OpenVzDriver(
            manager.ComputeVirtAPI(None), False)
        self.mox.StubOutWithMock(ovz_conn.virtapi, 'instance_update')
        ovz_conn.virtapi.instance_update(
            fakes.ADMINCONTEXT, fakes.INSTANCE['uuid'],
            mox.IgnoreArg()).MultipleTimes()
        self.mox.StubOutWithMock(ovz_utils.utils, 'execute')
        ovz_utils.utils.execute(
            'vzctl', 'restart', fakes.INSTANCE['id'],
            run_as_root=True).AndReturn(
                ('', fakes.ERRORMSG))
        self.mox.StubOutWithMock(ovz_conn, 'get_info')
        ovz_conn.get_info(fakes.INSTANCE).AndReturn(fakes.NOSTATUS)
        self.mox.ReplayAll()
        timer = ovz_conn.reboot(
            fakes.ADMINCONTEXT, fakes.INSTANCE, fakes.NETWORKINFO, None)
        timer.wait()

    def test_reboot_failure(self):
        self.mox.StubOutWithMock(openvz_conn.ovzshutdown, 'OVZShutdownFile')
        openvz_conn.ovzshutdown.OVZShutdownFile(
            fakes.INSTANCE['id'], mox.IgnoreArg()).AndReturn(
                fakes.FakeOVZShutdownFile(fakes.INSTANCE['id'], 700))
        ovz_conn = openvz_conn.OpenVzDriver(
            manager.ComputeVirtAPI(None), False)
        self.mox.StubOutWithMock(ovz_conn.virtapi, 'instance_update')
        ovz_conn.virtapi.instance_update(
            fakes.ADMINCONTEXT, fakes.INSTANCE['uuid'],
            {'power_state': power_state.PAUSED})
        self.mox.StubOutWithMock(ovz_utils.utils, 'execute')
        ovz_utils.utils.execute(
            'vzctl', 'restart', fakes.INSTANCE['id'],
            run_as_root=True).AndRaise(
                exception.InstanceUnacceptable(fakes.ERRORMSG))
        self.mox.ReplayAll()
        self.assertRaises(
            exception.InstanceUnacceptable, ovz_conn.reboot,
            fakes.ADMINCONTEXT, fakes.INSTANCE, fakes.NETWORKINFO, None)

    def test_inject_files(self):
        ovz_conn = openvz_conn.OpenVzDriver(
            manager.ComputeVirtAPI(None), False)
        self.mox.StubOutWithMock(ovz_conn, 'inject_file')
        ovz_conn.inject_file(
            fakes.INSTANCE, mox.IgnoreArg(),
            base64.b64encode(fakes.FILECONTENTS)).MultipleTimes()
        self.mox.ReplayAll()
        ovz_conn._inject_files(fakes.INSTANCE, fakes.FILESTOINJECT)

    def test_inject_file(self):
        full_path = '%s/%s/%s' % (CONF.ovz_ve_private_dir,
                                  fakes.INSTANCE['id'],
                                  fakes.FILESTOINJECT[0][0])
        self.mox.StubOutWithMock(openvz_conn.ovzfile, 'OVZFile')
        openvz_conn.ovzfile.OVZFile(
            full_path, 644).AndReturn(fakes.FakeOvzFile(full_path, 644))
        self.mox.ReplayAll()
        ovz_conn = openvz_conn.OpenVzDriver(
            manager.ComputeVirtAPI(None), False)
        ovz_conn.inject_file(
            fakes.INSTANCE, base64.b64encode(fakes.FILESTOINJECT[0][0]),
            base64.b64encode(fakes.FILESTOINJECT[0][1]))

    def test_set_admin_password_success(self):
        self.mox.StubOutWithMock(ovz_utils.utils, 'execute')
        ovz_utils.utils.execute(
            'vzctl', 'exec2', fakes.INSTANCE['id'], 'echo',
            'root:%s' % fakes.ROOTPASS, '|', 'chpasswd',
            run_as_root=True).AndReturn(
                ('', fakes.ERRORMSG))
        self.mox.ReplayAll()
        ovz_conn = openvz_conn.OpenVzDriver(
            manager.ComputeVirtAPI(None), False)
        ovz_conn.set_admin_password(
            fakes.ADMINCONTEXT, fakes.INSTANCE['id'], fakes.ROOTPASS)

    def test_set_admin_password_failure(self):
        self.mox.StubOutWithMock(ovz_utils.utils, 'execute')
        ovz_utils.utils.execute(
            'vzctl', 'exec2', fakes.INSTANCE['id'], 'echo',
            'root:%s' % fakes.ROOTPASS, '|', 'chpasswd',
            run_as_root=True).AndRaise(
                exception.InstanceUnacceptable(fakes.ERRORMSG))
        self.mox.ReplayAll()
        ovz_conn = openvz_conn.OpenVzDriver(
            manager.ComputeVirtAPI(None), False)
        self.assertRaises(
            exception.InstanceUnacceptable, ovz_conn.set_admin_password,
            fakes.ADMINCONTEXT, fakes.INSTANCE['id'], fakes.ROOTPASS)

    def test_pause_success(self):
        self.mox.StubOutWithMock(openvz_conn.ovzshutdown, 'OVZShutdownFile')
        openvz_conn.ovzshutdown.OVZShutdownFile(
            fakes.INSTANCE['id'], mox.IgnoreArg()).AndReturn(
                fakes.FakeOVZShutdownFile(fakes.INSTANCE['id'], 700))
        self.mox.StubOutWithMock(ovz_utils.utils, 'execute')
        ovz_utils.utils.execute(
            'vzctl', 'stop', fakes.INSTANCE['id'], run_as_root=True).AndReturn(
                ('', fakes.ERRORMSG))
        conn = openvz_conn.OpenVzDriver(manager.ComputeVirtAPI(None), False)
        self.mox.StubOutWithMock(conn.virtapi, 'instance_update')
        conn.virtapi.instance_update(
            mox.IgnoreArg(), fakes.INSTANCE['uuid'],
            {'power_state': power_state.SHUTDOWN})
        self.mox.ReplayAll()
        conn.pause(fakes.INSTANCE)

    def test_pause_failure(self):
        self.mox.StubOutWithMock(ovz_utils.utils, 'execute')
        ovz_utils.utils.execute(
            'vzctl', 'stop', fakes.INSTANCE['id'], run_as_root=True).AndRaise(
                exception.InstanceUnacceptable(fakes.ERRORMSG))
        self.mox.StubOutWithMock(openvz_conn.ovzshutdown, 'OVZShutdownFile')
        openvz_conn.ovzshutdown.OVZShutdownFile(
            fakes.INSTANCE['id'], mox.IgnoreArg()).AndReturn(
                fakes.FakeOVZShutdownFile(fakes.INSTANCE['id'], 700))
        self.mox.ReplayAll()
        conn = openvz_conn.OpenVzDriver(manager.ComputeVirtAPI(None), False)
        self.assertRaises(
            exception.InstanceUnacceptable, conn.pause, fakes.INSTANCE)

    def test_suspend_success(self):
        self.mox.StubOutWithMock(openvz_conn.context, 'get_admin_context')
        openvz_conn.context.get_admin_context().AndReturn(fakes.ADMINCONTEXT)
        self.mox.StubOutWithMock(ovz_utils.utils, 'execute')
        ovz_utils.utils.execute(
            'vzctl', 'chkpnt', fakes.INSTANCE['id'], '--suspend',
            run_as_root=True).AndReturn(
                ('', fakes.ERRORMSG))
        conn = openvz_conn.OpenVzDriver(manager.ComputeVirtAPI(None), False)
        self.mox.StubOutWithMock(conn.virtapi, 'instance_update')
        conn.virtapi.instance_update(
            fakes.ADMINCONTEXT, fakes.INSTANCE['uuid'],
            {'power_state': power_state.SUSPENDED})
        self.mox.ReplayAll()
        conn.suspend(fakes.INSTANCE)

    def test_suspend_failure(self):
        self.mox.StubOutWithMock(ovz_utils.utils, 'execute')
        ovz_utils.utils.execute(
            'vzctl', 'chkpnt', fakes.INSTANCE['id'], '--suspend',
            run_as_root=True).AndRaise(
                exception.InstanceUnacceptable(
                    fakes.ERRORMSG))
        self.mox.ReplayAll()
        conn = openvz_conn.OpenVzDriver(manager.ComputeVirtAPI(None), False)
        self.assertRaises(
            exception.InstanceUnacceptable, conn.suspend, fakes.INSTANCE)

    def test_suspend_dberror(self):
        self.mox.StubOutWithMock(openvz_conn.context, 'get_admin_context')
        openvz_conn.context.get_admin_context().AndReturn(fakes.ADMINCONTEXT)
        self.mox.StubOutWithMock(ovz_utils.utils, 'execute')
        ovz_utils.utils.execute(
            'vzctl', 'chkpnt', fakes.INSTANCE['id'], '--suspend',
            run_as_root=True).AndReturn(
                ('', fakes.ERRORMSG))
        conn = openvz_conn.OpenVzDriver(manager.ComputeVirtAPI(None), False)
        self.mox.StubOutWithMock(conn.virtapi, 'instance_update')
        conn.virtapi.instance_update(
            fakes.ADMINCONTEXT, fakes.INSTANCE['uuid'],
            {'power_state': power_state.SUSPENDED}).AndRaise(
                exception.InstanceNotFound(fakes.ERRORMSG))
        self.mox.ReplayAll()
        conn.suspend(fakes.INSTANCE)

    def test_unpause_success(self):
        self.mox.StubOutWithMock(openvz_conn.ovzboot, 'OVZBootFile')
        openvz_conn.ovzboot.OVZBootFile(
            fakes.INSTANCE['id'], mox.IgnoreArg()).AndReturn(
                fakes.FakeOVZBootFile(fakes.INSTANCE['id'], 700))
        self.mox.StubOutWithMock(ovz_utils.utils, 'execute')
        ovz_utils.utils.execute(
            'vzctl', 'start', fakes.INSTANCE['id'],
            run_as_root=True).AndReturn(
                ('', fakes.ERRORMSG))
        conn = openvz_conn.OpenVzDriver(manager.ComputeVirtAPI(None), True)
        self.mox.StubOutWithMock(conn.virtapi, 'instance_update')
        conn.virtapi.instance_update(
            mox.IgnoreArg(), fakes.INSTANCE['uuid'],
            {'power_state': power_state.RUNNING})

        self.mox.ReplayAll()
        conn.unpause(fakes.INSTANCE)

    def test_unpause_failure(self):
        self.mox.StubOutWithMock(ovz_utils.utils, 'execute')
        ovz_utils.utils.execute(
            'vzctl', 'start', fakes.INSTANCE['id'], run_as_root=True).AndRaise(
                exception.InstanceUnacceptable(fakes.ERRORMSG))
        self.mox.ReplayAll()
        conn = openvz_conn.OpenVzDriver(manager.ComputeVirtAPI(None), False)
        self.assertRaises(
            exception.InstanceUnacceptable, conn.unpause, fakes.INSTANCE)

    def test_resume_success(self):
        self.mox.StubOutWithMock(ovz_utils.utils, 'execute')
        ovz_utils.utils.execute(
            'vzctl', 'chkpnt', fakes.INSTANCE['id'], '--resume',
            run_as_root=True).AndReturn(
                ('', fakes.ERRORMSG))
        conn = openvz_conn.OpenVzDriver(manager.ComputeVirtAPI(None), True)
        self.mox.StubOutWithMock(conn.virtapi, 'instance_update')
        conn.virtapi.instance_update(
            mox.IgnoreArg(), fakes.INSTANCE['uuid'],
            {'power_state': power_state.RUNNING})

        self.mox.ReplayAll()
        conn.resume(fakes.INSTANCE, fakes.NETWORKINFO)

    def test_resume_db_not_found(self):
        self.mox.StubOutWithMock(ovz_utils.utils, 'execute')
        ovz_utils.utils.execute(
            'vzctl', 'chkpnt', fakes.INSTANCE['id'], '--resume',
            run_as_root=True).AndReturn(
                ('', fakes.ERRORMSG))
        conn = openvz_conn.OpenVzDriver(manager.ComputeVirtAPI(None), True)
        self.mox.StubOutWithMock(conn.virtapi, 'instance_update')
        conn.virtapi.instance_update(
            mox.IgnoreArg(), fakes.INSTANCE['uuid'],
            {'power_state': power_state.RUNNING}).AndRaise(
                exception.InstanceNotFound(fakes.ERRORMSG))
        self.mox.ReplayAll()
        conn.resume(fakes.INSTANCE, fakes.NETWORKINFO)

    def test_resume_failure(self):
        self.mox.StubOutWithMock(ovz_utils.utils, 'execute')
        ovz_utils.utils.execute(
            'vzctl', 'chkpnt', fakes.INSTANCE['id'], '--resume',
            run_as_root=True).AndRaise(
                exception.InstanceUnacceptable(fakes.ERRORMSG))
        self.mox.ReplayAll()
        conn = openvz_conn.OpenVzDriver(manager.ComputeVirtAPI(None), False)
        self.assertRaises(
            exception.InstanceUnacceptable, conn.resume, fakes.INSTANCE, None,
            None)

    def test_clean_orphaned_files(self):
        self.mox.StubOutWithMock(openvz_conn.os, 'listdir')
        openvz_conn.os.listdir(mox.IgnoreArg()).MultipleTimes().AndReturn(
            fakes.OSLISTDIR)
        self.mox.StubOutWithMock(ovz_utils.utils, 'execute')
        ovz_utils.utils.execute(
            'rm', '-f', mox.IgnoreArg(),
            run_as_root=True).MultipleTimes().AndReturn(('', ''))
        self.mox.ReplayAll()
        ovz_conn = openvz_conn.OpenVzDriver(
            manager.ComputeVirtAPI(None), False)
        ovz_conn._clean_orphaned_files(fakes.INSTANCE['id'])

    def test_destroy_fail_on_exec(self):
        self.mox.StubOutWithMock(
            openvz_conn.ovz_utils, 'remove_instance_metadata')
        openvz_conn.ovz_utils.remove_instance_metadata(fakes.INSTANCE['id'])
        ovz_conn = openvz_conn.OpenVzDriver(
            manager.ComputeVirtAPI(None), False)
        ovz_conn.vif_driver = mox.MockAnything()
        ovz_conn.vif_driver.unplug(
            fakes.INSTANCE, mox.IgnoreArg(), mox.IgnoreArg())
        self.mox.StubOutWithMock(ovz_conn, '_stop')
        ovz_conn._stop(fakes.INSTANCE)
        self.mox.StubOutWithMock(ovz_conn, 'get_info')
        ovz_conn.get_info(fakes.INSTANCE).AndReturn(fakes.GOODSTATUS)
        self.mox.StubOutWithMock(ovz_conn, '_destroy')
        ovz_conn._destroy(fakes.INSTANCE['id']).AndRaise(
            exception.InstanceUnacceptable(fakes.ERRORMSG))
        self.mox.ReplayAll()
        self.assertRaises(
            exception.InstanceTerminationFailure, ovz_conn.destroy,
            fakes.INSTANCE, fakes.NETWORKINFO)

    def test_destroy_success(self):
        self.mox.StubOutWithMock(
            openvz_conn.ovz_utils, 'remove_instance_metadata')
        openvz_conn.ovz_utils.remove_instance_metadata(fakes.INSTANCE['id'])
        ovz_conn = openvz_conn.OpenVzDriver(
            manager.ComputeVirtAPI(None), False)
        ovz_conn.vif_driver = mox.MockAnything()
        ovz_conn.vif_driver.unplug(
            fakes.INSTANCE, mox.IgnoreArg(), mox.IgnoreArg())
        self.mox.StubOutWithMock(ovz_conn, 'get_info')
        dir(ovz_conn.get_info)
        ovz_conn.get_info(fakes.INSTANCE).AndReturn(
            fakes.GOODSTATUS)
        ovz_conn.get_info(fakes.INSTANCE).AndRaise(exception.InstanceNotFound(
            fakes.ERRORMSG))
        self.mox.StubOutWithMock(ovz_conn, '_stop')
        ovz_conn._stop(fakes.INSTANCE)
        self.mox.StubOutWithMock(ovz_conn, '_destroy')
        ovz_conn._destroy(fakes.INSTANCE['id'])
        self.mox.StubOutWithMock(ovz_conn, '_clean_orphaned_files')
        ovz_conn._clean_orphaned_files(fakes.INSTANCE['id'])
        self.mox.StubOutWithMock(ovz_conn, '_detach_volumes')
        ovz_conn._detach_volumes(fakes.INSTANCE, fakes.BDM)
        self.mox.ReplayAll()
        ovz_conn.destroy(fakes.INSTANCE, fakes.NETWORKINFO, fakes.BDM)

    def test_get_info_running_state(self):
        ovz_conn = openvz_conn.OpenVzDriver(
            manager.ComputeVirtAPI(None), False)
        self.mox.StubOutWithMock(ovz_conn, '_find_by_name')
        ovz_conn._find_by_name(fakes.INSTANCE['name']).AndReturn(
            fakes.FINDBYNAME)
        self.mox.ReplayAll()
        meta = ovz_conn.get_info(fakes.INSTANCE)
        self.assertTrue(isinstance(meta, dict))
        self.assertEqual(meta['state'], power_state.RUNNING)

    def test_get_info_shutdown_state(self):
        ovz_conn = openvz_conn.OpenVzDriver(
            manager.ComputeVirtAPI(None), False)
        self.mox.StubOutWithMock(ovz_conn, '_find_by_name')
        ovz_conn._find_by_name(fakes.INSTANCE['name']).AndReturn(
            fakes.FINDBYNAMESHUTDOWN)
        self.mox.ReplayAll()
        meta = ovz_conn.get_info(fakes.INSTANCE)
        self.assertTrue(isinstance(meta, dict))
        self.assertEqual(meta['state'], power_state.SHUTDOWN)

    def test_get_info_no_state(self):
        ovz_conn = openvz_conn.OpenVzDriver(
            manager.ComputeVirtAPI(None), False)
        self.mox.StubOutWithMock(ovz_conn, '_find_by_name')
        ovz_conn._find_by_name(fakes.INSTANCE['name']).AndReturn(
            fakes.FINDBYNAMENOSTATE)
        self.mox.ReplayAll()
        meta = ovz_conn.get_info(fakes.INSTANCE)
        self.assertTrue(isinstance(meta, dict))
        self.assertEqual(meta['state'], power_state.NOSTATE)

    def test_get_info_state_is_None(self):
        BADFINDBYNAME = fakes.FINDBYNAME.copy()
        BADFINDBYNAME['state'] = None
        ovz_conn = openvz_conn.OpenVzDriver(
            manager.ComputeVirtAPI(None), False)
        self.mox.StubOutWithMock(ovz_conn, '_find_by_name')
        ovz_conn._find_by_name(fakes.INSTANCE['name']).AndReturn(BADFINDBYNAME)
        self.mox.ReplayAll()
        meta = ovz_conn.get_info(fakes.INSTANCE)
        self.assertTrue(isinstance(meta, dict))
        self.assertEqual(meta['state'], power_state.NOSTATE)

    def test_get_info_state_is_shutdown(self):
        BADFINDBYNAME = fakes.FINDBYNAME.copy()
        BADFINDBYNAME['state'] = 'shutdown'
        ovz_conn = openvz_conn.OpenVzDriver(
            manager.ComputeVirtAPI(None), False)
        self.mox.StubOutWithMock(ovz_conn, '_find_by_name')
        ovz_conn._find_by_name(fakes.INSTANCE['name']).AndReturn(BADFINDBYNAME)
        self.mox.ReplayAll()
        meta = ovz_conn.get_info(fakes.INSTANCE)
        self.assertTrue(isinstance(meta, dict))
        self.assertEqual(meta['state'], power_state.SHUTDOWN)

    def test_get_info_notfound(self):
        ovz_conn = openvz_conn.OpenVzDriver(
            manager.ComputeVirtAPI(None), False)
        self.mox.StubOutWithMock(ovz_conn, '_find_by_name')
        ovz_conn._find_by_name(fakes.INSTANCE['name']).AndRaise(
            exception.NotFound)
        self.mox.ReplayAll()
        self.assertRaises(
            exception.NotFound, ovz_conn.get_info, fakes.INSTANCE)

    def test_percent_of_memory_over_subscribe(self):
        # Force the utility storage to have really low memory so as to test the
        # code that doesn't allow more than a 1.x multiplier.
        ovz_conn = openvz_conn.OpenVzDriver(
            manager.ComputeVirtAPI(None), False)
        ovz_conn.utility['MEMORY_MB'] = 16
        self.mox.StubOutWithMock(ovz_utils, 'get_memory_mb_total')
        ovz_utils.get_memory_mb_total().AndReturn(1024)
        self.mox.ReplayAll()
        self.assertEqual(
            1, ovz_conn._percent_of_resource(fakes.INSTANCE['memory_mb']))

    def test_percent_of_memory_normal_subscribe(self):
        ovz_conn = openvz_conn.OpenVzDriver(
            manager.ComputeVirtAPI(None), False)
        ovz_conn.utility['MEMORY_MB'] = 16384
        self.mox.ReplayAll()
        self.assertTrue(
            ovz_conn._percent_of_resource(fakes.INSTANCE['memory_mb']) < 1)

    def test_get_cpulimit_success(self):
        self.mox.StubOutWithMock(ovz_utils.multiprocessing, 'cpu_count')
        ovz_utils.multiprocessing.cpu_count().AndReturn(2)
        self.mox.ReplayAll()
        ovz_conn = openvz_conn.OpenVzDriver(
            manager.ComputeVirtAPI(None), False)
        ovz_conn._get_cpulimit()
        self.assertEqual(ovz_conn.utility['CPULIMIT'], 200)

    def test_spawn_success(self):
        ovz_conn = openvz_conn.OpenVzDriver(
            manager.ComputeVirtAPI(None), False)
        self.mox.StubOutWithMock(ovz_conn.virtapi, 'instance_update')
        ovz_conn.virtapi.instance_update(
            fakes.ADMINCONTEXT, fakes.INSTANCE['uuid'], mox.IgnoreArg())
        self.mox.StubOutWithMock(ovz_conn, '_get_cpuunits_usage')
        ovz_conn._get_cpuunits_usage()
        self.mox.StubOutWithMock(ovz_conn, '_cache_image')
        ovz_conn._cache_image(fakes.ADMINCONTEXT, fakes.INSTANCE)
        self.mox.StubOutWithMock(ovz_conn, '_create_vz')
        ovz_conn._create_vz(fakes.INSTANCE)
        self.mox.StubOutWithMock(ovz_conn, '_set_vz_os_hint')
        ovz_conn._set_vz_os_hint(fakes.INSTANCE)
        self.mox.StubOutWithMock(ovz_conn, '_configure_vz')
        ovz_conn._configure_vz(fakes.INSTANCE)
        self.mox.StubOutWithMock(ovz_conn, '_set_description')
        ovz_conn._set_description(fakes.INSTANCE)
        self.mox.StubOutWithMock(ovz_conn, '_setup_networking')
        ovz_conn._setup_networking(fakes.INSTANCE, fakes.NETWORKINFO)
        self.mox.StubOutWithMock(ovz_conn, '_set_onboot')
        ovz_conn._set_onboot(fakes.INSTANCE)
        self.mox.StubOutWithMock(ovz_conn, '_set_name')
        ovz_conn._set_name(fakes.INSTANCE)
        self.mox.StubOutWithMock(ovz_conn, 'plug_vifs')
        ovz_conn.plug_vifs(fakes.INSTANCE, fakes.NETWORKINFO)
        self.mox.StubOutWithMock(ovz_conn, '_set_hostname')
        ovz_conn._set_hostname(fakes.INSTANCE)
        self.mox.StubOutWithMock(ovz_conn, '_set_instance_size')
        ovz_conn._set_instance_size(fakes.INSTANCE)
        self.mox.StubOutWithMock(ovz_conn, '_attach_volumes')
        ovz_conn._attach_volumes(fakes.INSTANCE, fakes.BDM)
        self.mox.StubOutWithMock(ovz_conn, '_inject_files')
        ovz_conn._inject_files(fakes.INSTANCE, fakes.FILESTOINJECT)
        self.mox.StubOutWithMock(ovz_conn, '_start')
        ovz_conn._start(fakes.INSTANCE)
        self.mox.StubOutWithMock(ovz_conn, '_gratuitous_arp_all_addresses')
        ovz_conn._gratuitous_arp_all_addresses(
            fakes.INSTANCE, fakes.NETWORKINFO)
        self.mox.StubOutWithMock(ovz_conn, 'set_admin_password')
        ovz_conn.set_admin_password(
            fakes.ADMINCONTEXT, fakes.INSTANCE['id'],
            fakes.INSTANCE['admin_pass'])
        self.mox.StubOutWithMock(ovz_conn, 'get_info')
        ovz_conn.get_info(fakes.INSTANCE).AndReturn(fakes.GOODSTATUS)
        self.mox.ReplayAll()
        timer = ovz_conn.spawn(
            fakes.ADMINCONTEXT, fakes.INSTANCE, None, fakes.FILESTOINJECT,
            fakes.ROOTPASS, fakes.NETWORKINFO, fakes.BDM)
        timer.wait()

    def test_spawn_failure(self):
        ovz_conn = openvz_conn.OpenVzDriver(
            manager.ComputeVirtAPI(None), False)
        self.mox.StubOutWithMock(ovz_conn.virtapi, 'instance_update')
        ovz_conn.virtapi.instance_update(
            fakes.ADMINCONTEXT, fakes.INSTANCE['uuid'], mox.IgnoreArg())
        self.mox.StubOutWithMock(ovz_conn, '_get_cpuunits_usage')
        ovz_conn._get_cpuunits_usage()
        self.mox.StubOutWithMock(ovz_conn, '_cache_image')
        ovz_conn._cache_image(fakes.ADMINCONTEXT, fakes.INSTANCE)
        self.mox.StubOutWithMock(ovz_conn, '_create_vz')
        ovz_conn._create_vz(fakes.INSTANCE)
        self.mox.StubOutWithMock(ovz_conn, '_set_vz_os_hint')
        ovz_conn._set_vz_os_hint(fakes.INSTANCE)
        self.mox.StubOutWithMock(ovz_conn, '_configure_vz')
        ovz_conn._configure_vz(fakes.INSTANCE)
        self.mox.StubOutWithMock(ovz_conn, '_set_description')
        ovz_conn._set_description(fakes.INSTANCE)
        self.mox.StubOutWithMock(ovz_conn, '_setup_networking')
        ovz_conn._setup_networking(fakes.INSTANCE, fakes.NETWORKINFO)
        self.mox.StubOutWithMock(ovz_conn, '_set_onboot')
        ovz_conn._set_onboot(fakes.INSTANCE)
        self.mox.StubOutWithMock(ovz_conn, '_set_name')
        ovz_conn._set_name(fakes.INSTANCE)
        self.mox.StubOutWithMock(ovz_conn, 'plug_vifs')
        ovz_conn.plug_vifs(fakes.INSTANCE, fakes.NETWORKINFO)
        self.mox.StubOutWithMock(ovz_conn, '_set_hostname')
        ovz_conn._set_hostname(fakes.INSTANCE)
        self.mox.StubOutWithMock(ovz_conn, '_set_instance_size')
        ovz_conn._set_instance_size(fakes.INSTANCE)
        self.mox.StubOutWithMock(ovz_conn, '_attach_volumes')
        ovz_conn._attach_volumes(fakes.INSTANCE, fakes.BDM)
        self.mox.StubOutWithMock(ovz_conn, '_inject_files')
        ovz_conn._inject_files(fakes.INSTANCE, fakes.FILESTOINJECT)
        self.mox.StubOutWithMock(ovz_conn, '_start')
        ovz_conn._start(fakes.INSTANCE)
        self.mox.StubOutWithMock(ovz_conn, '_gratuitous_arp_all_addresses')
        ovz_conn._gratuitous_arp_all_addresses(
            fakes.INSTANCE, fakes.NETWORKINFO)
        self.mox.StubOutWithMock(ovz_conn, 'set_admin_password')
        ovz_conn.set_admin_password(
            fakes.ADMINCONTEXT, fakes.INSTANCE['id'],
            fakes.INSTANCE['admin_pass'])
        self.mox.StubOutWithMock(ovz_conn, 'get_info')
        ovz_conn.get_info(fakes.INSTANCE).AndRaise(exception.NotFound)
        self.mox.ReplayAll()
        timer = ovz_conn.spawn(
            fakes.ADMINCONTEXT, fakes.INSTANCE, None, fakes.FILESTOINJECT,
            fakes.ROOTPASS, fakes.NETWORKINFO, fakes.BDM)
        timer.wait()

    def test_snapshot(self):
        ovz_conn = openvz_conn.OpenVzDriver(
            manager.ComputeVirtAPI(None), False)
        ovz_conn.snapshot(
            fakes.ADMINCONTEXT, fakes.INSTANCE, fakes.INSTANCE['image_ref'],
            True)

    def test_rescue(self):
        ovz_conn = openvz_conn.OpenVzDriver(
            manager.ComputeVirtAPI(None), False)
        ovz_conn.rescue(
            fakes.ADMINCONTEXT, fakes.INSTANCE, fakes.NETWORKINFO,
            fakes.INSTANCE['image_ref'], fakes.ROOTPASS)

    def test_unrescue(self):
        ovz_conn = openvz_conn.OpenVzDriver(
            manager.ComputeVirtAPI(None), False)
        ovz_conn.unrescue(fakes.INSTANCE, fakes.NETWORKINFO)

    def test_get_diagnostics(self):
        ovz_conn = openvz_conn.OpenVzDriver(
            manager.ComputeVirtAPI(None), False)
        ovz_conn.get_diagnostics(fakes.INSTANCE['name'])

    def test_list_disks(self):
        ovz_conn = openvz_conn.OpenVzDriver(
            manager.ComputeVirtAPI(None), False)
        result = ovz_conn.list_disks(fakes.INSTANCE['name'])
        self.assertTrue(isinstance(result, list))
        self.assertEqual(result[0], 'A_DISK')

    def test_list_interfaces(self):
        ovz_conn = openvz_conn.OpenVzDriver(
            manager.ComputeVirtAPI(None), False)
        result = ovz_conn.list_interfaces(fakes.INSTANCE['name'])
        self.assertTrue(isinstance(result, list))
        self.assertEqual(result[0], 'A_VIF')

    def test_block_stats(self):
        ovz_conn = openvz_conn.OpenVzDriver(
            manager.ComputeVirtAPI(None), False)
        result = ovz_conn.block_stats(
            fakes.INSTANCE['name'], 'c49a7247-731e-4135-8420-7a3c67002582')
        self.assertTrue(isinstance(result, list))
        self.assertEqual(result[0], 0L)
        self.assertEqual(result[1], 0L)
        self.assertEqual(result[2], 0L)
        self.assertEqual(result[3], 0L)
        self.assertEqual(result[4], None)

    def test_interface_stats(self):
        ovz_conn = openvz_conn.OpenVzDriver(
            manager.ComputeVirtAPI(None), False)
        result = ovz_conn.interface_stats(fakes.INSTANCE['name'], 'eth0')
        self.assertTrue(isinstance(result, list))
        self.assertEqual(result[0], 0L)
        self.assertEqual(result[1], 0L)
        self.assertEqual(result[2], 0L)
        self.assertEqual(result[3], 0L)
        self.assertEqual(result[4], 0L)
        self.assertEqual(result[5], 0L)
        self.assertEqual(result[6], 0L)
        self.assertEqual(result[7], 0L)

    def test_get_console_output(self):
        ovz_conn = openvz_conn.OpenVzDriver(
            manager.ComputeVirtAPI(None), False)
        result = ovz_conn.get_console_output(fakes.INSTANCE)
        self.assertTrue(isinstance(result, str))
        self.assertEqual(result, 'FAKE CONSOLE OUTPUT')

    def test_get_ajax_console(self):
        ovz_conn = openvz_conn.OpenVzDriver(
            manager.ComputeVirtAPI(None), False)
        result = ovz_conn.get_ajax_console(fakes.INSTANCE)
        self.assertTrue(isinstance(result, str))
        self.assertEqual(result, 'http://fakeajaxconsole.com/?token=FAKETOKEN')

    def test_get_console_pool_info(self):
        ovz_conn = openvz_conn.OpenVzDriver(
            manager.ComputeVirtAPI(None), False)
        result = ovz_conn.get_console_pool_info(None)
        self.assertTrue(isinstance(result, dict))
        self.assertEqual(result['address'], '127.0.0.1')
        self.assertEqual(result['username'], 'fakeuser')
        self.assertEqual(result['password'], 'fakepassword')

    def test_refresh_security_group_rules(self):
        ovz_conn = openvz_conn.OpenVzDriver(
            manager.ComputeVirtAPI(None), False)
        result = ovz_conn.refresh_security_group_rules(None)
        self.assertTrue(result)

    def test_refresh_security_group_members(self):
        ovz_conn = openvz_conn.OpenVzDriver(
            manager.ComputeVirtAPI(None), False)
        result = ovz_conn.refresh_security_group_members(None)
        self.assertTrue(result)

    def test_poll_rebooting_instances(self):
        ovz_conn = openvz_conn.OpenVzDriver(
            manager.ComputeVirtAPI(None), False)
        result = ovz_conn.poll_rebooting_instances(5, fakes.INSTANCES)

    def test_poll_rescued_instances(self):
        ovz_conn = openvz_conn.OpenVzDriver(
            manager.ComputeVirtAPI(None), False)
        result = ovz_conn.poll_rescued_instances(5)

    def test_power_off(self):
        ovz_conn = openvz_conn.OpenVzDriver(
            manager.ComputeVirtAPI(None), False)
        result = ovz_conn.power_off(fakes.INSTANCE)

    def test_power_on(self):
        ovz_conn = openvz_conn.OpenVzDriver(
            manager.ComputeVirtAPI(None), False)
        result = ovz_conn.power_on(fakes.INSTANCE)

    def test_compare_cpu(self):
        ovz_conn = openvz_conn.OpenVzDriver(
            manager.ComputeVirtAPI(None), False)
        result = ovz_conn.compare_cpu(fakes.PROCINFO)

    def test_poll_unconfirmed_resizes(self):
        ovz_conn = openvz_conn.OpenVzDriver(
            manager.ComputeVirtAPI(None), False)
        result = ovz_conn.poll_unconfirmed_resizes(None)

    def test_host_power_action(self):
        ovz_conn = openvz_conn.OpenVzDriver(
            manager.ComputeVirtAPI(None), False)
        result = ovz_conn.host_power_action(CONF.host, 'reboot')

    def test_set_host_enabled(self):
        ovz_conn = openvz_conn.OpenVzDriver(
            manager.ComputeVirtAPI(None), False)
        result = ovz_conn.set_host_enabled(CONF.host, True)

    def test_ensure_filtering_rules_for_instance(self):
        ovz_conn = openvz_conn.OpenVzDriver(
            manager.ComputeVirtAPI(None), False)
        result = ovz_conn.ensure_filtering_rules_for_instance(
            fakes.INSTANCE, fakes.NETWORKINFO)

    def test_unfilter_instance(self):
        ovz_conn = openvz_conn.OpenVzDriver(
            manager.ComputeVirtAPI(None), False)
        result = ovz_conn.unfilter_instance(fakes.INSTANCE, fakes.NETWORKINFO)

    def test_refresh_provider_fw_rules(self):
        ovz_conn = openvz_conn.OpenVzDriver(
            manager.ComputeVirtAPI(None), False)
        result = ovz_conn.refresh_provider_fw_rules()

    def test_agent_update(self):
        ovz_conn = openvz_conn.OpenVzDriver(
            manager.ComputeVirtAPI(None), False)
        result = ovz_conn.agent_update(fakes.INSTANCE, None, None)

    def test_update_host_status(self):
        ovz_conn = openvz_conn.OpenVzDriver(
            manager.ComputeVirtAPI(None), False)
        result = ovz_conn.update_host_status()

    def test_get_all_bw_usage(self):
        ovz_conn = openvz_conn.OpenVzDriver(
            manager.ComputeVirtAPI(None), False)
        result = ovz_conn.get_all_bw_usage(fakes.INSTANCES, 'now')
        self.assertTrue(isinstance(result, list))
        self.assertTrue(len(result) == 0)

    def test_snapshot_instance(self):
        ovz_conn = openvz_conn.OpenVzDriver(
            manager.ComputeVirtAPI(None), False)
        result = ovz_conn.snapshot_instance(
            fakes.ADMINCONTEXT, fakes.INSTANCE['id'],
            fakes.INSTANCE['image_ref'])

    def test_resize(self):
        ovz_conn = openvz_conn.OpenVzDriver(
            manager.ComputeVirtAPI(None), False)
        ovz_conn.resize(fakes.INSTANCE, None)

    def test_get_host_ip_addr(self):
        ovz_conn = openvz_conn.OpenVzDriver(
            manager.ComputeVirtAPI(None), False)
        ovz_conn.get_host_ip_addr()

    def test_migrate_disk_and_power_off_success_with_storage(self):
        ovz_conn = openvz_conn.OpenVzDriver(
            manager.ComputeVirtAPI(None), False)
        self.mox.StubOutWithMock(ovz_conn, '_stop')
        ovz_conn._stop(fakes.INSTANCE)
        self.mox.StubOutWithMock(ovz_conn, '_pymigration_send_to_host')
        ovz_conn._pymigration_send_to_host(
            fakes.INSTANCE, ovz_utils.generate_network_dict(
                fakes.INSTANCE['id'], fakes.NETWORKINFO),
            fakes.BDM, fakes.DESTINATION, False)
        self.mox.StubOutWithMock(ovz_conn, '_detach_volumes')
        ovz_conn._detach_volumes(fakes.INSTANCE, fakes.BDM, True, False)
        self.mox.ReplayAll()
        ovz_conn.migrate_disk_and_power_off(
            fakes.ADMINCONTEXT, fakes.INSTANCE, fakes.DESTINATION,
            fakes.INSTANCETYPE, fakes.NETWORKINFO, fakes.BDM)

    def test_migrate_disk_and_power_off_success_without_storage(self):
        INSTANCE = fakes.INSTANCE.copy()
        INSTANCE['block_device_mapping'] = {}
        ovz_conn = openvz_conn.OpenVzDriver(
            manager.ComputeVirtAPI(None), False)
        self.mox.StubOutWithMock(ovz_conn, 'suspend')
        ovz_conn.suspend(INSTANCE)
        self.mox.StubOutWithMock(ovz_conn, '_pymigration_send_to_host')
        ovz_conn._pymigration_send_to_host(
            INSTANCE, ovz_utils.generate_network_dict(
                INSTANCE['id'], fakes.NETWORKINFO),
            None, fakes.DESTINATION, True)
        self.mox.ReplayAll()
        ovz_conn.migrate_disk_and_power_off(
            fakes.ADMINCONTEXT, INSTANCE, fakes.DESTINATION,
            fakes.INSTANCETYPE, fakes.NETWORKINFO)

    def test_migrate_disk_and_power_off_no_dest(self):
        ovz_conn = openvz_conn.OpenVzDriver(
            manager.ComputeVirtAPI(None), False)
        self.assertRaises(
            exception.MigrationError, ovz_conn.migrate_disk_and_power_off,
            fakes.ADMINCONTEXT, fakes.INSTANCE, None, fakes.INSTANCETYPE,
            fakes.NETWORKINFO)

    def test_migrate_disk_and_power_off_same_host(self):
        ovz_conn = openvz_conn.OpenVzDriver(
            manager.ComputeVirtAPI(None), False)
        self.mox.StubOutWithMock(ovz_utils, 'save_instance_metadata')
        ovz_utils.save_instance_metadata(
            fakes.INSTANCE['id'], 'migration_type', 'resize_in_place')
        self.mox.ReplayAll()
        ovz_conn.migrate_disk_and_power_off(
            fakes.ADMINCONTEXT, fakes.INSTANCE, CONF.host,
            fakes.INSTANCETYPE, fakes.NETWORKINFO)

    def test_pymigration_send_to_host(self):
        self.mox.StubOutWithMock(
            migration.OVZMigration, 'dump_and_transfer_instance')
        migration.OVZMigration.dump_and_transfer_instance()
        self.mox.StubOutWithMock(migration.OVZMigration, 'send')
        migration.OVZMigration.send()
        self.mox.ReplayAll()
        ovz_conn = openvz_conn.OpenVzDriver(
            manager.ComputeVirtAPI(None), False)
        ovz_conn._pymigration_send_to_host(
            fakes.INSTANCE, fakes.NETWORKINFO, fakes.BDM,
            fakes.DESTINATION, False)

    def test_vzmigration_send_to_host(self):
        self.mox.StubOutWithMock(ovz_utils, 'execute')
        ovz_utils.execute(
            'vzmigrate', '--online', '-r', 'yes', '-v', fakes.DESTINATION,
            fakes.INSTANCE['id'], run_as_root=True)
        self.mox.ReplayAll()
        ovz_conn = openvz_conn.OpenVzDriver(
            manager.ComputeVirtAPI(None), False)
        ovz_conn._vzmigration_send_to_host(fakes.INSTANCE, fakes.DESTINATION)

    def test_finish_migration_resize_in_place(self):
        self.mox.StubOutWithMock(ovz_utils, 'read_instance_metadata')
        ovz_utils.read_instance_metadata(fakes.INSTANCE['id']).AndReturn(
            {'migration_type': 'resize_in_place'})
        ovz_conn = openvz_conn.OpenVzDriver(
            manager.ComputeVirtAPI(None), False)
        self.mox.StubOutWithMock(ovz_conn, '_set_instance_size')
        ovz_conn._set_instance_size(fakes.INSTANCE, fakes.NETWORKINFO, False)
        self.mox.ReplayAll()
        ovz_conn.finish_migration(
            fakes.ADMINCONTEXT, None, fakes.INSTANCE, None, fakes.NETWORKINFO,
            None, None, fakes.BDM)

    def test_finish_migration_no_resize(self):
        self.mox.StubOutWithMock(ovz_utils, 'read_instance_metadata')
        ovz_utils.read_instance_metadata(fakes.INSTANCE['id']).AndReturn({})
        ovz_conn = openvz_conn.OpenVzDriver(
            manager.ComputeVirtAPI(None), False)
        self.mox.StubOutWithMock(ovz_conn, '_attach_volumes')
        ovz_conn._attach_volumes(fakes.INSTANCE, fakes.BDM)
        self.mox.StubOutWithMock(ovz_conn, '_pymigrate_finish_migration')
        ovz_conn._pymigrate_finish_migration(
            fakes.INSTANCE, fakes.NETWORKINFO, False)
        self.mox.StubOutWithMock(ovz_conn, '_set_name')
        ovz_conn._set_name(fakes.INSTANCE)
        self.mox.StubOutWithMock(ovz_conn, '_set_description')
        ovz_conn._set_description(fakes.INSTANCE)
        self.mox.StubOutWithMock(ovz_conn, '_generate_tc_rules')
        ovz_conn._generate_tc_rules(fakes.INSTANCE, fakes.NETWORKINFO, True)
        self.mox.StubOutWithMock(ovz_conn, '_start')
        ovz_conn._start(fakes.INSTANCE)
        self.mox.ReplayAll()
        ovz_conn.finish_migration(
            fakes.ADMINCONTEXT, None, fakes.INSTANCE, None, fakes.NETWORKINFO,
            None, False, fakes.BDM)

    def test_finish_migration_resize(self):
        self.mox.StubOutWithMock(ovz_utils, 'read_instance_metadata')
        ovz_utils.read_instance_metadata(fakes.INSTANCE['id']).AndReturn({})
        ovz_conn = openvz_conn.OpenVzDriver(
            manager.ComputeVirtAPI(None), False)
        self.mox.StubOutWithMock(ovz_conn, '_attach_volumes')
        ovz_conn._attach_volumes(fakes.INSTANCE, fakes.BDM)
        self.mox.StubOutWithMock(ovz_conn, '_pymigrate_finish_migration')
        ovz_conn._pymigrate_finish_migration(
            fakes.INSTANCE, fakes.NETWORKINFO, False)
        self.mox.StubOutWithMock(ovz_conn, '_set_name')
        ovz_conn._set_name(fakes.INSTANCE)
        self.mox.StubOutWithMock(ovz_conn, '_set_instance_size')
        ovz_conn._set_instance_size(fakes.INSTANCE, fakes.NETWORKINFO, True)
        self.mox.StubOutWithMock(ovz_conn, '_set_description')
        ovz_conn._set_description(fakes.INSTANCE)
        self.mox.StubOutWithMock(ovz_conn, '_start')
        ovz_conn._start(fakes.INSTANCE)
        self.mox.ReplayAll()
        ovz_conn.finish_migration(
            fakes.ADMINCONTEXT, None, fakes.INSTANCE, None, fakes.NETWORKINFO,
            None, True, fakes.BDM)

    def test_pymigrate_finish_migration(self):
        self.mox.StubOutWithMock(migration.OVZMigration, 'undump_instance')
        migration.OVZMigration.undump_instance()
        self.mox.StubOutWithMock(migration.OVZMigration, 'cleanup_destination')
        migration.OVZMigration.cleanup_destination()
        self.mox.ReplayAll()
        ovz_conn = openvz_conn.OpenVzDriver(
            manager.ComputeVirtAPI(None), False)
        ovz_conn._pymigrate_finish_migration(
            fakes.INSTANCE, fakes.NETWORKINFO, False)

    def test_vzmigrate_setup_dest_host(self):
        ovz_conn = openvz_conn.OpenVzDriver(
            manager.ComputeVirtAPI(None), False)
        self.mox.StubOutWithMock(ovz_conn, '_stop')
        ovz_conn._stop(fakes.INSTANCE)
        self.mox.StubOutWithMock(ovz_conn, 'plug_vifs')
        ovz_conn.plug_vifs(fakes.INSTANCE, fakes.NETWORKINFO)
        self.mox.StubOutWithMock(ovz_conn, '_start')
        ovz_conn._start(fakes.INSTANCE)
        self.mox.ReplayAll()
        ovz_conn._vzmigrate_setup_dest_host(fakes.INSTANCE, fakes.NETWORKINFO)

    def test_confirm_migration_resize_in_place(self):
        self.mox.StubOutWithMock(ovz_utils, 'read_instance_metadata')
        ovz_utils.read_instance_metadata(fakes.INSTANCE['id']).AndReturn(
            {'migration_type': 'resize_in_place'})
        self.mox.StubOutWithMock(ovz_utils, 'remove_instance_metadata_key')
        ovz_utils.remove_instance_metadata_key(
            fakes.INSTANCE['id'], 'migration_type')
        self.mox.StubOutWithMock(openvz_conn.ext_storage, 'OVZExtStorage')
        openvz_conn.ext_storage.OVZExtStorage(fakes.INSTANCE['id']).AndReturn(
            fakes.FakeOVZExtStorage(fakes.INSTANCE['id']))
        self.mox.ReplayAll()
        ovz_conn = openvz_conn.OpenVzDriver(
            manager.ComputeVirtAPI(None), False)
        ovz_conn.confirm_migration(None, fakes.INSTANCE, fakes.NETWORKINFO)

    def test_confirm_migration(self):
        self.mox.StubOutWithMock(migration.OVZMigration, 'cleanup_source')
        migration.OVZMigration.cleanup_source()
        self.mox.StubOutWithMock(ovz_utils, 'read_instance_metadata')
        ovz_utils.read_instance_metadata(fakes.INSTANCE['id']).AndReturn({})
        self.mox.StubOutWithMock(openvz_conn.ext_storage, 'OVZExtStorage')
        openvz_conn.ext_storage.OVZExtStorage(fakes.INSTANCE['id']).AndReturn(
            fakes.FakeOVZExtStorage(fakes.INSTANCE['id']))
        ovz_conn = openvz_conn.OpenVzDriver(
            manager.ComputeVirtAPI(None), False)
        self.mox.StubOutWithMock(ovz_conn, 'get_info')
        ovz_conn.get_info(fakes.INSTANCE).AndReturn(fakes.SHUTDOWNSTATUS)
        self.mox.StubOutWithMock(ovz_conn, '_destroy')
        ovz_conn._destroy(fakes.INSTANCE['id'])
        self.mox.StubOutWithMock(ovz_conn, '_clean_orphaned_files')
        ovz_conn._clean_orphaned_files(fakes.INSTANCE['id'])
        self.mox.ReplayAll()
        ovz_conn.confirm_migration(None, fakes.INSTANCE, fakes.NETWORKINFO)

    def test_finish_revert_migration(self):
        self.mox.StubOutWithMock(ovz_utils, 'read_instance_metadata')
        ovz_utils.read_instance_metadata(fakes.INSTANCE['id']).AndReturn({})
        self.mox.StubOutWithMock(migration.OVZMigration, 'cleanup_files')
        migration.OVZMigration.cleanup_files()
        ovz_conn = openvz_conn.OpenVzDriver(
            manager.ComputeVirtAPI(None), False)
        self.mox.StubOutWithMock(ovz_conn, 'resume')
        ovz_conn.resume(fakes.INSTANCE, fakes.NETWORKINFO)
        self.mox.ReplayAll()
        ovz_conn.finish_revert_migration(
            fakes.INSTANCE, fakes.NETWORKINFO, None)
