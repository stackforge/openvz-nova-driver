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

import mox
from nova import exception
from nova import test
from nova.tests.openvz import fakes
from nova.virt.openvz import driver as openvz_conn
from nova.virt.openvz import network as openvz_net
from nova.virt.openvz.network_drivers import network_bridge
from oslo.config import cfg

CONF = cfg.CONF


class OpenVzNetworkTestCase(test.TestCase):
    def setUp(self):
        super(OpenVzNetworkTestCase, self).setUp()
        try:
            CONF.injected_network_template
        except AttributeError as err:
            CONF.register_opt(
                cfg.StrOpt(
                    'injected_network_template',
                    default='nova/virt/interfaces.template',
                    help='Stub for network template for testing purposes')
            )
        CONF.use_ipv6 = False
        self.fake_file = mox.MockAnything()
        self.fake_file.readlines().AndReturn(fakes.FILECONTENTS.split())
        self.fake_file.writelines(mox.IgnoreArg())
        self.fake_file.read().AndReturn(fakes.FILECONTENTS)

    def test_ovz_network_interfaces_add_success(self):
        self.mox.StubOutWithMock(openvz_net.ovzshutdown, 'OVZShutdownFile')
        openvz_net.ovzshutdown.OVZShutdownFile(
            fakes.INSTANCE['id'], mox.IgnoreArg()).AndReturn(
                fakes.FakeOVZShutdownFile(fakes.INSTANCE['id'], 700))
        self.mox.StubOutWithMock(openvz_net.ovzboot, 'OVZBootFile')
        openvz_net.ovzboot.OVZBootFile(
            fakes.INSTANCE['id'], mox.IgnoreArg()).AndReturn(
                fakes.FakeOVZBootFile(fakes.INSTANCE['id'], 700))
        self.mox.StubOutWithMock(openvz_net.ovztc, 'OVZTcRules')
        openvz_net.ovztc.OVZTcRules().MultipleTimes().AndReturn(
            fakes.FakeOVZTcRules())
        self.mox.StubOutWithMock(openvz_net.OVZNetworkInterfaces, '_add_netif')
        openvz_net.OVZNetworkInterfaces._add_netif(
            fakes.INTERFACEINFO[0]['id'],
            mox.IgnoreArg(),
            mox.IgnoreArg(),
            mox.IgnoreArg()).MultipleTimes()
        self.mox.StubOutWithMock(
            openvz_net.OVZNetworkInterfaces, '_set_nameserver')
        self.mox.StubOutWithMock(openvz_net, 'OVZNetworkFile')
        openvz_net.OVZNetworkFile(
            ('/var/lib/vz/private/%s/etc/network/interfaces' %
             fakes.INSTANCE['id'])).AndReturn(
                 fakes.FakeOVZNetworkFile('/etc/network/interfaces'))
        openvz_net.OVZNetworkInterfaces._set_nameserver(
            fakes.INTERFACEINFO[0]['id'], fakes.INTERFACEINFO[0]['dns'])
        self.mox.ReplayAll()
        ifaces = openvz_net.OVZNetworkInterfaces(
            fakes.INTERFACEINFO)
        ifaces.add()

    def test_ovz_network_interfaces_add_ip_success(self):
        self.mox.StubOutWithMock(openvz_net.ovzshutdown, 'OVZShutdownFile')
        openvz_net.ovzshutdown.OVZShutdownFile(
            fakes.INSTANCE['id'], mox.IgnoreArg()).AndReturn(
                fakes.FakeOVZShutdownFile(fakes.INSTANCE['id'], 700))
        self.mox.StubOutWithMock(openvz_net.ovzboot, 'OVZBootFile')
        openvz_net.ovzboot.OVZBootFile(
            fakes.INSTANCE['id'], mox.IgnoreArg()).AndReturn(
                fakes.FakeOVZBootFile(fakes.INSTANCE['id'], 700))
        self.mox.StubOutWithMock(openvz_conn.ovz_utils, 'execute')
        openvz_conn.ovz_utils.execute(
            'vzctl', 'set', fakes.INTERFACEINFO[0]['id'], '--save', '--ipadd',
            fakes.INTERFACEINFO[0]['address'], run_as_root=True).AndReturn(
                ('', fakes.ERRORMSG))
        self.mox.ReplayAll()
        ifaces = openvz_net.OVZNetworkInterfaces(fakes.INTERFACEINFO)
        ifaces._add_ip(
            fakes.INTERFACEINFO[0]['id'], fakes.INTERFACEINFO[0]['address'])

    def test_ovz_network_interfaces_add_ip_failure(self):
        self.mox.StubOutWithMock(openvz_net.ovzshutdown, 'OVZShutdownFile')
        openvz_net.ovzshutdown.OVZShutdownFile(
            fakes.INSTANCE['id'], mox.IgnoreArg()).AndReturn(
                fakes.FakeOVZShutdownFile(fakes.INSTANCE['id'], 700))
        self.mox.StubOutWithMock(openvz_net.ovzboot, 'OVZBootFile')
        openvz_net.ovzboot.OVZBootFile(
            fakes.INSTANCE['id'], mox.IgnoreArg()).AndReturn(
                fakes.FakeOVZBootFile(fakes.INSTANCE['id'], 700))
        self.mox.StubOutWithMock(openvz_conn.ovz_utils, 'execute')
        openvz_conn.ovz_utils.execute(
            'vzctl', 'set', fakes.INTERFACEINFO[0]['id'], '--save',
            '--ipadd', fakes.INTERFACEINFO[0]['address'],
            run_as_root=True).AndRaise(
                exception.InstanceUnacceptable(fakes.ERRORMSG))
        self.mox.ReplayAll()
        ifaces = openvz_net.OVZNetworkInterfaces(fakes.INTERFACEINFO)
        self.assertRaises(
            exception.InstanceUnacceptable, ifaces._add_ip,
            fakes.INTERFACEINFO[0]['id'], fakes.INTERFACEINFO[0]['address'])

    def test_ovz_network_interfaces_add_netif(self):
        self.mox.StubOutWithMock(openvz_net.ovzshutdown, 'OVZShutdownFile')
        openvz_net.ovzshutdown.OVZShutdownFile(
            fakes.INSTANCE['id'], mox.IgnoreArg()).AndReturn(
                fakes.FakeOVZShutdownFile(fakes.INSTANCE['id'], 700))
        self.mox.StubOutWithMock(openvz_net.ovzboot, 'OVZBootFile')
        openvz_net.ovzboot.OVZBootFile(
            fakes.INSTANCE['id'], mox.IgnoreArg()).AndReturn(
                fakes.FakeOVZBootFile(fakes.INSTANCE['id'], 700))
        self.mox.StubOutWithMock(openvz_conn.ovz_utils, 'execute')
        openvz_conn.ovz_utils.execute(
            'vzctl', 'set', fakes.INTERFACEINFO[0]['id'], '--save',
            '--netif_add',
            '%s,,veth%s.%s,%s,%s' % (
                fakes.INTERFACEINFO[0]['name'],
                fakes.INTERFACEINFO[0]['id'],
                fakes.INTERFACEINFO[0]['name'],
                fakes.INTERFACEINFO[0]['mac'],
                fakes.INTERFACEINFO[0]['bridge']),
            run_as_root=True).AndReturn(('', fakes.ERRORMSG))
        self.mox.ReplayAll()
        ifaces = openvz_net.OVZNetworkInterfaces(fakes.INTERFACEINFO)
        ifaces._add_netif(
            fakes.INTERFACEINFO[0]['id'],
            fakes.INTERFACEINFO[0]['name'],
            fakes.INTERFACEINFO[0]['bridge'],
            fakes.INTERFACEINFO[0]['mac']
        )

    def test_filename_factory_debian_variant(self):
        self.mox.StubOutWithMock(openvz_net.ovzshutdown, 'OVZShutdownFile')
        openvz_net.ovzshutdown.OVZShutdownFile(
            fakes.INSTANCE['id'], mox.IgnoreArg()).AndReturn(
                fakes.FakeOVZShutdownFile(fakes.INSTANCE['id'], 700))
        self.mox.StubOutWithMock(openvz_net.ovzboot, 'OVZBootFile')
        openvz_net.ovzboot.OVZBootFile(
            fakes.INSTANCE['id'], mox.IgnoreArg()).AndReturn(
                fakes.FakeOVZBootFile(fakes.INSTANCE['id'], 700))
        self.mox.ReplayAll()
        ifaces = openvz_net.OVZNetworkInterfaces(fakes.INTERFACEINFO)
        for filename in ifaces._filename_factory():
            self.assertFalse('//' in filename)

    def test_set_nameserver_success(self):
        self.mox.StubOutWithMock(openvz_net.ovzshutdown, 'OVZShutdownFile')
        openvz_net.ovzshutdown.OVZShutdownFile(
            fakes.INSTANCE['id'], mox.IgnoreArg()).AndReturn(
                fakes.FakeOVZShutdownFile(fakes.INSTANCE['id'], 700))
        self.mox.StubOutWithMock(openvz_net.ovzboot, 'OVZBootFile')
        openvz_net.ovzboot.OVZBootFile(
            fakes.INSTANCE['id'], mox.IgnoreArg()).AndReturn(
                fakes.FakeOVZBootFile(fakes.INSTANCE['id'], 700))
        self.mox.StubOutWithMock(openvz_net.ovz_utils, 'execute')
        openvz_net.ovz_utils.execute(
            'vzctl', 'set', fakes.INTERFACEINFO[0]['id'], '--save',
            '--nameserver', fakes.INTERFACEINFO[0]['dns'],
            run_as_root=True).AndReturn(('', fakes.ERRORMSG))
        self.mox.ReplayAll()
        ifaces = openvz_net.OVZNetworkInterfaces(fakes.INTERFACEINFO)
        ifaces._set_nameserver(
            fakes.INTERFACEINFO[0]['id'], fakes.INTERFACEINFO[0]['dns'])

    def test_set_nameserver_failure(self):
        self.mox.StubOutWithMock(openvz_net.ovzshutdown, 'OVZShutdownFile')
        openvz_net.ovzshutdown.OVZShutdownFile(
            fakes.INSTANCE['id'], mox.IgnoreArg()).AndReturn(
                fakes.FakeOVZShutdownFile(fakes.INSTANCE['id'], 700))
        self.mox.StubOutWithMock(openvz_net.ovzboot, 'OVZBootFile')
        openvz_net.ovzboot.OVZBootFile(
            fakes.INSTANCE['id'], mox.IgnoreArg()).AndReturn(
                fakes.FakeOVZBootFile(fakes.INSTANCE['id'], 700))
        self.mox.StubOutWithMock(openvz_conn.ovz_utils, 'execute')
        openvz_conn.ovz_utils.execute(
            'vzctl', 'set', fakes.INTERFACEINFO[0]['id'], '--save',
            '--nameserver', fakes.INTERFACEINFO[0]['dns'],
            run_as_root=True).AndRaise(exception.InstanceUnacceptable(
                fakes.ERRORMSG))
        self.mox.ReplayAll()
        ifaces = openvz_net.OVZNetworkInterfaces(fakes.INTERFACEINFO)
        self.assertRaises(
            exception.InstanceUnacceptable, ifaces._set_nameserver,
            fakes.INTERFACEINFO[0]['id'], fakes.INTERFACEINFO[0]['dns'])

    def test_ovz_network_bridge_driver_plug(self):
        self.mox.StubOutWithMock(
            openvz_conn.linux_net.LinuxBridgeInterfaceDriver,
            'ensure_vlan_bridge'
        )
        openvz_conn.linux_net.LinuxBridgeInterfaceDriver.ensure_vlan_bridge(
            mox.IgnoreArg(), mox.IgnoreArg(), mox.IgnoreArg()
        )
        self.mox.ReplayAll()
        driver = network_bridge.OVZNetworkBridgeDriver()
        for network, mapping in fakes.NETWORKINFO:
            driver.plug(fakes.INSTANCE, network, mapping)
