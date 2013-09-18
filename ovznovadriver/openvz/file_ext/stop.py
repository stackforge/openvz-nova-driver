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
from ovznovadriver.openvz import file as ovzfile
import os
from oslo.config import cfg

CONF = cfg.CONF


class OVZContainerStopScript(ovzfile.OVZFile):
    def __init__(self, instance_id):
        filename = "%s/%s.stop" % (CONF.ovz_config_dir, instance_id)
        filename = os.path.abspath(filename)
        super(OVZContainerStopScript, self).__init__(filename)
