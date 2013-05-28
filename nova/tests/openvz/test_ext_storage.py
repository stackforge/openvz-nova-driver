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

import json
from nova import test
from nova.tests.openvz import fakes
from nova.virt.openvz.file_ext import ext_storage
import os
from oslo.config import cfg

CONF = cfg.CONF


class OpenVzExtStorageTestCase(test.TestCase):
    def setUp(self):
        super(OpenVzExtStorageTestCase, self).setUp()
        self.filename = '%s/%s.ext_storage' % (CONF.ovz_config_dir,
                                               fakes.INSTANCE['id'])
        self.filename = os.path.abspath(self.filename)
        self.permissions = 600

    def test_new_object(self):
        self.mox.StubOutWithMock(ext_storage.ovzfile, 'OVZFile')
        ext_storage.ovzfile.OVZFile(self.filename, self.permissions).AndReturn(
            fakes.FakeOvzFile(self.filename, self.permissions))
        self.mox.ReplayAll()
        ext_str = ext_storage.OVZExtStorage(fakes.INSTANCE['id'])
        self.assertEqual(ext_str.instance_id, fakes.INSTANCE['id'])
        self.assertEqual(ext_str.local_store.filename, self.filename)
        self.assertEqual(ext_str.local_store.permissions, self.permissions)

    def test_load_volumes(self):
        self.mox.StubOutWithMock(ext_storage.ovzfile, 'OVZFile')
        ext_storage.ovzfile.OVZFile(self.filename, self.permissions).AndReturn(
            fakes.FakeOvzFile(self.filename, self.permissions))
        self.mox.ReplayAll()
        ext_str = ext_storage.OVZExtStorage(fakes.INSTANCE['id'])
        self.assertTrue(isinstance(ext_str._volumes, dict))

    def test_add_volume_success(self):
        BDM = fakes.BDM['block_device_mapping'][0]
        self.mox.StubOutWithMock(ext_storage.ovzfile, 'OVZFile')
        ext_storage.ovzfile.OVZFile(self.filename, self.permissions).AndReturn(
            fakes.FakeOvzFile(self.filename, self.permissions))
        self.mox.ReplayAll()
        ext_str = ext_storage.OVZExtStorage(fakes.INSTANCE['id'])
        for bdm in fakes.BDM['block_device_mapping']:
            ext_str.add_volume(bdm['mount_device'], bdm['connection_info'])

        self.assertTrue(BDM['mount_device'] in ext_str._volumes)
        self.assertEqual(
            ext_str._volumes[BDM['mount_device']], BDM['connection_info'])

    def test_remove_volume_success(self):
        BDM = fakes.BDM['block_device_mapping'][0]
        self.mox.StubOutWithMock(ext_storage.ovzfile, 'OVZFile')
        ext_storage.ovzfile.OVZFile(self.filename, self.permissions).AndReturn(
            fakes.FakeOvzFile(self.filename, self.permissions))
        self.mox.ReplayAll()
        ext_str = ext_storage.OVZExtStorage(fakes.INSTANCE['id'])
        for bdm in fakes.BDM['block_device_mapping']:
            ext_str.add_volume(bdm['mount_device'], bdm['connection_info'])

        self.assertTrue(BDM['mount_device'] in ext_str._volumes)
        self.assertEqual(
            ext_str._volumes[BDM['mount_device']], BDM['connection_info'])

        ext_str.remove_volume(BDM['mount_device'])

        self.assertFalse(BDM['mount_device'] in ext_str._volumes)

    def test_remove_volume_failure(self):
        BDM = fakes.BDM['block_device_mapping'][0]
        self.mox.StubOutWithMock(ext_storage.ovzfile, 'OVZFile')
        ext_storage.ovzfile.OVZFile(self.filename, self.permissions).AndReturn(
            fakes.FakeOvzFile(self.filename, self.permissions))
        self.mox.ReplayAll()
        ext_str = ext_storage.OVZExtStorage(fakes.INSTANCE['id'])
        ext_str.remove_volume(BDM['mount_device'])

    def test_save(self):
        BDM = fakes.BDM['block_device_mapping'][0]
        self.mox.StubOutWithMock(ext_storage.ovzfile, 'OVZFile')
        ext_storage.ovzfile.OVZFile(self.filename, self.permissions).AndReturn(
            fakes.FakeOvzFile(self.filename, self.permissions))
        self.mox.ReplayAll()
        ext_str = ext_storage.OVZExtStorage(fakes.INSTANCE['id'])
        for bdm in fakes.BDM['block_device_mapping']:
            ext_str.add_volume(bdm['mount_device'], bdm['connection_info'])

        ext_str.save()
        self.assertEqual(
            json.dumps(ext_str._volumes), ext_str.local_store.contents)
