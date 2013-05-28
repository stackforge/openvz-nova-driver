# vim: tabstop=4 shiftwidth=4 softtabstop=4

# Copyright 2010 OpenStack LLC.
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
import __builtin__
import mox
from nova import exception
from nova import test
from nova.tests.openvz import fakes
from nova.virt.openvz import driver as openvz_conn
from nova.virt.openvz import file as ovzfile
from nova.virt.openvz import utils as ovz_utils
from oslo.config import cfg

CONF = cfg.CONF


class OpenVzFileTestCase(test.TestCase):
    def setUp(self):
        super(OpenVzFileTestCase, self).setUp()
        self.fake_file = mox.MockAnything()
        self.fake_file.readlines().AndReturn(fakes.FILECONTENTS.split())
        self.fake_file.writelines(mox.IgnoreArg())
        self.fake_file.read().AndReturn(fakes.FILECONTENTS)

    def test_touch_file_success(self):
        fh = ovzfile.OVZFile(fakes.TEMPFILE, 755)
        self.mox.StubOutWithMock(fh, 'make_path')
        fh.make_path()
        self.mox.StubOutWithMock(ovz_utils, 'execute')
        ovz_utils.execute(
            'touch', fakes.TEMPFILE, run_as_root=True).AndReturn(
                ('', fakes.ERRORMSG))
        self.mox.ReplayAll()
        fh.touch()

    def test_touch_file_failure(self):
        fh = ovzfile.OVZFile(fakes.TEMPFILE, 755)
        self.mox.StubOutWithMock(fh, 'make_path')
        fh.make_path()
        self.mox.StubOutWithMock(ovz_utils, 'execute')
        ovz_utils.execute(
            'touch', fakes.TEMPFILE, run_as_root=True).AndRaise(
                exception.InstanceUnacceptable(fakes.ERRORMSG))
        self.mox.ReplayAll()
        self.assertRaises(exception.InstanceUnacceptable, fh.touch)

    def test_read_file_success(self):
        self.mox.StubOutWithMock(__builtin__, 'open')
        __builtin__.open(mox.IgnoreArg(), 'r').AndReturn(self.fake_file)
        self.mox.ReplayAll()
        fh = ovzfile.OVZFile(fakes.TEMPFILE, 755)
        fh.read()

    def test_read_file_failure(self):
        self.mox.StubOutWithMock(__builtin__, 'open')
        __builtin__.open(mox.IgnoreArg(), 'r').AndRaise(
            exception.FileNotFound(fakes.ERRORMSG))
        self.mox.ReplayAll()
        fh = ovzfile.OVZFile(fakes.TEMPFILE, 755)
        self.assertRaises(exception.FileNotFound, fh.read)

    def test_write_to_file_success(self):
        self.mox.StubOutWithMock(__builtin__, 'open')
        __builtin__.open(mox.IgnoreArg(), 'w').AndReturn(self.fake_file)
        self.mox.ReplayAll()
        fh = ovzfile.OVZFile(fakes.TEMPFILE, 755)
        fh.write()

    def test_write_to_file_failure(self):
        self.mox.StubOutWithMock(__builtin__, 'open')
        __builtin__.open(mox.IgnoreArg(), 'w').AndRaise(
            exception.FileNotFound(fakes.ERRORMSG))
        self.mox.ReplayAll()
        fh = ovzfile.OVZFile(fakes.TEMPFILE, 755)
        self.assertRaises(exception.FileNotFound, fh.write)

    def test_set_perms_success(self):
        self.mox.StubOutWithMock(ovz_utils, 'execute')
        ovz_utils.execute(
            'chmod', 755, fakes.TEMPFILE, run_as_root=True).AndReturn(
                ('', fakes.ERRORMSG))
        self.mox.ReplayAll()
        fh = ovzfile.OVZFile(fakes.TEMPFILE, 755)
        fh.set_permissions(755)

    def test_set_perms_failure(self):
        self.mox.StubOutWithMock(ovz_utils, 'execute')
        ovz_utils.execute(
            'chmod', 755, fakes.TEMPFILE, run_as_root=True).AndRaise(
                exception.InstanceUnacceptable(fakes.ERRORMSG))
        self.mox.ReplayAll()
        fh = ovzfile.OVZFile(fakes.TEMPFILE, 755)
        self.assertRaises(
            exception.InstanceUnacceptable, fh.set_permissions, 755)

    def test_make_path_and_dir_success(self):
        self.mox.StubOutWithMock(ovz_utils, 'execute')
        ovz_utils.execute(
            'mkdir', '-p', mox.IgnoreArg(), run_as_root=True).AndReturn(
                ('', fakes.ERRORMSG))
        self.mox.StubOutWithMock(openvz_conn.os.path, 'exists')
        openvz_conn.os.path.exists(mox.IgnoreArg()).AndReturn(False)
        self.mox.ReplayAll()
        fh = ovzfile.OVZFile(fakes.TEMPFILE, 755)
        fh.make_path()

    def test_make_path_and_dir_exists(self):
        self.mox.StubOutWithMock(openvz_conn.os.path, 'exists')
        openvz_conn.os.path.exists(mox.IgnoreArg()).AndReturn(True)
        self.mox.ReplayAll()
        fh = ovzfile.OVZFile(fakes.TEMPFILE, 755)
        fh.make_path()
