import pytest
from odin_snmp.adapter import SnmpSnapshot

@pytest.fixture(scope="class")
def test_example_ports():
    ports = {1 : 'detector', 6 : 'node_1', 7 : 'node_2'}
    return ports

@pytest.fixture(scope="class")
def test_example_packets():
    packets = {1 : {'inPackets' : 12, 'outPackets' : 13}, 6 : {'inPackets' : 312, 'outPackets' : 143}, 7 : {'inPackets' : 65, 'outPackets' : 433}}
    return packets
    
class TestSnapshot():
    """Test cases for the SnmpSnapshot class."""
    def test_populate_packets(self, test_example_ports, test_example_packets):
        # instantiate a snapshot with example ports
        testSnapshot = SnmpSnapshot(test_example_ports)
        # feed the snapshot with example packet counts
        for port in test_example_packets.keys():
            testSnapshot.feed_port_data(port, test_example_packets[port]['inPackets'], test_example_packets[port]['outPackets'])
        # compare packets stored in the shapshot with the packets fed
        assert testSnapshot.packets == test_example_packets