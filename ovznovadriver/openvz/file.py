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
from ovznovadriver.localization import _
from nova.openstack.common import log as logging
from ovznovadriver.openvz import utils as ovz_utils
import os
from oslo.config import cfg

CONF = cfg.CONF

LOG = logging.getLogger('ovznovadriver.openvz.file')


class OVZFile(object):
    """
    This is a generic file class for wrapping up standard file operations that
    may need to run on files associated with OpenVz
    """
    def __init__(self, filename, permissions):
        self.filename = os.path.abspath(filename)
        self.contents = []
        self.permissions = permissions

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

    def read(self):
        """
        Open the file for reading only and read it's contents into the instance
        attribute self.contents
        """
        try:
            with open(self.filename, 'r') as fh:
                self.contents = fh.read().split('\n')
        except Exception as err:
            LOG.error(_('Output from open: %s') % err)
            raise exception.FileNotFound(
                _('Failed to read %s') % self.filename)

    def write(self):
        """
        Because self.contents may or may not get manipulated throughout the
        process, this is a method used to dump the contents of self.contents
        back into the file that this object represents.
        """
        LOG.debug(_('File contents before write: %s') %
                  '\n'.join(self.contents))
        try:
            with open(self.filename, 'w') as fh:
                fh.writelines('\n'.join(self.contents) + '\n')
        except Exception as err:
            LOG.error(_('Output from open: %s') % err)
            raise exception.FileNotFound(
                _('Failed to write %s') % self.filename)

    def touch(self):
        """
        There are certain conditions where we create an OVZFile object but that
        file may or may not exist and this provides us with a way to create
        that file if it doesn't exist.

        Run the command:

        touch <filename>

        If this does not run an exception is raised as a failure to touch a
        file when you intend to do so would cause a serious failure of
        procedure.
        """
        self.make_path()
        ovz_utils.execute('touch', self.filename, run_as_root=True)

    def make_proper_script(self):
        """
        OpenVz mount and umount files must be properly formatted scripts.
        Prepend the proper shell script header to the files
        """
        if not self.contents[:1] == ['#!/bin/sh']:
            self.prepend('#!/bin/sh')

    def append(self, contents):
        """
        Add the argument contents to the end of self.contents
        """
        if not isinstance(contents, list):
            contents = [str(contents)]
        self.contents += contents
        LOG.debug(_('File contents: %s') % self.contents)

    def prepend(self, contents):
        """
        Add the argument contents to the beginning of self.contents
        """
        if not isinstance(contents, list):
            contents = [str(contents)]
        self.contents = contents + self.contents
        LOG.debug(_('File contents: %s') % self.contents)

    def delete(self, contents):
        """
        Delete the argument contents from self.contents if they exist.
        """
        if isinstance(contents, list):
            LOG.debug(_('A list was passed to delete: %s') % contents)
            for line in contents:
                LOG.debug(_('Line: %(line)s \tFound in: %(contents)s') %
                          locals())
                self.remove_line(line)
        else:
            LOG.debug(_('A string was passed to delete: %s') % contents)
            self.remove_line(contents)

    def remove_line(self, line):
        """
        Simple helper method to actually do the removal of a line from an array
        """
        LOG.debug(_('Removing line if present: %s') % line)
        if line in self.contents:
            LOG.debug(_('Line found: %s') % line)
            self.contents.remove(line)
            LOG.debug(_('Line removed: %(line)s \t%(contents)s') %
                      {'line': line, 'contents': self.contents})

    def set_permissions(self, permissions):
        """
        Because nova runs as an unprivileged user we need a way to mangle
        permissions on files that may be owned by root for manipulation

        Run the command:

        chmod <permissions> <filename>

        If this doesn't run an exception is raised because the permissions not
        being set to what the application believes they are set to can cause
        a failure of epic proportions.
        """
        ovz_utils.execute('chmod', permissions, self.filename,
                          run_as_root=True)

    def make_path(self, path=None):
        """
        Helper method for an OVZFile object to be able to create the path for
        self.filename if it doesn't exist before running touch()
        """
        if not path:
            path = self.filename
        basedir = os.path.dirname(path)
        ovz_utils.make_dir(basedir)

    def set_contents(self, new_contents):
        """
        We need the ability to overwrite all contents and this is the cleanest
        way while allowing some validation.
        """
        if isinstance(new_contents, str):
            LOG.debug(_('Type of new_contents is: str'))
            self.contents = new_contents.split('\n')
            LOG.debug(_('Contents of file: %s') % self.contents)
        elif isinstance(new_contents, list):
            LOG.debug(_('Type of new_contents is: list'))
            self.contents = new_contents
        else:
            raise TypeError(_("I don't know what to do with this type: %s") %
                            type(new_contents))

    def run_contents(self, raise_on_error=True):
        # Because only approved commands in rootwrap can be executed we
        # don't need to worry about unwanted command injection
        for line in self.contents:
            if len(line) > 0:
                if  line[0] != '#' and line != '\n':
                    line = line.strip()
                    try:
                        LOG.debug(
                            _('Running line from %(filename)s: %(line)s') %
                            {'filename': self.filename, 'line': line})
                        line = line.split()
                        ovz_utils.execute(
                            *line, run_as_root=True,
                            raise_on_error=raise_on_error)
                    except exception.InstanceUnacceptable as err:
                        LOG.error(_('Cannot execute: %s') % line)
                        LOG.error(err)

    def exists(self):
        """
        Simple wrapper for os.path.exists
        """
        return os.path.exists(self.filename)
