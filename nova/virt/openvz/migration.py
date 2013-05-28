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
from nova import exception
from nova.openstack.common import log as logging
from nova.virt.openvz import utils as ovz_utils
import os
from oslo.config import cfg

CONF = cfg.CONF
LOG = logging.getLogger('nova.virt.openvz.volume')


class OVZMigration(object):
    def __init__(self, instance, interfaces, block_device_info=None,
                 dest=None, live=False):
        self.instance = instance
        self.interfaces = interfaces
        self.block_device_info = block_device_info
        self.destination_host = dest
        self.live_migration = live

        # Hook in archives if migrations aren't done with the root user
        # because we need to preserve ownerships on the files inside
        # the container's root.
        if CONF.ovz_migration_user == 'root':
            self.unprivileged_user = False
            self.instance_tarfile = None
        else:
            self.unprivileged_user = True
            self.instance_tarfile = os.path.abspath('%s/%s.tar' %
                                                    (CONF.ovz_tmp_dir,
                                                     self.instance['id']))

        self.instance_parent = CONF.ovz_ve_private_dir
        self.instance_source = os.path.abspath(
            '%s/%s' % (self.instance_parent, self.instance['id']))
        self.dumpdir_name = '%s-dumpdir' % instance['id']
        self.dumpdir_parent = CONF.ovz_tmp_dir
        self.dumpdir = os.path.abspath('%s/%s' % (self.dumpdir_parent,
                                                  self.dumpdir_name))
        self.i_dumpdir = os.path.abspath('%s/instance' % self.dumpdir)
        self.dumpfile = os.path.abspath(
            '%s/dump.%s' % (self.i_dumpdir, self.instance['id']))
        self.q_dumpdir = os.path.abspath('%s/quotas' % self.dumpdir)
        self.qdumpfile = os.path.abspath(
            '%s/qdump.%s' % (self.q_dumpdir, self.instance['id']))
        self.as_dumpdir = os.path.abspath('%s/ascripts' % self.dumpdir)
        self.actionscripts = ['start', 'stop', 'mount', 'umount', 'premount',
                              'postumount', 'boot', 'shutdown', 'conf',
                              'ext_storage']
        self.dumpdir_tarfile = '%s.tar' % self.dumpdir

    def dump_and_transfer_instance(self):
        """
        Put all the pieces together to dump the instance and make the dump
        ready for transfer to the destination host.
        """
        self.make_dump_dir()

        # Begin the dumping process
        if self.live_migration:
            # this is a live migration so dump current memory and network
            # state.
            LOG.debug(_('Making container backup for %s') %
                      self.instance['id'])
            self.dump()

        if self.unprivileged_user:
            # we are transferring the instance by using an unprivileged user
            # so we need to tar the instance to preserve ownerships and
            # permissions
            LOG.debug(_('self.unprivileged_user is True'))
            self.tar_instance()

        # Take all instance scripts from /etc/vz/conf and put them in a
        # tarball for transfer.
        LOG.debug(_('Making action script backups for %s') %
                  self.instance['id'])
        self.backup_action_scripts()
        LOG.debug(_('Archiving misc files: %s') % self.instance['id'])
        self.tar_dumpdir()
        LOG.debug(_('Migration image created'))

    def undump_instance(self):
        """
        Take the pieces of a dump archive and put them back in place.
        """
        # If a non-root user was used to transfer the files then we
        # need to move everything to where it is expected to be.
        LOG.debug(_('Restoring action scripts from archive: %s') %
                  self.dumpdir_tarfile)
        self.untar_dumpdir()
        LOG.debug(_('Restoring action scripts for %s') % self.instance['id'])
        self.restore_action_scripts()

        if self.unprivileged_user:
            LOG.debug(_('Restoring container from tar: %s') %
                      self.instance['id'])
            self.untar_instance()

        if self.live_migration:
            LOG.debug(_('Restoring container state from dump for %s') %
                      self.instance['id'])
            self.undump()
            LOG.debug(_('Resuming container: %s') % self.instance['id'])
            self.resume()

        LOG.debug(_('Done restoring instance %s') % self.instance['id'])

    def make_dump_dir(self):
        """
        Make our dump locations
        """
        LOG.debug(_('Making dump location for %s') % self.instance['id'])
        ovz_utils.make_dir(self.dumpdir)
        ovz_utils.make_dir(self.i_dumpdir)
        ovz_utils.make_dir(self.q_dumpdir)
        ovz_utils.make_dir(self.as_dumpdir)
        LOG.debug(_('Done making location for %s') % self.instance['id'])

    def cleanup_source(self):
        """
        Helper method to wrap up all methods required to clean up a
        migration.
        """
        if self.live_migration:
            self.kill()

        self.cleanup_files()

    def cleanup_destination(self):
        """
        Do anything you need to do on the destination host to clean it up
        """
        self.cleanup_files()

    def dump(self):
        """
        Create a vz dump file from a container.  This is the file that we
        transfer to do a full migration
        """
        LOG.debug(_('Dumping instance %s') % self.instance['id'])
        ovz_utils.execute('vzctl', 'chkpnt', self.instance['id'],
                          '--dump', '--dumpfile', self.dumpfile,
                          run_as_root=True)
        LOG.debug(_('Dumped instance %(instance_id)s to %(dumpfile)s') %
                  {'instance_id': self.instance['id'],
                   'dumpfile': self.dumpfile})

    def undump(self):
        """
        Restore a VZ from a dump file
        """
        LOG.debug(_('Undumping instance %s') % self.instance['id'])
        ovz_utils.execute('vzctl', 'restore', self.instance['id'], '--undump',
                          '--dumpfile', self.dumpfile, '--skip_arpdetect',
                          run_as_root=True)
        LOG.debug(_('Undumped instance %(instance_id)s from %(dumpfile)s') %
                  {'instance_id': self.instance['id'],
                   'dumpfile': self.dumpfile})

    def resume(self):
        """
        Resume a container from an undumped migration
        """
        LOG.debug(_('Resuming instance %s') % self.instance['id'])
        ovz_utils.execute('vzctl', 'restore', self.instance['id'],
                          '--resume', run_as_root=True)
        LOG.debug(_('Resumed instance %s') % self.instance['id'])

    def kill(self):
        """
        This is used to stop a container once it's suspended without having to
        resume it to properly destroy it
        """
        LOG.debug(_('Killing instance %s') % self.instance['id'])
        ovz_utils.execute('vzctl', 'chkpnt', self.instance['id'],
                          '--kill', run_as_root=True)
        LOG.debug(_('Killed instance %s') % self.instance['id'])

    def quotadump(self):
        """
        Dump the quotas for containers
        """
        LOG.debug(_('Dumping quotas for %s') % self.instance['id'])
        ovz_utils.execute('vzdqdump', self.instance['id'], '-U', '-G',
                          '-T', '>', self.qdumpfile, run_as_root=True)
        LOG.debug(_('Dumped quotas for %s') % self.instance['id'])

    def quotaload(self):
        """
        Load quotas from quota file
        """
        LOG.debug(_('Loading quotas for %s') % self.instance['id'])
        ovz_utils.execute('vzdqload', self.instance['id'], '-U', '-G', '-T',
                          '<', self.qdumpfile, run_as_root=True)
        LOG.debug(_('Loaded quotas for %s') % self.instance['id'])

    def quotaenable(self):
        """
        enable quotas for a given container
        """
        LOG.debug(_('Enabling quotas for %s') % self.instance['id'])
        ovz_utils.execute('vzquota', 'reload2',
                          self.instance['id'], run_as_root=True)
        LOG.debug(_('Enabled quotas for %s') % self.instance['id'])

    def quota_init(self):
        """
        Initialize quotas for instance
        """
        LOG.debug(_('Initializing quotas for %s') % self.instance['id'])
        ovz_utils.execute('vzctl', 'quotainit', self.instance['id'])
        LOG.debug(_('Initialized quotas for %s') % self.instance['id'])

    def quota_on(self):
        """
        Turn on quotas for instance
        """
        LOG.debug(_('Turning on quotas for %s') % self.instance['id'])
        ovz_utils.execute('vzctl', 'quotaon', self.instance['id'],
                          run_as_root=True)
        LOG.debug(_('Turned on quotas for %s') % self.instance['id'])

    def backup_action_scripts(self):
        """
        Take the action scripts with us in the backup/migration
        """
        LOG.debug(_('Copying actionscripts into place'))
        for a_script in self.actionscripts:
            a_script = os.path.abspath(
                '%s/%s.%s' % (CONF.ovz_config_dir, self.instance['id'],
                              a_script))
            if os.path.exists(a_script):
                LOG.debug(_('Copying actionscript: %s') % a_script)
                ovz_utils.copy(a_script, self.as_dumpdir)
                LOG.debug(_('Copied actionscript: %s') % a_script)
        LOG.debug(_('Copied actionscripts into place'))

    def restore_action_scripts(self):
        """
        Put the action scripts back into place
        """
        LOG.debug(_('Restoring actionscripts into place'))
        for a_script in self.actionscripts:
            a_script = os.path.abspath('%s/%s.%s' % (self.as_dumpdir,
                                                     self.instance['id'],
                                                     a_script))
            if os.path.exists(a_script):
                LOG.debug(_('Restoring actionscript: %s') % a_script)
                ovz_utils.copy(a_script, CONF.ovz_config_dir)
                LOG.debug(_('Restored actionscript: %s') % a_script)
        LOG.debug(_('Restored actionscripts into place'))

    def send(self):
        """
        Use the configured transport to transfer the image from the src to
        the dest host.  This will run on the source host.
        """
        # Set the destination and source for the instance transfer
        if self.unprivileged_user:
            # Since we tarred up the instance we don't need to skip rsync'ing
            # any paths.
            src_path = self.instance_tarfile
            dest_path = CONF.ovz_tmp_dir
        else:
            src_path = self.instance_source
            dest_path = self.instance_parent

        transport_instance = self._setup_transport(src_path, dest_path)
        transport_instance.send()

        # Set the destination and source for the dumpdir transfer
        src_path = self.dumpdir_tarfile
        dest_path = self.dumpdir_parent

        transport_instance_scripts = self._setup_transport(src_path, dest_path)
        transport_instance_scripts.send()

    def receive(self):
        """
        Use the configured transport to transfer the image form the src to
        dest host.  This will run on the destination host
        """
        # Right now we don't have a transport that needs this.
        raise NotImplementedError()

    def cleanup_files(self):
        """
        Remove the files in the OpenVz temp dir
        """
        LOG.debug(_('Cleaning migration files for %s') % self.instance['id'])
        ovz_utils.execute('rm', '-rf', self.dumpdir, run_as_root=True)
        ovz_utils.execute('rm', '-f', self.dumpdir_tarfile, run_as_root=True)
        if self.instance_tarfile:
            ovz_utils.execute('rm', '-f', self.instance_tarfile,
                              run_as_root=True)
        LOG.debug(
            _('Cleaned up migration files for %s') % self.instance['id'])

    def tar_instance(self):
        """
        Not an optimal way to do this but if you aren't using the root user
        to rsync the files from host to host you need to preserve the
        permissions and ownerships thus tar is your only hope.
        """
        # Create our batch volume operations object
        LOG.debug(_('Tarring up instance: %s') % self.instance['id'])
        ovz_utils.tar(self.instance['id'], self.instance_tarfile,
                      self.instance_parent)
        LOG.debug(_('Tarred up instance: %s') % self.instance['id'])

    def tar_dumpdir(self):
        """
        Archive the instance action scripts
        """
        LOG.debug(_('Tarring up instance dumpdir: %s') % self.dumpdir)
        ovz_utils.tar(self.dumpdir_name, self.dumpdir_tarfile,
                      self.dumpdir_parent)
        LOG.debug(_('Tarred up instance dumpdir: %s') % self.dumpdir)

    def untar_instance(self):
        """
        Expand the tarball from the instance and expand it into place
        """
        LOG.debug(_('Untarring instance: %s') % self.instance_source)
        ovz_utils.untar(self.instance_tarfile, self.instance_parent)
        LOG.debug(_('Untarred instance: %s') % self.instance_source)

    def untar_dumpdir(self):
        """
        Expand the dumpdir into place on the destination machine
        """
        LOG.debug(_('Untarring instance dumpdir: %s') % self.dumpdir)
        ovz_utils.untar(self.dumpdir_tarfile, self.dumpdir_parent)
        LOG.debug(_('Untarred instance dumpdir: %s') % self.dumpdir)

    def _setup_transport(self, src_path, dest_path, skip_list=None):
        if CONF.ovz_migration_transport == 'rsync':
            from nova.virt.openvz.migration_drivers import rsync
            return rsync.OVZMigrationRsyncTransport(
                src_path, dest_path, self.instance['id'],
                self.destination_host, skip_list)
        else:
            LOG.error(
                _('I do not understand your migration transport: %s') %
                CONF.ovz_migration_transport)
            raise exception.MigrationError(
                _('No valid migration transport: %s') %
                CONF.ovz_migration_transport)
