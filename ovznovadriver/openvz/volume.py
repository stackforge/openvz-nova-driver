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

import glob
from nova import block_device
from nova import exception
from nova.openstack.common import log as logging
from nova.openstack.common import processutils
from ovznovadriver.localization import _
from ovznovadriver.openvz import file as ovzfile
from ovznovadriver.openvz import utils as ovz_utils
import os
from oslo.config import cfg
import re
import time

CONF = cfg.CONF
CONF.register_opts([
    cfg.IntOpt('ovz_volume_settle_num_tries',
               default=10,
               help='Number of attempts before we determine that a device '
                    'has had time to settle and we are beyond a reasonable '
                    'wait time'),
    cfg.BoolOpt('ovz_create_disk_partition',
                default=False,
                help='Create a partition on the volume (if it does not exist) '
                     'when it is attached to the container'),
])
LOG = logging.getLogger('ovznovadriver.openvz.volume')


class OVZVolume(object):
    """
    This Class is a helper class to manage the mount and umount files
    for a given container.
    """
    def __init__(self, instance_id, mountpoint, dev):
        """
        Setup critical information for attaching/detaching volumes from
        instance_id.

        :param instance_id:
        :param mountpoint: connection_info['data']['mount_device']
        :param dev:
        """
        #TODO(imsplitbit): Need to make this happen while supporting block
        # devices and externally mounted filesystems at the same time.
        # Currently it does not.
        self.instance_id = instance_id
        self.mountpoint = block_device.prepend_dev(mountpoint)
        self.device = dev

    def attach(self):
        """
        Public method for attaching volumes either by creating bind mounts or
        attaching a raw device to the instance.
        """
        self._attach_raw_devices()

    def detach(self, container_is_running=True):
        """
        Public method for detaching volumes from an instance.
        """
        self._detach_raw_devices(container_is_running)

    def device_name(self):
        """
        Generates an device name.  If no volume driver is used this will
        assist in generating an error should things get this far.
        """
        return

    def _check_device_exists(self, device_path):
        """Check that the device path exists.

        Verify that the device path has actually been created and can report
        it's size, only then can it be available for formatting, retry
        num_tries to account for the time lag.
        """
        try:
            ovz_utils.execute('blockdev', '--getsize64', device_path,
                              attempts=CONF.ovz_system_num_tries,
                              run_as_root=True)
        except processutils.ProcessExecutionError:
            raise exception.InvalidDevicePath(path=device_path)

    def _find_device(self):
        try:
            return os.path.realpath(self.device_name())

        except (OSError, AttributeError) as err:
            raise exception.VolumeNotFound(
                _('Device cannot be found %s') % err)

    def _list_host_devices(self):
        dev = self._find_device()
        LOG.debug(_('Device name: %(dev)s') % locals())
        devs = list()
        listing = glob.glob('/dev/sd*')

        for device in listing:
            LOG.debug(_('Trying to match against %(device)s') % locals())
            m = re.match('^(?P<device>%s)(?P<part>\\d*)$' % dev, device)
            if m:
                LOG.debug(_('Matched device %(device)s') % locals())
                devs.append(m.string)

        LOG.info(_('Devices: %(devs)s') % locals())
        return devs

    def _get_major_minor(self, device):
        try:
            dev = os.stat(device)
            maj_min = dict()
            maj_min['major'] = os.major(dev.st_rdev)
            maj_min['minor'] = os.minor(dev.st_rdev)
            LOG.info(
                _('Major Minor numbers for %(dev)s: %(maj_min)s') % locals())
            return maj_min

        except OSError as err:
            LOG.error(_('Device error in OpenVz volumes: %(err)s') % locals())
            raise exception.VolumeNotFound(
                _('Unable to find device: %(device)s') % locals())

    def _let_disk_settle(self):
        """
        Check to see if the device_name is available in /dev/disk/by-path.  If
        it isn't then we'll wait a bit and retry.  Disks don't just instantly
        show up in /dev so this gives a little bit of wait time for things
        to show up.

        :return:
        """
        ovz_utils.execute(
            'ls', self.device_name(), run_as_root=True,
            attempts=CONF.ovz_volume_settle_num_tries, delay_on_retry=True)

    def _list_partition_table(self):
        """
        There is a bit of work done here to make a nice partition table
        structure for later use.  Currently the only thing we care about is
        if there is already a partition table but we may need this info soon
        so the legwork is done.

        :return: dict
        """
        # Now we should be able to move on to goodness.
        dev = self._find_device()
        LOG.debug(_('Device name: %(dev)s') % locals())
        part_table = dict()
        try:
            out = ovz_utils.execute('sfdisk', '-d', dev, run_as_root=True)

            for line in out.splitlines():
                line = line.split(':')
                dev = line[0].strip()
                LOG.debug(_('Device from part table: %(dev)s') % locals())

                if dev.startswith('/dev'):
                    line = line[1].split(',')

                    LOG.debug(_('Line from part table: %(line)s') % locals())
                    part_table[dev] = {}
                    part_table[dev]['start'] = line[0].split('=')[1]
                    part_table[dev]['size'] = line[1].split('=')[1]
                    part_table[dev]['id'] = line[2].split('=')[1]

                    LOG.debug(
                        _('Device info from part table: %s') % part_table[dev])

                    if len(line) > 3:
                        part_table[dev]['flags'] = line[3]
                        LOG.debug(_('Disk flags exist for %(dev)s') % locals())

            LOG.info(_('Partition Table: %(part_table)s') % locals())
            return part_table

        except exception.InstanceUnacceptable as err:
            LOG.warn(_('No partition table on %(dev)s') % locals())
            return part_table

    def _partition_disk(self):
        """
        Openvz requires that we pass in not only the root device but if the
        user is to access partitions on that disk we need also to pass in
        the major/minor numbers for each partition.  Given that requirement
        we're making the assumption for now that volumes are going to be
        presented as one partition.  This will partition the root device so
        we can later get the information on both the major and minor numbers.

        If the disk contains a valid partition table then we just return

        :return:
        """
        # We need potentially need to give the device time to settle
        self._let_disk_settle()

        dev = self._find_device()
        part_table = self._list_partition_table()
        LOG.debug(_('Partition table for %(dev)s: %(part_table)s') % locals())

        # If the volume is partitioned we're assuming this is a reconnect of
        # a volume and we won't partition anything.
        if not part_table:
            commands = ['o,w', 'n,p,1,,,t,1,83,w']

            for command in commands:
                command = command.replace(',', '\n')
                ovz_utils.execute(
                    'fdisk', dev, process_input=command, run_as_root=True)
                time.sleep(3)

    def _get_vz_device_list(self):
        """
        Grab existing device information from vz config and create new
        device string for device connections.

        :return:
        """
        devices = list()
        config = os.path.normpath(
            '%s/%s.conf' % (CONF.ovz_config_dir, self.instance_id))
        config = ovzfile.OVZFile(config, 644)
        config.read()

        for line in config.contents:
            if '=' in line:
                line = line.split('=')
                if line[0] == 'DEVICES':
                    devices = line[1].replace('\"', '').strip().split()

        return devices

    def _ovz_block_dev_name_generator(self, major, minor, privs):
        """
        OpenVz requires a specific format for granting or revoking block
        device privileges.  This tool will create those names.

        :param major:
        :param minor:
        :param privs:
        :return:
        """
        return 'b:%(major)s:%(minor)s:%(privs)s' % locals()

    def _attach_raw_devices(self):
        """
        If there are no partitions on the disk already, partition it with 1
        partition.  Then use openvz tools and mknod to attach these devices
        to the container.

        :return:
        """
        if CONF.ovz_create_disk_partition:
            LOG.debug(_('Partitioning disk in _attach_raw_devices'))
            self._partition_disk()

        # storage for block device major/minor pairs for setting in
        # the container config file.
        bdevs = []
        for dev in self._list_host_devices():
            maj_min = self._get_major_minor(dev)
            bdevs.append(
                self._ovz_block_dev_name_generator(
                    maj_min['major'], maj_min['minor'], 'rw'))

            m = re.match('^(?P<device>/[a-z]+/[a-z]+)(?P<part>\\d*)$', dev)
            device_name = self.mountpoint

            if m and m.group('part'):
                device_name = '%s%s' % (self.mountpoint, m.group('part'))

            # Remove the device if it happens to exist.  This prevents stale
            # devices with old major/minor numbers existing.
            self._delete_container_device(device_name)
            self._mknod(maj_min['major'], maj_min['minor'], device_name)

        if bdevs:
            self._add_block_devices(bdevs)

    def _attach_raw_device(self, major, minor):
        ovz_utils.execute(
            'vzctl', 'set', self.instance_id,
            '--devices', 'b:%s:%s:rw' % (major, minor),
            run_as_root=True)

    def _add_block_devices(self, new_devices):
        """
        New devices should be a list that looks like this:

        ['b:8:32:rw', 'b:8:33:rw']

        :param new_devices:
        :return:
        """
        devices = self._get_vz_device_list()

        for dev in new_devices:
            if dev not in devices:
                devices.append(dev)

        self._set_block_devices(devices)

    def _del_block_devices(self, new_devices, container_is_running=True):
        """
        New devices should be a list that looks like this:

        ['b:8:32:rw', 'b:8:33:rw']

        :param new_devices:
        :return:
        """
        devices = self._get_vz_device_list()

        for dev in new_devices:
            if dev in devices:
                devices.remove(dev)

        self._set_block_devices(devices, container_is_running)

    def _detach_remaining_block_devices(self):
        """
        If the final ephemeral volume is detached from the container we issue
        a privs :none for the remaining devices in the conf file for the
        container before we remove those entries from the conf file.  This is
        necessary to ensure the removal of the devices from the in memory
        device list for openvz.

        :return:
        """
        devices = self._get_vz_device_list()
        cmd = ['vzctl', 'set', self.instance_id]

        for dev in devices:
            dev = dev.replace(':rw', ':none')
            cmd.append('--devices')
            cmd.append(dev)

        ovz_utils.execute(*cmd, run_as_root=True)

    def _set_block_devices(self, devices, container_is_running=True):
        """
        Run the command necessary to save the block device permissions to
        the container config file so that they persist on reboot.

        :param devices:
        :return:
        """
        cmd = ['vzctl', 'set', self.instance_id, '--save']
        if devices:
            for dev in devices:
                cmd.append('--devices')
                cmd.append(dev)

            ovz_utils.execute(*cmd, run_as_root=True)
        else:
            # if the device list is empty this means that it's the last device
            # attached to the container and we need to run some special case
            # code to remove that device.
            if container_is_running:
                self._detach_remaining_block_devices()

            self._remove_all_device_entries()

    def _remove_all_device_entries(self):
        """
        When there are no devices left to remove we need to remove the
        DEVICES line from the CTID.conf file.  This cannot be achieved by
        a vzctl command so the file has to be manipulated and saved.

        :return: None
        """
        filename = '%s/%s.conf' % (CONF.ovz_config_dir, self.instance_id)
        filename = os.path.normpath(filename)
        ct_conf = ovzfile.OVZFile(filename, 644)
        ct_conf.read()
        with ct_conf:
            for line in ct_conf.contents:
                if "DEVICES" in line:
                    ct_conf.contents.remove(line)
                    ct_conf.write()

    def _detach_raw_devices(self, container_is_running=True):
        """
        Remove the associated devices from the container.

        :return:
        """
        LOG.info(_('Detaching raw devices'))

        # storage for block device major/minor pairs for setting in
        # the container config file.
        bdevs_conf = list()
        for dev in self._list_host_devices():
            maj_min = self._get_major_minor(dev)
            bdevs_conf.append(
                self._ovz_block_dev_name_generator(
                    maj_min['major'], maj_min['minor'], 'rw'))

            # TODO(imsplitbit): research to see if running this remove command
            # adhoc is necessary or if just removing the device from the config
            # file is adequate.
            if container_is_running:
                # If the container isn't running this command makes things
                # unhappy.  Very very unhappy.
                self._detach_raw_device(maj_min['major'], maj_min['minor'])

            m = re.match('^(?P<device>/[a-z]+/[a-z]+)(?P<part>\\d*)$', dev)
            device_name = self.mountpoint

            if m and m.group('part'):
                device_name = '%s%s' % (self.mountpoint, m.group('part'))

            self._delete_container_device(device_name)
            self._delete_device(dev)

        if bdevs_conf:
            self._del_block_devices(bdevs_conf, container_is_running)

    def _detach_raw_device(self, major, minor):
        """
        Remove perms from the container for a device based on major and minor
        numbers.

        :param major:
        :param minor:
        :return:
        """
        ovz_utils.execute(
            'vzctl', 'set', self.instance_id, '--devices',
            'b:%s:%s:none' % (major, minor), run_as_root=True)

    def _mknod(self, major, minor, device_name, dev_type='b'):
        """
        Because openvz doesn't support device aliases we manually create
        a block device sharing the same major and minor numbers as the device
        on the host.  This device should be created in the openvz chrooted
        /dev directory so as to allow it to be cleaned up upon deletion.

        :param major:
        :param minor:
        :param dev_type:
        :return: None
        """
        good_types = ['b']
        if dev_type not in good_types:
            raise TypeError(
                _('Cannot make device of type "%(dev_type)s"') % locals())

        device_path = '%s/%s/%s' % (
            CONF.ovz_ve_private_dir, self.instance_id, device_name)
        device_path = os.path.normpath(device_path)
        ovz_utils.execute(
            'mknod', device_path, dev_type, major, minor, run_as_root=True)

    def _delete_device(self, device):
        """
        Utility method for deleting/removing device file

        :param device
        """
        ovz_utils.execute('rm', '-f', device, run_as_root=True)

    def _delete_container_device(self, device):
        """
        Utility method for removing a device from inside the container.  This
        is to happen before you create a device because the existing device
        may or may not have the proper major/minor numbers.  This is obviously
        done on volume detach as well.

        :param device:
        :return:
        """
        device = '%s/%s/%s' % (
            CONF.ovz_ve_private_dir, self.instance_id, device)
        device = os.path.normpath(device)
        self._delete_device(device)
