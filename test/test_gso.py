#!/usr/bin/env python3
"""GSO functional tests"""

#
# Add tests for:
# - GSO
# - Verify that sending Jumbo frame without GSO enabled correctly
# - Verify that sending Jumbo frame with GSO enabled correctly
# - Verify that sending Jumbo frame with GSO enabled only on ingress interface
#
import unittest

from scapy.packet import Raw
from scapy.layers.inet6 import IPv6, Ether, IP, UDP, ICMPv6PacketTooBig
from scapy.layers.inet6 import ipv6nh, IPerror6
from scapy.layers.inet import TCP, ICMP
from scapy.layers.vxlan import VXLAN
from scapy.data import ETH_P_IP, ETH_P_IPV6, ETH_P_ARP

from framework import VppTestCase, VppTestRunner
from vpp_object import VppObject
from vpp_interface import VppInterface
from vpp_ip import DpoProto
from vpp_ip_route import VppIpRoute, VppRoutePath, FibPathProto
from vpp_vxlan_tunnel import VppVxlanTunnel
from socket import AF_INET, AF_INET6, inet_pton
from util import reassemble4


""" Test_gso is a subclass of VPPTestCase classes.
    GSO tests.
"""


class TestGSO(VppTestCase):
    """ GSO Test Case """

    def __init__(self, *args):
        VppTestCase.__init__(self, *args)

    @classmethod
    def setUpClass(self):
        super(TestGSO, self).setUpClass()
        res = self.create_pg_interfaces(range(2))
        res_gso = self.create_pg_interfaces(range(2, 4), 1, 1460)
        self.create_pg_interfaces(range(4, 5), 1, 8940)
        self.pg_interfaces.append(res[0])
        self.pg_interfaces.append(res[1])
        self.pg_interfaces.append(res_gso[0])
        self.pg_interfaces.append(res_gso[1])

    @classmethod
    def tearDownClass(self):
        super(TestGSO, self).tearDownClass()

    def setUp(self):
        super(TestGSO, self).setUp()
        for i in self.pg_interfaces:
            i.admin_up()
            i.config_ip4()
            i.config_ip6()
            i.disable_ipv6_ra()
            i.resolve_arp()
            i.resolve_ndp()

        self.single_tunnel_bd = 10
        self.vxlan = VppVxlanTunnel(self, src=self.pg0.local_ip4,
                                    dst=self.pg0.remote_ip4,
                                    vni=self.single_tunnel_bd)
        self.vxlan.add_vpp_config()

        self.vxlan2 = VppVxlanTunnel(self, src=self.pg0.local_ip6,
                                     dst=self.pg0.remote_ip6,
                                     vni=self.single_tunnel_bd)
        self.vxlan2.add_vpp_config()

    def tearDown(self):
        super(TestGSO, self).tearDown()
        if not self.vpp_dead:
            for i in self.pg_interfaces:
                i.unconfig_ip4()
                i.unconfig_ip6()
                i.admin_down()

    def test_gso(self):
        """ GSO test """
        #
        # Send jumbo frame with gso disabled and DF bit is set
        #
        p4 = (Ether(src=self.pg0.remote_mac, dst=self.pg0.local_mac) /
              IP(src=self.pg0.remote_ip4, dst=self.pg1.remote_ip4,
                 flags='DF') /
              TCP(sport=1234, dport=1234) /
              Raw(b'\xa5' * 65200))

        rxs = self.send_and_expect(self.pg0, [p4], self.pg0)

        for rx in rxs:
            self.assertEqual(rx[Ether].src, self.pg0.local_mac)
            self.assertEqual(rx[Ether].dst, self.pg0.remote_mac)
            self.assertEqual(rx[IP].src, self.pg0.local_ip4)
            self.assertEqual(rx[IP].dst, self.pg0.remote_ip4)
            self.assertEqual(rx[ICMP].type, 3)  # "dest-unreach"
            self.assertEqual(rx[ICMP].code, 4)  # "fragmentation-needed"

        #
        # Send jumbo frame with gso enabled and DF bit is set
        # input and output interfaces support GSO
        #
        p41 = (Ether(src=self.pg2.remote_mac, dst=self.pg2.local_mac) /
               IP(src=self.pg2.remote_ip4, dst=self.pg3.remote_ip4,
                  flags='DF') /
               TCP(sport=1234, dport=1234) /
               Raw(b'\xa5' * 65200))

        rxs = self.send_and_expect(self.pg2, [p41], self.pg3)

        for rx in rxs:
            self.assertEqual(rx[Ether].src, self.pg3.local_mac)
            self.assertEqual(rx[Ether].dst, self.pg3.remote_mac)
            self.assertEqual(rx[IP].src, self.pg2.remote_ip4)
            self.assertEqual(rx[IP].dst, self.pg3.remote_ip4)
            self.assertEqual(rx[IP].len, 65240)  # 65200 + 20 (IP) + 20 (TCP)
            self.assertEqual(rx[TCP].sport, 1234)
            self.assertEqual(rx[TCP].dport, 1234)

        #
        # ipv6
        #
        p61 = (Ether(src=self.pg2.remote_mac, dst=self.pg2.local_mac) /
               IPv6(src=self.pg2.remote_ip6, dst=self.pg3.remote_ip6) /
               TCP(sport=1234, dport=1234) /
               Raw(b'\xa5' * 65200))

        rxs = self.send_and_expect(self.pg2, [p61], self.pg3)

        for rx in rxs:
            self.assertEqual(rx[Ether].src, self.pg3.local_mac)
            self.assertEqual(rx[Ether].dst, self.pg3.remote_mac)
            self.assertEqual(rx[IPv6].src, self.pg2.remote_ip6)
            self.assertEqual(rx[IPv6].dst, self.pg3.remote_ip6)
            self.assertEqual(rx[IPv6].plen, 65220)  # 65200 + 20 (TCP)
            self.assertEqual(rx[TCP].sport, 1234)
            self.assertEqual(rx[TCP].dport, 1234)

        #
        # Send jumbo frame with gso enabled only on input interface
        # and DF bit is set. GSO packet will be chunked into gso_size
        # data payload
        #
        self.vapi.feature_gso_enable_disable(self.pg0.sw_if_index)
        p42 = (Ether(src=self.pg2.remote_mac, dst=self.pg2.local_mac) /
               IP(src=self.pg2.remote_ip4, dst=self.pg0.remote_ip4,
                  flags='DF') /
               TCP(sport=1234, dport=1234) /
               Raw(b'\xa5' * 65200))

        rxs = self.send_and_expect(self.pg2, [p42], self.pg0, 45)
        size = 0
        for rx in rxs:
            self.assertEqual(rx[Ether].src, self.pg0.local_mac)
            self.assertEqual(rx[Ether].dst, self.pg0.remote_mac)
            self.assertEqual(rx[IP].src, self.pg2.remote_ip4)
            self.assertEqual(rx[IP].dst, self.pg0.remote_ip4)
            self.assertEqual(rx[TCP].sport, 1234)
            self.assertEqual(rx[TCP].dport, 1234)

        size = rxs[44][TCP].seq + rxs[44][IP].len - 20 - 20
        self.assertEqual(size, 65200)

        #
        # ipv6
        #
        p62 = (Ether(src=self.pg2.remote_mac, dst=self.pg2.local_mac) /
               IPv6(src=self.pg2.remote_ip6, dst=self.pg0.remote_ip6) /
               TCP(sport=1234, dport=1234) /
               Raw(b'\xa5' * 65200))

        rxs = self.send_and_expect(self.pg2, [p62], self.pg0, 45)
        size = 0
        for rx in rxs:
            self.assertEqual(rx[Ether].src, self.pg0.local_mac)
            self.assertEqual(rx[Ether].dst, self.pg0.remote_mac)
            self.assertEqual(rx[IPv6].src, self.pg2.remote_ip6)
            self.assertEqual(rx[IPv6].dst, self.pg0.remote_ip6)
            self.assertEqual(rx[TCP].sport, 1234)
            self.assertEqual(rx[TCP].dport, 1234)

        size = rxs[44][TCP].seq + rxs[44][IPv6].plen - 20
        self.assertEqual(size, 65200)

        #
        # Send jumbo frame with gso enabled only on input interface
        # and DF bit is unset. GSO packet will be fragmented.
        #
        self.vapi.sw_interface_set_mtu(self.pg1.sw_if_index, [576, 0, 0, 0])
        self.vapi.feature_gso_enable_disable(self.pg1.sw_if_index)

        p43 = (Ether(src=self.pg2.remote_mac, dst=self.pg2.local_mac) /
               IP(src=self.pg2.remote_ip4, dst=self.pg1.remote_ip4) /
               TCP(sport=1234, dport=1234) /
               Raw(b'\xa5' * 65200))

        rxs = self.send_and_expect(self.pg2, [p43], self.pg1, 119)
        size = 0
        for rx in rxs:
            self.assertEqual(rx[Ether].src, self.pg1.local_mac)
            self.assertEqual(rx[Ether].dst, self.pg1.remote_mac)
            self.assertEqual(rx[IP].src, self.pg2.remote_ip4)
            self.assertEqual(rx[IP].dst, self.pg1.remote_ip4)
            size += rx[IP].len - 20
        size -= 20  # TCP header
        self.assertEqual(size, 65200)

        #
        # IPv6
        # Send jumbo frame with gso enabled only on input interface.
        # ICMPv6 Packet Too Big will be sent back to sender.
        #
        self.vapi.sw_interface_set_mtu(self.pg1.sw_if_index, [1280, 0, 0, 0])
        p63 = (Ether(src=self.pg2.remote_mac, dst=self.pg2.local_mac) /
               IPv6(src=self.pg2.remote_ip6, dst=self.pg1.remote_ip6) /
               TCP(sport=1234, dport=1234) /
               Raw(b'\xa5' * 65200))

        rxs = self.send_and_expect(self.pg2, [p63], self.pg2, 1)
        for rx in rxs:
            self.assertEqual(rx[Ether].src, self.pg2.local_mac)
            self.assertEqual(rx[Ether].dst, self.pg2.remote_mac)
            self.assertEqual(rx[IPv6].src, self.pg2.local_ip6)
            self.assertEqual(rx[IPv6].dst, self.pg2.remote_ip6)
            self.assertEqual(rx[IPv6].plen, 1240)  # MTU - IPv6 header
            self.assertEqual(ipv6nh[rx[IPv6].nh], "ICMPv6")
            self.assertEqual(rx[ICMPv6PacketTooBig].mtu, 1280)
            self.assertEqual(rx[IPerror6].src, self.pg2.remote_ip6)
            self.assertEqual(rx[IPerror6].dst, self.pg1.remote_ip6)
            self.assertEqual(rx[IPerror6].plen - 20, 65200)

        #
        # Send jumbo frame with gso enabled only on input interface with 9K MTU
        # and DF bit is unset. GSO packet will be fragmented. MSS is 8960. GSO
        # size will be min(MSS, 2048 - 14 - 20) vlib_buffer_t size
        #
        self.vapi.sw_interface_set_mtu(self.pg1.sw_if_index, [9000, 0, 0, 0])
        self.vapi.sw_interface_set_mtu(self.pg4.sw_if_index, [9000, 0, 0, 0])
        p44 = (Ether(src=self.pg4.remote_mac, dst=self.pg4.local_mac) /
               IP(src=self.pg4.remote_ip4, dst=self.pg1.remote_ip4) /
               TCP(sport=1234, dport=1234) /
               Raw(b'\xa5' * 65200))

        self.pg1.enable_capture()
        rxs = self.send_and_expect(self.pg4, [p44], self.pg1, 33)
        size = 0
        for rx in rxs:
            self.assertEqual(rx[Ether].src, self.pg1.local_mac)
            self.assertEqual(rx[Ether].dst, self.pg1.remote_mac)
            self.assertEqual(rx[IP].src, self.pg4.remote_ip4)
            self.assertEqual(rx[IP].dst, self.pg1.remote_ip4)
        size = rxs[32][TCP].seq + rxs[32][IP].len - 20 - 20
        self.assertEqual(size, 65200)

        #
        # IPv6
        #
        p64 = (Ether(src=self.pg4.remote_mac, dst=self.pg4.local_mac) /
               IPv6(src=self.pg4.remote_ip6, dst=self.pg1.remote_ip6) /
               TCP(sport=1234, dport=1234) /
               Raw(b'\xa5' * 65200))

        self.pg1.enable_capture()
        rxs = self.send_and_expect(self.pg4, [p64], self.pg1, 34)
        size = 0
        for rx in rxs:
            self.assertEqual(rx[Ether].src, self.pg1.local_mac)
            self.assertEqual(rx[Ether].dst, self.pg1.remote_mac)
            self.assertEqual(rx[IPv6].src, self.pg4.remote_ip6)
            self.assertEqual(rx[IPv6].dst, self.pg1.remote_ip6)
        size = rxs[33][TCP].seq + rxs[33][IPv6].plen - 20
        self.assertEqual(size, 65200)

    def test_gso_vxlan(self):
        """ GSO VXLAN test """
        self.logger.info(self.vapi.cli("sh int addr"))
        #
        # Send jumbo frame with gso enabled only on input interface and
        # create VXLAN VTEP on VPP pg0, and put vxlan_tunnel0 and pg2
        # into BD.
        #
        self.vapi.sw_interface_set_l2_bridge(
            rx_sw_if_index=self.vxlan.sw_if_index, bd_id=self.single_tunnel_bd)
        self.vapi.sw_interface_set_l2_bridge(
            rx_sw_if_index=self.pg2.sw_if_index, bd_id=self.single_tunnel_bd)
        self.vapi.feature_gso_enable_disable(self.pg0.sw_if_index)

        #
        # IPv4/IPv4 - VXLAN
        #
        p45 = (Ether(src=self.pg2.remote_mac, dst="02:fe:60:1e:a2:79") /
               IP(src=self.pg2.remote_ip4, dst="172.16.3.3", flags='DF') /
               TCP(sport=1234, dport=1234) /
               Raw(b'\xa5' * 65200))

        self.pg0.enable_capture()
        rxs = self.send_and_expect(self.pg2, [p45], self.pg0, 45)
        size = 0
        for rx in rxs:
            self.assertEqual(rx[Ether].src, self.pg0.local_mac)
            self.assertEqual(rx[Ether].dst, self.pg0.remote_mac)
            self.assertEqual(rx[IP].src, self.pg0.local_ip4)
            self.assertEqual(rx[IP].dst, self.pg0.remote_ip4)
            self.assertEqual(rx[VXLAN].vni, 10)
            inner = rx[VXLAN].payload
            self.assertEqual(inner[Ether].src, self.pg2.remote_mac)
            self.assertEqual(inner[Ether].dst, "02:fe:60:1e:a2:79")
            self.assertEqual(inner[IP].src, self.pg2.remote_ip4)
            self.assertEqual(inner[IP].dst, "172.16.3.3")
            size += inner[IP].len - 20 - 20
        self.assertEqual(size, 65200)

        #
        # IPv4/IPv6 - VXLAN
        #
        p65 = (Ether(src=self.pg2.remote_mac, dst="02:fe:60:1e:a2:79") /
               IPv6(src=self.pg2.remote_ip6, dst="fd01:3::3") /
               TCP(sport=1234, dport=1234) /
               Raw(b'\xa5' * 65200))

        self.pg0.enable_capture()
        rxs = self.send_and_expect(self.pg2, [p65], self.pg0, 45)
        size = 0
        for rx in rxs:
            self.assertEqual(rx[Ether].src, self.pg0.local_mac)
            self.assertEqual(rx[Ether].dst, self.pg0.remote_mac)
            self.assertEqual(rx[IP].src, self.pg0.local_ip4)
            self.assertEqual(rx[IP].dst, self.pg0.remote_ip4)
            self.assertEqual(rx[VXLAN].vni, 10)
            inner = rx[VXLAN].payload
            self.assertEqual(inner[Ether].src, self.pg2.remote_mac)
            self.assertEqual(inner[Ether].dst, "02:fe:60:1e:a2:79")
            self.assertEqual(inner[IPv6].src, self.pg2.remote_ip6)
            self.assertEqual(inner[IPv6].dst, "fd01:3::3")
            size += inner[IPv6].plen - 20
        self.assertEqual(size, 65200)

        #
        # disable ipv4/vxlan
        #
        self.vxlan.remove_vpp_config()

        #
        # enable ipv6/vxlan
        #
        self.vapi.sw_interface_set_l2_bridge(
            rx_sw_if_index=self.vxlan2.sw_if_index,
            bd_id=self.single_tunnel_bd)

        #
        # IPv6/IPv4 - VXLAN
        #
        p46 = (Ether(src=self.pg2.remote_mac, dst="02:fe:60:1e:a2:79") /
               IP(src=self.pg2.remote_ip4, dst="172.16.3.3", flags='DF') /
               TCP(sport=1234, dport=1234) /
               Raw(b'\xa5' * 65200))

        self.pg0.enable_capture()
        rxs = self.send_and_expect(self.pg2, [p46], self.pg0, 45)
        size = 0
        for rx in rxs:
            self.assertEqual(rx[Ether].src, self.pg0.local_mac)
            self.assertEqual(rx[Ether].dst, self.pg0.remote_mac)
            self.assertEqual(rx[IPv6].src, self.pg0.local_ip6)
            self.assertEqual(rx[IPv6].dst, self.pg0.remote_ip6)
            self.assertEqual(rx[VXLAN].vni, 10)
            inner = rx[VXLAN].payload
            self.assertEqual(inner[Ether].src, self.pg2.remote_mac)
            self.assertEqual(inner[Ether].dst, "02:fe:60:1e:a2:79")
            self.assertEqual(inner[IP].src, self.pg2.remote_ip4)
            self.assertEqual(inner[IP].dst, "172.16.3.3")
            size += inner[IP].len - 20 - 20
        self.assertEqual(size, 65200)

        #
        # IPv6/IPv6 - VXLAN
        #
        p66 = (Ether(src=self.pg2.remote_mac, dst="02:fe:60:1e:a2:79") /
               IPv6(src=self.pg2.remote_ip6, dst="fd01:3::3") /
               TCP(sport=1234, dport=1234) /
               Raw(b'\xa5' * 65200))

        self.pg0.enable_capture()
        rxs = self.send_and_expect(self.pg2, [p66], self.pg0, 45)
        size = 0
        for rx in rxs:
            self.assertEqual(rx[Ether].src, self.pg0.local_mac)
            self.assertEqual(rx[Ether].dst, self.pg0.remote_mac)
            self.assertEqual(rx[IPv6].src, self.pg0.local_ip6)
            self.assertEqual(rx[IPv6].dst, self.pg0.remote_ip6)
            self.assertEqual(rx[VXLAN].vni, 10)
            inner = rx[VXLAN].payload
            self.assertEqual(inner[Ether].src, self.pg2.remote_mac)
            self.assertEqual(inner[Ether].dst, "02:fe:60:1e:a2:79")
            self.assertEqual(inner[IPv6].src, self.pg2.remote_ip6)
            self.assertEqual(inner[IPv6].dst, "fd01:3::3")
            size += inner[IPv6].plen - 20
        self.assertEqual(size, 65200)

if __name__ == '__main__':
    unittest.main(testRunner=VppTestRunner)
